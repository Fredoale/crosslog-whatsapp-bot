#!/usr/bin/env python3
"""
Bridge local — expone Obsidian + scripts a Render bot via ngrok.
Corre en WSL2 puerto 8001.
"""
from fastapi import FastAPI, Request
import httpx, subprocess, os, json, re, logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
OBSIDIAN_TOKEN = "eb1bd60fd8b6614cb1c160e2fb66a147eaf4007bcd9e6cc549258c28f8e37d31"
OBSIDIAN_URL   = "http://localhost:27123"
GOOGLE_ACC     = "alealfredo.af@gmail.com"

env = {**os.environ, "GOG_KEYRING_PASSWORD": ""}

def build_system_prompt_base() -> str:
    hoy = datetime.now().strftime("%A %d de %B de %Y")
    dias = {"Monday":"Lunes","Tuesday":"Martes","Wednesday":"Miércoles",
            "Thursday":"Jueves","Friday":"Viernes","Saturday":"Sábado","Sunday":"Domingo"}
    meses = {"January":"enero","February":"febrero","March":"marzo","April":"abril",
             "May":"mayo","June":"junio","July":"julio","August":"agosto",
             "September":"septiembre","October":"octubre","November":"noviembre","December":"diciembre"}
    for en, es in {**dias, **meses}.items():
        hoy = hoy.replace(en, es)
    manana = datetime.now().strftime("%Y-%m-%d")
    from datetime import timedelta
    manana_dt = datetime.now() + timedelta(days=1)
    manana_fmt = manana_dt.strftime("%d/%m/%Y")
    return f"""Hoy es {hoy}. Mañana es {manana_fmt}.

Tenés acceso a herramientas reales:
- buscar_hdr: busca HDRs por número o por fecha (usa formato DD/MM/YYYY)
- leer_obsidian: lee una nota del vault de Obsidian
- escribir_obsidian: escribe o actualiza una nota en Obsidian
- ejecutar_script: ejecuta un script Python del sistema

Cuando el usuario pida datos (HDR, viajes, tarifas, clientes), usá las herramientas. No inventes datos.
Si dicen "mañana", la fecha es {manana_fmt}. Si dicen "hoy", usá la fecha de hoy.
Respuestas cortas y directas. Sin presentarte. Sin listas de capacidades.
Este es un canal de WhatsApp — evitá markdown con asteriscos o #, usá texto plano."""

def cargar_personalidad() -> str:
    """Carga la personalidad de Avi desde Obsidian en cada request."""
    try:
        import urllib.request
        req = urllib.request.Request(f"{OBSIDIAN_URL}/vault/Memoria/personalidad-avi.md")
        req.add_header("Authorization", f"Bearer {OBSIDIAN_TOKEN}")
        personalidad = urllib.request.urlopen(req, timeout=3).read().decode("utf-8")
        return personalidad + "\n\n" + build_system_prompt_base()
    except Exception as e:
        logger.warning(f"No se pudo cargar personalidad de Obsidian: {e}")
        return "Eres Avi, asistente operativa de Crosslog. Venezolana, directa.\n\n" + build_system_prompt_base()

TOOLS = [
    {
        "name": "buscar_hdr",
        "description": "Busca HDRs en las planillas de Google Sheets. Puede buscar por número de HDR o por fecha de salida.",
        "input_schema": {
            "type": "object",
            "properties": {
                "numero": {"type": "string", "description": "Número del HDR a buscar (ej: 12345)"},
                "fecha": {"type": "string", "description": "Fecha de salida en formato DD/MM/YYYY para listar todos los HDRs de ese día"}
            }
        }
    },
    {
        "name": "leer_obsidian",
        "description": "Lee el contenido de una nota del vault de Obsidian",
        "input_schema": {
            "type": "object",
            "properties": {
                "ruta": {"type": "string", "description": "Ruta relativa al vault, ej: Memoria/tareas-pendientes.md"}
            },
            "required": ["ruta"]
        }
    },
    {
        "name": "escribir_obsidian",
        "description": "Escribe o actualiza el contenido de una nota en Obsidian",
        "input_schema": {
            "type": "object",
            "properties": {
                "ruta": {"type": "string", "description": "Ruta relativa al vault"},
                "contenido": {"type": "string", "description": "Contenido a escribir"}
            },
            "required": ["ruta", "contenido"]
        }
    },
    {
        "name": "ejecutar_script",
        "description": "Ejecuta un script Python del sistema (briefing, reporte, etc.)",
        "input_schema": {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "Nombre del script, ej: briefing.py"},
                "args": {"type": "array", "items": {"type": "string"}, "description": "Argumentos opcionales"}
            },
            "required": ["script"]
        }
    }
]

# ── Ejecutores de herramientas ────────────────────────────────────────────────

def tool_buscar_hdr(numero: str = "", fecha: str = "") -> str:
    try:
        if fecha:
            args = ["python3", "/mnt/c/Users/alfre/buscar-hdr.py", "--fecha", fecha, "--silent"]
        else:
            args = ["python3", "/mnt/c/Users/alfre/buscar-hdr.py", numero, "--silent"]
        r = subprocess.run(args, capture_output=True, text=True, env=env, timeout=30)
        return r.stdout.strip() or r.stderr.strip() or "Sin resultados"
    except Exception as e:
        return f"Error: {e}"

def tool_leer_obsidian(ruta: str) -> str:
    try:
        import urllib.request
        req = urllib.request.Request(f"{OBSIDIAN_URL}/vault/{ruta}")
        req.add_header("Authorization", f"Bearer {OBSIDIAN_TOKEN}")
        return urllib.request.urlopen(req).read().decode("utf-8")[:3000]
    except Exception as e:
        return f"Error leyendo {ruta}: {e}"

def tool_escribir_obsidian(ruta: str, contenido: str) -> str:
    try:
        import urllib.request
        data = contenido.encode("utf-8")
        req = urllib.request.Request(f"{OBSIDIAN_URL}/vault/{ruta}", data=data, method="PUT")
        req.add_header("Authorization", f"Bearer {OBSIDIAN_TOKEN}")
        req.add_header("Content-Type", "text/markdown")
        urllib.request.urlopen(req)
        return f"Nota guardada: {ruta}"
    except Exception as e:
        return f"Error escribiendo {ruta}: {e}"

def tool_ejecutar_script(script: str, args: list = []) -> str:
    scripts_permitidos = ["briefing.py", "buscar-hdr.py", "obsidian-auditoria.py"]
    if script not in scripts_permitidos:
        return f"Script no permitido: {script}"
    try:
        r = subprocess.run(
            ["python3", f"/mnt/c/Users/alfre/{script}"] + args + ["--silent"],
            capture_output=True, text=True, env=env, timeout=60
        )
        return r.stdout.strip() or r.stderr.strip() or "Ejecutado sin output"
    except Exception as e:
        return f"Error: {e}"

def ejecutar_tool(nombre: str, inputs: dict) -> str:
    if nombre == "buscar_hdr":
        return tool_buscar_hdr(inputs.get("numero", ""), inputs.get("fecha", ""))
    elif nombre == "leer_obsidian":
        return tool_leer_obsidian(inputs["ruta"])
    elif nombre == "escribir_obsidian":
        return tool_escribir_obsidian(inputs["ruta"], inputs["contenido"])
    elif nombre == "ejecutar_script":
        return tool_ejecutar_script(inputs["script"], inputs.get("args", []))
    return "Herramienta desconocida"

# ── Endpoint principal ────────────────────────────────────────────────────────

@app.post("/ask")
async def ask(request: Request):
    body = await request.json()
    mensaje = body.get("mensaje", "")

    messages = [{"role": "user", "content": mensaje}]
    system_prompt = cargar_personalidad()

    async with httpx.AsyncClient() as client:
        # Agentic loop — Claude puede llamar herramientas varias veces
        for _ in range(5):
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
                    "system": system_prompt,
                    "tools": TOOLS,
                    "messages": messages
                },
                timeout=60
            )
            data = r.json()
            stop_reason = data.get("stop_reason")
            logger.info(f"Claude stop_reason: {stop_reason} | data keys: {list(data.keys())}")
            if "error" in data:
                logger.error(f"Claude API error: {data['error']}")
                return {"respuesta": f"Error de API: {data['error'].get('message', 'desconocido')}"}

            if stop_reason == "end_turn":
                # Respuesta final
                for block in data["content"]:
                    if block["type"] == "text":
                        return {"respuesta": block["text"]}
                return {"respuesta": "Sin respuesta"}

            elif stop_reason == "tool_use":
                # Claude quiere usar una herramienta
                messages.append({"role": "assistant", "content": data["content"]})
                tool_results = []
                for block in data["content"]:
                    if block["type"] == "tool_use":
                        resultado = ejecutar_tool(block["name"], block["input"])
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block["id"],
                            "content": resultado
                        })
                messages.append({"role": "user", "content": tool_results})
            else:
                break

    return {"respuesta": "No pude procesar el mensaje."}

@app.get("/health")
def health():
    return {"status": "ok", "bridge": "Crosslog Avi Bridge"}
