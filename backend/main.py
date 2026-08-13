"""
Voice AI Agent - Backend API
============================
FastAPI backend that receives voice notes, transcribes them with Groq
(whisper-large-v3-turbo), generates an AI response with Groq
(llama-3.3-70b-versatile), and returns an MP3 voice response via edge-tts.

Flow:
  POST /api/voice-chat (audio/webm|wav)
    -> 1. STT: Groq Whisper -> text
    -> 2. LLM: Groq Llama -> response text
    -> 3. TTS: edge-tts -> response.mp3
    -> Return FileResponse(audio/mpeg)
"""

import json
import os
import uuid
from pathlib import Path
from typing import Any, List, Optional
from urllib.parse import quote

import qrcode
import edge_tts
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI

# ------------------------------------------------------------------
# 1. CONFIGURACIÓN INICIAL
# ------------------------------------------------------------------

# Cargar variables de entorno desde .env (GROQ_API_KEY)
load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise RuntimeError(
        "GROQ_API_KEY no encontrada. "
        "Copia .env.example a .env y agrega tu clave de Groq."
    )

# Cliente de Groq (compatible con el SDK de OpenAI)
client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1",
)

# Configuración de modelos
STT_MODEL = "whisper-large-v3-turbo"   # Speech-to-Text

# Modelos LLM en orden de preferencia con fallback automático.
# Si llama-3.3-70b-versatile alcanza el límite de tokens (429),
# se usa automáticamente el siguiente modelo de la lista.
###
LLM_MODELS = [
    "llama-3.3-70b-versatile",  # Principal: cerebro / LLM
    "llama-3.1-8b-instant",     # Fallback 1: rápido y con límites más altos
    "gemma2-9b-it",             # Fallback 2: alternativa ligera
]
LLM_MODEL = LLM_MODELS[0]  # Modelo principal (para health check)

TTS_VOICE = "es-CO-SalomeNeural"       # Voz TTS en español colombiano (Microsoft Edge)
# Alternativas: es-CO-GonzaloNeural (Colombia masc), es-MX-DaliaNeural (México)

# Directorio temporal para audios entrantes y salientes
TEMP_DIR = Path(__file__).parent / "temp"
TEMP_DIR.mkdir(exist_ok=True)

# Directorio del frontend (archivos estáticos)
# En producción, Vite build genera los archivos en frontend/dist
# En desarrollo (sin build), sirve directamente desde frontend/
FRONTEND_DIR = Path(__file__).parent.parent / "frontend" / "dist"
if not FRONTEND_DIR.exists():
    FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

# ------------------------------------------------------------------
# CATÁLOGO MOCK DE PRODUCTOS
# ------------------------------------------------------------------
# Este catálogo se inyecta al prompt del LLM para que el asistente
# SOLO responda información sobre estos productos.
# En producción, esto puede venir de una base de datos o API externa.
PRODUCT_CATALOG = [
    {
        "id": "AUR-001",
        "nombre": "Audífonos Inalámbricos Pro",
        "categoria": "Audio",
        "precio": 249000,
        "moneda": "COP",
        "stock": 12,
        "disponible": True,
        "descripcion": "Audífonos bluetooth con cancelación de ruido, 30 horas de batería y calidad de estudio.",
    },
    {
        "id": "AUR-002",
        "nombre": "Audífonos Deportivos Lite",
        "categoria": "Audio",
        "precio": 89000,
        "moneda": "COP",
        "stock": 0,
        "disponible": False,
        "descripcion": "Audífonos deportivos resistentes al sudor con gancho ajustable.",
    },
    {
        "id": "PAR-001",
        "nombre": "Parlante Bluetooth Mini",
        "categoria": "Audio",
        "precio": 129000,
        "moneda": "COP",
        "stock": 25,
        "disponible": True,
        "descripcion": "Parlante portátil con sonido 360 grados, waterproof y batería de 12 horas.",
    },
    {
        "id": "REL-001",
        "nombre": "Smartwatch Serie 5",
        "categoria": "Tecnología",
        "precio": 459000,
        "moneda": "COP",
        "stock": 7,
        "disponible": True,
        "descripcion": "Reloj inteligente con monitor de sueño, ritmo cardíaco y GPS integrado.",
    },
    {
        "id": "REL-002",
        "nombre": "Smartwatch Básico Fit",
        "categoria": "Tecnología",
        "precio": 189000,
        "moneda": "COP",
        "stock": 30,
        "disponible": True,
        "descripcion": "Pulsera inteligente económica con contador de pasos y notificaciones.",
    },
    {
        "id": "CEL-001",
        "nombre": "Cargador Rápido 65W",
        "categoria": "Accesorios",
        "precio": 99000,
        "moneda": "COP",
        "stock": 15,
        "disponible": True,
        "descripcion": "Cargador GaN de 65 vatios con doble puerto USB-C para cargar varios dispositivos.",
    },
    {
        "id": "CEL-002",
        "nombre": "Cable USB-C Trenzado 2m",
        "categoria": "Accesorios",
        "precio": 35000,
        "moneda": "COP",
        "stock": 0,
        "disponible": False,
        "descripcion": "Cable resistente trenzado de 2 metros con carga rápida de 100 vatios.",
    },
    {
        "id": "CAM-001",
        "nombre": "Cámara Web Full HD",
        "categoria": "Video",
        "precio": 159000,
        "moneda": "COP",
        "stock": 10,
        "disponible": True,
        "descripcion": "Cámara web con resolución 1080p, micrófono integrado y enfoque automático.",
    },
]


def format_catalog_for_prompt(catalog: list) -> str:
    """
    Convierte el catálogo de productos a texto legible para inyectar
    en el system prompt del LLM.
    """
    lines = ["CATÁLOGO DE PRODUCTOS DISPONIBLES:"]
    for p in catalog:
        estado = "disponible" if p["disponible"] else "agotado"
        lines.append(
            f"- {p['nombre']} (ID: {p['id']}) | Categoría: {p['categoria']} | "
            f"Precio: ${p['precio']:,} {p['moneda']} | Estado: {estado} | "
            f"Descripción: {p['descripcion']}"
        )
    return "\n".join(lines)


# Prompt del sistema para el LLM (respuestas cortas para nota de voz)
SYSTEM_PROMPT = (
    "Eres un asistente virtual de atención al cliente llamado Romerito Agent. "
    "Sigue estrictamente las siguientes reglas:\n\n"
    
    "1. PRIMER MENSAJE (si no conoces el nombre del usuario):\n"
    "- Saluda brevemente según el momento del día (buenos días, buenas tardes o buenas noches).\n"
    "- Pregunta el nombre del usuario sin dar información adicional aún.  y di en que te puedo ayudar\n\n"
    
    "2. MENSAJES SIGUIENTES (si el usuario ya se presentó):\n"
    "- PROHIBIDO volver a saludar con 'buenos días/tardes/noches'.\n"
    "- PROHIBIDO volver a preguntar el nombre.\n"
    "- Ve directamente al grano y responde la consulta del usuario.\n\n"
    
    "3. INFORMACIÓN DE PRODUCTOS (CRÍTICO):\n"
    "- PROHIBIDO volver a preguntar el nombre.\n"
    "- SOLO puedes dar información sobre los productos listados en el catálogo que se te proporciona.\n"
    "- Si el usuario pregunta por un producto que NO está en el catálogo, responde amablemente que "
    "no dispones de ese producto o servicio y sugiere preguntar por otro.\n"
    "- Siempre menciona disponibilidad: si el producto está 'disponible' o 'agotado'.\n"
    "- Indica el precio cuando el usuario lo pida o sea relevante.\n"
    "- Describe brevemente el producto usando la descripción del catálogo.\n\n"
    
    "3.1 MONEDA (CRÍTICO, SOLO PESOS COLOMBIANOS):\n"
    "- TODOS los precios del catálogo están en PESOS COLOMBIANOS (COP), NUNCA en dólares.\n"
    "- Cuando menciones un precio SIEMPRE di la palabra 'pesos'. Ejemplos: '249 mil pesos', "
    "'cuatrocientos cincuenta y nueve mil pesos', 'siete mil pesos', 'veinticinco mil pesos'.\n"
    "- PROHIBIDO decir la palabra 'dólares' en tus respuestas.\n"
    "- No uses el símbolo $ en voz alta; di la cantidad en palabras o 'mil pesos'.\n\n"
    
    "4. PEDIDOS Y CONTACTO:\n"
    "- PROHIBIDO volver a preguntar el nombre.\n"
    "- Si el usuario quiere hacer un pedido, confirma el producto y pregunta la cantidad deseada.\n"
    "- El usuario puede agregar MÁS productos en cualquier momento, incluso después de "
    "registrar la dirección y ciudad o generar el QR. Usa registrar_pedido para agregar.\n"
    "- El usuario puede RETIRAR productos en cualquier momento, incluso después de "
    "registrar la dirección y ciudad o generar el QR. Usa retirar_producto para retirar.\n"
    "- Al agregar o retirar productos, el subtotal y total se recalculan automáticamente.\n"
    "- Después de confirmar cantidad, DI al usuario: 'Para poder calcular el valor del envío, "
    "necesito que me indiques la dirección de entrega y la ciudad'.\n"
    "- Cuando el usuario indique dirección y ciudad, invoca la herramienta registrar_envio.\n"
    "- Después de registrar el envío, confirma: productos, subtotal, valor del envío, "
    "y total a pagar. Indica que se mostrará el código QR de pago.\n"
    "- Si el usuario pide hablar con un asesor o agente humano, responde que "
    "le contactaremos por WhatsApp al número de su cuenta.\n\n"
    
    "5. ENVÍO (CRÍTICO):\n"
    "- El envío a Bogotá cuesta 7000 pesos.\n"
    "- El envío a cualquier otra ciudad cuesta 25000 pesos.\n"
    "- El costo de envío se calcula automáticamente con la herramienta registrar_envio.\n\n"
    
    "6. FLUJO DE PEDIDO COMPLETO:\n"
    "1. Pedido confirmado con cantidad → preguntar dirección y ciudad.\n"
    "2. Usuario da dirección y ciudad → invocar registrar_envio.\n"
    "3. Mostrar resumen final: subtotal + envío = total.\n"
    "4. Indicar QR de pago al usuario.\n\n"
    
    "REGLAS DE FORMATO (para nota de voz):\n"
    "- PROHIBIDO volver a preguntar el nombre.\n"
    "- Máximo 2 a 3 oraciones por respuesta.\n"
    "- No uses caracteres especiales, viñetas, asteriscos, emoticonos ni ningún tipo de formato Markdown.\n"
    "- No leas IDs ni códigos internos en voz alta.\n\n"
    
    f"{format_catalog_for_prompt(PRODUCT_CATALOG)}"
)

# Instancia de la aplicación FastAPI
app = FastAPI(
    title="Voice AI Agent API",
    description="API de agente conversacional por notas de voz (STT + LLM + TTS)",
    version="1.0.0",
)

# ------------------------------------------------------------------
# Configuración CORS (permitir todos los orígenes para pruebas locales)
# ------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------
# UTILIDADES
# ------------------------------------------------------------------

def _get_audio_extension(filename: str) -> str:
    """Extrae la extensión del archivo de audio (webm, wav, etc.)."""
    suffix = Path(filename).suffix.lower()
    return suffix if suffix else ".webm"


def _cleanup(*paths: Path) -> None:
    """Elimina archivos temporales después del procesamiento."""
    for path in paths:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass  # Si no se puede borrar, no es crítico


def _llm_completion(**kwargs: Any) -> Any:
    """
    Realiza una llamada al LLM con fallback automático entre modelos.

    Intenta cada modelo en LLM_MODELS en orden. Si el modelo principal
    (llama-3.3-70b-versatile) alcanza el límite de tokens por día (error 429),
    se reintenta automáticamente con el siguiente modelo de la lista
    (llama-3.1-8b-instant, gemma2-9b-it) que tienen límites más altos.

    Devuelve la respuesta del primer modelo que tenga éxito.
    Lanza la última excepción si TODOS los modelos fallan.
    """
    last_error: Optional[Exception] = None

    for model in LLM_MODELS:
        try:
            return client.chat.completions.create(
                model=model,
                **kwargs,
            )
        except Exception as e:
            last_error = e
            # Solo continuar con el siguiente modelo si es un error de
            # límite de tasa (429) o de disponibilidad del modelo.
            error_str = str(e).lower()
            is_rate_limit = "429" in error_str or "rate limit" in error_str
            is_model_error = "model" in error_str and (
                "not found" in error_str or "does not exist" in error_str
            )
            if not (is_rate_limit or is_model_error):
                # Error no relacionado con límites: propagar inmediatamente
                raise e

    # Todos los modelos fallaron
    raise last_error if last_error else RuntimeError("No hay modelos LLM disponibles.")


# ------------------------------------------------------------------
# GENERACIÓN DE QR DE PAGO
# ------------------------------------------------------------------

def _normalize_text(text: str) -> str:
    """Normaliza texto: minúsculas y quita tildes (para búsquedas robustas)."""
    replacements = {
        'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u',
        'ü': 'u', 'ñ': 'n',
    }
    text = text.lower().strip()
    for accent, plain in replacements.items():
        text = text.replace(accent, plain)
    return text


def _find_product(product_name: str):
    """
    Busca un producto en el catálogo por nombre (coincidencia parcial).
    Normaliza tildes para que 'Audifonos' encuentre 'Audífonos'.
    Devuelve el producto si existe, None si no.
    """
    normalized_query = _normalize_text(product_name)
    for p in PRODUCT_CATALOG:
        normalized_name = _normalize_text(p["nombre"])
        if normalized_name in normalized_query or normalized_query in normalized_name:
            return p
    return None


def generate_payment_qr(product_name: str, quantity: int = 1):
    """
    Genera un QR de pago ficticio para un pedido.
    En producción, esto debería conectarse a una pasarela real
    (PSE, Nequi, Wompi, Stripe, etc.) que genere un QR de pago.

    El QR contiene un texto simulado de pago (mock) con:
      - ID de pedido
      - Producto
      - Cantidad
      - Total
    """
    # Buscar el producto en el catálogo para el precio
    product = _find_product(product_name)
    if not product:
        # Si no se encuentra, usar un nombre genérico
        product = {
            "nombre": product_name,
            "precio": 0,
            "moneda": "COP",
        }

    order_id = uuid.uuid4().hex[:8].upper()
    unit_price = product["precio"]
    total = unit_price * quantity
    currency = product.get("moneda", "COP")

    # Texto de pago simulado (en producción: link/payload real de la pasarela)
    payment_payload = (
        f"ORDER:{order_id}|PRODUCT:{product['nombre']}|"
        f"QTY:{quantity}|TOTAL:{total}|CURRENCY:{currency}"
    )

    # Generar imagen QR en memoria
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(payment_payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    # Guardar PNG temporalmente
    qr_path = TEMP_DIR / f"qr_{order_id}.png"
    img.save(str(qr_path))

    return {
        "order_id": order_id,
        "product": product["nombre"],
        "quantity": quantity,
        "unit_price": unit_price,
        "total": total,
        "currency": currency,
        "payment_payload": payment_payload,
        "qr_path": qr_path,
    }


def generate_payment_qr_multi(productos: list, total: int, currency: str = "COP"):
    """
    Genera un QR de pago ficticio para un pedido con múltiples productos.
    El QR contiene un texto simulado de pago (mock) con:
      - ID de pedido
      - Lista de productos
      - Total
    """
    order_id = uuid.uuid4().hex[:8].upper()

    # Construir el payload con todos los productos
    productos_str = "|".join(
        f"{p['cantidad']}x {p['producto']} (${p['subtotal']:,})" for p in productos
    )

    # Texto de pago simulado (en producción: link/payload real de la pasarela)
    payment_payload = (
        f"ORDER:{order_id}|PRODUCTS:{productos_str}|"
        f"TOTAL:{total}|CURRENCY:{currency}"
    )

    # Generar imagen QR en memoria
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(payment_payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    # Guardar PNG temporalmente
    qr_path = TEMP_DIR / f"qr_{order_id}.png"
    img.save(str(qr_path))

    return {
        "order_id": order_id,
        "productos": productos,
        "total": total,
        "currency": currency,
        "payment_payload": payment_payload,
        "qr_path": qr_path,
    }


# ------------------------------------------------------------------
# ENVÍO / DIRECCIÓN
# ------------------------------------------------------------------

# Tarifas de envío: Bogotá $7,000 COP, resto del país $25,000 COP
SHIPPING_COST_BOGOTA = 7000
SHIPPING_COST_OTHER = 25000


def calculate_shipping(city: str) -> int:
    """
    Calcula el costo de envío según la ciudad.
    Bogotá: $7,000 COP · Otras ciudades: $25,000 COP
    """
    city_lower = city.lower().strip()
    bogota_keywords = ["bogota", "bogotá", "bta", "santa fe de bogota", "santa fe de bogotá"]
    for keyword in bogota_keywords:
        if keyword in city_lower:
            return SHIPPING_COST_BOGOTA
    return SHIPPING_COST_OTHER


# Pedidos activos en memoria: clave = session_id (o "default" para MVP)
# Formato: { producto, cantidad, subtotal, moneda, estado }
ACTIVE_ORDERS: dict = {}


# Herramienta (function calling) que el LLM puede invocar para registrar un pedido
PAYMENT_FUNCTION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "registrar_pedido",
        "description": (
            "Registra un producto en el pedido del usuario. Llama a esta función cuando el usuario "
            "confirme que quiere comprar o pedir un producto del catálogo, "
            "indicando el producto y la cantidad. Puedes llamarla múltiples veces "
            "para agregar diferentes productos al mismo carrito. "
            "Después de agregar productos, el usuario DEBE proporcionar dirección y ciudad de envío."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "producto": {
                    "type": "string",
                    "description": "Nombre del producto que el usuario quiere comprar",
                },
                "cantidad": {
                    "type": "integer",
                    "description": "Cantidad que desea pedir (por defecto 1)",
                    "minimum": 1,
                },
            },
            "required": ["producto"],
        },
    },
}


# Herramienta para registrar la dirección/ciudad de envío y calcular su costo
SHIPPING_FUNCTION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "registrar_envio",
        "description": (
            "Registra la dirección y ciudad de envío del pedido. Llama a esta función "
            "CUANDO el usuario proporcione su dirección de entrega Y la ciudad. "
            "Esta función calcula automáticamente el costo de envío: "
            "Bogotá cuesta 7000 pesos, cualquier otra ciudad cuesta 25000 pesos. "
            "Después de llamarla, se genera el código QR de pago con el total final."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "direccion": {
                    "type": "string",
                    "description": "Dirección de entrega completa del usuario",
                },
                "ciudad": {
                    "type": "string",
                    "description": "Ciudad de entrega (ej: Bogotá, Medellín, Cali)",
                },
            },
            "required": ["direccion", "ciudad"],
        },
    },
}


# Herramienta para retirar un producto del pedido
REMOVE_PRODUCT_FUNCTION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "retirar_producto",
        "description": (
            "Retira un producto del pedido actual. Llama a esta función cuando el usuario "
            "quiera eliminar o retirar un producto que ya haya agregado al carrito, "
            "ya sea antes o después de registrar la dirección/envío. "
            "El subtotal y total se recalculan automáticamente."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "producto": {
                    "type": "string",
                    "description": "Nombre del producto a retirar del pedido",
                },
            },
            "required": ["producto"],
        },
    },
}


# ------------------------------------------------------------------
# ENDPOINT PRINCIPAL
# ------------------------------------------------------------------

@app.get("/api/health")
async def health_check():
    """Endpoint de verificación de salud del servicio."""
    return {
        "status": "ok",
        "service": "Voice AI Agent API",
        "stt_model": STT_MODEL,
        "llm_model": LLM_MODEL,
        "tts_voice": TTS_VOICE,
    }


@app.get("/api/init-greeting")
async def init_greeting():
    """
    Genera y retorna el saludo inicial de bienvenida (MP3).
    Se invoca automáticamente al cargar la página para que el bot
    salude 1-2 segundos después de que el usuario entra.
    """
    greeting_text = "Bienvenido a Romerito Agent, tu asistente de atención al cliente. ¿En qué puedo ayudarte el día de hoy?"
    greeting_path = TEMP_DIR / "greeting.mp3"

    try:
        tts = edge_tts.Communicate(text=greeting_text, voice=TTS_VOICE)
        await tts.save(str(greeting_path))
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Error generando saludo: {str(e)}",
        )

    if not greeting_path.exists():
        raise HTTPException(status_code=500, detail="No se pudo generar el saludo.")

    return FileResponse(
        path=greeting_path,
        media_type="audio/mpeg",
        filename="greeting.mp3",
    )


@app.get("/api/payment-qr/{order_id}")
async def get_payment_qr(order_id: str):
    """
    Sirve la imagen PNG del QR de pago para un pedido.
    SEGURIDAD: En producción, validar que el order_id pertenezca al usuario.
    """
    qr_file = TEMP_DIR / f"qr_{order_id}.png"
    if not qr_file.exists():
        raise HTTPException(status_code=404, detail="QR de pago no encontrado o expirado.")

    return FileResponse(
        path=qr_file,
        media_type="image/png",
        filename=f"qr_{order_id}.png",
    )


@app.post("/api/voice-chat")
async def voice_chat(
    voice: UploadFile = File(...),
    history: Optional[str] = Form(None),  # Historial JSON opcional desde el frontend
):
    """
    Endpoint principal: recibe un audio, lo transcribe, genera una
    respuesta con IA y devuelve un MP3 hablado.

    Entrada:  Archivo de audio (webm/wav) + historial opcional (JSON string)
    Salida:   audio/mpeg (MP3 generado por edge-tts)
    """
    # Identificadores únicos para los archivos temporales
    request_id = uuid.uuid4().hex
    input_ext = _get_audio_extension(voice.filename or "")
    input_path = TEMP_DIR / f"{request_id}_input{input_ext}"
    output_path = TEMP_DIR / f"{request_id}_output.mp3"

    try:
        # ----------------------------------------------------------
        # PASO 0: Guardar audio entrante en disco
        # ----------------------------------------------------------
        content = await voice.read()
        if not content:
            raise HTTPException(status_code=400, detail="El archivo de audio está vacío.")

        input_path.write_bytes(content)

        # ----------------------------------------------------------
        # PASO 1: STT - Transcripción con Groq Whisper
        # ----------------------------------------------------------
        try:
            with input_path.open("rb") as audio_file:
                transcription = client.audio.transcriptions.create(
                    model=STT_MODEL,
                    file=audio_file,
                    language="es",  # Optimizar para español
                )
            user_text = transcription.text.strip()
        except Exception as e:
            raise HTTPException(
                status_code=502,
                detail=f"Error en la transcripción (STT): {str(e)}",
            )

        if not user_text:
            raise HTTPException(
                status_code=400,
                detail="No se pudo transcribir el audio. Intenta de nuevo.",
            )

        # ----------------------------------------------------------
        # Construir mensajes con memoria de conversación
        # ----------------------------------------------------------
        # El frontend envía un historial JSON de mensajes previos
        # para que el LLM recuerde el contexto (nombre, producto, etc.)
        # Usar Any para evitar problemas de tipado con el SDK de Groq/OpenAI
        # Los mensajes son dicts: {"role": str, "content": str}
        build_messages: list = [{"role": "system", "content": SYSTEM_PROMPT}]

        if history:
            try:
                parsed_history = json.loads(history)
                if isinstance(parsed_history, list):
                    # Validar y añadir solo mensajes con roles válidos
                    for msg in parsed_history[-10:]:  # últimas 10 interacciones
                        role = msg.get("role")
                        text = msg.get("content")
                        if role in ("user", "assistant") and text:
                            build_messages.append({"role": role, "content": text})
            except json.JSONDecodeError:
                pass  # Si el historial es inválido, ignorarlo

        # Añadir el mensaje actual del usuario
        build_messages.append({"role": "user", "content": user_text})

        # ----------------------------------------------------------
        # PASO 2: LLM - Generar respuesta con Groq Llama
        # (Function Calling: registrar_pedido + registrar_envio)
        # ----------------------------------------------------------
        qr_path: Optional[Path] = None   # QR generado cuando el pedido está completo
        order_data: Optional[dict] = None  # Datos del pedido para el dashboard
        try:
            # Usar Any para evitar problemas de tipos con el SDK (Groq/OpenAI)
            # _llm_completion intenta cada modelo en LLM_MODELS con fallback
            # automático si el principal alcanza el límite de tokens (429).
            chat_response: Any = _llm_completion(
                messages=build_messages,
                tools=[PAYMENT_FUNCTION_SCHEMA, SHIPPING_FUNCTION_SCHEMA, REMOVE_PRODUCT_FUNCTION_SCHEMA],  # type: ignore[arg-type]
                temperature=0.7,
                max_tokens=150,
            )

            message: Any = chat_response.choices[0].message
            tool_calls = getattr(message, "tool_calls", None) or []

            # Procesar tool calls (puede haber pedido y/o envío)
            if len(tool_calls) > 0:
                tool_call = tool_calls[0]
                function_name = getattr(tool_call, "function", None)
                fn_name = getattr(function_name, "name", "") if function_name else ""
                fn_args = getattr(function_name, "arguments", "{}")
                try:
                    args = json.loads(fn_args)
                except json.JSONDecodeError:
                    args = {}

                if fn_name == "registrar_pedido":
                    # PASO A: registrar el pedido (sin QR todavía)
                    # Soporta múltiples productos: agrega al carrito existente
                    producto = args.get("producto", "")
                    cantidad = int(args.get("cantidad", 1))
                    product = _find_product(producto)
                    if product:
                        # Inicializar orden si no existe
                        if "default" not in ACTIVE_ORDERS:
                            ACTIVE_ORDERS["default"] = {
                                "productos": [],
                                "moneda": product.get("moneda", "COP"),
                                "direccion": "",
                                "ciudad": "",
                                "envio": None,
                                "total": None,
                                "qr_path": None,
                                "order_data": None,
                            }

                        # Agregar producto al carrito
                        ACTIVE_ORDERS["default"]["productos"].append({
                            "producto": product["nombre"],
                            "cantidad": cantidad,
                            "precio_unitario": product["precio"],
                            "subtotal": product["precio"] * cantidad,
                        })

                        # Recalcular subtotal
                        subtotal = sum(p["subtotal"] for p in ACTIVE_ORDERS["default"]["productos"])
                        moneda = ACTIVE_ORDERS["default"]["moneda"]

                        # Actualizar order_data
                        order_data = {
                            "productos": ACTIVE_ORDERS["default"]["productos"],
                            "subtotal": subtotal,
                            "moneda": moneda,
                            "direccion": "",
                            "ciudad": "",
                            "envio": None,
                            "total": None,
                        }
                        ACTIVE_ORDERS["default"]["order_data"] = order_data

                        # Responder pidiendo dirección y ciudad (sin QR aún).
                        assistant_text = (
                            f"Se agregó {cantidad} unidad(es) de {product['nombre']} al pedido. "
                            f"Subtotal: ${subtotal:,} {moneda}. "
                            f"¿Deseas agregar más productos o necesitas indicar dirección y ciudad?"
                        )
                    else:
                        assistant_text = "Lo siento, no encontré ese producto en el catálogo."

                elif fn_name == "retirar_producto":
                    # Retirar un producto del carrito
                    producto = args.get("producto", "")
                    active_order = ACTIVE_ORDERS.get("default")
                    if active_order and active_order.get("productos"):
                        productos = active_order["productos"]
                        # Buscar y retirar el producto
                        removed = False
                        for i, p in enumerate(productos):
                            if _normalize_text(p["producto"]) == _normalize_text(producto) or _normalize_text(producto) in _normalize_text(p["producto"]):
                                productos.pop(i)
                                removed = True
                                break

                        if removed:
                            # Recalcular subtotal
                            subtotal = sum(p["subtotal"] for p in productos)
                            moneda = active_order["moneda"]

                            # Actualizar order_data
                            order_data = {
                                "productos": productos,
                                "subtotal": subtotal,
                                "moneda": moneda,
                                "direccion": active_order.get("direccion", ""),
                                "ciudad": active_order.get("ciudad", ""),
                                "envio": active_order.get("envio"),
                                "total": active_order.get("total"),
                            }
                            active_order["order_data"] = order_data

                            if productos:
                                assistant_text = f"Se retiró {producto} del pedido. Subtotal actualizado: ${subtotal:,} {moneda}."
                            else:
                                assistant_text = "Se retiró el producto. El pedido está vacío."
                        else:
                            assistant_text = f"No encontré {producto} en tu pedido."
                    else:
                        assistant_text = "No hay productos en el pedido para retirar."

                elif fn_name == "registrar_envio":
                    # PASO B: registrar envío y generar QR + datos del pedido
                    direccion = args.get("direccion", "")
                    ciudad = args.get("ciudad", "")

                    # Obtener el pedido activo (debe existir del paso anterior)
                    active_order = ACTIVE_ORDERS.get("default")
                    if not active_order:
                        # Si no hay pedido, pedir primero el producto
                        assistant_text = "Primero necesito saber qué producto deseas ordenar."
                    else:
                        productos = active_order.get("productos", [])
                        if not productos:
                            assistant_text = "Primero necesito saber qué producto deseas ordenar."
                        else:
                            envio = calculate_shipping(ciudad)
                            # Calcular subtotal desde todos los productos
                            subtotal = sum(p["subtotal"] for p in productos)
                            total = subtotal + envio
                            moneda = active_order["moneda"]

                            # Generar QR con el TOTAL final (producto + envío)
                            qr_info = generate_payment_qr_multi(productos, total, moneda)
                            qr_path = qr_info["qr_path"]

                            # Datos completos del pedido para el dashboard
                            order_data = {
                                "productos": productos,
                                "subtotal": subtotal,
                                "direccion": direccion,
                                "ciudad": ciudad,
                                "envio": envio,
                                "total": total,
                                "moneda": moneda,
                                "qr_url": f"/api/payment-qr/{qr_info['order_id']}",
                            }

                            # PERSISTIR el pedido completado en ACTIVE_ORDERS
                            # para que el QR y dashboard NO se pierdan si el usuario
                            # cambia de tema (pide otro producto, descuento, etc.)
                            ACTIVE_ORDERS["default"]["order_data"] = order_data
                            ACTIVE_ORDERS["default"]["qr_path"] = qr_path

                            # Responder confirmando el pedido, envío y QR.
                            # El mensaje se construye directamente en el backend para
                            # evitar una llamada LLM adicional (ahorra tokens).
                            productos_str = ", ".join(
                                f"{p['cantidad']}x {p['producto']}" for p in productos
                            )
                            assistant_text = (
                                f"Tu pedido ({productos_str}) quedó así: subtotal ${subtotal:,}, "
                                f"envío a {ciudad} ${envio:,}, total ${total:,} {moneda}. "
                                f"Se mostrará el código QR para el pago."
                            )
                else:
                    raw_content = message.content
                    assistant_text = raw_content.strip() if raw_content else "Lo siento, no pude procesar tu solicitud."
            else:
                # Respuesta normal sin tool calls
                raw_content = message.content
                assistant_text = raw_content.strip() if raw_content else ""
                if not assistant_text:
                    raise HTTPException(
                        status_code=502,
                        detail="El modelo LLM no generó una respuesta válida.",
                    )
        except Exception as e:
            raise HTTPException(
                status_code=502,
                detail=f"Error en la generación de la respuesta (LLM): {str(e)}",
            )

        # ----------------------------------------------------------
        # PASO 3: TTS - Convertir respuesta a voz con edge-tts
        # ----------------------------------------------------------
        try:
            tts = edge_tts.Communicate(text=assistant_text, voice=TTS_VOICE)
            await tts.save(str(output_path))
        except Exception as e:
            raise HTTPException(
                status_code=502,
                detail=f"Error en la síntesis de voz (TTS): {str(e)}",
            )

        if not output_path.exists():
            raise HTTPException(
                status_code=500,
                detail="No se pudo generar el archivo de voz de respuesta.",
            )

        # ----------------------------------------------------------
        # Respuesta: devolver el MP3 generado
        # ----------------------------------------------------------
        # Devolver transcripciones en headers para que el frontend
        # acumule el historial de conversación (memoria del contexto)
        # Construir headers con transcripciones y QR
        response_headers = {
            "X-User-Text": quote(user_text),
            "X-Assistant-Text": quote(assistant_text),
        }

        # PERSISTENCIA: Si hay un pedido completado guardado en ACTIVE_ORDERS,
        # SIEMPRE devolver el QR y dashboard aunque el usuario haya cambiado de tema
        # (pida otro producto, descuento, etc.). Así el contexto NO se pierde.
        persisted_order = ACTIVE_ORDERS.get("default") or {}
        persisted_qr_path = persisted_order.get("qr_path")
        persisted_order_data = persisted_order.get("order_data")

        # Usar los datos del turno actual si hay, si no usar los persistidos
        if qr_path:
            effective_qr = qr_path
        else:
            effective_qr = persisted_qr_path

        if order_data:
            effective_order_data = order_data
        else:
            effective_order_data = persisted_order_data

        if effective_qr:
            qr_filename = Path(str(effective_qr)).name  # ej: qr_ABC123.png
            qr_order_id = qr_filename.replace("qr_", "").replace(".png", "")
            response_headers["X-Payment-QR"] = f"/api/payment-qr/{qr_order_id}"

        if effective_order_data:
            response_headers["X-Order-Data"] = quote(json.dumps(effective_order_data))

        return FileResponse(
            path=output_path,
            media_type="audio/mpeg",
            filename="respuesta.mp3",
            background=None,
            headers=response_headers,
        )

    finally:
        # Eliminar archivo temporal de entrada
        _cleanup(input_path)
        # Nota: output_path no se borra aquí porque FileResponse lo
        # necesita durante el envío. FastAPI lo elimina después de
        # enviar la respuesta si usamos background.
        # Para simplificar, borramos en el siguiente request o
        # dejamos la limpieza del directorio temp/ para un cron.
        # En este MVP, el archivo de salida se mantiene en temp/.


# ------------------------------------------------------------------
# LIMPIEZA PERIÓDICA (opcional)
# ------------------------------------------------------------------

@app.on_event("startup")
async def startup_cleanup():
    """Limpia archivos temporales antiguos al iniciar el servidor."""
    for f in TEMP_DIR.iterdir():
        if f.is_file():
            try:
                f.unlink()
            except OSError:
                pass


# Servir el frontend estático en la raíz.
# IMPORTANTE: Debe ir DESPUÉS de todas las rutas de la API para que
# /api/voice-chat no sea interceptado por el mount estático.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    # Puerto desde variable de entorno (Render, Railway, HF Spaces, etc.)
    # Default: 8000 para desarrollo local
    port = int(os.getenv("PORT", "8000"))

    uvicorn.run(app, host="0.0.0.0", port=port)
