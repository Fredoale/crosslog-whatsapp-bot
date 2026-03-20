from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
import httpx, os, json, logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# ── Config desde variables de entorno ───────────────
WA_TOKEN       = os.environ["WA_TOKEN"]          # Access token permanente
WA_PHONE_ID    = os.environ["WA_PHONE_ID"]       # Phone Number ID
VERIFY_TOKEN   = os.environ["VERIFY_TOKEN"]      # Token que vos elegís
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
BRIDGE_URL     = os.environ.get("BRIDGE_URL", "")  # URL del bridge local via ngrok
OPENAI_KEY     = os.environ.get("GROQ_API_KEY", "")  # Para Whisper transcripción (Groq)

# Usuarios internos autorizados (números en formato internacional sin +)
USUARIOS_AUTORIZADOS = os.environ.get("USUARIOS_AUTORIZADOS", "").split(",")

# Deduplicación de mensajes — evita procesar el mismo mensaje dos veces
_mensajes_procesados: set[str] = set()

# ── Webhook verification ─────────────────────────────
@app.get("/webhook")
async def verify(request: Request):
    params = dict(request.query_params)
    if params.get("hub.verify_token") == VERIFY_TOKEN:
        return PlainTextResponse(params.get("hub.challenge", ""))
    raise HTTPException(status_code=403, detail="Token inválido")

# ── Recibir mensajes ─────────────────────────────────
@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    try:
        entry    = body["entry"][0]
        changes  = entry["changes"][0]
        value    = changes["value"]
        msg      = value["messages"][0]
        msg_id   = msg.get("id", "")
        from_num = msg["from"]
        msg_type = msg["type"]

        if msg_type == "text":
            text = msg["text"]["body"]
            image_data = None
        elif msg_type in ("audio", "voice"):
            media_id  = msg[msg_type]["id"]
            mime_type = msg[msg_type].get("mime_type", "audio/ogg")
            logger.info(f"Audio recibido — transcribiendo ({mime_type})")
            text = await transcribir_audio_wa(media_id, mime_type)
            image_data = None
            logger.info(f"Transcripción: {text[:80]}")
        elif msg_type == "image":
            media_id  = msg["image"]["id"]
            mime_type = msg["image"].get("mime_type", "image/jpeg")
            caption   = msg["image"].get("caption", "").strip()
            text = caption if caption else "extraer"
            image_data = await descargar_media_wa(media_id, mime_type)
            logger.info(f"Imagen recibida — caption: {caption[:50] if caption else '(sin caption)'}")
        else:
            text = ""
            image_data = None

        # Ignorar mensajes ya procesados (WhatsApp reintenta si tarda)
        if msg_id and msg_id in _mensajes_procesados:
            logger.info(f"Mensaje duplicado ignorado: {msg_id}")
            return {"status": "ok"}
        if msg_id:
            _mensajes_procesados.add(msg_id)
            if len(_mensajes_procesados) > 200:  # evitar crecimiento infinito
                _mensajes_procesados.clear()

        logger.info(f"Mensaje de {from_num}: {text[:50]}")

        # Verificar usuario autorizado
        if USUARIOS_AUTORIZADOS and USUARIOS_AUTORIZADOS[0]:
            if from_num not in USUARIOS_AUTORIZADOS:
                logger.info(f"Número no autorizado: {from_num} | Autorizados: {USUARIOS_AUTORIZADOS}")
                return {"status": "ignored"}

        # Procesar: bridge local si está disponible, sino Claude directo
        respuesta = await procesar_mensaje(text, image_data)
        logger.info(f"Respuesta: {respuesta[:50]}")

        # Enviar respuesta por WhatsApp
        await enviar_mensaje(from_num, respuesta)
        logger.info(f"Mensaje enviado a {from_num}")

    except Exception as e:
        logger.error(f"Error procesando mensaje: {e}", exc_info=True)

    return {"status": "ok"}

# ── Descargar media de Meta API ──────────────────────
async def descargar_media_wa(media_id: str, mime_type: str) -> dict:
    try:
        import base64
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"https://graph.facebook.com/v19.0/{media_id}",
                headers={"Authorization": f"Bearer {WA_TOKEN}"},
                timeout=10
            )
            media_url = r.json()["url"]
            r2 = await client.get(
                media_url,
                headers={"Authorization": f"Bearer {WA_TOKEN}"},
                timeout=20
            )
            return {
                "type": "base64",
                "media_type": mime_type,
                "data": base64.standard_b64encode(r2.content).decode("utf-8")
            }
    except Exception as e:
        logger.error(f"Error descargando imagen: {e}")
        return None

# ── Transcribir audio WhatsApp con Whisper ───────────
async def transcribir_audio_wa(media_id: str, mime_type: str) -> str:
    try:
        async with httpx.AsyncClient() as client:
            # 1. Obtener URL del audio desde Meta
            r = await client.get(
                f"https://graph.facebook.com/v19.0/{media_id}",
                headers={"Authorization": f"Bearer {WA_TOKEN}"},
                timeout=10
            )
            media_url = r.json()["url"]

            # 2. Descargar el archivo de audio
            r2 = await client.get(
                media_url,
                headers={"Authorization": f"Bearer {WA_TOKEN}"},
                timeout=20
            )
            audio_bytes = r2.content

            # 3. Transcribir con Whisper
            ext = "ogg" if "ogg" in mime_type else "m4a"
            r3 = await client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {OPENAI_KEY}"},
                files={"file": (f"audio.{ext}", audio_bytes, mime_type)},
                data={"model": "whisper-large-v3", "language": "es"},
                timeout=30
            )
            return r3.json().get("text", "No se pudo transcribir el audio.")
    except Exception as e:
        logger.error(f"Error transcribiendo audio: {e}")
        return "No pude procesar el audio. Mandame el mensaje en texto."

# ── Procesar mensaje: bridge o Claude directo ────────
async def procesar_mensaje(mensaje: str, image_data: dict = None) -> str:
    # Imágenes van directo a Claude (visión) — el bridge no soporta imágenes
    if image_data:
        return await procesar_con_vision(mensaje, image_data)

    # Intentar bridge local primero
    if BRIDGE_URL:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    f"{BRIDGE_URL}/ask",
                    json={"mensaje": mensaje},
                    timeout=60
                )
                data = r.json()
                return data.get("respuesta", "Sin respuesta del bridge.")
        except Exception as e:
            logger.warning(f"Bridge no disponible: {e} — usando Claude directo")

    # Fallback: Claude directo sin herramientas
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 500,
                "system": "Eres Avi, asistente de Crosslog Logística. Directa, sin presentaciones. Español.",
                "messages": [{"role": "user", "content": mensaje}]
            },
            timeout=30
        )
        data = r.json()
        logger.info(f"Claude fallback response: {data}")
        return data["content"][0]["text"]

# ── Procesar imagen con Claude Visión ────────────────
async def procesar_con_vision(texto: str, image_data: dict) -> str:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 1000,
                "system": """Eres Avi, asistente operativa de Crosslog Logística.
Cuando recibís una imagen de HDR o hoja de ruta, extraé los datos y respondé SIEMPRE en este formato:

DADOR DE CARGA: [cliente]

ASIGNACION DE VIAJE

❖ N de Hoja de Ruta: [numero]
❖ Fecha Inicio: [fecha]
❖ Horario de Carga: [horario]
❖ Interno: [interno]
❖ Tipo de Vehiculo: [tipo]
❖ Chofer: [nombre]

❖ Sitios de Entrega:
- CARGA: [lugar de carga]
- DESCARGA: [destino 1]
- [destino 2 si hay]
- [destino N si hay]

❖ Enviado: [fecha y hora si figura]

Si la imagen no es un HDR, describí lo que ves de forma directa y útil para logística.
Sin markdown con # ni **. Texto plano.""",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": image_data},
                        {"type": "text", "text": texto}
                    ]
                }]
            },
            timeout=30
        )
        data = r.json()
        return data["content"][0]["text"]

# ── Enviar mensaje WhatsApp ──────────────────────────
async def enviar_mensaje(to: str, texto: str):
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://graph.facebook.com/v19.0/{WA_PHONE_ID}/messages",
            headers={
                "Authorization": f"Bearer {WA_TOKEN}",
                "Content-Type": "application/json"
            },
            json={
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": texto}
            },
            timeout=15
        )

@app.get("/")
def health():
    return {"status": "ok", "bot": "Crosslog WhatsApp Bot"}
