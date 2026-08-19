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

from humanwire.decisionos_models import (
    DecisionOSContext,
    DecisionOSPrincipal,
    DecisionOSRole,
)
from humanwire.decisionos_store import (
    DecisionOSAuthorizationDenied,
    DecisionOSPermission,
    DecisionOSRepository,
    InvitationUnavailable,
    OrganizationUnavailable,
)
from humanwire.organization_activation import (
    ActivatedOrganizationMembership,
    ActivationService,
    BulkInvitationReceipt,
    BulkInvitationRequest,
)
from humanwire.organization_canonical import (
    exact_canonical_equal,
    exact_canonical_model,
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
    AuthorityAssignment,
    CommitImportRequest,
    ImportDraft,
    ImportReceipt,
    ImportReconciliation,
    OrganizationEdge,
    OrganizationGraph,
    OrganizationProjection,
    OrganizationProjectionSubject,
    OrganizationUnit,
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
    activation_service: ActivationService | None = None


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


class _SubjectInvitationsBody(_RouteBody):
    subject_ids: list[str] = Field(min_length=1, max_length=100)
    role: DecisionOSRole = Field(strict=False)
    expires_in_seconds: int = Field(default=604800, ge=60, le=2592000)


class _SubjectInvitationAcceptanceBody(_RouteBody):
    invitation_token: str = Field(min_length=1, max_length=512)


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


def _canonical_bound_import(
    context: DecisionOSContext,
    organization_id: str,
    import_id: str,
    draft: object,
    reconciliation: object,
    receipt: object | None,
    *,
    supersedes_import_id: str | None = None,
    graph_version: int | None = None,
) -> tuple[ImportDraft, ImportReconciliation, ImportReceipt | None] | None:
    canonical_draft = exact_canonical_model(draft, ImportDraft)
    canonical_reconciliation = exact_canonical_model(
        reconciliation,
        ImportReconciliation,
    )
    canonical_receipt = (
        None if receipt is None else exact_canonical_model(receipt, ImportReceipt)
    )
    if (
        canonical_draft is None
        or canonical_reconciliation is None
        or (receipt is not None and canonical_receipt is None)
        or not organization_import_is_bound(
            context,
            import_id,
            canonical_draft,
            canonical_reconciliation,
            canonical_receipt,
            graph_version=graph_version,
        )
        or not organization_reconciliation_is_safe(canonical_reconciliation)
    ):
        return None
    if (
        context.organization_id != organization_id
        or canonical_draft.organization_id != organization_id
        or canonical_draft.import_id != import_id
        or canonical_draft.source_snapshot.organization_id != organization_id
        or canonical_draft.candidate.organization_id != organization_id
        or canonical_draft.candidate.source_snapshot_id
        != canonical_draft.source_snapshot.snapshot_id
        or canonical_reconciliation.organization_id != organization_id
        or canonical_reconciliation.import_id != canonical_draft.import_id
        or (
            supersedes_import_id is not None
            and canonical_draft.supersedes_import_id != supersedes_import_id
        )
    ):
        return None
    return canonical_draft, canonical_reconciliation, canonical_receipt


def _canonical_bound_receipt(
    context: DecisionOSContext,
    organization_id: str,
    import_id: str,
    receipt: object,
    *,
    graph_version: int | None = None,
) -> ImportReceipt | None:
    canonical_receipt = exact_canonical_model(receipt, ImportReceipt)
    if canonical_receipt is None:
        return None
    if (
        context.organization_id != organization_id
        or canonical_receipt.organization_id != organization_id
        or canonical_receipt.import_id != import_id
        or (
            graph_version is not None
            and canonical_receipt.graph_version > graph_version
        )
    ):
        return None
    return canonical_receipt


def _canonical_bound_projection(
    context: DecisionOSContext,
    organization_id: str,
    graph: object,
    reconciliation: object | None,
    projected: object,
) -> tuple[
    OrganizationGraph,
    ImportReconciliation | None,
    OrganizationProjection,
] | None:
    canonical_graph = exact_canonical_model(graph, OrganizationGraph)
    canonical_projection = exact_canonical_model(projected, OrganizationProjection)
    canonical_reconciliation = (
        None
        if reconciliation is None
        else exact_canonical_model(reconciliation, ImportReconciliation)
    )
    if (
        canonical_graph is None
        or canonical_projection is None
        or (reconciliation is not None and canonical_reconciliation is None)
        or (
            canonical_reconciliation is not None
            and not organization_reconciliation_is_safe(canonical_reconciliation)
        )
    ):
        return None
    projected_subjects = {
        item.subject_id: item for item in canonical_projection.subjects
    }
    graph_subjects = {item.subject_id: item for item in canonical_graph.subjects}
    expected_subjects = {
        subject_id: OrganizationProjectionSubject(
            subject_id=item.subject_id,
            kind=item.kind,
            lifecycle=item.lifecycle,
            display_name=item.display_name,
            unit_id=item.unit_id,
            title=item.title,
        )
        for subject_id, item in graph_subjects.items()
    }
    projected_units = {item.unit_id: item for item in canonical_projection.units}
    graph_units = {item.unit_id: item for item in canonical_graph.units}
    projected_edges = {item.edge_id: item for item in canonical_projection.edges}
    graph_edges = {item.edge_id: item for item in canonical_graph.edges}
    projected_assignments = {
        item.assignment_id: item for item in canonical_projection.authority_assignments
    }
    graph_assignments = {
        item.assignment_id: item for item in canonical_graph.authority_assignments
    }
    bound = (
        context.organization_id == organization_id
        and canonical_graph.organization_id == organization_id
        and canonical_projection.organization_id == organization_id
        and canonical_projection.graph_version == canonical_graph.version
        and canonical_projection.source_kind is None
        and canonical_projection.synchronized_at is not None
        and canonical_projection.synchronized_at.isoformat()
        == canonical_graph.created_at.isoformat()
        and (
            (canonical_projection.reconciliation is None and reconciliation is None)
            or (
                canonical_reconciliation is not None
                and exact_canonical_equal(
                    canonical_projection.reconciliation,
                    canonical_reconciliation,
                    ImportReconciliation,
                )
            )
        )
        and projected_subjects.keys() == expected_subjects.keys()
        and all(
            exact_canonical_equal(
                projected_subjects[subject_id],
                expected,
                OrganizationProjectionSubject,
            )
            for subject_id, expected in expected_subjects.items()
        )
        and projected_units.keys() == graph_units.keys()
        and all(
            exact_canonical_equal(projected_units[key], graph_units[key], OrganizationUnit)
            for key in graph_units
        )
        and projected_edges.keys() == graph_edges.keys()
        and all(
            exact_canonical_equal(projected_edges[key], graph_edges[key], OrganizationEdge)
            for key in graph_edges
        )
        and projected_assignments.keys() == graph_assignments.keys()
        and all(
            exact_canonical_equal(
                projected_assignments[key],
                graph_assignments[key],
                AuthorityAssignment,
            )
            for key in graph_assignments
        )
    )
    if not bound:
        return None
    return canonical_graph, canonical_reconciliation, canonical_projection


def _draft_payload(
    draft: object,
    reconciliation: object,
    receipt: object | None = None,
) -> dict[str, object]:
    canonical_draft = exact_canonical_model(draft, ImportDraft)
    canonical_reconciliation = exact_canonical_model(
        reconciliation,
        ImportReconciliation,
    )
    canonical_receipt = (
        None if receipt is None else exact_canonical_model(receipt, ImportReceipt)
    )
    if (
        canonical_draft is None
        or canonical_reconciliation is None
        or (receipt is not None and canonical_receipt is None)
    ):
        raise ValueError("organization import payload is invalid")
    draft = canonical_draft
    reconciliation = canonical_reconciliation
    receipt = canonical_receipt
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


def _receipt_payload(receipt: object) -> dict[str, object]:
    canonical_receipt = exact_canonical_model(receipt, ImportReceipt)
    if canonical_receipt is None:
        raise ValueError("organization receipt payload is invalid")
    receipt = canonical_receipt
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
            canonical_created = exact_canonical_model(created, ImportDraft)
            if canonical_created is None or canonical_created.organization_id != organization_id:
                return _fixed_error(404, "organization_not_found")
            draft, reconciliation, receipt = dependencies.import_service.load_import(
                context,
                canonical_created.import_id,
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
        bound = _canonical_bound_import(
            context,
            organization_id,
            canonical_created.import_id,
            draft,
            reconciliation,
            receipt,
        )
        if bound is None:
            return _fixed_error(404, "organization_not_found")
        canonical_draft, canonical_reconciliation, canonical_receipt = bound
        if canonical_receipt is not None or not exact_canonical_equal(
            canonical_draft,
            canonical_created,
            ImportDraft,
        ):
            return _fixed_error(404, "organization_not_found")
        return JSONResponse(
            status_code=201,
            content=_draft_payload(canonical_draft, canonical_reconciliation),
        )

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
        bound = _canonical_bound_import(
            context,
            organization_id,
            import_id,
            draft,
            reconciliation,
            receipt,
        )
        if bound is None:
            return _fixed_error(404, "organization_not_found")
        canonical_draft, canonical_reconciliation, canonical_receipt = bound
        return JSONResponse(
            content=_draft_payload(
                canonical_draft,
                canonical_reconciliation,
                canonical_receipt,
            )
        )

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
            canonical_corrected = exact_canonical_model(corrected, ImportDraft)
            if (
                canonical_corrected is None
                or canonical_corrected.organization_id != organization_id
            ):
                return _fixed_error(404, "organization_not_found")
            draft, reconciliation, receipt = dependencies.import_service.load_import(
                context,
                canonical_corrected.import_id,
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
        bound = _canonical_bound_import(
            context,
            organization_id,
            canonical_corrected.import_id,
            draft,
            reconciliation,
            receipt,
            supersedes_import_id=import_id,
        )
        if bound is None:
            return _fixed_error(404, "organization_not_found")
        canonical_draft, canonical_reconciliation, canonical_receipt = bound
        if canonical_receipt is not None or not exact_canonical_equal(
            canonical_draft,
            canonical_corrected,
            ImportDraft,
        ):
            return _fixed_error(404, "organization_not_found")
        return JSONResponse(
            status_code=201,
            content=_draft_payload(canonical_draft, canonical_reconciliation),
        )

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
            canonical_receipt = _canonical_bound_receipt(
                context,
                organization_id,
                import_id,
                receipt,
            )
            if canonical_receipt is None:
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
        bound = _canonical_bound_import(
            context,
            organization_id,
            import_id,
            draft,
            reconciliation,
            stored_receipt,
        )
        if bound is None:
            return _fixed_error(404, "organization_not_found")
        canonical_draft, canonical_reconciliation, canonical_stored_receipt = bound
        if (
            canonical_stored_receipt is None
            or not exact_canonical_equal(
                canonical_stored_receipt,
                canonical_receipt,
                ImportReceipt,
            )
            or canonical_draft.semantic_digest != body.reviewed_digest
            or canonical_receipt.acknowledged_codes
            != tuple(body.acknowledged_codes)
            or canonical_receipt.acknowledged_codes
            != canonical_reconciliation.acknowledged_codes
        ):
            return _fixed_error(404, "organization_not_found")
        return JSONResponse(content=_receipt_payload(canonical_receipt))

    def projection(organization_id: str, request: Request) -> Response:
        context = _context(request, organization_id, dependencies, manage=False)
        if isinstance(context, Response):
            return context
        try:
            raw_graph = dependencies.graph_repository.load_graph(context)
            graph = exact_canonical_model(raw_graph, OrganizationGraph)
            if (
                graph is None
                or context.organization_id != organization_id
                or graph.organization_id != organization_id
            ):
                return _fixed_error(404, "organization_not_found")
            review = dependencies.import_service.review_for_graph(context, graph.version)
            reconciliation = None
            if review is not None:
                raw_reconciliation, raw_receipt = review
                reconciliation = exact_canonical_model(
                    raw_reconciliation,
                    ImportReconciliation,
                )
                receipt = _canonical_bound_receipt(
                    context,
                    organization_id,
                    reconciliation.import_id if reconciliation is not None else "",
                    raw_receipt,
                    graph_version=graph.version,
                )
                if (
                    reconciliation is None
                    or receipt is None
                    or reconciliation.organization_id != organization_id
                    or receipt.acknowledged_codes
                    != reconciliation.acknowledged_codes
                    or not organization_reconciliation_is_safe(reconciliation)
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
        bound_projection = _canonical_bound_projection(
            context,
            organization_id,
            graph,
            reconciliation,
            projected,
        )
        if bound_projection is None:
            return _fixed_error(404, "organization_not_found")
        _canonical_graph, _canonical_reconciliation, canonical_projection = bound_projection
        return JSONResponse(content=canonical_projection.model_dump(mode="json"))

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
    if dependencies.activation_service is not None:

        @router.post(
            "/api/organizations/{organization_id}/subject-invitations",
        )
        async def create_subject_invitations(
            organization_id: str,
            request: Request,
        ) -> Response:
            context = _context(request, organization_id, dependencies, manage=True)
            if isinstance(context, Response):
                return context
            body = await _json_body(request, _SubjectInvitationsBody)
            if not isinstance(body, _SubjectInvitationsBody):
                return _fixed_error(400, "invalid_request")
            try:
                bulk_request = BulkInvitationRequest(
                    subject_ids=tuple(body.subject_ids),
                    role=body.role,
                    expires_in_seconds=body.expires_in_seconds,
                )
                receipt = dependencies.activation_service.create_invitations(
                    context,
                    bulk_request,
                )
            except DecisionOSAuthorizationDenied:
                return _fixed_error(403, "authorization_denied")
            except OrganizationUnavailable:
                return _fixed_error(404, "organization_not_found")
            except InvitationUnavailable:
                return _fixed_error(400, "invitation_unavailable")
            except (TypeError, ValueError, ValidationError):
                return _fixed_error(400, "invalid_request")
            canonical_receipt = exact_canonical_model(receipt, BulkInvitationReceipt)
            if (
                canonical_receipt is None
                or canonical_receipt.organization_id != context.organization_id
                or canonical_receipt.organization_id != organization_id
                or canonical_receipt.requested_subject_ids != bulk_request.subject_ids
                or tuple(
                    item.subject_id for item in canonical_receipt.invitations
                )
                != bulk_request.subject_ids
            ):
                return _fixed_error(400, "invitation_unavailable")
            return JSONResponse(
                status_code=201,
                content=BaseModel.model_dump(canonical_receipt, mode="json"),
            )

        @router.post("/api/subject-invitations/accept")
        async def accept_subject_invitation(request: Request) -> Response:
            principal = dependencies.principal_loader(request)
            if principal is None:
                return _fixed_error(401, "authentication_required")
            body = await _json_body(request, _SubjectInvitationAcceptanceBody)
            if not isinstance(body, _SubjectInvitationAcceptanceBody):
                return _fixed_error(400, "invalid_request")
            try:
                accepted = dependencies.activation_service.accept(
                    principal,
                    body.invitation_token,
                )
            except Exception:  # noqa: BLE001 - every token failure is non-enumerating
                return _fixed_error(400, "invitation_unavailable")
            canonical_accepted = exact_canonical_model(
                accepted,
                ActivatedOrganizationMembership,
            )
            if canonical_accepted is None:
                return _fixed_error(400, "invitation_unavailable")
            return JSONResponse(
                content={
                    "status": "active",
                    "organization_id": canonical_accepted.organization_id,
                    "subject_id": canonical_accepted.subject_id,
                    "role": object.__getattribute__(canonical_accepted.role, "_value_"),
                }
            )
    return router


__all__ = [
    "OrganizationProjectionBuilder",
    "OrganizationRouteDependencies",
    "OrganizationSourceParser",
    "create_organization_router",
]
