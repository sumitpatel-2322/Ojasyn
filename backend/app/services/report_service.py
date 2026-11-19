import hashlib
import io
import re
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from xml.sax.saxutils import escape as xml_escape

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import UnitOfWork, next_tenant_sequence, verify_and_increment_version
from app.core.deps import TenantContext
from app.core.exceptions import (
    AppError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.domain.enums import (
    ActorType,
    DocumentType,
    ReportStatus,
    ScanStatus,
    SecurityEventOutcome,
    SecurityEventSeverity,
)
from app.domain.state_machines import assert_transition
from app.integrations.storage_provider import (
    LocalPrivateVaultAdapter,
    ObjectStorageProvider,
    build_vault_object_key,
)
from app.models.domain.complaints import Complaint
from app.models.domain.documents import Document, DocumentVersion
from app.models.domain.governance import ApprovalRecord
from app.models.domain.investigations import GeneratedReport, Investigation
from app.models.domain.products_materials import Product
from app.models.domain.qms_loop import Deviation
from app.models.domain.tenancy import Organization, Site
from app.models.domain.traceability import (
    Batch,
    BatchDisposition,
    BatchMaterialConsumption,
    BatchMovementEvent,
    ShipmentLine,
)
from app.models.schemas.investigations import ReportGenerateRequest, ReportReviewRequest
from app.repositories.document_sop_repo import DocumentSOPRepository
from app.repositories.governance_repo import OutboxRepository
from app.repositories.investigation_repo import InvestigationRepository
from app.services.audit_service import AuditService
from app.services.document_control_service import DocumentControlService

DOCX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

REPORT_TRANSITIONS: Dict[str, Set[str]] = {
    ReportStatus.DRAFT.value: {ReportStatus.GENERATED.value, ReportStatus.SUPERSEDED.value},
    ReportStatus.GENERATED.value: {
        ReportStatus.UNDER_REVIEW.value,
        ReportStatus.APPROVED.value,
        ReportStatus.SUPERSEDED.value,
    },
    ReportStatus.UNDER_REVIEW.value: {
        ReportStatus.UNDER_REVIEW.value,
        ReportStatus.APPROVED.value,
        ReportStatus.SUPERSEDED.value,
        ReportStatus.DRAFT.value,
    },
    ReportStatus.APPROVED.value: {ReportStatus.SUPERSEDED.value},
    ReportStatus.SUPERSEDED.value: set(),
}


def _org_report_prefix(org: Optional[Organization]) -> str:
    if org is None:
        return "ORG-RPT"
    slug = re.sub(r"[^A-Z0-9]", "", (org.trade_name or org.legal_name or "ORG").upper())[:5] or "ORG"
    return f"{slug}-RPT"


def _render_docx_with_python_docx(snapshot: Dict[str, Any]) -> bytes:
    from docx import Document as DocxDocument  # type: ignore

    doc = DocxDocument()
    doc.add_heading(snapshot.get("title") or "Quality Investigation Report", level=0)

    # 1. Control Page
    doc.add_heading("1. Document Control & Case Metadata", level=1)
    ctrl_table = doc.add_table(rows=1, cols=2)
    ctrl_table.rows[0].cells[0].text = "Field"
    ctrl_table.rows[0].cells[1].text = "Value"
    for key, val in (snapshot.get("control_page") or {}).items():
        row = ctrl_table.add_row()
        row.cells[0].text = str(key)
        row.cells[1].text = str(val if val is not None else "N/A")

    # 2. Executive Summary
    doc.add_heading("2. Executive Summary", level=1)
    exec_sum = snapshot.get("executive_summary") or {}
    doc.add_paragraph(str(exec_sum.get("narrative") or "Investigation summary compiled from persisted records."))
    for k, v in (exec_sum.get("metrics") or {}).items():
        doc.add_paragraph(f"- {k}: {v}")

    # 3. Complaint & Product Details
    doc.add_heading("3. Complaint & Product Details", level=1)
    comp = snapshot.get("complaint")
    if comp:
        for k, v in comp.items():
            doc.add_paragraph(f"{k}: {v if v is not None else 'N/A'}")
    else:
        doc.add_paragraph("No complaint linked (deviation/standalone investigation).")
    prod = snapshot.get("product")
    if prod:
        doc.add_paragraph("Product Master:")
        for k, v in prod.items():
            doc.add_paragraph(f"  - {k}: {v if v is not None else 'N/A'}")

    # 4. Traceability & Missing Links
    doc.add_heading("4. Batch Traceability & Missing Links", level=1)
    trace = snapshot.get("traceability") or {}
    if trace.get("batch"):
        for k, v in trace["batch"].items():
            doc.add_paragraph(f"{k}: {v if v is not None else 'N/A'}")
        doc.add_paragraph(f"Dispositions recorded: {len(trace.get('dispositions') or [])}")
        doc.add_paragraph(f"Material consumptions recorded: {len(trace.get('material_consumptions') or [])}")
        doc.add_paragraph(f"Shipment allocations recorded: {len(trace.get('shipment_lines') or [])}")
        doc.add_paragraph(f"Movement events recorded: {len(trace.get('movement_events') or [])}")
    else:
        doc.add_paragraph("No batch linked to this investigation.")
    missing_links = trace.get("missing_links") or []
    if missing_links:
        doc.add_paragraph("Identified Traceability Gaps:")
        for gap in missing_links:
            doc.add_paragraph(f"- {gap}")

    # 5. SOP, QC & Calibration Review
    doc.add_heading("5. Deterministic SOP, QC & Calibration Compliance Review", level=1)
    comp_items = snapshot.get("compliance_results") or []
    if comp_items:
        tbl = doc.add_table(rows=1, cols=6)
        headers = ["Rule", "Version", "Status", "Actual", "Expected", "Evidence Ref ID"]
        for idx, h in enumerate(headers):
            tbl.rows[0].cells[idx].text = h
        for item in comp_items:
            row = tbl.add_row()
            row.cells[0].text = str(item.get("rule_identifier") or "")
            row.cells[1].text = str(item.get("rule_version") or "")
            row.cells[2].text = str(item.get("status") or "")
            row.cells[3].text = str(item.get("actual_value_text") or "")
            row.cells[4].text = str(item.get("expected_requirement_text") or "")
            row.cells[5].text = str(item.get("evidence_ref_id") or "")
            doc.add_paragraph(f"Explanation ({item.get('rule_identifier')}): {item.get('explanation')}")
    else:
        doc.add_paragraph("No deterministic compliance results recorded.")

    # 6. Investigation Activity / Step Log
    doc.add_heading("6. Durable Investigation Run & Step Log", level=1)
    for run in snapshot.get("runs") or []:
        doc.add_paragraph(
            f"Run #{run.get('run_number')} | Status: {run.get('status')} | "
            f"Workflow: {run.get('workflow_version')} | Model: {run.get('model_name') or 'N/A'}"
        )
        for st in run.get("steps") or []:
            doc.add_paragraph(
                f"  Step {st.get('step_seq')}: {st.get('node_name')} [{st.get('status')}]"
            )

    # 7. Evidence Register
    doc.add_heading("7. Persisted Evidence Register", level=1)
    ev_items = snapshot.get("evidence_references") or []
    if ev_items:
        ev_tbl = doc.add_table(rows=1, cols=5)
        for idx, h in enumerate(["Evidence ID", "Source Type", "Table/Record", "Doc Version / SHA256", "Quoted Value"]):
            ev_tbl.rows[0].cells[idx].text = h
        for ev in ev_items:
            row = ev_tbl.add_row()
            row.cells[0].text = str(ev.get("id"))
            row.cells[1].text = str(ev.get("source_type"))
            row.cells[2].text = f"{ev.get('source_table')}:{ev.get('source_record_id')}"
            row.cells[3].text = (
                f"{ev.get('document_version_id') or 'N/A'} / {ev.get('document_checksum_sha256') or 'N/A'}"
            )
            row.cells[4].text = str(ev.get("quoted_value") or "")
    else:
        doc.add_paragraph("No evidence references persisted.")

    # 8. Findings vs Root Cause Hypotheses
    doc.add_heading("8. Findings (Verified Observations vs Inferences) & Root Cause Hypotheses", level=1)
    doc.add_paragraph("Findings:")
    for fnd in snapshot.get("findings") or []:
        kind = "VERIFIED OBSERVATION" if fnd.get("is_verified_observation") else "ANALYTICAL INFERENCE"
        doc.add_paragraph(
            f"- [{fnd.get('finding_code')}] ({kind}) {fnd.get('title')}: {fnd.get('statement')} "
            f"| Evidence: {fnd.get('evidence_ref_ids')}"
        )
    doc.add_paragraph("Root Cause Hypotheses:")
    for hyp in snapshot.get("hypotheses") or []:
        doc.add_paragraph(
            f"- [{hyp.get('hypothesis_code')}] ({hyp.get('status')}, uncertainty={hyp.get('uncertainty_level')}) "
            f"{hyp.get('title')}: {hyp.get('description')} "
            f"| Supporting: {hyp.get('supporting_evidence_ids')} | Contradicting: {hyp.get('contradicting_evidence_ids')}"
        )

    # 9. Limitations & Uncertainties
    doc.add_heading("9. Limitations & Uncertainties", level=1)
    lims = snapshot.get("limitations") or []
    if lims:
        for lim in lims:
            doc.add_paragraph(f"- {lim}")
    else:
        doc.add_paragraph("No additional limitations reported.")

    # 10. QA Decision & Linked Deviations
    doc.add_heading("10. QA Decision & Linked Deviations", level=1)
    qa_info = snapshot.get("qa_decision") or {}
    for k, v in qa_info.items():
        doc.add_paragraph(f"{k}: {v if v is not None else 'PENDING'}")
    for dev in snapshot.get("deviations") or []:
        doc.add_paragraph(
            f"- Deviation {dev.get('deviation_number')} [{dev.get('classification')} | {dev.get('status')}]: {dev.get('title')}"
        )

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _render_docx_ooxml_fallback(snapshot: Dict[str, Any]) -> bytes:
    """
    Minimal standards-compliant OOXML (.docx) ZIP package fallback used ONLY if
    `python-docx` is absent in the current Python runtime.
    """
    lines: List[str] = [str(snapshot.get("title") or "Quality Investigation Report"), ""]

    def _add_section(heading: str, rows: List[str]) -> None:
        lines.append(heading)
        lines.extend(rows or ["(none)"])
        lines.append("")

    _add_section(
        "1. Document Control & Case Metadata",
        [f"{k}: {v}" for k, v in (snapshot.get("control_page") or {}).items()],
    )
    exec_sum = snapshot.get("executive_summary") or {}
    _add_section(
        "2. Executive Summary",
        [str(exec_sum.get("narrative") or "")]
        + [f"{k}: {v}" for k, v in (exec_sum.get("metrics") or {}).items()],
    )
    comp = snapshot.get("complaint") or {}
    _add_section(
        "3. Complaint & Product Details",
        [f"{k}: {v}" for k, v in comp.items()] if comp else ["No complaint linked."],
    )
    trace = snapshot.get("traceability") or {}
    _add_section(
        "4. Batch Traceability & Missing Links",
        ([f"{k}: {v}" for k, v in (trace.get("batch") or {}).items()] or ["No batch linked."])
        + [f"Missing Link: {g}" for g in (trace.get("missing_links") or [])],
    )
    _add_section(
        "5. Deterministic SOP, QC & Calibration Review",
        [
            f"{c.get('rule_identifier')} ({c.get('rule_version')}): {c.get('status')} - {c.get('explanation')} [ev={c.get('evidence_ref_id')}]"
            for c in (snapshot.get("compliance_results") or [])
        ],
    )
    _add_section(
        "6. Investigation Run & Step Log",
        [
            f"Run #{r.get('run_number')} {r.get('status')} steps={len(r.get('steps') or [])}"
            for r in (snapshot.get("runs") or [])
        ],
    )
    _add_section(
        "7. Persisted Evidence Register",
        [
            f"{e.get('id')} | {e.get('source_type')} | {e.get('source_table')}:{e.get('source_record_id')} | sha256={e.get('document_checksum_sha256')}"
            for e in (snapshot.get("evidence_references") or [])
        ],
    )
    _add_section(
        "8. Findings & Root Cause Hypotheses",
        [
            f"{f.get('finding_code')} verified={f.get('is_verified_observation')}: {f.get('title')}"
            for f in (snapshot.get("findings") or [])
        ]
        + [
            f"{h.get('hypothesis_code')} [{h.get('status')}]: {h.get('title')} supporting={h.get('supporting_evidence_ids')}"
            for h in (snapshot.get("hypotheses") or [])
        ],
    )
    _add_section("9. Limitations & Uncertainties", [str(x) for x in (snapshot.get("limitations") or [])])
    _add_section(
        "10. QA Decision & Linked Deviations",
        [f"{k}: {v}" for k, v in (snapshot.get("qa_decision") or {}).items()]
        + [
            f"{d.get('deviation_number')} [{d.get('status')}]: {d.get('title')}"
            for d in (snapshot.get("deviations") or [])
        ],
    )

    paragraphs = "".join(
        f"<w:p><w:r><w:t xml:space=\"preserve\">{xml_escape(line)}</w:t></w:r></w:p>"
        for line in lines
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{paragraphs}<w:sectPr/></w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels_xml)
        zf.writestr("word/document.xml", document_xml)
    return buf.getvalue()


def render_investigation_docx_bytes(snapshot: Dict[str, Any]) -> bytes:
    """Renders DOCX bytes using `python-docx` when available, with OOXML fallback."""
    try:
        return _render_docx_with_python_docx(snapshot)
    except ImportError:
        return _render_docx_ooxml_fallback(snapshot)


class ReportService:
    """
    Versioned DOCX Investigation Report generation, storage, checksum verification,
    QA review/approval, and secure download (`D6`, `D7`).
    """

    def __init__(
        self,
        repo: Optional[InvestigationRepository] = None,
        doc_repo: Optional[DocumentSOPRepository] = None,
        storage: Optional[ObjectStorageProvider] = None,
        doc_service: Optional[DocumentControlService] = None,
        audit_service: Optional[AuditService] = None,
        outbox_repo: Optional[OutboxRepository] = None,
    ) -> None:
        self._repo = repo or InvestigationRepository()
        self._doc_repo = doc_repo or DocumentSOPRepository()
        self._storage = storage or LocalPrivateVaultAdapter()
        self._doc_service = doc_service or DocumentControlService(storage=self._storage)
        self._audit = audit_service or AuditService()
        self._outbox = outbox_repo or OutboxRepository()

    def _assert_site_access(
        self,
        tenant_ctx: TenantContext,
        site_id: Optional[uuid.UUID],
        *,
        target_resource: str,
    ) -> None:
        if site_id is not None and not tenant_ctx.is_org_wide and site_id not in tenant_ctx.allowed_site_ids:
            self._audit.record_security_event(
                event_type="SITE_SCOPE_VIOLATION",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.WARNING.value,
                organization_id=tenant_ctx.organization_id,
                user_id=tenant_ctx.user_id,
                target_resource=target_resource,
                details_json={"site_id": str(site_id)},
            )
            raise PermissionDeniedError(
                "Not authorized to access investigation reports for this site.",
                error_code="SITE_SCOPE_DENIED",
            )

    def _build_persisted_snapshot(
        self,
        db: Session,
        *,
        investigation: Investigation,
        run_id: Optional[uuid.UUID],
        report_number: str,
        version_number: int,
        title: str,
    ) -> Dict[str, Any]:
        org = db.get(Organization, investigation.organization_id)
        site = db.get(Site, investigation.site_id) if investigation.site_id else None
        complaint = (
            db.scalar(
                select(Complaint).where(
                    Complaint.organization_id == investigation.organization_id,
                    Complaint.id == investigation.complaint_id,
                )
            )
            if investigation.complaint_id
            else None
        )
        product = (
            db.scalar(
                select(Product).where(
                    Product.organization_id == investigation.organization_id,
                    Product.id == (investigation.product_id or (complaint.product_id if complaint else None)),
                )
            )
            if (investigation.product_id or (complaint and complaint.product_id))
            else None
        )
        batch = (
            db.scalar(
                select(Batch).where(
                    Batch.organization_id == investigation.organization_id,
                    Batch.id == investigation.batch_id,
                )
            )
            if investigation.batch_id
            else None
        )

        runs = self._repo.list_evidence_for_investigation  # noqa: F841 (clarity)
        inv_runs = list(investigation.runs)
        if not inv_runs:
            from app.models.domain.investigations import InvestigationRun

            inv_runs = list(
                db.scalars(
                    select(InvestigationRun).where(
                        InvestigationRun.organization_id == investigation.organization_id,
                        InvestigationRun.investigation_id == investigation.id,
                    )
                ).all()
            )
        target_run = None
        if run_id is not None:
            for r in inv_runs:
                if r.id == run_id:
                    target_run = r
                    break
        elif inv_runs:
            target_run = inv_runs[-1]

        ev_refs = self._repo.list_evidence_for_investigation(
            db, investigation.organization_id, investigation.id
        )
        comp_results = self._repo.list_compliance_results_for_investigation(
            db, investigation.organization_id, investigation.id
        )
        findings = self._repo.list_findings_for_investigation(
            db, investigation.organization_id, investigation.id
        )
        hypotheses = self._repo.list_hypotheses_for_investigation(
            db, investigation.organization_id, investigation.id
        )
        deviations = list(
            db.scalars(
                select(Deviation).where(
                    Deviation.organization_id == investigation.organization_id,
                    Deviation.investigation_id == investigation.id,
                )
            ).all()
        )

        # Traceability & missing links strictly from persisted records
        traceability_data: Dict[str, Any] = {"batch": None, "missing_links": []}
        if batch is not None:
            disps = list(
                db.scalars(
                    select(BatchDisposition).where(
                        BatchDisposition.organization_id == investigation.organization_id,
                        BatchDisposition.batch_id == batch.id,
                    )
                ).all()
            )
            consumptions = list(
                db.scalars(
                    select(BatchMaterialConsumption).where(
                        BatchMaterialConsumption.organization_id == investigation.organization_id,
                        BatchMaterialConsumption.batch_id == batch.id,
                    )
                ).all()
            )
            ship_lines = list(
                db.scalars(
                    select(ShipmentLine).where(
                        ShipmentLine.organization_id == investigation.organization_id,
                        ShipmentLine.batch_id == batch.id,
                    )
                ).all()
            )
            movements = list(
                db.scalars(
                    select(BatchMovementEvent).where(
                        BatchMovementEvent.organization_id == investigation.organization_id,
                        BatchMovementEvent.batch_id == batch.id,
                    )
                ).all()
            )
            missing_links: List[str] = []
            if not disps:
                missing_links.append("MISSING_BATCH_DISPOSITION_RECORD")
            if not consumptions:
                missing_links.append("MISSING_MATERIAL_CONSUMPTION_RECORDS")
            if not ship_lines:
                missing_links.append("MISSING_SHIPMENT_RECORDS")
            if not movements:
                missing_links.append("MISSING_BATCH_MOVEMENT_EVENTS")
            traceability_data = {
                "batch": {
                    "id": str(batch.id),
                    "batch_number": batch.batch_number,
                    "batch_status": batch.batch_status,
                    "quality_review_status": batch.quality_review_status,
                    "manufacturing_date": batch.manufacturing_date.isoformat(),
                    "expiry_date": batch.expiry_date.isoformat(),
                    "batch_size": f"{batch.batch_size_value} {batch.batch_size_unit}",
                },
                "dispositions": [
                    {
                        "id": str(d.id),
                        "decision": d.disposition_decision,
                        "decided_at": d.decided_at.isoformat(),
                    }
                    for d in disps
                ],
                "material_consumptions": [
                    {"id": str(c.id), "material_id": str(c.material_id), "supplier_lot_id": str(c.supplier_lot_id)}
                    for c in consumptions
                ],
                "shipment_lines": [
                    {"id": str(sl.id), "shipment_id": str(sl.shipment_id), "quantity": str(sl.quantity)}
                    for sl in ship_lines
                ],
                "movement_events": [
                    {"id": str(m.id), "event_type": m.event_type, "occurred_at": m.occurred_at.isoformat()}
                    for m in movements
                ],
                "missing_links": missing_links,
            }
        else:
            traceability_data["missing_links"] = ["NO_BATCH_LINKED_TO_INVESTIGATION"]

        limitations: List[str] = []
        if batch is None:
            limitations.append("No manufacturing batch linked; production and distribution traceability incomplete.")
        for c in comp_results:
            if c.status in {"MISSING", "UNVERIFIED"}:
                limitations.append(f"{c.rule_identifier}: {c.status} - {c.explanation}")
        for h in hypotheses:
            if h.limitations:
                limitations.append(f"{h.hypothesis_code}: {h.limitations}")
        if target_run and isinstance(target_run.summary_json, dict):
            for lim in target_run.summary_json.get("limitations") or []:
                if str(lim) not in limitations:
                    limitations.append(str(lim))

        run_entries = []
        for r in inv_runs:
            steps = self._repo.list_steps_for_run(db, investigation.organization_id, r.id)
            run_entries.append(
                {
                    "id": str(r.id),
                    "run_number": r.run_number,
                    "status": r.status,
                    "workflow_version": r.workflow_version,
                    "model_name": r.model_name,
                    "prompt_version": r.prompt_version,
                    "attempt_count": r.attempt_count,
                    "steps": [
                        {
                            "step_seq": s.step_seq,
                            "node_name": s.node_name,
                            "status": s.status,
                            "error_message": s.error_message,
                        }
                        for s in steps
                    ],
                }
            )

        exec_narrative = (
            (target_run.summary_json or {}).get("executive_summary")
            if target_run and isinstance(target_run.summary_json, dict)
            else None
        ) or f"Investigation {investigation.investigation_number} ({investigation.status})."

        return {
            "title": title,
            "control_page": {
                "report_number": report_number,
                "version_number": version_number,
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "organization": org.legal_name if org else str(investigation.organization_id),
                "site": f"{site.site_code} ({site.site_name})" if site else "Org-wide / Unspecified",
                "investigation_number": investigation.investigation_number,
                "investigation_status": investigation.status,
                "trigger_type": investigation.trigger_type,
                "run_id": str(target_run.id) if target_run else None,
                "workflow_version": target_run.workflow_version if target_run else None,
                "model_name": target_run.model_name if target_run else None,
                "prompt_version": target_run.prompt_version if target_run else None,
            },
            "executive_summary": {
                "narrative": exec_narrative,
                "metrics": {
                    "evidence_references": len(ev_refs),
                    "compliance_checks_total": len(comp_results),
                    "compliance_pass": sum(1 for c in comp_results if c.status == "PASS"),
                    "compliance_fail": sum(1 for c in comp_results if c.status == "FAIL"),
                    "compliance_missing_or_unverified": sum(
                        1 for c in comp_results if c.status in {"MISSING", "UNVERIFIED"}
                    ),
                    "verified_findings": sum(1 for f in findings if f.is_verified_observation),
                    "inferred_findings": sum(1 for f in findings if not f.is_verified_observation),
                    "hypotheses": len(hypotheses),
                    "linked_deviations": len(deviations),
                },
            },
            "complaint": (
                {
                    "complaint_number": complaint.complaint_number,
                    "source_channel": complaint.source_channel,
                    "status": complaint.status,
                    "reported_product_name": complaint.reported_product_name,
                    "reported_batch_number": complaint.reported_batch_number,
                    "defect_category": complaint.defect_category,
                    "ai_severity": complaint.ai_severity,
                    "final_severity": complaint.final_severity,
                    "description": complaint.description,
                }
                if complaint
                else None
            ),
            "product": (
                {
                    "product_code": product.product_code,
                    "generic_name": product.generic_name,
                    "brand_name": product.brand_name,
                    "dosage_form": product.dosage_form,
                    "strength": f"{product.strength_value} {product.strength_unit or ''}".strip()
                    if product.strength_value is not None
                    else None,
                }
                if product
                else None
            ),
            "traceability": traceability_data,
            "compliance_results": [
                {
                    "id": str(c.id),
                    "rule_identifier": c.rule_identifier,
                    "rule_version": c.rule_version,
                    "check_type": c.check_type,
                    "status": c.status,
                    "actual_value_text": c.actual_value_text,
                    "expected_requirement_text": c.expected_requirement_text,
                    "explanation": c.explanation,
                    "evidence_ref_id": str(c.evidence_ref_id) if c.evidence_ref_id else None,
                    "deviation_suggested": c.deviation_suggested,
                }
                for c in comp_results
            ],
            "runs": run_entries,
            "evidence_references": [
                {
                    "id": str(e.id),
                    "source_type": e.source_type,
                    "source_table": e.source_table,
                    "source_record_id": str(e.source_record_id),
                    "source_row_version": e.source_row_version,
                    "document_id": str(e.document_id) if e.document_id else None,
                    "document_version_id": str(e.document_version_id) if e.document_version_id else None,
                    "document_checksum_sha256": e.document_checksum_sha256,
                    "page_number": e.page_number,
                    "section_ref": e.section_ref,
                    "field_path": e.field_path,
                    "quoted_value": e.quoted_value,
                }
                for e in ev_refs
            ],
            "findings": [
                {
                    "id": str(f.id),
                    "finding_code": f.finding_code,
                    "category": f.category,
                    "title": f.title,
                    "statement": f.statement,
                    "is_verified_observation": f.is_verified_observation,
                    "severity": f.severity,
                    "evidence_ref_ids": list(f.evidence_ref_ids_json or []),
                    "source_origin": f.source_origin,
                }
                for f in findings
            ],
            "hypotheses": [
                {
                    "id": str(h.id),
                    "hypothesis_code": h.hypothesis_code,
                    "category": h.category,
                    "title": h.title,
                    "description": h.description,
                    "status": h.status,
                    "uncertainty_level": h.uncertainty_level,
                    "supporting_evidence_ids": list(h.supporting_evidence_ids_json or []),
                    "contradicting_evidence_ids": list(h.contradicting_evidence_ids_json or []),
                    "recommended_verification": h.recommended_verification,
                    "limitations": h.limitations,
                    "review_comments": h.review_comments,
                }
                for h in hypotheses
            ],
            "limitations": limitations,
            "qa_decision": {
                "investigation_qa_decision": investigation.qa_decision,
                "qa_reviewed_at": investigation.qa_reviewed_at.isoformat()
                if investigation.qa_reviewed_at
                else None,
                "qa_decision_comments": investigation.qa_decision_comments,
                "approval_record_id": str(investigation.approval_record_id)
                if investigation.approval_record_id
                else None,
            },
            "deviations": [
                {
                    "id": str(d.id),
                    "deviation_number": d.deviation_number,
                    "classification": d.classification,
                    "status": d.status,
                    "title": d.title,
                }
                for d in deviations
            ],
        }

    def generate_report(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        investigation_id: uuid.UUID,
        payload: ReportGenerateRequest,
        source_ip: Optional[str] = None,
    ) -> GeneratedReport:
        investigation = self._repo.get_investigation_for_org(
            db, tenant_ctx.organization_id, investigation_id
        )
        if investigation is None:
            raise NotFoundError("Investigation not found.")
        self._assert_site_access(
            tenant_ctx,
            investigation.site_id,
            target_resource=f"/api/v1/investigations/{investigation_id}/reports",
        )

        if payload.run_id is not None:
            run_obj = self._repo.get_run_for_org(
                db,
                tenant_ctx.organization_id,
                payload.run_id,
                investigation_id=investigation.id,
            )
            if run_obj is None:
                raise NotFoundError("Specified investigation run not found for this investigation.")

        now = datetime.now(timezone.utc)
        next_ver = self._repo.get_next_report_version(
            db, tenant_ctx.organization_id, investigation.id
        )
        org = db.get(Organization, tenant_ctx.organization_id)
        provisional_num = f"{investigation.investigation_number}-RPT-v{next_ver}"
        title = (
            payload.title.strip()
            if payload.title
            else f"Investigation Report {investigation.investigation_number} (v{next_ver})"
        )

        snapshot = self._build_persisted_snapshot(
            db,
            investigation=investigation,
            run_id=payload.run_id,
            report_number=provisional_num,
            version_number=next_ver,
            title=title,
        )
        # Close/release any implicit read transaction BEFORE external storage/DOCX I/O
        db.rollback()

        docx_bytes = render_investigation_docx_bytes(snapshot)
        checksum = hashlib.sha256(docx_bytes).hexdigest()
        doc_id = uuid.uuid4()
        ver_id = uuid.uuid4()
        object_key = build_vault_object_key(tenant_ctx.organization_id, doc_id, ver_id)
        safe_filename = f"{investigation.investigation_number}_v{next_ver}.docx"

        # Write binary to ObjectStorageProvider strictly BEFORE/OUTSIDE DB UnitOfWork
        self._storage.put_object(object_key, docx_bytes, content_type=DOCX_MIME_TYPE)
        try:
            with UnitOfWork(db):
                inv = self._repo.get_investigation_for_org(
                    db, tenant_ctx.organization_id, investigation_id, for_update=True
                )
                if inv is None:
                    raise NotFoundError("Investigation not found.")

                report_number = next_tenant_sequence(
                    db,
                    tenant_ctx.organization_id,
                    "INVESTIGATION_REPORT",
                    period_year=now.year,
                    prefix=_org_report_prefix(org),
                )
                snapshot["control_page"]["report_number"] = report_number

                doc = self._doc_service.upload_in_tx(
                    db,
                    organization_id=tenant_ctx.organization_id,
                    uploaded_by_user_id=tenant_ctx.user_id,
                    file_bytes=docx_bytes,
                    original_filename=safe_filename,
                    claimed_mime_type=DOCX_MIME_TYPE,
                    category=DocumentType.INVESTIGATION_REPORT.value,
                    site_id=inv.site_id,
                    title=title,
                    owner_membership_id=tenant_ctx.membership_id,
                    metadata_json={
                        "investigation_id": str(inv.id),
                        "report_number": report_number,
                        "version_number": next_ver,
                    },
                    pre_stored=(doc_id, ver_id, object_key, safe_filename, DOCX_MIME_TYPE, checksum),
                )

                prior_reports = self._repo.list_reports_for_investigation(
                    db, tenant_ctx.organization_id, inv.id
                )
                latest_prior = prior_reports[-1] if prior_reports else None
                for prev in prior_reports:
                    if prev.status in {
                        ReportStatus.DRAFT.value,
                        ReportStatus.GENERATED.value,
                        ReportStatus.UNDER_REVIEW.value,
                        ReportStatus.APPROVED.value,
                    }:
                        prev.status = ReportStatus.SUPERSEDED.value
                        prev.row_version += 1

                report = GeneratedReport(
                    organization_id=tenant_ctx.organization_id,
                    investigation_id=inv.id,
                    investigation_run_id=payload.run_id,
                    report_number=report_number,
                    version_number=next_ver,
                    title=title,
                    status=ReportStatus.GENERATED.value,
                    document_id=doc.id,
                    document_version_id=ver_id,
                    checksum_sha256=checksum,
                    report_snapshot_json=snapshot,
                    generated_by_user_id=tenant_ctx.user_id,
                    generated_at=now,
                    supersedes_report_id=latest_prior.id if latest_prior else None,
                    created_by_user_id=tenant_ctx.user_id,
                    updated_by_user_id=tenant_ctx.user_id,
                )
                db.add(report)
                db.flush()

                self._audit.record(
                    db,
                    organization_id=tenant_ctx.organization_id,
                    site_id=inv.site_id,
                    actor_type=ActorType.USER.value,
                    actor_id=tenant_ctx.user_id,
                    action="INVESTIGATION_REPORT_GENERATED",
                    entity_type="generated_reports",
                    entity_id=report.id,
                    source_ip=source_ip,
                    after_state={
                        "report_number": report.report_number,
                        "version_number": report.version_number,
                        "document_version_id": str(ver_id),
                        "checksum_sha256": checksum,
                    },
                )
                self._outbox.enqueue(
                    db,
                    organization_id=tenant_ctx.organization_id,
                    aggregate_type="generated_report",
                    aggregate_id=report.id,
                    event_type="ReportGenerated",
                    payload_json={
                        "report_id": str(report.id),
                        "investigation_id": str(inv.id),
                        "organization_id": str(tenant_ctx.organization_id),
                        "report_number": report.report_number,
                    },
                )
            db.refresh(report)
            return report
        except Exception:
            self._storage.delete_object(object_key)
            raise

    def list_reports(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        investigation_id: uuid.UUID,
    ) -> List[GeneratedReport]:
        inv = self._repo.get_investigation_for_org(db, tenant_ctx.organization_id, investigation_id)
        if inv is None:
            raise NotFoundError("Investigation not found.")
        self._assert_site_access(
            tenant_ctx,
            inv.site_id,
            target_resource=f"/api/v1/investigations/{investigation_id}/reports",
        )
        return self._repo.list_reports_for_investigation(db, tenant_ctx.organization_id, inv.id)

    def download_report(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        report_id: uuid.UUID,
        investigation_id: Optional[uuid.UUID] = None,
        source_ip: Optional[str] = None,
    ) -> Tuple[GeneratedReport, DocumentVersion, bytes]:
        report = self._repo.get_report_for_org(
            db,
            tenant_ctx.organization_id,
            report_id,
            investigation_id=investigation_id,
        )
        if report is None:
            raise NotFoundError("Generated report not found.")
        inv = self._repo.get_investigation_for_org(
            db, tenant_ctx.organization_id, report.investigation_id
        )
        if inv is None:
            raise NotFoundError("Investigation not found.")
        self._assert_site_access(
            tenant_ctx,
            inv.site_id,
            target_resource=f"/api/v1/reports/{report_id}/download",
        )
        if report.document_version_id is None:
            raise NotFoundError("Report document version is missing.")
        pair = self._doc_repo.get_version_with_document(db, report.document_version_id)
        if pair is None or pair[1].organization_id != tenant_ctx.organization_id:
            raise NotFoundError("Report document version not found.")
        ver, doc = pair
        if ver.scan_status != ScanStatus.CLEAN.value:
            raise PermissionDeniedError("Report binary is not CLEAN for download.")

        raw_bytes = self._storage.get_object(ver.object_key)
        actual_sha = hashlib.sha256(raw_bytes).hexdigest()
        if actual_sha != ver.checksum_sha256 or (
            report.checksum_sha256 and actual_sha != report.checksum_sha256
        ):
            self._audit.record_security_event(
                event_type="REPORT_CHECKSUM_TAMPER_DETECTED",
                outcome=SecurityEventOutcome.BLOCKED.value,
                severity=SecurityEventSeverity.CRITICAL.value,
                organization_id=tenant_ctx.organization_id,
                user_id=tenant_ctx.user_id,
                source_ip=source_ip,
                target_resource=f"/api/v1/reports/{report_id}/download",
            )
            raise AppError(
                "Stored investigation report integrity verification failed.",
                status_code=500,
                error_code="REPORT_INTEGRITY_ERROR",
            )

        with UnitOfWork(db):
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=inv.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="INVESTIGATION_REPORT_DOWNLOADED",
                entity_type="generated_reports",
                entity_id=report.id,
                source_ip=source_ip,
            )
        return report, ver, raw_bytes

    def review_report(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        report_id: uuid.UUID,
        payload: ReportReviewRequest,
        investigation_id: Optional[uuid.UUID] = None,
        source_ip: Optional[str] = None,
    ) -> GeneratedReport:
        report = self._repo.get_report_for_org(
            db,
            tenant_ctx.organization_id,
            report_id,
            investigation_id=investigation_id,
        )
        if report is None:
            raise NotFoundError("Generated report not found.")
        inv = self._repo.get_investigation_for_org(
            db, tenant_ctx.organization_id, report.investigation_id
        )
        if inv is None:
            raise NotFoundError("Investigation not found.")
        self._assert_site_access(
            tenant_ctx,
            inv.site_id,
            target_resource=f"/api/v1/reports/{report_id}/review",
        )

        # Dual user_id maker-checker enforcement
        if report.generated_by_user_id is not None and report.generated_by_user_id == tenant_ctx.user_id:
            self._audit.record_security_event(
                event_type="MAKER_CHECKER_VIOLATION",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.HIGH.value,
                organization_id=tenant_ctx.organization_id,
                user_id=tenant_ctx.user_id,
                source_ip=source_ip,
                target_resource=f"/api/v1/reports/{report_id}/review",
                details_json={"report_id": str(report.id), "generated_by_user_id": str(report.generated_by_user_id)},
            )
            raise PermissionDeniedError(
                "Maker-checker violation: report reviewer must be a different user than the report generator.",
                error_code="MAKER_CHECKER_VIOLATION",
            )

        target_status = (
            ReportStatus.APPROVED.value
            if payload.decision == "APPROVED"
            else (
                ReportStatus.UNDER_REVIEW.value
                if payload.decision == "REQUEST_MORE_WORK"
                else ReportStatus.SUPERSEDED.value
            )
        )
        assert_transition(
            REPORT_TRANSITIONS,
            report.status,
            target_status,
            entity_name="GeneratedReport",
        )

        if tenant_ctx.membership_id is None:
            raise PermissionDeniedError("Active organization membership is required to review reports.")

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            locked_report = self._repo.get_report_for_org(
                db, tenant_ctx.organization_id, report.id, for_update=True
            )
            if locked_report is None:
                raise NotFoundError("Generated report not found.")
            verify_and_increment_version(locked_report, payload.row_version)
            prev_status = locked_report.status

            approval = ApprovalRecord(
                organization_id=tenant_ctx.organization_id,
                entity_type="generated_report",
                entity_id=locked_report.id,
                approval_stage="QA_REPORT_REVIEW",
                decision=payload.decision,
                approver_membership_id=tenant_ctx.membership_id,
                decided_at=now,
                comments=payload.comments.strip(),
            )
            db.add(approval)
            db.flush()

            locked_report.status = target_status
            locked_report.reviewed_by_user_id = tenant_ctx.user_id
            locked_report.reviewed_by_membership_id = tenant_ctx.membership_id
            locked_report.reviewed_at = now
            locked_report.review_comments = payload.comments.strip()
            locked_report.approval_record_id = approval.id
            locked_report.updated_by_user_id = tenant_ctx.user_id

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=inv.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="INVESTIGATION_REPORT_REVIEWED",
                entity_type="generated_reports",
                entity_id=locked_report.id,
                reason=payload.comments.strip(),
                source_ip=source_ip,
                before_state={"status": prev_status},
                after_state={"status": target_status, "approval_record_id": str(approval.id)},
            )
            self._outbox.enqueue(
                db,
                organization_id=tenant_ctx.organization_id,
                aggregate_type="generated_report",
                aggregate_id=locked_report.id,
                event_type="ReportReviewed",
                payload_json={
                    "report_id": str(locked_report.id),
                    "investigation_id": str(inv.id),
                    "organization_id": str(tenant_ctx.organization_id),
                    "decision": payload.decision,
                    "status": target_status,
                },
            )
        db.refresh(locked_report)
        return locked_report
