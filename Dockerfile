# Pinned production image: Python 3.13 slim + Gunicorn serving Django.
# Installs production dependencies only (no pytest/playwright); static
# assets are collected at build and served by WhiteNoise from Gunicorn, so
# runtime needs only the LAN host (Caddy -> app).
FROM python:3.13.7-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.prod.txt ./
RUN pip install --no-cache-dir -r requirements.prod.txt

COPY manage.py ./
COPY config ./config
COPY apps ./apps
COPY templates ./templates
COPY static ./static

# Synthetic collectstatic with a throwaway secret; real secrets come from env.
RUN DJANGO_SECRET_KEY=build-only-insecure DJANGO_DEBUG=0 \
    python manage.py collectstatic --noinput --clear

# Non-root runtime user owns nothing writable except via mounted volumes.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app/data /app/staticfiles
USER appuser

ENV DJANGO_DEBUG=0 DJANGO_DB_PATH=/app/data/db.sqlite3
EXPOSE 8000
# One local-disk SQLite owner: single gunicorn worker tree on one host.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", \
     "--workers", "3", "--threads", "2", "--timeout", "60", \
     "--access-logfile", "-", "--error-logfile", "-"]
