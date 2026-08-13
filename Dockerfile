# ============================================================================
# Voice AI Agent - Dockerfile
# ============================================================================
# Imagen base: Python 3.11 (slim para menor tamaño)
FROM python:3.11-slim

# Evitar que Python genere archivos .pyc y buffers de salida
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Directorio de trabajo dentro del contenedor
WORKDIR /app

# Copiar requirements primero (aprovecha caché de Docker)
COPY backend/requirements.txt ./backend/requirements.txt

# Instalar dependencias del sistema necesarias para edge-tts/redes
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias de Python
RUN pip install --no-cache-dir -r backend/requirements.txt

# Copiar el código de la aplicación
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# Exponer el puerto (Render asigna uno dinámico via PORT)
EXPOSE 8000

# Comando de arranque: main.py lee la variable PORT
# (default 8000) y ejecuta uvicorn automáticamente
CMD ["python", "backend/main.py"]
