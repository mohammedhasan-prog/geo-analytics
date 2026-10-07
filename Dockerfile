# Build Python dependencies separately so compiler/build tools never enter the
# application image. Pyogrio PyPI wheels include GDAL.
FROM python:3.12-slim AS builder

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"
RUN python -m venv "${VIRTUAL_ENV}"

WORKDIR /build
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim AS runtime

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_PATH=/app/data/geospatial.sqlite3 \
    UPLOAD_DIRECTORY=/app/data/uploads

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app app

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app app ./app
COPY --chown=app:app frontend ./frontend
RUN mkdir -p /app/data/uploads && chown -R app:app /app/data

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
