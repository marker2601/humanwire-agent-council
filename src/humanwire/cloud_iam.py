"""Exact least-privilege IAM role contract for the Google Cloud stack."""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

_WEB_ROLES = ("roles/datastore.user", "roles/pubsub.publisher")
_WORKER_ROLES = (
    "roles/aiplatform.user",
    "roles/datastore.user",
    "roles/logging.logWriter",
)
_PUSH_ROLES = ("roles/run.invoker",)


class GoogleIamContract(BaseModel):
    """Frozen role sets used by tests, deployment, and operational documentation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    web_roles: tuple[str, ...] = _WEB_ROLES
    worker_roles: tuple[str, ...] = _WORKER_ROLES
    push_roles: tuple[str, ...] = _PUSH_ROLES

    @model_validator(mode="after")
    def is_exact_and_separated(self) -> Self:
        if (
            self.web_roles != _WEB_ROLES
            or self.worker_roles != _WORKER_ROLES
            or self.push_roles != _PUSH_ROLES
            or "roles/aiplatform.user" in self.web_roles
            or "roles/pubsub.publisher" in self.worker_roles
            or set(self.push_roles) & (set(self.web_roles) | set(self.worker_roles))
        ):
            raise ValueError("cloud IAM contract is invalid")
        return self


def cloud_iam_contract() -> GoogleIamContract:
    """Return the canonical role boundary without project or identity data."""
    return GoogleIamContract()


__all__ = ["GoogleIamContract", "cloud_iam_contract"]
