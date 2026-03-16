# server/models/transcriber.py — AI Model Loading & Confidence-Based Routing
# Phase 2b: The brain's decision-making system

import random

# ============================================
# CONFIDENCE THRESHOLD
# ============================================
# If Canary's confidence is at or above this number,
# we trust its result. Below this, we fall back to Whisper.
# 0.7 = 70% average confidence across all words in the chunk.
#
# WHY 0.7? Too high (0.9) = Whisper gets called too often,
# wasting GPU time on audio Canary handled fine.
# Too low (0.5) = bad transcriptions slip through.
# 0.7 is the sweet spot — tunable after real-world testing.

CONFIDENCE_THRESHOLD = 0.7


# ============================================
# MOCK MODELS (replaced with real models on GPU server)
# ============================================
# These simulate what real models do:
#   input: raw audio bytes
#   output: {"text": "transcribed words", "confidence": 0.0-1.0}
#
# WHY mock first? If routing logic breaks, we know it's
# the routing — not a model loading issue. Test the plumbing
# before turning on the water.


class MockCanaryQwen:
    """
    Primary model — fast, good contextual understanding.
    In production: nvidia/canary-qwen-2.5b loaded via NVIDIA NeMo.
    Confidence varies based on audio quality.
    """
    def __init__(self):
        self.name = "Canary-Qwen-2.5B"
        self.loaded = False

    def load(self):
        """Simulate loading model into GPU memory (~30-60 seconds on real GPU)"""
        print(f"[MODEL] Loading {self.name}...")
        self.loaded = True
        print(f"[MODEL] {self.name} ready")

    def transcribe(self, audio_bytes: bytes) -> dict:
        """
        Simulate transcription.
        Real model: processes audio waveform through neural network,
        outputs text + per-word probabilities, averages to confidence.
        """
        if not self.loaded:
            raise RuntimeError(f"{self.name} not loaded! Call .load() first")

        # Simulate: sometimes confident, sometimes not
        # In reality, confidence depends on audio quality (noise, clarity)
        confidence = random.uniform(0.5, 0.95)
        return {
            "text": f"[MOCK Canary] Transcribed {len(audio_bytes)} bytes",
            "confidence": round(confidence, 2),
            "model": self.name
        }


class MockWhisperLargeV3:
    """
    Heavy-duty fallback — extremely robust against noise.
    In production: openai/whisper-large-v3 loaded via HuggingFace.
    Slower but handles bad audio that Canary can't.
    """
    def __init__(self):
        self.name = "Whisper-Large-v3"
        self.loaded = False

    def load(self):
        print(f"[MODEL] Loading {self.name}...")
        self.loaded = True
        print(f"[MODEL] {self.name} ready")

    def transcribe(self, audio_bytes: bytes) -> dict:
        if not self.loaded:
            raise RuntimeError(f"{self.name} not loaded! Call .load() first")

        # Whisper is more robust — consistently higher confidence
        confidence = random.uniform(0.75, 0.98)
        return {
            "text": f"[MOCK Whisper] Transcribed {len(audio_bytes)} bytes",
            "confidence": round(confidence, 2),
            "model": self.name
        }


# ============================================
# THE ROUTER — The Decision Maker
# ============================================
# This is the core logic that NEVER changes between
# mock and production. Models get swapped; routing stays.

class TranscriptionRouter:
    """
    Routes audio to the right model based on confidence.

    Logic (two-level if-else, NOT a waterfall):
        1. Send chunk to Canary
        2. If confidence >= threshold → return Canary result
        3. If confidence < threshold  → send to Whisper, return its result

    Parakeet is NOT part of this chain. It's a separate
    speed-priority option, not a third fallback tier.
    """
    def __init__(self):
        self.canary = MockCanaryQwen()
        self.whisper = MockWhisperLargeV3()

    def load_models(self):
        """Load all models into GPU memory. Called once at server startup."""
        self.canary.load()
        self.whisper.load()
        print("[ROUTER] All models loaded and ready")

    def transcribe(self, audio_bytes: bytes) -> dict:
        """
        Route audio through confidence-based system.
        Returns dict with: text, confidence, model used, was_fallback
        """
        # Step 1: Always try Canary first (fast, primary)
        canary_result = self.canary.transcribe(audio_bytes)

        # Step 2: Check confidence against threshold
        if canary_result["confidence"] >= CONFIDENCE_THRESHOLD:
            # Canary is confident — use its result, done
            return {
                "text": canary_result["text"],
                "confidence": canary_result["confidence"],
                "model_used": canary_result["model"],
                "was_fallback": False
            }
        else:
            # Canary is NOT confident — fall back to Whisper
            print(f"[ROUTER] Canary confidence {canary_result['confidence']} "
                  f"< {CONFIDENCE_THRESHOLD}, falling back to Whisper")
            whisper_result = self.whisper.transcribe(audio_bytes)
            return {
                "text": whisper_result["text"],
                "confidence": whisper_result["confidence"],
                "model_used": whisper_result["model"],
                "was_fallback": True
            }
