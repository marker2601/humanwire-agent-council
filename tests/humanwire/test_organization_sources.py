"""Confined parsing tests for organization source uploads."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from openpyxl import Workbook
from pypdf import PdfWriter

from humanwire import organization_sources
from humanwire.organization_models import SourceRecord
from humanwire.organization_sources import (
    OrganizationSourceLimits,
    OrganizationSourceRejected,
    ParseOrganizationSourceRequest,
    parse_organization_source,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "humanwire" / "organization"
ORG_ID = "org_01J00000000000000000000000"
EXPECTED_DIGEST = "9c18ef1f6480bb4ca6c69be44d086362853d8d1d31dde8892e8ad5e6c0fe2541"
MIME_TYPES = {
    ".csv": "text/csv",
    ".json": "application/json",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pdf": "application/pdf",
}


def expected_source_records() -> tuple[SourceRecord, ...]:
    return (
        SourceRecord(
            record_id="rec_00000000000000000000000001",
            source_ordinal=1,
            source_identity="person:ada",
            fields=(("display_name", "Ada Lovelace"), ("title", "Chief Architect")),
        ),
        SourceRecord(
            record_id="rec_00000000000000000000000002",
            source_ordinal=2,
            source_identity="person:grace",
            fields=(("display_name", "Grace Hopper"), ("title", "Engineering Lead")),
        ),
    )


def source_request(
    fixture: str,
    *,
    content: bytes | None = None,
    filename: str | None = None,
    content_type: str | None = None,
    limits: OrganizationSourceLimits | None = None,
) -> ParseOrganizationSourceRequest:
    suffix = Path(fixture).suffix.lower()
    return ParseOrganizationSourceRequest(
        content=(FIXTURES / fixture).read_bytes() if content is None else content,
        filename=fixture if filename is None else filename,
        content_type=MIME_TYPES[suffix] if content_type is None else content_type,
        organization_id=ORG_ID,
        limits=limits or OrganizationSourceLimits(),
    )


def xlsx_bytes(rows: tuple[tuple[str, ...], ...]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def xlsx_with_formula(formula: str) -> ParseOrganizationSourceRequest:
    content = xlsx_bytes(
        (
            ("source_identity", "display_name", "title"),
            ("person:ada", "Ada Lovelace", formula),
        )
    )
    return source_request("sample.xlsx", content=content)


def xlsx_with_member(name: str, content: bytes) -> bytes:
    source = (FIXTURES / "sample.xlsx").read_bytes()
    output = BytesIO()
    with ZipFile(BytesIO(source)) as archive, ZipFile(output, "w", ZIP_DEFLATED) as target:
        for info in archive.infolist():
            target.writestr(info, archive.read(info.filename))
        target.writestr(name, content)
    return output.getvalue()


def pdf_bytes(
    *,
    pages: int = 1,
    encrypted: bool = False,
    attachment: bool = False,
    javascript: bool = False,
) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    if attachment:
        writer.add_attachment("private.txt", b"private")
    if javascript:
        writer.add_js("app.alert('private')")
    if encrypted:
        writer.encrypt("private-password")
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def exception_graph_text(error: BaseException) -> str:
    pending = [error]
    seen: set[int] = set()
    rendered: list[str] = []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        rendered.extend((str(current), repr(current), repr(current.args)))
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return " ".join(rendered)


@pytest.mark.parametrize("fixture", ["sample.csv", "sample.json", "sample.xlsx"])
def test_structured_sources_produce_equal_canonical_rows(fixture: str) -> None:
    snapshot = parse_organization_source(source_request(fixture))

    assert snapshot.records == expected_source_records()
    assert snapshot.semantic_digest == EXPECTED_DIGEST
    assert snapshot.organization_id == ORG_ID


def test_pdf_produces_bounded_fragments_not_structured_people() -> None:
    snapshot = parse_organization_source(source_request("sample.pdf"))

    assert snapshot.source_kind == "pdf_fragment"
    assert snapshot.records
    assert all(record.fields[0][0] == "text_fragment" for record in snapshot.records)
    assert all(len(record.fields) == 1 for record in snapshot.records)
    assert all(len(record.fields[0][1]) <= 120 for record in snapshot.records)
    assert all(record.source_identity.startswith("pdf_page:") for record in snapshot.records)


@pytest.mark.parametrize(
    ("fixture", "content_type"),
    [
        ("sample.csv", "application/json"),
        ("sample.json", "text/csv"),
        ("sample.xlsx", "application/zip"),
        ("sample.pdf", "application/octet-stream"),
        ("sample.csv", "TEXT/CSV"),
        ("sample.csv", "text/csv; charset=utf-8"),
    ],
)
def test_extension_and_mime_must_match_exact_allowlist(
    fixture: str,
    content_type: str,
) -> None:
    with pytest.raises(OrganizationSourceRejected, match="^source_unsupported$"):
        parse_organization_source(source_request(fixture, content_type=content_type))


def test_extension_dispatch_is_lowercase_normalized() -> None:
    snapshot = parse_organization_source(source_request("sample.csv", filename="SAMPLE.CSV"))

    assert snapshot.records == expected_source_records()


def test_files_over_ten_mebibytes_are_rejected_before_parsing() -> None:
    content = b"x" * (10 * 1024 * 1024 + 1)

    with pytest.raises(OrganizationSourceRejected, match="^source_too_large$"):
        parse_organization_source(source_request("sample.csv", content=content))


@pytest.mark.parametrize(
    "content",
    [
        b"source_identity,display_name\nperson:one,\xff\n",
        b'source_identity,display_name\nperson:one,"unterminated\n',
        b"source_identity,display_name\nperson:one,Ada\x00Lovelace\n",
        b"source_identity,display_name\nperson:one,Ada\x01Lovelace\n",
        b"source_identity,display_name\nperson:one,=HYPERLINK(https://internal)\n",
        b"source_identity,display_name\nperson:one,+cmd|' /C private'!A0\n",
    ],
)
def test_csv_rejects_malformed_or_active_content(content: bytes) -> None:
    with pytest.raises(OrganizationSourceRejected, match="^source_unsafe$"):
        parse_organization_source(source_request("sample.csv", content=content))


def test_csv_rejects_overlong_cells() -> None:
    content = f"source_identity,display_name\nperson:one,{'x' * 121}\n".encode()

    with pytest.raises(OrganizationSourceRejected, match="^source_too_large$"):
        parse_organization_source(source_request("sample.csv", content=content))


def test_csv_rejects_more_than_five_thousand_records() -> None:
    rows = ["source_identity,display_name"]
    rows.extend(f"person:{number},Person {number}" for number in range(5_001))

    with pytest.raises(OrganizationSourceRejected, match="^source_too_large$"):
        parse_organization_source(
            source_request("sample.csv", content=("\n".join(rows) + "\n").encode())
        )


def test_request_limits_may_reduce_but_not_raise_hard_boundaries() -> None:
    with pytest.raises(OrganizationSourceRejected, match="^source_too_large$"):
        parse_organization_source(
            source_request("sample.csv", limits=OrganizationSourceLimits(max_records=1))
        )

    with pytest.raises(ValueError, match="hard maximum"):
        OrganizationSourceLimits(max_records=5_001)


@pytest.mark.parametrize(
    "content",
    [
        b'{"source_identity":"person:one"}',
        b'[{"source_identity":"person:one","display_name":{"nested":"Ada"}}]',
        b'[{"source_identity":"person:one","display_name":42}]',
        b'[{"source_identity":"person:one","display_name":"Ada","display_name":"Grace"}]',
        b'[{"source_identity":"person:one","score":NaN}]',
        b'[{"source_identity":"person:one","score":Infinity}]',
        b'[{"source_identity":"person:one","score":-Infinity}]',
    ],
)
def test_json_rejects_duplicate_non_finite_or_invalid_structure(content: bytes) -> None:
    with pytest.raises(OrganizationSourceRejected, match="^source_unsafe$"):
        parse_organization_source(source_request("sample.json", content=content))


def test_xlsx_formula_is_rejected() -> None:
    with pytest.raises(OrganizationSourceRejected, match="^source_unsafe$"):
        parse_organization_source(xlsx_with_formula('=HYPERLINK("https://internal")'))


@pytest.mark.parametrize(
    ("member", "content"),
    [
        ("../private.txt", b"private"),
        ("xl/vbaProject.bin", b"macro"),
        (
            "custom/_rels/unsafe.rels",
            (
                b'<?xml version="1.0"?><Relationships '
                b'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                b'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
                b'officeDocument/2006/relationships/hyperlink" '
                b'Target="https://private.invalid" '
                b'TargetMode="External"/></Relationships>'
            ),
        ),
        (
            "custom/_rels/unsupported.rels",
            (
                b'<?xml version="1.0"?><Relationships '
                b'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                b'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
                b'officeDocument/2006/relationships/oleObject" '
                b'Target="../private.bin"/></Relationships>'
            ),
        ),
    ],
)
def test_xlsx_rejects_traversal_macros_and_unsafe_relationships(
    member: str,
    content: bytes,
) -> None:
    hostile = xlsx_with_member(member, content)

    with pytest.raises(OrganizationSourceRejected, match="^source_unsafe$"):
        parse_organization_source(source_request("sample.xlsx", content=hostile))


def test_xlsx_rejects_zip_bombs() -> None:
    hostile = xlsx_with_member("xl/bomb.bin", b"0" * (1024 * 1024))

    with pytest.raises(OrganizationSourceRejected, match="^source_unsafe$"):
        parse_organization_source(source_request("sample.xlsx", content=hostile))


@pytest.mark.parametrize(
    "content",
    [
        pdf_bytes(pages=65),
        pdf_bytes(encrypted=True),
        pdf_bytes(attachment=True),
        pdf_bytes(javascript=True),
    ],
)
def test_pdf_rejects_over_page_encrypted_embedded_and_active_files(content: bytes) -> None:
    with pytest.raises(OrganizationSourceRejected, match="^source_unsafe$"):
        parse_organization_source(source_request("sample.pdf", content=content))


@pytest.mark.parametrize(
    "private_value",
    [
        "api_key=sk-private-organization-secret",
        r"C:\Users\private\organization.xlsx",
        "/home/private/organization.csv",
        "powershell Invoke-Expression private-command",
        "rm -rf /private/organization",
    ],
)
def test_embedded_credentials_private_paths_and_commands_are_rejected(
    private_value: str,
) -> None:
    content = f"source_identity,display_name\nperson:one,{private_value}\n".encode()

    with pytest.raises(OrganizationSourceRejected, match="^source_unsafe$") as captured:
        parse_organization_source(source_request("sample.csv", content=content))

    assert private_value not in exception_graph_text(captured.value)


def test_fixed_errors_do_not_retain_filename_path_or_parsed_values() -> None:
    private_filename = r"C:\Users\private\organization.csv"
    private_value = "password=private-password-sentinel"
    request = source_request(
        "sample.csv",
        content=f"source_identity,display_name\nperson:one,{private_value}\n".encode(),
        filename=private_filename,
    )

    with pytest.raises(OrganizationSourceRejected) as captured:
        parse_organization_source(request)

    rendered = exception_graph_text(captured.value)
    assert str(captured.value) in {
        "source_invalid",
        "source_unsafe",
        "source_unsupported",
        "source_too_large",
    }
    assert private_filename not in rendered
    assert private_value not in rendered
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_provider_exceptions_are_sealed_without_retained_sentinel(monkeypatch) -> None:
    sentinel = "PRIVATE-PDF-PROVIDER-SENTINEL"

    def fail_reader(*_args, **_kwargs):
        raise RuntimeError(sentinel)

    monkeypatch.setattr(organization_sources.pypdf, "PdfReader", fail_reader)

    with pytest.raises(OrganizationSourceRejected, match="^source_invalid$") as captured:
        parse_organization_source(source_request("sample.pdf"))

    assert sentinel not in exception_graph_text(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
