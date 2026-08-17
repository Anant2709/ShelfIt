FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Core runtime only. Build with --build-arg INSTALL_ML=true to add the local
# inference stack; the classifier degrades gracefully without it.
ARG INSTALL_ML=false
COPY requirements.txt requirements-ml.txt ./
RUN pip install --no-cache-dir -r requirements.txt && \
    if [ "$INSTALL_ML" = "true" ]; then \
        pip install --no-cache-dir -r requirements-ml.txt; \
    fi

COPY app ./app
COPY scripts ./scripts
# The migrations are part of the runtime, not just developer tooling: without them
# the container has no way to create or update its schema now that the app no
# longer builds one on startup.
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini

EXPOSE 8000

# Migrations run before the server, in the container, as a separate step from
# serving. On a fresh volume this creates the schema; on an existing one it applies
# only what is outstanding and leaves the data alone.
CMD ["sh", "-c", "python -m scripts.migrate && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
