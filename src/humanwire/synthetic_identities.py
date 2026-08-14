"""Deterministic fictional identities for isolated HumanWire scenarios."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

IDENTITY_GENERATOR_VERSION: Literal["humanwire.synthetic-identities/v1"] = (
    "humanwire.synthetic-identities/v1"
)

_FICTIONAL_NAMES = (
    "Avery Chen", "Maya Brooks", "Eli Torres", "Sora Kim", "Priya Shah",
    "Noah Williams", "Lina Alvarez", "Jonah Reed", "Amara Okafor",
    "Theo Martin", "Nadia Patel", "Miles Bennett", "Inez Ward",
    "Kai Morgan", "Leila Haddad", "Owen Park", "Zara Flores",
    "Ravi Mehta", "Talia Green", "Marco Silva", "Anika Rao",
    "Drew Lawson", "Nora Jensen", "Samira Cole",
)


class FictionalIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    persona_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=120)
    email: str = Field(pattern=r"^[a-z0-9-]+@example\.test$")


def seeded_identity_map(
    seed: int, persona_ids: Sequence[str]
) -> dict[str, FictionalIdentity]:
    if seed < 0 or seed > 2_147_483_647:
        raise ValueError("identity seed must be between 0 and 2147483647")
    if len(set(persona_ids)) != len(persona_ids):
        raise ValueError("persona IDs must be unique")
    if len(persona_ids) > len(_FICTIONAL_NAMES):
        raise ValueError("fictional identity catalog is too small")
    ranked = sorted(
        _FICTIONAL_NAMES,
        key=lambda name: hashlib.sha256(
            f"{IDENTITY_GENERATOR_VERSION}:{seed}:{name}".encode()
        ).digest(),
    )
    result: dict[str, FictionalIdentity] = {}
    selected = ranked[: len(persona_ids)]
    for persona_id, display_name in zip(persona_ids, selected, strict=True):
        local = re.sub(r"[^a-z0-9]+", "-", display_name.lower()).strip("-")
        result[persona_id] = FictionalIdentity(
            persona_id=persona_id,
            display_name=display_name,
            email=f"{local}@example.test",
        )
    return result
