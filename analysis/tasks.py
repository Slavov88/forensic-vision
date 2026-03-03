"""
Celery tasks for ForensicVision:

  render_pdf_to_pages  – converts a PDF evidence item into EvidencePage images
  run_analysis_job     – executes a scheduled pipeline task
"""
import io
import logging
import time
from pathlib import Path

from celery import shared_task
from django.core.files.base import ContentFile
from django.utils import timezone

logger = logging.getLogger(__name__)



@shared_task(bind=True, max_retries=3, default_retry_delay=30, name="analysis.render_pdf_to_pages")
def render_pdf_to_pages(self, evidence_id: int) -> dict:
    """
    Render each page of a PDF Evidence as a PNG image and store as EvidencePage.

    Uses pypdfium2 – a Python binding for PDFium (no Poppler/Ghostscript needed).
    DPI = 150, scale factor = 150/72 ≈ 2.08.
    """
    import pypdfium2 as pdfium
    from core.models import Evidence, EvidencePage

    try:
        evidence = Evidence.objects.select_related("case").get(pk=evidence_id)
    except Evidence.DoesNotExist:
        logger.error("render_pdf_to_pages: Evidence %s not found", evidence_id)
        return {"error": "not_found"}

    evidence.original_file.seek(0)
    pdf_bytes = evidence.original_file.read()
    evidence.original_file.seek(0)

    try:
        doc = pdfium.PdfDocument(pdf_bytes)
    except Exception as exc:
        logger.exception("render_pdf_to_pages: Failed to open PDF %s", evidence_id)
        raise self.retry(exc=exc)

    scale = 150 / 72  # 150 DPI
    created_pages = []

    try:
        for page_index in range(len(doc)):
            page = doc[page_index]
            bitmap = page.render(scale=scale, rotation=0)
            pil_image = bitmap.to_pil()

            width, height = pil_image.size
            buf = io.BytesIO()
            pil_image.save(buf, format="PNG", optimize=True)
            buf.seek(0)

            filename = f"evidence_{evidence_id}_page_{page_index:04d}.png"
            content = ContentFile(buf.read(), name=filename)

            ep, created = EvidencePage.objects.update_or_create(
                evidence=evidence,
                page_index=page_index,
                defaults={"width": width, "height": height},
            )
            if created or not ep.rendered_image:
                ep.rendered_image.save(filename, content, save=True)
            else:
                ep.rendered_image.delete(save=False)
                ep.rendered_image.save(filename, content, save=True)
            ep.width = width
            ep.height = height
            ep.save(update_fields=["width", "height"])

            created_pages.append(page_index)
            logger.info("Rendered page %d of evidence %d", page_index, evidence_id)
    finally:
        doc.close()

    logger.info(
        "render_pdf_to_pages done: evidence=%d, pages=%d", evidence_id, len(created_pages)
    )
    return {"evidence_id": evidence_id, "pages_rendered": len(created_pages)}



@shared_task(bind=True, name="analysis.run_analysis_job")
def run_analysis_job(self, job_id: int) -> dict:
    """
    Execute a registered analysis pipeline.
    """
    from core.models import AnalysisJob, Artifact
    from analysis.registry import PIPELINE_REGISTRY

    try:
        job = AnalysisJob.objects.select_related("case", "evidence").get(pk=job_id)
    except AnalysisJob.DoesNotExist:
        logger.error("run_analysis_job: AnalysisJob %s not found", job_id)
        return {"error": "not_found"}

    job.status = AnalysisJob.Status.RUNNING
    job.started_at = timezone.now()
    job.progress = 0
    job.save(update_fields=["status", "started_at", "progress"])

    pipeline_meta = PIPELINE_REGISTRY.get(job.pipeline_name, {})

    try:
        # 1. Preprocessing (0-20%)
        job.progress = 10
        job.save(update_fields=["progress"])
        
        target_path = ""
        if job.page:
            target_path = job.page.rendered_image.path
        elif job.evidence.type == "pdf": # Use string literal for flexibility or import if needed
            # For PDF without a specific page, fallback to first rendered page
            from core.models import EvidencePage
            p0 = EvidencePage.objects.filter(evidence=job.evidence, page_index=0).first()
            if p0 and p0.rendered_image:
                target_path = p0.rendered_image.path
            else:
                raise ValueError("PDF обектът няма рендирани страници. Моля, изчакайте обработката или изберете конкретна страница.")
        else:
            target_path = job.evidence.original_file.path

        metrics = {}
        overlays = []

        # 2. Pipeline Dispatch (20-80%)
        target_info = {
            "evidence_id": job.evidence.id,
            "evidence_name": Path(job.evidence.original_file.name).name,
            "page_index": job.page.page_index if job.page else "Всички"
        }
        
        if job.pipeline_name == "general_scan":
            from analysis.pipelines.general_scan import run_general_scan
            metrics, overlays = run_general_scan(target_path, job.id, job.params_json)
            metrics["target"] = target_info
            
        elif job.pipeline_name == "layout_consistency":
            from analysis.pipelines.layout_consistency import run_layout_consistency
            metrics, overlays = run_layout_consistency(target_path, job.id, job.params_json)
            metrics["target"] = target_info
            
        elif job.pipeline_name == "compare_reference":
            from analysis.pipelines.compare_reference import run_compare_reference
            from core.models import Evidence, EvidencePage
            
            params = job.params_json or {}
            ref_id = params.get('reference_evidence_id')
            ref_page_idx = params.get('reference_page_index', 0)
            
            ref_ev = Evidence.objects.get(id=ref_id)
            ref_info = {
                "evidence_id": ref_ev.id,
                "evidence_name": Path(ref_ev.original_file.name).name,
                "page_index": ref_page_idx
            }
            
            if ref_ev.type == Evidence.EvidenceType.PDF:
                try:
                    ref_page = EvidencePage.objects.get(evidence=ref_ev, page_index=ref_page_idx)
                    ref_path = ref_page.rendered_image.path
                except EvidencePage.DoesNotExist:
                    raise ValueError(f"Страница {ref_page_idx} на еталона не съществува.")
            else:
                ref_path = ref_ev.original_file.path
            
            metrics, overlays = run_compare_reference(target_path, ref_path, job.id, params)
            metrics["target"] = target_info
            metrics["reference"] = ref_info

        elif job.pipeline_name == "handwriting_compare":
            from analysis.pipelines.handwriting_compare import run_handwriting_compare
            from core.models import Evidence, EvidencePage
            
            params = job.params_json or {}
            ref_id = params.get('compare_evidence_id')
            
            if not ref_id:
                raise ValueError("Не е избрано доказателство за сравнение на почерка.")
                
            ref_ev = Evidence.objects.get(id=ref_id)
            # Assuming we just compare against the first page of the reference doc for now
            # unless a specific page is passed in params
            ref_page_idx = params.get('compare_page_index', 0)
            
            ref_info = {
                "evidence_id": ref_ev.id,
                "evidence_name": Path(ref_ev.original_file.name).name,
                "page_index": ref_page_idx
            }
            
            if ref_ev.type == Evidence.EvidenceType.PDF:
                try:
                    ref_page = EvidencePage.objects.get(evidence=ref_ev, page_index=ref_page_idx)
                    ref_path = ref_page.rendered_image.path
                except EvidencePage.DoesNotExist:
                    raise ValueError(f"Страница {ref_page_idx} на документа за сравнение не съществува.")
            else:
                ref_path = ref_ev.original_file.path
                
            metrics, overlays = run_handwriting_compare(target_path, ref_path, job.id, params)
            metrics["target"] = target_info
            metrics["reference"] = ref_info


        job.progress = 80
        job.save(update_fields=["progress"])

        # 3. Saving Artifacts (80-100%)
        # Create metrics artifact
        Artifact.objects.create(
            job=job,
            kind=Artifact.Kind.METRICS,
            data_json=metrics,
        )

        # Create overlay artifacts
        for ov in overlays:
            Artifact.objects.create(
                job=job,
                kind=Artifact.Kind.OVERLAY,
                file=ov['file'],
                data_json={"label": ov.get('label', "")},
            )

        job.status = AnalysisJob.Status.DONE
        job.finished_at = timezone.now()
        job.progress = 100
        job.save(update_fields=["status", "finished_at", "progress"])

        from audit.models import AuditLog
        AuditLog.log(
            "analysis_finished", 
            target=job, 
            details={"pipeline": job.pipeline_name, "case_id": job.evidence.case_id}
        )

        logger.info("run_analysis_job done: job=%d pipeline=%s", job_id, job.pipeline_name)
        return {"job_id": job_id, "status": "done"}

    except Exception as exc:
        logger.exception("run_analysis_job failed: job=%d", job_id)
        job.status = AnalysisJob.Status.FAILED
        job.finished_at = timezone.now()
        job.error_message = str(exc)
        job.save(update_fields=["status", "finished_at", "error_message"])
        
        from audit.models import AuditLog
        AuditLog.log(
            "analysis_failed", 
            target=job, 
            details={"pipeline": job.pipeline_name, "case_id": job.evidence.case_id, "error": str(exc)}
        )
        raise
