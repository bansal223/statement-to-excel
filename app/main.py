"""FastAPI app: upload scanned statements -> download an Excel mirroring the source.

Extraction uses Claude vision via the locally-authenticated `claude` CLI.

Flow: /preview extracts once (the slow Claude step) and returns the parsed data.
The browser holds that data and posts it back to /convert to build the .xlsx -- so
downloading is instant and never re-runs extraction.

Endpoints
  GET  /         -> drag-drop upload page
  POST /preview  -> parsed JSON (header + columns + rows) for on-screen review
  POST /convert  -> build .xlsx from already-parsed JSON (no re-extraction)
"""
from __future__ import annotations

import os
import traceback
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from .config import StatementLLMSettings, make_client
from .excel_writer import build_xlsx, _reconcile
from .vision import PdfPasswordError, Statement, _infer_roles

# Engine selection (priority): OpenAI-compatible gateway > Anthropic API > Claude CLI.
# Force one with STATEMENT_ENGINE=openai | anthropic | cli.
_engine = os.environ.get("STATEMENT_ENGINE")
_settings = StatementLLMSettings()  # reads OPENAI_* from .env / env

if _engine == "openai" or (_engine is None and _settings.API_KEY):
    from . import extract_openai
    _client = make_client(_settings)

    def extract(content: bytes, filename: str, password: str | None = None):
        return extract_openai.extract(
            content, filename, password,
            client=_client, model=_settings.CHAT_MODEL, max_tokens=_settings.MAX_TOKENS,
        )

    ENGINE_NAME = f"openai-gateway · {_settings.CHAT_MODEL}"
elif _engine == "anthropic" or (_engine is None and os.environ.get("ANTHROPIC_API_KEY")):
    from .vision_api import extract  # requires `anthropic` + ANTHROPIC_API_KEY
    ENGINE_NAME = "anthropic-api"
else:
    from .vision import extract  # local `claude` CLI
    ENGINE_NAME = "claude-cli"

print(f"[statement-to-excel] extraction engine: {ENGINE_NAME}")

app = FastAPI(title="Statement -> Excel")

_STATIC = Path(__file__).parent / "static"
_ALLOWED = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def _check(file: UploadFile) -> None:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED:
        raise HTTPException(400, f"Unsupported file type '{suffix}'. "
                                 f"Allowed: {', '.join(sorted(_ALLOWED))}")


def _password(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


@app.get("/engine")
def engine() -> JSONResponse:
    """Which extraction engine is active — to confirm OpenAI gateway vs Claude CLI."""
    return JSONResponse({"engine": ENGINE_NAME})


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    # no-store so browsers never serve a stale page after the app is updated.
    return HTMLResponse(
        (_STATIC / "index.html").read_text(),
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.post("/preview")
async def preview(
    file: UploadFile = File(...),
    password: str | None = Form(None),
) -> JSONResponse:
    _check(file)
    try:
        stmt = extract(await file.read(), file.filename or "upload", _password(password))
    except PdfPasswordError as e:
        # 422 + flag tells the UI to show/highlight the password box.
        return JSONResponse(status_code=422,
                            content={"password_required": True, "detail": str(e)})
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, f"Could not process file: {e}")
    flags = _reconcile(stmt)
    return JSONResponse({
        "header": stmt.header,
        "columns": stmt.columns,
        "rows": [{"cells": r, "ok": ok} for r, ok in zip(stmt.rows, flags)],
        "footer": stmt.footer,
        "row_count": len(stmt.rows),
        "needs_review": sum(1 for ok in flags if not ok),
        "page_errors": stmt.page_errors,
        "filename": file.filename or "statement",
    })


class ConvertPayload(BaseModel):
    header: list[tuple[str, str]] = []
    columns: list[str] = []
    rows: list[dict] = []        # each row is {column: cell}
    footer: str = ""
    filename: str = "statement"


@app.post("/convert")
async def convert(payload: ConvertPayload):
    """Build the workbook from data already parsed by /preview -- no Claude call."""
    try:
        stmt = Statement(
            header=[(str(k), str(v)) for k, v in payload.header],
            columns=payload.columns,
            rows=payload.rows,
            footer=payload.footer,
            roles=_infer_roles(payload.columns),
        )
        xlsx = build_xlsx(stmt)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, f"Could not build Excel: {e}")
    out_name = (Path(payload.filename).stem or "statement") + ".xlsx"
    return StreamingResponse(
        BytesIO(xlsx),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{out_name}"'},
    )
