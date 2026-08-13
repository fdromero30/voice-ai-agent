# ============================================================================
# Voice AI Agent - Dockerfile (Multi-stage Build)
# ============================================================================
# Stage 1: Build del frontend con Node.js + Vite
# Stage 2: Imagen de producción con Python + FastAPI
# ============================================================================

# ---------------------------------------------------------------------------
# STAGE 1 - Frontend Build (Node.js + Vite)
# ---------------------------------------------------------------------------
FROM node:20-alpine AS frontend-builder

WORKDIR /app

# Copiar package.json y package-lock (aproveja caché de npm)
COPY frontend/package*.json ./

# Instalar dependencias de Node.js
RUN npm ci

# Copiar el código fuente del frontend
COPY frontend/ .

# Build de producción (minificado + ofuscado)
RUN npm run build

# ---------------------------------------------------------------------------
# STAGE 2 - Backend (Python + FastAPI)
# ---------------------------------------------------------------------------
FROM python:3.11-slim

# Evitar que Python genere archivos .pyc y buffers de salida
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Directorio de trabajo dentro del contenedor
WORKDIR /app

# Copiar requirements primero (aprovecha caché de Docker)
COPY backend/requirements.txt ./backend/requirements.txt

# Instalar dependencias de sistema necesarias para edge-tts/redes
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias de Python
RUN pip install --no-cache-dir -r backend/requirements.txt

# Copiar el código de la aplicación
COPY backend/ ./backend/

# Copiar el frontend ya buildado (dist/) desde el stage anterior
COPY --from=frontend-builder /app/dist ./frontend/dist

# Exponer el puerto (Render asigna uno dinámico via PORT)
EXPOSE 8000

# Comando de arranque: main.py lee la variable PORT
# (default 8000) y ejecuta uvicorn automáticamente
CMD ["python", "backend/main.py"]
