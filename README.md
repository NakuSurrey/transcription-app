# Real-Time Audio Transcription System

![Python](https://img.shields.io/badge/python-3.10-blue.svg)
![PyQt6](https://img.shields.io/badge/UI-PyQt6-green.svg)
![FastAPI](https://img.shields.io/badge/backend-FastAPI-teal.svg)
![NVIDIA NeMo](https://img.shields.io/badge/ASR-Canary%201B-76B900.svg)
![Whisper](https://img.shields.io/badge/fallback-Whisper--Large--v3-orange.svg)
![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-lightgrey.svg)
![Tests](https://img.shields.io/badge/tests-50%20passing-brightgreen.svg)
![License](https://img.shields.io/badge/license-MIT-yellow.svg)

Real-time desktop overlay that transcribes live audio (per-app or system-wide) with a confidence-based dual AI model router, streams over WebSocket, and captures a visual frame feed for a future vision pipeline.

---

## Live Demo

Deployment for this project is a Windows desktop client plus a GPU inference server — there is no single web URL to open. A screen recording of a live session (window picker, dual audio capture, ghost mode toggle, confidence-based model switching) is being produced — link will go here.

---

## What It Does

- Captures audio from **one specific app window** (per-app capture) OR all system audio, user's choice via a window picker dropdown
- Toggles microphone on or off per session — mic on for meetings, mic off for solo lecture playback
- Streams audio chunks to a GPU server over WebSocket and returns live transcript text
- Runs a **two-model confidence router** server-side — Canary 1B first, Whisper Large-v3 takes over when Canary's confidence drops below 0.7
- De-duplicates overlapping transcripts from the sliding window using exact + fuzzy matching
- Takes one screenshot per second of the target window and stores them as `(timestamp, PIL.Image)` tuples — ready input for a future vision pipeline (OCR, math recognition, screen understanding)
- **Ghost mode** — `SetWindowDisplayAffinity` flag hides the overlay from screen capture software (Zoom, Teams, OBS) while still showing it on the physical monitor
- Bulk transcription of YouTube URLs via yt-dlp
- Export transcript as clipboard / .txt / .srt subtitles

---

## Why I Built It

Needed a tool that could sit on top of any app and transcribe what it was saying without me routing audio through virtual cables or installing heavyweight third-party software. Off-the-shelf options captured only system audio, only mic, or required admin-level setup. Built this from scratch using Windows Process Loopback + PrintWindow APIs so it works on any Windows 10+ machine with zero config.

Chose a dual-model router instead of one big model because real audio is a mix of clean and noisy segments — one model is fast on clean audio, the other survives noise. Routing by confidence gives both.

Each build phase adds one working layer — audio capture first, then network transport, then UI, then per-app isolation, then visual frames. Every layer has tests that run on their own without starting the server.

---

## Tech Stack

| Layer             | Technology                         | Why this choice                                                                 |
|-------------------|------------------------------------|---------------------------------------------------------------------------------|
| UI Framework      | PyQt6                              | Translucent borderless overlay + signals/slots for thread-safe UI updates       |
| Audio Capture     | PyAudioWPatch + WASAPI + Process Loopback API | Native Windows audio, zero-install per-app filtering via COM                 |
| Window Enum       | ctypes + user32.dll                | Direct Win32 EnumWindows — no extra dependency for window listing               |
| Frame Capture     | ctypes + PrintWindow API           | Captures target window content even when covered — unlike mss / PIL.ImageGrab   |
| Image Processing  | Pillow (PIL)                       | BGRA → RGB conversion, 1920px resize, PNG save                                  |
| Primary ASR       | NVIDIA Canary 1B (NeMo)            | Fast multilingual transcription with per-token confidence scoring               |
| Fallback ASR      | OpenAI Whisper Large-v3 (HF)       | Robust on noise, 90+ language auto-detect                                       |
| Server Framework  | FastAPI + Uvicorn                  | Async WebSocket + HTTP endpoints in one app, clean ASGI lifecycle               |
| Live Transport    | websockets                         | Persistent bidirectional connection with built-in ping/pong keepalive           |
| Network           | SSH tunnel                         | No inbound ports opened on the HPC cluster — safe multi-user environment        |
| GPU Compute       | NVIDIA A100 (MIG 3g.40gb)          | Large enough for Whisper Large-v3 at fp16, scheduled via HPC cluster Slurm       |
| Tests             | unittest (stdlib)                  | Zero install, cross-platform where possible, runs with `python -m unittest`     |

---

## Architecture

```
┌──────────────────────────── CLIENT (Windows) ────────────────────────────┐
│                                                                           │
│  User picks window + mic toggle in overlay                                │
│                           │                                                │
│                           ▼                                                │
│   ┌──────────────────────────────────────────┐                             │
│   │ DualCapturer (mode switch)               │                             │
│   │   target_pid=None  → AudioCapturer       │                             │
│   │   target_pid=<id>  → ProcessAudioCapturer│                             │
│   │   enable_mic=True  → MicCapturer         │                             │
│   │   enable_mic=False → None                │                             │
│   └───────────────────┬──────────────────────┘                             │
│                       │ WAV chunks (tagged speaker/mic)                    │
│                       ▼                                                    │
│   ┌──────────────────────────────────────────┐                             │
│   │ FrameGrabber (Phase 7C)                   │                             │
│   │   PrintWindow + PW_RENDERFULLCONTENT      │                             │
│   │   1 frame / second → (timestamp, Image)   │                             │
│   └───────────────────┬──────────────────────┘                             │
│                       │                                                    │
│                       ▼                                                    │
│   ┌──────────────────────────────────────────┐                             │
│   │ LiveWorker (asyncio in background thread)│                             │
│   │   send audio → recv transcript → dedup   │                             │
│   │   emit Qt signals → Overlay UI updates   │                             │
│   └───────────────────┬──────────────────────┘                             │
│                       │ WebSocket                                          │
└───────────────────────┼────────────────────────────────────────────────────┘
                        │ SSH tunnel (port 8000)
┌───────────────────────┼──────────── SERVER (GPU) ──────────────────────────┐
│                       ▼                                                    │
│   ┌──────────────────────────────────────────┐                             │
│   │ FastAPI                                   │                             │
│   │   /ws/transcribe  (live)                  │                             │
│   │   /api/transcribe (bulk)                  │                             │
│   │   /health                                 │                             │
│   └───────────────────┬──────────────────────┘                             │
│                       │                                                    │
│                       ▼                                                    │
│   ┌──────────────────────────────────────────┐                             │
│   │ Transcription Router                      │                             │
│   │   try Canary 1B                           │                             │
│   │     confidence >= 0.7? → return Canary    │                             │
│   │     else                → run Whisper v3  │                             │
│   └──────────────────────────────────────────┘                             │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## How To Run Locally

### Prerequisites

- Windows 10 or 11 (WASAPI + COM audio + PrintWindow are Windows-only)
- Python 3.10 (required for NeMo compatibility)
- An NVIDIA GPU with 40GB+ VRAM for the server (A100, A6000, or similar)
- SSH access to a GPU server or HPC cluster

### 1. Clone the repo

```
git clone https://github.com/<your-username>/transcription-app.git
cd transcription-app
```

### 2. Set up environment variables

Copy the template and fill in your values:

```
copy .env.example .env
```

Edit `.env` in any text editor. Every variable is listed in `.env.example` with a placeholder. At minimum you need:

- `SERVER_HOST` — the tunnel endpoint (usually `localhost` with SSH tunnel)
- `SERVER_PORT` — default `8000`
- `DEPLOYMENT_MODE` — `hpc` or `digitalocean`

### 3. Install client dependencies

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Set up the server

On the HPC login node:

```
ssh <your-username>@<hpc-login-node>
bash server/deploy_surrey.sh     # one-time setup
sbatch server/surrey_job.sh      # per session — submits GPU job
squeue -u <your-username>        # check which node it landed on
```

### 5. Open the SSH tunnel

On your laptop:

```
ssh -L 8000:<gpu-node>:8000 <your-username>@<hpc-login-node>
```

### 6. Run the client

```
cd client
python main.py
```

The overlay appears. Pick a window from the dropdown (or leave "All System Audio"), toggle the mic, click Start Listening.

### Run the tests

From the project root:

```
python -m unittest discover tests -v
```

- `test_dedup.py` runs on any OS (15 tests, ~35ms)
- `test_window_selector.py`, `test_frame_grabber.py`, `test_capture_modes.py` — Windows only (35 tests, skip cleanly on other platforms)

Total: 50 tests.

---

## Key Decisions

- **Windows Process Loopback over Virtual Audio Cables** — Loopback is built into Windows 10+ and needs zero user setup. Virtual cables require a third-party driver and manual audio routing. Chose zero-install over more flexible routing.

- **PrintWindow API over screen capture libraries** — Screen capture libs (mss, PIL.ImageGrab) grab whatever pixels are visible at given coordinates. If another window covers the target, you get the wrong pixels. PrintWindow tells the window to render itself into a bitmap — works even when partially covered.

- **Polymorphism for speaker capturer** — `ProcessAudioCapturer` and `AudioCapturer` share the same public interface. `DualCapturer` picks one based on `target_pid` in a single line of its constructor. No if/else branches anywhere else.

- **`mic_capturer = None` instead of a muted MicCapturer** — A muted MicCapturer still opens the mic device and runs a capture thread. Setting to None kills all that overhead when the user just wants to listen to a lecture.

- **Fresh LiveWorker per recording session** — Reusing a worker means leftover dedup history and stale audio buffers. Fresh instance per session guarantees clean state. Tradeoff: slightly more allocation per Start/Stop cycle — negligible.

- **Confidence-based dual model router** — Canary is fast but produces low-quality output on noisy audio. Whisper is robust but slower. Using Canary first and routing to Whisper only when confidence < 0.7 gives the best of both without doubling every inference cost.

- **Separate asyncio event loop in a background thread** — PyQt6 and asyncio each want their own event loop. Running asyncio in a daemon thread and using Qt signals for cross-thread communication keeps both happy without monkey-patching either.

- **Fire-and-forget WebSocket config messages** — Early version awaited server acknowledgment on mode-switch. That `recv()` point trapped Futures during shutdown and crashed the event loop. Switching to fire-and-forget removed the crash surface.

- **try/finally for GDI cleanup in FrameGrabber** — GDI objects are system-wide limited (10,000 per process default). Leaking one per frame at 1fps crashes Windows in ~3 hours. try/finally guarantees cleanup even if PrintWindow throws.

- **unittest over pytest for the test suite** — unittest is stdlib. No `pip install` required before running any test. Tests are still pytest-compatible via auto-discovery.

---

## What I Learned

- How Windows COM activation works end-to-end — building `AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS` in ctypes, wrapping in `PROPVARIANT`, passing to `ActivateAudioInterfaceAsync`, reading back a filtered `IAudioClient`.

- Why DWM-composited apps render blank with default PrintWindow and how `PW_RENDERFULLCONTENT` (0x2) fixes it.

- How to share a pure-Python function across a module full of platform-specific imports without forcing cross-platform compatibility on the whole module — `sys.modules` stub shim in the test file.

- How sliding-window audio capture creates duplicate text in transcripts and how to strip it with a two-pass (exact then fuzzy) dedup using `difflib.SequenceMatcher`.

- Why GDI object leaks are the silent killer of Windows apps that do screen capture and why try/finally beats a context manager in that specific case — explicit, no abstraction.

- The difference between what a dropdown shows (title + PID) and what the backend actually uses (hwnd for frame capture, pid for audio filter). Both must come from the same `WindowInfo` to stay consistent.

---

## Project Structure

```
transcription-app/
├── .env.example                 — configuration template (copy to .env)
├── .gitignore                   — blocks docs/, .env, venv, node_modules, junk
├── requirements.txt             — client dependencies (Python 3.10)
├── setup.py                     — initial project setup script
├── README.md                    — this file
│
├── client/                      — runs on Windows machine
│   ├── main.py                  — entry point, launches the overlay
│   ├── audio/
│   │   ├── capture.py           — AudioCapturer, MicCapturer, ProcessAudioCapturer, DualCapturer
│   │   ├── window_selector.py   — list_windows(), WindowInfo — per-app dropdown data
│   │   └── youtube.py           — yt-dlp wrapper for bulk transcription
│   ├── network/
│   │   ├── transmitter.py       — LiveTransmitter (WSS) + BulkTransmitter (HTTP)
│   │   ├── connection_manager.py — HPC/DO mode abstraction + health monitor
│   │   └── cloud_control.py     — DigitalOcean API + heartbeat (DO mode only)
│   ├── ui/
│   │   ├── overlay.py           — main window, live panel, bulk panel, ghost mode, export
│   │   └── workers.py           — LiveWorker + BulkWorker + deduplicate_transcript
│   └── video/
│       └── frame_grabber.py     — FrameGrabber (PrintWindow-based screenshot capturer)
│
├── server/                      — runs on GPU machine
│   ├── main.py                  — FastAPI app, /ws/transcribe, /api/transcribe, /health
│   ├── requirements.txt         — server-side deps (PyTorch, NeMo, transformers)
│   ├── deploy_surrey.sh         — one-time HPC setup
│   ├── surrey_job.sh            — Slurm GPU job script
│   └── models/
│       └── transcriber.py       — TranscriptionRouter (Canary + Whisper)
│
└── tests/                       — standalone test suite (50 tests)
    ├── __init__.py
    ├── test_dedup.py            — 15 tests, cross-platform
    ├── test_window_selector.py  — 15 tests, Windows only
    ├── test_frame_grabber.py    — 11 tests, Windows only
    └── test_capture_modes.py    — 9 tests, Windows only
```

---

## License

MIT.
