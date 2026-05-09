# tests/test_vision_olmocr.py — Smoke + end-to-end test for OlmOCR-2.
#
# What this test covers:
#   1. The class loads weights from /parallel_scratch onto the GPU.
#   2. ready flag flips True after load().
#   3. ocr_image() returns a non-empty string with LaTeX markers
#      when given a synthetic math frame.
#
# What it does NOT cover:
#   - The HTTP endpoint (vision_v2.py). That gets a separate test
#     once the route is mounted in main.py — keeps this test fast
#     and runnable without booting FastAPI.
#
# How to run on the GPU node:
#   srun --jobid=<JOBID> bash -c \
#     '/users/$USER/.conda/envs/transcribe/bin/python tests/test_vision_olmocr.py'

import io
import sys
import time
from pathlib import Path

# server/ is a sibling of tests/ — add the project root to sys.path
# so "from server.models import ..." works no matter where pytest
# or python is invoked from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "server"))

from PIL import Image, ImageDraw  # noqa: E402
from models.vision_olmocr import OlmOCRModel  # noqa: E402


def make_test_image() -> Image.Image:
    """
    Build a simple 800x200 white image with one math equation.
    Plain ASCII inside — the model should LaTeX-wrap the math parts.
    """
    img = Image.new("RGB", (800, 200), "white")
    d = ImageDraw.Draw(img)
    d.text(
        (40, 60),
        "E = mc^2     and    sum from i=1 to n of x_i",
        fill="black",
    )
    return img


def test_load_and_infer():
    """
    End-to-end: construct, load, run one inference, check output.
    Asserts loud — fails the test runner if anything is off.
    """
    print("[TEST] constructing model wrapper...")
    m = OlmOCRModel()
    assert not m.ready, "ready should be False before load()"

    print("[TEST] calling load() — this is the slow part...")
    t0 = time.time()
    m.load()
    load_time = time.time() - t0
    print(f"[TEST] load() finished in {load_time:.1f}s")
    assert m.ready, "ready should be True after load()"
    assert m.model is not None
    assert m.processor is not None

    print("[TEST] building synthetic test image...")
    img = make_test_image()

    print("[TEST] running ocr_image()...")
    t0 = time.time()
    text = m.ocr_image(img)
    infer_time = time.time() - t0
    print(f"[TEST] inference took {infer_time:.1f}s")
    print("---OUTPUT---")
    print(text)
    print("---END---")

    # cheap correctness checks — exact output varies run to run with
    # greedy decoding (still deterministic but driver-version sensitive),
    # so just assert SHAPE: non-empty + LaTeX wrapping was applied.
    assert text, "OCR returned empty string"
    assert "$" in text, f"no LaTeX delimiter found in: {text!r}"
    print("[TEST] PASS — OlmOCR loaded, ran, produced LaTeX-wrapped output")


if __name__ == "__main__":
    # Allow running this file directly as a script (no pytest needed).
    # Useful inside an srun call where importing pytest's plugins
    # would just add boot time.
    test_load_and_infer()
