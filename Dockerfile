FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# Install deps (use trimmed requirements for Cloud Run)
COPY req.deploy.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && pip install --no-cache-dir gunicorn eventlet

# Copy the app source
COPY src/ /app/

# Cloud Run injects $PORT
ENV PORT=8080
CMD exec gunicorn -k eventlet -w 1 -b 0.0.0.0:${PORT} app:app