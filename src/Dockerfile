FROM python:3.12-slim

# System prep
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install deps first (cache-friendly)
COPY requirements-prod.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy app
COPY token.json /app/token.json
COPY . /app

# Cloud Run provides PORT
ENV PORT=8080

# Threaded worker; no eventlet
CMD exec gunicorn -k gthread --threads 8 -w 1 -b 0.0.0.0:${PORT} app:app