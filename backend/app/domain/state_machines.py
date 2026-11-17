from typing import Dict, Iterable, Set

from app.core.exceptions import ConflictError


def assert_transition(
    transitions: Dict[str, Set[str]],
    current_state: str,
    target_state: str,
    *,
    entity_name: str = "Record",
) -> None:
    """
    Validates a lifecycle state transition against an explicit transition table.
    Raises ConflictError (409) when the requested transition is not permitted.
    """
    allowed: Iterable[str] = transitions.get(current_state, set())
    if target_state not in allowed:
        raise ConflictError(
            f"Invalid {entity_name} transition from '{current_state}' to '{target_state}'.",
            error_code="INVALID_STATE_TRANSITION",
            details={
                "entity": entity_name,
                "current_state": current_state,
                "target_state": target_state,
                "allowed_targets": sorted(allowed),
            },
        )
