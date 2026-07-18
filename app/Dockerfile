FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/video-editing-app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY video_app/ ./video_app/
COPY static/ ./static/

EXPOSE 8787

CMD ["uvicorn", "video_app.main:app", "--host", "0.0.0.0", "--port", "8787"]
