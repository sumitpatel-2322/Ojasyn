import ast
import contextlib
import io
import re
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.domain.enums import (
    BatchResolutionOutcome,
    DeviationClassification,
    DeviationSourceType,
    EvidenceSourceType,
    FindingCategory,
    HypothesisCategory,
    InvestigationTriggerType,
    UncertaintyLevel,
)
from app.domain.rbac_catalog import PERMISSION_CODES, ROLE_PERMISSION_MAP, WORKER_PERMISSIONS
from app.models.domain import Base
from app.worker import OutboxWorker


BACKEND_DIR = Path(__file__).resolve().parents[3]


def test_alembic_0006_chain_pg_sql_and_column_parity() -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option(
        "sqlalchemy.url",
        "postgresql+psycopg://user:pass@localhost:5432/ojasyn",
    )
    sd = ScriptDirectory.from_config(cfg)
    assert len(sd.get_heads()) == 1

    revisions = list(sd.walk_revisions(base="base", head="0006_phase5_investigations"))
    assert [r.revision for r in revisions] == [
        "0006_phase5_investigations",
        "0005_phase4_mfg_evidence",
        "0004_phase3_traceability",
        "0003_phase2_docs_complaints",
        "0002_phase1_tenancy_actors",
        "0001_phase0_foundation",
    ]
    for rev in revisions:
        assert len(rev.revision) <= 32

    up_buf = io.StringIO()
    with contextlib.redirect_stdout(up_buf):
        command.upgrade(cfg, "0005_phase4_mfg_evidence:0006_phase5_investigations", sql=True)
    up_sql = up_buf.getvalue()

    phase5_tables = {
        "investigations",
        "investigation_runs",
        "investigation_steps",
        "evidence_references",
        "sop_compliance_results",
        "investigation_findings",
        "root_cause_hypotheses",
        "generated_reports",
        "deviations",
    }
    for tbl in phase5_tables:
        assert f"CREATE TABLE {tbl}" in up_sql
    assert "trg_prevent_mutation_evidence_references" in up_sql
    assert "trg_prevent_mutation_sop_compliance_results" in up_sql
    assert "ON DELETE CASCADE" not in up_sql.upper()

    for ident in re.findall(r"(?:CONSTRAINT|INDEX)\s+([a-zA-Z0-9_]+)", up_sql):
        assert len(ident) <= 63, f"Identifier > 63 chars: {ident}"

    for tbl_name in phase5_tables:
        tbl = Base.metadata.tables[tbl_name]
        for constraint in tbl.constraints:
            if constraint.name:
                assert len(constraint.name) <= 63
        for idx in tbl.indexes:
            if idx.name:
                assert len(idx.name) <= 63

    down_buf = io.StringIO()
    with contextlib.redirect_stdout(down_buf):
        command.downgrade(cfg, "0006_phase5_investigations:0005_phase4_mfg_evidence", sql=True)
    down_sql = down_buf.getvalue()
    for tbl in phase5_tables:
        assert f"DROP TABLE {tbl}" in down_sql
    assert "DROP CONSTRAINT fk_investigations_org_dev" in down_sql
    assert "DROP CONSTRAINT fk_investigations_deviation_id" in down_sql

    # Verify AST column parity between 0006_phase5_investigations.py and ORM models
    mig_src = (BACKEND_DIR / "alembic" / "versions" / "0006_phase5_investigations.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(mig_src)
    common_cols = {
        "id",
        "organization_id",
        "created_at",
        "updated_at",
        "created_by_user_id",
        "updated_by_user_id",
        "row_version",
    }
    mig_tables: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_table"
        ):
            tname = node.args[0].value
            cols: set[str] = set()
            for arg in node.args[1:]:
                if isinstance(arg, ast.Starred):
                    cols |= common_cols
                elif (
                    isinstance(arg, ast.Call)
                    and isinstance(arg.func, ast.Attribute)
                    and arg.func.attr == "Column"
                ):
                    cols.add(arg.args[0].value)
            mig_tables[tname] = cols

    assert set(mig_tables.keys()) == phase5_tables
    for tbl_name in phase5_tables:
        assert mig_tables[tbl_name] == set(Base.metadata.tables[tbl_name].columns.keys())


def test_phase5_enums_rbac_and_worker_hooks() -> None:
    assert {
        "MATCHED",
        "MULTIPLE_CANDIDATES",
        "MANUAL_LINKED",
        "NOT_FOUND",
        "INSUFFICIENT_INFO",
        "HUMAN_VERIFICATION_REQUIRED",
    }.issubset({e.value for e in BatchResolutionOutcome})
    assert "COMPLAINT" in {e.value for e in InvestigationTriggerType}
    assert "SOP_STEP" in {e.value for e in EvidenceSourceType}
    assert "SOP_NON_COMPLIANCE" in {e.value for e in FindingCategory}
    assert "MANUFACTURING_PROCESS" in {e.value for e in HypothesisCategory}
    assert "HIGH" in {e.value for e in UncertaintyLevel}
    assert {"MINOR", "MAJOR", "CRITICAL"} == {e.value for e in DeviationClassification}
    assert "SOP_COMPLIANCE_RESULT" in {e.value for e in DeviationSourceType}

    assert {"deviation.close", "report.review"}.issubset(PERMISSION_CODES)
    assert {"deviation.close", "report.review"}.issubset(ROLE_PERMISSION_MAP["QMS_HEAD"])
    assert {"deviation.close", "report.review"}.issubset(ROLE_PERMISSION_MAP["QA_HEAD"])
    assert "deviation.close" not in ROLE_PERMISSION_MAP["QA_OFFICER"]
    assert "report.review" not in ROLE_PERMISSION_MAP["QA_OFFICER"]
    assert WORKER_PERMISSIONS.isdisjoint({"deviation.close", "report.review"})

    worker = OutboxWorker(session_factory=lambda: None)  # type: ignore[arg-type]
    assert "InvestigationRunRequested" in worker._external_handlers
    assert "InvestigationRunResumeRequested" in worker._external_handlers
    for ev in (
        "InvestigationOpened",
        "InvestigationRunCompleted",
        "InvestigationRunWaitingHuman",
        "InvestigationRunFailed",
        "InvestigationReviewed",
        "ComplaintBatchResolved",
        "ComplaintClosed",
        "ReportGenerated",
        "ReportReviewed",
        "DeviationCreated",
        "DeviationSubmittedForReview",
        "DeviationClosed",
    ):
        assert ev in worker._handlers
