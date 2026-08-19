"""Authenticated, tenant-bound organization onboarding API routes."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from humanwire.decisionos_models import DecisionOSContext, DecisionOSPrincipal
from humanwire.decisionos_store import (
    DecisionOSAuthorizationDenied,
    DecisionOSPermission,
    DecisionOSRepository,
    OrganizationUnavailable,
)
from humanwire.organization_import import (
    ImportCorrectionKind,
    ImportCorrectionRequest,
    OrganizationImportReviewRequired,
    OrganizationImportService,
    OrganizationImportStale,
    OrganizationImportUnavailable,
)
from humanwire.organization_models import (
    CommitImportRequest,
    ImportDraft,
    ImportReceipt,
    ImportReconciliation,
    OrganizationGraph,
    OrganizationProjection,
    SourceSnapshot,
)
from humanwire.organization_projection import OrganizationProjectionUnavailable
from humanwire.organization_sources import (
    OrganizationSourceRejected,
    ParseOrganizationSourceRequest,
)
from humanwire.organization_store import ImportUnavailable, OrganizationGraphRepository

_BOUNDARY = re.compile(rb"^[A-Za-z0-9'()+_,./:=?-]{1,70}$")
_CONTENT_DISPOSITION = re.compile(
    r'^form-data; name="source"; filename="([^"\\]{1,255})"$'
)


class OrganizationSourceParser(Protocol):
    def __call__(self, request: ParseOrganizationSourceRequest) -> SourceSnapshot: ...


class OrganizationProjectionBuilder(Protocol):
    def __call__(
        self,
        graph: OrganizationGraph,
        reconciliation: ImportReconciliation | None,
    ) -> OrganizationProjection: ...


@dataclass(frozen=True, slots=True)
class OrganizationRouteDependencies:
    decisionos_repository: DecisionOSRepository
    source_parser: OrganizationSourceParser
    import_service: OrganizationImportService
    graph_repository: OrganizationGraphRepository
    projection_builder: OrganizationProjectionBuilder
    principal_loader: Callable[[Request], DecisionOSPrincipal | None]
    body_loader: Callable[
        [Request, type[BaseModel]],
        Awaitable[BaseModel | None],
    ]


class _RouteBody(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _CorrectionBody(_RouteBody):
    reviewed_digest: str
    kind: ImportCorrectionKind = Field(strict=False)
    source_record_ids: list[str] = Field(min_length=1)
    replacement_fields: list[list[str]] = Field(min_length=1)


class _CommitBody(_RouteBody):
    reviewed_digest: str
    acknowledged_codes: list[str] = Field(default_factory=list)


def _fixed_error(status_code: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": code})


def _raw_headers(request: Request, name: bytes) -> tuple[bytes, ...]:
    lowered = name.lower()
    return tuple(
        value
        for key, value in request.scope.get("headers", ())
        if key.lower() == lowered
    )


def _ascii_header(values: tuple[bytes, ...]) -> str | None:
    if len(values) != 1:
        return None
    try:
        return values[0].decode("ascii")
    except UnicodeDecodeError:
        return None


def _context(
    request: Request,
    organization_id: str,
    dependencies: OrganizationRouteDependencies,
    *,
    manage: bool,
) -> DecisionOSContext | Response:
    principal = dependencies.principal_loader(request)
    if principal is None:
        return _fixed_error(401, "authentication_required")
    try:
        context = dependencies.decisionos_repository.load_context(principal, organization_id)
        if manage:
            context = dependencies.decisionos_repository.authorize_context(
                context,
                DecisionOSPermission.MANAGE_MEMBERS,
            )
        return context
    except OrganizationUnavailable:
        return _fixed_error(404, "organization_not_found")
    except DecisionOSAuthorizationDenied:
        return _fixed_error(403, "authorization_denied")


def _multipart_boundary(request: Request) -> bytes | None:
    content_type = _ascii_header(_raw_headers(request, b"content-type"))
    prefix = "multipart/form-data; boundary="
    if content_type is None or not content_type.startswith(prefix):
        return None
    value = content_type[len(prefix) :]
    try:
        boundary = value.encode("ascii")
    except UnicodeEncodeError:
        return None
    return boundary if _BOUNDARY.fullmatch(boundary) is not None else None


async def _source_upload(
    request: Request,
) -> tuple[str, str, bytes] | None:
    length_text = _ascii_header(_raw_headers(request, b"content-length"))
    boundary = _multipart_boundary(request)
    if length_text is None or not length_text.isdecimal() or boundary is None:
        return None
    raw = await request.body()
    if len(raw) != int(length_text):
        return None
    opening = b"--" + boundary + b"\r\n"
    closing = b"\r\n--" + boundary + b"--\r\n"
    if not raw.startswith(opening) or not raw.endswith(closing):
        return None
    part = raw[len(opening) : -len(closing)]
    if b"\r\n--" + boundary in part:
        return None
    header_bytes, separator, content = part.partition(b"\r\n\r\n")
    if not separator or not content:
        return None
    headers: dict[str, str] = {}
    try:
        for raw_header in header_bytes.split(b"\r\n"):
            name_bytes, colon, value_bytes = raw_header.partition(b":")
            if not colon:
                return None
            name = name_bytes.decode("ascii").strip().casefold()
            value = value_bytes.decode("utf-8").strip()
            if name in headers:
                return None
            headers[name] = value
    except UnicodeError:
        return None
    if set(headers) != {"content-disposition", "content-type"}:
        return None
    matched = _CONTENT_DISPOSITION.fullmatch(headers["content-disposition"])
    content_type = headers["content-type"]
    if matched is None or not content_type or ";" in content_type:
        return None
    return matched.group(1), content_type, content


def _draft_payload(
    draft: ImportDraft,
    reconciliation: ImportReconciliation,
) -> dict[str, object]:
    return {
        "status": "draft",
        "import_id": draft.import_id,
        "supersedes_import_id": draft.supersedes_import_id,
        "organization_id": draft.organization_id,
        "source_snapshot_id": draft.source_snapshot.snapshot_id,
        "source_kind": draft.source_snapshot.source_kind,
        "captured_at": draft.source_snapshot.captured_at.isoformat(),
        "source_record_ids": [item.record_id for item in draft.source_snapshot.records],
        "base_graph_version": draft.base_graph_version,
        "reviewed_digest": draft.semantic_digest,
        "created_at": draft.created_at.isoformat(),
        "source_count": reconciliation.source_count,
        "normalized_count": reconciliation.normalized_count,
        "rejected_count": reconciliation.rejected_count,
        "blocking_codes": list(reconciliation.blocking_codes),
        "acknowledged_codes": list(reconciliation.acknowledged_codes),
        "committable": reconciliation.committable,
        "reconciliation": reconciliation.model_dump(mode="json"),
    }


def _receipt_payload(receipt: ImportReceipt) -> dict[str, object]:
    return {
        "status": "committed",
        "receipt_id": receipt.receipt_id,
        "import_id": receipt.import_id,
        "organization_id": receipt.organization_id,
        "source_snapshot_id": receipt.source_snapshot_id,
        "source_snapshot_digest": receipt.source_snapshot_digest,
        "graph_version": receipt.graph_version,
        "committed_subject_count": receipt.committed_subject_count,
        "acknowledged_codes": list(receipt.acknowledged_codes),
        "committed_at": receipt.committed_at.isoformat(),
    }


def _import_error(error: Exception) -> JSONResponse:
    if isinstance(error, OrganizationImportStale):
        return _fixed_error(409, "import_stale")
    if isinstance(error, OrganizationImportReviewRequired):
        return _fixed_error(409, "import_review_required")
    if isinstance(error, ImportUnavailable):
        return _fixed_error(404, "import_not_found")
    if isinstance(error, DecisionOSAuthorizationDenied):
        return _fixed_error(403, "authorization_denied")
    if isinstance(error, OrganizationUnavailable):
        return _fixed_error(404, "organization_not_found")
    if isinstance(error, OrganizationImportUnavailable):
        return _fixed_error(400, "import_unavailable")
    return _fixed_error(400, "invalid_request")


def create_organization_router(
    dependencies: OrganizationRouteDependencies,
) -> APIRouter:
    """Create exactly the staged import, graph, and authority routes."""

    router = APIRouter()

    @router.post("/api/organizations/{organization_id}/imports")
    async def upload_import(organization_id: str, request: Request) -> Response:
        context = _context(request, organization_id, dependencies, manage=True)
        if isinstance(context, Response):
            return context
        upload = await _source_upload(request)
        if upload is None:
            return _fixed_error(400, "invalid_request")
        filename, content_type, content = upload
        try:
            snapshot = dependencies.source_parser(
                ParseOrganizationSourceRequest(
                    content=content,
                    filename=filename,
                    content_type=content_type,
                    organization_id=organization_id,
                )
            )
            draft = dependencies.import_service.create_draft(context, snapshot)
            reconciliation = dependencies.import_service.reconcile(context, draft.import_id)
        except OrganizationSourceRejected as error:
            code = str(error)
            status = 413 if code == "source_too_large" else 415 if code == "source_unsupported" else 400
            return _fixed_error(status, code)
        except (
            DecisionOSAuthorizationDenied,
            ImportUnavailable,
            OrganizationImportUnavailable,
            OrganizationUnavailable,
            TypeError,
            ValueError,
            ValidationError,
        ) as error:
            return _import_error(error)
        return JSONResponse(status_code=201, content=_draft_payload(draft, reconciliation))

    @router.api_route(
        "/api/organizations/{organization_id}/imports/{import_id}",
        methods=["GET", "HEAD"],
    )
    def load_import(organization_id: str, import_id: str, request: Request) -> Response:
        context = _context(request, organization_id, dependencies, manage=True)
        if isinstance(context, Response):
            return context
        try:
            draft = dependencies.graph_repository.load_import_draft(context, import_id)
            reconciliation = dependencies.import_service.reconcile(context, import_id)
        except (
            DecisionOSAuthorizationDenied,
            ImportUnavailable,
            OrganizationImportUnavailable,
            OrganizationUnavailable,
        ) as error:
            return _import_error(error)
        return JSONResponse(content=_draft_payload(draft, reconciliation))

    @router.post(
        "/api/organizations/{organization_id}/imports/{import_id}/corrections"
    )
    async def correct_import(
        organization_id: str,
        import_id: str,
        request: Request,
    ) -> Response:
        context = _context(request, organization_id, dependencies, manage=True)
        if isinstance(context, Response):
            return context
        body = await dependencies.body_loader(request, _CorrectionBody)
        if not isinstance(body, _CorrectionBody):
            return _fixed_error(400, "invalid_request")
        try:
            correction = ImportCorrectionRequest(
                import_id=import_id,
                reviewed_digest=body.reviewed_digest,
                kind=body.kind,
                source_record_ids=tuple(body.source_record_ids),
                replacement_fields=tuple(tuple(item) for item in body.replacement_fields),
            )
            draft = dependencies.import_service.apply_correction(context, correction)
            reconciliation = dependencies.import_service.reconcile(context, draft.import_id)
        except (
            DecisionOSAuthorizationDenied,
            ImportUnavailable,
            OrganizationImportUnavailable,
            OrganizationUnavailable,
            TypeError,
            ValueError,
            ValidationError,
        ) as error:
            return _import_error(error)
        return JSONResponse(status_code=201, content=_draft_payload(draft, reconciliation))

    @router.post("/api/organizations/{organization_id}/imports/{import_id}/commit")
    async def commit_import(
        organization_id: str,
        import_id: str,
        request: Request,
    ) -> Response:
        context = _context(request, organization_id, dependencies, manage=True)
        if isinstance(context, Response):
            return context
        body = await dependencies.body_loader(request, _CommitBody)
        if not isinstance(body, _CommitBody):
            return _fixed_error(400, "invalid_request")
        try:
            receipt = dependencies.import_service.commit(
                context,
                CommitImportRequest(
                    import_id=import_id,
                    reviewed_digest=body.reviewed_digest,
                    acknowledged_codes=tuple(body.acknowledged_codes),
                ),
            )
        except (
            DecisionOSAuthorizationDenied,
            ImportUnavailable,
            OrganizationImportUnavailable,
            OrganizationUnavailable,
            TypeError,
            ValueError,
            ValidationError,
        ) as error:
            return _import_error(error)
        return JSONResponse(content=_receipt_payload(receipt))

    def projection(organization_id: str, request: Request) -> Response:
        context = _context(request, organization_id, dependencies, manage=False)
        if isinstance(context, Response):
            return context
        try:
            graph = dependencies.graph_repository.load_graph(context)
            projected = dependencies.projection_builder(graph, None)
        except OrganizationUnavailable:
            return _fixed_error(404, "organization_not_found")
        except DecisionOSAuthorizationDenied:
            return _fixed_error(403, "authorization_denied")
        except OrganizationProjectionUnavailable:
            return _fixed_error(500, "request_failed")
        return JSONResponse(content=projected.model_dump(mode="json"))

    router.add_api_route(
        "/api/organizations/{organization_id}/organization-graph",
        projection,
        methods=["GET", "HEAD"],
    )
    router.add_api_route(
        "/api/organizations/{organization_id}/authority-map",
        projection,
        methods=["GET", "HEAD"],
    )
    return router


__all__ = [
    "OrganizationProjectionBuilder",
    "OrganizationRouteDependencies",
    "OrganizationSourceParser",
    "create_organization_router",
]
