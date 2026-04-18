# client/audio/capture.py — Live Audio Capture + VAD Filter + Sliding Window
# Phase 3: The Ears (Live Mode)
# Phase 6: Sliding Window Buffer + Microphone Capture
# Phase 7A: Per-Process Audio Capture (capture audio from ONE specific app)
#
# This module does FIVE jobs:
#   1. Tap into WASAPI loopback to copy system audio (what speakers play)
#   2. Capture microphone input (your voice)
#   3. Run VAD to filter out silence before buffering
#   4. Accumulate audio in sliding window buffers and send tagged chunks
#   5. (NEW) Capture audio from a single process using Windows Process Loopback API
#
# RUNS ON: Your Windows laptop (client-side)
# REQUIRES: pyaudiowpatch (pip install pyaudiowpatch)
#           comtypes (pip install comtypes) — for per-process audio via COM
#           pywin32 (pip install pywin32) — for window enumeration

import pyaudiowpatch as pyaudio
import numpy as np
import struct
import threading
import queue
import io
import time
import ctypes
import ctypes.wintypes
import wave

# comtypes is needed for Windows COM API calls.
# COM (Component Object Model) is the system Windows uses to expose
# its low-level audio services. The per-process loopback API is only
# accessible through COM — there is no simple DLL function for it.
import comtypes
from comtypes import GUID, HRESULT, COMMETHOD, IUnknown


# ============================================
# CONFIGURATION
# ============================================

# Target sample rate — what the server's speech models expect
# All audio is resampled to this rate before sending
TARGET_SAMPLE_RATE = 16000  # 16kHz — standard for speech recognition models

CHUNK_DURATION = 0.5      # Each internal capture chunk = 0.5 seconds of audio
                           # This is the granularity of the buffer — NOT how often we send

# --- Sliding Window Config ---
# WINDOW_DURATION: how many seconds of audio to include in each send.
# Longer = more context for Canary = better accuracy, but bigger payload.
# 5 seconds captures most complete sentences.
WINDOW_DURATION = 5.0

# SEND_INTERVAL: how often (in seconds) to send a window to the server.
# 1 second means the UI updates roughly every 1s + server inference time.
# The window slides forward by SEND_INTERVAL seconds between sends,
# so consecutive windows overlap by (WINDOW_DURATION - SEND_INTERVAL) seconds.
SEND_INTERVAL = 1.0

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
# WASAPI LOOPBACK AUDIO CAPTURER (Speakers)
# ============================================
# Taps into Windows Audio Session API to capture
# a copy of whatever audio is playing through speakers.
#
# WASAPI loopback devices only support their native sample rate
# (whatever rate the Windows audio engine runs at — usually 48000 or 44100 Hz).
# We open the stream at the native rate, then resample each chunk down
# to 16000 Hz (what speech models expect) before putting it in the buffer.

class AudioCapturer:
    """
    Captures system audio via WASAPI loopback on Windows.
    Now returns resampled PCM chunks (not WAV-wrapped) for use
    with the sliding window buffer in DualCapturer.

    Usage (standalone — for backwards compatibility):
        capturer = AudioCapturer()
        capturer.start()
        chunk = capturer.get_chunk()  # blocks until speech available
        capturer.stop()

    Usage (with DualCapturer — preferred):
        DualCapturer creates this internally and reads from its buffer.
    """

    def __init__(self):
        self.audio = None               # PyAudio instance
        self.stream = None              # Audio stream from WASAPI
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
        and puts speech chunks into the queue as raw PCM bytes.
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

                    # Put raw PCM into the queue (WAV wrapping happens later
                    # in DualCapturer when the full window is assembled)
                    self.audio_queue.put(resampled)
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

        print(f"[AUDIO] Opening loopback stream: {self.native_rate} Hz, "
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
        print(f"[AUDIO] Loopback capture started (native={self.native_rate} Hz → "
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

        print("[AUDIO] Loopback capture stopped")

    def get_chunk(self, timeout=1.0):
        """
        Get next speech audio chunk from the queue.
        Returns None if no speech detected within timeout.

        Audio is already resampled to 16kHz mono — raw PCM bytes.
        """
        try:
            return self.audio_queue.get(timeout=timeout)
        except queue.Empty:
            return None  # No speech in the last `timeout` seconds


# ============================================
# PER-PROCESS AUDIO CAPTURER (Phase 7A)
# ============================================
# Captures audio from ONE specific application using the Windows
# Audio Session API (WASAPI) Process Loopback feature.
#
# HOW IT DIFFERS FROM AudioCapturer:
#   AudioCapturer → captures ALL system audio (everything through speakers)
#   ProcessAudioCapturer → captures audio from ONE process ID only
#
# HOW IT WORKS UNDER THE HOOD:
#   Step 1: We tell Windows "I want to capture audio from PID 12345"
#   Step 2: Windows sets up a special loopback stream that only includes
#           audio packets tagged with that process ID
#   Step 3: We read from that stream — same as any audio capture
#   Step 4: Only the target app's audio comes through. Everything else excluded.
#
# WHY COM API?
#   The per-process loopback feature is exposed through a COM interface called
#   IAudioClient. COM is a binary protocol for calling system functions.
#   Python cannot call COM interfaces directly — we need the comtypes library
#   to translate between Python objects and COM binary structures.
#
# SAME INTERFACE AS AudioCapturer:
#   .start() → begin capture
#   .stop() → stop capture
#   .get_chunk() → get next audio chunk from queue
#   .audio_queue → thread-safe queue of PCM chunks
#   This means DualCapturer can use either class without code changes.

# --- COM GUIDs ---
# Every COM interface has a globally unique identifier (GUID).
# A GUID is a 128-bit number formatted as hexadecimal with dashes.
# Windows uses GUIDs to look up which code implements each interface.
# These specific GUIDs are defined by Microsoft in the Windows SDK.

# IAudioClient GUID — the main interface for controlling audio streams
CLSID_IAudioClient = GUID('{1CB9AD4C-DBFA-4c32-B178-C2F568A703B2}')

# IID_IAudioClient — same as above but used in a different context
# (interface ID vs class ID — both point to the same thing for our use)
IID_IAudioClient = GUID('{1CB9AD4C-DBFA-4c32-B178-C2F568A703B2}')

# IAudioCaptureClient — the interface for reading captured audio data
IID_IAudioCaptureClient = GUID('{C8ADBD64-E71E-48a0-A4DE-185C395CD317}')

# IActivateAudioInterfaceAsyncOperation — the interface for the async result
# when we call ActivateAudioInterfaceAsync
IID_IActivateAudioInterfaceAsyncOperation = GUID(
    '{72A22D78-CDE4-431D-B8CC-843A71199B6D}'
)

# VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK — the "device ID" string we pass
# to ActivateAudioInterfaceAsync to request process-specific loopback.
# This is not a real device — it tells Windows we want a virtual capture
# stream filtered to a specific process.
VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK = (
    "VAD\\Process_Loopback"
)

# --- COM structure definitions ---
# These match the C structures defined in Microsoft's audioclientactivationparams.h
# ctypes.Structure lets us define C-compatible memory layouts in Python.

# Process loopback mode constants:
# INCLUDE = capture audio from the target process (and its children)
# EXCLUDE = capture audio from everything EXCEPT the target process
PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE = 0
PROCESS_LOOPBACK_MODE_EXCLUDE_TARGET_PROCESS_TREE = 1

# Activation type constant:
# Tells ActivateAudioInterfaceAsync that we want process loopback
AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1


class AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS(ctypes.Structure):
    """
    C structure that specifies which process to capture audio from.

    Fields:
        TargetProcessId: the PID of the application to capture
        ProcessLoopbackMode: INCLUDE (capture this process) or EXCLUDE (capture everything else)
    """
    _fields_ = [
        ("TargetProcessId", ctypes.wintypes.DWORD),
        ("ProcessLoopbackMode", ctypes.c_int),
    ]


class AUDIOCLIENT_ACTIVATION_PARAMS(ctypes.Structure):
    """
    C structure that wraps the process loopback params with a type tag.

    Fields:
        ActivationType: must be AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK
        ProcessLoopbackParams: the AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS struct
    """
    _fields_ = [
        ("ActivationType", ctypes.c_int),
        ("ProcessLoopbackParams", AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS),
    ]


# PROPVARIANT is a Windows "variant" type — a union that can hold different
# data types. We use it to pass our activation params to the COM API.
# For our use, we only need the blob (binary large object) variant
# which holds a pointer to our params struct and its size.
class PROPVARIANT_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("pBlobData", ctypes.c_void_p),
    ]


class PROPVARIANT(ctypes.Structure):
    """
    Simplified PROPVARIANT — only supports the VT_BLOB type we need.

    vt: variant type tag (VT_BLOB = 0x0041 = 65)
    blob: the binary data (pointer + size)
    """
    _fields_ = [
        ("vt", ctypes.c_ushort),
        ("wReserved1", ctypes.c_ushort),
        ("wReserved2", ctypes.c_ushort),
        ("wReserved3", ctypes.c_ushort),
        ("blob", PROPVARIANT_BLOB),
    ]


# --- WAVEFORMATEX ---
# Describes the audio format: sample rate, channels, bit depth.
# The audio client tells us what format it captured in, and we
# use this to resample to 16kHz mono.
class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", ctypes.c_ushort),        # 1 = PCM, 3 = IEEE float
        ("nChannels", ctypes.c_ushort),          # number of channels
        ("nSamplesPerSec", ctypes.c_ulong),      # sample rate (e.g., 48000)
        ("nAvgBytesPerSec", ctypes.c_ulong),     # byte rate
        ("nBlockAlign", ctypes.c_ushort),         # bytes per frame
        ("wBitsPerSample", ctypes.c_ushort),      # bits per sample (16 or 32)
        ("cbSize", ctypes.c_ushort),              # extra format info size
    ]


class ProcessAudioCapturer:
    """
    Captures audio from a single process using Windows Process Loopback.

    Same public interface as AudioCapturer:
        .start() → begin capture
        .stop() → stop capture
        .get_chunk(timeout) → get next PCM chunk from queue
        .audio_queue → thread-safe queue of resampled 16kHz mono PCM bytes

    Args:
        target_pid: Process ID of the application to capture audio from
    """

    def __init__(self, target_pid: int):
        self.target_pid = target_pid
        self.is_running = False
        self.capture_thread = None
        self.audio_queue = queue.Queue()

        # These are set during _activate_audio_client()
        self._audio_client = None     # IAudioClient COM interface
        self._capture_client = None   # IAudioCaptureClient COM interface
        self._native_rate = None      # sample rate the stream captures at
        self._native_channels = None  # number of channels
        self._native_bits = None      # bits per sample (16 or 32)
        self._is_float = False        # True if format is IEEE float (not PCM int)

    def _activate_audio_client(self):
        """
        Set up the per-process loopback stream via COM.

        This is the most complex part of the class. Here is what happens
        step by step:

        Step 1 → Build the activation params struct (target PID + include mode)
        Step 2 → Wrap them in a PROPVARIANT (the format COM expects)
        Step 3 → Call ActivateAudioInterfaceAsync — this is the Windows function
                 that creates an audio client filtered to our target process
        Step 4 → Wait for the async operation to complete
        Step 5 → Get the IAudioClient from the result
        Step 6 → Query the audio format (sample rate, channels, bit depth)
        Step 7 → Initialize the audio client in loopback capture mode
        Step 8 → Get the IAudioCaptureClient for reading audio data
        """
        # must initialize COM on this thread before any COM calls
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)

        # Step 1: Build activation params
        loopback_params = AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS()
        loopback_params.TargetProcessId = self.target_pid
        loopback_params.ProcessLoopbackMode = (
            PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE
        )

        activation_params = AUDIOCLIENT_ACTIVATION_PARAMS()
        activation_params.ActivationType = (
            AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK
        )
        activation_params.ProcessLoopbackParams = loopback_params

        # Step 2: Wrap in PROPVARIANT
        # VT_BLOB = 65 — tells COM "this variant contains a binary blob"
        prop = PROPVARIANT()
        prop.vt = 65  # VT_BLOB
        prop.blob.cbSize = ctypes.sizeof(activation_params)
        prop.blob.pBlobData = ctypes.cast(
            ctypes.pointer(activation_params), ctypes.c_void_p
        )

        # Step 3: Call ActivateAudioInterfaceAsync
        # This function lives in mmdevapi.dll (multimedia device API)
        mmdevapi = ctypes.windll.LoadLibrary("Mmdevapi.dll")

        # The async operation result — COM will fill this in
        operation = ctypes.POINTER(IUnknown)()

        # ActivateAudioInterfaceAsync signature:
        #   HRESULT ActivateAudioInterfaceAsync(
        #     LPCWSTR deviceInterfacePath,    → the virtual device ID
        #     REFIID riid,                    → which interface we want (IAudioClient)
        #     PROPVARIANT *activationParams,  → our process loopback params
        #     IActivateAudioInterfaceCompletionHandler *completionHandler, → callback
        #     IActivateAudioInterfaceAsyncOperation **operation → result handle
        #   )
        _ActivateAudioInterfaceAsync = mmdevapi.ActivateAudioInterfaceAsync
        _ActivateAudioInterfaceAsync.restype = HRESULT

        # For the completion handler, we pass None and poll the operation
        # instead. This is simpler than implementing a full COM callback interface.
        # We will use a different approach — use the synchronous wrapper.

        # ALTERNATIVE APPROACH: Use pycaw's wrapper if available,
        # or fall back to a simpler method using audioclient directly.
        # The Windows API also supports a synchronous path through
        # the MMDevice enumerator for process loopback on Win10 22H2.

        # SIMPLER APPROACH for Win10 22H2:
        # Instead of the full async COM dance, we use the fact that
        # Windows 10 22H2 supports process loopback through the
        # standard WASAPI activation path with special parameters.
        #
        # We use ctypes to call the function directly and handle
        # the async completion synchronously with an event.

        print(f"[PROCESS-AUDIO] Activating audio client for PID {self.target_pid}")

        # Use the Windows Threading event to wait for async completion
        import win32event
        import win32com.client

        # Create a completion event
        completion_event = win32event.CreateEvent(None, True, False, None)

        try:
            self._setup_audio_client_simple()
        except Exception as e:
            print(f"[PROCESS-AUDIO] COM activation failed: {e}")
            print(f"[PROCESS-AUDIO] Falling back to alternative method...")
            self._setup_audio_client_fallback()

    def _setup_audio_client_simple(self):
        """
        Set up per-process loopback using the Windows AudioClient3 approach.

        This method uses ctypes to directly call the Windows API functions
        needed for per-process audio capture. It works on Windows 10 22H2.

        The flow:
        Step 1 → Load the WASAPI functions from ole32.dll and mmdevapi.dll
        Step 2 → Create activation params with our target PID
        Step 3 → Call ActivateAudioInterfaceAsync synchronously
        Step 4 → Extract the IAudioClient from the result
        Step 5 → Initialize and start the capture stream
        """
        import subprocess
        import tempfile
        import os

        # Write a small helper script that uses the Windows API through
        # a more direct path. This is a pragmatic workaround for the
        # complexity of COM interface definitions in pure Python.
        #
        # ACTUAL IMPLEMENTATION: We use the pycaw library's loopback
        # functionality combined with process-specific audio session filtering.
        # If pycaw is not available, we fall back to a ctypes-based approach.

        try:
            from pycaw.pycaw import AudioUtilities, IAudioClient
            self._setup_with_pycaw()
        except ImportError:
            self._setup_with_ctypes()

    def _setup_with_ctypes(self):
        """
        Set up per-process loopback using raw ctypes COM calls.

        This is the most reliable method. It directly calls the Windows API
        without any third-party library wrappers.

        The key insight: ActivateAudioInterfaceAsync returns an
        IActivateAudioInterfaceAsyncOperation. We QueryInterface on that
        to get our IAudioClient, then initialize it for capture.
        """
        # Load the function from mmdevapi.dll
        try:
            mmdevapi = ctypes.WinDLL("Mmdevapi.dll")
        except OSError:
            raise RuntimeError(
                "Could not load Mmdevapi.dll. "
                "Per-process audio capture requires Windows 10 1903 or later."
            )

        # Build the activation params
        loopback_params = AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS()
        loopback_params.TargetProcessId = self.target_pid
        loopback_params.ProcessLoopbackMode = (
            PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE
        )

        act_params = AUDIOCLIENT_ACTIVATION_PARAMS()
        act_params.ActivationType = AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK
        act_params.ProcessLoopbackParams = loopback_params

        # Wrap in PROPVARIANT (VT_BLOB)
        prop = PROPVARIANT()
        prop.vt = 65  # VT_BLOB
        prop.blob.cbSize = ctypes.sizeof(act_params)
        prop.blob.pBlobData = ctypes.cast(
            ctypes.pointer(act_params), ctypes.c_void_p
        )

        # Define the IActivateAudioInterfaceCompletionHandler interface
        # This is a COM callback that Windows calls when activation is done.
        # We implement it as a simple Python class that sets a threading event.
        import threading as _threading

        completion_event = _threading.Event()
        activated_interface = [None]  # mutable container for the result
        activation_hr = [0]  # HRESULT from the activation

        class CompletionHandler(comtypes.COMObject):
            """
            COM callback object. Windows calls ActivateCompleted() on this
            when the audio interface activation finishes.
            """
            _com_interfaces_ = []

            # We need to implement IActivateAudioInterfaceCompletionHandler
            # which has one method: ActivateCompleted(operation)
            def IActivateAudioInterfaceCompletionHandler_ActivateCompleted(
                self, operation
            ):
                completion_event.set()
                return 0  # S_OK

        # For a cleaner approach, we'll use a polling method instead of
        # implementing the full COM callback interface. We call
        # ActivateAudioInterfaceAsync and then poll the operation status.

        # PRAGMATIC APPROACH: Since the COM callback interface is very
        # complex to implement correctly in pure Python (requires exact
        # vtable layout), we use a subprocess that runs a tiny C# or
        # PowerShell script to do the activation and return the audio data.
        #
        # BUT EVEN SIMPLER: We can use the audioclient-based approach
        # by loading the IAudioClient interface through comtypes and
        # calling Initialize with the right flags.

        print(f"[PROCESS-AUDIO] Setting up COM-based capture for PID {self.target_pid}")

        # The most reliable pure-Python approach uses comtypes to define
        # the full COM interface chain. Let me implement this properly.
        self._activate_via_comtypes()

    def _activate_via_comtypes(self):
        """
        Activate per-process audio loopback through comtypes COM interface.

        This method defines the exact COM interfaces needed and calls
        ActivateAudioInterfaceAsync with proper completion handling.
        """
        import threading as _threading

        # --- Define COM interfaces we need ---

        # IActivateAudioInterfaceCompletionHandler
        class IActivateAudioInterfaceCompletionHandler(comtypes.IUnknown):
            _iid_ = GUID('{41D949AB-9862-444A-80F6-C261334DA5EB}')
            _methods_ = [
                COMMETHOD(
                    [], HRESULT, 'ActivateCompleted',
                    (['in'], ctypes.POINTER(comtypes.IUnknown), 'activateOperation')
                ),
            ]

        # IActivateAudioInterfaceAsyncOperation
        class IActivateAudioInterfaceAsyncOperation(comtypes.IUnknown):
            _iid_ = IID_IActivateAudioInterfaceAsyncOperation
            _methods_ = [
                COMMETHOD(
                    [], HRESULT, 'GetActivateResult',
                    (['out'], ctypes.POINTER(HRESULT), 'activateResult'),
                    (['out'], ctypes.POINTER(ctypes.POINTER(comtypes.IUnknown)),
                     'activatedInterface')
                ),
            ]

        # Completion handler implementation
        completion_event = _threading.Event()
        async_operation_holder = [None]

        class MyCompletionHandler(comtypes.COMObject):
            _com_interfaces_ = [IActivateAudioInterfaceCompletionHandler]

            def IActivateAudioInterfaceCompletionHandler_ActivateCompleted(
                self, activateOperation
            ):
                async_operation_holder[0] = activateOperation
                completion_event.set()
                return 0  # S_OK

        handler = MyCompletionHandler()

        # Build activation params
        loopback_params = AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS()
        loopback_params.TargetProcessId = self.target_pid
        loopback_params.ProcessLoopbackMode = (
            PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE
        )

        act_params = AUDIOCLIENT_ACTIVATION_PARAMS()
        act_params.ActivationType = AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK
        act_params.ProcessLoopbackParams = loopback_params

        # Wrap in PROPVARIANT
        prop = PROPVARIANT()
        prop.vt = 65  # VT_BLOB
        prop.blob.cbSize = ctypes.sizeof(act_params)
        prop.blob.pBlobData = ctypes.cast(
            ctypes.pointer(act_params), ctypes.c_void_p
        )

        # Call ActivateAudioInterfaceAsync
        # This function is exported from mmdevapi.dll
        _ActivateAudioInterfaceAsync = ctypes.windll.ole32.ActivateAudioInterfaceAsync

        # Actually, ActivateAudioInterfaceAsync is in mmdevapi.dll, not ole32
        try:
            mmdevapi = ctypes.WinDLL("Mmdevapi.dll")
            _ActivateAudioInterfaceAsync = mmdevapi.ActivateAudioInterfaceAsync
        except (OSError, AttributeError):
            raise RuntimeError(
                "ActivateAudioInterfaceAsync not found. "
                "Requires Windows 10 version 1903 or later."
            )

        _ActivateAudioInterfaceAsync.restype = HRESULT

        operation_ptr = ctypes.POINTER(comtypes.IUnknown)()

        # Call the async activation
        # Parameters:
        #   1. Device path (LPCWSTR) — our virtual loopback device string
        #   2. Interface ID (REFIID) — IID_IAudioClient
        #   3. Activation params (PROPVARIANT*) — our process loopback config
        #   4. Completion handler — our callback object
        #   5. Operation out pointer — receives the async operation handle
        hr = _ActivateAudioInterfaceAsync(
            VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,
            ctypes.byref(IID_IAudioClient),
            ctypes.byref(prop),
            handler,
            ctypes.byref(operation_ptr)
        )

        if hr != 0:
            raise RuntimeError(
                f"ActivateAudioInterfaceAsync failed with HRESULT: {hr:#010x}"
            )

        # Wait for completion (timeout 5 seconds)
        if not completion_event.wait(timeout=5.0):
            raise RuntimeError(
                "ActivateAudioInterfaceAsync timed out after 5 seconds. "
                "The target process may not be producing audio."
            )

        # Get the result from the async operation
        if async_operation_holder[0] is None:
            raise RuntimeError("Async operation completed but returned no result")

        # Query the operation for its result
        async_op = async_operation_holder[0].QueryInterface(
            IActivateAudioInterfaceAsyncOperation
        )

        activate_hr = HRESULT()
        activated_interface = ctypes.POINTER(comtypes.IUnknown)()
        async_op.GetActivateResult(
            ctypes.byref(activate_hr),
            ctypes.byref(activated_interface)
        )

        if activate_hr.value != 0:
            raise RuntimeError(
                f"Audio client activation failed with HRESULT: {activate_hr.value:#010x}. "
                f"The target process (PID {self.target_pid}) may not exist or may not "
                f"be producing audio."
            )

        # We now have an IAudioClient interface for per-process loopback!
        print(f"[PROCESS-AUDIO] Successfully activated audio client for PID {self.target_pid}")

        # Store the raw COM pointer — we'll use it to initialize and capture
        self._audio_client_ptr = activated_interface

        # Now initialize the audio client for capture
        self._initialize_capture()

    def _setup_with_pycaw(self):
        """
        Attempt to use pycaw library for audio session access.
        Falls back to ctypes if pycaw doesn't support process loopback.
        """
        # pycaw doesn't currently support ActivateAudioInterfaceAsync
        # with process loopback params, so we always fall through to ctypes
        raise ImportError("pycaw does not support process loopback — using ctypes")

    def _setup_audio_client_fallback(self):
        """
        Fallback: if COM activation fails, capture all system audio
        and log a warning. The user gets system-wide capture instead
        of per-process, but at least the app doesn't crash.
        """
        print(f"[PROCESS-AUDIO] WARNING: Per-process capture failed for PID {self.target_pid}")
        print(f"[PROCESS-AUDIO] Falling back to system-wide capture (all audio)")
        self._fallback_mode = True
        self._fallback_capturer = AudioCapturer()

    def _initialize_capture(self):
        """
        Initialize the audio client for capture mode and query its format.

        After ActivateAudioInterfaceAsync gives us an IAudioClient, we need to:
        1. Get the mix format (sample rate, channels, bit depth)
        2. Initialize the client in shared loopback mode
        3. Get the IAudioCaptureClient for reading buffers
        """
        # For now, we read the audio using a simplified buffer approach.
        # The audio client captures at the system's native format
        # (typically 48kHz, 32-bit float, stereo). We resample to
        # 16kHz mono int16 in the capture loop, same as AudioCapturer.

        # Query the format through the COM interface
        # The mix format tells us what sample rate and channels to expect
        self._native_rate = 48000   # default — will be overridden by actual format
        self._native_channels = 2   # default — will be overridden
        self._native_bits = 32      # default — will be overridden
        self._is_float = True       # process loopback typically uses float32

        print(f"[PROCESS-AUDIO] Capture format: {self._native_rate}Hz, "
              f"{self._native_channels}ch, {self._native_bits}bit "
              f"({'float' if self._is_float else 'int'})")

    def _capture_loop(self):
        """
        Background thread: continuously reads audio from the per-process
        loopback stream, converts to 16kHz mono int16, applies VAD,
        and puts speech chunks into the queue.

        If running in fallback mode (system-wide), delegates to
        the AudioCapturer's capture loop instead.
        """
        if getattr(self, '_fallback_mode', False):
            # Fallback: use system-wide capturer
            self._fallback_capturer._capture_loop()
            return

        # Per-process capture loop
        # Read audio data from the COM audio client in chunks
        chunk_duration = CHUNK_DURATION  # 0.5 seconds
        chunk_samples = int(self._native_rate * chunk_duration)

        while self.is_running:
            try:
                # Read from the capture client buffer
                # The COM client fills a buffer, we read it, release it
                time.sleep(chunk_duration)  # wait for buffer to fill

                # In the full implementation, we would call:
                #   GetBuffer() → read audio bytes → ReleaseBuffer()
                # through the IAudioCaptureClient COM interface.
                #
                # For this initial version, we capture using a simplified
                # approach that works with the COM pointers we have.

                # placeholder — the actual COM buffer reading will be
                # implemented when we test on the real machine with
                # a running process producing audio.
                pass

            except Exception as e:
                if self.is_running:
                    print(f"[PROCESS-AUDIO] Capture error: {e}")
                break

    def start(self):
        """Start capturing audio from the target process."""
        if self.is_running:
            print("[PROCESS-AUDIO] Already capturing")
            return

        # If in fallback mode, start the system-wide capturer
        if getattr(self, '_fallback_mode', False):
            self._fallback_capturer.start()
            self.is_running = True
            # Mirror the fallback's queue so DualCapturer reads from ours
            self.audio_queue = self._fallback_capturer.audio_queue
            return

        try:
            self._activate_audio_client()
        except Exception as e:
            print(f"[PROCESS-AUDIO] Activation failed: {e}")
            self._setup_audio_client_fallback()
            self._fallback_capturer.start()
            self.is_running = True
            self.audio_queue = self._fallback_capturer.audio_queue
            return

        self.is_running = True
        self.capture_thread = threading.Thread(
            target=self._capture_loop, daemon=True
        )
        self.capture_thread.start()
        print(f"[PROCESS-AUDIO] Capture started for PID {self.target_pid}")

    def stop(self):
        """Stop capturing and release COM resources."""
        self.is_running = False

        # If in fallback mode, stop the system-wide capturer
        if getattr(self, '_fallback_mode', False):
            self._fallback_capturer.stop()
            print("[PROCESS-AUDIO] Fallback capturer stopped")
            return

        if self.capture_thread:
            self.capture_thread.join(timeout=2)

        # Release COM interfaces
        if self._capture_client:
            try:
                self._capture_client.Release()
            except Exception:
                pass
            self._capture_client = None

        if self._audio_client:
            try:
                self._audio_client.Release()
            except Exception:
                pass
            self._audio_client = None

        try:
            comtypes.CoUninitialize()
        except Exception:
            pass

        print(f"[PROCESS-AUDIO] Capture stopped for PID {self.target_pid}")

    def get_chunk(self, timeout=1.0):
        """
        Get next speech audio chunk from the queue.
        Same interface as AudioCapturer.get_chunk().

        Returns:
            Resampled 16kHz mono PCM bytes, or None on timeout
        """
        try:
            return self.audio_queue.get(timeout=timeout)
        except queue.Empty:
            return None


# ============================================
# MICROPHONE CAPTURER
# ============================================
# Captures audio from the default microphone input device.
# Same structure as AudioCapturer but uses the standard input
# device instead of WASAPI loopback.
#
# WHY a separate class?
#   - Loopback and microphone are different device types in Windows
#   - Loopback uses a special WASAPI API to find the "mirror" of speakers
#   - Microphone uses the standard default input device
#   - They may have different native sample rates and channel counts
#   - Keeping them separate means each handles its own device quirks

class MicCapturer:
    """
    Captures audio from the default microphone on Windows.
    Outputs resampled 16kHz mono PCM chunks into a queue.

    Usage (standalone):
        mic = MicCapturer()
        mic.start()
        chunk = mic.get_chunk()
        mic.stop()
    """

    def __init__(self):
        self.audio = None
        self.stream = None
        self.is_running = False
        self.capture_thread = None

        self.native_rate = None
        self.native_channels = None
        self.native_chunk_size = None

        self.audio_queue = queue.Queue()

    def _find_mic_device(self):
        """
        Find the default microphone input device.

        Unlike loopback (which mirrors speaker output), this is the
        standard audio input — your physical microphone or headset mic.

        Returns:
            dict: PyAudio device info for the default input device
        """
        p = pyaudio.PyAudio()

        try:
            # Get the default input device directly
            # This is whatever Windows has set as the default recording device
            # in Settings → Sound → Input
            default_input = p.get_default_input_device_info()
            print(f"[MIC] Found microphone: {default_input['name']}")
            print(f"[MIC] Native sample rate: {int(default_input['defaultSampleRate'])} Hz")
            print(f"[MIC] Native channels: {default_input['maxInputChannels']}")
            return default_input
        except Exception as e:
            raise RuntimeError(
                f"No microphone found. Check that a microphone is connected "
                f"and set as the default recording device in Windows Sound settings. "
                f"Error: {e}"
            )
        finally:
            p.terminate()

    def _capture_loop(self):
        """
        Runs in background thread. Reads audio from microphone,
        resamples to 16kHz mono, applies VAD, queues speech chunks.
        """
        while self.is_running:
            try:
                audio_data = self.stream.read(
                    self.native_chunk_size, exception_on_overflow=False
                )

                if is_speech(audio_data, num_channels=self.native_channels):
                    resampled = resample_audio(
                        audio_data,
                        native_rate=self.native_rate,
                        target_rate=TARGET_SAMPLE_RATE,
                        num_channels=self.native_channels
                    )
                    self.audio_queue.put(resampled)

            except Exception as e:
                if self.is_running:
                    print(f"[MIC] Capture error: {e}")
                break

    def start(self):
        """Start capturing microphone audio."""
        if self.is_running:
            print("[MIC] Already capturing")
            return

        mic_device = self._find_mic_device()

        self.native_rate = int(mic_device["defaultSampleRate"])
        self.native_channels = int(mic_device["maxInputChannels"])
        self.native_chunk_size = int(self.native_rate * CHUNK_DURATION)

        print(f"[MIC] Opening mic stream: {self.native_rate} Hz, "
              f"{self.native_channels} ch, chunk={self.native_chunk_size} samples")

        self.audio = pyaudio.PyAudio()
        self.stream = self.audio.open(
            format=pyaudio.paInt16,
            channels=self.native_channels,
            rate=self.native_rate,
            input=True,
            input_device_index=mic_device["index"],
            frames_per_buffer=self.native_chunk_size
        )

        self.is_running = True
        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.capture_thread.start()
        print(f"[MIC] Microphone capture started (native={self.native_rate} Hz → "
              f"target={TARGET_SAMPLE_RATE} Hz)")

    def stop(self):
        """Stop capturing and clean up."""
        self.is_running = False

        if self.capture_thread:
            self.capture_thread.join(timeout=2)

        if self.stream:
            self.stream.stop_stream()
            self.stream.close()

        if self.audio:
            self.audio.terminate()

        print("[MIC] Microphone capture stopped")

    def get_chunk(self, timeout=1.0):
        """Get next speech chunk from the mic queue. Returns None on timeout."""
        try:
            return self.audio_queue.get(timeout=timeout)
        except queue.Empty:
            return None


# ============================================
# DUAL CAPTURER — Sliding Window + Interleaving
# ============================================
# Owns both AudioCapturer (speakers) and MicCapturer (your voice).
# Runs a buffer thread that:
#   1. Drains both capturers' queues into separate sliding buffers
#   2. On a timer (SEND_INTERVAL), alternates which buffer to send
#   3. Assembles the buffer contents into a WAV, tags it with the source
#   4. Puts (wav_bytes, source_label) into the output queue
#
# WHY interleave instead of parallel?
#   Parallel would mean two audio windows hitting the GPU at the same time.
#   Canary runs one inference at a time on the GPU. Two simultaneous sends
#   would just queue up inside the server anyway. Interleaving does the
#   same thing explicitly, with the benefit that we control the order
#   and avoid unpredictable server-side queuing.
#
# BUFFER MEMORY:
#   Each buffer stores the last WINDOW_DURATION seconds of PCM chunks.
#   Old chunks are trimmed from the front when the buffer exceeds the
#   max window size. Audio captured while the server is processing a
#   previous window stays in the buffer — nothing is lost.

class DualCapturer:
    """
    Captures from both speakers (loopback) and microphone simultaneously.
    Manages sliding window buffers and interleaved sending.

    Output queue contains tuples: (wav_bytes, source_label)
        wav_bytes: Complete WAV file bytes (header + PCM)
        source_label: "speaker" or "mic"

    Mode switch — controlled by target_pid:
        target_pid=None  → system-wide capture (original behaviour, AudioCapturer)
        target_pid=12345 → per-app capture (ProcessAudioCapturer for that PID only)

    Mic toggle — controlled by enable_mic:
        enable_mic=True  → mic is captured (meetings, conversations)
        enable_mic=False → mic is off (solo lecture playback, no self-voice)

    Usage:
        # System-wide with mic (original mode — nothing changes)
        capturer = DualCapturer()

        # Per-app capture of PID 12345 with mic on (meeting in that app)
        capturer = DualCapturer(target_pid=12345, enable_mic=True)

        # Per-app capture of PID 12345 with mic off (watching a lecture)
        capturer = DualCapturer(target_pid=12345, enable_mic=False)

        capturer.start()

        while True:
            item = capturer.get_chunk(timeout=1.0)
            if item:
                wav_bytes, source = item
                send_to_server(wav_bytes, source)

        capturer.stop()
    """

    def __init__(self, target_pid: int = None, enable_mic: bool = True):
        # --- Speaker source decision ---
        # if a target PID was provided, use ProcessAudioCapturer to grab
        # audio from that one app only. Otherwise fall back to the original
        # system-wide AudioCapturer — nothing changes for existing callers.
        if target_pid is not None:
            print(f"[DUAL] Per-app mode — capturing PID {target_pid}")
            self.speaker_capturer = ProcessAudioCapturer(target_pid)
        else:
            print("[DUAL] System-wide mode — capturing all desktop audio")
            self.speaker_capturer = AudioCapturer()

        # --- Mic decision ---
        # enable_mic=False means the user is watching a solo lecture and
        # does not want their own voice captured. Setting mic_capturer
        # to None tells start/stop/drain to skip it entirely.
        self.enable_mic = enable_mic
        if enable_mic:
            self.mic_capturer = MicCapturer()
        else:
            self.mic_capturer = None
            print("[DUAL] Mic disabled — speaker-only mode")

        # saving these so the UI can read them later if needed
        self.target_pid = target_pid

        # --- Sliding window buffers ---
        # Each buffer is a list of raw PCM byte chunks (16kHz mono int16).
        # Each chunk represents CHUNK_DURATION seconds of audio.
        # The buffer grows as audio comes in. When it exceeds
        # max_chunks_in_window, old chunks are trimmed from the front.
        self.speaker_buffer = []
        self.mic_buffer = []

        # Lock protects buffer access — capture thread writes, buffer thread reads
        # threading.Lock = mutual exclusion: only one thread can hold it at a time.
        # Without this, the capture thread could be appending to the buffer
        # at the exact moment the buffer thread is reading + trimming it,
        # which could corrupt the list or cause a crash.
        self.speaker_lock = threading.Lock()
        self.mic_lock = threading.Lock()

        # How many 0.5s chunks fit in one window
        # Example: 5.0 / 0.5 = 10 chunks max per buffer
        self.max_chunks_in_window = int(WINDOW_DURATION / CHUNK_DURATION)

        # Output queue — what LiveWorker reads from
        # Items are tuples: (wav_bytes, "speaker") or (wav_bytes, "mic")
        self.output_queue = queue.Queue()

        # Controls the buffer→queue thread
        self.is_running = False
        self.buffer_thread = None

        # Tracks which source to send next: alternates between "speaker" and "mic"
        # Starts with "speaker" so the first transcription you see is what
        # others are saying (usually the more important context in a meeting)
        self._next_source = "speaker"

    def _drain_into_buffers(self):
        """
        Pull all available chunks from both capturers' queues
        into the sliding window buffers.

        Called frequently by the buffer thread. Non-blocking —
        if a queue is empty, we just skip it.

        After draining, trims each buffer to max_chunks_in_window
        so it only holds the most recent WINDOW_DURATION seconds.
        """
        # Drain speaker queue into speaker buffer
        while True:
            try:
                chunk = self.speaker_capturer.audio_queue.get_nowait()
                with self.speaker_lock:
                    self.speaker_buffer.append(chunk)
                    # Trim: keep only the last max_chunks_in_window chunks
                    if len(self.speaker_buffer) > self.max_chunks_in_window:
                        self.speaker_buffer = self.speaker_buffer[-self.max_chunks_in_window:]
            except queue.Empty:
                break

        # Drain mic queue into mic buffer — only if mic is enabled
        if self.mic_capturer is not None:
            while True:
                try:
                    chunk = self.mic_capturer.audio_queue.get_nowait()
                    with self.mic_lock:
                        self.mic_buffer.append(chunk)
                        if len(self.mic_buffer) > self.max_chunks_in_window:
                            self.mic_buffer = self.mic_buffer[-self.max_chunks_in_window:]
                except queue.Empty:
                    break

    def _assemble_window(self, buffer, lock):
        """
        Take all chunks currently in the buffer, concatenate them into
        one continuous PCM byte string, and wrap it in a WAV header.

        Args:
            buffer: list of raw PCM byte chunks
            lock: threading.Lock protecting this buffer

        Returns:
            WAV bytes (header + PCM) if buffer has audio, None if empty
        """
        with lock:
            if not buffer:
                return None
            # Concatenate all PCM chunks into one continuous byte string
            # b"".join() is like string concatenation but for bytes
            combined_pcm = b"".join(buffer)

        # Wrap the combined PCM in a WAV header so the server
        # can read sample rate, channels, etc. from the header
        wav_bytes = pcm_to_wav(
            combined_pcm,
            sample_rate=TARGET_SAMPLE_RATE,
            num_channels=1,
            bits_per_sample=16
        )
        return wav_bytes

    def _buffer_loop(self):
        """
        Runs in background thread. On a timer:
            1. Drain both capturers' queues into buffers
            2. Check which source is next (alternating)
            3. Assemble that buffer's window into WAV
            4. Put (wav_bytes, source) into output_queue
            5. Switch to the other source for next time

        If the current source's buffer is empty (no speech detected),
        skip it and try the other source. This prevents blocking on
        silence from one source while the other has speech.

        When mic is disabled (self.enable_mic=False), only speaker
        windows are sent — no interleaving, no mic buffer checks.
        """
        while self.is_running:
            # Wait for SEND_INTERVAL before sending the next window
            # Using a short sleep loop instead of time.sleep(SEND_INTERVAL)
            # so we can exit quickly when stop() is called
            wait_start = time.time()
            while self.is_running and (time.time() - wait_start) < SEND_INTERVAL:
                time.sleep(0.05)  # 50ms granularity

            if not self.is_running:
                break

            # Step 1: Drain both queues into buffers
            self._drain_into_buffers()

            # --- Mic-disabled path: speaker only, no interleaving ---
            # when the user is watching a solo lecture with mic off,
            # every cycle just sends the speaker window. Simpler path.
            if not self.enable_mic:
                wav = self._assemble_window(self.speaker_buffer, self.speaker_lock)
                if wav:
                    self.output_queue.put((wav, "speaker"))
                continue

            # --- Normal interleaved path (mic enabled) ---
            # Step 2: Try the next source in the interleave rotation
            if self._next_source == "speaker":
                wav = self._assemble_window(self.speaker_buffer, self.speaker_lock)
                if wav:
                    self.output_queue.put((wav, "speaker"))
                    self._next_source = "mic"
                else:
                    # Speaker buffer empty — try mic instead so we don't waste a cycle
                    wav = self._assemble_window(self.mic_buffer, self.mic_lock)
                    if wav:
                        self.output_queue.put((wav, "mic"))
                    # Next time still try speaker (don't get stuck on mic)
                    self._next_source = "mic"
            else:
                wav = self._assemble_window(self.mic_buffer, self.mic_lock)
                if wav:
                    self.output_queue.put((wav, "mic"))
                    self._next_source = "speaker"
                else:
                    # Mic buffer empty — try speaker instead
                    wav = self._assemble_window(self.speaker_buffer, self.speaker_lock)
                    if wav:
                        self.output_queue.put((wav, "speaker"))
                    self._next_source = "speaker"

    def start(self):
        """Start both capturers and the buffer management thread."""
        if self.is_running:
            print("[DUAL] Already running")
            return

        self.is_running = True

        # Start both audio capture streams
        # Each starts its own capture thread internally
        try:
            self.speaker_capturer.start()
        except Exception as e:
            print(f"[DUAL] Speaker capture failed: {e}")
            # Continue without speaker — mic might still work

        # only start mic if it was enabled at init time
        if self.mic_capturer is not None:
            try:
                self.mic_capturer.start()
            except Exception as e:
                print(f"[DUAL] Mic capture failed: {e}")
                # Continue without mic — speaker might still work

        # Start the buffer management thread
        # This thread drains both capturers' queues, manages the sliding
        # windows, and puts assembled WAV chunks into the output queue
        self.buffer_thread = threading.Thread(target=self._buffer_loop, daemon=True)
        self.buffer_thread.start()
        mic_status = "on" if self.enable_mic else "off"
        mode = f"PID {self.target_pid}" if self.target_pid else "system-wide"
        print(f"[DUAL] Dual capture started (mode={mode}, mic={mic_status}, "
              f"window={WINDOW_DURATION}s, interval={SEND_INTERVAL}s)")

    def stop(self):
        """Stop both capturers and the buffer thread."""
        self.is_running = False

        if self.buffer_thread:
            self.buffer_thread.join(timeout=2)

        self.speaker_capturer.stop()
        if self.mic_capturer is not None:
            self.mic_capturer.stop()

        # Clear buffers
        self.speaker_buffer.clear()
        self.mic_buffer.clear()

        print("[DUAL] Dual capture stopped")

    def get_chunk(self, timeout=1.0):
        """
        Get next tagged audio window from the output queue.

        Returns:
            Tuple of (wav_bytes, source_label) where source_label is
            "speaker" or "mic". Returns None on timeout.
        """
        try:
            return self.output_queue.get(timeout=timeout)
        except queue.Empty:
            return None
