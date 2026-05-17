FROM python:3.11-slim-trixie

ARG GHIDRA_TAG=Ghidra_12.0.4_build
ARG GHIDRA_SHA256=c3b458661d69e26e203d739c0c82d143cc8a4a29d9e571f099c2cf4bda62a120

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    JAVA_HOME=/opt/java/openjdk \
    REVERSING_MCP_WORKSPACE_ROOT=/workspace \
    REVERSING_MCP_GHIDRA_HOME=/opt/ghidra \
    PATH=/opt/java/openjdk/bin:/opt/ghidra/support:/opt/ghidra:${PATH}

WORKDIR /app

COPY pyproject.toml README.md ./

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        binutils \
        build-essential \
        ca-certificates \
        curl \
        file \
        openjdk-21-jdk-headless \
        libyara-dev \
        ssdeep \
        unzip \
    && mkdir -p /opt/java \
    && ln -s "$(dirname "$(dirname "$(readlink -f "$(command -v javac)")")")" /opt/java/openjdk \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    ghidra_url="$(python -c "import json, os, urllib.request; tag = os.environ['GHIDRA_TAG']; payload = json.load(urllib.request.urlopen(f'https://api.github.com/repos/NationalSecurityAgency/ghidra/releases/tags/{tag}')); assets = [item['browser_download_url'] for item in payload['assets'] if item['name'].endswith('.zip') and '_PUBLIC_' in item['name']]; print(assets[0] if assets else (_ for _ in ()).throw(SystemExit('No public Ghidra zip asset found for the configured tag.')))")"; \
    curl -fsSL "${ghidra_url}" -o /tmp/ghidra.zip; \
    echo "${GHIDRA_SHA256}  /tmp/ghidra.zip" | sha256sum -c -; \
    unzip -q /tmp/ghidra.zip -d /opt; \
    ghidra_dir="$(find /opt -maxdepth 1 -type d -name 'ghidra_*_PUBLIC*' | head -n 1)"; \
    ln -s "${ghidra_dir}" /opt/ghidra; \
    sed -i "s|^JAVA_HOME_OVERRIDE=.*|JAVA_HOME_OVERRIDE=${JAVA_HOME}|" /opt/ghidra/support/launch.properties; \
    rm -f /tmp/ghidra.zip

RUN python -m pip install --upgrade pip \
    && python -m pip install --no-index -f /opt/ghidra/Ghidra/Features/PyGhidra/pypkg/dist pyghidra \
    && python -c "import pathlib, tomllib; data = tomllib.loads(pathlib.Path('pyproject.toml').read_text()); requirements = list(data['project']['dependencies']); requirements.extend(data['project'].get('optional-dependencies', {}).get('dev', [])); pathlib.Path('/tmp/requirements.txt').write_text('\n'.join(requirements) + '\n')" \
    && python -m pip install -r /tmp/requirements.txt

COPY src ./src

RUN python -m pip install --no-deps .

COPY docs ./docs
COPY features ./features
COPY tests ./tests

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /workspace \
    && chown -R appuser:appuser /app /workspace

USER appuser

EXPOSE 8000

CMD ["reversing-mcp", "--transport", "both", "--host", "0.0.0.0", "--port", "8000"]
