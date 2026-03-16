# client/audio/capture.py — Live Audio Capture + VAD Filter
# Phase 3: The Ears (Live Mode)
#
# This module does TWO jobs:
#   1. Tap into WASAPI loopback to copy system audio (what speakers play)
#   2. Run VAD to filter out silence before sending anything to the server
#
# RUNS ON: Your Windows laptop (client-side)
# REQUIRES: pyaudiowpatch (pip install pyaudiowpatch)

import pyaudiowpatch as pyaudio
import numpy as np
import threading
import queue
import time


# ============================================
# CONFIGURATION
# ============================================

# Audio format settings
SAMPLE_RATE = 16000       # 16kHz — standard for speech recognition models
CHUNK_DURATION = 0.5      # Each chunk = 0.5 seconds of audio
CHANNELS = 1              # Mono — speech models expect single channel

# Calculate chunk size in samples
# SAMPLE_RATE * CHUNK_DURATION = how many audio samples per chunk
# 16000 * 0.5 = 8000 samples per chunk
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION)

# VAD (Voice Activity Detection) settings
VAD_ENERGY_THRESHOLD = 500  # Audio energy above this = speech, below = silence
                             # Tunable — increase if VAD triggers on background noise
                             # Decrease if VAD misses quiet speech


# ============================================
# VOICE ACTIVITY DETECTOR (VAD)
# ============================================
# The bandwidth saver. Checks each audio chunk:
#   Speech detected → let it through
#   Silence detected → throw it away, don't waste bandwidth
#
# HOW IT WORKS:
#   Measures "energy" of the audio signal.
#   Speech = big peaks and valleys in the waveform = high energy
#   Silence = nearly flat line = low energy
#   Compare energy to threshold → decide speech or silence

def is_speech(audio_chunk_bytes: bytes) -> bool:
    """
    Determine if an audio chunk contains speech or silence.

    Args:
        audio_chunk_bytes: Raw audio bytes from WASAPI capture

    Returns:
        True if speech detected, False if silence
    """
    # Convert raw bytes to numpy array of numbers
    # Each number represents one audio sample (amplitude at that moment)
    audio_data = np.frombuffer(audio_chunk_bytes, dtype=np.int16)

    # Calculate RMS (Root Mean Square) energy
    # RMS = square root of the average of squared values
    # This gives us a single number representing "how loud" this chunk is
    # WHY RMS not just average? Audio oscillates positive/negative,
    # raw average would cancel out to near zero. Squaring makes all positive.
    energy = np.sqrt(np.mean(audio_data.astype(np.float64) ** 2))

    return energy > VAD_ENERGY_THRESHOLD


# ============================================
# WASAPI LOOPBACK AUDIO CAPTURER
# ============================================
# Taps into Windows Audio Session API to capture
# a copy of whatever audio is playing through speakers.
#
# Think of it as a T-junction in a water pipe:
#   Water flows to faucet (speakers)
#   A branch splits off a copy to your bucket (this code)

class AudioCapturer:
    """
    Captures system audio via WASAPI loopback on Windows.

    Usage:
        capturer = AudioCapturer()
        capturer.start()

        # Get speech-only audio chunks (silence already filtered)
        while True:
            chunk = capturer.get_chunk()  # blocks until speech available
            if chunk:
                send_to_server(chunk)

        capturer.stop()
    """

    def __init__(self):
        self.audio = None               # PyAudio instance
        self.stream = None               # Audio stream from WASAPI
        self.is_running = False          # Flag to control capture loop
        self.capture_thread = None       # Background thread for capture

        # Thread-safe queue to pass audio chunks from capture thread to main thread
        # WHY a queue? Capture runs in a background thread (so it doesn't block UI).
        # Main thread reads chunks from queue when ready to send to server.
        # Queue = safe way for two threads to pass data without conflicts.
        self.audio_queue = queue.Queue()

    def _find_loopback_device(self):
        """
        Find the WASAPI loopback device for the default speakers.

        WASAPI loopback = capture what speakers are outputting.
        We need to find the correct device ID for the default output.
        """
        p = pyaudio.PyAudio()

        try:
            # Get the default speaker device info
            wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)

            # Get the default output (speaker) device
            default_speakers = p.get_device_info_by_index(
                wasapi_info["defaultOutputDevice"]
            )

            # Find the loopback version of this device
            # Loopback device = same as speakers but captures instead of plays
            for i in range(p.get_device_count()):
                device = p.get_device_info_by_index(i)
                if (device.get("name", "").find(default_speakers["name"]) != -1
                        and device.get("isLoopbackDevice", False)):
                    print(f"[AUDIO] Found loopback device: {device['name']}")
                    return device

            raise RuntimeError("No WASAPI loopback device found. "
                             "Make sure you're on Windows with audio output enabled.")
        finally:
            p.terminate()

    def _capture_loop(self):
        """
        Runs in background thread. Continuously reads audio from WASAPI,
        applies VAD, and puts speech chunks into the queue.
        """
        while self.is_running:
            try:
                # Read one chunk of audio from the stream
                audio_data = self.stream.read(CHUNK_SIZE, exception_on_overflow=False)

                # VAD gate: only let speech through
                if is_speech(audio_data):
                    self.audio_queue.put(audio_data)
                # If silence, we do NOTHING — chunk is discarded
                # This is where bandwidth savings happen

            except Exception as e:
                if self.is_running:  # Only print if we didn't intentionally stop
                    print(f"[AUDIO] Capture error: {e}")
                break

    def start(self):
        """Start capturing system audio."""
        if self.is_running:
            print("[AUDIO] Already capturing")
            return

        # Step 1: Find the loopback device
        loopback_device = self._find_loopback_device()

        # Step 2: Open the audio stream
        self.audio = pyaudio.PyAudio()
        self.stream = self.audio.open(
            format=pyaudio.paInt16,      # 16-bit audio (standard for speech)
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,                   # We're recording (input), not playing (output)
            input_device_index=loopback_device["index"],
            frames_per_buffer=CHUNK_SIZE
        )

        # Step 3: Start capture in background thread
        # WHY background thread? If capture ran on main thread,
        # the UI would freeze while waiting for audio data.
        self.is_running = True
        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.capture_thread.start()
        print("[AUDIO] Capture started")

    def stop(self):
        """Stop capturing and clean up resources."""
        self.is_running = False

        if self.capture_thread:
            self.capture_thread.join(timeout=2)  # Wait up to 2 sec for thread to finish

        if self.stream:
            self.stream.stop_stream()
            self.stream.close()

        if self.audio:
            self.audio.terminate()

        print("[AUDIO] Capture stopped")

    def get_chunk(self, timeout=1.0):
        """
        Get next speech audio chunk from the queue.
        Returns None if no speech detected within timeout.

        This is what the network module calls to get audio to send.
        """
        try:
            return self.audio_queue.get(timeout=timeout)
        except queue.Empty:
            return None  # No speech in the last `timeout` seconds
