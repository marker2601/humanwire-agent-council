"""Confined, deterministic parsing for untrusted organization source uploads."""

from __future__ import annotations

import csv
import hashlib
import json
import posixpath
import re
import stat
import traceback as traceback_module
import unicodedata
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO, StringIO
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

import openpyxl
import pypdf
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, StreamObject

from humanwire.organization_models import SourceRecord, SourceSnapshot

_MAX_INPUT_BYTES = 10 * 1024 * 1024
_MAX_RECORDS = 5_000
_MAX_CELL_CHARS = 120
_MAX_PDF_PAGES = 64
_MAX_ARCHIVE_ENTRIES = 1_000
_MAX_ARCHIVE_UNCOMPRESSED_BYTES = 20 * 1024 * 1024
_MAX_COMPRESSION_RATIO = 100
_MAX_PDF_OBJECTS = 20_000
_MAX_XLSX_COLUMNS = 256
_MAX_XML_BYTES = 10 * 1024 * 1024
_MAX_XML_NODES = 500_000
_MAX_XML_DEPTH = 64
_MAX_XML_TEXT_CHARS = 10 * 1024 * 1024
_CONTENT_TYPES_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/content-types"
_RELATIONSHIPS_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/relationships"
_WORKSHEET_NAMESPACE = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_DRAWING_NAMESPACE = "http://schemas.openxmlformats.org/drawingml/2006/main"
_CORE_PROPERTIES_NAMESPACE = (
    "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
)
_EXTENDED_PROPERTIES_NAMESPACE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
)
_CELL_REFERENCE = re.compile(r"^([A-Z]{1,3})([1-9][0-9]{0,6})$")
_XML_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_XML_DECLARATION = re.compile(
    r"<\?xml\s+version=(['\"])1\.0\1"
    r"(?:\s+encoding=(['\"])UTF-8\2)?"
    r"(?:\s+standalone=(['\"])(?:yes|no)\3)?\s*\?>",
    re.IGNORECASE,
)
_WORKSHEET_ELEMENTS = frozenset(
    {
        "c",
        "dimension",
        "is",
        "outlinePr",
        "pageMargins",
        "pageSetUpPr",
        "row",
        "selection",
        "sheetData",
        "sheetFormatPr",
        "sheetPr",
        "sheetView",
        "sheetViews",
        "t",
        "v",
        "worksheet",
    }
)
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
_CONTENT_TYPE_BY_PART = {
    "docProps/app.xml": ("application/vnd.openxmlformats-officedocument.extended-properties+xml"),
    "docProps/core.xml": "application/vnd.openxmlformats-package.core-properties+xml",
    "xl/calcChain.xml": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.calcChain+xml"
    ),
    "xl/sharedStrings.xml": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"
    ),
    "xl/styles.xml": ("application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"),
    "xl/workbook.xml": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
    ),
}
_WORKSHEET_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
)
_THEME_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.theme+xml"
_FORBIDDEN_PDF_KEYS = frozenset(
    {
        "/A",
        "/AA",
        "/AcroForm",
        "/Annots",
        "/EmbeddedFiles",
        "/EF",
        "/Filespec",
        "/ImportData",
        "/JavaScript",
        "/JS",
        "/Launch",
        "/Metadata",
        "/OpenAction",
        "/SubmitForm",
        "/XFA",
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


class _PdfTextLimitReached(BaseException):
    """Abort provider extraction before it can materialize excess text."""


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
    if (
        not name
        or name != unicodedata.normalize("NFC", name)
        or "\\" in name
        or "\x00" in name
        or name.startswith("/")
        or name.endswith("/")
        or "//" in name
    ):
        return True
    path = PurePosixPath(name)
    return (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or str(path) != name
        or bool(re.match(r"^[a-zA-Z]:", name))
    )


def _xlsx_part_content_type(name: str) -> str | None:
    if name in _CONTENT_TYPE_BY_PART:
        return _CONTENT_TYPE_BY_PART[name]
    if re.fullmatch(r"xl/worksheets/sheet[1-9][0-9]*\.xml", name):
        return _WORKSHEET_CONTENT_TYPE
    if re.fullmatch(r"xl/theme/theme[1-9][0-9]*\.xml", name):
        return _THEME_CONTENT_TYPE
    return None


def _allowed_xlsx_member(name: str) -> bool:
    if name in {"[Content_Types].xml", "_rels/.rels", "xl/_rels/workbook.xml.rels"}:
        return True
    return _xlsx_part_content_type(name) is not None


def _hardened_xml_bytes(
    content: bytes,
    expected_tag: str,
    *,
    maximum_nodes: int = _MAX_XML_NODES,
) -> bytes | None:
    if len(content) > _MAX_XML_BYTES or content.startswith((b"\xff\xfe", b"\xfe\xff")):
        return None
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    if "\ufeff" in decoded:
        return None
    declaration = _XML_DECLARATION.match(decoded)
    if decoded.startswith("<?xml"):
        if declaration is None:
            return None
    elif "<?xml" in decoded:
        return None
    normalized = unicodedata.normalize("NFC", decoded)
    lowered = normalized.casefold()
    if "<!doctype" in lowered or "<!entity" in lowered:
        return None
    normalized_bytes = normalized.encode("utf-8")
    depth = 0
    nodes = 0
    text_characters = 0
    root_seen = False
    try:
        for event, element in ElementTree.iterparse(
            BytesIO(normalized_bytes),
            events=("start", "end"),
        ):
            if event == "start":
                nodes += 1
                depth += 1
                if nodes > maximum_nodes or depth > _MAX_XML_DEPTH:
                    return None
                if not root_seen:
                    root_seen = True
                    if element.tag != expected_tag:
                        return None
            else:
                text_characters += len(element.text or "") + len(element.tail or "")
                if text_characters > _MAX_XML_TEXT_CHARS:
                    return None
                depth -= 1
                element.clear()
    except ElementTree.ParseError:
        return None
    if not root_seen or depth != 0:
        return None
    return normalized_bytes


def _safe_xml_root(
    content: bytes,
    expected_tag: str,
    *,
    maximum_nodes: int = _MAX_XML_NODES,
) -> ElementTree.Element | None:
    normalized = _hardened_xml_bytes(
        content,
        expected_tag,
        maximum_nodes=maximum_nodes,
    )
    return None if normalized is None else ElementTree.fromstring(normalized)


def _xlsx_xml_root(name: str) -> str | None:
    if name == "[Content_Types].xml":
        return f"{{{_CONTENT_TYPES_NAMESPACE}}}Types"
    if name.endswith(".rels"):
        return f"{{{_RELATIONSHIPS_NAMESPACE}}}Relationships"
    if name == "docProps/app.xml":
        return f"{{{_EXTENDED_PROPERTIES_NAMESPACE}}}Properties"
    if name == "docProps/core.xml":
        return f"{{{_CORE_PROPERTIES_NAMESPACE}}}coreProperties"
    if name == "xl/workbook.xml":
        return f"{{{_WORKSHEET_NAMESPACE}}}workbook"
    if name == "xl/styles.xml":
        return f"{{{_WORKSHEET_NAMESPACE}}}styleSheet"
    if name == "xl/sharedStrings.xml":
        return f"{{{_WORKSHEET_NAMESPACE}}}sst"
    if name == "xl/calcChain.xml":
        return f"{{{_WORKSHEET_NAMESPACE}}}calcChain"
    if re.fullmatch(r"xl/worksheets/sheet[1-9][0-9]*\.xml", name):
        return f"{{{_WORKSHEET_NAMESPACE}}}worksheet"
    if re.fullmatch(r"xl/theme/theme[1-9][0-9]*\.xml", name):
        return f"{{{_DRAWING_NAMESPACE}}}theme"
    return None


def _content_types_are_safe(content: bytes, names: set[str]) -> bool:
    root = _safe_xml_root(content, f"{{{_CONTENT_TYPES_NAMESPACE}}}Types")
    if root is None or len(root) > _MAX_ARCHIVE_ENTRIES:
        return False
    defaults: dict[str, str] = {}
    overrides: dict[str, str] = {}
    for child in root:
        if child.tag == f"{{{_CONTENT_TYPES_NAMESPACE}}}Default":
            if set(child.attrib) != {"Extension", "ContentType"}:
                return False
            extension = child.attrib["Extension"]
            content_type = child.attrib["ContentType"]
            if extension in defaults:
                return False
            defaults[extension] = content_type
        elif child.tag == f"{{{_CONTENT_TYPES_NAMESPACE}}}Override":
            if set(child.attrib) != {"PartName", "ContentType"}:
                return False
            raw_name = child.attrib["PartName"]
            if not raw_name.startswith("/") or raw_name.startswith("//"):
                return False
            name = raw_name[1:]
            if name in overrides or name not in names:
                return False
            overrides[name] = child.attrib["ContentType"]
        else:
            return False
    if defaults != {
        "rels": "application/vnd.openxmlformats-package.relationships+xml",
        "xml": "application/xml",
    }:
        return False
    expected = {
        name: content_type
        for name in names
        if (content_type := _xlsx_part_content_type(name)) is not None
    }
    return overrides == expected


def _relationship_source(name: str) -> str | None:
    if name == "_rels/.rels":
        return ""
    match = re.fullmatch(r"(.+)/_rels/([^/]+)\.rels", name)
    if match is None:
        return None
    return f"{match.group(1)}/{match.group(2)}"


def _resolved_relationship_target(source: str, target: str) -> str | None:
    if not target or "\\" in target or "\x00" in target:
        return None
    decoded = unquote(target)
    if unquote(decoded) != decoded or "%" in decoded:
        return None
    parsed = urlsplit(decoded)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        return None
    target_path = parsed.path
    if not target_path or any(part in {".", ".."} for part in PurePosixPath(target_path).parts):
        return None
    if target_path.startswith("/"):
        resolved = target_path[1:]
    else:
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(source), target_path))
    if not resolved or resolved.startswith("../") or _archive_member_is_unsafe(resolved):
        return None
    return resolved


def _relationship_matches_target(relationship_type: str, target: str) -> bool:
    kind = relationship_type.rsplit("/", 1)[-1]
    if kind == "officeDocument":
        return target == "xl/workbook.xml"
    if kind == "core-properties":
        return target == "docProps/core.xml"
    if kind == "extended-properties":
        return target == "docProps/app.xml"
    if kind == "worksheet":
        return re.fullmatch(r"xl/worksheets/sheet[1-9][0-9]*\.xml", target) is not None
    if kind == "styles":
        return target == "xl/styles.xml"
    if kind == "theme":
        return re.fullmatch(r"xl/theme/theme[1-9][0-9]*\.xml", target) is not None
    if kind == "sharedStrings":
        return target == "xl/sharedStrings.xml"
    if kind == "calcChain":
        return target == "xl/calcChain.xml"
    return False


def _safe_relationship_targets(
    name: str,
    content: bytes,
    names: set[str],
) -> tuple[str, tuple[str, ...]] | None:
    source = _relationship_source(name)
    if source is None or (source and source not in names):
        return None
    root = _safe_xml_root(content, f"{{{_RELATIONSHIPS_NAMESPACE}}}Relationships")
    if root is None or len(root) > _MAX_ARCHIVE_ENTRIES:
        return None
    identifiers: set[str] = set()
    targets: list[str] = []
    for relationship in root:
        if relationship.tag != f"{{{_RELATIONSHIPS_NAMESPACE}}}Relationship":
            return None
        if set(relationship.attrib) != {"Id", "Type", "Target"}:
            return None
        identifier = relationship.attrib["Id"]
        relationship_type = relationship.attrib["Type"]
        if (
            _XML_ID.fullmatch(identifier) is None
            or len(identifier) > 120
            or identifier in identifiers
            or relationship_type not in _SAFE_RELATIONSHIP_TYPES
        ):
            return None
        identifiers.add(identifier)
        target = _resolved_relationship_target(source, relationship.attrib["Target"])
        if (
            target is None
            or target not in names
            or not _relationship_matches_target(relationship_type, target)
        ):
            return None
        targets.append(target)
    return source, tuple(targets)


def _column_number(label: str) -> int:
    value = 0
    for character in label:
        value = value * 26 + ord(character) - ord("A") + 1
    return value


def _cell_coordinates(reference: str) -> tuple[int, int] | None:
    match = _CELL_REFERENCE.fullmatch(reference)
    if match is None:
        return None
    return int(match.group(2)), _column_number(match.group(1))


def _worksheet_bounds(
    content: bytes,
    limits: OrganizationSourceLimits,
) -> tuple[int, int, int, int, int] | None:
    maximum_nodes = (limits.max_records + 1) * (_MAX_XLSX_COLUMNS + 2) + 4_096
    normalized = _hardened_xml_bytes(
        content,
        f"{{{_WORKSHEET_NAMESPACE}}}worksheet",
        maximum_nodes=maximum_nodes,
    )
    if normalized is None:
        return None
    dimension: tuple[int, int, int, int] | None = None
    active_row: int | None = None
    active_first_column = 0
    active_last_column = 0
    active_cell_count = 0
    rows: list[tuple[int, int, int, int]] = []
    node_count = 0
    cell_count = 0
    root_seen = False
    for event, element in ElementTree.iterparse(BytesIO(normalized), events=("start", "end")):
        if event == "start":
            node_count += 1
            if node_count > maximum_nodes:
                raise _reject("source_too_large")
            if not isinstance(element.tag, str):
                return None
            local_name = element.tag.rsplit("}", 1)[-1]
            if local_name == "f":
                return None
            if element.tag != f"{{{_WORKSHEET_NAMESPACE}}}{local_name}":
                return None
            if local_name not in _WORKSHEET_ELEMENTS:
                return None
            if not root_seen:
                root_seen = True
                if element.tag != f"{{{_WORKSHEET_NAMESPACE}}}worksheet":
                    return None
            if element.tag == f"{{{_WORKSHEET_NAMESPACE}}}dimension":
                if dimension is not None or set(element.attrib) != {"ref"}:
                    return None
                endpoints = element.attrib["ref"].split(":")
                if len(endpoints) not in {1, 2}:
                    return None
                start = _cell_coordinates(endpoints[0])
                end = _cell_coordinates(endpoints[-1])
                if start is None or end is None:
                    return None
                min_row, min_column = start
                max_row, max_column = end
                if min_row > max_row or min_column > max_column or max_column > _MAX_XLSX_COLUMNS:
                    return None
                if max_row - min_row + 1 > limits.max_records + 1:
                    raise _reject("source_too_large")
                dimension = (min_row, max_row, min_column, max_column)
            elif element.tag == f"{{{_WORKSHEET_NAMESPACE}}}row":
                if active_row is not None or set(element.attrib) - {"r", "spans"}:
                    return None
                raw_row = element.attrib.get("r", "")
                if not raw_row.isdecimal() or raw_row.startswith("0"):
                    return None
                active_row = int(raw_row)
                active_first_column = 0
                active_last_column = 0
                active_cell_count = 0
            elif element.tag == f"{{{_WORKSHEET_NAMESPACE}}}c":
                coordinates = _cell_coordinates(element.attrib.get("r", ""))
                if active_row is None or coordinates is None:
                    return None
                row_number, column_number = coordinates
                if (
                    row_number != active_row
                    or column_number > _MAX_XLSX_COLUMNS
                    or column_number <= active_last_column
                ):
                    return None
                active_first_column = active_first_column or column_number
                active_last_column = column_number
                active_cell_count += 1
                cell_count += 1
                if cell_count > (limits.max_records + 1) * _MAX_XLSX_COLUMNS:
                    raise _reject("source_too_large")
        elif element.tag == f"{{{_WORKSHEET_NAMESPACE}}}row":
            if active_row is None or active_cell_count == 0:
                return None
            if rows and active_row <= rows[-1][0]:
                return None
            rows.append((active_row, active_first_column, active_last_column, active_cell_count))
            if len(rows) > limits.max_records + 1:
                raise _reject("source_too_large")
            active_row = None
        if event == "end":
            element.clear()
    if not root_seen or dimension is None or active_row is not None:
        return None
    min_row, max_row, min_column, max_column = dimension
    if not rows:
        return (*dimension, 0) if dimension == (1, 1, 1, 1) else None
    if rows[0][0] != min_row or rows[-1][0] != max_row or len(rows) != max_row - min_row + 1:
        return None
    width = max_column - min_column + 1
    if any(
        row_number != min_row + index
        or first_column != min_column
        or last_column != max_column
        or count != width
        for index, (row_number, first_column, last_column, count) in enumerate(rows)
    ):
        return None
    return (*dimension, len(rows))


def _inspect_xlsx_archive(
    content: bytes,
    limits: OrganizationSourceLimits,
) -> dict[str, tuple[int, int, int, int, int]] | None:
    try:
        with ZipFile(BytesIO(content)) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > _MAX_ARCHIVE_ENTRIES:
                return None
            names: set[str] = set()
            canonical_names: set[str] = set()
            total_size = 0
            for info in infos:
                lowered = info.filename.casefold()
                unix_mode = (info.external_attr >> 16) & 0xFFFF
                file_type = stat.S_IFMT(unix_mode)
                if (
                    info.filename in names
                    or lowered in canonical_names
                    or _archive_member_is_unsafe(info.filename)
                    or not _allowed_xlsx_member(info.filename)
                    or (file_type not in {0, stat.S_IFREG})
                ):
                    return None
                names.add(info.filename)
                canonical_names.add(lowered)
                total_size += info.file_size
                if total_size > _MAX_ARCHIVE_UNCOMPRESSED_BYTES or info.flag_bits & 0x1:
                    return None
                if (
                    info.file_size > 128 * 1024
                    and info.file_size > max(1, info.compress_size) * _MAX_COMPRESSION_RATIO
                ):
                    return None
                if (
                    lowered.endswith("vbaproject.bin")
                    or "customxml" in lowered
                    or "oleobject" in lowered
                    or "/externallinks/" in lowered
                    or "/embeddings/" in lowered
                    or "/activex/" in lowered
                ):
                    return None
            required = {
                "[Content_Types].xml",
                "_rels/.rels",
                "xl/_rels/workbook.xml.rels",
                "xl/workbook.xml",
            }
            if not required.issubset(names):
                return None
            xml_parts: dict[str, bytes] = {}
            worksheet_nodes = (limits.max_records + 1) * (_MAX_XLSX_COLUMNS + 2) + 4_096
            for name in names:
                expected_root = _xlsx_xml_root(name)
                if expected_root is None:
                    return None
                maximum_nodes = (
                    worksheet_nodes if name.startswith("xl/worksheets/") else _MAX_XML_NODES
                )
                normalized = _hardened_xml_bytes(
                    archive.read(name),
                    expected_root,
                    maximum_nodes=maximum_nodes,
                )
                if normalized is None:
                    return None
                xml_parts[name] = normalized
            if not _content_types_are_safe(xml_parts["[Content_Types].xml"], names):
                return None

            edges: dict[str, tuple[str, ...]] = {}
            for name in names:
                if not name.endswith(".rels"):
                    continue
                relationship = _safe_relationship_targets(name, xml_parts[name], names)
                if relationship is None:
                    return None
                source, targets = relationship
                if source in edges:
                    return None
                edges[source] = targets
            reachable: set[str] = set()
            pending = list(edges.get("", ()))
            while pending:
                target = pending.pop()
                if target in reachable:
                    continue
                reachable.add(target)
                pending.extend(edges.get(target, ()))
            content_parts = {
                name
                for name in names
                if name != "[Content_Types].xml" and not name.endswith(".rels")
            }
            if reachable != content_parts:
                return None

            bounds: dict[str, tuple[int, int, int, int, int]] = {}
            total_records = 0
            for name in sorted(names):
                if not re.fullmatch(r"xl/worksheets/sheet[1-9][0-9]*\.xml", name):
                    continue
                sheet_bounds = _worksheet_bounds(xml_parts[name], limits)
                if sheet_bounds is None:
                    return None
                bounds[name] = sheet_bounds
                row_count = sheet_bounds[-1]
                total_records += max(0, row_count - 1)
                if total_records > limits.max_records:
                    raise _reject("source_too_large")
            if not bounds:
                return None
            return bounds
    except OrganizationSourceRejected:
        raise
    except (BadZipFile, KeyError, OSError, ElementTree.ParseError, UnicodeError, ValueError):
        return None


def _xlsx_rows(
    content: bytes,
    limits: OrganizationSourceLimits,
    bounds: dict[str, tuple[int, int, int, int, int]],
) -> list[list[str]]:
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
        visited: set[str] = set()
        for worksheet in workbook.worksheets:
            worksheet_path = getattr(worksheet, "_worksheet_path", "").lstrip("/")
            if worksheet_path not in bounds or worksheet_path in visited:
                raise _reject("source_unsafe")
            visited.add(worksheet_path)
            min_row, max_row, min_column, max_column, row_count = bounds[worksheet_path]
            if row_count == 0:
                continue
            sheet_rows: list[list[str]] = []
            for cells in worksheet.iter_rows(
                min_row=min_row,
                max_row=max_row,
                min_col=min_column,
                max_col=max_column,
            ):
                values = list(cells)
                if any(cell.data_type == "f" for cell in values):
                    raise _reject("source_unsafe")
                if any(cell.value is None or type(cell.value) is not str for cell in values):
                    raise _reject("source_unsafe")
                sheet_rows.append([cell.value for cell in values])
            if sheet_rows:
                if not rows:
                    rows.extend(sheet_rows)
                else:
                    if sheet_rows[0] != rows[0]:
                        raise _reject("source_unsafe")
                    rows.extend(sheet_rows[1:])
        if visited != set(bounds):
            raise _reject("source_unsafe")
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
    bounds = _inspect_xlsx_archive(content, limits)
    if bounds is None:
        raise _reject("source_unsafe")
    return _records_from_rows(_xlsx_rows(content, limits, bounds), limits)


def _pdf_value_is_unsafe(value: str | bytes, limits: OrganizationSourceLimits) -> bool:
    failed = False
    decoded = ""
    if isinstance(value, bytes):
        try:
            decoded = bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            failed = True
    else:
        decoded = str(value)
    if failed:
        return True
    try:
        _safe_text(decoded, limits)
    except OrganizationSourceRejected:
        return True
    return False


def _pdf_has_forbidden_objects(root: Any, limits: OrganizationSourceLimits) -> bool:
    pending: list[Any] = [root]
    seen_indirect: set[tuple[int, int]] = set()
    seen_direct: set[int] = set()
    visited = 0
    while pending:
        current = pending.pop()
        visited += 1
        if visited > _MAX_PDF_OBJECTS:
            return True
        if isinstance(current, IndirectObject):
            key = (current.idnum, current.generation)
            if key in seen_indirect:
                continue
            seen_indirect.add(key)
            pending.append(current.get_object())
            continue
        if isinstance(current, (str, bytes)):
            if _pdf_value_is_unsafe(current, limits):
                return True
            continue
        if isinstance(current, (DictionaryObject, ArrayObject)):
            direct_id = id(current)
            if direct_id in seen_direct:
                continue
            seen_direct.add(direct_id)
        if isinstance(current, Mapping):
            if any(str(key) in _FORBIDDEN_PDF_KEYS for key in current):
                return True
            for key, value in current.items():
                pending.extend((key, value))
        elif isinstance(current, (list, tuple, ArrayObject)):
            pending.extend(current)
    return False


def _pdf_content_streams(page: Any) -> tuple[StreamObject, ...] | None:
    pending: list[tuple[str, Any]] = []
    if "/Contents" in page:
        pending.append(("contents", page.raw_get("/Contents")))
    if "/Resources" in page:
        pending.append(("resources", page.raw_get("/Resources")))
    streams: list[StreamObject] = []
    seen_indirect: set[tuple[int, int]] = set()
    seen_direct: set[int] = set()
    traversed = 0
    while pending:
        kind, current = pending.pop()
        traversed += 1
        if traversed > _MAX_PDF_OBJECTS:
            return None
        if isinstance(current, IndirectObject):
            key = (current.idnum, current.generation)
            if key in seen_indirect:
                return None
            seen_indirect.add(key)
            pending.append((kind, current.get_object()))
            continue
        if isinstance(current, (DictionaryObject, ArrayObject)):
            direct_id = id(current)
            if direct_id in seen_direct:
                return None
            seen_direct.add(direct_id)
        if kind == "contents":
            if isinstance(current, StreamObject):
                streams.append(current)
            elif isinstance(current, ArrayObject):
                pending.extend(("contents", item) for item in reversed(current))
            else:
                return None
        elif kind == "resources":
            if not isinstance(current, DictionaryObject):
                return None
            if "/XObject" in current:
                pending.append(("xobjects", current.raw_get("/XObject")))
        elif kind == "xobjects":
            if not isinstance(current, DictionaryObject):
                return None
            pending.extend(("xobject", current.raw_get(key)) for key in current)
        elif kind == "xobject":
            if not isinstance(current, StreamObject):
                return None
            if str(current.get("/Subtype", "")) != "/Form":
                continue
            streams.append(current)
            if "/Resources" in current:
                pending.append(("resources", current.raw_get("/Resources")))
        else:
            return None
    return tuple(streams)


def _bounded_flate_size(data: bytes, maximum: int) -> int | None:
    decompressor = zlib.decompressobj()
    total = 0
    failed = False
    try:
        for offset in range(0, len(data), 64 * 1024):
            pending = data[offset : offset + 64 * 1024]
            while pending:
                chunk = decompressor.decompress(pending, maximum - total + 1)
                total += len(chunk)
                if total > maximum:
                    raise _reject("source_too_large")
                pending = decompressor.unconsumed_tail
        tail = decompressor.flush(maximum - total + 1)
        total += len(tail)
        if total > maximum:
            raise _reject("source_too_large")
        if not decompressor.eof or decompressor.unused_data:
            failed = True
    except OrganizationSourceRejected:
        raise
    except zlib.error:
        failed = True
    return None if failed else total


def _decoded_stream_size(stream: StreamObject, maximum: int) -> int | None:
    raw_data = getattr(stream, "_data", None)
    if type(raw_data) is not bytes:
        return None
    if "/DecodeParms" in stream:
        return None
    if "/Filter" not in stream:
        if len(raw_data) > maximum:
            raise _reject("source_too_large")
        return len(raw_data)
    filter_value = stream.raw_get("/Filter")
    if isinstance(filter_value, ArrayObject):
        if len(filter_value) != 1:
            return None
        filter_value = filter_value[0]
    if str(filter_value) != "/FlateDecode":
        return None
    return _bounded_flate_size(raw_data, maximum)


def _preflight_pdf_content(reader: Any, limits: OrganizationSourceLimits) -> None:
    total = 0
    for page in reader.pages:
        streams = _pdf_content_streams(page)
        if streams is None:
            raise _reject("source_unsafe")
        for stream in streams:
            size = _decoded_stream_size(stream, limits.max_input_bytes - total)
            if size is None:
                raise _reject("source_unsafe")
            total += size


def _append_pdf_line_fragments(
    safe_line: str,
    *,
    page_number: int,
    fragment_number: int,
    limits: OrganizationSourceLimits,
    records: list[SourceRecord],
) -> int:
    start = 0
    while start < len(safe_line):
        end = min(start + limits.max_cell_chars, len(safe_line))
        if end < len(safe_line):
            boundary = safe_line.rfind(" ", start, end + 1)
            if boundary > start:
                end = boundary
        fragment = safe_line[start:end].strip()
        if fragment:
            if len(records) >= limits.max_records:
                raise _reject("source_too_large")
            fragment_number += 1
            ordinal = len(records) + 1
            records.append(
                SourceRecord(
                    record_id=f"rec_{ordinal:026d}",
                    source_ordinal=ordinal,
                    source_identity=f"pdf_page:{page_number}:fragment:{fragment_number}",
                    fields=(("text_fragment", _safe_text(fragment, limits)),),
                )
            )
        start = end
        while start < len(safe_line) and safe_line[start] == " ":
            start += 1
    return fragment_number


def _parse_pdf(content: bytes, limits: OrganizationSourceLimits) -> tuple[SourceRecord, ...]:
    reader = pypdf.PdfReader(BytesIO(content), strict=True)
    if reader.is_encrypted or len(reader.pages) > limits.max_pdf_pages:
        raise _reject("source_unsafe")
    if _pdf_has_forbidden_objects(reader.trailer, limits):
        raise _reject("source_unsafe")
    _preflight_pdf_content(reader, limits)

    records: list[SourceRecord] = []
    provider_text = 0
    normalized_text = 0
    maximum_text = limits.max_records * limits.max_cell_chars

    def count_provider_text(text: str, *_args: Any) -> None:
        nonlocal provider_text
        normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()
        provider_text += len(normalized)
        if provider_text > maximum_text:
            raise _PdfTextLimitReached

    for page_number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text(visitor_text=count_provider_text) or ""
        except _PdfTextLimitReached:
            raise _reject("source_too_large") from None
        fragment_number = 0
        for line in StringIO(text):
            safe_line = re.sub(r"\s+", " ", unicodedata.normalize("NFC", line)).strip()
            if not safe_line:
                continue
            normalized_text += len(safe_line)
            if normalized_text > maximum_text:
                raise _reject("source_too_large")
            if (
                _CREDENTIAL.search(safe_line)
                or _PRIVATE_PATH.search(safe_line)
                or _COMMAND.search(safe_line)
            ):
                raise _reject("source_unsafe")
            fragment_number = _append_pdf_line_fragments(
                safe_line,
                page_number=page_number,
                fragment_number=fragment_number,
                limits=limits,
                records=records,
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
    code: str | None = None
    try:
        result = _parse_organization_source(request)
    except OrganizationSourceRejected as error:
        code = str(error) if str(error) in _ERROR_CODES else "source_invalid"
        traceback_module.clear_frames(error.__traceback__)
        error.__traceback__ = None
        error.__cause__ = None
        error.__context__ = None
    except Exception as error:  # noqa: BLE001 - parser/provider details are intentionally sealed
        code = "source_invalid"
        traceback_module.clear_frames(error.__traceback__)
        error.__traceback__ = None
        error.__cause__ = None
        error.__context__ = None
    if code is not None or result is None:
        code = code or "source_invalid"
        request = None  # type: ignore[assignment]  # erase source-bearing active-frame local
        result = None
        _raise_fixed_source_error(code)
    return result


def _raise_fixed_source_error(code: str) -> None:
    raise OrganizationSourceRejected(code) from None
