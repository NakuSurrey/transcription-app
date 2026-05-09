# server/endpoints/vision_v2.py — Math-grade OCR endpoint
# Phase P2-1: OlmOCR-2 replaces Tesseract for math frames.
#
# One job:
#   Receive one PNG/JPG frame from the client
#   Run OlmOCR-2 on the GPU
#   Return LaTeX-wrapped text as JSON
#
# RUNS ON: HPC GPU node (same Slurm job as audio + Tesseract endpoints)
# REQUIRES: transformers, torch, accelerate, pillow, the OlmOCR weights
#           cached at /parallel_scratch (see surrey_job.sh HF_HOME)
#
# USED BY:
#   - client/video/vision_transmitter.py (later — when client switches over)
#   - tests/test_vision_olmocr.py (smoke + end-to-end)
#   - server/main.py — mounts this router on /vision_v2
#
# WHY a SEPARATE router from vision.py:
#   Tesseract stays as the fast/cheap CPU fallback path on /vision.
#   OlmOCR runs on the GPU and costs ~5 sec per frame. Different
#   characteristics, different lifecycle, different prefix. Splitting
#   them lets the client choose which one to call per frame.

import asyncio
import io

from fastapi import APIRouter, UploadFile, File
from fastapi.responses import JSONResponse

# soft import — same pattern as vision.py. server boots without these
# (audio still works); the /vision_v2/ocr endpoint just returns 503.
try:
    from PIL import Image
    from models.vision_olmocr import OlmOCRModel, OLMOCR_DEPS_OK
    OLMOCR_AVAILABLE = OLMOCR_DEPS_OK
except ImportError as e:
    print(f"[VISION_V2] dependencies missing: {e}")
    OLMOCR_AVAILABLE = False
    OlmOCRModel = None  # type: ignore


# ============================================
# ROUTER + MODEL HANDLE
# ============================================
# main.py wires the router via include_router(prefix="/vision_v2") and
# calls set_model(...) once after it has loaded the weights via the
# lifespan hook. That gives main.py full control over WHEN the heavy
# load runs (concurrent with Canary/Whisper boot, on the same lifespan
# context) instead of doing it lazily on the first request.

router = APIRouter()
_model = None  # populated by set_model() during server lifespan


def set_model(model):
    """Called by main.py once the OlmOCR weights are on the GPU."""
    global _model
    _model = model


# ============================================
# OCR PATH
# ============================================
# Path:   POST /vision_v2/ocr
# Input:  multipart form with "file" = PNG or JPG bytes
# Output: {"status": "success", "text": "...LaTeX...", "length": N}
# Errors: 503 if model not loaded, 400 if empty body, 500 on inference fail

def _run_olmocr(image_bytes: bytes) -> str:
    """
    Synchronous OCR call — runs the GPU forward pass.

    Stays a plain function so asyncio.to_thread can hand it to a
    worker thread. The FastAPI event loop keeps answering audio
    pings while this thread waits on the GPU.

    Args:
        image_bytes: raw PNG or JPG bytes from the upload

    Returns:
        LaTeX-wrapped extracted text, may be empty
    """
    # PIL reads from in-memory bytes — same trick vision.py uses for
    # Tesseract, no temp file dance.
    image = Image.open(io.BytesIO(image_bytes))

    # delegate the actual model call. The wrapper handles RGB convert,
    # cuDNN toggling, tokenization, generate, decode.
    return _model.ocr_image(image)


@router.post("/ocr")
async def ocr_endpoint(file: UploadFile = File(...)):
    """
    Run OlmOCR-2 on one uploaded frame and return LaTeX text.

    Flow:
        Step 1 → client uploads PNG bytes
        Step 2 → asyncio.to_thread runs OlmOCR on a worker thread
        Step 3 → LaTeX text returned as JSON
    """
    # guard 1: the import failed at boot — transformers/PIL missing.
    if not OLMOCR_AVAILABLE:
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "message": (
                    "OlmOCR dependencies not installed on the server. "
                    "Check that transformers, torch, accelerate, and "
                    "pillow are present in the conda env."
                ),
            },
        )

    # guard 2: model not yet loaded by lifespan, or load() failed.
    # never crash the request — return 503 and let the client retry.
    if _model is None or not getattr(_model, "ready", False):
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "message": (
                    "OlmOCR model is not loaded. Check the Slurm job "
                    "log — the lifespan startup hook may have failed."
                ),
            },
        )

    # buffer the upload. Frames are small (~1 MB after FrameGrabber's
    # 1920px resize) so loading the whole body is safe.
    image_bytes = await file.read()
    if not image_bytes:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Empty upload"},
        )

    try:
        # hand the GPU call to a worker thread — same pattern as
        # vision.py and main.py's audio path. Keeps the event loop free.
        text = await asyncio.to_thread(_run_olmocr, image_bytes)
    except Exception as e:
        # broad catch on purpose — model can throw on weird image
        # shapes, OOM, cuDNN hiccups. Any of these should fail this
        # one request, not the whole server.
        print(f"[VISION_V2] OCR failed: {e}")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": f"OCR failed: {e}"},
        )

    return {
        "status": "success",
        "text": text,
        "length": len(text),
    }


# ============================================
# HEALTH CHECK
# ============================================
# Path: GET /vision_v2/health
# Used by the client at startup to decide whether to enable the
# "math OCR" toggle in the UI.

@router.get("/health")
async def vision_v2_health():
    """Report whether OlmOCR is loaded and ready on this server."""
    if not OLMOCR_AVAILABLE:
        return {
            "status": "unavailable",
            "reason": "transformers/PIL not installed",
        }

    if _model is None:
        return {"status": "unavailable", "reason": "model not yet loaded"}

    if not _model.ready:
        return {"status": "unavailable", "reason": "load() did not complete"}

    return {"status": "ready", "model": _model.name}
