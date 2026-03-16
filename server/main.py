# server/main.py — FastAPI Server Entry Point
# Phase 2: The Brain (skeleton first, models added next)
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, UploadFile, File
from fastapi.responses import JSONResponse
import uvicorn
from models.transcriber import TranscriptionRouter

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

    try:
        # Step 2: Keep listening for audio chunks forever
        # This loop runs until the client disconnects
        while True:
            # Receive raw audio bytes from the client
            audio_chunk = await websocket.receive_bytes()

            # Route audio through confidence-based model system
            result = router.transcribe(audio_chunk)
            await websocket.send_json({
                "status": "success",
                "transcript": result["text"],
                "confidence": result["confidence"],
                "model_used": result["model_used"],
                "was_fallback": result["was_fallback"]
            })

    except Exception as e:
        # Client disconnected or connection error
        print(f"[LIVE] Client disconnected: {e}")


# ============================================
# DOOR 2: HTTP POST Endpoint — Bulk Mode
# Path: /api/transcribe
# ============================================
# WHY HTTP POST? Bulk = one complete file, one request, one response.
# No need for persistent connection. Simple and clean.

@app.post("/api/transcribe")
async def bulk_transcribe(file: UploadFile = File(...)):
    # Step 1: Read the entire uploaded audio file
    audio_data = await file.read()
    file_size = len(audio_data)

    print(f"[BULK] Received file: {file.filename}, size: {file_size} bytes")

    # Route audio through confidence-based model system
    result = router.transcribe(audio_data)

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
    uvicorn.run(app, host="0.0.0.0", port=8000)
