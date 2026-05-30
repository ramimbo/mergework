# models.py
import logging
from typing import Optional, Tuple

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, DatabaseError
from django.db.models import Sum

from .proposal_utils import get_pending_proposals_for_bounty

logger = logging.getLogger(__name__)


class ProposalType(models.TextChoices):
    """Enumerates the types of treasury proposals."""

    PAY_BOUNTY = "pay_bounty", "Pay Bounty"
    CLOSE_BOUNTY = "close_bounty", "Close Bounty"


class Bounty(models.Model):
    """Represents a bounty issue on MergeWork.

    A bounty corresponds to an issue for which rewards can be claimed.
    This model tracks the total available awards, how many are
    practically remaining after pending proposals, and active status.
    """

    issue_url = models.URLField(
        unique=True,
        help_text="URL of the bounty issue",
    )
    title = models.CharField(
        max_length=255,
        help_text="Human-readable title of the bounty",
    )
    awards_remaining = models.PositiveIntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Total awards remaining (ignoring pending proposals)",
    )
    reward_per_award = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0.01)],
        help_text="Reward amount per individual award",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When the bounty was created",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether the bounty is currently accepting claims",
    )

    # Cache for pending proposal data to avoid multiple DB queries per request
    _pending_data_cache: Optional[Tuple[int, bool]] = None

    class Meta:
        verbose_name_plural = "bounties"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["is_active", "created_at"]),
            models.Index(fields=["issue_url"]),
        ]

    def __str__(self) -> str:
        return (
            f"Bounty #{self.pk or '(unsaved)'}: "
            f"{self.title} ({self.awards_remaining} awards remaining)"
        )

    def __repr__(self) -> str:
        return (
            f"<Bounty pk={self.pk!r} title={self.title!r} "
            f"awards_remaining={self.awards_remaining}>"
        )

    def _get_pending_proposal_data(self) -> Tuple[int, bool]:
        """Retrieve and cache pending payout and close proposal data.

        Returns:
            Tuple (pending_payout_count, has_pending_close).
            Cached on the instance for the lifetime of the request.
        """
        if self._pending_data_cache is not None:
            return self._pending_data_cache

        try:
            data = get_pending_proposals_for_bounty(self)
        except (ValueError, TypeError, DatabaseError, Bounty.DoesNotExist) as exc:
            logger.exception(
                "Failed to retrieve pending proposal data for Bounty %d: %s",
                self.pk,
                exc,
            )
            data = (0, False)
        self._pending_data_cache = data
        return data

    @property
    def effective_awards_remaining(self) -> int:
        """Compute the number of awards practically available for new claims.

        Takes into account pending treasury proposals that would consume awards.
        If a close_bounty proposal is pending, the entire remaining pool is
        considered consumed. Otherwise, only the amounts in pending pay_bounty
        proposals are subtracted.

        Returns:
            Non-negative integer representing how many awards can still be claimed.
        """
        pending_payouts, has_pending_close = self._get_pending_proposal_data()
        if has_pending_close:
            return 0
        return max(0, self.awards_remaining - pending_payouts)

    @property
    def is_effectively_closed(self) -> bool:
        """Return True if a close_bounty proposal is pending.

        When a close proposal is pending, maintainers have indicated no further
        claims should be accepted, even if the bounty is still technically active.
        """
        _, has_pending_close = self._get_pending_proposal_data()
        return has_pending_close

    def pending_proposals_count(self) -> int:
        """Return the total number of pending proposals for this bounty.

        Uses a single database count query.

        Returns:
            int: number of pending proposals; 0 on error.
        """
        try:
            return self.proposals.filter(is_pending=True).count()
        except DatabaseError as exc:
            logger.exception(
                "Failed to count pending proposals for Bounty %d: %s",
                self.pk,
                exc,
            )
            return 0

    def pending_payout_count(self) -> int:
        """Return the sum of pending payout proposal counts.

        Uses a database aggregation that returns 0 if no pending payouts.

        Returns:
            int: total payout count from pending PAY_BOUNTY proposals.
        """
        try:
            result = self.proposals.filter(
                is_pending=True,
                proposal_type=ProposalType.PAY_BOUNTY,
            ).aggregate(total=models.Sum("payout_count"))
            return result.get("total") or 0
        except DatabaseError as exc:
            logger.exception(
                "Failed to compute pending payout count for Bounty %d: %s",
                self.pk,
                exc,
            )
            return 0

    def to_api_dict(self) -> dict:
        """Serialize bounty to a dictionary for API responses.

        Includes effective availability fields.

        Returns:
            dict with keys: id, issue_url, title, awards_remaining,
            reward_per_award, created_at, is_active,
            effective_awards_remaining, is_effectively_closed,
            pending_proposals_count, pending_payout_count.
        """
        return {
            "id": self.pk,
            "issue_url": self.issue_url,
            "title": self.title,
            "awards_remaining": self.awards_remaining,
            "reward_per_award": float(self.reward_per_award),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "is_active": self.is_active,
            "effective_awards_remaining": self.effective_awards_remaining,
            "is_effectively_closed": self.is_effectively_closed,
            "pending_proposals_count": self.pending_proposals_count(),
            "pending_payout_count": self.pending_payout_count(),
        }


class TreasuryProposal(models.Model):
    """Represents a treasury proposal that can affect a bounty's availability.

    Only pending proposals influence the effective remaining awards.
    """

    bounty = models.ForeignKey(
        Bounty,
        on_delete=models.CASCADE,
        related_name="proposals",
        help_text="The bounty this proposal relates to",
    )
    proposal_type = models.CharField(
        max_length=20,
        choices=ProposalType.choices,
        help_text="Type of treasury action",
    )
    payout_count = models.PositiveIntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Number of awards to pay out (only meaningful for pay_bounty)",
    )
    is_pending = models.BooleanField(
        default=True,
        help_text="Whether the proposal is still pending execution",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When the proposal was created",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["bounty", "is_pending", "proposal_type"]),
            models.Index(fields=["bounty", "proposal_type"]),
        ]

    def __str__(self) -> str:
        return (
            f"TreasuryProposal #{self.pk}: {self.get_proposal_type_display()}"
            f" on Bounty #{self.bounty_id}"
        )

    def __repr__(self) -> str:
        return (
            f"<TreasuryProposal pk={self.pk!r} "
            f"type={self.proposal_type!r} "
            f"bounty_id={self.bounty_id!r}>"
        )

    def clean(self) -> None:
        """Validate proposal fields.

        Raises:
            ValidationError: if PAY_BOUNTY has zero payout_count.
        """
        if self.proposal_type == ProposalType.PAY_BOUNTY and self.payout_count == 0:
            raise ValidationError(
                {"payout_count": "Pay bounty proposals must have a positive payout count."}
            )
        # Ensure proposal_type is within choices (already enforced by Django model field)