FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        fonts-liberation \
        imagemagick \
        jq \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/video-editing-poc
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY benchmark.json ./
COPY schemas/ ./schemas/
COPY semantic/ ./semantic/
COPY scripts/ ./scripts/

ENTRYPOINT ["python"]
CMD ["scripts/validate_artifacts.py"]
