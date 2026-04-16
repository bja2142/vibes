FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    BROWSER_PUPPET_ARTIFACT_DIR=/data/artifacts

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libcairo2 \
    libcairo-gobject2 \
    curl \
    git \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgdk-pixbuf-2.0-0 \
    libgbm1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libx11-6 \
    libxcb1 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    xdg-utils \
    xauth \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md /app/
COPY src /app/src
COPY tests /app/tests
COPY docker /app/docker

RUN pip install --upgrade pip && \
    pip install -e .[dev] && \
    python -m playwright install chromium firefox

RUN mkdir -p /data/artifacts
RUN chmod +x /app/docker/start-browser-puppet.sh

VOLUME ["/data/artifacts"]

EXPOSE 8000

CMD ["/app/docker/start-browser-puppet.sh", "browser-puppet", "--transport", "both", "--host", "0.0.0.0", "--port", "8000"]
