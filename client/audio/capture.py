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
import struct
import threading
import queue
import io
import time


# ============================================
# CONFIGURATION
# ============================================

# Target sample rate — what the server's speech models expect
# All audio is resampled to this rate before sending
TARGET_SAMPLE_RATE = 16000  # 16kHz — standard for speech recognition models

CHUNK_DURATION = 0.5      # Each chunk = 0.5 seconds of audio

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

def is_speech(audio_chunk_bytes: bytes, num_channels: int = 1) -> bool:
    """
    Determine if an audio chunk contains speech or silence.

    Args:
        audio_chunk_bytes: Raw audio bytes from WASAPI capture
        num_channels: Number of audio channels (1=mono, 2=stereo).
            If stereo, channels are averaged to mono before checking energy.

    Returns:
        True if speech detected, False if silence
    """
    # Convert raw bytes to numpy array of numbers
    # Each number represents one audio sample (amplitude at that moment)
    audio_data = np.frombuffer(audio_chunk_bytes, dtype=np.int16)

    # If stereo, reshape to (samples, channels) and average to mono
    # Stereo interleaves: [L0, R0, L1, R1, L2, R2, ...]
    # Reshape gives: [[L0, R0], [L1, R1], ...] → average across columns → mono
    if num_channels > 1:
        audio_data = audio_data.reshape(-1, num_channels).mean(axis=1)

    # Calculate RMS (Root Mean Square) energy
    # RMS = square root of the average of squared values
    # This gives us a single number representing "how loud" this chunk is
    # WHY RMS not just average? Audio oscillates positive/negative,
    # raw average would cancel out to near zero. Squaring makes all positive.
    energy = np.sqrt(np.mean(audio_data.astype(np.float64) ** 2))

    return energy > VAD_ENERGY_THRESHOLD


def resample_audio(audio_bytes: bytes, native_rate: int, target_rate: int,
                   num_channels: int) -> bytes:
    """
    Resample audio from native_rate to target_rate and convert to mono.

    Uses NumPy linear interpolation — sufficient quality for speech audio
    going into a transcription model. No extra dependencies required.

    Steps:
        1. Convert raw bytes → numpy int16 array
        2. If stereo → average channels to mono
        3. Create new sample positions at the target rate
        4. Interpolate original samples onto new positions
        5. Convert back to int16 bytes

    Args:
        audio_bytes: Raw audio bytes (int16 format)
        native_rate: Sample rate the audio was captured at (e.g., 48000)
        target_rate: Sample rate we want (16000 for speech models)
        num_channels: Number of channels in the input audio

    Returns:
        Resampled mono audio as raw int16 bytes
    """
    # Step 1: bytes → numpy array
    audio_data = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float64)

    # Step 2: stereo → mono (average left and right channels)
    if num_channels > 1:
        audio_data = audio_data.reshape(-1, num_channels).mean(axis=1)

    # Step 3: if rates already match, just return mono audio
    if native_rate == target_rate:
        return audio_data.astype(np.int16).tobytes()

    # Step 4: calculate resampling ratio and create new sample positions
    # Example: native=48000, target=16000 → ratio = 0.333...
    # For 24000 native samples (0.5s at 48kHz), we want 8000 target samples (0.5s at 16kHz)
    num_samples = len(audio_data)
    target_length = int(num_samples * target_rate / native_rate)

    # Original sample positions: [0, 1, 2, ..., num_samples-1]
    original_positions = np.arange(num_samples)

    # Target sample positions: evenly spaced points that map back to original positions
    # linspace(0, num_samples-1, target_length) creates target_length points
    # spread evenly across the original range
    target_positions = np.linspace(0, num_samples - 1, target_length)

    # Step 5: interpolate — find the value at each target position
    # np.interp does linear interpolation: for each target position,
    # it finds the two nearest original samples and draws a straight line between them,
    # then reads the value at the target position on that line
    resampled = np.interp(target_positions, original_positions, audio_data)

    # Step 6: convert back to int16 bytes (same format the server expects)
    return resampled.astype(np.int16).tobytes()


def pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 16000,
               num_channels: int = 1, bits_per_sample: int = 16) -> bytes:
    """
    Wrap raw PCM audio bytes in a WAV file header.

    A WAV file is just a 44-byte header followed by the raw audio data.
    The header tells the reader everything it needs to interpret the bytes:
    sample rate, number of channels, bit depth, and data size.

    This is why Option B is reliable forever: the server reads the header
    and adapts automatically. If the client changes sample rate, channels,
    or bit depth, the header carries that information along with the audio.

    WAV header structure (44 bytes total):
        Bytes 0-3:   "RIFF" — file type identifier
        Bytes 4-7:   file size - 8 (total size minus the RIFF header itself)
        Bytes 8-11:  "WAVE" — format identifier
        Bytes 12-15: "fmt " — format chunk marker
        Bytes 16-19: 16 — size of the format chunk (always 16 for PCM)
        Bytes 20-21: 1 — audio format (1 = PCM, uncompressed)
        Bytes 22-23: number of channels (1 = mono, 2 = stereo)
        Bytes 24-27: sample rate (e.g., 16000)
        Bytes 28-31: byte rate (sample_rate * channels * bytes_per_sample)
        Bytes 32-33: block align (channels * bytes_per_sample)
        Bytes 34-35: bits per sample (e.g., 16)
        Bytes 36-39: "data" — data chunk marker
        Bytes 40-43: size of the audio data in bytes

    Args:
        pcm_bytes: Raw PCM audio data (int16 format)
        sample_rate: Sample rate in Hz (default 16000)
        num_channels: Number of channels (default 1 = mono)
        bits_per_sample: Bits per sample (default 16)

    Returns:
        Complete WAV file as bytes (44-byte header + pcm_bytes)
    """
    data_size = len(pcm_bytes)
    bytes_per_sample = bits_per_sample // 8
    byte_rate = sample_rate * num_channels * bytes_per_sample
    block_align = num_channels * bytes_per_sample

    # struct.pack() converts Python values into raw bytes in a specific layout
    # '<' = little-endian byte order (WAV standard)
    # '4s' = 4-byte string, 'I' = unsigned 32-bit int, 'H' = unsigned 16-bit int
    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF',                    # RIFF marker
        36 + data_size,             # File size minus 8 bytes (RIFF + size field)
        b'WAVE',                    # WAVE marker
        b'fmt ',                    # Format chunk marker (note trailing space)
        16,                         # Format chunk size (16 for PCM)
        1,                          # Audio format (1 = PCM)
        num_channels,               # Number of channels
        sample_rate,                # Sample rate
        byte_rate,                  # Byte rate
        block_align,                # Block align
        bits_per_sample,            # Bits per sample
        b'data',                    # Data chunk marker
        data_size                   # Size of audio data
    )

    return header + pcm_bytes


# ============================================
# WASAPI LOOPBACK AUDIO CAPTURER
# ============================================
# Taps into Windows Audio Session API to capture
# a copy of whatever audio is playing through speakers.
#
# WASAPI loopback devices only support their native sample rate
# (whatever rate the Windows audio engine runs at — usually 48000 or 44100 Hz).
# We open the stream at the native rate, then resample each chunk down
# to 16000 Hz (what speech models expect) before putting it in the queue.

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

        # Native device properties — set when stream opens
        self.native_rate = None          # Device's native sample rate (e.g., 48000)
        self.native_channels = None      # Device's native channel count (e.g., 2 for stereo)
        self.native_chunk_size = None    # Chunk size in samples at native rate

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

        Returns:
            dict: PyAudio device info including 'index', 'defaultSampleRate',
                  and 'maxInputChannels'
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
                    print(f"[AUDIO] Native sample rate: {int(device['defaultSampleRate'])} Hz")
                    print(f"[AUDIO] Native channels: {device['maxInputChannels']}")
                    return device

            raise RuntimeError("No WASAPI loopback device found. "
                             "Make sure you're on Windows with audio output enabled.")
        finally:
            p.terminate()

    def _capture_loop(self):
        """
        Runs in background thread. Continuously reads audio from WASAPI
        at the device's native rate, resamples to 16kHz mono, applies VAD,
        and puts speech chunks into the queue.
        """
        while self.is_running:
            try:
                # Read one chunk of audio from the stream (at native rate)
                audio_data = self.stream.read(
                    self.native_chunk_size, exception_on_overflow=False
                )

                # VAD gate: check energy BEFORE resampling (saves CPU on silent chunks)
                # Pass native channel count so VAD can average stereo to mono
                if is_speech(audio_data, num_channels=self.native_channels):
                    # Resample from native rate → 16kHz mono
                    resampled = resample_audio(
                        audio_data,
                        native_rate=self.native_rate,
                        target_rate=TARGET_SAMPLE_RATE,
                        num_channels=self.native_channels
                    )

                    # Wrap raw PCM in WAV header before sending
                    # This makes the server format-agnostic — it reads the
                    # header to learn sample rate, channels, and bit depth
                    # instead of hardcoding assumptions about the format
                    wav_chunk = pcm_to_wav(
                        resampled,
                        sample_rate=TARGET_SAMPLE_RATE,
                        num_channels=1,
                        bits_per_sample=16
                    )
                    self.audio_queue.put(wav_chunk)
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

        # Step 2: Read the device's native properties
        # WASAPI loopback only supports its native rate — we MUST use it
        # Resampling to 16kHz happens later in _capture_loop()
        self.native_rate = int(loopback_device["defaultSampleRate"])
        self.native_channels = int(loopback_device["maxInputChannels"])

        # Calculate chunk size at the NATIVE rate (not target rate)
        # 0.5 seconds at 48000 Hz = 24000 samples per chunk
        # (vs 8000 samples at 16000 Hz — 3x more data per chunk)
        self.native_chunk_size = int(self.native_rate * CHUNK_DURATION)

        print(f"[AUDIO] Opening stream: {self.native_rate} Hz, "
              f"{self.native_channels} ch, chunk={self.native_chunk_size} samples")

        # Step 3: Open the audio stream at the NATIVE rate
        self.audio = pyaudio.PyAudio()
        self.stream = self.audio.open(
            format=pyaudio.paInt16,           # 16-bit audio (standard for speech)
            channels=self.native_channels,    # Match device's channel count
            rate=self.native_rate,            # Match device's native sample rate
            input=True,                       # We're recording (input), not playing (output)
            input_device_index=loopback_device["index"],
            frames_per_buffer=self.native_chunk_size
        )

        # Step 4: Start capture in background thread
        # WHY background thread? If capture ran on main thread,
        # the UI would freeze while waiting for audio data.
        self.is_running = True
        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.capture_thread.start()
        print(f"[AUDIO] Capture started (native={self.native_rate} Hz → "
              f"target={TARGET_SAMPLE_RATE} Hz)")

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

        Audio is already resampled to 16kHz mono — ready to send to server.
        """
        try:
            return self.audio_queue.get(timeout=timeout)
        except queue.Empty:
            return None  # No speech in the last `timeout` seconds
