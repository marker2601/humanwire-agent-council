"""Human authority boundary for evidence-bound council recommendations."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from humanwire.council_models import (
    ChallengeSeverity,
    ClaimClassification,
    CouncilRecommendation,
)
from humanwire.decisionos_models import (
    DecisionOSContext,
    DecisionOSRole,
)
from humanwire.decisionos_store import (
    DecisionOSAuthorizationDenied,
    DecisionOSPermission,
    require_permission,
)

_WORKSPACE_ID = r"^wrk_[0-9A-HJKMNP-TV-Z]{26}$"
_RUN_ID = r"^council_run_[a-z0-9_]{1,64}$"
_SHA256 = r"^[0-9a-f]{64}$"


class CouncilGatewayDenied(RuntimeError):
    def __init__(self) -> None:
        super().__init__("approval_unavailable")


class _GatewayModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CouncilGatewayResult(_GatewayModel):
    accepted: bool
    reason: Literal[
        "accepted",
        "evidence_unconfirmed",
        "blocking_challenge",
        "invalid_recommendation",
    ]
    recommendation_digest: str | None = Field(default=None, pattern=_SHA256)
    requires_human_approval: bool
    authoritative_mutation_count: Literal[0] = 0


class ApprovalChallenge(_GatewayModel):
    challenge_id: str = Field(pattern=r"^approval_[0-9a-f]{24}$")
    organization_id: str
    workspace_id: str = Field(pattern=_WORKSPACE_ID)
    run_id: str = Field(pattern=_RUN_ID)
    approver_role: Literal[DecisionOSRole.APPROVER] = DecisionOSRole.APPROVER
    recommendation_digest: str = Field(pattern=_SHA256)
    expires_at: datetime
    nonce: SecretStr

    @model_validator(mode="after")
    def expiry_is_aware(self) -> ApprovalChallenge:
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("approval expiry must be timezone-aware")
        return self


class CouncilApprovalReceipt(_GatewayModel):
    challenge_id: str = Field(pattern=r"^approval_[0-9a-f]{24}$")
    organization_id: str
    workspace_id: str = Field(pattern=_WORKSPACE_ID)
    run_id: str = Field(pattern=_RUN_ID)
    recommendation_digest: str = Field(pattern=_SHA256)
    approver_uid: str
    approver_role: DecisionOSRole = Field(strict=False)
    approved_at: datetime


def _canonical_now(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise CouncilGatewayDenied()
    return value.astimezone(UTC)


def _confirmed_ids(value: Iterable[str]) -> frozenset[str] | None:
    if type(value) not in {tuple, frozenset}:
        return None
    rows: list[str] = []
    for item in value:
        if type(item) is not str or not item.startswith("evidence_"):
            return None
        rows.append(item)
    if len(rows) != len(set(rows)):
        return None
    return frozenset(rows)


class CouncilGateway:
    """Validate advisory output and issue one-use, identity-bound approvals."""

    def __init__(self, *, nonce_factory: Callable[[], str]) -> None:
        if not callable(nonce_factory):
            raise TypeError("nonce factory is invalid")
        self._nonce_factory = nonce_factory
        self._lock = threading.Lock()
        self._challenges: dict[str, ApprovalChallenge] = {}
        self._used: set[str] = set()

    def evaluate(
        self,
        recommendation: CouncilRecommendation,
        *,
        confirmed_evidence_ids: Iterable[str],
    ) -> CouncilGatewayResult:
        try:
            canonical = CouncilRecommendation.model_validate(recommendation)
        except Exception:  # noqa: BLE001 - malformed model output stays private
            return CouncilGatewayResult(
                accepted=False,
                reason="invalid_recommendation",
                requires_human_approval=False,
            )
        confirmed = _confirmed_ids(confirmed_evidence_ids)
        if confirmed is None:
            return CouncilGatewayResult(
                accepted=False,
                reason="invalid_recommendation",
                requires_human_approval=False,
            )
        for claim in canonical.claims:
            if claim.classification in {
                ClaimClassification.CONFIRMED_FACT,
                ClaimClassification.SOURCE_ASSERTION,
            } and not set(claim.evidence_ids) <= confirmed:
                return CouncilGatewayResult(
                    accepted=False,
                    reason="evidence_unconfirmed",
                    requires_human_approval=False,
                )
        if any(
            item.severity is ChallengeSeverity.BLOCKING
            for item in canonical.challenges
        ):
            return CouncilGatewayResult(
                accepted=False,
                reason="blocking_challenge",
                requires_human_approval=False,
            )
        return CouncilGatewayResult(
            accepted=True,
            reason="accepted",
            recommendation_digest=canonical.semantic_digest,
            requires_human_approval=True,
        )

    def prepare_approval(
        self,
        recommendation: CouncilRecommendation,
        context: DecisionOSContext,
        *,
        workspace_id: str,
        run_id: str,
        confirmed_evidence_ids: Iterable[str],
        now: datetime,
        expires_in: timedelta = timedelta(minutes=15),
    ) -> ApprovalChallenge:
        canonical_context = DecisionOSContext.model_validate(context)
        try:
            require_permission(canonical_context, DecisionOSPermission.APPROVE)
        except DecisionOSAuthorizationDenied:
            raise CouncilGatewayDenied() from None
        evaluated = self.evaluate(
            recommendation,
            confirmed_evidence_ids=confirmed_evidence_ids,
        )
        if not evaluated.accepted or evaluated.recommendation_digest is None:
            raise CouncilGatewayDenied()
        timestamp = _canonical_now(now)
        if type(expires_in) is not timedelta or not timedelta(minutes=1) <= expires_in <= timedelta(
            hours=1
        ):
            raise CouncilGatewayDenied()
        nonce = self._nonce_factory()
        if type(nonce) is not str or not 1 <= len(nonce) <= 256 or not nonce.isascii():
            raise CouncilGatewayDenied()
        binding = (
            f"{canonical_context.organization_id}|{workspace_id}|{run_id}|"
            f"{evaluated.recommendation_digest}|{nonce}"
        )
        challenge = ApprovalChallenge(
            challenge_id=f"approval_{hashlib.sha256(binding.encode('ascii')).hexdigest()[:24]}",
            organization_id=canonical_context.organization_id,
            workspace_id=workspace_id,
            run_id=run_id,
            recommendation_digest=evaluated.recommendation_digest,
            expires_at=timestamp + expires_in,
            nonce=SecretStr(nonce),
        )
        with self._lock:
            if challenge.challenge_id in self._challenges:
                raise CouncilGatewayDenied()
            self._challenges[challenge.challenge_id] = challenge
        return challenge

    def approve(
        self,
        challenge_id: str,
        nonce: str,
        recommendation: CouncilRecommendation,
        context: DecisionOSContext,
        *,
        workspace_id: str,
        run_id: str,
        now: datetime,
    ) -> CouncilApprovalReceipt:
        canonical_context = DecisionOSContext.model_validate(context)
        timestamp = _canonical_now(now)
        try:
            require_permission(canonical_context, DecisionOSPermission.APPROVE)
        except DecisionOSAuthorizationDenied:
            raise CouncilGatewayDenied() from None
        with self._lock:
            challenge = self._challenges.get(challenge_id)
            unavailable = (
                challenge is None
                or challenge_id in self._used
                or type(nonce) is not str
                or challenge.nonce.get_secret_value() != nonce
                or challenge.organization_id != canonical_context.organization_id
                or challenge.workspace_id != workspace_id
                or challenge.run_id != run_id
                or challenge.expires_at < timestamp
                or challenge.recommendation_digest != recommendation.semantic_digest
            )
            if unavailable:
                raise CouncilGatewayDenied()
            self._used.add(challenge_id)
        return CouncilApprovalReceipt(
            challenge_id=challenge.challenge_id,
            organization_id=challenge.organization_id,
            workspace_id=challenge.workspace_id,
            run_id=challenge.run_id,
            recommendation_digest=challenge.recommendation_digest,
            approver_uid=canonical_context.principal.uid,
            approver_role=canonical_context.membership.role,
            approved_at=timestamp,
        )
