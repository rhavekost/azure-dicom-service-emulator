"""State machine for UPS-RS workitems."""

import logging

logger = logging.getLogger(__name__)


class StateTransitionError(Exception):
    """Invalid state transition error."""
    pass


VALID_STATES = ["SCHEDULED", "IN PROGRESS", "COMPLETED", "CANCELED"]


def validate_state_transition(
    current_state: str,
    new_state: str,
    current_txn_uid: str | None,
    provided_txn_uid: str | None
) -> None:
    """
    Validate state transition and transaction UID.

    State machine:
    - SCHEDULED → IN PROGRESS (claim, sets transaction UID)
    - IN PROGRESS → COMPLETED (requires matching transaction UID)
    - IN PROGRESS → CANCELED (requires matching transaction UID)
    - SCHEDULED → CANCELED (via cancel request, no transaction UID)

    Args:
        current_state: Current procedure step state
        new_state: Requested new state
        current_txn_uid: Current transaction UID (None if not claimed)
        provided_txn_uid: Provided transaction UID in request

    Raises:
        StateTransitionError: If transition is invalid
    """
    # Validate states exist
    if current_state not in VALID_STATES:
        raise StateTransitionError(f"Invalid current state: {current_state}")

    if new_state not in VALID_STATES:
        raise StateTransitionError(f"Invalid new state: {new_state}")

    # SCHEDULED → IN PROGRESS (claim)
    if current_state == "SCHEDULED" and new_state == "IN PROGRESS":
        if not provided_txn_uid:
            raise StateTransitionError(
                "Transaction UID required to claim workitem"
            )
        return  # Valid transition

    # IN PROGRESS → COMPLETED
    if current_state == "IN PROGRESS" and new_state == "COMPLETED":
        if not provided_txn_uid:
            raise StateTransitionError(
                "Transaction UID required for workitem in IN PROGRESS state"
            )
        if provided_txn_uid != current_txn_uid:
            raise StateTransitionError(
                "Transaction UID does not match. "
                "Workitem is owned by another process."
            )
        return  # Valid transition

    # IN PROGRESS → CANCELED
    if current_state == "IN PROGRESS" and new_state == "CANCELED":
        if not provided_txn_uid:
            raise StateTransitionError(
                "Transaction UID required for workitem in IN PROGRESS state"
            )
        if provided_txn_uid != current_txn_uid:
            raise StateTransitionError(
                "Transaction UID does not match. "
                "Workitem is owned by another process."
            )
        return  # Valid transition

    # SCHEDULED → CANCELED (via cancel request only)
    if current_state == "SCHEDULED" and new_state == "CANCELED":
        raise StateTransitionError(
            "Use /cancelrequest endpoint to cancel SCHEDULED workitem"
        )

    # COMPLETED/CANCELED → anything (invalid)
    if current_state in ["COMPLETED", "CANCELED"]:
        raise StateTransitionError(
            f"Cannot transition from {current_state} to {new_state}"
        )

    # Any other transition
    raise StateTransitionError(
        f"Invalid state transition: {current_state} → {new_state}"
    )


def can_update_workitem(
    current_state: str,
    current_txn_uid: str | None,
    provided_txn_uid: str | None
) -> bool:
    """
    Check if workitem can be updated.

    Args:
        current_state: Current procedure step state
        current_txn_uid: Current transaction UID
        provided_txn_uid: Provided transaction UID in request

    Returns:
        True if update is allowed

    Raises:
        StateTransitionError: If update is not allowed
    """
    # Cannot update COMPLETED or CANCELED
    if current_state in ["COMPLETED", "CANCELED"]:
        raise StateTransitionError(
            f"Cannot update workitem in {current_state} state"
        )

    # SCHEDULED - no transaction UID required
    if current_state == "SCHEDULED":
        return True

    # IN PROGRESS - transaction UID required
    if current_state == "IN PROGRESS":
        if not provided_txn_uid:
            raise StateTransitionError(
                "Transaction UID required for workitem in IN PROGRESS state"
            )
        if provided_txn_uid != current_txn_uid:
            raise StateTransitionError(
                "Transaction UID does not match. "
                "Workitem is owned by another process."
            )
        return True

    return False
