#!/bin/bash
source ~/.avi-env 2>/dev/null || true
source ~/avi-bridge/bin/activate
uvicorn bridge:app --host 0.0.0.0 --port 8001 --app-dir /mnt/c/Users/alfre/whatsapp-bot
