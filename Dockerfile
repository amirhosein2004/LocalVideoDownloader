FROM denoland/deno:bin-2.9.7 AS deno
FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir pip==26.2.1
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=deno /deno /usr/local/bin/deno
COPY app ./app
RUN mkdir -p /data /downloads
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
