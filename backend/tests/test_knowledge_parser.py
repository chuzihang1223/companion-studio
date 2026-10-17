from io import BytesIO
from zipfile import ZipFile

import pytest

from app.services.knowledge.chunker import chunk_document
from app.services.knowledge.contracts import KnowledgeError
from app.services.knowledge.isolated_parser import parse_isolated
from app.services.knowledge.parser import parse


def make_docx():
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>授权知识库</w:t></w:r></w:p><w:p><w:r><w:t>第二段为正确出处。</w:t></w:r></w:p></w:body></w:document>',
        )
    return buffer.getvalue()


def make_pdf():
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    for value in ("Page one orchard", "Page two knowledge evidence"):
        page = writer.add_blank_page(width=300, height=300)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 10 100 Td ({value}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_structured_md_code_and_stable_chunks():
    blocks = parse("# 安装\n\n使用隔离环境。\n\n## 启动\n\n运行本机应用。".encode(), ".md")
    assert blocks[-1]["location"]["heading"] == "启动"
    assert blocks[-1]["location"]["line_start"] == 7
    source = "import os\n\ndef load_project():\n    return '项目'\n\nclass Collection:\n    pass\n"
    code = parse(source.encode(), ".py")
    assert code[1]["location"]["symbol"] == "load_project"
    assert code[1]["location"]["line_start"] == 3
    chunks = chunk_document("test", code, size=20, overlap=3)
    assert chunks == chunk_document("test", code, size=20, overlap=3)
    assert len({row["id"] for row in chunks}) == len(chunks)
    assert all(len(row["content"]) <= 20 for row in chunks)
    assert all(row["location"]["line_start"] <= row["location"]["line_end"] for row in chunks)


@pytest.mark.parametrize(
    "kind,fixture,coordinate", [(".pdf", make_pdf, "page"), (".docx", make_docx, "paragraph")]
)
def test_document_positions_and_isolated_worker(kind, fixture, coordinate):
    blocks = parse(fixture(), kind)
    assert blocks[1]["location"][coordinate] == 2
    assert parse_isolated(fixture(), kind) == blocks


def test_corrupt_binary_encoding_and_chunk_limits():
    for kind in (".pdf", ".docx"):
        with pytest.raises(KnowledgeError, match="CORRUPT"):
            parse(b"broken", kind)
    with pytest.raises(KnowledgeError, match="BINARY"):
        parse(b"a\x00b", ".txt")
    assert parse("中文编码".encode("gb18030"), ".txt")[0]["text"] == "中文编码"
    assert parse("中文编码".encode("utf-16"), ".txt")[0]["text"] == "中文编码"
    with pytest.raises(KnowledgeError, match="CHUNK_LIMIT"):
        chunk_document(
            "doc", [{"text": "x" * 1000, "location": {}}], size=100, overlap=0, maximum=2
        )
