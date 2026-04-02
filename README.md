# Real-Time Audio Transcription System

A real-time audio transcription application with a confidence-based dual AI model router, live WebSocket streaming, and a stealth desktop overlay built with PyQt6.

Captures system audio (speakers) and microphone simultaneously, streams it to a GPU-powered transcription server, and displays results in a translucent overlay that can be made invisible to screen capture software.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     CLIENT (Windows)                         │
│                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌───────────────┐  │
│  │ DualCapturer │───>│ LiveWorker   │───>│ Overlay UI    │  │
│  │              │    │ (asyncio)    │    │ (PyQt6)       │  │
│  │ - Speaker    │    │              │    │               │  │
│  │   (WASAPI)   │    │ Dedup +      │    │ - Live panel  │  │
│  │ - Microphone │    │ Staleness    │    │ - Bulk panel  │  │
│  │              │    │ guard        │    │ - Ghost mode  │  │
│  └──────────────┘    └──────┬───────┘    │ - Rec Panel  │  │
│                             │            └───────────────┘  │
│                             │ WebSocket                      │
│                             │ (audio chunks)                 │
└─────────────────────────────┼───────────────────────────────┘
                              │
                    SSH Tunnel │ or Direct IP
                              │
┌─────────────────────────────┼───────────────────────────────┐
│                     SERVER (GPU)                             │
│                             │                                │
│                     ┌───────▼───────┐                        │
│                     │  FastAPI      │                        │
│                     │  /ws/transcribe (live)                 │
│                     │  /api/transcribe (bulk)                │
│                     │  /health                               │
│                     └───────┬───────┘                        │
│                             │                                │
│                     ┌───────▼───────┐                        │
│                     │ Transcription │                        │
│                     │ Router        │                        │
│                     │               │                        │
│                     │ confidence    │                        │
│                     │ >= 0.7?       │                        │
│                     ┌───┴───┐       │                        │
│                  YES│       │NO     │                        │
│              ┌──────▼──┐ ┌──▼──────┐│                        │
│              │ Canary   │ │ Whisper ││                        │
│              │ 1B       │ │ Large   ││                        │
│              │ (NeMo)   │ │ v3      ││                        │
│              │          │ │ (HF)    ││                        │
│              └──────────┘ └─────────┘│                        │
│                                      │                        │
└──────────────────────────────────────────────────────────────┘
```

**Data flow:** Audio is captured from speakers and microphone via WASAPI loopback, chunked into WAV segments, sent over WebSocket to the server, routed through a confidence-based dual model system (Canary first, Whisper as fallback), and the transcript is streamed back to the UI in real time.

---

## Features

**Client (Desktop Overlay)**
- Borderless translucent dark-mode overlay, always on top
- Ghost mode — invisible to screen sharing (Zoom, Teams, OBS) using Windows API `SetWindowDisplayAffinity`
- Floating recording panel — dark translucent panel during live recording with scrollable transcript, elapsed timer, and auto-scroll
- Dual audio capture — system speakers (WASAPI loopback) + microphone simultaneously
- Live transcript de-duplication with fuzzy matching (SequenceMatcher) and staleness guard
- Bulk transcription from YouTube URLs via yt-dlp
- Export to clipboard, .txt, or .srt subtitle format
- Custom title bars for easy window dragging

**Server (GPU Transcription)**
- Confidence-based dual model router: NVIDIA Canary 1B (primary) → OpenAI Whisper Large-v3 (fallback)
- Canary outputs per-token confidence from log probabilities; if below 0.7 threshold, Whisper takes over
- WebSocket endpoint for live streaming, HTTP POST for bulk uploads
- GPU inference offloaded to background thread (`asyncio.to_thread`) to keep event loop responsive

**Network & Reliability**
- WebSocket auto-reconnect with exponential backoff + jitter (prevents thundering herd)
- HTTP upload retry with backoff (retryable: network errors; non-retryable: HTTP 400/500)
- Background health monitor pings `/health` every 30 seconds, emits UI updates on state change
- Heartbeat system for DigitalOcean mode — server auto-shuts down if no ping for 5 minutes

---

## Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| UI Framework | PyQt6 | Desktop overlay with translucent styling, signals/slots for thread communication |
| Audio Capture | PyAudioWPatch (WASAPI) | System audio loopback + microphone, Windows-native low-latency capture |
| Primary Model | NVIDIA Canary 1B (NeMo) | Fast multilingual transcription (en, de, fr, es) with confidence scoring |
| Fallback Model | OpenAI Whisper Large-v3 (HuggingFace) | Robust noise-tolerant transcription, 90+ language auto-detection |
| Server Framework | FastAPI + Uvicorn | Async ASGI server with WebSocket and HTTP endpoints |
| Live Transport | WebSockets | Persistent bidirectional connection for real-time audio streaming |
| Video Download | yt-dlp | YouTube audio extraction for bulk transcription |
| GPU Compute | NVIDIA A100 (MIG 3g.40gb) | HPC cluster GPU via Slurm job scheduler |
| Infrastructure | HPC cluster / DigitalOcean | Dual deployment targets switchable via .env config |

---

## Project Structure

```
transcription-app/
├── .env.example                 # Configuration template (copy to .env)
├── .gitignore                   # Excludes secrets, models, venv, downloads
├── requirements.txt             # All Python dependencies
├── setup.py                     # Initial project setup script
│
├── client/                      # Runs on your Windows machine
│   ├── main.py                  # Entry point — launches the overlay
│   ├── audio/
│   │   ├── capture.py           # DualCapturer — WASAPI speaker + mic capture
│   │   └── youtube.py           # yt-dlp wrapper — YouTube audio download
│   ├── network/
│   │   ├── transmitter.py       # LiveTransmitter (WSS) + BulkTransmitter (HTTP)
│   │   ├── connection_manager.py # HPC/DO mode abstraction + health monitor
│   │   └── cloud_control.py     # DigitalOcean API + heartbeat (DO mode only)
│   └── ui/
│       ├── overlay.py           # Main window + Rec Panel + ghost mode + export
│       └── workers.py           # Async pipeline: capture → send → dedup → emit
│
└── server/                      # Runs on GPU machine (HPC or cloud)
    ├── main.py                  # FastAPI app — /ws/transcribe, /api/transcribe, /health
    ├── requirements.txt         # Server-specific dependencies (PyTorch, NeMo, transformers)
    ├── deploy_surrey.sh         # One-time HPC setup (conda env + model download)
    ├── surrey_job.sh            # Slurm batch script (GPU allocation + server start)
    ├── deploy.sh                # DigitalOcean setup script
    ├── tunnel.sh                # SSH tunnel helper
    └── models/
        └── transcriber.py       # TranscriptionRouter — Canary + Whisper + confidence routing
```

---

## Setup

### Prerequisites

- Python 3.10 (required for NeMo compatibility)
- Windows 10/11 (for WASAPI audio capture and ghost mode)
- NVIDIA GPU with 40GB+ VRAM for the server (A100, A6000, etc.)

### Client Setup

```bash
git clone https://github.com/<your-username>/transcription-app.git
cd transcription-app
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your server configuration
```

### Server Setup (HPC cluster)

```bash
# One-time setup — creates conda env, installs PyTorch + models
ssh <your-username>@<hpc-login-node>
bash server/deploy_surrey.sh

# Per-session — submit GPU job
sbatch server/surrey_job.sh
# Check which node it landed on:
squeue -u <your-username>
# Note the NODELIST value (e.g., gpu-node01)
```

### Running

```bash
# 1. On your laptop — create SSH tunnel to GPU node
ssh -L 8000:<gpu-node>:8000 <your-username>@<hpc-login-node>

# 2. On your laptop — start the client
cd transcription-app
venv\Scripts\activate
cd client
python main.py
```

---

## Key Engineering Decisions

**Confidence-based model routing** — Canary is faster but can produce low-quality output on noisy audio. Rather than always using the slower Whisper, we use Canary first and check its confidence score (derived from token log probabilities). Only when confidence drops below 0.7 do we invoke Whisper. This gives fast results on clean audio and robust results on noisy audio.

**Separate asyncio event loop in background thread** — PyQt6 runs its own event loop for UI updates. asyncio needs its own event loop for WebSocket I/O. They cannot share a loop. The LiveWorker creates a dedicated asyncio loop in a daemon thread, and communicates results back to the UI thread via Qt signals (thread-safe by design).

**Fire-and-forget WebSocket config** — When switching audio sources (speaker/mic), the client sends a config message but does NOT await the server's acknowledgment. This eliminates a vulnerable `recv()` await point that could trap a pending Future during shutdown, causing event loop crashes.

**Fuzzy transcript de-duplication** — Live audio chunks overlap, producing duplicate text. A two-pass system handles this: Pass 1 checks exact word-level overlap (case-insensitive, punctuation-stripped). Pass 2 uses `SequenceMatcher` with a 0.6 similarity threshold for fuzzy matching. A staleness guard (10-second threshold) prevents false matches after pauses.

**Ghost mode via Windows API** — `SetWindowDisplayAffinity` with `WDA_EXCLUDEFROMCAPTURE` tells the Windows Desktop Window Manager to skip this window's layer when compositing for screen capture. The physical monitor still shows it. This is accessed via `ctypes` since PyQt6 doesn't expose Windows-specific low-level functions.

---

## Demo

*Coming soon — screen recording of live transcription session showing dual audio capture, compact recording bar, ghost mode, and confidence-based model switching.*

---

## License

This project was built as part of a university coursework and portfolio project.
