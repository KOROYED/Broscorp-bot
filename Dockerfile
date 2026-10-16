# --- build stage: install dependencies with uv from the lockfile ---
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.20 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0
WORKDIR /app
COPY pyproject.toml uv.lock ./
# --locked: fail if uv.lock is out of date, so the image matches what was tested.
RUN uv sync --locked --no-dev --no-install-project

# --- runtime stage: no uv, no build cache, just the venv and the code ---
FROM python:3.12-slim
# Tesseract OCR with Ukrainian: some archive PDFs are scans without a text layer.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-ukr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 1000 app
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY bot ./bot
COPY data ./data
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
USER app
CMD ["python", "-m", "bot.main"]
