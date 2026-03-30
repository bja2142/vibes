FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_LOG_VERBOSITY=normal \
    APP_DB_PATH=/var/lib/math-worksheet-generator/db/app.sqlite3 \
    APP_ARTIFACT_ROOT=/var/lib/math-worksheet-generator/artifacts

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-dejavu-core \
        libcairo2 \
        libffi-dev \
        shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /var/lib/math-worksheet-generator/db /var/lib/math-worksheet-generator/artifacts

EXPOSE 9595

CMD ["python", "scripts/serve_site.py", "--port", "9595"]
