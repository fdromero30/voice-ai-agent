# 🎙️ Voice AI Agent - Notas de Voz con Inteligencia Artificial

Aplicación web funcional que permite grabar una nota de voz, enviarla a un backend en **FastAPI**, y recibir automáticamente una respuesta hablada generada por **Inteligencia Artificial**.

## Instalación

### 1. Requisitos Previos

- **Python 3.10+** instalado ([descargar](https://www.python.org/downloads/))
- **Una API Key de Groq** (gratuita) — [https://console.groq.com/keys](https://console.groq.com/keys)
- Navegador moderno (Chrome, Edge, Firefox)

### 2. Configurar el Backend

```bash
# Navegar a la carpeta del backend
cd backend

# (Opcional pero recomendado) Crear entorno virtual
python3 -m venv venv

# Activar entorno virtual
# En macOS/Linux:
source venv/bin/activate
# En Windows:
# venv\Scripts\activate

# Instalar dependencias
pip install -r requirements.txt

# Configurar la API Key de Groq
# Copiar la plantilla y editarla:
cp .env.example .env
# Abrir .env y reemplazar tu_api_key_aqui por tu clave real de Groq
```

### 3. Ejecutar el Backend (sirve también el frontend)

> **IMPORTANTE:** Si usas una terminal NUEVA, debes activar el entorno virtual antes de ejecutar `uvicorn`. Sin esto dará `command not found: uvicorn`.

```bashn
# Desde la carpeta backend
cd voice-ai-agent/backend

# 1. Activar el entorno virtual (obligatorio en cada terminal nueva)
source venv/bin/activate

# 2. Ejecutar el servidor
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Verificar que funciona: abrir [http://localhost:8000](http://localhost:8000) — deberías ver el JSON de estado. También puedes probar la documentación interactiva en [http://localhost:8000/docs](http://localhost:8000/docs).

### 4. Abrir el Frontend

El frontend se sirve automáticamente desde FastAPI (necesario para que el navegador permita el acceso al micrófono — requiere `http://localhost`):

```bash
# Abrir la aplicación en el navegador
open http://localhost:8000/index.html
```

> **Importante:** Debes usar `http://localhost:8000/index.html` y **no** abrir `frontend/index.html` directamente con `file://`, ya que el navegador **bloquea el micrófono** en páginas no seguras.

---

## Cómo Usar la Aplicación

1. **Mantén presionado** el botón del micrófono 🎤 para grabar tu nota de voz.
2. **Suelta** el botón para detener la grabación y enviarla al backend.
3. El sistema mostrará el estado **"Procesando respuesta..."** mientras:
   - **Paso 1 (STT):** Groq transcribe tu audio con Whisper.
   - **Paso 2 (LLM):** Llama 3.3 genera una respuesta concisa.
   - **Paso 3 (TTS):** Edge TTS convierte la respuesta en voz.
4. La respuesta de la IA se reproducirá automáticamente como nota de voz 🎧.


### Ejemplo con cURL

```bash
curl -X POST http://localhost:8000/api/voice-chat \
  -F "voice=@nota.webm" \
  -o respuesta.mp3
```

---

## Prueba Rápida (sin frontend)

Para probar el backend directamente con un archivo de audio:

```bash
# Crear un audio de prueba con macOS (puedes usar QuickTime o Audacity)
# Luego enviarlo:
curl -X POST http://localhost:8000/api/voice-chat \
  -F "voice=@test.wav" \
  -o respuesta.mp3

# Reproducir la respuesta
afplay respuesta.mp3
```

---

##  Solución de Problemas

| Problema | Solución |
|---|---|
| `GROQ_API_KEY no encontrada` | Verifica que el archivo `.env` exista en `backend/` y contenga tu API key real. |
| Error 502 en STT | La API Key de Groq no es válida o no tiene créditos. Verifica en la consola de Groq. |
| No se accede al micrófono | Asegúrate de usar `http://localhost` (no `file://`) y conceder permisos al navegador. Si abres el HTML directamente, abre con Live Server o un servidor simple: `python3 -m http.server 8080` dentro de `frontend/`. |
| El audio no se reproduce | Los audios se envían como `audio/webm`. Asegúrate de usar Chrome o Edge. |
| Puerto 8000 ocupado | Cambia el puerto: `uvicorn main:app --port 8001` y actualiza `API_URL` en `frontend/index.html`. |
| Error de CORS | El backend ya permite todos los orígenes (`allow_origins=["*"]`). Si persiste, revisa la URL del frontend. |

---

## Fase Siguiente: Integración con WhatsApp Cloud API

El backend ya está listo para desacoplarse. Para integrarlo con WhatsApp:

1. Configurar un servidor de webhooks de Meta (o usar **Twilio** como adaptador).
2. Al recibir un mensaje de audio, descargar el media desde la API de WhatsApp.
3. Enviar el audio al endpoint `POST /api/voice-chat`.
4. Subir la respuesta MP3 a WhatsApp y enviarla como mensaje de audio.

---