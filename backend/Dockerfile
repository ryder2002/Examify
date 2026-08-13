FROM python:3.11.15-slim

ARG APP_REVISION=unknown
LABEL org.opencontainers.image.revision="${APP_REVISION}"

# Poppler renders scanned PDF pages; Tesseract provides bounded local OCR.
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    libreoffice-writer \
    ffmpeg \
    libgl1 \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-osd \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements-base.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --system --gid 10001 examify \
    && useradd --system --uid 10001 --gid examify --home-dir /app examify \
    && mkdir -p /dictionary-data /scratch /tmp/smart-exam \
    && chown -R examify:examify /app /dictionary-data /scratch /tmp/smart-exam

COPY --chown=examify:examify . .
# Keep the extraction runtime contract explicit.  If a release context omits
# either module (for example a stale/partial checkout), the image build must
# fail instead of starting a worker that accepts jobs and fails at runtime.
COPY --chown=examify:examify audio_processing.py toeic_audio_cutter.py ./
RUN python -c 'import audio_processing, toeic_audio_cutter, pytesseract; print(pytesseract.get_tesseract_version())'

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    TESSERACT_LANG=eng

USER examify

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
