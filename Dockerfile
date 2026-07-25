# Antigravity Feishu Bot — production image
# Note: the antigravity/agy CLI and its auth state are expected on the host
# (or mounted in). This image only packages the Feishu bot runtime.

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps used by install scripts / git OTA update / process tooling
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        procps \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first for better layer caching
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Application source
COPY . .

# Runtime directories (logs / downloads / sqlite live here when not volume-mounted)
RUN mkdir -p /app/logs /app/downloads /app/scratch \
    && useradd --create-home --shell /bin/bash bot \
    && chown -R bot:bot /app

USER bot

# Bot talks outbound to Feishu via WebSocket; no inbound port required.
# Expose is optional documentation only.
EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import importlib; importlib.import_module('main'); importlib.import_module('config')" || exit 1

CMD ["python", "main.py"]
