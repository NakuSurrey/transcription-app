# server/models/transcriber.py — AI Model Loading & Confidence-Based Routing
# Phase 3: REAL models replacing mocks

import io
import numpy as np
import soundfile as sf
import torch

# ============================================
# CONFIDENCE THRESHOLD — unchanged from mock
# ============================================
CONFIDENCE_THRESHOLD = 0.7


# ============================================
# REAL CANARY MODEL
# ============================================
class RealCanaryQwen:
    """
    Primary model — fast, good contextual understanding.
    Loaded via NVIDIA NeMo framework.
    """
    def __init__(self):
        self.name = "Canary-Qwen-2.5B"
        self.model = None  # will hold the NeMo model object

    def load(self):
        import nemo.collections.asr as nemo_asr
        print(f"[MODEL] Loading {self.name}...")

        # from_pretrained downloads weights if not cached,
        # or loads from cache if already downloaded by deploy.sh
        self.model = nemo_asr.models.ASRModel.from_pretrained(
            "nvidia/canary-1b"
        )

        # Move model weights into GPU memory
        # .cuda() = "put this on the GPU"
        # without this, inference runs on CPU — 100x slower
        self.model = self.model.cuda()

        # eval mode = inference only, no gradient tracking
        # training mode tracks gradients (needed for learning)
        # we're NOT training, so turn it off to save memory + speed
        self.model.eval()
        print(f"[MODEL] {self.name} ready")

    def transcribe(self, audio_bytes: bytes) -> dict:
        if self.model is None:
            raise RuntimeError(f"{self.name} not loaded!")

        # Step 1: bytes → numpy waveform
        audio_buffer = io.BytesIO(audio_bytes)
        waveform, sample_rate = sf.read(audio_buffer, dtype='float32')

        # Step 2: ensure mono (1 channel)
        # stereo audio has shape (samples, 2) — average the two channels
        if waveform.ndim == 2:
            waveform = waveform.mean(axis=1)

        # Step 3: NeMo expects 16000 Hz sample rate
        # if audio comes in at different rate, resample it
        if sample_rate != 16000:
            import librosa
            waveform = librosa.resample(
                waveform, orig_sr=sample_rate, target_sr=16000
            )

        # Step 4: transcribe
        # NeMo transcribe() accepts a list of waveforms
        # returns list of results — we take index [0]
        with torch.no_grad():  # no_grad = don't track gradients, saves memory
            output = self.model.transcribe([waveform], batch_size=1)

        text = output[0] if isinstance(output[0], str) else output[0].text

        # Step 5: Canary returns per-token log probabilities
        # convert to a 0-1 confidence score
        # if not available, default to 0.85 (Canary is generally reliable)
        try:
            confidence = float(torch.exp(
                torch.tensor(output[0].score)
            ).item())
            confidence = max(0.0, min(1.0, confidence))  # clamp to [0,1]
        except Exception:
            confidence = 0.85

        return {
            "text": text,
            "confidence": round(confidence, 2),
            "model": self.name
        }


# ============================================
# REAL WHISPER MODEL
# ============================================
class RealWhisperLargeV3:
    """
    Fallback model — extremely robust against noise.
    Loaded via HuggingFace transformers.
    """
    def __init__(self):
        self.name = "Whisper-Large-v3"
        self.model = None
        self.processor = None  # handles tokenization + feature extraction

    def load(self):
        from transformers import (
            WhisperForConditionalGeneration,
            WhisperProcessor
        )
        print(f"[MODEL] Loading {self.name}...")

        # Processor = tokenizer + feature extractor combined
        # converts raw waveform → mel spectrogram → model input
        self.processor = WhisperProcessor.from_pretrained(
            "openai/whisper-large-v3"
        )

        # The actual neural network weights
        self.model = WhisperForConditionalGeneration.from_pretrained(
            "openai/whisper-large-v3"
        )

        # Move to GPU + eval mode (same reasoning as Canary above)
        self.model = self.model.cuda()
        self.model.eval()
        print(f"[MODEL] {self.name} ready")

    def transcribe(self, audio_bytes: bytes) -> dict:
        if self.model is None:
            raise RuntimeError(f"{self.name} not loaded!")

        # Step 1: bytes → numpy waveform (same as Canary)
        audio_buffer = io.BytesIO(audio_bytes)
        waveform, sample_rate = sf.read(audio_buffer, dtype='float32')

        if waveform.ndim == 2:
            waveform = waveform.mean(axis=1)

        if sample_rate != 16000:
            import librosa
            waveform = librosa.resample(
                waveform, orig_sr=sample_rate, target_sr=16000
            )

        # Step 2: processor converts waveform → input tensors
        # return_tensors="pt" = return PyTorch tensors (not numpy)
        # .to("cuda") = move input tensors to GPU to match model
        inputs = self.processor(
            waveform,
            sampling_rate=16000,
            return_tensors="pt"
        ).input_features.to("cuda")

        # Step 3: generate transcription
        # output_scores=True = return token probabilities for confidence
        with torch.no_grad():
            outputs = self.model.generate(
                inputs,
                output_scores=True,
                return_dict_in_generate=True
            )

        # Step 4: decode token IDs back to text
        text = self.processor.batch_decode(
            outputs.sequences,
            skip_special_tokens=True  # remove <|startoftranscript|> etc
        )[0]

        # Step 5: compute confidence from token scores
        # each score is log probability of that token
        # exp(log_prob) = probability, average across all tokens
        try:
            scores = torch.stack(outputs.scores, dim=1)
            token_probs = torch.exp(scores.max(dim=-1).values)
            confidence = float(token_probs.mean().item())
            confidence = max(0.0, min(1.0, confidence))
        except Exception:
            confidence = 0.90

        return {
            "text": text.strip(),
            "confidence": round(confidence, 2),
            "model": self.name
        }


# ============================================
# THE ROUTER — identical to mock version
# Only change: MockCanaryQwen → RealCanaryQwen
#              MockWhisperLargeV3 → RealWhisperLargeV3
# ============================================
class TranscriptionRouter:
    def __init__(self):
        self.canary = RealCanaryQwen()      # ← only change
        self.whisper = RealWhisperLargeV3() # ← only change

    def load_models(self):
        self.canary.load()
        self.whisper.load()
        print("[ROUTER] All models loaded and ready")

    def transcribe(self, audio_bytes: bytes) -> dict:
        canary_result = self.canary.transcribe(audio_bytes)

        if canary_result["confidence"] >= CONFIDENCE_THRESHOLD:
            return {
                "text": canary_result["text"],
                "confidence": canary_result["confidence"],
                "model_used": canary_result["model"],
                "was_fallback": False
            }
        else:
            print(f"[ROUTER] Canary confidence {canary_result['confidence']} "
                  f"< {CONFIDENCE_THRESHOLD}, falling back to Whisper")
            whisper_result = self.whisper.transcribe(audio_bytes)
            return {
                "text": whisper_result["text"],
                "confidence": whisper_result["confidence"],
                "model_used": whisper_result["model"],
                "was_fallback": True
            }