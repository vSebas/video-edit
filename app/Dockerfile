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
RUN python -m pip install --no-cache-dir -r requirements.txt \
    nvidia-cublas-cu12 nvidia-cudnn-cu12

# CUDA runtime libraries for faster-whisper GPU inference (harmless on CPU).
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.12/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.12/site-packages/nvidia/cudnn/lib

COPY video_app/ ./video_app/
COPY static/ ./static/

EXPOSE 8787

CMD ["uvicorn", "video_app.main:app", "--host", "0.0.0.0", "--port", "8787"]
