# PHASE 4B REFERENCE — Connection Mode Toggle (HPC vs DigitalOcean)
# Session 9 — Step 4b Complete
# Updated: Session 9

---

## What Was Built

A server mode toggle that lets the app work with two different infrastructures without changing the data pipeline. The app reads `SERVER_MODE` from `.env` and adjusts the UI's server control button accordingly.

---

## Files Changed

| File | Change Type | What Changed |
|------|-------------|-------------|
| `.env.example` | Modified | Added `SERVER_MODE=hpc` with documentation for both modes |
| `.env` | Modified | Added `SERVER_MODE=hpc`, set `SERVER_IP=localhost` |
| `client/network/connection_manager.py` | **New file** | Mode-aware server management — decides HPC vs DO behavior |
| `client/ui/overlay.py` | Modified | Server button branches on mode; import + constructor + toggle + close updated |

## Files NOT Changed (and why)

| File | Why No Change |
|------|--------------|
| `client/network/transmitter.py` | Already connects to `localhost:8000` — works for both modes via `.env` |
| `client/audio/capture.py` | Captures audio regardless of server infrastructure |
| `client/ui/workers.py` | Bridges audio → network → UI — infrastructure-agnostic |
| `client/network/cloud_control.py` | Still exists, still works — only imported in DigitalOcean mode |
| `server/*` | Server code is the same regardless of where it runs |

---

## New File: `client/network/connection_manager.py`

### Purpose
Sits between the UI and the infrastructure. Reads `SERVER_MODE` from `.env` and provides a unified interface so `overlay.py` doesn't need to know which infrastructure is active.

### Key Design Decision: Lazy Importing
```python
if self.mode == "digitalocean":
    from network.cloud_control import CloudController
    self.cloud_controller = CloudController()
```
`CloudController` is only imported and created in DigitalOcean mode. In HPC mode, it never exists in memory. This means missing DigitalOcean API tokens don't cause errors when using HPC.

### Methods

| Method | What It Does |
|--------|-------------|
| `get_mode()` | Returns `"hpc"` or `"digitalocean"` |
| `is_hpc_mode()` | Returns `True` if HPC mode |
| `is_cloud_mode()` | Returns `True` if DigitalOcean mode |
| `is_server_available()` | Pings `/health` endpoint — works in both modes |
| `get_status_message(is_available)` | Returns mode-appropriate status text for UI |
| `get_button_label(is_available)` | Returns mode-appropriate button label |
| `start_server()` | DO mode: calls CloudController. HPC mode: returns manual instructions |
| `stop_server()` | DO mode: calls CloudController. HPC mode: returns manual instructions |

---

## Changes to `overlay.py`

### Change 1 — Import (line 31)
```python
# BEFORE:
from network.cloud_control import CloudController

# AFTER:
from network.connection_manager import ConnectionManager
```

### Change 2 — Constructor (line 204)
```python
# BEFORE:
self.cloud = CloudController()

# AFTER:
self.connection = ConnectionManager()
```

### Change 3 — Server Button Initialization
- HPC mode: button says "Check Connection", status says "Server: HPC Mode"
- DO mode: button says "Start Server", status says "Server: Offline"
- Button width increased from 120 to 140 to fit "Check Connection" text

### Change 4 — `_toggle_server()` method
Now branches on `self.connection.is_hpc_mode()`:

**HPC branch:**
1. Button changes to "Checking..."
2. Background thread pings `/health` via `connection.is_server_available()`
3. If reachable → emits "online" signal → UI shows "HPC Connected"
4. If not reachable → emits "hpc_offline" signal → UI shows "Not Reachable"
5. Button always resets to "Check Connection" (never "Start/Stop" in HPC mode)

**DigitalOcean branch:**
1. Works exactly as before
2. Now calls through `ConnectionManager` instead of `CloudController` directly
3. Heartbeat accessed via `self.connection.cloud_controller`

### Change 5 — `_on_server_status()` handler
New status values:
- `"online"` → HPC shows "HPC Connected", DO shows "Online"
- `"hpc_offline"` → new status, only in HPC mode, shows "Not Reachable"
- `"offline"` → DO only, shows "Offline"
- `"booting"` → DO only, unchanged

### Change 6 — `closeEvent()` (app shutdown)
```python
# BEFORE: Always tried to stop DigitalOcean droplet
# AFTER: Only stops droplet in DigitalOcean mode
if self.connection.is_cloud_mode() and self.connection.cloud_controller:
    # stop heartbeat + power off droplet
```
In HPC mode, closing the app just closes the app. The Slurm job keeps running until you `scancel` it.

---

## `.env` Configuration

### HPC Mode (current)
```
SERVER_MODE=hpc
SERVER_IP=localhost
SERVER_PORT=8000
```

### DigitalOcean Mode (switch back anytime)
```
SERVER_MODE=digitalocean
SERVER_IP=your_droplet_public_ip
SERVER_PORT=8000
DO_API_TOKEN=your_token
DO_DROPLET_ID=your_id
```

---

## UI Behavior Summary

| Action | HPC Mode | DigitalOcean Mode |
|--------|----------|-------------------|
| App starts | Button: "Check Connection" | Button: "Start Server" |
| Click button | Pings `/health` → shows connected/not reachable | Calls DO API → boots droplet → polls health |
| Server reachable | "HPC Connected" (green) | "Online" / "Stop Server" |
| Server not reachable | "Not Reachable — Run sbatch + tunnel.sh" | "Offline" / "Start Server" |
| Close app | Just closes (server keeps running) | Stops heartbeat + powers off droplet |

---

## Architecture Principle

**Separate the data pipeline from the infrastructure management.**

```
DATA PIPELINE (never changes):
  capture.py → workers.py → transmitter.py → localhost:8000 → server → models

INFRASTRUCTURE LAYER (swappable):
  Option A: SSH tunnel → HPC GPU node
  Option B: DigitalOcean API → cloud droplet
  Option C: (future) any server on localhost:8000
```

The `ConnectionManager` is the switch between options. The data pipeline is identical in all cases.

---

## Connection Map — Full System After Phase 4B

```
.env
├── SERVER_MODE → connection_manager.py → decides HPC or DO behavior
├── SERVER_IP → transmitter.py → where to send audio
└── SERVER_PORT → transmitter.py → which port

client/
├── main.py → launches UI
├── audio/
│   ├── capture.py → WASAPI loopback audio capture + VAD
│   └── youtube.py → yt-dlp audio download
├── network/
│   ├── connection_manager.py → NEW: mode-aware server management
│   ├── cloud_control.py → DigitalOcean API (only used in DO mode)
│   └── transmitter.py → LiveTransmitter (WS) + BulkTransmitter (HTTP)
└── ui/
    ├── overlay.py → MODIFIED: server button branches on mode
    └── workers.py → async bridges (unchanged)

server/
├── main.py → FastAPI (unchanged)
├── deploy.sh → OLD DigitalOcean setup
├── deploy_surrey.sh → NEW (Phase 4A): HPC one-time setup
├── surrey_job.sh → NEW (Phase 4A): Slurm job script
├── tunnel.sh → NEW (Phase 4A): SSH tunnel helper
└── models/
    └── transcriber.py → Canary + Whisper router (unchanged)
```

---

## Concepts Learned — Phase 4B

| Concept | Definition |
|---------|-----------|
| Lazy importing | Only importing a module when it's actually needed, inside a conditional block. Avoids loading unnecessary code and prevents errors from missing dependencies. |
| Mode toggle via environment variable | Using `.env` to switch app behavior without changing code. One variable controls which infrastructure path the app uses. |
| Unified interface | A single class (`ConnectionManager`) that provides the same methods regardless of which backend is active. The UI calls the same functions — the class decides what to do internally. |
| Separation of concerns | Data pipeline (audio → server → text) stays independent from infrastructure management (how the server is started/stopped). Either can change without affecting the other. |

---

## Interview Prep — Phase 4B

**Q: How did you design the system to support multiple deployment targets without rewriting the client?**

A: I created a `ConnectionManager` class that reads a `SERVER_MODE` environment variable. This class provides a unified interface — the UI calls the same methods regardless of which infrastructure is active. In HPC mode, the server button only checks health. In DigitalOcean mode, it calls the cloud provider's API. The data pipeline never changes because the transmitters connect to `localhost:8000` in both modes — the SSH tunnel makes the HPC server appear local.

**Q: What's the advantage of lazy importing `CloudController`?**

A: Two benefits. First, if the user is in HPC mode and doesn't have DigitalOcean API tokens set in `.env`, the app doesn't crash on startup — `CloudController` is never imported, so its missing credentials are never checked. Second, it saves memory — why load DigitalOcean-specific code if you're never going to use it?

---

*Phase 4B complete. Connection mode toggle built. Data pipeline unchanged. HPC and DigitalOcean modes both supported via single .env variable. Ready for Step 4c (wire end-to-end bulk pipeline) or Step 4d (full end-to-end testing) after GitHub push.*
