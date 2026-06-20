# Integrating statement → Excel into your service

Converts a bank-statement **PDF/image** into a **BUSY-ready `.xlsx`** using your
existing **TrueFoundry OpenAI-compatible gateway**. Framework-agnostic: one function,
bytes in → bytes out.

## 1. Files to copy into your service

Copy this package (the provider-agnostic core + the OpenAI engine):

```
app/
  vision.py          # PDF text extraction, image rendering, prompts, merge, concurrency
  excel_writer.py    # Statement -> BUSY-ready .xlsx (DD/MM/YY text dates, numeric amounts)
  extract_openai.py  # OpenAI-compatible model calls (gateway)
  service.py         # statement_to_xlsx(...) entry point
  config.py          # StatementLLMSettings + make_client + convert()
```

You can drop `vision_api.py`, `main.py`, and `static/` (those are the standalone web app).

## 2. Dependencies

```bash
pip install -r requirements-service.txt
apt-get install -y poppler-utils        # REQUIRED: pdftotext + pdf2image
```

## 3. Configuration (reuses your existing `OPENAI_` env)

Your current `.env` works as-is. Just point the model at one this account can use:

```dotenv
OPENAI_API_KEY=<token>          # keep in .env / secrets, never commit
OPENAI_API_BASE_ENDPOINT=https://truefoundry.innovaccer.com/api/llm/api/inference/openai/
OPENAI_USE_GATEWAY=True
OPENAI_CHAT_MODEL=openai/gpt-5.1   # benchmarked best; gpt-5 is NOT authorized for this account
```

> **Model choice (benchmarked on a real scanned statement, balance-reconciled):**
> `openai/gpt-5.1` and `internal-bedrock/sonnet-46` extracted every row with 0 digit
> errors; `openai/gpt-4o` missed a row. Use **`openai/gpt-5.1`** (fastest + complete);
> `internal-bedrock/sonnet-46` (Claude) is an equally accurate fallback.

Optional (BUSY date format — defaults to `DD/MM/YY`):
```dotenv
STATEMENT_DATE_FMT=%d/%m/%y       # use %d/%m/%Y for a 4-digit year
```

## 4. Use it

### Simplest — let it read settings/env

```python
from app.config import convert

xlsx_bytes = convert(pdf_bytes, "statement.pdf", password=None)  # -> .xlsx bytes
```

### Or reuse a client you already built

```python
from app.service import statement_to_xlsx, PdfPasswordError

xlsx_bytes = statement_to_xlsx(
    pdf_bytes, "statement.pdf",
    client=my_openai_client,        # your existing gateway client
    model="openai/gpt-4o",
    password=None,
)
```

### FastAPI endpoint

```python
from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import StreamingResponse
from io import BytesIO
from app.config import convert
from app.service import PdfPasswordError

app = FastAPI()

@app.post("/statements/convert")
async def convert_statement(file: UploadFile = File(...),
                            password: str | None = Form(None)):
    try:
        xlsx = convert(await file.read(), file.filename or "statement.pdf",
                       password=(password or None))
    except PdfPasswordError as e:
        raise HTTPException(422, str(e))   # signal the UI to ask for a password
    name = (file.filename or "statement").rsplit(".", 1)[0] + ".xlsx"
    return StreamingResponse(
        BytesIO(xlsx),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
```

### Background worker (Celery/RQ/etc.)

```python
from app.config import convert

def process_statement(file_bytes: bytes, filename: str, password: str | None = None) -> bytes:
    return convert(file_bytes, filename, password=password)   # store/return the .xlsx
```

## 5. Notes & limits

- **Digital PDFs** (most net-banking downloads) use the embedded text layer — exact and
  fast. **Scans/photos** fall back to vision OCR, which needs a **vision-capable** model
  (`openai/gpt-4o` qualifies).
- A **multi-page** statement makes one model call per page, run concurrently
  (cap = `MAX_CONCURRENCY` in `vision.py`). Expect a few seconds to a couple of minutes.
- **Password-protected PDFs**: pass `password=...`; a missing/wrong one raises
  `PdfPasswordError`.
- **Always glance at amber-highlighted rows** in the output — those are rows whose
  running balance didn't reconcile (possible model misread). Validate any file with
  `python tools/busy_check.py out.xlsx`.
- Per-row order is preserved as source order; the date column is written as `DD/MM/YY`
  text and amounts as numbers, matching what BUSY imports.
