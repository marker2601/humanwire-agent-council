"""Confined, deterministic parsing for untrusted organization source uploads."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO, StringIO
from pathlib import PurePosixPath
from typing import Any
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

import openpyxl
import pypdf
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

from humanwire.organization_models import SourceRecord, SourceSnapshot

_MAX_INPUT_BYTES = 10 * 1024 * 1024
_MAX_RECORDS = 5_000
_MAX_CELL_CHARS = 120
_MAX_PDF_PAGES = 64
_MAX_ARCHIVE_ENTRIES = 1_000
_MAX_ARCHIVE_UNCOMPRESSED_BYTES = 20 * 1024 * 1024
_MAX_COMPRESSION_RATIO = 100
_MAX_PDF_OBJECTS = 20_000
_ORGANIZATION_ID = re.compile(r"^org_[0-9A-HJKMNP-TV-Z]{26}$")
_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_FORMULA_PREFIXES = ("=", "+", "-", "@")
_CREDENTIAL = re.compile(
    r"(?i)(?:\b(?:api[_-]?key|password|passwd|secret|access[_-]?token|auth[_-]?token)"
    r"\s*[:=]\s*\S+|https?://[^\s/:@]+:[^\s/@]+@)"
)
_PRIVATE_PATH = re.compile(
    r"(?i)(?:[a-z]:\\|\\\\[^\s\\]+\\|(?:^|\s)/(?:home|users|private|root|etc|var|tmp)/)"
)
_COMMAND = re.compile(
    r"(?i)^\s*(?:powershell(?:\.exe)?|cmd(?:\.exe)?|bash|zsh|sh|rm|del|erase|curl|wget|"
    r"invoke-expression|invoke-webrequest)\b"
)
_SAFE_RELATIONSHIP_TYPES = frozenset(
    {
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument",
        "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties",
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet",
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles",
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme",
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings",
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/calcChain",
    }
)
_FORBIDDEN_PDF_KEYS = frozenset(
    {
        "/A",
        "/AA",
        "/EmbeddedFiles",
        "/EF",
        "/Filespec",
        "/ImportData",
        "/JavaScript",
        "/JS",
        "/Launch",
        "/OpenAction",
        "/SubmitForm",
    }
)
_MIME_BY_EXTENSION = {
    ".csv": "text/csv",
    ".json": "application/json",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pdf": "application/pdf",
}
_ERROR_CODES = frozenset(
    {"source_invalid", "source_unsafe", "source_unsupported", "source_too_large"}
)


class OrganizationSourceRejected(RuntimeError):
    """A fixed-safe source rejection that never includes uploaded or provider data."""

    def __init__(self, code: str = "source_invalid") -> None:
        super().__init__(code if code in _ERROR_CODES else "source_invalid")


@dataclass(frozen=True, slots=True)
class OrganizationSourceLimits:
    """Caller-selectable limits that may only tighten the service hard bounds."""

    max_input_bytes: int = _MAX_INPUT_BYTES
    max_records: int = _MAX_RECORDS
    max_cell_chars: int = _MAX_CELL_CHARS
    max_pdf_pages: int = _MAX_PDF_PAGES

    def __post_init__(self) -> None:
        limits = (
            (self.max_input_bytes, _MAX_INPUT_BYTES),
            (self.max_records, _MAX_RECORDS),
            (self.max_cell_chars, _MAX_CELL_CHARS),
            (self.max_pdf_pages, _MAX_PDF_PAGES),
        )
        if any(type(value) is not int or value < 1 for value, _ in limits):
            raise ValueError("source limits must be positive integers")
        if any(value > maximum for value, maximum in limits):
            raise ValueError("source limit exceeds hard maximum")


@dataclass(frozen=True, slots=True)
class ParseOrganizationSourceRequest:
    """One tenant-bound upload presented to the confined source parser."""

    content: bytes
    filename: str
    content_type: str
    organization_id: str
    limits: OrganizationSourceLimits = OrganizationSourceLimits()


class _UnsafeJson(ValueError):
    pass


def _reject(code: str) -> OrganizationSourceRejected:
    return OrganizationSourceRejected(code)


def _extension_for(request: ParseOrganizationSourceRequest) -> str:
    if (
        type(request.filename) is not str
        or not request.filename
        or len(request.filename) > 255
        or "/" in request.filename
        or "\\" in request.filename
        or any(unicodedata.category(character).startswith("C") for character in request.filename)
    ):
        raise _reject("source_unsafe")
    dot = request.filename.rfind(".")
    if dot <= 0:
        raise _reject("source_unsupported")
    return request.filename[dot:].lower()


def _validate_request(request: ParseOrganizationSourceRequest) -> str:
    if type(request) is not ParseOrganizationSourceRequest:
        raise _reject("source_invalid")
    if type(request.content) is not bytes or not request.content:
        raise _reject("source_invalid")
    if type(request.limits) is not OrganizationSourceLimits:
        raise _reject("source_invalid")
    if type(request.organization_id) is not str or not _ORGANIZATION_ID.fullmatch(
        request.organization_id
    ):
        raise _reject("source_invalid")
    if type(request.content_type) is not str:
        raise _reject("source_unsupported")
    if len(request.content) > request.limits.max_input_bytes:
        raise _reject("source_too_large")
    extension = _extension_for(request)
    if _MIME_BY_EXTENSION.get(extension) != request.content_type:
        raise _reject("source_unsupported")
    return extension


def _safe_text(value: str, limits: OrganizationSourceLimits) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise _reject("source_unsafe")
    normalized = unicodedata.normalize("NFC", value)
    if len(normalized) > limits.max_cell_chars:
        raise _reject("source_too_large")
    for character in normalized:
        category = unicodedata.category(character)
        if category.startswith("C") or (category.startswith("Z") and character != " "):
            raise _reject("source_unsafe")
    if normalized.startswith(_FORMULA_PREFIXES):
        raise _reject("source_unsafe")
    if (
        _CREDENTIAL.search(normalized)
        or _PRIVATE_PATH.search(normalized)
        or _COMMAND.search(normalized)
    ):
        raise _reject("source_unsafe")
    return normalized


def _canonical_field_name(value: str, limits: OrganizationSourceLimits) -> str:
    safe = _safe_text(value, limits)
    normalized = re.sub(r"[^a-z0-9]+", "_", safe.casefold()).strip("_")
    if not _FIELD_NAME.fullmatch(normalized):
        raise _reject("source_unsafe")
    return normalized


def _records_from_rows(
    rows: list[list[str]],
    limits: OrganizationSourceLimits,
) -> tuple[SourceRecord, ...]:
    if not rows:
        raise _reject("source_unsafe")
    headers = tuple(_canonical_field_name(value, limits) for value in rows[0])
    if not headers or len(set(headers)) != len(headers) or "source_identity" not in headers:
        raise _reject("source_unsafe")
    identity_index = headers.index("source_identity")
    records: list[SourceRecord] = []
    identities: set[str] = set()
    for row in rows[1:]:
        if len(row) != len(headers):
            raise _reject("source_unsafe")
        if len(records) >= limits.max_records:
            raise _reject("source_too_large")
        values = tuple(_safe_text(value, limits) for value in row)
        source_identity = values[identity_index]
        if source_identity in identities:
            raise _reject("source_unsafe")
        identities.add(source_identity)
        fields = tuple(
            sorted(
                (header, value)
                for header, value in zip(headers, values, strict=True)
                if header != "source_identity"
            )
        )
        if not fields:
            raise _reject("source_unsafe")
        ordinal = len(records) + 1
        records.append(
            SourceRecord(
                record_id=f"rec_{ordinal:026d}",
                source_ordinal=ordinal,
                source_identity=source_identity,
                fields=fields,
            )
        )
    return tuple(records)


def _parse_csv(content: bytes, limits: OrganizationSourceLimits) -> tuple[SourceRecord, ...]:
    failed = False
    text = ""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        failed = True
    if failed:
        raise _reject("source_unsafe") from None

    rows: list[list[str]] = []
    failed = False
    try:
        reader = csv.reader(StringIO(text, newline=""), strict=True)
        rows = [row for row in reader if row]
    except (csv.Error, UnicodeError):
        failed = True
    if failed:
        raise _reject("source_unsafe") from None
    return _records_from_rows(rows, limits)


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _UnsafeJson()
        value[key] = item
    return value


def _invalid_constant(_value: str) -> None:
    raise _UnsafeJson()


def _parse_json(content: bytes, limits: OrganizationSourceLimits) -> tuple[SourceRecord, ...]:
    failed = False
    payload: Any = None
    try:
        payload = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_json_object,
            parse_constant=_invalid_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _UnsafeJson):
        failed = True
    if failed:
        raise _reject("source_unsafe") from None
    if type(payload) is not list or not payload:
        raise _reject("source_unsafe")
    if len(payload) > limits.max_records:
        raise _reject("source_too_large")
    if any(type(item) is not dict or not item for item in payload):
        raise _reject("source_unsafe")

    all_headers: set[str] = set()
    normalized_items: list[dict[str, str]] = []
    for item in payload:
        normalized: dict[str, str] = {}
        for key, value in item.items():
            if type(key) is not str or type(value) is not str:
                raise _reject("source_unsafe")
            field_name = _canonical_field_name(key, limits)
            if field_name in normalized:
                raise _reject("source_unsafe")
            normalized[field_name] = value
        all_headers.update(normalized)
        normalized_items.append(normalized)
    headers = sorted(all_headers)
    if any(set(item) != all_headers for item in normalized_items):
        raise _reject("source_unsafe")
    rows = [headers]
    rows.extend([[item[header] for header in headers] for item in normalized_items])
    return _records_from_rows(rows, limits)


def _archive_member_is_unsafe(name: str) -> bool:
    if not name or "\\" in name or "\x00" in name or name.startswith("/"):
        return True
    path = PurePosixPath(name)
    return path.is_absolute() or ".." in path.parts or bool(re.match(r"^[a-zA-Z]:", name))


def _inspect_relationships(content: bytes) -> bool:
    root = ElementTree.fromstring(content)
    for relationship in root:
        relationship_type = relationship.attrib.get("Type", "")
        target = relationship.attrib.get("Target", "")
        if relationship.attrib.get("TargetMode") is not None:
            return False
        if relationship_type not in _SAFE_RELATIONSHIP_TYPES:
            return False
        if not target or "\\" in target or ".." in PurePosixPath(target).parts:
            return False
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
            return False
    return True


def _inspect_xlsx_archive(content: bytes) -> bool:
    try:
        with ZipFile(BytesIO(content)) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > _MAX_ARCHIVE_ENTRIES:
                return False
            names: set[str] = set()
            total_size = 0
            for info in infos:
                lowered = info.filename.casefold()
                if info.filename in names or _archive_member_is_unsafe(info.filename):
                    return False
                names.add(info.filename)
                total_size += info.file_size
                if total_size > _MAX_ARCHIVE_UNCOMPRESSED_BYTES or info.flag_bits & 0x1:
                    return False
                if (
                    info.file_size > 128 * 1024
                    and info.file_size > max(1, info.compress_size) * _MAX_COMPRESSION_RATIO
                ):
                    return False
                if (
                    lowered.endswith("vbaproject.bin")
                    or "/externallinks/" in lowered
                    or "/embeddings/" in lowered
                    or "/activex/" in lowered
                ):
                    return False
                if lowered.endswith(".rels") and not _inspect_relationships(archive.read(info)):
                    return False
            content_types = archive.read("[Content_Types].xml").lower()
            if any(
                marker in content_types
                for marker in (b"vba", b"macroenabled", b"activex", b"oleobject")
            ):
                return False
            return "xl/workbook.xml" in names
    except (BadZipFile, KeyError, OSError, ElementTree.ParseError):
        return False


def _xlsx_rows(content: bytes, limits: OrganizationSourceLimits) -> list[list[str]]:
    workbook = None
    rows: list[list[str]] = []
    failed = False
    rejected: OrganizationSourceRejected | None = None
    try:
        workbook = openpyxl.load_workbook(
            BytesIO(content),
            read_only=True,
            data_only=False,
            keep_links=False,
        )
        for worksheet in workbook.worksheets:
            sheet_rows: list[list[str]] = []
            for cells in worksheet.iter_rows():
                values = list(cells)
                while values and values[-1].value is None:
                    values.pop()
                if not values:
                    continue
                if any(cell.data_type == "f" for cell in values):
                    raise _reject("source_unsafe")
                if any(cell.value is None or type(cell.value) is not str for cell in values):
                    raise _reject("source_unsafe")
                sheet_rows.append([cell.value for cell in values])
                if len(rows) + len(sheet_rows) > limits.max_records + 1:
                    raise _reject("source_too_large")
            if sheet_rows:
                if not rows:
                    rows.extend(sheet_rows)
                else:
                    if sheet_rows[0] != rows[0]:
                        raise _reject("source_unsafe")
                    rows.extend(sheet_rows[1:])
    except OrganizationSourceRejected as error:
        rejected = error
    except Exception:  # noqa: BLE001 - provider errors are sealed by the public boundary
        failed = True
    finally:
        if workbook is not None:
            workbook.close()
    if rejected is not None:
        raise rejected from None
    if failed:
        raise _reject("source_invalid") from None
    return rows


def _parse_xlsx(content: bytes, limits: OrganizationSourceLimits) -> tuple[SourceRecord, ...]:
    if not _inspect_xlsx_archive(content):
        raise _reject("source_unsafe")
    return _records_from_rows(_xlsx_rows(content, limits), limits)


def _pdf_has_forbidden_objects(root: Any) -> bool:
    pending: list[Any] = [root]
    seen_indirect: set[tuple[int, int]] = set()
    seen_direct: set[int] = set()
    visited = 0
    while pending:
        current = pending.pop()
        if isinstance(current, IndirectObject):
            key = (current.idnum, current.generation)
            if key in seen_indirect:
                continue
            seen_indirect.add(key)
            current = current.get_object()
        if isinstance(current, (DictionaryObject, ArrayObject)):
            direct_id = id(current)
            if direct_id in seen_direct:
                continue
            seen_direct.add(direct_id)
            visited += 1
            if visited > _MAX_PDF_OBJECTS:
                return True
        if isinstance(current, Mapping):
            if any(str(key) in _FORBIDDEN_PDF_KEYS for key in current):
                return True
            pending.extend(current.values())
        elif isinstance(current, (list, tuple, ArrayObject)):
            pending.extend(current)
    return False


def _pdf_fragments(text: str, limits: OrganizationSourceLimits) -> list[str]:
    normalized = unicodedata.normalize("NFC", text)
    fragments: list[str] = []
    for line in normalized.splitlines():
        safe_line = re.sub(r"\s+", " ", line).strip()
        if not safe_line:
            continue
        if (
            _CREDENTIAL.search(safe_line)
            or _PRIVATE_PATH.search(safe_line)
            or _COMMAND.search(safe_line)
        ):
            raise _reject("source_unsafe")
        start = 0
        while start < len(safe_line):
            end = min(start + limits.max_cell_chars, len(safe_line))
            if end < len(safe_line):
                boundary = safe_line.rfind(" ", start, end + 1)
                if boundary > start:
                    end = boundary
            fragment = safe_line[start:end].strip()
            if fragment:
                fragments.append(_safe_text(fragment, limits))
            start = end
            while start < len(safe_line) and safe_line[start] == " ":
                start += 1
    return fragments


def _parse_pdf(content: bytes, limits: OrganizationSourceLimits) -> tuple[SourceRecord, ...]:
    reader = pypdf.PdfReader(BytesIO(content), strict=True)
    if reader.is_encrypted or len(reader.pages) > limits.max_pdf_pages:
        raise _reject("source_unsafe")
    if _pdf_has_forbidden_objects(reader.root_object):
        raise _reject("source_unsafe")

    records: list[SourceRecord] = []
    for page_number, page in enumerate(reader.pages, start=1):
        fragments = _pdf_fragments(page.extract_text() or "", limits)
        for fragment_number, fragment in enumerate(fragments, start=1):
            if len(records) >= limits.max_records:
                raise _reject("source_too_large")
            ordinal = len(records) + 1
            records.append(
                SourceRecord(
                    record_id=f"rec_{ordinal:026d}",
                    source_ordinal=ordinal,
                    source_identity=f"pdf_page:{page_number}:fragment:{fragment_number}",
                    fields=(("text_fragment", fragment),),
                )
            )
    return tuple(records)


def _semantic_digest(records: tuple[SourceRecord, ...]) -> str:
    payload = [
        {
            "source_ordinal": record.source_ordinal,
            "source_identity": record.source_identity,
            "fields": [list(field) for field in record.fields],
        }
        for record in records
    ]
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _parse_organization_source(request: ParseOrganizationSourceRequest) -> SourceSnapshot:
    extension = _validate_request(request)
    if extension == ".csv":
        records = _parse_csv(request.content, request.limits)
        source_kind = "csv"
    elif extension == ".json":
        records = _parse_json(request.content, request.limits)
        source_kind = "json"
    elif extension == ".xlsx":
        records = _parse_xlsx(request.content, request.limits)
        source_kind = "xlsx"
    else:
        records = _parse_pdf(request.content, request.limits)
        source_kind = "pdf_fragment"
    digest = _semantic_digest(records)
    return SourceSnapshot(
        snapshot_id=f"snap_{digest[:26].upper()}",
        organization_id=request.organization_id,
        source_kind=source_kind,
        captured_at=datetime.now(UTC),
        records=records,
        semantic_digest=digest,
    )


def parse_organization_source(request: ParseOrganizationSourceRequest) -> SourceSnapshot:
    """Parse one upload while sealing every non-domain failure behind a fixed code."""

    result: SourceSnapshot | None = None
    rejected: OrganizationSourceRejected | None = None
    failed = False
    try:
        result = _parse_organization_source(request)
    except OrganizationSourceRejected as error:
        rejected = error
    except Exception:  # noqa: BLE001 - all parser/provider details are intentionally sealed
        failed = True
    if rejected is not None:
        rejected.__cause__ = None
        rejected.__context__ = None
        raise rejected from None
    if failed or result is None:
        raise OrganizationSourceRejected("source_invalid") from None
    return result
