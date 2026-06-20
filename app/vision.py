"""Vision extraction: scanned statement (PDF/image) -> structured Statement.

Tesseract can't read these CamScanner photos reliably, so we hand each page image
to Claude via the locally-authenticated `claude` CLI (the same login Claude Code
uses -- no separate API key). Claude returns the header fields, the table's column
names, and every row as JSON, which we mirror faithfully into Excel.

Each page is extracted in its OWN Claude call, concurrently, then merged in page
order. This is what makes long statements (10-15+ pages) reliable: no single call
has to hold the whole document in context, pages run in parallel, and a failure on
one page doesn't lose the rest.
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from pdf2image import convert_from_bytes
from PIL import Image

RENDER_DPI = 200          # enough detail for Claude; keeps images light
PAGE_TIMEOUT = 240        # seconds per single-page Claude call
MAX_CONCURRENCY = 4       # parallel claude CLI calls
DIGITAL_MIN_CHARS = 200   # >= this much real text => treat the PDF as digital (not a scan)


class PdfPasswordError(Exception):
    """Raised when a PDF is encrypted and no/incorrect password was supplied."""


@dataclass
class Statement:
    header: list[tuple[str, str]] = field(default_factory=list)   # [(label, value), ...]
    columns: list[str] = field(default_factory=list)              # table headers, in order
    rows: list[dict] = field(default_factory=list)                # each keyed by column name
    footer: str = ""
    # column name -> role, for reconciliation/formatting ("debit"/"credit"/"balance")
    roles: dict[str, str] = field(default_factory=dict)
    page_errors: list[str] = field(default_factory=list)          # per-page failures, if any


_PROMPT = """\
Read the scanned bank-statement page image at: {path}

This is ONE page of a statement. Extract its data faithfully and return ONLY a single
JSON object (no markdown, no prose) with this exact shape:

{{
  "header": [["Label", "Value"], ...],   // key:value fields from the top account-summary
                                          // block, in order, labels exactly as printed.
                                          // [] if this page has no such block.
  "columns": ["...", ...],               // the transaction table's column headers on this
                                          // page, left-to-right, exactly as printed. [] if
                                          // this page shows no table header row.
  "rows": [ {{ "<column>": "<cell>", ... }}, ... ],  // EVERY transaction row on this page,
                                          // keys matching the column headers; null for empty
                                          // cells; keep amounts exactly as printed including
                                          // commas and any "DR"/"CR" suffix.
  "footer": "..."                        // the legal disclaimer line if present (e.g.
                                          // "This is a computer generated statement..."),
                                          // else "". Do NOT put page numbers here.
}}

Rules:
- Transcribe digits with extreme care; these are financial figures.
- Include every row: interest, transfers, rate-change notes, opening/closing balance.
- Do not invent columns or rows. Do not summarise. Output JSON only.
"""

# Per-page prompt for the digital-PDF text path (shared by both engines). The page
# text is appended after this. Mirrors the image prompt's JSON shape.
_PAGE_TEXT_PROMPT = """\
Below is the text of ONE page of a bank statement, extracted from a digital PDF.
Extract its data faithfully and return ONLY a single JSON object (no markdown, no
prose) with this exact shape:

{
  "header": [["Label", "Value"], ...],   // account-summary key:value fields, in order;
                                          // [] if this page has none
  "columns": ["...", ...],               // the transaction table's column headers on this
                                          // page, left-to-right; [] if no header row here
  "rows": [ { "<column>": "<cell>", ... }, ... ],  // EVERY transaction row on this page;
                                          // keys match the columns; null for empty cells;
                                          // amounts exactly as printed incl commas and DR/CR
  "footer": "..."                        // legal disclaimer line if present, else ""
}

Rules:
- Include EVERY transaction row on the page. Transcribe digits with extreme care.
- If this page shows the same transactions in MORE THAN ONE table layout, use only the
  most complete layout (prefer the one with a running Balance column) — do not duplicate.
- Do not invent or summarise. Output JSON only.

PAGE TEXT:
"""


def _pdf_text_pages(content: bytes, filename: str,
                    password: str | None) -> list[str] | None:
    """Per-page embedded text if this is a digital PDF, else None (scan/image)."""
    if not filename.lower().endswith(".pdf"):
        return None
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(content)
        f.flush()
        cmd = ["pdftotext", "-layout"]
        if password:
            cmd += ["-upw", password, "-opw", password]
        cmd += [f.name, "-"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except Exception:
            return None
    if proc.returncode != 0:
        return None  # encrypted/needs-password/etc -> let the image path handle it
    if len(re.sub(r"\s", "", proc.stdout)) < DIGITAL_MIN_CHARS:
        return None  # no meaningful text layer -> it's a scan, use vision OCR
    # pdftotext separates pages with form feed (\f); keep only non-empty pages.
    return [p for p in proc.stdout.split("\f") if re.sub(r"\s", "", p)]


def _extract_text_page(text: str) -> dict:
    """Structure one page of extracted text via the CLI; raises on failure."""
    return _extract_json(_call_claude(_PAGE_TEXT_PROMPT + text))


def _render_pages(content: bytes, filename: str, out_dir: Path,
                  password: str | None = None) -> list[Path]:
    if filename.lower().endswith(".pdf"):
        try:
            images = convert_from_bytes(
                content, dpi=RENDER_DPI, userpw=password, ownerpw=password,
            )
        except Exception as e:
            # Poppler fails the same way for "encrypted, no password" and
            # "wrong password" -- detect the /Encrypt marker to message clearly.
            if b"/Encrypt" in content:
                raise PdfPasswordError(
                    "This PDF is password-protected. Enter the correct password."
                    if password else "This PDF is password-protected. A password is required."
                ) from e
            raise
    else:
        images = [Image.open(BytesIO(content))]
    paths: list[Path] = []
    for i, img in enumerate(images):
        p = out_dir / f"page{i}.png"
        img.convert("RGB").save(p)
        paths.append(p)
    return paths


def _call_claude(prompt: str) -> str:
    proc = subprocess.run(
        ["claude", "-p", prompt, "--allowedTools", "Read"],
        capture_output=True, text=True, timeout=PAGE_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "claude CLI failed")
    return proc.stdout


def _extract_json(text: str) -> dict:
    """Pull the JSON object out of the model's reply, tolerating stray prose/fences."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    blob = fenced.group(1) if fenced else None
    if blob is None:
        start, depth = text.find("{"), 0
        if start == -1:
            raise ValueError(f"No JSON object in reply: {text[:200]!r}")
        for i in range(start, len(text)):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            if depth == 0:
                blob = text[start:i + 1]
                break
    return json.loads(blob)


def _extract_page(path: Path) -> dict:
    """Run one page through Claude; raises on failure so the caller can record it."""
    return _extract_json(_call_claude(_PROMPT.format(path=path)))


def _infer_roles(columns: list[str]) -> dict[str, str]:
    roles: dict[str, str] = {}
    for c in columns:
        lc = c.lower()
        if "debit" in lc or "withdraw" in lc:
            roles[c] = "debit"
        elif "credit" in lc or "deposit" in lc:
            roles[c] = "credit"
        elif "balance" in lc:
            roles[c] = "balance"
    return roles


def _merge(page_results: list[dict | None]) -> Statement:
    """Combine per-page results in page order into one Statement."""
    header: list[tuple[str, str]] = []
    seen_labels: set[str] = set()
    columns: list[str] = []
    rows: list[dict] = []
    footer = ""

    for data in page_results:
        if not data:
            continue
        # header: first occurrence of each label wins (the summary block is on page 1).
        for pair in data.get("header", []):
            if isinstance(pair, (list, tuple)) and len(pair) == 2 and pair[0]:
                label = str(pair[0])
                if label not in seen_labels:
                    seen_labels.add(label)
                    header.append((label, str(pair[1])))
        # columns: union, preserving first-seen order (repeated each page in these statements).
        for c in data.get("columns", []):
            c = str(c)
            if c and c not in columns:
                columns.append(c)
        for row in data.get("rows", []):
            rows.append({k: (None if v is None else str(v)) for k, v in row.items()})
        f = str(data.get("footer", "") or "").strip()
        if f:
            footer = f  # keep the last non-empty disclaimer

    return Statement(header=header, columns=columns, rows=rows, footer=footer,
                     roles=_infer_roles(columns))


def run_concurrent(items: list, extract_one) -> Statement:
    """Run extract_one over items concurrently (page order preserved), then merge.

    A failure on one item is recorded in page_errors and doesn't sink the rest.
    Shared by both the text and image paths, and by the API engine.
    """
    results: list[dict | None] = [None] * len(items)
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(MAX_CONCURRENCY, len(items) or 1)) as pool:
        futures = {pool.submit(extract_one, it): i for i, it in enumerate(items)}
        for fut in futures:
            i = futures[fut]
            try:
                results[i] = fut.result()
            except Exception as e:
                errors.append(f"page {i + 1}: {e}")
    if all(r is None for r in results):
        raise RuntimeError("Extraction failed on every page. "
                           + ("; ".join(errors) if errors else "No data returned."))
    stmt = _merge(results)
    stmt.page_errors = errors
    return stmt


def extract(content: bytes, filename: str, password: str | None = None) -> Statement:
    # Digital PDF? Read its text layer directly — far more accurate than OCR'ing a
    # rasterized image, especially on dense tables. Each page runs concurrently.
    pages = _pdf_text_pages(content, filename, password)
    if pages is not None:
        return run_concurrent(pages, _extract_text_page)

    # Otherwise it's a scan/photo/image -> vision OCR, one page at a time.
    with tempfile.TemporaryDirectory() as td:
        paths = _render_pages(content, filename, Path(td), password=password)
        return run_concurrent(paths, _extract_page)
