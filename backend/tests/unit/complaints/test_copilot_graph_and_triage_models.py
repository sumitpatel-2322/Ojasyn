import json
from typing import List

import pytest

from app.core.config import Settings
from app.core.exceptions import ConflictError
from app.domain.enums import ComplaintStatus
from app.domain.state_machines import assert_transition
from app.integrations.llm_provider import LLMProvider
from app.services.complaint_service import COMPLAINT_TRANSITIONS
from app.workflows.copilot_intake_graph import (
    build_copilot_intake_graph,
    compute_two_tier_completeness,
)


class MockCopilotTransport:
    """Captures outbound prompts to guarantee PII never leaves the local trust boundary."""

    def __init__(self, responses: List[dict]) -> None:
        self.responses = list(responses)
        self.captured_prompts: List[str] = []

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model_name: str,
        temperature: float,
        max_tokens: int,
        timeout_seconds: float,
    ) -> str:
        self.captured_prompts.append(user_prompt)
        if not self.responses:
            raise AssertionError("MockCopilotTransport ran out of scripted responses")
        return json.dumps(self.responses.pop(0))


def test_two_tier_completeness_never_blocks_on_missing_batch() -> None:
    complete, req, adv = compute_two_tier_completeness(
        {
            "reported_product_name": "Amoxicillin 500mg",
            "description": "Blister seal appears broken on two strips.",
            "reporter_email": "patient@example.com",
        }
    )
    assert complete is True
    assert req == []
    assert "reported_batch_number" in adv

    incomplete, missing_req, _ = compute_two_tier_completeness({"reported_batch_number": "B999"})
    assert incomplete is False
    assert set(missing_req) == {"description", "reported_product_name", "reporter_contact"}


def test_copilot_graph_pii_redaction_and_intent_branching() -> None:
    settings = Settings(
        DATABASE_URL="sqlite:///./copilot_unit.db",
        JWT_SIGNING_KEY="a_valid_random_secret_key_at_least_32_characters",
    )
    transport = MockCopilotTransport(
        [
            {"intent": "CREATE"},
            {
                "extracted_fields": {
                    "reported_product_name": "Paracetamol 650",
                    "description": "Tablet crumbling reported by [PERSON_1] at [EMAIL_1] / [PHONE_1].",
                    "reporter_name": "[PERSON_1]",
                    "reporter_email": "[EMAIL_1]",
                    "reporter_phone": "[PHONE_1]",
                },
                "confidence_score": "0.9400",
                "assistant_reply": "Captured Paracetamol 650 issue for [PERSON_1].",
            },
            {"intent": "QUERY"},
            {
                "assistant_reply": "Your draft is ready to submit; batch number is optional.",
            },
        ]
    )
    provider = LLMProvider(transport=transport, settings=settings)
    graph = build_copilot_intake_graph(llm_provider=provider)

    turn1 = graph.invoke(
        {
            "user_message": "Customer name: Rohan Mehta\nEmail: rohan.mehta@example.com\nPhone: +91 9876543210\nParacetamol 650 tablet crumbling.",
            "draft_state": {},
        }
    )
    assert turn1["intent"] == "CREATE"
    assert turn1["is_complete"] is True
    assert "reported_batch_number" in turn1["missing_advisory_fields"]
    # Unmasked locally cleanly
    assert turn1["draft_state"]["reporter_email"] == "rohan.mehta@example.com"
    assert turn1["draft_state"]["reporter_phone"] == "+91 9876543210"
    assert turn1["draft_state"]["reporter_name"] == "Rohan Mehta"

    # Outbound LLM prompt NEVER saw raw PII
    for prompt in transport.captured_prompts:
        assert "rohan.mehta@example.com" not in prompt
        assert "9876543210" not in prompt
        assert "Rohan Mehta" not in prompt

    # QUERY turn does not mutate captured draft fields
    turn2 = graph.invoke(
        {
            "user_message": "Can I submit without batch number?",
            "draft_state": dict(turn1["draft_state"]),
        }
    )
    assert turn2["intent"] == "QUERY"
    assert turn2["draft_state"] == turn1["draft_state"]
    assert turn2["is_complete"] is True


def test_complaint_state_machine_transitions() -> None:
    assert_transition(
        COMPLAINT_TRANSITIONS,
        ComplaintStatus.UNROUTED_TRIAGE.value,
        ComplaintStatus.RECEIVED.value,
        entity_name="Complaint",
    )
    assert_transition(
        COMPLAINT_TRANSITIONS,
        ComplaintStatus.RECEIVED.value,
        ComplaintStatus.TRIAGED.value,
        entity_name="Complaint",
    )
    with pytest.raises(ConflictError):
        assert_transition(
            COMPLAINT_TRANSITIONS,
            ComplaintStatus.CLOSED.value,
            ComplaintStatus.TRIAGED.value,
            entity_name="Complaint",
        )
