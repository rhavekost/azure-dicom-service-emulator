"""Tests for UPS-RS state machine validator (Phase 5, Task 2)."""

import pytest

from app.services.ups_state_machine import (
    StateTransitionError,
    VALID_STATES,
    validate_state_transition,
    can_update_workitem,
)


def test_valid_states_constant():
    """Test VALID_STATES constant contains expected states."""
    assert VALID_STATES == ["SCHEDULED", "IN PROGRESS", "COMPLETED", "CANCELED"]


# Valid state transitions


def test_transition_scheduled_to_in_progress():
    """Test SCHEDULED → IN PROGRESS (claim with new transaction UID)."""
    validate_state_transition(
        current_state="SCHEDULED",
        new_state="IN PROGRESS",
        current_txn_uid=None,
        provided_txn_uid="1.2.3.4.5"
    )
    # Should not raise


def test_transition_in_progress_to_completed():
    """Test IN PROGRESS → COMPLETED (requires matching transaction UID)."""
    validate_state_transition(
        current_state="IN PROGRESS",
        new_state="COMPLETED",
        current_txn_uid="1.2.3.4.5",
        provided_txn_uid="1.2.3.4.5"
    )
    # Should not raise


def test_transition_in_progress_to_canceled():
    """Test IN PROGRESS → CANCELED (requires matching transaction UID)."""
    validate_state_transition(
        current_state="IN PROGRESS",
        new_state="CANCELED",
        current_txn_uid="1.2.3.4.5",
        provided_txn_uid="1.2.3.4.5"
    )
    # Should not raise


# Invalid state transitions


def test_transition_scheduled_to_in_progress_without_txn_uid():
    """Test SCHEDULED → IN PROGRESS without transaction UID fails."""
    with pytest.raises(StateTransitionError) as exc_info:
        validate_state_transition(
            current_state="SCHEDULED",
            new_state="IN PROGRESS",
            current_txn_uid=None,
            provided_txn_uid=None
        )
    assert "Transaction UID required to claim workitem" in str(exc_info.value)


def test_transition_in_progress_to_completed_without_txn_uid():
    """Test IN PROGRESS → COMPLETED without transaction UID fails."""
    with pytest.raises(StateTransitionError) as exc_info:
        validate_state_transition(
            current_state="IN PROGRESS",
            new_state="COMPLETED",
            current_txn_uid="1.2.3.4.5",
            provided_txn_uid=None
        )
    assert "Transaction UID required" in str(exc_info.value)


def test_transition_in_progress_to_completed_wrong_txn_uid():
    """Test IN PROGRESS → COMPLETED with wrong transaction UID fails."""
    with pytest.raises(StateTransitionError) as exc_info:
        validate_state_transition(
            current_state="IN PROGRESS",
            new_state="COMPLETED",
            current_txn_uid="1.2.3.4.5",
            provided_txn_uid="9.8.7.6.5"  # Different UID
        )
    assert "Transaction UID does not match" in str(exc_info.value)
    assert "owned by another process" in str(exc_info.value)


def test_transition_in_progress_to_canceled_without_txn_uid():
    """Test IN PROGRESS → CANCELED without transaction UID fails."""
    with pytest.raises(StateTransitionError) as exc_info:
        validate_state_transition(
            current_state="IN PROGRESS",
            new_state="CANCELED",
            current_txn_uid="1.2.3.4.5",
            provided_txn_uid=None
        )
    assert "Transaction UID required" in str(exc_info.value)


def test_transition_in_progress_to_canceled_wrong_txn_uid():
    """Test IN PROGRESS → CANCELED with wrong transaction UID fails."""
    with pytest.raises(StateTransitionError) as exc_info:
        validate_state_transition(
            current_state="IN PROGRESS",
            new_state="CANCELED",
            current_txn_uid="1.2.3.4.5",
            provided_txn_uid="9.8.7.6.5"  # Different UID
        )
    assert "Transaction UID does not match" in str(exc_info.value)


def test_transition_scheduled_to_canceled_fails():
    """Test SCHEDULED → CANCELED via state change is invalid."""
    with pytest.raises(StateTransitionError) as exc_info:
        validate_state_transition(
            current_state="SCHEDULED",
            new_state="CANCELED",
            current_txn_uid=None,
            provided_txn_uid=None
        )
    assert "Use /cancelrequest endpoint" in str(exc_info.value)


def test_transition_from_completed_fails():
    """Test transitions from COMPLETED state are invalid."""
    with pytest.raises(StateTransitionError) as exc_info:
        validate_state_transition(
            current_state="COMPLETED",
            new_state="IN PROGRESS",
            current_txn_uid=None,
            provided_txn_uid=None
        )
    assert "Cannot transition from COMPLETED" in str(exc_info.value)


def test_transition_from_canceled_fails():
    """Test transitions from CANCELED state are invalid."""
    with pytest.raises(StateTransitionError) as exc_info:
        validate_state_transition(
            current_state="CANCELED",
            new_state="SCHEDULED",
            current_txn_uid=None,
            provided_txn_uid=None
        )
    assert "Cannot transition from CANCELED" in str(exc_info.value)


def test_transition_scheduled_to_completed_fails():
    """Test SCHEDULED → COMPLETED is invalid (must go through IN PROGRESS)."""
    with pytest.raises(StateTransitionError) as exc_info:
        validate_state_transition(
            current_state="SCHEDULED",
            new_state="COMPLETED",
            current_txn_uid=None,
            provided_txn_uid=None
        )
    assert "Invalid state transition: SCHEDULED → COMPLETED" in str(exc_info.value)


def test_invalid_current_state():
    """Test invalid current state raises error."""
    with pytest.raises(StateTransitionError) as exc_info:
        validate_state_transition(
            current_state="INVALID_STATE",
            new_state="SCHEDULED",
            current_txn_uid=None,
            provided_txn_uid=None
        )
    assert "Invalid current state" in str(exc_info.value)


def test_invalid_new_state():
    """Test invalid new state raises error."""
    with pytest.raises(StateTransitionError) as exc_info:
        validate_state_transition(
            current_state="SCHEDULED",
            new_state="INVALID_STATE",
            current_txn_uid=None,
            provided_txn_uid=None
        )
    assert "Invalid new state" in str(exc_info.value)


# Update permission tests


def test_can_update_scheduled_workitem():
    """Test SCHEDULED workitems can be updated without transaction UID."""
    result = can_update_workitem(
        current_state="SCHEDULED",
        current_txn_uid=None,
        provided_txn_uid=None
    )
    assert result is True


def test_can_update_in_progress_with_matching_txn_uid():
    """Test IN PROGRESS workitems can be updated with matching transaction UID."""
    result = can_update_workitem(
        current_state="IN PROGRESS",
        current_txn_uid="1.2.3.4.5",
        provided_txn_uid="1.2.3.4.5"
    )
    assert result is True


def test_cannot_update_in_progress_without_txn_uid():
    """Test IN PROGRESS workitems cannot be updated without transaction UID."""
    with pytest.raises(StateTransitionError) as exc_info:
        can_update_workitem(
            current_state="IN PROGRESS",
            current_txn_uid="1.2.3.4.5",
            provided_txn_uid=None
        )
    assert "Transaction UID required" in str(exc_info.value)


def test_cannot_update_in_progress_with_wrong_txn_uid():
    """Test IN PROGRESS workitems cannot be updated with wrong transaction UID."""
    with pytest.raises(StateTransitionError) as exc_info:
        can_update_workitem(
            current_state="IN PROGRESS",
            current_txn_uid="1.2.3.4.5",
            provided_txn_uid="9.8.7.6.5"  # Different UID
        )
    assert "Transaction UID does not match" in str(exc_info.value)


def test_cannot_update_completed_workitem():
    """Test COMPLETED workitems cannot be updated."""
    with pytest.raises(StateTransitionError) as exc_info:
        can_update_workitem(
            current_state="COMPLETED",
            current_txn_uid=None,
            provided_txn_uid=None
        )
    assert "Cannot update workitem in COMPLETED state" in str(exc_info.value)


def test_cannot_update_canceled_workitem():
    """Test CANCELED workitems cannot be updated."""
    with pytest.raises(StateTransitionError) as exc_info:
        can_update_workitem(
            current_state="CANCELED",
            current_txn_uid=None,
            provided_txn_uid=None
        )
    assert "Cannot update workitem in CANCELED state" in str(exc_info.value)
