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
ANTHROPIC_KEY  = os.environ["ANTHROPIC_API_KEY"] # Tu clave de Claude

# Usuarios internos autorizados (números en formato internacional sin +)
USUARIOS_AUTORIZADOS = os.environ.get("USUARIOS_AUTORIZADOS", "").split(",")

SYSTEM_PROMPT = """Tu nombre es Avi. Eres la asistente operativa de Crosslog Logística. Venezolana, mujer, directa, inteligente e irónica con clase.

REGLA PRINCIPAL: Nunca respondas con listas de lo que puedes hacer. Nunca uses frases como "¡Con gusto!", "Por supuesto", "Entendido", "¡Excelente pregunta!", "Puedo ayudarte con:". Eres una colega inteligente, no un bot corporativo.

SALUDOS — Cuando alguien diga "hola", "buenas", "qué tal" o similar, responde con energía y una pregunta directa. Ejemplos EXACTOS de cómo responder:
- "¡Activa! ¿Qué movemos hoy?"
- "¡Buenas! ¿Viajes, HDRs o qué necesitás?"
- "¡Qué hay! ¿En qué te ayudo?"
- "¡Lista! ¿Por dónde arrancamos?"

CONSULTAS OPERATIVAS: Responde directo al punto. Primero la respuesta, después la explicación si hace falta. Si no tenés el dato, decilo y proponé cómo buscarlo. Nunca inventes información.

Idioma: español. Respuestas cortas y directas."""

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
        from_num = msg["from"]
        text     = msg["text"]["body"] if msg["type"] == "text" else ""

        logger.info(f"Mensaje de {from_num}: {text[:50]}")

        # Verificar usuario autorizado
        if USUARIOS_AUTORIZADOS and USUARIOS_AUTORIZADOS[0]:
            if from_num not in USUARIOS_AUTORIZADOS:
                logger.info(f"Número no autorizado: {from_num} | Autorizados: {USUARIOS_AUTORIZADOS}")
                return {"status": "ignored"}

        # Procesar con Claude
        respuesta = await procesar_con_claude(text, from_num)
        logger.info(f"Respuesta Claude: {respuesta[:50]}")

        # Enviar respuesta por WhatsApp
        await enviar_mensaje(from_num, respuesta)
        logger.info(f"Mensaje enviado a {from_num}")

    except Exception as e:
        logger.error(f"Error procesando mensaje: {e}", exc_info=True)

    return {"status": "ok"}

# ── Claude API ───────────────────────────────────────
async def procesar_con_claude(mensaje: str, usuario: str) -> str:
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
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": mensaje}]
            },
            timeout=30
        )
        data = r.json()
        logger.info(f"Claude API response: {data}")
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
