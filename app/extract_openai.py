"""OpenAI-compatible extraction engine (TrueFoundry gateway, GPT-5, etc.).

Mirrors vision_api.py but talks to any OpenAI-compatible Chat Completions endpoint,
so it drops into a service that already has an OpenAI/gateway client. It reuses every
provider-agnostic helper from vision.py (PDF text extraction, image rendering, prompts,
merge, concurrency) — only the model call differs.

The caller INJECTS a configured `openai.OpenAI` client + model name. No global env, no
CLI, no Anthropic dependency. Digital PDFs use the text path; scans use vision input.
"""
from __future__ import annotations

import base64
import tempfile
from pathlib import Path

from .vision import (
    Statement,
    _PAGE_TEXT_PROMPT,
    _extract_json,
    _pdf_text_pages,
    _render_pages,
    run_concurrent,
)

DEFAULT_MAX_TOKENS = 16000

_IMAGE_PROMPT = """\
This is a scanned image of ONE page of a bank statement. Extract its data faithfully and
return ONLY a single JSON object (no markdown, no prose) with this shape:
{ "header": [["Label","Value"], ...], "columns": ["...", ...],
  "rows": [ {"<column>":"<cell>", ...}, ... ], "footer": "..." }
Rules: include EVERY transaction row; null for empty cells; keep amounts exactly as
printed including commas and any "DR"/"CR" suffix; transcribe digits with extreme care;
do not invent or summarise; output JSON only.
"""


def _complete(client, model: str, content, max_tokens: int) -> str:
    """One chat-completion call, tolerant of model/param variation across the gateway."""
    base = dict(model=model, messages=[{"role": "user", "content": content}])
    try:
        # Preferred shape for current OpenAI models (gpt-5 / o-series) + JSON mode.
        resp = client.chat.completions.create(
            **base, max_completion_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
    except Exception:
        # Fallback for models/gateways that reject those params.
        resp = client.chat.completions.create(**base, max_tokens=max_tokens)
    return resp.choices[0].message.content or ""


def _text_page(client, model, max_tokens, text: str) -> dict:
    content = [{"type": "text", "text": _PAGE_TEXT_PROMPT + text}]
    return _extract_json(_complete(client, model, content, max_tokens))


def _image_page(client, model, max_tokens, path: Path) -> dict:
    b64 = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    content = [
        {"type": "text", "text": _IMAGE_PROMPT},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
    ]
    return _extract_json(_complete(client, model, content, max_tokens))


def extract(content: bytes, filename: str, password: str | None = None, *,
            client, model: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> Statement:
    """Bank statement bytes -> Statement, via an OpenAI-compatible `client`."""
    pages = _pdf_text_pages(content, filename, password)
    if pages is not None:  # digital PDF -> exact text path
        return run_concurrent(pages, lambda t: _text_page(client, model, max_tokens, t))
    # scan / photo / image -> vision
    with tempfile.TemporaryDirectory() as td:
        paths = _render_pages(content, filename, Path(td), password=password)
        return run_concurrent(paths, lambda p: _image_page(client, model, max_tokens, p))
