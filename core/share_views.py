"""
Share link redemption web view.
GET /share/<token>/
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404

from audit.models import AuditLog
from core.models import CaseShareLink, CaseMembership


@login_required
def redeem_share_link(request, token):
    from django.contrib import messages
    link = get_object_or_404(CaseShareLink, token=token)

    if not link.is_valid:
        messages.error(request, "Този линк е невалиден или е изтекъл.")
        return redirect("case-list")

    # Upsert membership with downgrade prevention
    case = link.case
    user = request.user
    created = False
    
    if case.created_by == user:
        messages.info(request, "Вие сте собственик на този кейс.")
    else:
        existing = CaseMembership.objects.filter(case=case, user=user).first()
        if existing:
            if existing.role == CaseMembership.Role.EDITOR and link.role == CaseMembership.Role.VIEWER:
                messages.info(request, "Вече имате права на редактор за този кейс.")
            elif existing.role == link.role:
                messages.info(request, f"Вече сте част от този кейс като {link.role}.")
            else:
                existing.role = link.role
                existing.save(update_fields=["role"])
                messages.success(request, f"Правата ви бяха обновени до {link.role}.")
        else:
            CaseMembership.objects.create(
                case=case,
                user=user,
                role=link.role,
                invited_by=link.created_by,
            )
            created = True
            messages.success(request, f"Успешно се присъединихте към кейсите като {link.role}.")

        # Increment usage counter
        CaseShareLink.objects.filter(pk=link.pk).update(uses_count=link.uses_count + 1)

        AuditLog.log(
            "share_link_redeemed",
            actor=user,
            target=link,
            details={
                "case_id": case.pk,
                "role": link.role,
                "membership_created": created,
            },
        )

    return redirect("case-detail", pk=case.pk)
