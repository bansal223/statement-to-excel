"""Production extraction engine: scanned statement -> Statement via the Anthropic API.

This is the deployable counterpart to vision.py (which drives the local `claude`
CLI). It uses the official Anthropic Python SDK with an API key, so it runs on a
headless server with no interactive login. The parsing, merging, password handling,
and Statement shape are shared with vision.py — only the model call differs.

Activated automatically when ANTHROPIC_API_KEY is set (see app.main). Model is
configurable via STATEMENT_MODEL (default: claude-opus-4-8).
"""
from __future__ import annotations

import base64
import os
import tempfile
from pathlib import Path

import anthropic

from .vision import (
    Statement,
    _PAGE_TEXT_PROMPT,
    _extract_json,
    _pdf_text_pages,
    _render_pages,
    run_concurrent,
)

MODEL = os.environ.get("STATEMENT_MODEL", "claude-opus-4-8")
MAX_TOKENS = 16000

# Reuse the same per-page extraction instructions as the CLI engine, minus the
# "read the file at <path>" framing — here the image is attached to the message.
_PROMPT = """\
This is a scanned image of ONE page of a bank statement. Extract its data faithfully
and return ONLY a single JSON object (no markdown, no prose) with this exact shape:

{
  "header": [["Label", "Value"], ...],   // key:value fields from the top account-summary
                                          // block, in order, labels exactly as printed.
                                          // [] if this page has no such block.
  "columns": ["...", ...],               // the transaction table's column headers on this
                                          // page, left-to-right, exactly as printed. [] if
                                          // this page shows no table header row.
  "rows": [ { "<column>": "<cell>", ... }, ... ],  // EVERY transaction row on this page,
                                          // keys matching the column headers; null for empty
                                          // cells; keep amounts exactly as printed including
                                          // commas and any "DR"/"CR" suffix.
  "footer": "..."                        // the legal disclaimer line if present, else "".
                                          // Do NOT put page numbers here.
}

Rules:
- Transcribe digits with extreme care; these are financial figures.
- Include every row: interest, transfers, rate-change notes, opening/closing balance.
- Do not invent columns or rows. Do not summarise. Output JSON only.
"""

_client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment


def _extract_page(png_path: Path) -> dict:
    b64 = base64.standard_b64encode(png_path.read_bytes()).decode("utf-8")
    response = _client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                {"type": "text", "text": _PROMPT},
            ],
        }],
    )
    text = next((b.text for b in response.content if b.type == "text"), "")
    return _extract_json(text)


def _extract_text_page(text: str) -> dict:
    """Structure one page of digital-PDF text via the API; raises on failure."""
    response = _client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": _PAGE_TEXT_PROMPT + text},
        ]}],
    )
    out = next((b.text for b in response.content if b.type == "text"), "")
    return _extract_json(out)


def extract(content: bytes, filename: str, password: str | None = None) -> Statement:
    # Digital PDF -> read its text layer directly (exact, fast); else vision OCR.
    pages = _pdf_text_pages(content, filename, password)
    if pages is not None:
        return run_concurrent(pages, _extract_text_page)

    with tempfile.TemporaryDirectory() as td:
        paths = _render_pages(content, filename, Path(td), password=password)
        return run_concurrent(paths, _extract_page)
