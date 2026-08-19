"""Exact canonical boundaries for organization-domain values."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel
from pydantic_core import TzInfo

from humanwire.organization_models import (
    AuthorityAssignment,
    AuthorityDecision,
    AuthorityFunction,
    AuthorityRequest,
    CommitImportRequest,
    ImportDraft,
    ImportReceipt,
    ImportReconciliation,
    OrganizationEdge,
    OrganizationEdgeKind,
    OrganizationGraph,
    OrganizationGraphCandidate,
    OrganizationProjection,
    OrganizationProjectionSubject,
    OrganizationSubject,
    OrganizationSubjectKind,
    OrganizationUnit,
    SourceRecord,
    SourceSnapshot,
    SubjectLifecycle,
)

_MAX_PREFLIGHT_DEPTH = 24
_MAX_PREFLIGHT_ITEMS = 4_250_000
_MAX_PREFLIGHT_TEXT = 32 * 1024 * 1024

_TRUSTED_MODEL_TYPES = (
    OrganizationSubject,
    OrganizationUnit,
    OrganizationEdge,
    AuthorityAssignment,
    AuthorityRequest,
    AuthorityDecision,
    SourceRecord,
    SourceSnapshot,
    OrganizationGraph,
    OrganizationGraphCandidate,
    ImportDraft,
    ImportReconciliation,
    CommitImportRequest,
    ImportReceipt,
    OrganizationProjectionSubject,
    OrganizationProjection,
)
_TRUSTED_ENUM_TYPES = (
    OrganizationSubjectKind,
    SubjectLifecycle,
    OrganizationEdgeKind,
    AuthorityFunction,
)


def _is_trusted_identity(candidate: type[object], trusted: tuple[type[object], ...]) -> bool:
    return any(candidate is expected for expected in trusted)


def _is_trusted_model_identity(candidate: type[object]) -> bool:
    if _is_trusted_identity(candidate, _TRUSTED_MODEL_TYPES):
        return True
    try:
        from humanwire.organization_activation import (
            ActivatedOrganizationMembership,
            BulkInvitationReceipt,
            SubjectInvitationReceipt,
        )
    except ImportError:
        return False
    return any(
        candidate is expected
        for expected in (
            ActivatedOrganizationMembership,
            BulkInvitationReceipt,
            SubjectInvitationReceipt,
        )
    )


def _is_trusted_enum_identity(candidate: type[object]) -> bool:
    if _is_trusted_identity(candidate, _TRUSTED_ENUM_TYPES):
        return True
    try:
        from humanwire.decisionos_models import (
            DecisionOSRole,
            MembershipStatus,
        )
        from humanwire.organization_activation import ActivationDeliveryStatus
    except ImportError:
        return False
    return any(
        candidate is expected
        for expected in (ActivationDeliveryStatus, DecisionOSRole, MembershipStatus)
    )


def _declared_fields(model_type: type[BaseModel]) -> tuple[str, ...] | None:
    fields = type.__getattribute__(model_type, "__pydantic_fields__")
    if type(fields) is not dict:
        return None
    names: list[str] = []
    for name in dict.__iter__(fields):
        if type(name) is not str:
            return None
        names.append(name)
    return tuple(names)


def _matches_declared_field(name: str, fields: tuple[str, ...]) -> bool:
    for index in range(tuple.__len__(fields)):
        if str.__eq__(name, tuple.__getitem__(fields, index)) is True:
            return True
    return False


def _has_only_empty_builtin_state(model: BaseModel, attribute: str) -> bool:
    state = object.__getattribute__(model, attribute)
    return state is None or (type(state) is dict and dict.__len__(state) == 0)


def _preflight_exact_value(
    value: object,
    *,
    depth: int,
    budget: list[int],
) -> bool:
    """Inspect hostile values without dispatching any value-provided operation."""

    if depth > _MAX_PREFLIGHT_DEPTH or budget[0] <= 0:
        return False
    budget[0] -= 1
    value_type = type(value)

    if value_type is str:
        budget[1] -= str.__len__(value)
        return budget[1] >= 0
    if value_type is int:
        return int.bit_length(value) <= 256
    if value_type is float or value_type is bool or value_type is type(None):
        return True
    if value_type is datetime:
        tzinfo = object.__getattribute__(value, "tzinfo")
        return tzinfo is None or type(tzinfo) is timezone or type(tzinfo) is TzInfo
    if _is_trusted_enum_identity(value_type):
        enum_value = object.__getattribute__(value, "_value_")
        if type(enum_value) is not str:
            return False
        budget[1] -= str.__len__(enum_value)
        return budget[1] >= 0
    if value_type is tuple:
        size = tuple.__len__(value)
        if size > budget[0]:
            return False
        for index in range(size):
            if not _preflight_exact_value(
                tuple.__getitem__(value, index),
                depth=depth + 1,
                budget=budget,
            ):
                return False
        return True
    if value_type is dict:
        size = dict.__len__(value)
        if size > budget[0]:
            return False
        for key in dict.__iter__(value):
            if type(key) is not str:
                return False
            budget[1] -= str.__len__(key)
            if budget[1] < 0:
                return False
        for key in dict.__iter__(value):
            if not _preflight_exact_value(
                dict.__getitem__(value, key),
                depth=depth + 1,
                budget=budget,
            ):
                return False
        return True
    if not _is_trusted_model_identity(value_type):
        return False

    fields = _declared_fields(value_type)
    values = object.__getattribute__(value, "__dict__")
    if (
        fields is None
        or type(values) is not dict
        or dict.__len__(values) != tuple.__len__(fields)
        or not _has_only_empty_builtin_state(value, "__pydantic_extra__")
        or not _has_only_empty_builtin_state(value, "__pydantic_private__")
    ):
        return False
    for key in dict.__iter__(values):
        if type(key) is not str or not _matches_declared_field(key, fields):
            return False
    for index in range(tuple.__len__(fields)):
        field = tuple.__getitem__(fields, index)
        if not _preflight_exact_value(
            dict.__getitem__(values, field),
            depth=depth + 1,
            budget=budget,
        ):
            return False
    return True


def _passes_exact_preflight(value: object) -> bool:
    budget = [_MAX_PREFLIGHT_ITEMS, _MAX_PREFLIGHT_TEXT]
    return _preflight_exact_value(value, depth=0, budget=budget)


def _same_exact_value(raw: object, canonical: object) -> bool:
    raw_type = type(raw)
    if raw_type is not type(canonical):
        return False
    if _is_trusted_model_identity(raw_type):
        fields = _declared_fields(raw_type)
        if fields is None:
            return False
        raw_values = object.__getattribute__(raw, "__dict__")
        canonical_values = object.__getattribute__(canonical, "__dict__")
        for index in range(tuple.__len__(fields)):
            field = tuple.__getitem__(fields, index)
            if not _same_exact_value(
                dict.__getitem__(raw_values, field),
                dict.__getitem__(canonical_values, field),
            ):
                return False
        return True
    if raw_type is tuple:
        size = tuple.__len__(raw)
        if size != tuple.__len__(canonical):
            return False
        for index in range(size):
            if not _same_exact_value(
                tuple.__getitem__(raw, index),
                tuple.__getitem__(canonical, index),
            ):
                return False
        return True
    if raw_type is dict:
        if dict.__len__(raw) != dict.__len__(canonical):
            return False
        raw_keys = tuple(dict.__iter__(raw))
        canonical_keys = tuple(dict.__iter__(canonical))
        for index in range(tuple.__len__(raw_keys)):
            raw_key = tuple.__getitem__(raw_keys, index)
            canonical_key = tuple.__getitem__(canonical_keys, index)
            if str.__eq__(raw_key, canonical_key) is not True or not _same_exact_value(
                dict.__getitem__(raw, raw_key),
                dict.__getitem__(canonical, canonical_key),
            ):
                return False
        return True
    if _is_trusted_enum_identity(raw_type):
        return _same_exact_value(
            object.__getattribute__(raw, "_value_"),
            object.__getattribute__(canonical, "_value_"),
        )
    if raw_type is datetime:
        return (
            str.__eq__(datetime.isoformat(raw), datetime.isoformat(canonical)) is True
            and object.__getattribute__(raw, "fold")
            == object.__getattribute__(canonical, "fold")
        )
    if raw_type is str:
        return str.__eq__(raw, canonical) is True
    if raw_type is int:
        return int.__eq__(raw, canonical) is True
    if raw_type is float:
        return float.__eq__(raw, canonical) is True
    if raw_type is bool or raw_type is type(None):
        return raw is canonical
    return False


def exact_canonical_model[ModelT: BaseModel](
    value: object,
    model_type: type[ModelT],
) -> ModelT | None:
    """Reconstruct an exact model or return None for any coercion or hidden value."""

    if (
        not _is_trusted_model_identity(model_type)
        or type(value) is not model_type
    ):
        return None
    try:
        if not _passes_exact_preflight(value):
            return None
        raw_json = BaseModel.model_dump_json(value, warnings="error")
        canonical = BaseModel.model_validate_json.__func__(
            model_type,
            raw_json,
            strict=True,
        )
        if not _passes_exact_preflight(canonical):
            return None
        canonical_json = BaseModel.model_dump_json(canonical, warnings="error")
        if (
            str.__eq__(raw_json, canonical_json) is not True
            or not _same_exact_value(value, canonical)
        ):
            return None
    except Exception:  # noqa: BLE001 - hostile values fail closed without details
        return None
    return canonical


def exact_canonical_equal[ModelT: BaseModel](
    left: object,
    right: object,
    model_type: type[ModelT],
) -> bool:
    """Compare two models by their separately reconstructed canonical JSON."""

    canonical_left = exact_canonical_model(left, model_type)
    canonical_right = exact_canonical_model(right, model_type)
    if canonical_left is None or canonical_right is None:
        return False
    try:
        left_json = BaseModel.model_dump_json(canonical_left, warnings="error")
        right_json = BaseModel.model_dump_json(canonical_right, warnings="error")
        return str.__eq__(left_json, right_json) is True
    except Exception:  # noqa: BLE001 - serialization failures are fixed-safe
        return False


__all__ = ["exact_canonical_equal", "exact_canonical_model"]
