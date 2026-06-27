FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium for OpenCode Playwright collector (OPENCODE_MODE=playwright).
# This adds ~400MB but is required for browser-based usage scraping. The
# browser-state volume persists the login session across container restarts.
RUN python -m playwright install --with-deps chromium

COPY app ./app

# Default data dir for SQLite; mount a volume in compose.
RUN mkdir -p /app/data /app/browser-state
VOLUME ["/app/data", "/app/browser-state"]

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
