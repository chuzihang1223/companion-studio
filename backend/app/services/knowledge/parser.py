"""Text-layer parsers only. No OCR, scripts, macros, or remote references execute."""

import ast
from io import BytesIO
import re
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from app.services.knowledge.contracts import FORMATS, KnowledgeError

VERSION = "knowledge-1"
MAX_TEXT = 500000


def decode(raw):
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        encoding = "utf-16"
    else:
        encoding = "utf-8-sig"
    try:
        value = raw.decode(encoding)
    except UnicodeError:
        try:
            value = raw.decode("gb18030")
        except UnicodeError:
            raise KnowledgeError("KNOWLEDGE_ENCODING_UNSUPPORTED") from None
    if "\x00" in value or sum(ord(c) < 32 and c not in "\r\n\t" for c in value) > max(
        1, len(value) // 100
    ):
        raise KnowledgeError("KNOWLEDGE_BINARY_DOCUMENT")
    if len(value) > MAX_TEXT:
        raise KnowledgeError("KNOWLEDGE_EXTRACTED_TEXT_LIMIT")
    return value.replace("\r\n", "\n").replace("\r", "\n")


def paragraphs(value, **location):
    result = []
    for match in re.finditer(r"\S[\s\S]*?(?=\n\s*\n|\Z)", value):
        if match.group().strip():
            result.append(
                {
                    "text": match.group().strip(),
                    "location": {
                        **location,
                        "line_start": value[: match.start()].count("\n") + 1,
                        "line_end": value[: match.end()].count("\n") + 1,
                    },
                }
            )
    return result


def parse(raw, file_type):
    if file_type not in FORMATS:
        raise KnowledgeError("KNOWLEDGE_FORMAT_UNSUPPORTED")
    try:
        if file_type == ".pdf":
            blocks = pdf(raw)
        elif file_type == ".docx":
            blocks = docx(raw)
        else:
            value = decode(raw)
            if file_type == ".md":
                blocks, heading = paragraphs(value), ""
                for block in blocks:
                    found = re.findall(r"(?m)^#{1,6}\s+(.+)$", block["text"])
                    if found:
                        heading = found[-1][:200]
                    block["location"]["heading"] = heading
            elif file_type in {".py", ".ts", ".tsx", ".js", ".jsx"}:
                blocks = code(value, file_type)
            else:
                blocks = paragraphs(value)
        if not blocks or not any(b["text"].strip() for b in blocks):
            raise KnowledgeError("KNOWLEDGE_NO_TEXT_LAYER")
        if sum(len(b["text"]) for b in blocks) > MAX_TEXT:
            raise KnowledgeError("KNOWLEDGE_EXTRACTED_TEXT_LIMIT")
        return blocks
    except KnowledgeError:
        raise
    except Exception:
        raise KnowledgeError("KNOWLEDGE_DOCUMENT_CORRUPT") from None


def code(value, file_type):
    lines = value.splitlines(keepends=True)
    boundaries = {1: "module"}
    if file_type == ".py":
        try:
            tree = ast.parse(value)
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    boundaries[node.lineno] = node.name
        except SyntaxError:
            pass  # In-progress source remains searchable as numbered text.
    else:
        for number, line in enumerate(lines, 1):
            match = re.match(
                r"\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function|class|interface|const)\s+([\w$]+)",
                line,
            )
            if match:
                boundaries[number] = match.group(1)
    ordered = sorted(boundaries)
    return [
        {
            "text": "".join(lines[start - 1 : end - 1]).rstrip(),
            "location": {"line_start": start, "line_end": end - 1, "symbol": boundaries[start]},
        }
        for start, end in zip(ordered, ordered[1:] + [len(lines) + 1])
        if "".join(lines[start - 1 : end - 1]).strip()
    ]


def pdf(raw):
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(raw), strict=False)
    if reader.is_encrypted:
        raise KnowledgeError("KNOWLEDGE_PDF_ENCRYPTED")
    if len(reader.pages) > 400:
        raise KnowledgeError("KNOWLEDGE_PAGE_LIMIT")
    result, total = [], 0
    for page, source in enumerate(reader.pages, 1):
        value = source.extract_text() or ""
        total += len(value)
        if total > MAX_TEXT:
            raise KnowledgeError("KNOWLEDGE_EXTRACTED_TEXT_LIMIT")
        if value.strip():
            result.append({"text": value.strip(), "location": {"page": page}})
    return result


def docx(raw):
    try:
        with ZipFile(BytesIO(raw)) as archive:
            entry = archive.getinfo("word/document.xml")
            if entry.file_size > 8 * 1024 * 1024 or entry.flag_bits & 1:
                raise KnowledgeError("KNOWLEDGE_DOCX_XML_LIMIT")
            xml = archive.read(entry)
        if b"\x00" in xml or re.search(rb"<!\s*(DOCTYPE|ENTITY)", xml, re.I):
            raise KnowledgeError("KNOWLEDGE_DOCX_UNSAFE_XML")
        document = ET.fromstring(xml)
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        result, total = [], 0
        for number, paragraph in enumerate(document.iter(ns + "p"), 1):
            value = "".join(
                node.text or ""
                if node.tag == ns + "t"
                else "\n"
                if node.tag in {ns + "br", ns + "cr"}
                else "\t"
                if node.tag == ns + "tab"
                else ""
                for node in paragraph.iter()
            ).strip()
            total += len(value)
            if total > MAX_TEXT:
                raise KnowledgeError("KNOWLEDGE_EXTRACTED_TEXT_LIMIT")
            if value:
                result.append({"text": value, "location": {"paragraph": number}})
        return result
    except (BadZipFile, KeyError, ET.ParseError):
        raise KnowledgeError("KNOWLEDGE_DOCUMENT_CORRUPT") from None
