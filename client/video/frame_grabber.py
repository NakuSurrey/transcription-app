# client/video/frame_grabber.py — Window Screenshot Capturer
# Phase 7C: The Eyes
#
# This module does ONE job:
#   Screenshot a specific window once per second
#   Store those frames in memory as PIL Image objects
#   That's it. No AI processing happens here.
#
# A FUTURE module (vision pipeline) will pick up these frames
# and run OCR / visual understanding on them. This module
# only captures and stores — same separation pattern as
# audio capture vs transcription.
#
# RUNS ON: Your Windows laptop (client-side)
# REQUIRES: Pillow (pip install Pillow), ctypes (built-in)
#
# USED BY:
#   - workers.py → LiveWorker starts/stops FrameGrabber alongside audio
#   - overlay.py → passes the HWND of the selected window
#
# WHY PrintWindow API instead of screenshot libraries:
#   Libraries like mss or PIL.ImageGrab capture the SCREEN — they
#   grab whatever pixels are visible at those coordinates. If another
#   window covers the target, you get the wrong pixels.
#
#   PrintWindow tells the target window: "draw yourself into this
#   bitmap." The window renders its own content directly, regardless
#   of what's on top. Works even when partially covered.
#
# WHY ctypes instead of pywin32:
#   Consistent with window_selector.py. ctypes is built into Python
#   and talks to user32.dll / gdi32.dll directly. No extra dependency
#   needed for this specific task.

import ctypes
import ctypes.wintypes
import threading
import time
from PIL import Image


# ============================================
# WINDOWS API CONSTANTS
# ============================================

# PrintWindow flags
# PW_RENDERFULLCONTENT (0x2) — tells PrintWindow to ask DWM
# (Desktop Window Manager) for the fully composited content.
# Without this flag, some modern apps render blank/black because
# their content is composited by DWM, not drawn directly.
PW_RENDERFULLCONTENT = 0x00000002

# GetDIBits constants
DIB_RGB_COLORS = 0
BI_RGB = 0


# ============================================
# BITMAP INFO HEADER — GDI Structure
# ============================================
# Windows GDI needs this structure to know the format of
# the pixel data we want. Defined at module level so
# _capture_window() doesn't rebuild it every call.
#
# Fields that matter for us:
#   biWidth / biHeight — dimensions of the bitmap
#   biBitCount = 32 — 4 bytes per pixel (BGRA)
#   biHeight negative — means top-down row order
#     (first pixel in buffer = top-left of image)
#   biCompression = BI_RGB — uncompressed, raw pixels

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


# ============================================
# WINDOWS API HANDLES
# ============================================
# Loading DLL references once at module level — calling
# ctypes.windll.user32 every time creates overhead.

_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32


# ============================================
# FRAME GRABBER
# ============================================

class FrameGrabber:
    """
    Captures screenshots of a specific window at regular intervals.

    Uses Win32 PrintWindow API to render the window's own content
    into a bitmap — works even when the window is partially covered
    by other windows.

    Stores captured frames as (timestamp, PIL.Image) tuples in memory.
    A future vision pipeline will consume these frames.

    Public interface:
        .start()          — begin capturing in background thread
        .stop()           — stop capturing
        .get_frames()     — return list of (timestamp, Image) tuples
        .get_frame_count() — return number of frames captured so far
        .clear_frames()   — free all stored frames from memory
    """

    def __init__(self, hwnd: int = None, interval: float = 1.0,
                 max_frames: int = 3600):
        """
        Args:
            hwnd: Windows handle of the target window to screenshot.
                None = frame capture disabled (system-wide audio mode,
                no single window to screenshot).
            interval: Seconds between captures. Default 1.0 = one frame
                per second. Lower = more frames, more memory.
            max_frames: Maximum frames to keep in memory. Default 3600
                = 1 hour at 1fps. Oldest frames are dropped when this
                limit is reached. At ~100KB per resized frame, 3600
                frames ≈ 360MB peak memory.
        """
        self.hwnd = hwnd
        self.interval = interval
        self.max_frames = max_frames

        # stored as list of (timestamp_float, PIL.Image) tuples
        # timestamp is time.time() at the moment of capture —
        # useful for correlating frames with audio timestamps later
        self._frames = []
        self._frames_lock = threading.Lock()

        self._running = False
        self._thread = None

    def start(self):
        """
        Begin capturing frames in a background thread.

        If hwnd is None (user picked "None — audio only"), capture
        is skipped entirely. There is no single window to screenshot.
        """
        if self._running:
            return

        if not self.hwnd:
            print("[FRAME] No HWND provided — frame capture disabled")
            return

        # verify the window actually exists before starting
        if not _user32.IsWindow(self.hwnd):
            print(f"[FRAME] HWND {self.hwnd} is not a valid window — skipping")
            return

        self._running = True
        self._frames.clear()

        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        print(f"[FRAME] Started — capturing HWND {self.hwnd} "
              f"every {self.interval}s (max {self.max_frames} frames)")

    def stop(self):
        """Stop the capture loop and wait for the thread to finish."""
        self._running = False

        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

        frame_count = len(self._frames)
        print(f"[FRAME] Stopped — {frame_count} frames captured")

    def get_frames(self):
        """
        Return all stored frames as a list of (timestamp, Image) tuples.

        Returns a copy of the list — safe to iterate without holding
        the lock. The Images themselves are shared references (not
        deep copies) to save memory.

        Returns:
            List of (float, PIL.Image) tuples. Float is time.time()
            at capture. List may be empty if no frames were captured.
        """
        with self._frames_lock:
            return list(self._frames)

    def get_frame_count(self):
        """Return the number of frames currently stored."""
        with self._frames_lock:
            return len(self._frames)

    def clear_frames(self):
        """
        Free all stored frames from memory.

        Call this after the vision pipeline has processed the frames
        and no longer needs them. Prevents memory buildup across
        multiple recording sessions.
        """
        with self._frames_lock:
            self._frames.clear()
        print("[FRAME] All frames cleared from memory")

    # ------------------------------------------
    # INTERNAL — Capture Loop
    # ------------------------------------------

    def _capture_loop(self):
        """
        Background thread that screenshots the window at regular intervals.

        Loop structure:
          1. Capture one frame
          2. Store it with a timestamp
          3. Sleep for self.interval seconds
          4. Repeat until self._running is False

        If the target window is closed mid-capture, the loop detects
        this (IsWindow returns False) and stops itself gracefully.
        """
        while self._running:
            try:
                frame = self._capture_window()
                if frame is not None:
                    timestamp = time.time()
                    with self._frames_lock:
                        self._frames.append((timestamp, frame))
                        # trim to max_frames — drop oldest when full
                        if len(self._frames) > self.max_frames:
                            self._frames = self._frames[-self.max_frames:]
            except Exception as e:
                # capture failed for this frame — log and continue.
                # transient errors (window minimized, GPU busy) should
                # not kill the entire capture session.
                print(f"[FRAME] Capture error (non-fatal): {e}")

            # sleep in small increments so stop() returns quickly.
            # if we did time.sleep(1.0), stop() would block up to 1 second
            # waiting for the sleep to finish. Sleeping in 0.1s chunks
            # means stop() waits at most 0.1s.
            sleep_end = time.time() + self.interval
            while self._running and time.time() < sleep_end:
                time.sleep(0.1)

    def _capture_window(self):
        """
        Screenshot the target window using PrintWindow Win32 API.

        HOW IT WORKS — step by step:

        Step 1: Check if the window still exists
            ↓
        Step 2: Get the window dimensions (GetWindowRect)
            ↓
        Step 3: Get a device context (DC) for the window
            ↓
        Step 4: Create a memory DC + bitmap to draw into
            ↓
        Step 5: Call PrintWindow — the window draws itself into our bitmap
            ↓
        Step 6: Read the raw pixel bytes out of the bitmap (GetDIBits)
            ↓
        Step 7: Clean up all GDI objects (CRITICAL — leaks crash Windows)
            ↓
        Step 8: Convert raw BGRA bytes to a PIL Image
            ↓
        Step 9: Resize if too large (save memory, vision models don't need 4K)

        Returns:
            PIL.Image in RGB mode, or None if capture failed.
        """
        # Step 1 — check the window is still alive
        if not _user32.IsWindow(self.hwnd):
            print("[FRAME] Target window closed — stopping capture")
            self._running = False
            return None

        # Step 2 — get window dimensions
        rect = ctypes.wintypes.RECT()
        _user32.GetWindowRect(self.hwnd, ctypes.byref(rect))
        width = rect.right - rect.left
        height = rect.bottom - rect.top

        # skip if window has zero size (minimized to tray, etc.)
        if width <= 0 or height <= 0:
            return None

        # --- GDI resource block ---
        # Everything between here and cleanup MUST have matching
        # cleanup calls. If any GDI object is not released, Windows
        # slowly leaks graphics memory until the system becomes
        # unstable. Using try/finally to guarantee cleanup.

        hwnd_dc = None
        mem_dc = None
        bitmap = None
        old_bitmap = None

        try:
            # Step 3 — get device context for the target window
            # GetWindowDC returns a DC that covers the entire window
            # (including title bar, borders). This is what PrintWindow
            # draws into.
            hwnd_dc = _user32.GetWindowDC(self.hwnd)
            if not hwnd_dc:
                return None

            # Step 4 — create memory DC and bitmap
            # CreateCompatibleDC creates an in-memory drawing surface.
            # CreateCompatibleBitmap creates a bitmap matching the
            # window's color format and dimensions.
            # SelectObject tells the memory DC to draw into our bitmap.
            mem_dc = _gdi32.CreateCompatibleDC(hwnd_dc)
            if not mem_dc:
                return None

            bitmap = _gdi32.CreateCompatibleBitmap(hwnd_dc, width, height)
            if not bitmap:
                return None

            old_bitmap = _gdi32.SelectObject(mem_dc, bitmap)

            # Step 5 — PrintWindow: the window renders itself into our bitmap
            # PW_RENDERFULLCONTENT asks DWM for the composited content.
            # This is needed for modern apps (UWP, Electron, Chrome) whose
            # content is drawn by DWM, not by the window itself.
            result = _user32.PrintWindow(self.hwnd, mem_dc, PW_RENDERFULLCONTENT)

            if not result:
                # some older apps don't support PW_RENDERFULLCONTENT.
                # fall back to basic PrintWindow (flag = 0).
                result = _user32.PrintWindow(self.hwnd, mem_dc, 0)

            if not result:
                # PrintWindow failed completely — window may be in a
                # state that doesn't support rendering (e.g., hung process)
                return None

            # Step 6 — extract raw pixel bytes from the bitmap
            bmi = BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.biWidth = width
            bmi.biHeight = -height  # negative = top-down row order
            bmi.biPlanes = 1
            bmi.biBitCount = 32     # 4 bytes per pixel: B, G, R, A
            bmi.biCompression = BI_RGB

            # allocate buffer: width * height * 4 bytes (32-bit BGRA)
            buffer_size = width * height * 4
            pixel_buffer = ctypes.create_string_buffer(buffer_size)

            _gdi32.GetDIBits(
                mem_dc, bitmap, 0, height,
                pixel_buffer, ctypes.byref(bmi), DIB_RGB_COLORS
            )

        finally:
            # Step 7 — clean up ALL GDI objects (CRITICAL)
            # GDI objects are system-wide limited resources. Windows
            # has a default limit of 10,000 GDI objects per process.
            # If we leak even one per frame at 1fps, we hit the limit
            # in under 3 hours and the entire app crashes.
            if old_bitmap and mem_dc:
                _gdi32.SelectObject(mem_dc, old_bitmap)
            if bitmap:
                _gdi32.DeleteObject(bitmap)
            if mem_dc:
                _gdi32.DeleteDC(mem_dc)
            if hwnd_dc:
                _user32.ReleaseDC(self.hwnd, hwnd_dc)

        # Step 8 — convert raw bytes to PIL Image
        # Windows bitmaps store pixels as BGRA (blue, green, red, alpha).
        # PIL's frombuffer with "BGRA" raw mode handles this directly.
        image = Image.frombuffer(
            "RGBA",                    # target mode (PIL internal)
            (width, height),           # dimensions
            pixel_buffer,              # raw byte data
            "raw",                     # decoder name
            "BGRA",                    # pixel format in the buffer
            0,                         # stride (0 = auto-calculate)
            1                          # orientation (1 = normal)
        )

        # drop the alpha channel — vision models work with RGB
        image = image.convert("RGB")

        # Step 9 — resize if the window is very large
        # a 3840x2160 (4K) frame is ~25MB uncompressed in memory.
        # resizing to max 1920px on longest side drops it to ~6MB.
        # vision models (OlmOCR, Qwen-VL) work well at 1920px —
        # text remains readable, details preserved, memory manageable.
        max_dimension = 1920
        if image.width > max_dimension or image.height > max_dimension:
            image.thumbnail(
                (max_dimension, max_dimension),
                Image.Resampling.LANCZOS
            )

        return image
