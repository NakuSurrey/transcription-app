# server/main.py — FastAPI Server Entry Point
# Phase 2: The Brain (skeleton first, models added next)
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, UploadFile, File, Form
from fastapi.responses import JSONResponse
import uvicorn
from models.transcriber import TranscriptionRouter

# Phase 7D — OCR endpoint lives in its own router file so main.py
# stays focused on audio. include_router below mounts it on /vision.
from endpoints.vision import router as vision_router

# ============================================
# CREATE THE APP
# ============================================
# app = FastAPI(title="Transcription Server")

# ============================================
# LOAD AI MODELS AT STARTUP
# ============================================
# Models load ONCE when server boots — not per-request.
# Loading a model = copying gigabytes from disk into GPU memory.
# Takes 30-60 seconds. You do this ONCE, then reuse for every chunk.
# If we loaded per-request, every transcription would wait a minute.

router = TranscriptionRouter()


# @app.on_event("startup")
# async def startup_event():
#     """Called automatically when FastAPI server starts."""
#     router.load_models()

@asynccontextmanager
async def lifespan(app):
    # --- STARTUP: runs when server boots ---
    router.load_models()
    yield
    # --- SHUTDOWN: runs when server stops ---
    print("[SERVER] Shutting down, cleaning up resources")

app = FastAPI(title="Transcription Server", lifespan=lifespan)

# Phase 7D — mount the vision router under /vision.
# Endpoints: POST /vision/ocr, GET /vision/health
# Audio endpoints stay on their current paths — no conflict.
app.include_router(vision_router, prefix="/vision", tags=["vision"])

# ============================================
# DOOR 1: WebSocket Endpoint — Live Mode
# Path: /ws/transcribe
# ============================================
# WHY WebSocket? Live audio = hundreds of small chunks per minute.
# HTTP would require open→send→receive→close for EACH chunk.
# WebSocket opens ONCE, stays open — both sides talk freely.

@app.websocket("/ws/transcribe")
async def websocket_transcribe(websocket: WebSocket):
    # Step 1: Accept the incoming connection (handshake)
    await websocket.accept()
    print("[LIVE] Client connected")

    # Default language — used unless client sends a config message
    # Client can change this at any time by sending a JSON text message:
    #   {"type": "config", "lang": "fr"}
    # All subsequent audio chunks will use the new language until changed again.
    lang = "en"

    # Source label — identifies whether audio came from speakers or microphone.
    # Client sends: {"type": "config", "source": "speaker"} or "mic"
    # Server echoes this back with the transcript so the UI can label it.
    # Default "unknown" means client hasn't identified the source yet.
    source = "unknown"

    try:
        # Step 2: Keep listening for messages forever
        # Messages can be either:
        #   - bytes → audio chunk to transcribe
        #   - text (JSON) → configuration update (e.g., language change, source label)
        while True:
            message = await websocket.receive()

            # --- Text message: configuration update ---
            if "text" in message:
                import json
                try:
                    config = json.loads(message["text"])
                    if config.get("type") == "config":
                        if "lang" in config:
                            lang = config["lang"]
                            print(f"[LIVE] Language set to: {lang}")
                        if "source" in config:
                            source = config["source"]
                            print(f"[LIVE] Source set to: {source}")
                        await websocket.send_json({
                            "status": "config_updated",
                            "lang": lang,
                            "source": source
                        })
                except (json.JSONDecodeError, KeyError):
                    pass  # Ignore malformed config messages
                continue  # Don't try to transcribe text messages

            # --- Binary message: audio chunk to transcribe ---
            if "bytes" in message:
                audio_chunk = message["bytes"]

                # Route audio through confidence-based model system
                # asyncio.to_thread() runs the blocking GPU inference in a
                # background thread so the event loop stays free to respond
                # to WebSocket pings, health checks, and other connections.
                # Without this, the event loop freezes during inference and
                # the WebSocket ping timeout kills the connection.
                result = await asyncio.to_thread(router.transcribe, audio_chunk, lang)
                await websocket.send_json({
                    "status": "success",
                    "transcript": result["text"],
                    "confidence": result["confidence"],
                    "model_used": result["model_used"],
                    "was_fallback": result["was_fallback"],
                    "source": source
                })

    except Exception as e:
        # Log the full error — not just "disconnected"
        # This catches both real disconnects AND transcription errors
        import traceback
        print(f"[LIVE] Connection closed: {e}")
        traceback.print_exc()


# ============================================
# DOOR 2: HTTP POST Endpoint — Bulk Mode
# Path: /api/transcribe
# ============================================
# WHY HTTP POST? Bulk = one complete file, one request, one response.
# No need for persistent connection. Simple and clean.

@app.post("/api/transcribe")
async def bulk_transcribe(file: UploadFile = File(...), lang: str = Form("en")):
    """
    Bulk transcription endpoint.

    Args:
        file: Audio file to transcribe (WAV format)
        lang: Language code (default "en"). Sent as a form field alongside the file.
              Canary supports: "en", "de", "fr", "es"
              If Canary falls back to Whisper, Whisper auto-detects language.
    """
    # Step 1: Read the entire uploaded audio file
    audio_data = await file.read()
    file_size = len(audio_data)

    print(f"[BULK] Received file: {file.filename}, size: {file_size} bytes, lang: {lang}")

    # Route audio through confidence-based model system
    # Same threading fix as WebSocket handler — prevents blocking
    # the event loop during long GPU inference operations
    result = await asyncio.to_thread(router.transcribe, audio_data, lang)

    return JSONResponse(content={
        "status": "success",
        "filename": file.filename,
        "transcript": result["text"],
        "confidence": result["confidence"],
        "model_used": result["model_used"],
        "was_fallback": result["was_fallback"]
    })


# ============================================
# HEALTH CHECK — Is the server alive?
# ============================================
# Simple endpoint the client can ping to check
# if server is running before sending audio.

@app.get("/health")
async def health_check():
    return {"status": "alive"}


# ============================================
# START THE SERVER
# ============================================
# uvicorn is the ASGI server that actually runs FastAPI.
# FastAPI = the menu. Uvicorn = the restaurant that opens
# doors and serves customers.
# host="0.0.0.0" = accept connections from ANY IP
# (not just localhost) — critical for cloud deployment.

if __name__ == "__main__":
    # ws_ping_interval: how often server sends a ping to check if client is alive (seconds)
    # ws_ping_timeout: how long to wait for a pong reply before closing the connection (seconds)
    #
    # Defaults in the websockets library are 20s interval, 20s timeout.
    # GPU inference (especially first run with CUDA kernel compilation) can take 30-60+ seconds.
    # The threading fix (asyncio.to_thread) should prevent blocking, but this provides a
    # safety net in case any synchronous path still holds the event loop temporarily.
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        ws_ping_interval=60,
        ws_ping_timeout=60
    )
