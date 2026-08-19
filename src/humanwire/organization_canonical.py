"""Exact canonical boundaries for organization-domain values."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel

_EXACT_SCALAR_TYPES = frozenset({str, int, float, bool, type(None)})


def _has_only_empty_builtin_state(model: BaseModel, attribute: str) -> bool:
    state = object.__getattribute__(model, attribute)
    return state is None or (type(state) is dict and len(state) == 0)


def _same_exact_value(raw: object, canonical: object) -> bool:
    if type(raw) is not type(canonical):
        return False
    if isinstance(raw, BaseModel):
        fields = set(type(raw).model_fields)
        raw_values = object.__getattribute__(raw, "__dict__")
        canonical_values = object.__getattribute__(canonical, "__dict__")
        if (
            type(raw_values) is not dict
            or type(canonical_values) is not dict
            or any(type(key) is not str for key in raw_values)
            or any(type(key) is not str for key in canonical_values)
            or set(raw_values) != fields
            or set(canonical_values) != fields
            or not _has_only_empty_builtin_state(raw, "__pydantic_extra__")
            or not _has_only_empty_builtin_state(canonical, "__pydantic_extra__")
            or not _has_only_empty_builtin_state(raw, "__pydantic_private__")
            or not _has_only_empty_builtin_state(canonical, "__pydantic_private__")
        ):
            return False
        return all(
            _same_exact_value(raw_values[field], canonical_values[field])
            for field in fields
        )
    if isinstance(raw, tuple):
        return len(raw) == len(canonical) and all(
            _same_exact_value(left, right)
            for left, right in zip(raw, canonical, strict=True)
        )
    if isinstance(raw, list):
        return len(raw) == len(canonical) and all(
            _same_exact_value(left, right)
            for left, right in zip(raw, canonical, strict=True)
        )
    if isinstance(raw, dict):
        if len(raw) != len(canonical) or any(type(key) is not str for key in raw):
            return False
        if tuple(raw) != tuple(canonical):
            return False
        return all(_same_exact_value(raw[key], canonical[key]) for key in raw)
    if isinstance(raw, Enum):
        return type(raw.value) in _EXACT_SCALAR_TYPES and (
            type(raw.value) is type(canonical.value) and raw.value == canonical.value
        )
    if isinstance(raw, datetime):
        return raw.isoformat() == canonical.isoformat() and raw.fold == canonical.fold
    return type(raw) in _EXACT_SCALAR_TYPES and raw == canonical


def exact_canonical_model[ModelT: BaseModel](
    value: object,
    model_type: type[ModelT],
) -> ModelT | None:
    """Reconstruct an exact model or return None for any coercion or hidden value."""

    if type(value) is not model_type:
        return None
    try:
        raw_json = value.model_dump_json(warnings="error")
        canonical = model_type.model_validate_json(raw_json, strict=True)
        canonical_json = canonical.model_dump_json(warnings="error")
        if raw_json != canonical_json or not _same_exact_value(value, canonical):
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
        return canonical_left.model_dump_json(warnings="error") == (
            canonical_right.model_dump_json(warnings="error")
        )
    except Exception:  # noqa: BLE001 - serialization failures are fixed-safe
        return False


__all__ = ["exact_canonical_equal", "exact_canonical_model"]
