# backend/bounties/tests/test_effective_availability.py
"""
Regression tests for effective availability logic on Bounty model,
serializers, and API endpoints.

These tests verify that pending treasury proposals (pay_bounty, close_bounty)
are properly reflected in the effective awards remaining and pending proposal
status fields returned by the Bounty model and serializers.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, Optional

import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from ..models import Bounty
from ..serializers import BountyListSerializer
from proposals.models import Proposal

logger = logging.getLogger(__name__)
User = get_user_model()

# Constants for test data consistency
DEFAULT_AWARDS_REMAINING: int = 5
DEFAULT_MAX_AWARDS: int = 10
DEFAULT_PASSWORD: str = "testpass123"
PAY_ACTION: str = "pay_bounty"
CLOSE_ACTION: str = "close_bounty"
PENDING_STATUS: str = "pending"
APPROVED_STATUS: str = "approved"
EXECUTED_STATUS: str = "executed"


class TestEffectiveAvailability(TestCase):
    """
    Regression tests for effective availability logic on Bounty model,
    serializers, and API endpoints.
    """

    @classmethod
    def setUpClass(cls) -> None:
        """Set up any class-level resources (logger configuration)."""
        super().setUpClass()
        logging.basicConfig(level=logging.DEBUG)

    def setUp(self) -> None:
        """Set up test client and authenticated sponsor user."""
        self.client: APIClient = APIClient()
        self.sponsor: User = User.objects.create_user(
            username="sponsor",
            email="sponsor@test.com",
            password=DEFAULT_PASSWORD,
        )
        self.client.force_authenticate(user=self.sponsor)
        logger.debug("Test setup complete for %s", self._testMethodName)

    def tearDown(self) -> None:
        """Clean up after each test."""
        logger.debug("Test teardown for %s", self._testMethodName)

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------
    def _create_bounty(
        self,
        awards_remaining: int = DEFAULT_AWARDS_REMAINING,
        max_awards: int = DEFAULT_MAX_AWARDS,
        is_active: bool = True,
    ) -> Bounty:
        """
        Helper to create a Bounty with default values.

        Args:
            awards_remaining: Number of awards remaining.
            max_awards: Maximum number of awards.
            is_active: Whether the bounty is active.

        Returns:
            Bounty: Created Bounty instance.

        Raises:
            AssertionError: If awards_remaining is negative or max_awards is not positive.
        """
        assert awards_remaining >= 0, "awards_remaining must be non-negative"
        assert max_awards > 0, "max_awards must be positive"
        bounty = Bounty.objects.create(
            title="Test Bounty",
            awards_remaining=awards_remaining,
            max_awards=max_awards,
            sponsor=self.sponsor,
            is_active=is_active,
        )
        logger.debug("Created bounty id=%d with awards_remaining=%d", bounty.id, awards_remaining)
        return bounty

    def _create_proposal(
        self,
        bounty: Bounty,
        action: str = PAY_ACTION,
        status: str = PENDING_STATUS,
        awards: int = 2,
    ) -> Proposal:
        """
        Helper to create a Proposal linked to a bounty.

        Args:
            bounty: The Bounty to link.
            action: Proposal action (pay_bounty or close_bounty).
            status: Proposal status.
            awards: Number of awards consumed (for pay_bounty).

        Returns:
            Proposal: Created Proposal instance.

        Raises:
            AssertionError: If awards is negative and action is pay_bounty.
        """
        if action == PAY_ACTION:
            assert awards >= 0, "awards must be non-negative for pay_bounty"
        proposal = Proposal.objects.create(
            bounty=bounty,
            action=action,
            status=status,
            awards=awards,
        )
        logger.debug(
            "Created proposal id=%d for bounty id=%d action=%s awards=%d status=%s",
            proposal.id, bounty.id, action, awards, status
        )
        return proposal

    # ------------------------------------------------------------------
    # Model method tests
    # ------------------------------------------------------------------
    def test_get_pending_payout_count_no_proposals(self) -> None:
        """Verify get_pending_payout_count returns 0 when no pending proposals exist."""
        bounty = self._create_bounty(awards_remaining=5)
        self.assertEqual(bounty.get_pending_payout_count(), 0)

    def test_get_pending_payout_count_pending_pay(self) -> None:
        """Verify count sums awards from multiple pending pay_bounty proposals."""
        bounty = self._create_bounty(awards_remaining=10)
        self._create_proposal(bounty, action=PAY_ACTION, awards=3)
        self._create_proposal(bounty, action=PAY_ACTION, awards=2)
        self.assertEqual(bounty.get_pending_payout_count(), 5)

    def test_get_pending_payout_count_pending_close(self) -> None:
        """Verify pending close_bounty returns full awards_remaining."""
        bounty = self._create_bounty(awards_remaining=10)
        self._create_proposal(bounty, action=PAY_ACTION, awards=3)
        self._create_proposal(bounty, action=CLOSE_ACTION)
        self.assertEqual(bounty.get_pending_payout_count(), 10)

    def test_get_pending_payout_count_close_with_pay_still(self) -> None:
        """Verify pending close overrides pay proposals to consume all remaining."""
        bounty = self._create_bounty(awards_remaining=7)
        self._create_proposal(bounty, action=PAY_ACTION, awards=2)
        self._create_proposal(bounty, action=CLOSE_ACTION)
        self.assertEqual(bounty.get_pending_payout_count(), 7)

    def test_get_pending_payout_count_ignores_non_pending(self) -> None:
        """Verify only pending proposals affect the count."""
        bounty = self._create_bounty(awards_remaining=10)
        self._create_proposal(bounty, action=PAY_ACTION, status=APPROVED_STATUS, awards=5)
        self._create_proposal(bounty, action=CLOSE_ACTION, status=EXECUTED_STATUS)
        self.assertEqual(bounty.get_pending_payout_count(), 0)

    def test_effective_awards_remaining_basic(self) -> None:
        """Verify effective_awards_remaining equals awards_remaining when no pending proposals."""
        bounty = self._create_bounty(awards_remaining=10)
        self.assertEqual(bounty.effective_awards_remaining(), 10)

    def test_effective_awards_remaining_reduced(self) -> None:
        """Verify effective remaining is reduced by pending pay proposals."""
        bounty = self._create_bounty(awards_remaining=10)
        self._create_proposal(bounty, action=PAY_ACTION, awards=4)
        self.assertEqual(bounty.effective_awards_remaining(), 6)

    def test_effective_awards_remaining_zero_if_fully_consumed(self) -> None:
        """Verify effective remaining is 0 when pending pay consumes all awards."""
        bounty = self._create_bounty(awards_remaining=10)
        self._create_proposal(bounty, action=PAY_ACTION, awards=10)
        self.assertEqual(bounty.effective_awards_remaining(), 0)

    def test_effective_awards_remaining_negative_not_possible(self) -> None:
        """Verify effective remaining is never negative (clamped to 0)."""
        bounty = self._create_bounty(awards_remaining=3)
        self._create_proposal(bounty, action=PAY_ACTION, awards=5)
        self.assertEqual(bounty.effective_awards_remaining(), 0)

    def test_effective_awards_remaining_close_consumes_all(self) -> None:
        """Verify pending close_bounty sets effective remaining to 0."""
        bounty = self._create_bounty(awards_remaining=5)
        self._create_proposal(bounty, action=CLOSE_ACTION)
        self.assertEqual(bounty.effective_awards_remaining(), 0)

    def test_pending_proposal_status_none(self) -> None:
        """Verify pending_proposal_status returns None when no pending proposals exist."""
        bounty = self._create_bounty(awards_remaining=5)
        self.assertIsNone(bounty.pending_proposal_status())

    def test_pending_proposal_status_pending_payout(self) -> None:
        """Verify status returns 'pending_payout' when only pay proposals exist."""
        bounty = self._create_bounty(awards_remaining=5)
        self._create_proposal(bounty, action=PAY_ACTION, awards=2)
        self.assertEqual(bounty.pending_proposal_status(), "pending_payout")

    def test_pending_proposal_status_pending_close(self) -> None:
        """Verify status returns 'pending_close' when close proposal exists."""
        bounty = self._create_bounty(awards_remaining=5)
        self._create_proposal(bounty, action=CLOSE_ACTION)
        self.assertEqual(bounty.pending_proposal_status(), "pending_close")

    def test_pending_proposal_status_close_overrides_payout(self) -> None:
        """Verify close status takes priority over pay when both exist."""
        bounty = self._create_bounty(awards_remaining=5)
        self._create_proposal(bounty, action=PAY_ACTION, awards=2)
        self._create_proposal(bounty, action=CLOSE_ACTION)
        self.assertEqual(bounty.pending_proposal_status(), "pending_close")

    # ------------------------------------------------------------------
    # Serializer tests
    # ------------------------------------------------------------------
    def test_serializer_contains_effective_fields(self) -> None:
        """Verify serializer output includes effective_awards_remaining and pending_proposal_status."""
        bounty = self._create_bounty(awards_remaining=8)
        serializer = BountyListSerializer(bounty)
        self.assertIn("effective_awards_remaining", serializer.data)
        self.assertIn("pending_proposal_status", serializer.data)

    def test_serializer_effective_value_no_proposals(self) -> None:
        """Verify effective_awards_remaining equals awards_remaining when no pending."""
        bounty = self._create_bounty(awards_remaining=8)
        serializer = BountyListSerializer(bounty)
        self.assertEqual(serializer.data["effective_awards_remaining"], 8)

    def test_serializer_effective_value_with_pending_pay(self) -> None:
        """Verify effective_awards_remaining reflects pending pay proposals."""
        bounty = self._create_bounty(awards_remaining=8)
        self._create_proposal(bounty, action=PAY_ACTION, awards=3)
        serializer = BountyListSerializer(bounty)
        self.assertEqual(serializer.data["effective_awards_remaining"], 5)

    def test_serializer_pending_status_none(self) -> None:
        """Verify pending_proposal_status is None when no pending proposals."""
        bounty = self._create_bounty(awards_remaining=5)
        serializer = BountyListSerializer(bounty)
        self.assertIsNone(serializer.data["pending_proposal_status"])

    def test_serializer_pending_status_pending_payout(self) -> None:
        """Verify pending_proposal_status is 'pending_payout' when only pay exists."""
        bounty = self._create_bounty(awards_remaining=5)
        self._create_proposal(bounty, action=PAY_ACTION, awards=2)
        serializer = BountyListSerializer(bounty)
        self.assertEqual(serializer.data["pending_proposal_status"], "pending_payout")

    def test_serializer_pending_status_pending_close(self) -> None:
        """Verify pending_proposal_status is 'pending_close' when close proposal exists."""
        bounty = self._create_bounty(awards_remaining=5)
        self._create_proposal(bounty, action=CLOSE_ACTION)
        serializer = BountyListSerializer(bounty)
        self.assertEqual(serializer.data["pending_proposal_status"], "pending_close")

    def test_serializer_pending_close_overrides_payout(self) -> None:
        """Verify close status takes priority over pay in serializer output."""
        bounty = self._create_bounty(awards_remaining=5)
        self._create_proposal(bounty, action=PAY_ACTION, awards=2)
        self._create_proposal(bounty, action=CLOSE_ACTION)
        serializer = BountyListSerializer(bounty)
        self.assertEqual(serializer.data["pending_proposal_status"], "pending_close")

    def test_serializer_inactive_bounty(self) -> None:
        """Verify effective fields are still computed for inactive bounties (as edge case)."""
        bounty = self._create_bounty(awards_remaining=3, is_active=False)
        self._create_proposal(bounty, action=PAY_ACTION, awards=1)
        serializer = BountyListSerializer(bounty)
        self.assertEqual(serializer.data["effective_awards_remaining"], 2)
        self.assertEqual(serializer.data["pending_proposal_status"], "pending_payout")

    # ------------------------------------------------------------------
    # API endpoint tests (optional, if applicable)
    # ------------------------------------------------------------------
    def test_api_bounty_list_includes_effective_fields(self) -> None:
        """
        Verify the API list endpoint returns effective fields when serialized.
        This tests integration with the view layer.
        """
        bounty = self._create_bounty(awards_remaining=10)
        self._create_proposal(bounty, action=PAY_ACTION, awards=4)
        url = reverse("bounty-list")  # Adjust URL name as needed
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        # Find our bounty in the results
        bounty_data = next((item for item in data if item["id"] == bounty.id), None)
        self.assertIsNotNone(bounty_data, "Bounty not found in list response")
        self.assertIn("effective_awards_remaining", bounty_data)
        self.assertIn("pending_proposal_status", bounty_data)
        self.assertEqual(bounty_data["effective_awards_remaining"], 6)
        self.assertEqual(bounty_data["pending_proposal_status"], "pending_payout")