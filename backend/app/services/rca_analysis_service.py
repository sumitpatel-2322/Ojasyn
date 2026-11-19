import uuid
from typing import Iterable, List, Optional, Sequence, Set, Tuple

from sqlalchemy.orm import Session

from app.core.exceptions import ValidationError
from app.domain.enums import (
    FindingCategory,
    HypothesisStatus,
    SOPComplianceResultStatus,
    Severity,
)
from app.models.domain.investigations import (
    Investigation,
    InvestigationFinding,
    InvestigationRun,
    RootCauseHypothesis,
    SOPComplianceResult,
)
from app.models.schemas.investigations import (
    RCASynthesisFindingItem,
    RCASynthesisHypothesisItem,
    RCASynthesisLLMOutput,
)
from app.repositories.investigation_repo import InvestigationRepository


class RCAAnalysisService:
    """
    Evidence-Gated RCA Findings & Hypotheses Service (`D5`).
    - Distinguishes verified deterministic observations (`is_verified_observation=True`)
      from AI/analyst inferences (`is_verified_observation=False`).
    - Rejects ANY finding or hypothesis citing non-existent or cross-investigation EvidenceReference IDs.
    - Never creates CAPA records.
    """

    def __init__(self, repo: Optional[InvestigationRepository] = None) -> None:
        self._repo = repo or InvestigationRepository()

    def validate_evidence_citations(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        investigation_id: uuid.UUID,
        cited_ids: Iterable[uuid.UUID],
        require_non_empty: bool = False,
        context_label: str = "citation",
    ) -> List[str]:
        cited_list = list(dict.fromkeys(cited_ids))
        if require_non_empty and not cited_list:
            raise ValidationError(
                f"{context_label} must cite at least one persisted EvidenceReference ID.",
                error_code="EVIDENCE_CITATION_REQUIRED",
            )
        if not cited_list:
            return []

        valid_ids: Set[uuid.UUID] = self._repo.get_evidence_ids_for_investigation(
            db, organization_id, investigation_id
        )
        invalid = [str(eid) for eid in cited_list if eid not in valid_ids]
        if invalid:
            raise ValidationError(
                f"Rejected {context_label}: cited evidence reference ID(s) do not exist in this investigation: {invalid}",
                error_code="INVALID_EVIDENCE_CITATION",
                details={"invalid_evidence_ids": invalid, "investigation_id": str(investigation_id)},
            )
        return [str(eid) for eid in cited_list]

    def persist_deterministic_findings_in_tx(
        self,
        db: Session,
        *,
        investigation: Investigation,
        run: Optional[InvestigationRun],
        compliance_results: Sequence[SOPComplianceResult],
        created_by_user_id: Optional[uuid.UUID],
    ) -> List[InvestigationFinding]:
        """
        Creates verified observation findings (`is_verified_observation=True`) directly from
        persisted deterministic SOP compliance results inside the caller's transaction.
        """
        existing = self._repo.list_findings_for_investigation(
            db, investigation.organization_id, investigation.id
        )
        existing_codes = {f.finding_code for f in existing}
        seq = len(existing) + 1
        created: List[InvestigationFinding] = []

        for idx, comp in enumerate(compliance_results, start=1):
            if comp.status == SOPComplianceResultStatus.NOT_APPLICABLE.value:
                continue
            # Keep deterministic findings focused on actionable issues + overall verified checks
            if comp.status == SOPComplianceResultStatus.PASS.value and idx > 5:
                continue

            if comp.status == SOPComplianceResultStatus.FAIL.value:
                category = (
                    FindingCategory.EQUIPMENT_CALIBRATION_ISSUE.value
                    if comp.check_type == "EQUIPMENT_CALIBRATION_VALIDITY"
                    else (
                        FindingCategory.QC_ANOMALY.value
                        if comp.check_type == "QC_RESULT_VS_SPECIFICATION"
                        else FindingCategory.SOP_NON_COMPLIANCE.value
                    )
                )
                severity = Severity.HIGH.value
            elif comp.status in {
                SOPComplianceResultStatus.MISSING.value,
                SOPComplianceResultStatus.UNVERIFIED.value,
            }:
                category = FindingCategory.DATA_MISSING_OR_UNVERIFIED.value
                severity = Severity.MEDIUM.value
            else:
                category = FindingCategory.CONFORMANCE.value
                severity = Severity.LOW.value

            ev_ids: List[str] = []
            if comp.evidence_ref_id is not None:
                ev_ids = self.validate_evidence_citations(
                    db,
                    organization_id=investigation.organization_id,
                    investigation_id=investigation.id,
                    cited_ids=[comp.evidence_ref_id],
                    require_non_empty=False,
                    context_label="deterministic finding",
                )

            code = f"FND-DET-{seq:03d}"
            while code in existing_codes:
                seq += 1
                code = f"FND-DET-{seq:03d}"
            existing_codes.add(code)
            seq += 1

            finding = InvestigationFinding(
                organization_id=investigation.organization_id,
                investigation_id=investigation.id,
                investigation_run_id=run.id if run else None,
                finding_code=code,
                category=category,
                title=f"{comp.rule_identifier}: {comp.status}"[:255],
                statement=comp.explanation,
                is_verified_observation=True,
                severity=severity,
                evidence_ref_ids_json=ev_ids,
                sop_compliance_result_id=comp.id,
                source_origin="DETERMINISTIC",
                created_by_user_id=created_by_user_id,
                updated_by_user_id=created_by_user_id,
            )
            db.add(finding)
            created.append(finding)

        db.flush()
        return created

    def persist_ai_synthesis_in_tx(
        self,
        db: Session,
        *,
        investigation: Investigation,
        run: Optional[InvestigationRun],
        synthesis: RCASynthesisLLMOutput,
        model_name: Optional[str],
        prompt_version: Optional[str],
        created_by_user_id: Optional[uuid.UUID],
    ) -> Tuple[List[InvestigationFinding], List[RootCauseHypothesis]]:
        """
        Validates every evidence citation in the LLM synthesis output against persisted
        `EvidenceReference` rows for THIS investigation, rejecting invalid/cross-investigation IDs.
        Persists AI findings (`is_verified_observation=False`) and `RootCauseHypothesis` (`PROPOSED`).
        """
        validated_findings: List[Tuple[RCASynthesisFindingItem, List[str]]] = []
        for item in synthesis.findings:
            ev_strs = self.validate_evidence_citations(
                db,
                organization_id=investigation.organization_id,
                investigation_id=investigation.id,
                cited_ids=item.evidence_ref_ids,
                require_non_empty=True,
                context_label=f"AI finding '{item.title}'",
            )
            validated_findings.append((item, ev_strs))

        validated_hypotheses: List[Tuple[RCASynthesisHypothesisItem, List[str], List[str]]] = []
        for hyp in synthesis.hypotheses:
            sup_strs = self.validate_evidence_citations(
                db,
                organization_id=investigation.organization_id,
                investigation_id=investigation.id,
                cited_ids=hyp.supporting_evidence_ids,
                require_non_empty=True,
                context_label=f"hypothesis '{hyp.title}' supporting_evidence_ids",
            )
            con_strs = self.validate_evidence_citations(
                db,
                organization_id=investigation.organization_id,
                investigation_id=investigation.id,
                cited_ids=hyp.contradicting_evidence_ids,
                require_non_empty=False,
                context_label=f"hypothesis '{hyp.title}' contradicting_evidence_ids",
            )
            validated_hypotheses.append((hyp, sup_strs, con_strs))

        existing_findings = self._repo.list_findings_for_investigation(
            db, investigation.organization_id, investigation.id
        )
        existing_f_codes = {f.finding_code for f in existing_findings}
        f_seq = len(existing_findings) + 1

        persisted_findings: List[InvestigationFinding] = []
        for item, ev_strs in validated_findings:
            code = f"FND-AI-{f_seq:03d}"
            while code in existing_f_codes:
                f_seq += 1
                code = f"FND-AI-{f_seq:03d}"
            existing_f_codes.add(code)
            f_seq += 1
            f_obj = InvestigationFinding(
                organization_id=investigation.organization_id,
                investigation_id=investigation.id,
                investigation_run_id=run.id if run else None,
                finding_code=code,
                category=item.category.value,
                title=item.title.strip(),
                statement=item.statement.strip(),
                is_verified_observation=False,
                severity=item.severity.value if item.severity else None,
                evidence_ref_ids_json=ev_strs,
                source_origin="AI_SYNTHESIS",
                created_by_user_id=created_by_user_id,
                updated_by_user_id=created_by_user_id,
            )
            db.add(f_obj)
            persisted_findings.append(f_obj)

        existing_hyps = self._repo.list_hypotheses_for_investigation(
            db, investigation.organization_id, investigation.id
        )
        existing_h_codes = {h.hypothesis_code for h in existing_hyps}
        h_seq = len(existing_hyps) + 1

        persisted_hyps: List[RootCauseHypothesis] = []
        for hyp, sup_strs, con_strs in validated_hypotheses:
            code = f"HYP-{h_seq:03d}"
            while code in existing_h_codes:
                h_seq += 1
                code = f"HYP-{h_seq:03d}"
            existing_h_codes.add(code)
            h_seq += 1
            h_obj = RootCauseHypothesis(
                organization_id=investigation.organization_id,
                investigation_id=investigation.id,
                investigation_run_id=run.id if run else None,
                hypothesis_code=code,
                category=hyp.category.value,
                title=hyp.title.strip(),
                description=hyp.description.strip(),
                supporting_evidence_ids_json=sup_strs,
                contradicting_evidence_ids_json=con_strs,
                uncertainty_level=hyp.uncertainty_level.value,
                confidence_score=hyp.confidence_score,
                recommended_verification=hyp.recommended_verification,
                limitations=hyp.limitations,
                status=HypothesisStatus.PROPOSED.value,
                model_name=model_name,
                prompt_version=prompt_version,
                created_by_user_id=created_by_user_id,
                updated_by_user_id=created_by_user_id,
            )
            db.add(h_obj)
            persisted_hyps.append(h_obj)

        db.flush()
        return persisted_findings, persisted_hyps
