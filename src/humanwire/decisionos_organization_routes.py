"""Authenticated, tenant-bound organization onboarding API routes."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
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
    organization_import_is_bound,
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
from humanwire.organization_projection import (
    OrganizationProjectionUnavailable,
    organization_reconciliation_is_safe,
)
from humanwire.organization_sources import (
    OrganizationSourceRejected,
    ParseOrganizationSourceRequest,
)
from humanwire.organization_store import ImportUnavailable, OrganizationGraphRepository

_BOUNDARY = re.compile(rb"^[A-Za-z0-9'()+_,./:=?-]{1,70}$")
_CONTENT_DISPOSITION = re.compile(
    r'^form-data; name="source"; filename="([^"\\]{1,255})"$'
)
_MAX_JSON_BODY_BYTES = 8192
_MAX_MULTIPART_BODY_BYTES = 10 * 1024 * 1024 + 4096


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
    raw = await _bounded_body(request, _MAX_MULTIPART_BODY_BYTES)
    if raw is None:
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


async def _bounded_body(request: Request, maximum_bytes: int) -> bytes | None:
    length_text = _ascii_header(_raw_headers(request, b"content-length"))
    if (
        length_text is None
        or not length_text.isdecimal()
        or len(length_text) > len(str(maximum_bytes))
    ):
        return None
    declared = int(length_text)
    if not 1 <= declared <= maximum_bytes:
        return None
    raw = bytearray()
    received = 0
    try:
        async for chunk in request.stream():
            received += len(chunk)
            if received > declared or received > maximum_bytes:
                return None
            if chunk:
                raw.extend(chunk)
    except Exception:  # noqa: BLE001 - transport failures are fixed and content-free
        return None
    if received != declared:
        return None
    return bytes(raw)


def _rejecting_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


async def _json_body(
    request: Request,
    model_type: type[_RouteBody],
) -> _RouteBody | None:
    raw = await _bounded_body(request, _MAX_JSON_BODY_BYTES)
    if raw is None:
        return None
    try:
        decoded = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_rejecting_object,
            parse_constant=_reject_constant,
        )
        if not isinstance(decoded, dict):
            return None
        return model_type.model_validate(decoded)
    except (RecursionError, UnicodeError, ValueError, TypeError, ValidationError):
        return None


def _strict_roundtrip(value: object, model_type: type[BaseModel]) -> bool:
    if type(value) is not model_type:
        return False
    try:
        return _has_exact_model_shape(value) and (
            model_type.model_validate_json(value.model_dump_json()) == value
        )
    except Exception:  # noqa: BLE001 - hostile dependency output fails closed
        return False


def _has_exact_model_shape(value: object) -> bool:
    if isinstance(value, BaseModel):
        if (
            set(value.__dict__) != set(type(value).model_fields)
            or getattr(value, "__pydantic_extra__", None) not in (None, {})
        ):
            return False
        return all(_has_exact_model_shape(item) for item in value.__dict__.values())
    if isinstance(value, (list, tuple)):
        return all(_has_exact_model_shape(item) for item in value)
    if isinstance(value, dict):
        return all(
            _has_exact_model_shape(key) and _has_exact_model_shape(item)
            for key, item in value.items()
        )
    return True


def _draft_is_bound(
    context: DecisionOSContext,
    organization_id: str,
    draft: object,
    reconciliation: object,
    *,
    import_id: str | None = None,
    supersedes_import_id: str | None = None,
) -> bool:
    if (
        type(draft) is not ImportDraft
        or type(reconciliation) is not ImportReconciliation
        or not organization_import_is_bound(
            context,
            draft.import_id,
            draft,
            reconciliation,
            None,
        )
        or not organization_reconciliation_is_safe(reconciliation)
    ):
        return False
    expected_import_id = draft.import_id if import_id is None else import_id
    return (
        context.organization_id == organization_id
        and draft.organization_id == organization_id
        and draft.import_id == expected_import_id
        and draft.source_snapshot.organization_id == organization_id
        and draft.candidate.organization_id == organization_id
        and draft.candidate.source_snapshot_id == draft.source_snapshot.snapshot_id
        and reconciliation.organization_id == organization_id
        and reconciliation.import_id == draft.import_id
        and (
            supersedes_import_id is None
            or draft.supersedes_import_id == supersedes_import_id
        )
    )


def _receipt_is_bound(
    context: DecisionOSContext,
    organization_id: str,
    import_id: str,
    receipt: object,
    *,
    draft: ImportDraft | None = None,
    reconciliation: ImportReconciliation | None = None,
    graph_version: int | None = None,
) -> bool:
    if not _strict_roundtrip(receipt, ImportReceipt):
        return False
    if (
        context.organization_id != organization_id
        or receipt.organization_id != organization_id
        or receipt.import_id != import_id
        or (graph_version is not None and receipt.graph_version > graph_version)
    ):
        return False
    if draft is None or reconciliation is None:
        return True
    return organization_import_is_bound(
        context,
        import_id,
        draft,
        reconciliation,
        receipt,
        graph_version=graph_version,
    ) and (
        draft.organization_id == organization_id
        and draft.import_id == import_id
        and receipt.source_snapshot_id == draft.source_snapshot.snapshot_id
        and receipt.source_snapshot_digest == draft.source_snapshot.semantic_digest
        and receipt.graph_version == draft.base_graph_version + 1
        and receipt.committed_subject_count == len(draft.candidate.subjects)
        and receipt.acknowledged_codes == reconciliation.acknowledged_codes
    )


def _projection_is_bound(
    context: DecisionOSContext,
    organization_id: str,
    graph: OrganizationGraph,
    reconciliation: ImportReconciliation | None,
    projected: object,
) -> bool:
    if (
        not _strict_roundtrip(graph, OrganizationGraph)
        or not _strict_roundtrip(projected, OrganizationProjection)
        or (
            reconciliation is not None
            and not organization_reconciliation_is_safe(reconciliation)
        )
    ):
        return False
    projected_subjects = {item.subject_id: item for item in projected.subjects}
    graph_subjects = {item.subject_id: item for item in graph.subjects}
    expected_subjects = {
        subject_id: {
            "subject_id": item.subject_id,
            "kind": item.kind,
            "lifecycle": item.lifecycle,
            "display_name": item.display_name,
            "unit_id": item.unit_id,
            "title": item.title,
        }
        for subject_id, item in graph_subjects.items()
    }
    projected_units = {item.unit_id: item for item in projected.units}
    graph_units = {item.unit_id: item for item in graph.units}
    projected_edges = {item.edge_id: item for item in projected.edges}
    graph_edges = {item.edge_id: item for item in graph.edges}
    projected_assignments = {
        item.assignment_id: item for item in projected.authority_assignments
    }
    graph_assignments = {
        item.assignment_id: item for item in graph.authority_assignments
    }
    return (
        context.organization_id == organization_id
        and graph.organization_id == organization_id
        and projected.organization_id == organization_id
        and projected.graph_version == graph.version
        and projected.source_kind is None
        and projected.synchronized_at == graph.created_at
        and projected.reconciliation == reconciliation
        and {
            subject_id: item.model_dump(mode="python")
            for subject_id, item in projected_subjects.items()
        }
        == expected_subjects
        and projected_units == graph_units
        and projected_edges == graph_edges
        and projected_assignments == graph_assignments
    )


def _draft_payload(
    draft: ImportDraft,
    reconciliation: ImportReconciliation,
    receipt: ImportReceipt | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": "committed" if receipt is not None else "draft",
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
        "reconciliation": reconciliation.model_dump(mode="json"),
    }
    if receipt is None:
        payload["committable"] = reconciliation.committable
    else:
        payload["receipt"] = _receipt_payload(receipt)
    return payload


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
            if (
                type(snapshot) is not SourceSnapshot
                or snapshot.organization_id != organization_id
                or context.organization_id != organization_id
            ):
                return _fixed_error(404, "organization_not_found")
            created = dependencies.import_service.create_draft(context, snapshot)
            if (
                type(created) is not ImportDraft
                or created.organization_id != organization_id
            ):
                return _fixed_error(404, "organization_not_found")
            draft, reconciliation, receipt = dependencies.import_service.load_import(
                context,
                created.import_id,
            )
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
        if (
            receipt is not None
            or draft != created
            or not _draft_is_bound(context, organization_id, draft, reconciliation)
        ):
            return _fixed_error(404, "organization_not_found")
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
            draft, reconciliation, receipt = dependencies.import_service.load_import(
                context,
                import_id,
            )
        except (
            DecisionOSAuthorizationDenied,
            ImportUnavailable,
            OrganizationImportUnavailable,
            OrganizationUnavailable,
        ) as error:
            return _import_error(error)
        if not _draft_is_bound(
            context,
            organization_id,
            draft,
            reconciliation,
            import_id=import_id,
        ) or (
            receipt is not None
            and not _receipt_is_bound(
                context,
                organization_id,
                import_id,
                receipt,
                draft=draft,
                reconciliation=reconciliation,
            )
        ):
            return _fixed_error(404, "organization_not_found")
        return JSONResponse(content=_draft_payload(draft, reconciliation, receipt))

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
        body = await _json_body(request, _CorrectionBody)
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
            corrected = dependencies.import_service.apply_correction(context, correction)
            if (
                type(corrected) is not ImportDraft
                or corrected.organization_id != organization_id
            ):
                return _fixed_error(404, "organization_not_found")
            draft, reconciliation, receipt = dependencies.import_service.load_import(
                context,
                corrected.import_id,
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
        if not _draft_is_bound(
            context,
            organization_id,
            draft,
            reconciliation,
            supersedes_import_id=import_id,
        ) or receipt is not None or draft != corrected:
            return _fixed_error(404, "organization_not_found")
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
        body = await _json_body(request, _CommitBody)
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
            if not _receipt_is_bound(
                context,
                organization_id,
                import_id,
                receipt,
            ):
                return _fixed_error(404, "organization_not_found")
            draft, reconciliation, stored_receipt = dependencies.import_service.load_import(
                context,
                import_id,
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
        if (
            not _draft_is_bound(
                context,
                organization_id,
                draft,
                reconciliation,
                import_id=import_id,
            )
            or stored_receipt != receipt
            or not _receipt_is_bound(
                context,
                organization_id,
                import_id,
                receipt,
                draft=draft,
                reconciliation=reconciliation,
            )
            or draft.semantic_digest != body.reviewed_digest
            or receipt.acknowledged_codes != tuple(body.acknowledged_codes)
        ):
            return _fixed_error(404, "organization_not_found")
        return JSONResponse(content=_receipt_payload(receipt))

    def projection(organization_id: str, request: Request) -> Response:
        context = _context(request, organization_id, dependencies, manage=False)
        if isinstance(context, Response):
            return context
        try:
            graph = dependencies.graph_repository.load_graph(context)
            if (
                type(graph) is not OrganizationGraph
                or context.organization_id != organization_id
                or graph.organization_id != organization_id
            ):
                return _fixed_error(404, "organization_not_found")
            review = dependencies.import_service.review_for_graph(context, graph.version)
            reconciliation = None
            if review is not None:
                reconciliation, receipt = review
                if (
                    type(reconciliation) is not ImportReconciliation
                    or reconciliation.organization_id != organization_id
                    or not _receipt_is_bound(
                        context,
                        organization_id,
                        reconciliation.import_id,
                        receipt,
                        graph_version=graph.version,
                    )
                    or receipt.acknowledged_codes
                    != reconciliation.acknowledged_codes
                ):
                    return _fixed_error(404, "organization_not_found")
            elif graph.version != 0:
                return _fixed_error(404, "organization_not_found")
            projected = dependencies.projection_builder(graph, reconciliation)
        except OrganizationUnavailable:
            return _fixed_error(404, "organization_not_found")
        except DecisionOSAuthorizationDenied:
            return _fixed_error(403, "authorization_denied")
        except OrganizationProjectionUnavailable:
            return _fixed_error(500, "request_failed")
        except (ImportUnavailable, OrganizationImportUnavailable):
            return _fixed_error(500, "request_failed")
        if not _projection_is_bound(
            context,
            organization_id,
            graph,
            reconciliation,
            projected,
        ):
            return _fixed_error(404, "organization_not_found")
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
