FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-chi-sim \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --only-binary :all: -r requirements.txt

COPY backend/ backend/
COPY frontend/dist/ frontend/dist/

ENV UIDESIGN_ENABLE_OCR=true

CMD python3 -m uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}
