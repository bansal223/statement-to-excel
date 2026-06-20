"""Service entry point — bank statement (PDF/image) bytes -> Excel (.xlsx) bytes.

Framework-agnostic: no FastAPI, no UI, no global env. Inject your OpenAI-compatible
client (e.g. a TrueFoundry gateway client) and a model name. Drop this behind your own
endpoint/worker.

    from openai import OpenAI
    from app.service import statement_to_xlsx

    client = OpenAI(api_key=settings.API_KEY, base_url=str(settings.API_BASE_ENDPOINT))
    xlsx_bytes = statement_to_xlsx(pdf_bytes, "statement.pdf",
                                   client=client, model=settings.CHAT_MODEL)

Deploy dependency: Poppler must be installed in the image (`poppler-utils`) — used for
both digital-PDF text extraction (pdftotext) and scan rendering (pdf2image).
"""
from __future__ import annotations

from . import extract_openai
from .excel_writer import build_xlsx
from .vision import PdfPasswordError  # re-export so callers can catch it

__all__ = ["statement_to_xlsx", "PdfPasswordError"]


def statement_to_xlsx(content: bytes, filename: str, *, client, model: str,
                      password: str | None = None, max_tokens: int = 16000) -> bytes:
    """Convert one statement to a BUSY-ready .xlsx. Raises PdfPasswordError if the PDF
    is encrypted and `password` is missing/wrong."""
    stmt = extract_openai.extract(
        content, filename, password, client=client, model=model, max_tokens=max_tokens,
    )
    return build_xlsx(stmt)
