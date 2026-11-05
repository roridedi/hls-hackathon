import io
from typing import Protocol

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from um_agentic.errors import InvalidRequest


class DocumentExtractor(Protocol):
    def extract(self, content: bytes, media_type: str) -> tuple[str, ...]: ...


class LocalDocumentExtractor:
    max_bytes = 10 * 1024 * 1024
    max_pages = 100

    def extract(self, content: bytes, media_type: str) -> tuple[str, ...]:
        if not content or len(content) > self.max_bytes:
            raise InvalidRequest("Document is empty or exceeds the 10 MB limit.")
        if media_type == "text/plain":
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise InvalidRequest("Text documents must use UTF8.") from exc
            pages = tuple(text.split("\f"))
        elif media_type == "application/pdf":
            try:
                reader = PdfReader(io.BytesIO(content), strict=True)
                if reader.is_encrypted or len(reader.pages) > self.max_pages:
                    raise InvalidRequest("Encrypted or oversized PDFs are not supported.")
                pages = tuple(page.extract_text() or "" for page in reader.pages)
            except (PdfReadError, ValueError) as exc:
                raise InvalidRequest("The PDF could not be read.") from exc
        else:
            raise InvalidRequest("Only PDF and UTF8 text documents are supported.")
        if len(pages) > self.max_pages or not any(page.strip() for page in pages):
            raise InvalidRequest("No text was extracted. Scanned documents require the cloud OCR adapter.")
        return pages
