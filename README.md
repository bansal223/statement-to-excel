# Bank Statement → Excel

Web app that accepts a **scanned PDF or image** of a bank statement, reads it with
**Claude vision**, and returns an **Excel (.xlsx)** that mirrors the statement —
the account-summary header block, the transaction table (whatever columns the
statement has), and the footer.

Built for scanned SBI statements (home-loan and current-account layouts), but it is
column-driven, so it reproduces whatever fields the document actually contains.

## Why Claude vision (not Tesseract)

These are CamScanner photos — skewed, low-contrast, multi-line table cells.
Tesseract recovered only ~3 of ~20 rows and mangled digits. Claude vision reads the
full table accurately. A **running-balance reconciliation** check tints any row whose
balance doesn't add up (the rare vision misread) amber for quick review.

## Requirements

- **Poppler** (renders PDF pages to images): `brew install poppler`
- **The `claude` CLI, logged in** — extraction shells out to it, so it uses your
  existing Claude Code subscription. No separate API key needed.
  Verify with: `claude -p "say hi"`
- Python deps:
  ```bash
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
  ```

## Run

```bash
cd statement-to-excel
.venv/bin/uvicorn app.main:app --reload --port 8077
# open http://127.0.0.1:8077
```

Drag in a PDF/image → **Preview parsed data** (review the table; amber rows need a
look) → **Download Excel**.

## How it works

| Stage | File | Notes |
|-------|------|-------|
| Render | `app/vision.py` | pdf2image (200 DPI) → PNG per page. |
| Extract | `app/vision.py` | `claude -p` reads the page images, returns JSON: header fields, column names, every row, footer. |
| Excel | `app/excel_writer.py` | openpyxl; mirrors the source columns/header, Indian number format on money columns, amber highlight on unreconciled rows. |
| Web | `app/main.py` + `app/static/index.html` | `/preview` (JSON) and `/convert` (.xlsx). |

## Running in production (API key)

Locally the app drives the logged-in `claude` CLI. That won't work on a headless
server, so for deployment it uses the **Anthropic API** instead — automatically,
with no code change:

- Set **`ANTHROPIC_API_KEY`** in the environment → the app switches to the SDK
  engine ([app/vision_api.py](app/vision_api.py)) and never touches the CLI.
- Leave it unset → it uses the local CLI engine ([app/vision.py](app/vision.py)).
- Force either explicitly with `STATEMENT_ENGINE=api` or `STATEMENT_ENGINE=cli`.

Optional knobs:
- **`STATEMENT_MODEL`** — model id (default `claude-opus-4-8`). Use
  `claude-sonnet-4-6` or `claude-haiku-4-5` to cut cost on high volume.

Deploy notes: install system deps (`poppler`) in the image, `pip install -r
requirements.txt` (includes `anthropic`), run `uvicorn app.main:app --host 0.0.0.0
--port 8077` behind a reverse proxy, and raise the proxy's request timeout since a
multi-page statement can take a few minutes per extraction.

## Notes / limits

- Extraction takes ~30–60s per statement (a Claude call per upload).
- Vision is highly accurate but not infallible on bad scans — **always glance at the
  amber-flagged rows** before relying on the numbers.
- `samples/sbi_current_account_template.tsv` is the reference layout (an SBI digital
  export). If you can ever get statements as digital downloads, they need no vision
  step and are exact.
