"""
Core app frontend views: Табло, Кейсове, Детайли на кейс.
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404
from core.models import Case, CaseMembership


def _format_audit_log(log, case_title):
    """Formats an AuditLog entry into a dictionary for the UI activity feed."""
    action = log.action
    details = log.details_json
    
    icon = "🗂"
    title = action
    severity = "info"
    
    pipeline = details.get("pipeline", "Анализ")
    
    if action == "case_created":
        icon = "📁"
        title = "Създадохте кейс"
    elif action == "evidence_uploaded":
        icon = "📄"
        title = "Качихте нов файл"
    elif action == "analysis_job_created":
        icon = "🧪"
        title = f"Пуснахте анализ '{pipeline}'"
    elif action == "analysis_finished":
        icon = "✅"
        title = f"Завърши анализ '{pipeline}'"
        severity = "success"
    elif action == "analysis_failed":
        icon = "⚠️"
        title = f"Анализ '{pipeline}' завърши с грешка"
        severity = "warn"
    elif action == "comment_created":
        icon = "💬"
        title = "Добавихте коментар"
    elif action == "comment_deleted":
        icon = "💬"
        title = "Изтрихте коментар"
    elif action == "evidence_reference_set":
        icon = "🎯"
        title = "Зададохте файл като еталон"
    elif action == "evidence_reference_removed":
        icon = "🎯"
        title = "Премахнахте еталон"
    elif action == "share_link_created":
        icon = "🔗"
        title = f"Създадохте споделен линк ({details.get('role', '')})"
    elif action == "share_link_revoked":
        icon = "🚫"
        title = "Отменихте споделен линк"
    elif action == "share_link_redeemed":
        icon = "👋"
        title = f"Присъединихте се като {details.get('role', '')}"
        
    return {
        "icon": icon,
        "title": title,
        "case_title": case_title,
        "timestamp": log.timestamp,
        "actor": log.actor.get_username() if log.actor else "Система",
        "severity": severity,
        "case_id": details.get("case_id") or log.target_id
    }


@login_required
def dashboard(request):
    try:
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE core_evidence ADD COLUMN is_reference boolean DEFAULT false;")
    except Exception:
        pass
        
    user = request.user
    from core.models import Case, Evidence, AnalysisJob, Artifact
    from audit.models import AuditLog
    
    accessible_cases = Case.get_accessible_cases(user)
    case_dict = {c.pk: c.title for c in accessible_cases}
    case_ids = list(case_dict.keys())
    
    total_cases = len(case_ids)
    evidence_count = Evidence.objects.filter(case_id__in=case_ids).count()
    analysis_count = AnalysisJob.objects.filter(evidence__case_id__in=case_ids).count()
    artifact_count = Artifact.objects.filter(job__evidence__case_id__in=case_ids).count()
    
    # We fetch a chunk of recent logs and filter them in memory to ensure we only show accessible cases
    raw_logs = AuditLog.objects.all().select_related("actor")[:150]
    
    recent_activities = []
    for log in raw_logs:
        log_case_id = None
        if log.target_type == "Case" and str(log.target_id).isdigit():
            log_case_id = int(log.target_id)
        elif "case_id" in log.details_json:
            log_case_id = int(log.details_json["case_id"])
            
        if log_case_id and log_case_id in case_dict:
            fmt_log = _format_audit_log(log, case_dict[log_case_id])
            recent_activities.append(fmt_log)
            if len(recent_activities) >= 15:
                break

    context = {
        "total_cases": total_cases,
        "evidence_count": evidence_count,
        "analysis_count": analysis_count,
        "artifact_count": artifact_count,
        "recent_activities": recent_activities,
    }
    return render(request, "dashboard.html", context)


@login_required
def case_list(request):
    user = request.user
    if user.is_staff:
        cases = Case.objects.select_related("created_by").order_by("-created_at")
    else:
        cases = (
            Case.objects
            .filter(memberships__user=user)
            .select_related("created_by")
            .distinct()
            .order_by("-created_at")
        )
    return render(request, "cases.html", {"cases": cases})


@login_required
def case_create(request):
    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        description = request.POST.get("description", "").strip()
        tags_raw = request.POST.get("tags", "").strip()
        
        if not title:
            # simple validation fallback
            return render(request, "case_create.html", {"error": "Заглавието е задължително."})
            
        tags = [t.strip() for t in tags_raw.split(",")] if tags_raw else []
        
        case = Case.objects.create(
            title=title,
            description=description,
            tags=tags,
            created_by=request.user
        )
        CaseMembership.objects.create(
            case=case,
            user=request.user,
            role=CaseMembership.Role.EDITOR,
            invited_by=request.user
        )
        
        from audit.models import AuditLog
        AuditLog.log("case_created", actor=request.user, target=case)
        
        from django.shortcuts import redirect
        return redirect("case-detail", pk=case.pk)
        
    return render(request, "case_create.html")



@login_required
def case_detail(request, pk):
    try:
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE core_evidence ADD COLUMN is_reference boolean DEFAULT false;")
    except Exception as e:
        pass
        
    user = request.user
    case = get_object_or_404(Case, pk=pk)

    # Check membership
    if not user.is_staff:
        is_member = (
            case.created_by == user
            or CaseMembership.objects.filter(case=case, user=user).exists()
        )
        if not is_member:
            from django.http import Http404
            raise Http404

    # Current user's role
    if user.is_staff:
        role = "admin"
    elif case.created_by == user:
        role = "owner"
    else:
        m = CaseMembership.objects.filter(case=case, user=user).first()
        role = m.role if m else "viewer"

    evidence = case.evidence_items.select_related("uploaded_by").order_by("-uploaded_at")
    jobs = case.analysis_jobs.select_related("created_by", "evidence").order_by("-id")[:10]
    comments = case.comments.select_related("author").order_by("created_at")
    share_links = case.share_links.select_related("created_by").filter(revoked=False)

    return render(request, "case_detail.html", {
        "case": case,
        "role": role,
        "evidence": evidence,
        "jobs": jobs,
        "comments": comments,
        "share_links": share_links,
        "is_owner": role in ("owner", "admin"),
        "can_edit": role in ("editor", "owner", "admin"),
    })


@login_required
def evidence_detail(request, pk):
    from core.models import Evidence, AnalysisJob
    evidence = get_object_or_404(Evidence, pk=pk)
    case = evidence.case
    
    # Check access
    if not request.user.is_staff:
        is_member = (
            case.created_by == request.user
            or CaseMembership.objects.filter(case=case, user=request.user).exists()
        )
        if not is_member:
            from django.http import Http404
            raise Http404

    role = "viewer"
    if request.user.is_staff:
        role = "admin"
    elif case.created_by == request.user:
        role = "owner"
    else:
        m = CaseMembership.objects.filter(case=case, user=request.user).first()
        if m:
            role = m.role

    can_edit = role in ("editor", "owner", "admin")

    pages = []
    if evidence.type == "pdf":
        pages = evidence.pages.order_by("page_index")
        
    analysis_jobs = AnalysisJob.objects.filter(evidence=evidence).order_by("-created_at")

    context = {
        "evidence": evidence,
        "case": case,
        "pages": pages,
        "analysis_jobs": analysis_jobs,
        "can_edit": can_edit,
        "is_owner": role in ("owner", "admin"),
        "role": role
    }
    return render(request, "evidence_detail.html", context)


@login_required
def case_comment_create(request, pk):
    if request.method != "POST":
        from django.http import HttpResponseBadRequest
        return HttpResponseBadRequest("POST required")
        
    case = get_object_or_404(Case, pk=pk)
    user = request.user
    
    if not user.is_staff:
        if case.created_by != user:
            membership = CaseMembership.objects.filter(case=case, user=user).first()
            if not membership or membership.role not in ("editor",):
                from django.contrib import messages
                messages.error(request, "Нямате право да коментирате в този кейс (изисква се роля Собственик или Редактор).")
                from django.urls import reverse
                from django.shortcuts import redirect
                return redirect(f"{reverse('case-detail', args=[case.pk])}#komentari")

    text = request.POST.get("text", "").strip()
    from django.urls import reverse
    from django.shortcuts import redirect
    from django.contrib import messages
    
    if text:
        from core.models import CaseComment
        CaseComment.objects.create(case=case, author=user, text=text)
        try:
            from audit.models import AuditLog
            AuditLog.log("comment_created", actor=user, target=case)
        except Exception:
            pass
        messages.success(request, "Коментарът беше добавен успешно.")
    else:
        messages.error(request, "Коментарът не може да бъде празен.")
        
    return redirect(f"{reverse('case-detail', args=[case.pk])}#komentari")


@login_required
def case_comment_delete(request, pk, comment_id):
    if request.method != "POST":
        from django.http import HttpResponseBadRequest
        return HttpResponseBadRequest("POST required")
        
    from django.urls import reverse
    from django.shortcuts import redirect
    from django.contrib import messages
    
    case = get_object_or_404(Case, pk=pk)
    from core.models import CaseComment
    comment = get_object_or_404(CaseComment, pk=comment_id, case=case)
    
    if comment.author != request.user and not request.user.is_staff:
        messages.error(request, "Имате право да изтриете само собствените си коментари.")
    else:
        comment.delete()
        try:
            from audit.models import AuditLog
            AuditLog.log("comment_deleted", actor=request.user, target=case)
        except Exception:
            pass
        messages.success(request, "Коментарът беше изтрит.")
        
    return redirect(f"{reverse('case-detail', args=[case.pk])}#komentari")


@login_required
def methodology(request):
    """
    Renders the wiki-style methodology and documentation page.
    """
    return render(request, "methodology.html")


def register_view(request):
    """
    User registration view.
    """
    from django.contrib.auth.forms import UserCreationForm
    from django.contrib.auth import login
    
    if request.user.is_authenticated:
        from django.shortcuts import redirect
        return redirect('dashboard')
        
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            from django.shortcuts import redirect
            return redirect('dashboard')
    else:
        form = UserCreationForm()
        
    from django.shortcuts import render
    return render(request, 'register.html', {'form': form})

def logout_view(request):
    """
    Custom logout view to support GET requests (Django 5.0 deprecates GET for LogoutView).
    """
    from django.contrib.auth import logout
    from django.shortcuts import redirect
    logout(request)
    return redirect('login')
