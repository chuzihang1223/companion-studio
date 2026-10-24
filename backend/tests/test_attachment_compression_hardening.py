"""Reject unused OOXML decoders before decompression, preserving normal DOCX."""

from io import BytesIO
from zipfile import ZIP_BZIP2, ZIP_DEFLATED, ZIP_LZMA, ZIP_STORED, ZipFile

import pytest

from app.services.attachment_service import AttachmentService


def archive(compression):
    stream = BytesIO()
    xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>安全的正文</w:t></w:r></w:p></w:body></w:document>"
    )
    with ZipFile(stream, "w", compression=compression) as output:
        output.writestr("word/document.xml", xml)
    return stream.getvalue()


@pytest.mark.parametrize("compression", [ZIP_STORED, ZIP_DEFLATED])
def test_supported_docx_compression_preserves_chinese(compression):
    assert AttachmentService._extract_docx(archive(compression)) == "安全的正文"


@pytest.mark.parametrize("compression", [ZIP_BZIP2, ZIP_LZMA])
def test_other_docx_compression_is_rejected_before_open(monkeypatch, compression):
    raw = archive(compression)

    def unexpected_decoder(*_args, **_kwargs):
        pytest.fail("Rejected compression must never enter the decoder")

    monkeypatch.setattr(ZipFile, "open", unexpected_decoder)
    with pytest.raises(ValueError, match="不支持的压缩方式"):
        AttachmentService._extract_docx(raw)
