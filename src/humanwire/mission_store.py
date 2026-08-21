"""Tenant-bound persistence for HumanWire mission snapshots."""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timezone
from typing import Protocol

from pydantic import BaseModel
from pydantic_core import TzInfo

from humanwire.decisionos_models import (
    DecisionOSContext,
    DecisionOSPrincipal,
    DecisionOSRole,
    DecisionWorkspace,
    MembershipStatus,
    OrganizationMembership,
    WorkspacePlaybook,
)
from humanwire.decisionos_store import DecisionOSPermission, require_permission
from humanwire.mission_models import (
    MissionActorType,
    MissionBlockedReason,
    MissionEvent,
    MissionMode,
    MissionParticipant,
    MissionRequest,
    MissionSnapshot,
    MissionState,
    mission_id_is_valid,
)

_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class MissionUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("mission_unavailable")


class MissionIdentifierFactory(Protocol):
    def mission_id(self) -> str: ...


class SecureMissionIdentifiers:
    def mission_id(self) -> str:
        value = secrets.randbits(128)
        encoded = "".join(
            _ULID_ALPHABET[(value >> (5 * index)) & 31]
            for index in range(25, -1, -1)
        )
        return f"mis_{encoded}"


class MissionRepository(Protocol):
    def create(
        self,
        context: DecisionOSContext,
        workspace: DecisionWorkspace,
        request: MissionRequest,
    ) -> MissionSnapshot: ...

    def load(
        self,
        context: DecisionOSContext,
        mission_id: str,
    ) -> MissionSnapshot: ...

    def load_bound(
        self,
        context: DecisionOSContext,
        workspace: DecisionWorkspace,
        mission_id: str,
    ) -> MissionSnapshot: ...

    def update(
        self,
        context: DecisionOSContext,
        snapshot: MissionSnapshot,
        *,
        expected_version: int,
    ) -> MissionSnapshot: ...


_MODEL_TYPES = (
    DecisionOSPrincipal,
    OrganizationMembership,
    DecisionOSContext,
    DecisionWorkspace,
    MissionRequest,
    MissionParticipant,
    MissionEvent,
    MissionSnapshot,
)
_ENUM_TYPES = (
    DecisionOSRole,
    MembershipStatus,
    WorkspacePlaybook,
    MissionMode,
    MissionActorType,
    MissionState,
    MissionBlockedReason,
)


def _exact_value(value: object, *, depth: int = 0) -> bool:
    if depth > 16:
        return False
    value_type = type(value)
    if value_type in {str, int, float, bool, type(None)}:
        return True
    if value_type is datetime:
        tzinfo = object.__getattribute__(value, "tzinfo")
        return tzinfo is None or type(tzinfo) in {timezone, TzInfo}
    if any(value_type is enum_type for enum_type in _ENUM_TYPES):
        return type(object.__getattribute__(value, "_value_")) is str
    if value_type is tuple:
        return tuple.__len__(value) <= 10_000 and all(
            _exact_value(tuple.__getitem__(value, index), depth=depth + 1)
            for index in range(tuple.__len__(value))
        )
    if value_type is dict:
        return dict.__len__(value) <= 100 and all(
            type(key) is str
            and _exact_value(dict.__getitem__(value, key), depth=depth + 1)
            for key in dict.__iter__(value)
        )
    if not any(value_type is item for item in _MODEL_TYPES):
        return False
    values = object.__getattribute__(value, "__dict__")
    fields = type.__getattribute__(value_type, "__pydantic_fields__")
    extra = object.__getattribute__(value, "__pydantic_extra__")
    private = object.__getattribute__(value, "__pydantic_private__")
    if (
        type(values) is not dict
        or type(fields) is not dict
        or dict.__len__(values) != dict.__len__(fields)
        or extra not in (None, {})
        or private not in (None, {})
    ):
        return False
    for key in dict.__iter__(values):
        if type(key) is not str or key not in fields:
            return False
        if not _exact_value(dict.__getitem__(values, key), depth=depth + 1):
            return False
    return True


def _canonical(value: object, model_type):
    try:
        if type(value) is not model_type or not _exact_value(value):
            raise ValueError
        raw_json = BaseModel.model_dump_json(value, warnings="error")
        canonical = BaseModel.model_validate_json.__func__(
            model_type,
            raw_json,
            strict=True,
        )
        if not _exact_value(canonical):
            raise ValueError
        canonical_json = BaseModel.model_dump_json(canonical, warnings="error")
        if str.__eq__(raw_json, canonical_json) is not True:
            raise ValueError
    except Exception:  # noqa: BLE001 - malformed private state is fixed publicly
        canonical = None
    if canonical is None:
        raise MissionUnavailable() from None
    return canonical


def _clock_value(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
    except Exception:  # noqa: BLE001 - injected clock details stay private
        raise MissionUnavailable() from None
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise MissionUnavailable()
    return value.astimezone(UTC)


class InMemoryMissionRepository:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        identifiers: MissionIdentifierFactory | None = None,
    ) -> None:
        self._clock = clock
        self._identifiers = identifiers or SecureMissionIdentifiers()
        self._records: dict[tuple[str, str], MissionSnapshot] = {}
        self._lock = threading.RLock()

    def __repr__(self) -> str:
        return "InMemoryMissionRepository()"

    def create(
        self,
        context: DecisionOSContext,
        workspace: DecisionWorkspace,
        request: MissionRequest,
    ) -> MissionSnapshot:
        canonical_context = _canonical(context, DecisionOSContext)
        canonical_workspace = _canonical(workspace, DecisionWorkspace)
        canonical_request = _canonical(request, MissionRequest)
        require_permission(canonical_context, DecisionOSPermission.CONTRIBUTE)
        if canonical_workspace.organization_id != canonical_context.organization_id:
            raise MissionUnavailable()
        try:
            mission_id = self._identifiers.mission_id()
        except Exception:  # noqa: BLE001 - identifier details stay private
            raise MissionUnavailable() from None
        if not mission_id_is_valid(mission_id):
            raise MissionUnavailable()
        now = _clock_value(self._clock)
        snapshot = MissionSnapshot(
            schema_version="humanwire.mission/v1",
            mission_id=mission_id,
            version=1,
            organization_id=canonical_context.organization_id,
            workspace_id=canonical_workspace.workspace_id,
            mode=canonical_request.mode,
            state=MissionState.READY,
            objective=canonical_request.objective,
            urgency=canonical_request.urgency,
            include_conflict=canonical_request.include_conflict,
            participants=(),
            events=(
                MissionEvent(
                    ordinal=1,
                    kind="mission.created",
                    stage="request",
                    summary="Mission created.",
                    participant_id=None,
                    created_at=now,
                ),
            ),
            blocked_reason=None,
            created_at=now,
            updated_at=now,
        )
        key = (canonical_context.organization_id, mission_id)
        with self._lock:
            if key in self._records:
                raise MissionUnavailable()
            self._records[key] = snapshot
        return _canonical(snapshot, MissionSnapshot)

    def load(
        self,
        context: DecisionOSContext,
        mission_id: str,
    ) -> MissionSnapshot:
        canonical_context = _canonical(context, DecisionOSContext)
        require_permission(canonical_context, DecisionOSPermission.READ_WORKSPACE)
        if not mission_id_is_valid(mission_id):
            raise MissionUnavailable()
        with self._lock:
            stored = self._records.get((canonical_context.organization_id, mission_id))
        canonical = _canonical(stored, MissionSnapshot)
        if canonical.organization_id != canonical_context.organization_id:
            raise MissionUnavailable()
        return canonical

    def load_bound(
        self,
        context: DecisionOSContext,
        workspace: DecisionWorkspace,
        mission_id: str,
    ) -> MissionSnapshot:
        canonical_context = _canonical(context, DecisionOSContext)
        canonical_workspace = _canonical(workspace, DecisionWorkspace)
        if canonical_workspace.organization_id != canonical_context.organization_id:
            raise MissionUnavailable()
        require_permission(canonical_context, DecisionOSPermission.READ_WORKSPACE)
        if not mission_id_is_valid(mission_id):
            raise MissionUnavailable()
        with self._lock:
            stored = self._records.get(
                (canonical_context.organization_id, mission_id)
            )
        snapshot = _canonical(stored, MissionSnapshot)
        if (
            snapshot.organization_id != canonical_context.organization_id
            or snapshot.workspace_id != canonical_workspace.workspace_id
        ):
            raise MissionUnavailable()
        return snapshot

    def update(
        self,
        context: DecisionOSContext,
        snapshot: MissionSnapshot,
        *,
        expected_version: int,
    ) -> MissionSnapshot:
        canonical_context = _canonical(context, DecisionOSContext)
        canonical_snapshot = _canonical(snapshot, MissionSnapshot)
        require_permission(canonical_context, DecisionOSPermission.CONTRIBUTE)
        if type(expected_version) is not int or not 1 <= expected_version <= 1_000_000:
            raise MissionUnavailable()
        key = (canonical_context.organization_id, canonical_snapshot.mission_id)
        with self._lock:
            current = self._records.get(key)
            canonical_current = _canonical(current, MissionSnapshot)
            immutable = (
                canonical_current.mission_id,
                canonical_current.organization_id,
                canonical_current.workspace_id,
                canonical_current.mode,
                canonical_current.objective,
                canonical_current.urgency,
                canonical_current.include_conflict,
                canonical_current.created_at,
            )
            proposed = (
                canonical_snapshot.mission_id,
                canonical_snapshot.organization_id,
                canonical_snapshot.workspace_id,
                canonical_snapshot.mode,
                canonical_snapshot.objective,
                canonical_snapshot.urgency,
                canonical_snapshot.include_conflict,
                canonical_snapshot.created_at,
            )
            if (
                current is None
                or canonical_current.version != expected_version
                or canonical_snapshot.version != expected_version
                or immutable != proposed
            ):
                raise MissionUnavailable()
            replacement = canonical_snapshot.model_copy(
                update={
                    "version": expected_version + 1,
                    "updated_at": _clock_value(self._clock),
                }
            )
            canonical_replacement = _canonical(replacement, MissionSnapshot)
            self._records[key] = canonical_replacement
        return _canonical(canonical_replacement, MissionSnapshot)


def _snapshot_row(snapshot: MissionSnapshot) -> dict[str, object]:
    canonical = _canonical(snapshot, MissionSnapshot)
    return {
        "schema_version": 1,
        "organization_id": canonical.organization_id,
        "workspace_id": canonical.workspace_id,
        "mission_id": canonical.mission_id,
        "version": canonical.version,
        "snapshot": canonical.model_dump(mode="json"),
    }


def _snapshot_from_row(value: object) -> MissionSnapshot:
    if type(value) is not dict or set(value) != {
        "schema_version",
        "organization_id",
        "workspace_id",
        "mission_id",
        "version",
        "snapshot",
    }:
        raise MissionUnavailable()
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or type(value["organization_id"]) is not str
        or type(value["workspace_id"]) is not str
        or type(value["mission_id"]) is not str
        or type(value["version"]) is not int
        or type(value["snapshot"]) is not dict
    ):
        raise MissionUnavailable()
    try:
        import json

        encoded = json.dumps(
            value["snapshot"],
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        snapshot = MissionSnapshot.model_validate_json(encoded, strict=True)
    except Exception:  # noqa: BLE001 - corrupt provider state is fixed publicly
        snapshot = None
    canonical = _canonical(snapshot, MissionSnapshot)
    if (
        canonical.organization_id != value["organization_id"]
        or canonical.workspace_id != value["workspace_id"]
        or canonical.mission_id != value["mission_id"]
        or canonical.version != value["version"]
    ):
        raise MissionUnavailable()
    return canonical


def _same_immutable_mission(
    current: MissionSnapshot,
    proposed: MissionSnapshot,
) -> bool:
    return (
        current.mission_id,
        current.organization_id,
        current.workspace_id,
        current.mode,
        current.objective,
        current.urgency,
        current.include_conflict,
        current.created_at,
    ) == (
        proposed.mission_id,
        proposed.organization_id,
        proposed.workspace_id,
        proposed.mode,
        proposed.objective,
        proposed.urgency,
        proposed.include_conflict,
        proposed.created_at,
    )


class FirestoreMissionRepository:
    """One Firestore document per tenant-bound mission snapshot."""

    def __init__(
        self,
        client: object,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        identifiers: MissionIdentifierFactory | None = None,
    ) -> None:
        if not callable(getattr(client, "collection", None)) or not callable(
            getattr(client, "transaction", None)
        ):
            raise TypeError("Firestore client is invalid")
        self._client = client
        self._clock = clock
        self._identifiers = identifiers or SecureMissionIdentifiers()

    def __repr__(self) -> str:
        return "FirestoreMissionRepository()"

    def _mission_ref(
        self,
        organization_id: str,
        workspace_id: str,
        mission_id: str,
    ):
        return (
            self._client.collection("decisionos_organizations")
            .document(organization_id)
            .collection("workspaces")
            .document(workspace_id)
            .collection("missions")
            .document(mission_id)
        )

    def create(
        self,
        context: DecisionOSContext,
        workspace: DecisionWorkspace,
        request: MissionRequest,
    ) -> MissionSnapshot:
        canonical_context = _canonical(context, DecisionOSContext)
        canonical_workspace = _canonical(workspace, DecisionWorkspace)
        canonical_request = _canonical(request, MissionRequest)
        require_permission(canonical_context, DecisionOSPermission.CONTRIBUTE)
        if canonical_workspace.organization_id != canonical_context.organization_id:
            raise MissionUnavailable()
        try:
            mission_id = self._identifiers.mission_id()
        except Exception:  # noqa: BLE001 - identifier details stay private
            mission_id = None
        if not mission_id_is_valid(mission_id):
            raise MissionUnavailable()
        now = _clock_value(self._clock)
        snapshot = MissionSnapshot(
            schema_version="humanwire.mission/v1",
            mission_id=mission_id,
            version=1,
            organization_id=canonical_context.organization_id,
            workspace_id=canonical_workspace.workspace_id,
            mode=canonical_request.mode,
            state=MissionState.READY,
            objective=canonical_request.objective,
            urgency=canonical_request.urgency,
            include_conflict=canonical_request.include_conflict,
            participants=(),
            events=(
                MissionEvent(
                    ordinal=1,
                    kind="mission.created",
                    stage="request",
                    summary="Mission created.",
                    participant_id=None,
                    created_at=now,
                ),
            ),
            blocked_reason=None,
            created_at=now,
            updated_at=now,
        )
        reference = self._mission_ref(
            snapshot.organization_id,
            snapshot.workspace_id,
            snapshot.mission_id,
        )
        transaction = self._client.transaction()

        def publish(transaction):
            existing = reference.get(transaction=transaction)
            if existing.exists:
                raise MissionUnavailable()
            transaction.create(reference, _snapshot_row(snapshot))

        failed = False
        try:
            from google.cloud import firestore

            firestore.transactional(publish)(transaction)
        except Exception:  # noqa: BLE001 - provider details stay private
            failed = True
        if failed:
            raise MissionUnavailable()
        return _canonical(snapshot, MissionSnapshot)

    def load(
        self,
        context: DecisionOSContext,
        mission_id: str,
    ) -> MissionSnapshot:
        canonical_context = _canonical(context, DecisionOSContext)
        require_permission(canonical_context, DecisionOSPermission.READ_WORKSPACE)
        if not mission_id_is_valid(mission_id):
            raise MissionUnavailable()
        query_factory = getattr(self._client, "collection_group", None)
        if not callable(query_factory):
            raise MissionUnavailable()
        failed = False
        rows = None
        try:
            rows = tuple(
                query_factory("missions")
                .where("organization_id", "==", canonical_context.organization_id)
                .where("mission_id", "==", mission_id)
                .limit(2)
                .stream()
            )
        except Exception:  # noqa: BLE001 - provider details stay private
            failed = True
        if failed or rows is None or len(rows) != 1:
            raise MissionUnavailable()
        value = rows[0].to_dict()
        snapshot = _snapshot_from_row(value)
        if snapshot.organization_id != canonical_context.organization_id:
            raise MissionUnavailable()
        return snapshot

    def load_bound(
        self,
        context: DecisionOSContext,
        workspace: DecisionWorkspace,
        mission_id: str,
    ) -> MissionSnapshot:
        canonical_context = _canonical(context, DecisionOSContext)
        canonical_workspace = _canonical(workspace, DecisionWorkspace)
        require_permission(canonical_context, DecisionOSPermission.READ_WORKSPACE)
        if (
            canonical_workspace.organization_id != canonical_context.organization_id
            or not mission_id_is_valid(mission_id)
        ):
            raise MissionUnavailable()
        reference = self._mission_ref(
            canonical_context.organization_id,
            canonical_workspace.workspace_id,
            mission_id,
        )
        failed = False
        row = None
        try:
            document = reference.get()
            if document.exists:
                row = document.to_dict()
        except Exception:  # noqa: BLE001 - provider details stay private
            failed = True
        if failed or row is None:
            raise MissionUnavailable()
        snapshot = _snapshot_from_row(row)
        if (
            snapshot.organization_id != canonical_context.organization_id
            or snapshot.workspace_id != canonical_workspace.workspace_id
            or snapshot.mission_id != mission_id
        ):
            raise MissionUnavailable()
        return snapshot

    def update(
        self,
        context: DecisionOSContext,
        snapshot: MissionSnapshot,
        *,
        expected_version: int,
    ) -> MissionSnapshot:
        canonical_context = _canonical(context, DecisionOSContext)
        proposed = _canonical(snapshot, MissionSnapshot)
        require_permission(canonical_context, DecisionOSPermission.CONTRIBUTE)
        if type(expected_version) is not int or proposed.version != expected_version:
            raise MissionUnavailable()
        reference = self._mission_ref(
            canonical_context.organization_id,
            proposed.workspace_id,
            proposed.mission_id,
        )
        transaction = self._client.transaction()
        result: list[MissionSnapshot] = []

        def publish(transaction):
            document = reference.get(transaction=transaction)
            if not document.exists:
                raise MissionUnavailable()
            current = _snapshot_from_row(document.to_dict())
            if (
                current.organization_id != canonical_context.organization_id
                or current.version != expected_version
                or not _same_immutable_mission(current, proposed)
            ):
                raise MissionUnavailable()
            replacement = _canonical(
                proposed.model_copy(
                    update={
                        "version": expected_version + 1,
                        "updated_at": _clock_value(self._clock),
                    }
                ),
                MissionSnapshot,
            )
            transaction.set(reference, _snapshot_row(replacement))
            result.append(replacement)

        failed = False
        try:
            from google.cloud import firestore

            firestore.transactional(publish)(transaction)
        except Exception:  # noqa: BLE001 - provider details stay private
            failed = True
        if failed or len(result) != 1:
            raise MissionUnavailable()
        return _canonical(result[0], MissionSnapshot)


__all__ = [
    "FirestoreMissionRepository",
    "InMemoryMissionRepository",
    "MissionIdentifierFactory",
    "MissionRepository",
    "MissionUnavailable",
    "SecureMissionIdentifiers",
]
