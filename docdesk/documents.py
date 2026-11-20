"""Turn uploaded PDF, DOCX, and plain text into small searchable excerpts."""

from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
import re
from zipfile import BadZipFile, ZipFile

from docx import Document
from docx.table import Table
from lxml.etree import XMLSyntaxError
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from .settings import MAX_DOCUMENT_CHUNKS

SUPPORTED = {".pdf", ".docx", ".txt", ".md"}
MAX_PAGES = 150
MAX_TEXT_CHARS = 240_000
CHUNK_SIZE = 800
CHUNK_OVERLAP = 120


class DocumentError(ValueError):
    """An upload cannot be read or falls outside the supported limits."""


@dataclass(frozen=True)
class Chunk:
    text: str
    location: str
    number: int


def clean_filename(filename: str) -> str:
    # Filenames are labels only. Uploaded bytes are never saved at this path.
    basename = PurePosixPath(filename.replace("\\", "/")).name
    basename = re.sub(r"[\x00-\x1f\x7f]", "", basename).strip()
    if not basename or len(basename) > 180:
        raise DocumentError("Use a filename between 1 and 180 characters.")
    if PurePosixPath(basename).suffix.lower() not in SUPPORTED:
        raise DocumentError("Choose a PDF, DOCX, TXT, or Markdown file.")
    return basename


def extract_sections(filename: str, data: bytes):
    """Yield the original text in sections with labels a reader can find."""
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise DocumentError("This file does not have a valid PDF header.")
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise DocumentError("Unlock the PDF before uploading it.")
        if len(reader.pages) > MAX_PAGES:
            raise DocumentError(f"Split the PDF into files of at most {MAX_PAGES} pages.")
        for number, page in enumerate(reader.pages, start=1):
            yield f"Page {number}", page.extract_text() or ""
        return

    if suffix == ".docx":
        # A DOCX is a ZIP archive. Check expanded sizes before parsing its XML.
        with ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            names = {entry.filename for entry in entries}
            if (
                len(entries) > 500 or len(names) != len(entries)
                or sum(entry.file_size for entry in entries) > 32 * 1024 * 1024
                or "word/document.xml" not in names
            ):
                raise DocumentError("This DOCX is invalid or too large when expanded.")
        doc = Document(BytesIO(data))
        blocks = (
            "\n".join(" | ".join(cell.text for cell in row.cells) for row in block.rows)
            if isinstance(block, Table) else block.text
            for block in doc.iter_inner_content()
        )
        yield "Document body", "\n\n".join(blocks)
        return

    raw = data.decode("utf-8-sig")
    if "\x00" in raw:
        raise DocumentError("TXT and Markdown files must contain UTF-8 text.")
    yield "Document body", raw


def make_chunks(filename: str, data: bytes) -> list[Chunk]:
    """Split text near spaces. Overlap keeps nearby sentences together."""
    chunks = []
    total_chars = 0
    try:
        for location, raw in extract_sections(filename, data):
            text = " ".join(raw.split())
            total_chars += len(text)
            if total_chars > MAX_TEXT_CHARS:
                raise DocumentError("The document has too much text. Split it and upload each part.")
            start = 0
            while start < len(text):
                end = min(start + CHUNK_SIZE, len(text))
                if end < len(text):
                    # Prefer a word boundary, provided it leaves a useful excerpt.
                    boundary = text.rfind(" ", start + CHUNK_SIZE // 2, end + 1)
                    if boundary != -1:
                        end = boundary
                chunks.append(Chunk(text[start:end].strip(), location, len(chunks) + 1))
                if len(chunks) > MAX_DOCUMENT_CHUNKS:
                    raise DocumentError("This document creates too many excerpts. Split it into smaller files.")
                if end == len(text):
                    break
                next_start = end - CHUNK_OVERLAP
                # Start on a whole word where possible without losing coverage.
                next_space = text.find(" ", next_start, end)
                start = next_space + 1 if next_space != -1 else next_start
    except UnicodeDecodeError as exc:
        raise DocumentError("Save text files using UTF-8 encoding.") from exc
    except (PyPdfError, BadZipFile, XMLSyntaxError, KeyError, OSError, RuntimeError,
            ValueError, NotImplementedError, IndexError, TypeError, RecursionError) as exc:
        if isinstance(exc, DocumentError):
            raise
        raise DocumentError("The file could not be read. Try exporting a fresh copy.") from exc

    if not chunks:
        raise DocumentError("No readable text found. For a scanned PDF, run OCR and upload the searchable copy.")
    return chunks
