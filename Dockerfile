FROM python:3.11-slim

# poppler-utils provides pdftotext (digital PDFs) + pdftoppm (pdf2image, scans)
RUN apt-get update \
 && apt-get install -y --no-install-recommends poppler-utils \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8077
# Config (OPENAI_API_KEY, OPENAI_API_BASE_ENDPOINT, OPENAI_CHAT_MODEL, ...) is
# provided at runtime via env vars / secrets — never baked into the image.
# Bind to $PORT when the host sets one (Render, Cloud Run, ...); default 8077.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8077}"]
