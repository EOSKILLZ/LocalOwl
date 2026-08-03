FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Expected layout when running with docker compose (see docker-compose.yml):
#   /app/data/processed_prs.json   — review state (persisted via volume)
#   /app/data/localowl.log         — file logs (persisted via volume)
ENV STATE_FILE=/app/data/processed_prs.json \
    LOG_FILE=/app/data/localowl.log

RUN mkdir -p /app/data

CMD ["python", "main.py"]
