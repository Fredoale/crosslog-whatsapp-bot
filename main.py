from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
import httpx, os, json

app = FastAPI()

# ── Config desde variables de entorno ───────────────
WA_TOKEN       = os.environ["WA_TOKEN"]          # Access token permanente
WA_PHONE_ID    = os.environ["WA_PHONE_ID"]       # Phone Number ID
VERIFY_TOKEN   = os.environ["VERIFY_TOKEN"]      # Token que vos elegís
ANTHROPIC_KEY  = os.environ["ANTHROPIC_API_KEY"] # Tu clave de Claude

# Usuarios internos autorizados (números en formato internacional sin +)
USUARIOS_AUTORIZADOS = os.environ.get("USUARIOS_AUTORIZADOS", "").split(",")

SYSTEM_PROMPT = """Sos Avi, el asistente de Crosslog Logística.
Ayudás al equipo interno con información sobre viajes, HDRs y clientes.

Cuando te pidan buscar un HDR, responde con los datos que te pasen.
Sé conciso, profesional y en español."""

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

        # Verificar usuario autorizado
        if USUARIOS_AUTORIZADOS and USUARIOS_AUTORIZADOS[0]:
            if from_num not in USUARIOS_AUTORIZADOS:
                return {"status": "ignored"}

        # Procesar con Claude
        respuesta = await procesar_con_claude(text, from_num)

        # Enviar respuesta por WhatsApp
        await enviar_mensaje(from_num, respuesta)

    except (KeyError, IndexError):
        pass  # No es un mensaje de texto, ignorar

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
