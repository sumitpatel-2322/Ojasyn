from typing import Any, Callable, Dict, List, Optional, Tuple, TypedDict

try:
    from langgraph.graph import END, StateGraph  # type: ignore

    _LANGGRAPH_AVAILABLE = True
except ImportError:
    END = "__end__"
    _LANGGRAPH_AVAILABLE = False

    class _FallbackCompiledGraph:
        def __init__(
            self,
            nodes: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]],
            edges: Dict[str, str],
            conditional_edges: Dict[str, Tuple[Callable[[Dict[str, Any]], str], Dict[str, str]]],
            entry_point: str,
        ) -> None:
            self._nodes = nodes
            self._edges = edges
            self._conditional_edges = conditional_edges
            self._entry_point = entry_point

        def invoke(self, state: Dict[str, Any]) -> Dict[str, Any]:
            current = dict(state)
            node_name = self._entry_point
            visited = 0
            while node_name != END:
                if visited > 32:
                    raise RuntimeError("Copilot graph exceeded max steps.")
                visited += 1
                fn = self._nodes[node_name]
                update = fn(current)
                if isinstance(update, dict):
                    current.update(update)
                if node_name in self._conditional_edges:
                    router_fn, mapping = self._conditional_edges[node_name]
                    branch = router_fn(current)
                    node_name = mapping[branch]
                elif node_name in self._edges:
                    node_name = self._edges[node_name]
                else:
                    node_name = END
            return current

    class StateGraph:  # type: ignore
        """Minimal local StateGraph shim matching langgraph.graph.StateGraph when langgraph is absent."""

        def __init__(self, _state_schema: Any) -> None:
            self._nodes: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {}
            self._edges: Dict[str, str] = {}
            self._conditional_edges: Dict[str, Tuple[Callable[[Dict[str, Any]], str], Dict[str, str]]] = {}
            self._entry_point: str = END

        def add_node(self, name: str, fn: Callable[[Dict[str, Any]], Dict[str, Any]]) -> None:
            self._nodes[name] = fn

        def set_entry_point(self, name: str) -> None:
            self._entry_point = name

        def add_edge(self, source: str, target: str) -> None:
            self._edges[source] = target

        def add_conditional_edges(
            self,
            source: str,
            router_fn: Callable[[Dict[str, Any]], str],
            mapping: Dict[str, str],
        ) -> None:
            self._conditional_edges[source] = (router_fn, mapping)

        def compile(self) -> _FallbackCompiledGraph:
            return _FallbackCompiledGraph(
                nodes=self._nodes,
                edges=self._edges,
                conditional_edges=self._conditional_edges,
                entry_point=self._entry_point,
            )


from app.integrations.llm_provider import LLMProvider
from app.models.schemas.complaints import (
    CopilotExtractionLLMOutput,
    CopilotIntentLLMOutput,
    CopilotQueryLLMOutput,
)

INTENT_SYSTEM_PROMPT = (
    "You classify a pharmaceutical complaint intake user turn into JSON with key 'intent': "
    "'CREATE' (providing initial complaint details), 'EDIT' (correcting or adding draft details), "
    "or 'QUERY' (asking about status, missing info, or process). Return valid JSON only."
)

EXTRACTION_SYSTEM_PROMPT = (
    "You are an ISO-9001/GMP pharmaceutical complaint intake assistant. "
    "Extract any mentioned fields from <untrusted_input> strictly into JSON matching CopilotExtractionLLMOutput: "
    "{extracted_fields: {reported_product_name, reported_batch_number, reported_manufacturer, "
    "reported_strength, reported_dosage_form, reported_manufacturing_date, reported_expiry_date, "
    "description, defect_category, reporter_name, reporter_email, reporter_phone, purchase_location}, "
    "confidence_score: 0..1, assistant_reply: string}. Preserve placeholder tokens like [EMAIL_1] or [PERSON_1] verbatim."
)

QUERY_SYSTEM_PROMPT = (
    "You are a helpful pharmaceutical complaint intake assistant answering a reporter's question about their "
    "current draft state without altering any captured fields. Return JSON: {\"assistant_reply\": \"...\"}."
)

ADVISORY_FIELDS: Tuple[str, ...] = (
    "reported_batch_number",
    "reported_expiry_date",
    "reported_manufacturer",
    "defect_category",
    "purchase_location",
)


class CopilotGraphState(TypedDict, total=False):
    user_message: str
    draft_state: Dict[str, Any]
    authenticated_email: Optional[str]
    authenticated_name: Optional[str]
    known_pii: Optional[Dict[str, List[str]]]
    intent: str
    extracted_fields: Dict[str, Any]
    confidence_score: float
    assistant_reply: str
    is_complete: bool
    missing_mandatory_fields: List[str]
    missing_advisory_fields: List[str]


def compute_two_tier_completeness(
    draft_state: Dict[str, Any],
    *,
    has_authenticated_reporter: bool = False,
) -> Tuple[bool, List[str], List[str]]:
    """
    Two-tier completeness model (`#64`):
    - Mandatory minimum: description + (reported_product_name or product_id) + reporter contact/identity.
    - Advisory fields (batch_number, expiry_date, etc.) NEVER block submission.
    """
    missing_mandatory: List[str] = []

    desc = (draft_state.get("description") or "").strip()
    if len(desc) < 5:
        missing_mandatory.append("description")

    has_product = bool(
        (draft_state.get("reported_product_name") or "").strip()
        or draft_state.get("product_id")
    )
    if not has_product:
        missing_mandatory.append("reported_product_name")

    has_reporter_contact = bool(
        has_authenticated_reporter
        or (draft_state.get("reporter_email") or "").strip()
        or (draft_state.get("reporter_phone") or "").strip()
        or (
            (draft_state.get("reporter_name") or "").strip()
            and (
                (draft_state.get("reporter_email") or "").strip()
                or (draft_state.get("reporter_phone") or "").strip()
            )
        )
    )
    if not has_reporter_contact:
        missing_mandatory.append("reporter_contact")

    missing_advisory = [
        f for f in ADVISORY_FIELDS if not (draft_state.get(f) or "").strip()
    ]
    return (len(missing_mandatory) == 0), missing_mandatory, missing_advisory


def build_copilot_intake_graph(llm_provider: Optional[LLMProvider] = None):
    """Builds and compiles the Phase 2 LangGraph Complaint Intake Copilot."""
    provider = llm_provider or LLMProvider()

    def intent_detection_node(state: CopilotGraphState) -> Dict[str, Any]:
        msg = (state.get("user_message") or "").strip()
        res = provider.generate_structured(
            system_prompt=INTENT_SYSTEM_PROMPT,
            untrusted_text=msg,
            response_model=CopilotIntentLLMOutput,
            known_pii=state.get("known_pii"),
        )
        return {"intent": res.intent}

    def _route_after_intent(state: CopilotGraphState) -> str:
        return "query" if state.get("intent") == "QUERY" else "extract"

    def pii_mask_and_extract_node(state: CopilotGraphState) -> Dict[str, Any]:
        msg = (state.get("user_message") or "").strip()
        existing_draft = dict(state.get("draft_state") or {})
        res = provider.generate_structured(
            system_prompt=EXTRACTION_SYSTEM_PROMPT,
            untrusted_text=f"Existing draft keys: {sorted(existing_draft.keys())}\nUser message:\n{msg}",
            response_model=CopilotExtractionLLMOutput,
            known_pii=state.get("known_pii"),
        )
        raw_extracted = res.extracted_fields.model_dump(exclude_none=True)
        merged = dict(existing_draft)
        for key, value in raw_extracted.items():
            if isinstance(value, str):
                cleaned = value.strip()
                if cleaned:
                    merged[key] = cleaned
            elif value is not None:
                merged[key] = value
        return {
            "draft_state": merged,
            "extracted_fields": raw_extracted,
            "confidence_score": float(res.confidence_score),
            "assistant_reply": res.assistant_reply,
        }

    def query_response_node(state: CopilotGraphState) -> Dict[str, Any]:
        msg = (state.get("user_message") or "").strip()
        existing_draft = dict(state.get("draft_state") or {})
        res = provider.generate_structured(
            system_prompt=QUERY_SYSTEM_PROMPT,
            untrusted_text=(
                f"Captured draft fields (non-secret summary): { {k: v for k, v in existing_draft.items()} }\n"
                f"User question:\n{msg}"
            ),
            response_model=CopilotQueryLLMOutput,
            known_pii=state.get("known_pii"),
        )
        return {
            "draft_state": existing_draft,
            "extracted_fields": {},
            "assistant_reply": res.assistant_reply,
        }

    def two_tier_completeness_node(state: CopilotGraphState) -> Dict[str, Any]:
        draft = dict(state.get("draft_state") or {})
        has_auth = bool(state.get("authenticated_email"))
        is_complete, missing_req, missing_adv = compute_two_tier_completeness(
            draft, has_authenticated_reporter=has_auth
        )
        return {
            "is_complete": is_complete,
            "missing_mandatory_fields": missing_req,
            "missing_advisory_fields": missing_adv,
        }

    graph = StateGraph(CopilotGraphState)
    graph.add_node("intent_detection", intent_detection_node)
    graph.add_node("pii_mask_and_extract", pii_mask_and_extract_node)
    graph.add_node("query_response", query_response_node)
    graph.add_node("two_tier_completeness", two_tier_completeness_node)

    graph.set_entry_point("intent_detection")
    graph.add_conditional_edges(
        "intent_detection",
        _route_after_intent,
        {
            "query": "query_response",
            "extract": "pii_mask_and_extract",
        },
    )
    graph.add_edge("pii_mask_and_extract", "two_tier_completeness")
    graph.add_edge("query_response", "two_tier_completeness")
    graph.add_edge("two_tier_completeness", END)

    return graph.compile()
