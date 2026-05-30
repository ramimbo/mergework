# improve/bounty/tests/test_effective_availability.py
"""
Regression tests for effective bounty availability visibility.

Verifies that the 'effective_awards_remaining' property correctly reflects
pending treasury proposals that consume awards (pay_bounty) or close the
bounty (close_bounty). All tests assume the property is implemented on the
Bounty model and exposed in the serializer.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Generator
from decimal import Decimal
from typing import List, Optional, TypeVar

import pytest
from django.utils import timezone

from bounty.models import Bounty, BountyStatus, TreasuryProposal
from bounty.serializers import BountySerializer

logger = logging.getLogger(__name__)

T = TypeVar("T")


class BountyAwardsError(ValueError):
    """Raised when an invalid awards value is provided."""


class ProposalCountError(ValueError):
    """Raised when an invalid proposal count is provided."""


@pytest.mark.django_db
class TestEffectiveAvailability:
    """Container for all effective availability regression tests."""

    # ------------------------------------------------------------------
    #  Fixtures
    # ------------------------------------------------------------------

    @pytest.fixture
    def bounty_factory(self) -> Callable[[int], Bounty]:
        """Return a factory to create open bounties with configurable awards.

        The factory creates a Bounty with status OPEN and the specified number
        of awards remaining.

        Args:
            awards: Total awards_remaining for the bounty.

        Returns:
            A callable that creates and returns a Bounty instance.

        Raises:
            BountyAwardsError: If 'awards' is negative or not an int.
        """
        def _create(awards: int = 5) -> Bounty:
            if not isinstance(awards, int):
                raise BountyAwardsError(
                    f"awards must be int, got {type(awards).__name__}"
                )
            if awards < 0:
                raise BountyAwardsError(
                    f"awards must be non-negative, got {awards}"
                )
            bounty = Bounty.objects.create(
                title=f"Test Bounty ({awards} awards)",
                awards_remaining=awards,
                reward_per_award=Decimal("10.0"),
                status=BountyStatus.OPEN,
            )
            logger.debug("Created bounty %s with awards_remaining=%d", bounty.id, awards)
            return bounty

        return _create

    @pytest.fixture
    def pay_proposals_factory(
        self,
    ) -> Callable[[Bounty, int], List[TreasuryProposal]]:
        """Return a factory to create pending PAY_BOUNTY proposals.

        Args:
            bounty: The bounty to associate proposals with.
            count: Number of PAY_BOUNTY proposals to create (default 1).

        Returns:
            A list of created TreasuryProposal instances.

        Raises:
            ProposalCountError: If count is negative.
            TypeError: If bounty is not a Bounty instance.
        """
        def _create(bounty: Bounty, count: int = 1) -> List[TreasuryProposal]:
            if not isinstance(bounty, Bounty):
                raise TypeError("bounty must be a Bounty instance")
            if not isinstance(count, int):
                raise ProposalCountError(
                    f"count must be int, got {type(count).__name__}"
                )
            if count < 0:
                raise ProposalCountError(
                    f"count must be non-negative, got {count}"
                )
            proposals: List[TreasuryProposal] = []
            for _ in range(count):
                proposal = TreasuryProposal.objects.create(
                    bounty=bounty,
                    proposal_type=TreasuryProposal.ProposalType.PAY_BOUNTY,
                    created_at=timezone.now(),
                    executed_at=None,
                )
                proposals.append(proposal)
            logger.debug(
                "Created %d pay proposals for bounty %s", count, bounty.id
            )
            return proposals

        return _create

    @pytest.fixture
    def close_proposal_factory(self) -> Callable[[Bounty], TreasuryProposal]:
        """Return a factory to create a single pending CLOSE_BOUNTY proposal.

        Args:
            bounty: The bounty to associate the proposal with.

        Returns:
            The created TreasuryProposal instance.

        Raises:
            TypeError: If bounty is not a Bounty instance.
        """
        def _create(bounty: Bounty) -> TreasuryProposal:
            if not isinstance(bounty, Bounty):
                raise TypeError("bounty must be a Bounty instance")
            proposal = TreasuryProposal.objects.create(
                bounty=bounty,
                proposal_type=TreasuryProposal.ProposalType.CLOSE_BOUNTY,
                created_at=timezone.now(),
                executed_at=None,
            )
            logger.debug("Created close proposal for bounty %s", bounty.id)
            return proposal

        return _create

    @pytest.fixture
    def executed_proposal_factory(
        self,
    ) -> Callable[[Bounty, str], TreasuryProposal]:
        """Return a factory to create an executed PAY_BOUNTY or CLOSE_BOUNTY proposal.

        Args:
            bounty: The bounty to associate the proposal with.
            proposal_type: Either 'PAY_BOUNTY' or 'CLOSE_BOUNTY'.

        Returns:
            The created executed TreasuryProposal instance.

        Raises:
            ValueError: If proposal_type is invalid.
            TypeError: If bounty is not a Bounty instance.
        """
        def _create(bounty: Bounty, proposal_type: str) -> TreasuryProposal:
            if not isinstance(bounty, Bounty):
                raise TypeError("bounty must be a Bounty instance")
            valid_types = {
                "PAY_BOUNTY": TreasuryProposal.ProposalType.PAY_BOUNTY,
                "CLOSE_BOUNTY": TreasuryProposal.ProposalType.CLOSE_BOUNTY,
            }
            if proposal_type not in valid_types:
                raise ValueError(
                    f"proposal_type must be one of {list(valid_types.keys())}, "
                    f"got '{proposal_type}'"
                )
            proposal = TreasuryProposal.objects.create(
                bounty=bounty,
                proposal_type=valid_types[proposal_type],
                created_at=timezone.now(),
                executed_at=timezone.now(),
            )
            logger.debug(
                "Created executed %s proposal for bounty %s",
                proposal_type,
                bounty.id,
            )
            return proposal

        return _create

    # ------------------------------------------------------------------
    #  Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _assert_effective(
        bounty: Bounty, expected: int, msg: Optional[str] = None
    ) -> None:
        """Assert that effective_awards_remaining equals *expected*.

        Args:
            bounty: The bounty instance to check.
            expected: The expected integer value.
            msg: Optional custom failure message.

        Raises:
            AssertionError: If effective value does not match expected.
        """
        effective = bounty.effective_awards_remaining
        assert effective == expected, (
            msg or f"effective_awards_remaining = {effective}, expected {expected}"
        )

    @staticmethod
    def _assert_pending_close(bounty: Bounty, expected: bool) -> None:
        """Assert that pending_close_exists returns *expected*.

        Args:
            bounty: The bounty instance to check.
            expected: The expected boolean value.

        Raises:
            AssertionError: If actual value does not match expected.
        """
        actual = bounty.pending_close_exists
        assert actual is expected, (
            f"pending_close_exists = {actual}, expected {expected}"
        )

    @staticmethod
    def _assert_pending_payout_count(bounty: Bounty, expected: int) -> None:
        """Assert that pending_payout_count returns *expected*.

        Args:
            bounty: The bounty instance to check.
            expected: The expected count value.

        Raises:
            AssertionError: If actual count does not match expected.
        """
        actual = bounty.pending_payout_count
        assert actual == expected, (
            f"pending_payout_count = {actual}, expected {expected}"
        )

    @staticmethod
    def _assert_serialized_field(
        bounty: Bounty, field_name: str, expected: object
    ) -> None:
        """Assert that a serializer field matches expected value.

        Args:
            bounty: The bounty instance to serialize.
            field_name: Name of the field in the serializer.
            expected: The expected value.

        Raises:
            AssertionError: If serialized field does not match expected.
        """
        serializer = BountySerializer(bounty)
        data = serializer.data
        assert field_name in data, f"Field '{field_name}' missing from serializer"
        actual = data[field_name]
        assert actual == expected, (
            f"Serializer field '{field_name}' = {actual}, expected {expected}"
        )

    # ------------------------------------------------------------------
    #  Test Cases
    # ------------------------------------------------------------------

    def test_no_proposals__effective_equals_awards_remaining(
        self, bounty_factory: Callable[[int], Bounty]
    ) -> None:
        """When no proposals exist, effective equals awards_remaining."""
        bounty = bounty_factory(awards=10)
        self._assert_pending_payout_count(bounty, 0)
        self._assert_pending_close(bounty, False)
        self._assert_effective(bounty, 10)

    def test_open_bounty_identical(
        self, bounty_factory: Callable[[int], Bounty]
    ) -> None:
        """Normal open bounty with no proposals: effective = awards_remaining."""
        bounty = bounty_factory(awards=3)
        self._assert_effective(bounty, 3)

    def test_pending_payout_reduces_effective(
        self,
        bounty_factory: Callable[[int], Bounty],
        pay_proposals_factory: Callable[[Bounty, int], List[TreasuryProposal]],
    ) -> None:
        """Each pending PAY_BOUNTY reduces effective remaining by one."""
        bounty = bounty_factory(awards=5)
        pay_proposals_factory(bounty, count=2)
        self._assert_pending_payout_count(bounty, 2)
        self._assert_effective(bounty, 3)

    def test_pending_payout_reduces_effective_exact_zero(
        self,
        bounty_factory: Callable[[int], Bounty],
        pay_proposals_factory: Callable[[Bounty, int], List[TreasuryProposal]],
    ) -> None:
        """Pending payouts that consume all remaining awards yield zero effective."""
        bounty = bounty_factory(awards=3)
        pay_proposals_factory(bounty, count=3)
        self._assert_pending_payout_count(bounty, 3)
        self._assert_effective(bounty, 0)

    def test_pending_payout_exceeds_awards_effective_negative(
        self,
        bounty_factory: Callable[[int], Bounty],
        pay_proposals_factory: Callable[[Bounty, int], List[TreasuryProposal]],
    ) -> None:
        """Pending payouts more than awards produce negative effective (or zero if capped)."""
        bounty = bounty_factory(awards=1)
        pay_proposals_factory(bounty, count=3)
        # Implementation should cap at 0; if not, test expected behavior.
        effective = bounty.effective_awards_remaining
        assert effective <= 0, (
            f"effective_awards_remaining should be <= 0, got {effective}"
        )

    def test_pending_close_renders_effective_zero(
        self,
        bounty_factory: Callable[[int], Bounty],
        close_proposal_factory: Callable[[Bounty], TreasuryProposal],
    ) -> None:
        """A pending close_bounty proposal makes effective awards zero even if
        awards_remaining > 0."""
        bounty = bounty_factory(awards=5)
        close_proposal_factory(bounty)
        self._assert_pending_close(bounty, True)
        self._assert_effective(bounty, 0)

    def test_pending_close_overrides_pending_payout(
        self,
        bounty_factory: Callable[[int], Bounty],
        pay_proposals_factory: Callable[[Bounty, int], List[TreasuryProposal]],
        close_proposal_factory: Callable[[Bounty], TreasuryProposal],
    ) -> None:
        """If both pending close and pay exist, close dominates: effective zero."""
        bounty = bounty_factory(awards=5)
        pay_proposals_factory(bounty, count=2)
        close_proposal_factory(bounty)
        self._assert_pending_close(bounty, True)
        self._assert_effective(bounty, 0)

    def test_executed_proposals_do_not_affect_effective(
        self,
        bounty_factory: Callable[[int], Bounty],
        executed_proposal_factory: Callable[[Bounty, str], TreasuryProposal],
    ) -> None:
        """Executed proposals should be ignored; effective = awards_remaining - only pending."""
        bounty = bounty_factory(awards=5)
        executed_proposal_factory(bounty, "PAY_BOUNTY")
        executed_proposal_factory(bounty, "CLOSE_BOUNTY")
        self._assert_pending_payout_count(bounty, 0)
        self._assert_pending_close(bounty, False)
        self._assert_effective(bounty, 5)

    def test_serializer_exposes_effective_field(
        self,
        bounty_factory: Callable[[int], Bounty],
        pay_proposals_factory: Callable[[Bounty, int], List[TreasuryProposal]],
    ) -> None:
        """The serializer must include effective_awards_remaining and
        pending_close_exists fields."""
        bounty = bounty_factory(awards=10)
        pay_proposals_factory(bounty, count=3)
        self._assert_serialized_field(bounty, "effective_awards_remaining", 7)
        self._assert_serialized_field(bounty, "pending_close_exists", False)
        self._assert_serialized_field(bounty, "pending_payout_count", 3)

    def test_serializer_zero_when_pending_close(
        self,
        bounty_factory: Callable[[int], Bounty],
        close_proposal_factory: Callable[[Bounty], TreasuryProposal],
    ) -> None:
        """Serializer effective_awards_remaining is zero when close pending."""
        bounty = bounty_factory(awards=5)
        close_proposal_factory(bounty)
        self._assert_serialized_field(bounty, "effective_awards_remaining", 0)
        self._assert_serialized_field(bounty, "pending_close_exists", True)

    def test_bounty_list_uses_effective_for_display(
        self,
        bounty_factory: Callable[[int], Bounty],
        pay_proposals_factory: Callable[[Bounty, int], List[TreasuryProposal]],
    ) -> None:
        """Simulate list endpoint: each bounty's effective field reflects proposals."""
        bounty1 = bounty_factory(awards=5)
        bounty2 = bounty_factory(awards=5)
        pay_proposals_factory(bounty1, count=2)
        pay_proposals_factory(bounty2, count=5)

        serializer = BountySerializer([bounty1, bounty2], many=True)
        data = serializer.data
        assert data[0]["effective_awards_remaining"] == 3
        assert data[1]["effective_awards_remaining"] == 0

    def test_mixed_open_and_closed_bounties_in_list(
        self,
        bounty_factory: Callable[[int], Bounty],
        close_proposal_factory: Callable[[Bounty], TreasuryProposal],
    ) -> None:
        """Bounty list shows zero effective for close-pending bounties, normal for others."""
        bounty_open = bounty_factory(awards=5)
        bounty_closing = bounty_factory(awards=5)
        close_proposal_factory(bounty_closing)

        serializer = BountySerializer([bounty_open, bounty_closing], many=True)
        data = serializer.data
        assert data[0]["effective_awards_remaining"] == 5
        assert data[0]["pending_close_exists"] is False
        assert data[1]["effective_awards_remaining"] == 0
        assert data[1]["pending_close_exists"] is True

    def test_zero_awards_bounty(
        self, bounty_factory: Callable[[int], Bounty]
    ) -> None:
        """Bounty with zero awards has effective zero (no proposals)."""
        bounty = bounty_factory(awards=0)
        self._assert_effective(bounty, 0)
        self._assert_pending_payout_count(bounty, 0)
        self._assert_pending_close(bounty, False)