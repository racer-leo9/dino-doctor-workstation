FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir yt-dlp

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Match the project structure expected by app.py
# app.py is at web/app.py, so parent.parent = /app
COPY skills/ /app/skills/
COPY web/ /app/web/
COPY config/ /app/config/
COPY data/ /app/data/
COPY preview/ /app/preview/

ENV PYTHONPATH="/app/skills/video-analyzer/scripts:/app"
ENV PORT=7860

EXPOSE 7860

CMD ["python", "web/app.py"]
