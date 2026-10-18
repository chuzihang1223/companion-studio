"""Original MIT fixtures and known answers; no user documents or model requests."""

from io import BytesIO
import json
from zipfile import ZipFile

from app.services.knowledge.context import NO_EVIDENCE

GUIDE = "# 安装\n\n知识库支持中文增量索引。Orchard launch port is 8794.\n"
CODE = "import math\n\ndef load_document():\n    return 'orchard evidence'\n\nclass Collection:\n    pass\n"


def pdf():
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


def docx():
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>授权知识库</w:t></w:r></w:p><w:p><w:r><w:t>第二段为正确出处。</w:t></w:r></w:p></w:body></w:document>',
        )
    return output.getvalue()


class GroundedMockModel:
    """Fixed fixture assertion, not an assessment of a real model's knowledge."""

    calls = 0

    async def chat(self, messages):
        self.calls += 1
        for message in reversed(messages):
            value = message.get("content", "")
            marker = "UNTRUSTED_KNOWLEDGE_DATA\n"
            if isinstance(value, str) and marker in value:
                rows = json.loads(value.split(marker)[-1])
                rows = [part for row in rows for part in row.get("adjacent_parts", [row])]
                source = next((r for r in rows if "8794" in r["excerpt"]), None)
                if source:
                    return f"根据合成项目资料，启动端口为 8794。[K:{source['chunk_id']}]"
        return NO_EVIDENCE

    async def stream_chat(self, messages):
        yield await self.chat(messages)
