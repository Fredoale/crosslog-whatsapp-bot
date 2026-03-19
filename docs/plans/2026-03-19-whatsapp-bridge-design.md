# WhatsApp Bridge — Diseño
**Fecha:** 2026-03-19

## Objetivo
Conectar el bot de WhatsApp con toda la operatividad de Crosslog: Obsidian, Google Sheets, scripts Python, igual que Avi en Telegram.

## Arquitectura

```
WhatsApp → Render bot → Cloudflare Tunnel → Bridge WSL2 → OpenClaw
                                                         → Obsidian (localhost:27123)
                                                         → Scripts Python (buscar-hdr, etc.)
```

## Componentes

### 1. Bridge FastAPI (WSL2) — `bridge.py`
- Puerto: 8001
- `POST /ask` — recibe mensaje, llama a OpenClaw, devuelve respuesta
- Autostart junto con OpenClaw

### 2. Cloudflare Tunnel
- URL permanente gratis → apunta a localhost:8001
- Se instala en WSL2 una sola vez como servicio

### 3. Render bot modificado — `main.py`
- En vez de llamar Claude directamente → llama al bridge via HTTP
- Variable de entorno: `BRIDGE_URL` (URL de Cloudflare)
- Fallback si bridge no responde: "No puedo acceder a los datos ahora, intentá más tarde."

## Variables de entorno nuevas en Render
- `BRIDGE_URL` — URL del Cloudflare Tunnel

## Dependencias
- PC prendida → acceso completo a datos
- PC apagada → bot responde con mensaje de no disponibilidad

## Próximo paso
Migrar a VPS para 24/7 completo (proyecto futuro).
