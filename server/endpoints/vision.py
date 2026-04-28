# server/endpoints/vision.py — OCR endpoint for the visual pipeline
# Phase 7D: The Eyes → Text
#
# One job:
#   Receive one PNG/JPG frame from the client
#   Run Tesseract OCR on it
#   Return the extracted text as JSON
#
# RUNS ON: HPC GPU node (same Slurm job as the audio server)
# REQUIRES: pytesseract (Python wrapper) + Tesseract binary on PATH
#
# USED BY:
#   - client/video/vision_transmitter.py → POSTs one frame per second
#   - server/main.py → mounts this router on /vision
#
# WHY A SEPARATE ROUTER INSTEAD OF PUTTING IT IN main.py:
#   The audio endpoints in main.py are already dense. Splitting each
#   feature into its own router file keeps main.py as a thin entry
#   point. Also makes it easy to disable OCR (comment one include_router
#   line) without touching the audio code.
#
# WHY CPU TESSERACT INSTEAD OF A GPU VISION MODEL:
#   Canary + Whisper already own the GPU slot on this node. Tesseract
#   runs on CPU — it cannot fight Canary for VRAM. Typical OCR on a
#   1920px frame takes 100–400ms of CPU time. That keeps up with the
#   1 frame/sec upload rate from the client.

import asyncio
import io

from fastapi import APIRouter, UploadFile, File
from fastapi.responses import JSONResponse

try:
    import pytesseract
    from PIL import Image
    TESSERACT_READY = True
except ImportError as e:
    # keeping this soft so the server still boots without OCR if the
    # module load step on HPC missed the tesseract install. The /ocr
    # endpoint will return 503 in that case — audio still works.
    print(f"[VISION] pytesseract/Pillow not available: {e}")
    TESSERACT_READY = False


# ============================================
# ROUTER
# ============================================
# APIRouter is FastAPI's way of grouping related endpoints into a
# module. main.py calls app.include_router(router, prefix="/vision")
# so every endpoint below is reachable under /vision/<path>.

router = APIRouter()


# ============================================
# OCR PATH
# ============================================
# Path:   POST /vision/ocr
# Input:  multipart form with "file" = PNG or JPG bytes
# Output: {"status": "success", "text": "...extracted text..."}
# Errors: 503 if tesseract is missing, 500 if OCR itself fails

def _run_ocr(image_bytes: bytes) -> str:
    """
    Synchronous OCR call — blocks the thread while Tesseract runs.

    Kept as a plain function so it can be handed to asyncio.to_thread.
    That keeps the FastAPI event loop free to answer other requests
    (including audio WebSocket pings) while OCR is running.

    Args:
        image_bytes: raw PNG or JPG bytes from the upload

    Returns:
        extracted text string — may be empty if nothing readable found
    """
    # using BytesIO here — no temp file needed. PIL can load from an
    # in-memory bytes buffer directly, same way it does from disk.
    image = Image.open(io.BytesIO(image_bytes))

    # convert to RGB — Tesseract works fine on RGB and some formats
    # (like palette-based PNG) confuse it. one safe convert avoids
    # a whole class of "Invalid resolution" errors.
    if image.mode != "RGB":
        image = image.convert("RGB")

    # image_to_string is the main Tesseract call.
    # lang="eng" covers English only — switch later if other
    # languages are needed. timeout=10 caps Tesseract at 10 seconds
    # so a pathological frame can't block the worker forever.
    text = pytesseract.image_to_string(image, lang="eng", timeout=10)

    # Tesseract returns a lot of whitespace and newlines even for
    # simple frames. strip() keeps the payload small over the wire.
    return text.strip()


@router.post("/ocr")
async def ocr_endpoint(file: UploadFile = File(...)):
    """
    Run OCR on one uploaded frame and return the extracted text.

    Flow:
      Step 1 → client uploads PNG bytes
      Step 2 → asyncio.to_thread runs Tesseract on a worker thread
      Step 3 → extracted text returned as JSON
    """
    # guard: if the module load on HPC didn't install tesseract, tell
    # the client clearly instead of crashing on the first call.
    if not TESSERACT_READY:
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "message": (
                    "Tesseract is not installed on the server. "
                    "Check the Slurm job log — the module load step "
                    "probably failed."
                ),
            },
        )

    # read the whole upload into memory — frames are small (~1 MB each
    # after Pillow's 1920px resize in FrameGrabber), so buffering is fine.
    image_bytes = await file.read()

    if not image_bytes:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Empty upload"},
        )

    try:
        # hand the blocking OCR call to a worker thread so the FastAPI
        # event loop stays responsive. same trick main.py uses for the
        # GPU inference calls.
        text = await asyncio.to_thread(_run_ocr, image_bytes)
    except Exception as e:
        # catching broadly on purpose — Tesseract can throw on malformed
        # images, missing language packs, timeouts, etc. any of these
        # should fail this one request without killing the worker.
        print(f"[VISION] OCR failed: {e}")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": f"OCR failed: {e}"},
        )

    return {
        "status": "success",
        "text": text,
        # length is useful on the client side for quick sanity checks
        # and debug logging — cheap to compute, harmless to expose.
        "length": len(text),
    }


# ============================================
# HEALTH CHECK FOR OCR
# ============================================
# Path: GET /vision/health
# Returns whether Tesseract is importable and callable.
# Useful for the client to decide at startup whether to show the
# "Show on-screen text" checkbox at all.

@router.get("/health")
async def vision_health():
    """Report whether OCR is available on this server instance."""
    if not TESSERACT_READY:
        return {"status": "unavailable", "reason": "pytesseract not installed"}

    # also try to get the Tesseract version string — if the Python
    # wrapper is installed but the binary is missing, this raises.
    try:
        version = str(pytesseract.get_tesseract_version())
        return {"status": "ready", "tesseract_version": version}
    except Exception as e:
        return {"status": "unavailable", "reason": f"tesseract binary missing: {e}"}
