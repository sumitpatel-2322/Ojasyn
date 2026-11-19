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
                    raise RuntimeError("Investigation graph exceeded max steps.")
                visited += 1
                if node_name in self._nodes:
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


class InvestigationGraphState(TypedDict, total=False):
    organization_id: str
    investigation_id: str
    run_id: str
    resume_from_step_seq: int
    paused_for_human: bool
    waiting_reason: Optional[str]
    batch_id: Optional[str]
    evidence_ref_ids: List[str]
    compliance_result_ids: List[str]
    finding_ids: List[str]
    hypothesis_ids: List[str]
    completed_steps: List[str]


def build_investigation_graph(
    *,
    batch_resolution_fn: Callable[[InvestigationGraphState], Dict[str, Any]],
    evidence_collection_fn: Callable[[InvestigationGraphState], Dict[str, Any]],
    sop_compliance_fn: Callable[[InvestigationGraphState], Dict[str, Any]],
    rca_synthesis_fn: Callable[[InvestigationGraphState], Dict[str, Any]],
    finalize_run_fn: Callable[[InvestigationGraphState], Dict[str, Any]],
):
    """
    Builds the durable Phase 5 Investigation LangGraph pipeline:
    dispatch_resume -> batch_resolution -> (paused? END : evidence_collection ->
    deterministic_sop_compliance -> rca_synthesis -> finalize_run -> END).
    """

    def dispatch_resume_node(state: InvestigationGraphState) -> Dict[str, Any]:
        return {}

    def _route_resume(state: InvestigationGraphState) -> str:
        seq = int(state.get("resume_from_step_seq") or 1)
        if seq <= 1:
            return "batch_resolution"
        if seq == 2:
            return "evidence_collection"
        if seq == 3:
            return "deterministic_sop_compliance"
        if seq == 4:
            return "rca_synthesis"
        return "finalize_run"

    def _route_after_batch(state: InvestigationGraphState) -> str:
        return "pause" if state.get("paused_for_human") else "continue"

    graph = StateGraph(InvestigationGraphState)
    graph.add_node("dispatch_resume", dispatch_resume_node)
    graph.add_node("batch_resolution", batch_resolution_fn)
    graph.add_node("evidence_collection", evidence_collection_fn)
    graph.add_node("deterministic_sop_compliance", sop_compliance_fn)
    graph.add_node("rca_synthesis", rca_synthesis_fn)
    graph.add_node("finalize_run", finalize_run_fn)

    graph.set_entry_point("dispatch_resume")
    graph.add_conditional_edges(
        "dispatch_resume",
        _route_resume,
        {
            "batch_resolution": "batch_resolution",
            "evidence_collection": "evidence_collection",
            "deterministic_sop_compliance": "deterministic_sop_compliance",
            "rca_synthesis": "rca_synthesis",
            "finalize_run": "finalize_run",
        },
    )
    graph.add_conditional_edges(
        "batch_resolution",
        _route_after_batch,
        {
            "pause": END,
            "continue": "evidence_collection",
        },
    )
    graph.add_edge("evidence_collection", "deterministic_sop_compliance")
    graph.add_edge("deterministic_sop_compliance", "rca_synthesis")
    graph.add_edge("rca_synthesis", "finalize_run")
    graph.add_edge("finalize_run", END)
    return graph.compile()
