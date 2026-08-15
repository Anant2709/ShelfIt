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

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
