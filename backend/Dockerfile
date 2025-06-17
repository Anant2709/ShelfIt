# ---- 1. base image ----
FROM python:3.11-slim

# ---- 2. set workdir inside container ----
WORKDIR /app

# ---- 3. copy dependency list & install ----
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- 4. copy source code ----
COPY app ./app

# ---- 5. expose port & run server ----
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]