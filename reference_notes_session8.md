# Real-Time Transcription System — Session 8 Reference Notes
# Updated: Session 8 — HPC cluster Setup, A100 Infrastructure, SSH Tunnel Concepts, Environment Discovery
# Nothing skipped. Every command, error, decision, concept, and code exchange documented.

---

## Table of Contents

1. [Session 8 Overview — Where We Picked Up](#1-session-8-overview--where-we-picked-up)
2. [Project Status Assessment — Steps 1-7 Recap](#2-project-status-assessment--steps-1-7-recap)
3. [Complete Existing Codebase — Every File](#3-complete-existing-codebase--every-file)
4. [A4000 vs A100 GPU Comparison](#4-a4000-vs-a100-gpu-comparison)
5. [HPC cluster — Account Setup & Access](#5-surrey-hpc--account-setup--access)
6. [REDACTED_HOST Cluster Environment Discovery](#6-REDACTED_HOST-cluster-environment-discovery)
7. [SSH Tunnel — What It Is and How It Works](#7-ssh-tunnel--what-it-is-and-how-it-works)
8. [Decision: Anaconda3 vs venv for HPC Python Environment](#8-decision-anaconda3-vs-venv-for-hpc-python-environment)
9. [Step 4 Phase A Plan — Three New Files](#9-step-4-phase-a-plan--three-new-files)
10. [Key Concepts Taught This Session](#10-key-concepts-taught-this-session)
11. [Roadmap Status After Session 8](#11-roadmap-status-after-session-8)
12. [Interview Prep — Session 8](#12-interview-prep--session-8)
13. [Troubleshooting Log — Session 8](#13-troubleshooting-log--session-8)
14. [Comprehension Checks — Session 8](#14-comprehension-checks--session-8)
15. [REDACTED_HOST SSH Login — Exact Output](#15-REDACTED_HOST-ssh-login--exact-output)
16. [Slurm GPU Request Syntax](#16-slurm-gpu-request-syntax)

---

## 1. Session 8 Overview — Where We Picked Up

### Context

Session 8 started by reviewing ALL reference notes (Sessions 1-7) and ALL existing code files to determine exactly where the project was left off. The DigitalOcean GPU droplet had been destroyed at the end of Session 7 (snapshot saved: `transcription-server-step3-complete`, 42.03 GB, TOR1 region). The HPC cluster cluster was identified as the next GPU infrastructure.

### What Happened This Session

- Full project audit: read all 7 session reference notes + all 19 code files
- Confirmed Steps 1-3 complete, Step 4 is next
- Received HPC cluster account activation email
- Explored REDACTED_HOST OnDemand web interface
- SSHd into REDACTED_HOST cluster and discovered environment details
- Compared A4000 vs A100 GPUs — chose A100
- Learned what SSH tunnels are and why HPC clusters need them
- Decided on Anaconda3 (over venv) for the HPC Python environment
- Planned three new files: surrey_job.sh, deploy_surrey.sh, tunnel.sh
- All explanation done, waiting to build

---

## 2. Project Status Assessment — Steps 1-7 Recap

| Step | Description | Status | Session Completed |
|------|-------------|--------|-------------------|
| 1 | Deploy server skeleton, verify endpoints remotely | ✅ Complete | Session 6 |
| 2 | Replace mock models with real AI inference code | ✅ Complete | Session 6 |
| 3 | Verify actual transcription works (synthetic test audio) | ✅ Complete | Session 7 |
| 4 | End-to-end test: laptop audio → cloud → text back | ⬜ In Progress (Session 8) | — |
| 5 | Production hardening: TLS, reconnection, error recovery | ⬜ Pending | — |

### Step 3 Final Result (Session 7)
- Sent synthetic test audio (3-second sine wave, 16kHz, mono, float32) to `/api/transcribe`
- Response: `{"transcript": "That's right.", "confidence": 1.0}`
- First real AI transcription confirmed working
- Canary API contract understood: requires `source_lang`, `target_lang`, `task`, `pnc` parameters

### End of Session 7 Actions
- Droplet snapshot taken: `transcription-server-step3-complete` (42.03 GB, TOR1)
- Droplet destroyed (billing stopped)
- HPC cluster identified as next free GPU option

---

## 3. Complete Existing Codebase — Every File

### Project Structure

```
transcription-app/
├── .env                          # Secret credentials (gitignored)
├── .env.example                  # Template for .env
├── .gitignore                    # Excludes secrets, models, venv, audio
├── requirements.txt              # Python dependencies (client + server)
├── gpu_sniper.py                 # DigitalOcean GPU availability scanner
├── client/
│   ├── main.py                   # App entry point — launches UI + auto-updates yt-dlp
│   ├── audio/
│   │   ├── __init__.py           # Empty
│   │   ├── capture.py            # WASAPI loopback audio capture + VAD filter
│   │   └── youtube.py            # YouTube audio downloader via yt-dlp
│   ├── network/
│   │   ├── __init__.py           # Empty
│   │   ├── cloud_control.py      # DigitalOcean droplet start/stop + heartbeat
│   │   └── transmitter.py        # LiveTransmitter (WSS) + BulkTransmitter (HTTP POST)
│   └── ui/
│       ├── __init__.py           # Empty
│       ├── overlay.py            # PyQt6 borderless translucent overlay (786 lines)
│       └── workers.py            # LiveWorker + BulkWorker async bridges (261 lines)
└── server/
    ├── main.py                   # FastAPI server with 3 endpoints
    ├── deploy.sh                 # DigitalOcean deployment script
    ├── endpoints/
    │   └── __init__.py           # Empty
    └── models/
        ├── __init__.py           # Empty
        └── transcriber.py        # Real Canary + Whisper models + confidence router
```

---

### File: `.env.example` (8 lines)

```
# Digital Ocean API — controls your droplet (start/stop)
DO_API_TOKEN=your_digital_ocean_api_token_here
DO_DROPLET_ID=your_droplet_id_here

# Server connection — where client sends audio
SERVER_IP=your_server_ip_here
SERVER_PORT=8000
```

---

### File: `.gitignore` (50 lines)

```
# ============================================
# CATEGORY 1: SECRETS
# Passwords, API keys, session tokens
# WHY: Scraper bots find these in seconds
# ============================================
.env
cookies.txt

# ============================================
# CATEGORY 2: AI MODEL FILES
# Multi-GB files that break GitHub's 100MB limit
# WHY: These live on the cloud server, not in repo
# ============================================
*.pt
*.bin
*.onnx

# ============================================
# CATEGORY 3: VIRTUAL ENVIRONMENT
# Recreatable from requirements.txt
# WHY: 500MB+ folder, share recipe not meal
# ============================================
venv/

# ============================================
# CATEGORY 4: PYTHON JUNK
# Auto-generated bytecode, machine-specific
# WHY: Useless to others, regenerated on run
# ============================================
__pycache__/
*.pyc

# ============================================
# CATEGORY 5: IDE AND OS JUNK
# Personal editor settings, OS metadata
# WHY: Irrelevant to the project
# ============================================
.vscode/
Thumbs.db
.DS_Store
desktop.ini

# ============================================
# CATEGORY 6: DOWNLOADED AUDIO FILES
# yt-dlp downloads, can be hundreds of MB
# WHY: User-generated content, not part of codebase
# ============================================
downloads/
*.wav
```

---

### File: `requirements.txt` (14 lines)

```
# === SERVER DEPENDENCIES (install on Digital Ocean droplet) ===
fastapi
uvicorn
python-multipart

# === CLIENT DEPENDENCIES (install on your Windows laptop) ===
pyaudiowpatch
PyQt6
yt-dlp
websockets
aiohttp
python-dotenv
numpy
requests
```

---

### File: `client/main.py` (17 lines)

```python
# client/main.py — Application Entry Point
# Launches the transcription overlay UI
#
# This is the file you run: python client/main.py

from ui.overlay import run_app
from audio.youtube import auto_update_ytdlp

if __name__ == "__main__":
    # Self-heal yt-dlp before anything else
    print("[STARTUP] Checking for yt-dlp updates...")
    auto_update_ytdlp()

    # Launch the UI
    print("[STARTUP] Launching transcription overlay...")
    run_app()
```

---

### File: `client/audio/capture.py` (222 lines)

```python
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
```

---

### File: `client/audio/youtube.py` (217 lines)

```python
# client/audio/youtube.py — YouTube Audio Extraction
# Phase 3: The Ears (Bulk Mode)
#
# This module does ONE job:
#   Download ONLY the audio track from a YouTube URL
#   Save it as a file on your local machine
#   That's it. yt-dlp's job ENDS when the file is saved.
#
# A SEPARATE module (client/network/) picks up this file
# and sends it to the server. These two don't know each other.
# They share a file on your local filesystem.
#
# RUNS ON: Your Windows laptop (client-side)
# REQUIRES: yt-dlp (pip install yt-dlp)

import subprocess
import sys
import os
import yt_dlp


# ============================================
# AUTO-UPDATE YT-DLP ON LAUNCH
# ============================================
# YouTube constantly changes its internal code to block
# download tools. yt-dlp releases updates to counter this.
# Running this on app launch = self-healing.
#
# WHY: If yt-dlp is outdated by even a few days,
# YouTube extraction can silently fail.

def auto_update_ytdlp():
    """
    Silently upgrade yt-dlp to latest version.
    Runs on every app launch to stay ahead of YouTube's anti-bot changes.
    """
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
            capture_output=True,  # Don't show pip output to user
            timeout=30            # Don't hang forever if network is slow
        )
        print("[YT-DLP] Auto-update complete")
    except Exception as e:
        # Update failed — not fatal, existing version might still work
        print(f"[YT-DLP] Auto-update failed (not critical): {e}")


# ============================================
# COOKIE FILE PATH
# ============================================
# yt-dlp can use your browser cookies to look like a real
# logged-in human instead of a bot.
#
# WITHOUT cookies: YouTube may block you, show CAPTCHAs, or
#   restrict age-gated content
# WITH cookies: YouTube sees an authenticated browser session,
#   treats you as a normal user
#
# Export cookies from your browser using a browser extension
# (like "Get cookies.txt") and save as cookies.txt in project root.
# This file is in .gitignore — NEVER committed.

COOKIES_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "cookies.txt")


# ============================================
# YOUTUBE AUDIO DOWNLOADER
# ============================================

class YouTubeExtractor:
    """
    Downloads audio-only from YouTube URLs using yt-dlp.

    Usage:
        extractor = YouTubeExtractor()
        filepath = extractor.download("https://www.youtube.com/watch?v=...")
        # filepath is now a local .wav file ready to send to server
    """

    def __init__(self, output_dir=None):
        """
        Args:
            output_dir: Where to save downloaded audio files.
                        Defaults to a 'downloads' folder in project root.
        """
        if output_dir is None:
            output_dir = os.path.join(
                os.path.dirname(__file__), "..", "..", "downloads"
            )
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def download(self, url: str, progress_callback=None) -> str:
        """
        Download audio from a YouTube URL.

        Args:
            url: YouTube video URL
            progress_callback: Optional function called with download progress
                             Signature: callback(percent: float, status: str)
                             Used by UI to show progress bar.

        Returns:
            Filepath to the downloaded audio file (.wav)

        Raises:
            Exception if download fails (bad URL, blocked, network error)
        """
        # yt-dlp configuration
        ydl_opts = {
            # Extract ONLY audio, no video
            # WHY: Video data is massive and useless for transcription.
            # A 1-hour video might be 2GB with video, 50MB audio only.
            "format": "bestaudio/best",

            # Convert to WAV format after download
            # WHY WAV? Uncompressed audio — AI models work best with
            # raw uncompressed audio. No quality lost to compression.
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "wav",
                "preferredquality": "192",
            }],

            # Output file path template
            # %(title)s = video title, used as filename
            "outtmpl": os.path.join(self.output_dir, "%(title)s.%(ext)s"),

            # Quiet mode — don't spam console with download progress
            "quiet": True,
            "no_warnings": True,
        }

        # Add cookies if the file exists
        # Cookie file is optional — works without it for most videos,
        # but needed for age-restricted or region-locked content
        cookies_path = os.path.abspath(COOKIES_FILE)
        if os.path.exists(cookies_path):
            ydl_opts["cookiefile"] = cookies_path
            print("[YT-DLP] Using browser cookies for authentication")
        else:
            print("[YT-DLP] No cookies.txt found — proceeding without auth")

        # Add progress hook if callback provided
        if progress_callback:
            def progress_hook(d):
                if d["status"] == "downloading":
                    percent = d.get("_percent_str", "0%").strip()
                    progress_callback(float(percent.replace("%", "")), "Downloading")
                elif d["status"] == "finished":
                    progress_callback(100.0, "Processing audio")

            ydl_opts["progress_hooks"] = [progress_hook]

        # Execute the download
        print(f"[YT-DLP] Downloading audio from: {url}")
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # extract_info downloads + returns video metadata
                info = ydl.extract_info(url, download=True)
                title = info.get("title", "unknown")

                # The final file path after conversion to WAV
                filepath = os.path.join(self.output_dir, f"{title}.wav")

                # Verify file exists
                if not os.path.exists(filepath):
                    # Sometimes yt-dlp sanitizes the title differently
                    # Look for any .wav file in the output dir
                    wav_files = [f for f in os.listdir(self.output_dir)
                                 if f.endswith(".wav")]
                    if wav_files:
                        filepath = os.path.join(self.output_dir, wav_files[-1])
                    else:
                        raise FileNotFoundError("Download succeeded but WAV file not found")

                file_size_mb = os.path.getsize(filepath) / (1024 * 1024)
                print(f"[YT-DLP] Saved: {filepath} ({file_size_mb:.1f} MB)")
                return filepath

        except Exception as e:
            print(f"[YT-DLP] Download failed: {e}")
            raise

    def get_video_info(self, url: str) -> dict:
        """
        Get video metadata without downloading.
        Used by UI to show video title/duration before user commits to download.
        """
        ydl_opts = {"quiet": True, "no_warnings": True}

        # Add cookies if available
        cookies_path = os.path.abspath(COOKIES_FILE)
        if os.path.exists(cookies_path):
            ydl_opts["cookiefile"] = cookies_path

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                "title": info.get("title", "Unknown"),
                "duration_seconds": info.get("duration", 0),
                "duration_formatted": self._format_duration(info.get("duration", 0)),
                "channel": info.get("channel", "Unknown"),
                "thumbnail": info.get("thumbnail", None),
            }

    @staticmethod
    def _format_duration(seconds: int) -> str:
        """Convert seconds to HH:MM:SS format."""
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"
```

---

### File: `client/network/cloud_control.py` (191 lines)

```python
# client/network/cloud_control.py — Digital Ocean GPU Droplet Controller
# The Cloud Switch + Heartbeat System
#
# TWO JOBS:
#   1. Start/stop the GPU droplet via Digital Ocean API
#   2. Heartbeat — ping server every 60 seconds so it knows app is alive
#      If no ping for 5 minutes, server shuts itself down (crash protection)
#
# RUNS ON: Your Windows laptop (client-side)
# REQUIRES: aiohttp, python-dotenv

import os
import asyncio
import threading
import aiohttp
from dotenv import load_dotenv

load_dotenv()

DO_API_TOKEN = os.getenv("DO_API_TOKEN", "")
DO_DROPLET_ID = os.getenv("DO_DROPLET_ID", "")
DO_API_BASE = "https://api.digitalocean.com/v2"

# Heartbeat interval (seconds) — how often we tell server "I'm alive"
HEARTBEAT_INTERVAL = 60


class CloudController:
    """
    Controls the Digital Ocean GPU droplet lifecycle.

    Usage:
        cloud = CloudController()
        await cloud.start_server()   # Powers on droplet
        await cloud.stop_server()    # Powers off droplet
        cloud.start_heartbeat()      # Begin pinging server
        cloud.stop_heartbeat()       # Stop pinging
    """

    def __init__(self):
        self.server_running = False
        self.heartbeat_thread = None
        self.heartbeat_active = False
        self.headers = {
            "Authorization": f"Bearer {DO_API_TOKEN}",
            "Content-Type": "application/json"
        }

    async def start_server(self) -> bool:
        """
        Power on the Digital Ocean GPU droplet.

        Sends POST to Digital Ocean API:
            /v2/droplets/{id}/actions with {"type": "power_on"}

        Returns True if request accepted, False on failure.
        Boot time: ~1-2 minutes (server OS boots + models load into GPU)
        """
        if not DO_API_TOKEN or not DO_DROPLET_ID:
            print("[CLOUD] ERROR: DO_API_TOKEN or DO_DROPLET_ID not set in .env")
            return False

        url = f"{DO_API_BASE}/droplets/{DO_DROPLET_ID}/actions"
        payload = {"type": "power_on"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload,
                                        headers=self.headers) as resp:
                    if resp.status in (200, 201):
                        self.server_running = True
                        print("[CLOUD] Server power-on request accepted")
                        return True
                    else:
                        error = await resp.text()
                        print(f"[CLOUD] Power-on failed ({resp.status}): {error}")
                        return False
        except Exception as e:
            print(f"[CLOUD] Power-on request error: {e}")
            return False

    async def stop_server(self) -> bool:
        """
        Power off the Digital Ocean GPU droplet.
        Called on app close to save cloud credits.
        """
        if not DO_API_TOKEN or not DO_DROPLET_ID:
            return False

        url = f"{DO_API_BASE}/droplets/{DO_DROPLET_ID}/actions"
        payload = {"type": "power_off"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload,
                                        headers=self.headers) as resp:
                    if resp.status in (200, 201):
                        self.server_running = False
                        print("[CLOUD] Server power-off request accepted")
                        return True
                    else:
                        error = await resp.text()
                        print(f"[CLOUD] Power-off failed ({resp.status}): {error}")
                        return False
        except Exception as e:
            print(f"[CLOUD] Power-off request error: {e}")
            return False

    async def get_server_status(self) -> str:
        """
        Check current droplet status.
        Returns: "active", "off", "new", or "error"
        """
        if not DO_API_TOKEN or not DO_DROPLET_ID:
            return "error"

        url = f"{DO_API_BASE}/droplets/{DO_DROPLET_ID}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        status = data.get("droplet", {}).get("status", "unknown")
                        return status
                    return "error"
        except Exception:
            return "error"

    # ------------------------------------------
    # HEARTBEAT SYSTEM
    # ------------------------------------------

    def start_heartbeat(self, server_ip: str, server_port: str):
        """Start the heartbeat ping in a background thread."""
        self.heartbeat_active = True
        self.heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(server_ip, server_port),
            daemon=True
        )
        self.heartbeat_thread.start()
        print("[HEARTBEAT] Started — pinging every 60 seconds")

    def stop_heartbeat(self):
        """Stop the heartbeat."""
        self.heartbeat_active = False
        if self.heartbeat_thread:
            self.heartbeat_thread.join(timeout=2)
        print("[HEARTBEAT] Stopped")

    def _heartbeat_loop(self, server_ip: str, server_port: str):
        """Background loop that pings the server's health endpoint."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        url = f"http://{server_ip}:{server_port}/health"

        while self.heartbeat_active:
            try:
                loop.run_until_complete(self._ping(url))
            except Exception:
                pass  # Server might be booting, don't crash heartbeat

            # Sleep in small increments so stop() is responsive
            for _ in range(HEARTBEAT_INTERVAL):
                if not self.heartbeat_active:
                    break
                import time
                time.sleep(1)

        loop.close()

    async def _ping(self, url: str):
        """Send a single heartbeat ping."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url,
                                       timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        pass  # Server is alive, all good
                    else:
                        print(f"[HEARTBEAT] Server responded with {resp.status}")
        except Exception:
            print("[HEARTBEAT] Server not responding")
```

---

### File: `client/network/transmitter.py` (206 lines)

```python
# client/network/transmitter.py — Audio Transmission to Cloud Server
# Phase 4: The Bridge
#
# Two transmission modes:
#   1. LiveTransmitter — WSS pipe for real-time audio streaming
#   2. BulkTransmitter — Async HTTP POST for complete YouTube files
#
# RUNS ON: Your Windows laptop (client-side)
# CONNECTS TO: FastAPI server on Digital Ocean GPU droplet

import asyncio
import json
import os
import aiohttp
import websockets
from dotenv import load_dotenv

# Load server connection details from .env file
load_dotenv()
SERVER_IP = os.getenv("SERVER_IP", "localhost")
SERVER_PORT = os.getenv("SERVER_PORT", "8000")


# ============================================
# LIVE TRANSMITTER — WebSocket Secure (WSS)
# ============================================

class LiveTransmitter:
    """
    Streams audio chunks to server via WebSocket and receives
    transcribed text back in real-time.
    """

    def __init__(self):
        self.websocket = None
        self.connected = False
        self.url = f"ws://{SERVER_IP}:{SERVER_PORT}/ws/transcribe"

    async def connect(self):
        """Open WebSocket connection to the server."""
        try:
            self.websocket = await websockets.connect(self.url)
            self.connected = True
            print(f"[WSS] Connected to {self.url}")
        except Exception as e:
            self.connected = False
            print(f"[WSS] Connection failed: {e}")
            raise

    async def send_chunk(self, audio_bytes: bytes) -> dict:
        """
        Send one audio chunk and receive transcription back.

        Args:
            audio_bytes: Raw audio bytes from WASAPI capture (already VAD-filtered)

        Returns:
            Dict with: transcript, confidence, model_used, was_fallback
        """
        if not self.connected or not self.websocket:
            raise RuntimeError("Not connected. Call connect() first.")

        await self.websocket.send(audio_bytes)
        response = await self.websocket.recv()
        return json.loads(response)

    async def disconnect(self):
        """Close the WebSocket connection."""
        if self.websocket:
            await self.websocket.close()
            self.connected = False
            print("[WSS] Disconnected")

    async def check_server_health(self) -> bool:
        """Ping the server's health endpoint before opening WebSocket."""
        health_url = f"http://{SERVER_IP}:{SERVER_PORT}/health"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(health_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("status") == "alive"
            return False
        except Exception:
            return False


# ============================================
# BULK TRANSMITTER — Async HTTP POST
# ============================================

class BulkTransmitter:
    """
    Uploads complete audio files to server for bulk transcription.
    """

    def __init__(self):
        self.url = f"http://{SERVER_IP}:{SERVER_PORT}/api/transcribe"

    async def upload_file(self, filepath: str, progress_callback=None) -> dict:
        """
        Upload an audio file to the server for transcription.

        Args:
            filepath: Path to local audio file (from yt-dlp download)
            progress_callback: Optional function for upload progress

        Returns:
            Dict with: transcript, confidence, model_used, was_fallback
        """
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Audio file not found: {filepath}")

        filename = os.path.basename(filepath)
        file_size_mb = os.path.getsize(filepath) / (1024 * 1024)
        print(f"[BULK] Uploading {filename} ({file_size_mb:.1f} MB)")

        try:
            timeout = aiohttp.ClientTimeout(total=600)  # 10 minute max

            async with aiohttp.ClientSession(timeout=timeout) as session:
                data = aiohttp.FormData()
                data.add_field(
                    "file",
                    open(filepath, "rb"),
                    filename=filename,
                    content_type="audio/wav"
                )

                async with session.post(self.url, data=data) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        print(f"[BULK] Transcription complete via {result.get('model_used')}")
                        return result
                    else:
                        error_text = await resp.text()
                        raise RuntimeError(f"Server returned {resp.status}: {error_text}")

        except asyncio.TimeoutError:
            raise RuntimeError("Upload/transcription timed out (>10 minutes)")
        except Exception as e:
            print(f"[BULK] Upload failed: {e}")
            raise

    async def check_server_health(self) -> bool:
        """Check if server is alive before attempting upload."""
        health_url = f"http://{SERVER_IP}:{SERVER_PORT}/health"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(health_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    return resp.status == 200
        except Exception:
            return False
```

---

### File: `client/ui/overlay.py` (786 lines)

```python
# client/ui/overlay.py — Stealth Transcription Overlay
# Phase 5: The Face
#
# Features:
#   1. Borderless translucent dark-mode overlay (Cluely-style)
#   2. Ghost Feature — invisible to screen sharing software
#   3. Cloud Switch — start/stop Digital Ocean GPU droplet
#   4. Dual Mode — toggle between Live Mode and Bulk Mode
#   5. Export Suite — copy to clipboard, save as .txt or .srt
#
# RUNS ON: Your Windows laptop (client-side)
# REQUIRES: PyQt6, ctypes (built into Python on Windows)

import sys
import os
import ctypes
import asyncio
import threading
from datetime import timedelta

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTextEdit, QLineEdit, QFileDialog,
    QStackedWidget, QFrame, QMessageBox
)
from PyQt6.QtCore import Qt, QPoint, pyqtSignal, QObject, QTimer
from PyQt6.QtGui import QFont, QColor, QPalette, QIcon

from ui.workers import LiveWorker, BulkWorker
from network.cloud_control import CloudController


# ============================================
# GHOST FEATURE — Screen Share Invisibility
# ============================================
WDA_EXCLUDEFROMCAPTURE = 0x00000011

def enable_ghost_mode(window_handle):
    """Make a window invisible to all screen capture software."""
    try:
        ctypes.windll.user32.SetWindowDisplayAffinity(
            window_handle,
            WDA_EXCLUDEFROMCAPTURE
        )
        print("[GHOST] Screen capture invisibility enabled")
    except Exception as e:
        print(f"[GHOST] Failed to enable (non-Windows OS?): {e}")

def disable_ghost_mode(window_handle):
    """Restore window visibility to screen capture software."""
    try:
        ctypes.windll.user32.SetWindowDisplayAffinity(window_handle, 0x00000000)
        print("[GHOST] Screen capture invisibility disabled")
    except Exception as e:
        print(f"[GHOST] Failed to disable: {e}")


# ============================================
# STYLESHEET — Dark Mode Translucent Theme
# ============================================
STYLESHEET = """
    QMainWindow {
        background-color: rgba(15, 15, 20, 200);
    }
    QLabel {
        color: #E8E8E8;
        font-size: 13px;
    }
    QLabel#title {
        color: #FFFFFF;
        font-size: 16px;
        font-weight: bold;
    }
    QLabel#status {
        color: #888888;
        font-size: 11px;
    }
    QTextEdit {
        background-color: rgba(25, 25, 35, 180);
        color: #F0F0F0;
        border: 1px solid rgba(255, 255, 255, 30);
        border-radius: 8px;
        padding: 10px;
        font-size: 14px;
        font-family: 'Segoe UI', 'Consolas', monospace;
    }
    QLineEdit {
        background-color: rgba(25, 25, 35, 180);
        color: #F0F0F0;
        border: 1px solid rgba(255, 255, 255, 30);
        border-radius: 6px;
        padding: 8px 12px;
        font-size: 13px;
    }
    QLineEdit:focus {
        border: 1px solid rgba(100, 150, 255, 150);
    }
    QPushButton {
        background-color: rgba(60, 60, 80, 200);
        color: #E0E0E0;
        border: 1px solid rgba(255, 255, 255, 20);
        border-radius: 6px;
        padding: 8px 16px;
        font-size: 12px;
        font-weight: bold;
    }
    QPushButton:hover {
        background-color: rgba(80, 80, 110, 220);
        border: 1px solid rgba(100, 150, 255, 100);
    }
    QPushButton:pressed {
        background-color: rgba(40, 40, 60, 220);
    }
    QPushButton#server_on {
        background-color: rgba(30, 120, 60, 200);
        border: 1px solid rgba(50, 200, 100, 100);
    }
    QPushButton#server_off {
        background-color: rgba(120, 30, 30, 200);
        border: 1px solid rgba(200, 50, 50, 100);
    }
    QPushButton#mode_active {
        background-color: rgba(50, 100, 180, 200);
        border: 1px solid rgba(80, 150, 255, 150);
    }
    QFrame#separator {
        background-color: rgba(255, 255, 255, 20);
        max-height: 1px;
    }
"""


# ============================================
# ASYNC SIGNALS
# ============================================
class AsyncSignals(QObject):
    """Signals to communicate between async thread and UI thread."""
    transcript_received = pyqtSignal(str, float, str, bool)
    bulk_complete = pyqtSignal(str)
    connection_status = pyqtSignal(bool)
    server_status = pyqtSignal(str)
    error = pyqtSignal(str)
    download_progress = pyqtSignal(float, str)


# ============================================
# MAIN WINDOW — The Stealth Overlay
# ============================================
class TranscriptionOverlay(QMainWindow):
    """
    Main application window.
    Borderless, translucent, draggable, ghost-mode capable.
    """

    def __init__(self):
        super().__init__()
        self.signals = AsyncSignals()
        self.ghost_enabled = False
        self.is_live = False
        self.drag_position = None
        self.current_transcript = ""
        self.transcript_segments = []

        self._setup_window()
        self._build_ui()
        self._connect_signals()

        self.live_worker = LiveWorker(self.signals)
        self.bulk_worker = BulkWorker(self.signals)
        self.cloud = CloudController()

    def _setup_window(self):
        """Configure the borderless, translucent, always-on-top window."""
        self.setWindowTitle("Transcription")
        self.setFixedSize(480, 620)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(STYLESHEET)

    def _build_ui(self):
        """Construct all UI elements."""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(16, 12, 16, 12)
        main_layout.setSpacing(8)

        # TOP BAR
        top_bar = QHBoxLayout()
        title = QLabel("Transcription")
        title.setObjectName("title")
        top_bar.addWidget(title)
        top_bar.addStretch()

        self.ghost_btn = QPushButton("Ghost: OFF")
        self.ghost_btn.setFixedWidth(100)
        self.ghost_btn.clicked.connect(self._toggle_ghost)
        top_bar.addWidget(self.ghost_btn)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(30, 30)
        close_btn.clicked.connect(self.close)
        top_bar.addWidget(close_btn)
        main_layout.addLayout(top_bar)

        # SEPARATOR
        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFrameShape(QFrame.Shape.HLine)
        main_layout.addWidget(sep)

        # SERVER STATUS BAR
        server_bar = QHBoxLayout()
        self.server_status_label = QLabel("Server: Offline")
        self.server_status_label.setObjectName("status")
        server_bar.addWidget(self.server_status_label)
        server_bar.addStretch()

        self.server_btn = QPushButton("Start Server")
        self.server_btn.setObjectName("server_off")
        self.server_btn.setFixedWidth(120)
        self.server_btn.clicked.connect(self._toggle_server)
        server_bar.addWidget(self.server_btn)
        main_layout.addLayout(server_bar)

        # MODE TOGGLE
        mode_bar = QHBoxLayout()
        self.live_btn = QPushButton("Live Mode")
        self.live_btn.setObjectName("mode_active")
        self.live_btn.clicked.connect(lambda: self._switch_mode("live"))
        mode_bar.addWidget(self.live_btn)

        self.bulk_btn = QPushButton("Bulk Mode")
        self.bulk_btn.clicked.connect(lambda: self._switch_mode("bulk"))
        mode_bar.addWidget(self.bulk_btn)
        main_layout.addLayout(mode_bar)

        # STACKED WIDGET
        self.stack = QStackedWidget()
        self.live_panel = self._build_live_panel()
        self.stack.addWidget(self.live_panel)
        self.bulk_panel = self._build_bulk_panel()
        self.stack.addWidget(self.bulk_panel)
        main_layout.addWidget(self.stack)

    def _build_live_panel(self) -> QWidget:
        """Build the Live Mode view."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 8, 0, 0)

        self.live_text = QTextEdit()
        self.live_text.setReadOnly(True)
        self.live_text.setPlaceholderText(
            "Live transcript will appear here...\n\n"
            "1. Start the server\n"
            "2. Click 'Start Listening' below\n"
            "3. Play audio through your speakers"
        )
        layout.addWidget(self.live_text)

        self.model_label = QLabel("")
        self.model_label.setObjectName("status")
        layout.addWidget(self.model_label)

        self.listen_btn = QPushButton("Start Listening")
        self.listen_btn.clicked.connect(self._toggle_listening)
        layout.addWidget(self.listen_btn)
        return panel

    def _build_bulk_panel(self) -> QWidget:
        """Build the Bulk Mode view."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 8, 0, 0)

        url_row = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Paste YouTube URL here...")
        url_row.addWidget(self.url_input)

        self.download_btn = QPushButton("Transcribe")
        self.download_btn.setFixedWidth(100)
        self.download_btn.clicked.connect(self._start_bulk_transcription)
        url_row.addWidget(self.download_btn)
        layout.addLayout(url_row)

        self.bulk_status = QLabel("")
        self.bulk_status.setObjectName("status")
        layout.addWidget(self.bulk_status)

        self.bulk_text = QTextEdit()
        self.bulk_text.setReadOnly(True)
        self.bulk_text.setPlaceholderText("Transcript will appear here after processing...")
        layout.addWidget(self.bulk_text)

        export_row = QHBoxLayout()
        copy_btn = QPushButton("Copy to Clipboard")
        copy_btn.clicked.connect(self._copy_to_clipboard)
        export_row.addWidget(copy_btn)

        save_txt_btn = QPushButton("Save .txt")
        save_txt_btn.clicked.connect(lambda: self._save_transcript("txt"))
        export_row.addWidget(save_txt_btn)

        save_srt_btn = QPushButton("Save .srt")
        save_srt_btn.clicked.connect(lambda: self._save_transcript("srt"))
        export_row.addWidget(save_srt_btn)
        layout.addLayout(export_row)
        return panel

    def _connect_signals(self):
        """Wire up async signals to UI update methods."""
        self.signals.transcript_received.connect(self._on_transcript_received)
        self.signals.bulk_complete.connect(self._on_bulk_complete)
        self.signals.server_status.connect(self._on_server_status)
        self.signals.error.connect(self._on_error)
        self.signals.download_progress.connect(self._on_download_progress)

    def _toggle_ghost(self):
        hwnd = int(self.winId())
        if self.ghost_enabled:
            disable_ghost_mode(hwnd)
            self.ghost_btn.setText("Ghost: OFF")
            self.ghost_enabled = False
        else:
            enable_ghost_mode(hwnd)
            self.ghost_btn.setText("Ghost: ON")
            self.ghost_enabled = True

    def _toggle_server(self):
        current_text = self.server_btn.text()
        if current_text == "Start Server":
            self.server_btn.setText("Booting...")
            self.server_btn.setEnabled(False)
            self.server_status_label.setText("Server: Booting...")

            def _boot():
                loop = asyncio.new_event_loop()
                success = loop.run_until_complete(self.cloud.start_server())
                loop.close()
                if success:
                    server_ip = os.getenv("SERVER_IP", "localhost")
                    server_port = os.getenv("SERVER_PORT", "8000")
                    self.cloud.start_heartbeat(server_ip, server_port)
                    self.signals.server_status.emit("booting")
                else:
                    self.signals.error.emit("Failed to start server")
                    self.signals.server_status.emit("offline")

            threading.Thread(target=_boot, daemon=True).start()
            self._poll_server_ready()
        else:
            self.cloud.stop_heartbeat()
            def _shutdown():
                loop = asyncio.new_event_loop()
                loop.run_until_complete(self.cloud.stop_server())
                loop.close()
            threading.Thread(target=_shutdown, daemon=True).start()
            self.server_btn.setText("Start Server")
            self.server_btn.setObjectName("server_off")
            self.server_btn.setStyle(self.server_btn.style())
            self.server_status_label.setText("Server: Offline")

    def _poll_server_ready(self):
        self._poll_count = 0
        self._poll_timer = QTimer()
        self._poll_timer.setInterval(5000)

        def _check():
            self._poll_count += 1
            if self._poll_count > 24:
                self._poll_timer.stop()
                self.server_btn.setText("Start Server")
                self.server_btn.setObjectName("server_off")
                self.server_btn.setStyle(self.server_btn.style())
                self.server_btn.setEnabled(True)
                self.signals.error.emit("Server boot timed out (2 minutes)")
                return

            def _health_check():
                loop = asyncio.new_event_loop()
                from network.transmitter import LiveTransmitter
                t = LiveTransmitter()
                healthy = loop.run_until_complete(t.check_server_health())
                loop.close()
                if healthy:
                    self._poll_timer.stop()
                    self.signals.server_status.emit("online")

            threading.Thread(target=_health_check, daemon=True).start()

        self._poll_timer.timeout.connect(_check)
        self._poll_timer.start()

    def _switch_mode(self, mode: str):
        if mode == "live":
            self.stack.setCurrentIndex(0)
            self.live_btn.setObjectName("mode_active")
            self.bulk_btn.setObjectName("")
        else:
            self.stack.setCurrentIndex(1)
            self.bulk_btn.setObjectName("mode_active")
            self.live_btn.setObjectName("")
        self.live_btn.setStyle(self.live_btn.style())
        self.bulk_btn.setStyle(self.bulk_btn.style())

    def _toggle_listening(self):
        if not self.is_live:
            self.is_live = True
            self.listen_btn.setText("Stop Listening")
            self.live_text.clear()
            self.model_label.setText("Connecting...")
            self.live_worker.start()
        else:
            self.is_live = False
            self.listen_btn.setText("Start Listening")
            self.model_label.setText("Stopped")
            self.live_worker.stop()

    def _on_transcript_received(self, text, confidence, model, fallback):
        self.live_text.append(text)
        fallback_note = " (fallback)" if fallback else ""
        self.model_label.setText(
            f"Model: {model}{fallback_note} | Confidence: {confidence:.0%}"
        )

    def _start_bulk_transcription(self):
        url = self.url_input.text().strip()
        if not url:
            self._on_error("Please paste a YouTube URL")
            return
        self.download_btn.setEnabled(False)
        self.download_btn.setText("Working...")
        self.bulk_status.setText("Downloading audio...")
        self.bulk_text.clear()
        self.bulk_worker.start(url)

    def _on_bulk_complete(self, transcript):
        self.current_transcript = transcript
        self.bulk_text.setText(transcript)
        self.bulk_status.setText("Transcription complete")
        self.download_btn.setEnabled(True)
        self.download_btn.setText("Transcribe")

    def _on_download_progress(self, percent, status):
        self.bulk_status.setText(f"{status}: {percent:.0f}%")

    def _copy_to_clipboard(self):
        text = self.bulk_text.toPlainText()
        if not text:
            self._on_error("No transcript to copy")
            return
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        self.bulk_status.setText("Copied to clipboard!")

    def _save_transcript(self, format_type):
        text = self.bulk_text.toPlainText()
        if not text:
            self._on_error("No transcript to save")
            return
        if format_type == "txt":
            filter_str = "Text Files (*.txt)"
            default_ext = ".txt"
        else:
            filter_str = "Subtitle Files (*.srt)"
            default_ext = ".srt"

        filepath, _ = QFileDialog.getSaveFileName(
            self, "Save Transcript", f"transcript{default_ext}", filter_str
        )
        if not filepath:
            return
        try:
            if format_type == "srt":
                content = self._generate_srt(text)
            else:
                content = text
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            self.bulk_status.setText(f"Saved to {os.path.basename(filepath)}")
        except Exception as e:
            self._on_error(f"Save failed: {e}")

    def _generate_srt(self, text):
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        srt_blocks = []
        seconds_per_line = 3
        for i, line in enumerate(lines):
            start_seconds = i * seconds_per_line
            end_seconds = start_seconds + seconds_per_line
            start_ts = self._seconds_to_srt_time(start_seconds)
            end_ts = self._seconds_to_srt_time(end_seconds)
            srt_blocks.append(f"{i + 1}\n{start_ts} --> {end_ts}\n{line}\n")
        return "\n".join(srt_blocks)

    @staticmethod
    def _seconds_to_srt_time(seconds):
        td = timedelta(seconds=seconds)
        total_seconds = int(td.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        secs = total_seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d},000"

    def _on_error(self, message):
        self.bulk_status.setText(f"Error: {message}")
        self.bulk_status.setStyleSheet("color: #FF6B6B;")
        QTimer.singleShot(3000, lambda: self.bulk_status.setStyleSheet(""))

    def _on_server_status(self, status):
        self.server_status_label.setText(f"Server: {status.capitalize()}")
        if status == "online":
            self.server_btn.setText("Stop Server")
            self.server_btn.setObjectName("server_on")
            self.server_btn.setStyle(self.server_btn.style())
            self.server_btn.setEnabled(True)
        elif status == "offline":
            self.server_btn.setText("Start Server")
            self.server_btn.setObjectName("server_off")
            self.server_btn.setStyle(self.server_btn.style())
            self.server_btn.setEnabled(True)
        elif status == "booting":
            self.server_btn.setText("Booting...")
            self.server_btn.setEnabled(False)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if self.drag_position and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_position)

    def mouseReleaseEvent(self, event):
        self.drag_position = None

    def closeEvent(self, event):
        if self.is_live:
            self.live_worker.stop()
            self.is_live = False
        self.bulk_worker.stop()
        self.cloud.stop_heartbeat()

        def _shutdown():
            loop = asyncio.new_event_loop()
            loop.run_until_complete(self.cloud.stop_server())
            loop.close()

        shutdown_thread = threading.Thread(target=_shutdown, daemon=True)
        shutdown_thread.start()
        shutdown_thread.join(timeout=5)
        print("[APP] Shutdown complete")
        event.accept()


def run_app():
    """Launch the transcription overlay application."""
    app = QApplication(sys.argv)
    font = QFont("Segoe UI", 10)
    app.setFont(font)
    window = TranscriptionOverlay()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    run_app()
```

---

### File: `client/ui/workers.py` (261 lines)

```python
# client/ui/workers.py — Async Workers for Live and Bulk Pipelines
# The glue that connects: Audio Capture → Network → UI
#
# PROBLEM: PyQt6 has its own event loop (for UI).
#          asyncio has its own event loop (for network).
#          They can't share. So we run asyncio in a separate
#          thread and use Qt signals to send results back to UI.
#
# TWO WORKERS:
#   LiveWorker  — captures audio → streams via WSS → returns text
#   BulkWorker  — downloads YouTube → uploads via HTTP → returns text

import asyncio
import threading

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from audio.capture import AudioCapturer
from audio.youtube import YouTubeExtractor
from network.transmitter import LiveTransmitter, BulkTransmitter


# ============================================
# LIVE WORKER
# ============================================
class LiveWorker:
    """Manages the live transcription pipeline."""

    def __init__(self, signals):
        self.signals = signals
        self.capturer = AudioCapturer()
        self.transmitter = LiveTransmitter()
        self.running = False
        self.thread = None
        self.loop = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run_pipeline, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        self.capturer.stop()
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self.thread:
            self.thread.join(timeout=3)

    def _run_pipeline(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._async_pipeline())
        except Exception as e:
            self.signals.error.emit(f"Live pipeline error: {e}")
        finally:
            self.loop.close()

    async def _async_pipeline(self):
        # Step 1: Check server health
        is_healthy = await self.transmitter.check_server_health()
        if not is_healthy:
            self.signals.error.emit("Server is not responding. Start the server first.")
            self.signals.connection_status.emit(False)
            return

        # Step 2: Connect WebSocket
        try:
            await self.transmitter.connect()
            self.signals.connection_status.emit(True)
        except Exception as e:
            self.signals.error.emit(f"WebSocket connection failed: {e}")
            self.signals.connection_status.emit(False)
            return

        # Step 3: Start audio capture
        try:
            self.capturer.start()
        except Exception as e:
            self.signals.error.emit(f"Audio capture failed: {e}")
            await self.transmitter.disconnect()
            return

        # Step 4: Main loop
        try:
            while self.running:
                chunk = self.capturer.get_chunk(timeout=0.5)
                if chunk is None:
                    continue
                try:
                    result = await self.transmitter.send_chunk(chunk)
                    self.signals.transcript_received.emit(
                        result.get("transcript", ""),
                        result.get("confidence", 0.0),
                        result.get("model_used", "unknown"),
                        result.get("was_fallback", False)
                    )
                except Exception as e:
                    self.signals.error.emit(f"Transcription error: {e}")
                    break
        finally:
            self.capturer.stop()
            await self.transmitter.disconnect()
            self.signals.connection_status.emit(False)


# ============================================
# BULK WORKER
# ============================================
class BulkWorker:
    """Manages the bulk transcription pipeline."""

    def __init__(self, signals):
        self.signals = signals
        self.extractor = YouTubeExtractor()
        self.transmitter = BulkTransmitter()
        self.running = False
        self.thread = None

    def start(self, url: str):
        if self.running:
            self.signals.error.emit("Already processing a video. Please wait.")
            return
        self.running = True
        self.thread = threading.Thread(
            target=self._run_pipeline,
            args=(url,),
            daemon=True
        )
        self.thread.start()

    def stop(self):
        self.running = False

    def _run_pipeline(self, url: str):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._async_pipeline(url))
        except Exception as e:
            self.signals.error.emit(f"Bulk pipeline error: {e}")
        finally:
            self.running = False
            loop.close()

    async def _async_pipeline(self, url: str):
        # Step 1: Check server health
        is_healthy = await self.transmitter.check_server_health()
        if not is_healthy:
            self.signals.error.emit("Server is not responding. Start the server first.")
            return

        # Step 2: Download audio
        self.signals.download_progress.emit(0.0, "Starting download")
        try:
            filepath = self.extractor.download(
                url,
                progress_callback=lambda pct, status: (
                    self.signals.download_progress.emit(pct, status)
                )
            )
        except Exception as e:
            self.signals.error.emit(f"Download failed: {e}")
            return

        if not self.running:
            return

        # Step 3: Upload to server
        self.signals.download_progress.emit(100.0, "Uploading to server")
        try:
            result = await self.transmitter.upload_file(filepath)
            transcript = result.get("transcript", "No transcript returned")
            self.signals.bulk_complete.emit(transcript)
        except Exception as e:
            self.signals.error.emit(f"Transcription failed: {e}")

        # Step 4: Clean up
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
                print(f"[BULK] Cleaned up: {filepath}")
        except Exception:
            pass
```

---

### File: `server/main.py` (126 lines)

```python
# server/main.py — FastAPI Server Entry Point
# Phase 2: The Brain (skeleton first, models added next)
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, UploadFile, File
from fastapi.responses import JSONResponse
import uvicorn
from models.transcriber import TranscriptionRouter

router = TranscriptionRouter()

@asynccontextmanager
async def lifespan(app):
    # --- STARTUP: runs when server boots ---
    router.load_models()
    yield
    # --- SHUTDOWN: runs when server stops ---
    print("[SERVER] Shutting down, cleaning up resources")

app = FastAPI(title="Transcription Server", lifespan=lifespan)

# DOOR 1: WebSocket Endpoint — Live Mode
@app.websocket("/ws/transcribe")
async def websocket_transcribe(websocket: WebSocket):
    await websocket.accept()
    print("[LIVE] Client connected")
    try:
        while True:
            audio_chunk = await websocket.receive_bytes()
            result = router.transcribe(audio_chunk)
            await websocket.send_json({
                "status": "success",
                "transcript": result["text"],
                "confidence": result["confidence"],
                "model_used": result["model_used"],
                "was_fallback": result["was_fallback"]
            })
    except Exception as e:
        print(f"[LIVE] Client disconnected: {e}")

# DOOR 2: HTTP POST Endpoint — Bulk Mode
@app.post("/api/transcribe")
async def bulk_transcribe(file: UploadFile = File(...)):
    audio_data = await file.read()
    file_size = len(audio_data)
    print(f"[BULK] Received file: {file.filename}, size: {file_size} bytes")
    result = router.transcribe(audio_data)
    return JSONResponse(content={
        "status": "success",
        "filename": file.filename,
        "transcript": result["text"],
        "confidence": result["confidence"],
        "model_used": result["model_used"],
        "was_fallback": result["was_fallback"]
    })

# HEALTH CHECK
@app.get("/health")
async def health_check():
    return {"status": "alive"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

---

### File: `server/models/transcriber.py` (224 lines)

```python
# server/models/transcriber.py — AI Model Loading & Confidence-Based Routing
# Phase 3: REAL models replacing mocks

import io
import numpy as np
import soundfile as sf
import torch

CONFIDENCE_THRESHOLD = 0.7


class RealCanaryQwen:
    """Primary model — fast, good contextual understanding."""
    def __init__(self):
        self.name = "Canary-Qwen-2.5B"
        self.model = None

    def load(self):
        import nemo.collections.asr as nemo_asr
        print(f"[MODEL] Loading {self.name}...")
        self.model = nemo_asr.models.ASRModel.from_pretrained("nvidia/canary-1b")
        self.model = self.model.cuda()
        self.model.eval()
        print(f"[MODEL] {self.name} ready")

    def transcribe(self, audio_bytes: bytes) -> dict:
        if self.model is None:
            raise RuntimeError(f"{self.name} not loaded!")
        audio_buffer = io.BytesIO(audio_bytes)
        waveform, sample_rate = sf.read(audio_buffer, dtype='float32')
        if waveform.ndim == 2:
            waveform = waveform.mean(axis=1)
        if sample_rate != 16000:
            import librosa
            waveform = librosa.resample(waveform, orig_sr=sample_rate, target_sr=16000)
        with torch.no_grad():
            output = self.model.transcribe([waveform], batch_size=1)
        text = output[0] if isinstance(output[0], str) else output[0].text
        try:
            confidence = float(torch.exp(torch.tensor(output[0].score)).item())
            confidence = max(0.0, min(1.0, confidence))
        except Exception:
            confidence = 0.85
        return {"text": text, "confidence": round(confidence, 2), "model": self.name}


class RealWhisperLargeV3:
    """Fallback model — extremely robust against noise."""
    def __init__(self):
        self.name = "Whisper-Large-v3"
        self.model = None
        self.processor = None

    def load(self):
        from transformers import WhisperForConditionalGeneration, WhisperProcessor
        print(f"[MODEL] Loading {self.name}...")
        self.processor = WhisperProcessor.from_pretrained("openai/whisper-large-v3")
        self.model = WhisperForConditionalGeneration.from_pretrained("openai/whisper-large-v3")
        self.model = self.model.cuda()
        self.model.eval()
        print(f"[MODEL] {self.name} ready")

    def transcribe(self, audio_bytes: bytes) -> dict:
        if self.model is None:
            raise RuntimeError(f"{self.name} not loaded!")
        audio_buffer = io.BytesIO(audio_bytes)
        waveform, sample_rate = sf.read(audio_buffer, dtype='float32')
        if waveform.ndim == 2:
            waveform = waveform.mean(axis=1)
        if sample_rate != 16000:
            import librosa
            waveform = librosa.resample(waveform, orig_sr=sample_rate, target_sr=16000)
        inputs = self.processor(
            waveform, sampling_rate=16000, return_tensors="pt"
        ).input_features.to("cuda")
        with torch.no_grad():
            outputs = self.model.generate(
                inputs, output_scores=True, return_dict_in_generate=True
            )
        text = self.processor.batch_decode(
            outputs.sequences, skip_special_tokens=True
        )[0]
        try:
            scores = torch.stack(outputs.scores, dim=1)
            token_probs = torch.exp(scores.max(dim=-1).values)
            confidence = float(token_probs.mean().item())
            confidence = max(0.0, min(1.0, confidence))
        except Exception:
            confidence = 0.90
        return {"text": text.strip(), "confidence": round(confidence, 2), "model": self.name}


class TranscriptionRouter:
    def __init__(self):
        self.canary = RealCanaryQwen()
        self.whisper = RealWhisperLargeV3()

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
```

---

### File: `server/deploy.sh` (66 lines)

```bash
#!/bin/bash
set -e
# server/deploy.sh — Run this on the GPU droplet after SSH-ing in
# Sets up everything needed to run the transcription server

echo "=========================================="
echo "  TRANSCRIPTION SERVER — DEPLOYMENT SETUP"
echo "=========================================="

# STAGE 1: System Update
echo ""
echo "[1/4] Updating system packages..."
apt update && apt upgrade -y

# STAGE 2: Python Environment
echo ""
echo "[2/4] Setting up Python environment..."
cd /root/transcription-server
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# STAGE 3: Download AI Models
echo ""
echo "[3/4] Downloading AI models (this takes a while)..."

python3 -c "
from transformers import WhisperForConditionalGeneration, WhisperProcessor
print('[MODEL] Downloading Whisper Large-v3...')
WhisperProcessor.from_pretrained('openai/whisper-large-v3')
WhisperForConditionalGeneration.from_pretrained('openai/whisper-large-v3')
print('[MODEL] Whisper Large-v3 downloaded')
"

python3 -c "
import nemo.collections.asr as nemo_asr
print('[MODEL] Downloading Canary Qwen 2.5B...')
nemo_asr.models.ASRModel.from_pretrained('nvidia/canary-1b')
print('[MODEL] Canary downloaded')
"

# STAGE 4: Start the Server
echo ""
echo "[4/4] Starting transcription server..."
echo "Server will be available on port 8000"
cd /root/transcription-server/server
python3 main.py
```

---

### File: `gpu_sniper.py` (175 lines)

```python
import requests
import time
import os
from dotenv import load_dotenv

load_dotenv()

API_TOKEN        = os.getenv("DO_API_TOKEN")
SSH_KEY_ID       = 54989881
GPU_SIZE         = "gpu-rtx4000-ada-1x"
IMAGE            = "ubuntu-22-04-x64"
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

REGIONS = ["nyc2", "sfo3", "atl1", "tor1", "ams3"]

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {API_TOKEN}"
}

POLL_INTERVAL = 30

def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print("  Telegram message sent successfully")
        else:
            print(f"  Telegram failed: {response.text}")
    except Exception as e:
        print(f"  Telegram error: {e}")

def test_telegram():
    print("  Sending Telegram test message...")
    send_telegram(
        "<b>GPU Sniper is now ACTIVE</b>\n\n"
        f"Targeting: {GPU_SIZE}\n"
        f"Regions: {', '.join(REGIONS)}\n"
        f"Polling every {POLL_INTERVAL} seconds\n\n"
        "I will message you the moment a GPU is claimed."
    )

def try_claim(region):
    payload = {
        "name": f"gpu-sniper-{region}",
        "region": region,
        "size": GPU_SIZE,
        "image": IMAGE,
        "ssh_keys": [54989881],
        "tags": ["gpu-hunter"]
    }
    try:
        response = requests.post(
            "https://api.digitalocean.com/v2/droplets",
            headers=HEADERS, json=payload, timeout=15
        )
        if response.status_code == 202:
            data = response.json()
            droplet_id = data["droplet"]["id"]
            droplet_name = data["droplet"]["name"]
            print(f"\n{'=' * 50}")
            print(f"  GPU CLAIMED in {region.upper()}!")
            print(f"  Droplet ID: {droplet_id}")
            print(f"{'=' * 50}\n")
            send_telegram(
                f"<b>GPU CLAIMED!</b>\n\nRegion: <b>{region.upper()}</b>\n"
                f"Droplet ID: <b>{droplet_id}</b>\nName: {droplet_name}\n\n"
                f"Go to DigitalOcean dashboard NOW\n"
                f"ssh root@YOUR_NEW_IP\n\nBilling has started!"
            )
            return True
        elif response.status_code == 422:
            print(f"  [{region.upper()}] Out of stock — waiting...")
            return False
        elif response.status_code == 403:
            print(f"  [{region.upper()}] Auth failed — check your API token")
            send_telegram("GPU Sniper: Auth failed — check your API token")
            return False
        else:
            print(f"  [{region.upper()}] Unexpected {response.status_code}: {response.text[:100]}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"  [{region.upper()}] Network error: {e} — will retry")
        return False

def run_sniper():
    attempt = 0
    print(f"\n{'=' * 50}")
    print("  GPU SNIPER ACTIVE")
    print(f"  Targeting: {GPU_SIZE}")
    print(f"  Regions:   {', '.join(REGIONS)}")
    print(f"  Polling every {POLL_INTERVAL} seconds")
    print("  Press Ctrl+C to stop")
    print(f"{'=' * 50}\n")
    test_telegram()

    while True:
        attempt += 1
        print(f"[Attempt #{attempt}] Checking all regions...")
        for region in REGIONS:
            success = try_claim(region)
            if success:
                print("Sniper complete. Script stopped.")
                print("DO NOT run this again or you will buy a second GPU.")
                return
            time.sleep(2)
        print(f"  All regions dry. Next check in {POLL_INTERVAL} seconds...\n")
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    if not API_TOKEN:
        print("ERROR: DO_API_TOKEN not found in .env file")
    elif not TELEGRAM_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not found in .env file")
    elif not TELEGRAM_CHAT_ID:
        print("ERROR: TELEGRAM_CHAT_ID not found in .env file")
    else:
        run_sniper()
```

---

## 4. A4000 vs A100 GPU Comparison

| Spec | A4000 | A100 |
|------|-------|------|
| VRAM | 16 GB | 80 GB |
| Memory bandwidth | 448 GB/s | 2,039 GB/s |
| CUDA cores | 6,144 | 6,912 |
| Tensor cores | 192 | 432 |
| FP16 performance | ~19.2 TFLOPS | ~77.9 TFLOPS |
| Architecture | Ampere (consumer) | Ampere (datacenter) |

**Why A100 wins for our project:**
- Our models need ~16 GB VRAM total. A4000 has exactly 16 GB — zero headroom. Any memory spike crashes it.
- A100 at 80 GB gives 64 GB of breathing room.
- ~4.5x memory bandwidth = faster model inference (model weights read faster during each forward pass).
- A100 processes each audio chunk roughly 3-4x faster than A4000.

**Decision: Use A100 from HPC cluster labs.**

---

## 5. HPC cluster — Account Setup & Access

### Account Activation Email Received

```
Your account has now been setup on the REDACTED_HOST HPC cluster and you now have access.
Documentation: https://docs.pages.REDACTED_DOMAIN/research_computing/hpc/clusters/REDACTED_HOST.html
```

### Access Methods

| Method | Details |
|--------|---------|
| SSH | `ssh REDACTED_USER@REDACTED_HOST` |
| Web GUI | `https://REDACTED_HOST-ondemand.REDACTED_DOMAIN/` |
| Remote Labs | `https://remotelabs.eps.REDACTED_DOMAIN/#/` |
| Username | `REDACTED_USER` |

### REDACTED_HOST OnDemand Web Interface

Logged in successfully. Available pinned apps: Abaqus, ANSYS, Code Server, COMSOL, FSL, JupyterLab, Mathematica, MATLAB, ParaView, RStudio Server, STATA. Web terminal accessible via Clusters menu.

---

## 6. REDACTED_HOST Cluster Environment Discovery

### Commands Run & Results

All commands run via the OnDemand web terminal to avoid pager issues in SSH.

```bash
module spider Python 2>&1 | head -60; echo "===BREAK==="; module spider cuda 2>&1 | head -40; echo "===BREAK==="; module spider anaconda 2>&1 | head -40; echo "===BREAK==="; module spider miniconda 2>&1 | head -40; echo "===BREAK==="; echo $HOME; echo "===BREAK==="; df -h ~; echo "===BREAK==="; which python3; python3 --version; echo "===BREAK==="; ls /mnt/fast 2>&1; echo "===BREAK==="; sinfo -p gpu
```

### Results Summary

**Python versions available (via module system):**
- Python/2.7.14 through Python/3.13.1
- Key versions: Python/3.10.4, Python/3.10.8, Python/3.11.3, Python/3.11.5, Python/3.12.3, Python/3.13.1

**CUDA versions available:**
- CUDA/11.4.1
- CUDA/11.7.0
- CUDA/12.0.0
- CUDA/12.2.0
- CUDA/12.2.2 (latest)

**Anaconda available:**
- Anaconda2/2019.10 (Python 2 — skip)
- Anaconda3/2021.05, Anaconda3/2021.11, Anaconda3/2022.10
- **Anaconda3/2024.02-1** (latest, Python 3)

**Miniconda:** Not found (`lmod has detected the following error: Unable to find: "miniconda"`)

**Home directory:** `/users/REDACTED_USER`

**Storage:** 30 GB total, 1.8 GB used, 30 GB available (1% used). Mounted via NFS: `nfs01-ice1-mgmt:/REDACTED_HOST/users/REDACTED_USER`

**System Python:** `/usr/bin/python3` → Python 3.6.8 (too old — need 3.10+)

**WEKA fast storage:** `/mnt/fast` does NOT exist (`ls: cannot access '/mnt/fast': No such file or directory`)

**GPU partition status:**

| Node | State | Meaning |
|------|-------|---------|
| gpu-node01 | alloc | Fully allocated (all GPUs in use) |
| gpu-node02 | mix | Partially available (some GPUs free) |

**Time limit:** 7 days maximum per job

**GPU hardware:** 6x NVIDIA A100 80GB across 2 nodes. Some A100s partitioned into MIG slices (e.g., `2g.20gb`).

### Key Environment Implications

- No WEKA fast storage — model weights go in home directory (~7 GB)
- 30 GB home storage — enough for conda env (~4-6 GB) + models (~7 GB) + code
- NFS-mounted home = shared across login node and all compute nodes (install once, access everywhere)
- System Python 3.6.8 too old — must use module system or Anaconda

---

## 7. SSH Tunnel — What It Is and How It Works

### The Problem

HPC GPU compute nodes sit behind the login node on a **private internal network**. They don't have public IP addresses. Your laptop can only SSH into the login node (`REDACTED_HOST`). The GPU node (where your FastAPI server is running) has an internal address like `gpu-node-03` that only exists inside the cluster's network.

### The Solution

An SSH tunnel is **port forwarding through an encrypted SSH connection**. It makes a service running on a remote machine appear as if it's running on your local machine.

### The Command

```bash
ssh -L 8000:gpu-node-02:8000 REDACTED_USER@REDACTED_HOST
```

Breaking it down:
- `-L` — local port forwarding (forward a port from my local machine to a remote destination)
- First `8000` — the port on **your laptop** that you'll connect to
- `gpu-node-02:8000` — the **destination** inside the cluster (the GPU node running your server)
- `REDACTED_USER@REDACTED_HOST` — the **middleman** (the login node)

### Data Flow

```
Your client code (transmitter.py)
    │
    │ sends audio to localhost:8000
    │
    ▼
Your laptop's port 8000
    │
    │ SSH encrypts and forwards
    │
    ▼
REDACTED_HOST (login node)
    │
    │ internal cluster network
    │
    ▼
gpu-node-02:8000 (FastAPI server)
    │
    │ Canary/Whisper processes audio
    │
    ▼
JSON response travels back the same path in reverse
```

### Why SSH Tunnel (Not Reverse Proxy / VPN)

SSH tunnels are the standard for HPC because you don't have admin privileges on shared clusters. You can't install nginx, configure VPNs, or open firewall ports. SSH is already the only entry point, and `-L` port forwarding requires zero cluster-side configuration.

### Key Points

- The tunnel is **invisible to the application** — `transmitter.py` just connects to `localhost:8000`
- The FastAPI server is **already running** on the GPU node before you open the tunnel. The tunnel just creates the path for your laptop to reach it
- The tunnel encrypts everything in transit (same protection as TLS/WSS)
- Models are loaded once when the Slurm job starts. Every request after that just uses what's already in VRAM

---

## 8. Decision: Anaconda3 vs venv for HPC Python Environment

### Decision Point

We need a Python environment on the cluster. Two options evaluated:

### Option A — Python module + venv
- Load `Python/3.10.8` from cluster's module system
- Create virtual environment with `python -m venv`
- Install dependencies with `pip install`
- Uses ~2-3 GB of home directory
- Same workflow as DigitalOcean

### Option B — Anaconda3 + conda env (CHOSEN)
- Load `Anaconda3/2024.02-1` from cluster's module system
- Create conda environment with `conda create`
- Install dependencies with `conda install` / `pip install`
- Uses ~4-6 GB of home directory
- Conda automatically bundles matching CUDA/cuDNN libraries inside the environment

### Comparison

| Factor | Option A (venv) | Option B (conda) |
|--------|----------------|-----------------|
| Disk usage | ~2-3 GB | ~4-6 GB |
| CUDA handling | Must match module versions manually | Bundles automatically |
| Failure risk | Higher (version mismatch possible) | Lower |
| Learning curve | Familiar `pip install` only | New commands: `conda create`, `conda activate` |

### Why Anaconda3 Was Chosen

When you run `conda install pytorch`, conda automatically pulls in the exact CUDA libraries that match. You don't need to figure out which `module load CUDA/X.X.X` is compatible with which PyTorch build. This eliminates the most common failure point on HPC clusters — CUDA version mismatches.

The disk cost is higher (~4-6 GB vs ~2-3 GB), but with 28 GB free, that's plenty.

### Key Concepts Explained

- **`module load`** — activates a specific software package on the cluster. Modifies PATH for current session only.
- **`module load CUDA/12.2.2`** — makes NVIDIA's GPU programming toolkit visible so PyTorch can find it.
- **`venv` (virtual environment)** — self-contained folder with Python + installed packages. Isolates project dependencies.
- **`conda` (from Anaconda)** — package manager that handles both Python and non-Python dependencies (like CUDA libraries).
- **`pip`** — Python's default package installer. Works inside both venv and conda environments.

---

## 9. Step 4 Phase A Plan — Three New Files

### File 1: `server/surrey_job.sh` — The Slurm Job Script

**What it does:** The file you submit to the cluster's job scheduler. Contains `#SBATCH` directives telling Slurm what resources you need (1 A100 GPU, time, memory), then commands to load modules, activate conda env, and start FastAPI server.

**When you run it:** Every time you want to start a transcription session. Run `sbatch surrey_job.sh`.

**How it connects:** Replaces the manual process on DigitalOcean where you SSH'd in and ran `deploy.sh`. Slurm handles the queuing, your server commands ride inside the script.

### File 2: `server/deploy_surrey.sh` — One-Time Environment Setup

**What it does:** Creates the conda environment, installs all dependencies (PyTorch, FastAPI, NeMo, Transformers), and downloads model weights to home directory.

**When you run it:** Once on the login node. Conda env and model files persist in home directory (shared via NFS across all nodes).

**How it connects:** Replaces `deploy.sh` from DigitalOcean. Same purpose, different commands (conda instead of venv, module load instead of apt install).

### File 3: `server/tunnel.sh` — SSH Tunnel Helper Script

**What it does:** Opens the SSH tunnel from your Windows laptop so `localhost:8000` forwards to the GPU node.

**When you run it:** After your Slurm job starts and you know which GPU node was assigned.

**How it connects:** The bridge between existing client code and Surrey GPU. `transmitter.py` already targets `localhost:8000` in dev mode.

### Workflow

```
ONE TIME (setup):
  SSH into REDACTED_HOST → run deploy_surrey.sh → conda env + models ready

EVERY SESSION:
  1. SSH into REDACTED_HOST → run: sbatch surrey_job.sh
  2. Wait for job to start → check which GPU node assigned
  3. On your laptop → run: tunnel.sh gpu-node02
  4. Client app connects to localhost:8000 → tunnel forwards to GPU node
  5. When done → scancel the job → tunnel closes
```

### Time Limit Decision

**Chosen:** 4 hours per session. Long enough for development and testing, short enough to not waste queue time if forgotten. Cluster maximum is 7 days.

---

## 10. Key Concepts Taught This Session

| Concept | Definition |
|---------|-----------|
| HPC (High Performance Computing) | A shared cluster of powerful computers with GPUs, managed by a job scheduler |
| Slurm | The job scheduler — manages the queue of GPU requests from all users |
| `sbatch` | Command to submit a job script to Slurm |
| `scancel` | Command to cancel a running Slurm job |
| `sinfo` | Command to check current cluster status (which nodes are free/busy) |
| SSH tunnel | Port forwarding through an encrypted SSH connection — makes a remote service appear local |
| `-L` flag | Local port forwarding in SSH |
| `module load` | Activates pre-installed software on an HPC cluster |
| `conda` | Package manager that handles Python + non-Python dependencies (like CUDA) |
| NFS | Network File System — home directory shared across all cluster nodes |
| MIG (Multi-Instance GPU) | NVIDIA feature that partitions one A100 into smaller virtual GPUs |
| `alloc` state | Slurm node fully allocated — all resources in use |
| `mix` state | Slurm node partially available — some GPUs free |
| Login node | The entry point you SSH into — not for running jobs, only for submitting them |
| Compute node | The actual GPU machine where your job runs |
| WEKA storage | Fast NVMe storage on some HPC clusters (not available on REDACTED_HOST) |

---

## 11. Roadmap Status After Session 8

| Step | Description | Status |
|------|-------------|--------|
| 1 | Deploy server skeleton, verify endpoints | ✅ Complete (Session 6) |
| 2 | Replace mock models with real AI inference | ✅ Complete (Session 6) |
| 3 | Verify actual transcription works | ✅ Complete (Session 7) |
| 4a | HPC cluster infrastructure setup | ⬜ In Progress — environment discovered, scripts planned |
| 4b | Wire end-to-end live pipeline | ⬜ Pending |
| 4c | Wire end-to-end bulk pipeline | ⬜ Pending |
| 4d | Full end-to-end testing | ⬜ Pending |
| 5 | Production hardening (TLS, reconnection) | ⬜ Pending |

### Infrastructure Status

| Resource | Status |
|----------|--------|
| DigitalOcean droplet | Destroyed (snapshot saved: `transcription-server-step3-complete`, 42 GB, TOR1) |
| HPC cluster account | Active — `REDACTED_USER@REDACTED_HOST` |
| GPU available | A100 80GB on gpu-node02 (mix state = partially available) |
| Conda environment | Not yet created (deploy_surrey.sh not yet built) |
| Model weights on cluster | Not yet uploaded (part of deploy_surrey.sh) |

---

## 12. Interview Prep — Session 8

### Q: Why use an SSH tunnel instead of a reverse proxy or VPN on an HPC cluster?

**A:** SSH tunnels are the standard for HPC because you don't have admin privileges on shared clusters. You can't install nginx, configure VPNs, or open firewall ports. SSH is already the only entry point, and `-L` port forwarding requires zero cluster-side configuration — just your user account and an SSH key. It also encrypts all traffic, providing the same protection as TLS.

### Q: Why choose conda over pip+venv for an HPC environment?

**A:** The main advantage of conda on HPC is CUDA library management. When you `conda install pytorch`, conda automatically bundles matching CUDA and cuDNN libraries inside the environment. With pip+venv, you rely on the cluster's CUDA module being loaded and version-compatible with PyTorch. CUDA version mismatches are the most common failure point for GPU applications on HPC clusters. Conda eliminates that risk at the cost of slightly higher disk usage.

### Q: What's the difference between a login node and a compute node?

**A:** The login node is the entry point where you SSH in. It's shared by all users and is only for submitting jobs, editing files, and managing your environment. It has no GPUs. The compute node is the actual powerful machine with GPUs where your job runs. Slurm assigns compute nodes based on your job's resource request. You never SSH directly to compute nodes — Slurm handles the scheduling and you access your running job through SSH tunnels or Slurm's output logs.

---

---

## 13. Troubleshooting Log — Session 8

### Issue 1: Terminal Pager Blocking Output

**What happened:** When running `module spider python`, `sinfo -p gpu`, and other commands via SSH, the output was displayed inside `less` (a pager program). The terminal showed `lines 1-15` at the bottom and froze, waiting for navigation input.

**Why it happens:** The REDACTED_HOST cluster's module system pipes long output through `less` by default. This is a common HPC configuration to prevent long outputs from scrolling past too fast.

**How we solved it:**
1. Press `q` to exit the pager
2. Pipe output through `head` to bypass the pager entirely: `module spider Python 2>&1 | head -60`
3. Switched to the **REDACTED_HOST OnDemand web terminal** (`https://REDACTED_HOST-ondemand.REDACTED_DOMAIN/` → Clusters → Shell Access) where copy-paste works and output is easier to manage

**Prevention:** Always pipe HPC module commands through `head` or redirect to a file when running via SSH. The web terminal is generally easier for exploration.

### Issue 2: Copy-Paste Not Working in SSH Terminal

**What happened:** When connected via `ssh REDACTED_USER@REDACTED_HOST` from the local terminal, copy-paste wasn't working to transfer the long compound command.

**How we solved it:** Switched to the OnDemand web GUI terminal at `https://REDACTED_HOST-ondemand.REDACTED_DOMAIN/` which runs in the browser and supports normal copy-paste.

---

## 14. Comprehension Checks — Session 8

### SSH Tunnel Comprehension (Teaching Protocol Rule 5)

**First attempt by student:**
> "The tunnel just makes a secure HTTP connection and puts a reverse proxy of my localhost 8000 which is called by the interface at my local PC and then it forwards the calls to the FastAPI server which connects to the GPU server."

**Corrections given:**
1. It's not a reverse proxy. A reverse proxy is a separate piece of software (like nginx). The SSH tunnel is port forwarding built into SSH itself — not a separate program.
2. The FastAPI server doesn't "connect to" the GPU — it runs **directly on** the GPU node. The GPU node is a full computer with CPU, RAM, and an A100 GPU card.

**Second attempt by student:**
> "When I give a Slurm request, the SSH routing helps to get the localhost 8000 to the server. When I am allocated to the GPU, the localhost is called which then gets forwarded to the GPU server via SSH which calls the GPU and all the models and then all the transcription is done."

**Corrections given:**
1. The SSH tunnel doesn't "call the GPU." The FastAPI server is **already running** on the GPU node before you even open the tunnel.
2. Models are loaded **once** when the Slurm job starts. The tunnel only carries audio bytes in and JSON text out.

**Final corrected understanding:**
1. Slurm job starts → FastAPI server launches on GPU node with models in VRAM
2. SSH tunnel opened from laptop to that GPU node (through login node)
3. Client code calls `localhost:8000` → SSH forwards through login node → GPU node port 8000
4. FastAPI receives audio, runs through Canary/Whisper, sends JSON back
5. Response travels back through the same tunnel

### Three Files Comprehension (Teaching Protocol Rule 5)

**Student's explanation:**
> "surrey_job.sh makes the request of Slurm to get the GPU required and it reads what GPU are required and how many then gives the request to the server. deploy_surrey.sh basically creates the conda requirements — how numpy will be able to talk to the CUDA libraries and by itself downloads the required ones. tunnel.sh is a link between my localhost which is forwarded via the SSH into the GPU node where all the models are loaded via the deploy_surrey.sh."

**Corrections given:**
1. `surrey_job.sh` gives the request to **Slurm** (the job scheduler), not to the FastAPI server. Slurm reads the `#SBATCH` lines, waits for a GPU node to become free, then runs the rest of the script on that node.
2. `deploy_surrey.sh` runs **once on the login node**, not on the GPU node. The conda environment and models live in the home directory which is shared across all nodes via NFS.

---

## 15. REDACTED_HOST SSH Login — Exact Output

```
(REDACTED_USER@REDACTED_HOST) Password:
    +++++++++++++++++++++++++++++++++++++++++
      Welcome to Surrey's REDACTED_HOST HPC Cluster
             Based on Rocky 8
    +++++++++++++++++++++++++++++++++++++++++
Documentation for HPC users is at below site:
https://docs.pages.REDACTED_DOMAIN/research_computing/
Last failed login: Tue Mar 24 16:47:42 GMT 2026 from 192.168.86.217 on ssh:notty
There were 6 failed login attempts since the last successful login.
[REDACTED_USER@login1 (REDACTED_HOST) ~]$
```

Note: 6 failed login attempts before successful login — likely from earlier password attempts.

---

## 16. Slurm GPU Request Syntax (from web search)

From the REDACTED_HOST documentation (retrieved via web search):

```bash
# Request a full A100 GPU:
#SBATCH --partition=gpu
#SBATCH --gres=gpu:a100:1

# Request a MIG partition (smaller slice):
#SBATCH --gres=gpu:2g.20gb:1
```

The cluster has 6x A100 80GB GPUs across 2 nodes. Some are split into MIG (Multi-Instance GPU) partitions like `2g.20gb` for smaller jobs.

For our project (Canary ~6GB + Whisper ~10GB = ~16GB), even a MIG slice would work, but a full A100 gives massive headroom and faster inference.

---

*Last updated: Session 8 — HPC cluster account active, REDACTED_HOST environment discovered (A100 80GB, Anaconda3/2024.02-1, CUDA/12.2.2, 30GB home storage, no WEKA), SSH tunnel concept taught, Anaconda3 chosen over venv, three Surrey scripts planned (surrey_job.sh, deploy_surrey.sh, tunnel.sh), 4-hour time limit chosen. Troubleshooting log added (pager issues, copy-paste fix). Comprehension checks documented. Waiting to build scripts. Nothing skipped.*
