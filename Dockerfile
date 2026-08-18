# One image, one URL: built React app + FastAPI. Session cookies stay first-party.
FROM node:20-alpine AS frontend

WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ENV VITE_API_BASE=/api
RUN npm run build

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/scripts ./scripts
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./alembic.ini
COPY data/shelf_life.json data/categories.json data/recipes.json ./data/
COPY --from=frontend /web/dist ./static

ENV SHELF_LIFE_PATH=/app/data/shelf_life.json \
    CATEGORIES_PATH=/app/data/categories.json \
    RECIPES_PATH=/app/data/recipes.json \
    UPLOAD_DIR=/tmp/shelfit-uploads \
    STATIC_DIR=/app/static \
    ENABLE_DEMO_LOGIN=false \
    COOKIE_SECURE=true

EXPOSE 8000

CMD ["sh", "-c", "python -m scripts.migrate && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
