# client/video/vision_transmitter.py — Frame Uploader for OCR
# Phase 7D: Client side of the visual pipeline
#
# One job:
#   Take a PIL.Image captured by FrameGrabber
#   Encode it as PNG bytes in memory
#   POST it to the server's /vision/ocr endpoint
#   Return the extracted text string
#
# RUNS ON: Your Windows laptop (client-side)
# CONNECTS TO: same FastAPI server as the audio pipeline
#             (localhost via SSH tunnel, or DigitalOcean droplet)
#
# USED BY:
#   - workers.py → LiveWorker drains frames from FrameGrabber every
#                  second, skips if pHash unchanged, calls this.
#
# DESIGN NOTES:
#   - One request per frame. No batching — at 1 frame/sec the load
#     is light and batching would slow down the first result.
#   - Retry ONCE on network errors. Frames arrive continuously, so
#     if one fails the next one is never more than a second away.
#     Heavy retry loops would queue up and snowball under bad wifi.
#   - Short timeout (8 seconds). Tesseract on the server should
#     finish in well under a second for a 1920px frame. 8 seconds
#     means "something is clearly wrong" — give up and move on.

import asyncio
import io
import os
import random

import aiohttp
from dotenv import load_dotenv

# re-using the same env vars as transmitter.py so the OCR endpoint
# follows the audio server automatically. one place to change the IP.
load_dotenv()
SERVER_IP = os.getenv("SERVER_IP", "localhost")
SERVER_PORT = os.getenv("SERVER_PORT", "8000")


# ============================================
# VISION TRANSMITTER
# ============================================

class VisionTransmitter:
    """
    Sends one PIL.Image frame at a time to the server for OCR.
    Lightweight cousin of BulkTransmitter — no file on disk, no
    progress callback, just a quick request / response.

    Public interface:
        .upload_frame(image)         — returns extracted text string
        .check_vision_available()    — returns True if /vision/health is OK

    The class holds no state between calls. Each upload_frame is
    independent — safe to call from an asyncio task while the audio
    WSS pipeline is also running.
    """

    def __init__(self, status_callback=None):
        """
        Args:
            status_callback: optional function(status: str, message: str)
                called when a request fails or a retry fires. Matches
                the same contract as LiveTransmitter/BulkTransmitter so
                the UI can treat all three the same way.
        """
        self.ocr_url = f"http://{SERVER_IP}:{SERVER_PORT}/vision/ocr"
        self.health_url = f"http://{SERVER_IP}:{SERVER_PORT}/vision/health"

        # one retry after the first attempt = two total tries.
        # more than that starts queueing frames faster than we drain
        # them at 1 Hz.
        self.max_retries = 2

        # base_delay chosen small — the caller is already on a 1 Hz
        # loop, so a long sleep here would push us behind real-time.
        self.base_delay = 0.5    # seconds
        self.max_delay = 2.0     # seconds

        # per-request timeout. Tesseract on the server finishes in
        # ~0.5s normally, so 8s is "something is very wrong" territory.
        self.timeout_seconds = 8

        self.status_callback = status_callback

    def _emit_status(self, status: str, message: str):
        """Send a status update to whoever is listening, if anyone."""
        print(f"[VISION] [{status.upper()}] {message}")
        if self.status_callback:
            self.status_callback(status, message)

    async def upload_frame(self, image) -> str:
        """
        Upload one PIL.Image, return the OCR text.

        Args:
            image: PIL.Image in RGB mode (FrameGrabber returns exactly this)

        Returns:
            Extracted text string. Empty string if the server found
            nothing readable — that is NOT an error, just a blank frame.

        Raises:
            RuntimeError: if all retries fail. Caller should log and move on
                (the next frame will be along in about a second).
        """
        # Step 1 — encode the image to PNG in memory
        # PNG is lossless. Tesseract benefits from sharp edges on text,
        # and JPEG compression can blur small characters enough to hurt
        # OCR accuracy. PNG bytes for a 1920px frame land around 1-2 MB.
        png_bytes = self._encode_png(image)

        last_error = None

        # Step 2 — try, retry on network error, give up on HTTP errors
        for attempt in range(self.max_retries):
            try:
                timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)

                async with aiohttp.ClientSession(timeout=timeout) as session:
                    # re-creating FormData each attempt because the bytes
                    # buffer is consumed after a successful send
                    data = aiohttp.FormData()
                    data.add_field(
                        "file",
                        png_bytes,
                        filename="frame.png",
                        content_type="image/png",
                    )

                    async with session.post(self.ocr_url, data=data) as resp:
                        if resp.status == 200:
                            result = await resp.json()
                            return result.get("text", "")

                        # 503 = tesseract missing on server. no retry —
                        # retrying will not magically install it.
                        if resp.status == 503:
                            error_text = await resp.text()
                            raise RuntimeError(
                                f"OCR unavailable on server (503): {error_text}"
                            )

                        # other HTTP errors (400, 500) — the request itself
                        # is the problem, retrying will not help.
                        error_text = await resp.text()
                        raise RuntimeError(
                            f"OCR server returned {resp.status}: {error_text}"
                        )

            except RuntimeError:
                # server-side errors — do NOT retry
                raise
            except asyncio.TimeoutError:
                last_error = f"OCR request timed out (>{self.timeout_seconds}s)"
            except (aiohttp.ClientError, OSError) as e:
                last_error = str(e)

            # if we reach here it was a retryable network error
            if attempt < self.max_retries - 1:
                base = min(self.base_delay * (2 ** attempt), self.max_delay)
                # jitter — same reason as LiveTransmitter/BulkTransmitter:
                # if many clients retry at once, spread them out.
                delay = base * random.uniform(0.5, 1.5)
                self._emit_status(
                    "retrying",
                    f"OCR upload failed, retrying in {delay:.1f}s "
                    f"(attempt {attempt + 2}/{self.max_retries})"
                )
                await asyncio.sleep(delay)

        # all retries burned — give up on this frame. caller should
        # log and move on to the next frame.
        self._emit_status(
            "failed",
            f"OCR upload failed after {self.max_retries} attempts: {last_error}"
        )
        raise RuntimeError(
            f"OCR upload failed after {self.max_retries} attempts: {last_error}"
        )

    async def check_vision_available(self) -> bool:
        """
        Ping /vision/health and return True if Tesseract is ready.

        Called once at the start of a recording session so the UI
        can decide whether to fire frames at all. Saves wasted
        uploads when the server has no Tesseract installed.
        """
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(self.health_url) as resp:
                    if resp.status != 200:
                        return False
                    data = await resp.json()
                    return data.get("status") == "ready"
        except Exception:
            # any failure here = not available. do not raise — the
            # audio pipeline should still run even if OCR is dead.
            return False

    # ------------------------------------------
    # INTERNAL — Image encoding
    # ------------------------------------------

    def _encode_png(self, image) -> bytes:
        """
        Turn a PIL.Image into raw PNG bytes without touching disk.

        BytesIO is an in-memory file-like object. PIL.save() writes to
        it exactly like it would to a real file, but the result stays
        in RAM. Nothing hits the SSD — faster and cleaner than tempfiles.

        Args:
            image: PIL.Image

        Returns:
            bytes — the PNG-encoded image
        """
        buffer = io.BytesIO()
        # optimize=False keeps the encode fast. We are not writing this
        # to disk long-term; we just need bytes to upload. The extra
        # few KB of size don't matter on a localhost/SSH tunnel.
        image.save(buffer, format="PNG", optimize=False)
        return buffer.getvalue()
