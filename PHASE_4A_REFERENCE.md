# PHASE 4A REFERENCE — HPC cluster Infrastructure Scripts
# Session 9 — Step 4a Phase A Complete
# Updated: Session 9

---

## What Was Built

Three shell scripts that replace the DigitalOcean deployment workflow with a HPC cluster workflow. No existing code was modified — all client and server Python files remain unchanged.

---

## Files Created

| File | Purpose | Where It Runs | When |
|------|---------|---------------|------|
| `server/deploy_surrey.sh` | One-time environment setup: conda env + dependencies + model downloads | Login node (REDACTED_HOST) | Once |
| `server/surrey_job.sh` | Slurm job script: requests A100 GPU, starts FastAPI server | GPU node (via Slurm) | Every session |
| `server/tunnel.sh` | SSH tunnel: forwards laptop's localhost:8000 to GPU node | Your Windows laptop | Every session |

---

## File 1: `server/deploy_surrey.sh` — One-Time Setup

### What It Does
1. Loads Anaconda3/2024.02-1 and CUDA/12.2.2 from the cluster's module system
2. Creates a conda environment named `transcribe` with Python 3.10
3. Installs PyTorch with CUDA 12.1 support (conda bundles matching CUDA libraries automatically)
4. Installs server dependencies: FastAPI, uvicorn, python-multipart
5. Installs AI model dependencies: nemo_toolkit[asr], transformers, soundfile, librosa
6. Pre-downloads Canary 1B and Whisper Large-v3 model weights to `~/.cache/`

### Key Decisions
- **Python 3.10** chosen because NeMo has best compatibility with 3.10. Versions 3.11+ can cause import errors with NeMo's C extensions.
- **pytorch-cuda=12.1** chosen because PyTorch's official conda builds target 12.1. The cluster's 12.2 driver is backward-compatible (driver >= toolkit within same major version).
- **Pre-downloading models** on the login node because: (a) Slurm jobs have time limits — don't waste GPU time downloading, (b) some compute nodes have restricted internet access. Models are cached in `~/.cache/` which is NFS-shared across all nodes.
- **Re-run safe**: checks if conda env already exists before creating.

### How It Connects
- Creates the environment that `surrey_job.sh` activates.
- Downloads models that `server/models/transcriber.py` loads via `from_pretrained()`.
- Both scripts use the same `module load` commands to ensure consistency.

---

## File 2: `server/surrey_job.sh` — Slurm Job Script

### What It Does
1. Tells Slurm what resources are needed via `#SBATCH` directives
2. Loads Anaconda3 and CUDA modules (same as deploy_surrey.sh)
3. Activates the `transcribe` conda environment
4. Prints the assigned GPU node hostname (needed for tunnel setup)
5. Starts `server/main.py` in the foreground

### SBATCH Directives Explained
| Directive | Value | Meaning |
|-----------|-------|---------|
| `--job-name` | transcribe-server | Human-readable name in queue |
| `--partition` | gpu | Use the GPU partition (has A100s) |
| `--gres` | gpu:1 | Request 1 GPU |
| `--mem` | 32G | 32 GB system RAM (separate from GPU VRAM) |
| `--time` | 04:00:00 | 4-hour maximum (Slurm kills job after this) |
| `--output` | transcribe_%j.log | Stdout log (%j = job ID) |
| `--error` | transcribe_%j.err | Stderr log (%j = job ID) |

### Key Decisions
- **Foreground execution**: `python main.py` runs without `&`. Slurm keeps a job alive as long as the script's main process is running. If we used background (`&`), the script would end immediately and Slurm would kill the job.
- **0.0.0.0 binding**: `main.py` binds to `0.0.0.0:8000` (already configured). This accepts connections from any network interface, which is required because SSH tunnel traffic arrives through the cluster's internal network, not localhost.
- **4-hour time limit**: Long enough for development/testing, short enough to not waste queue time if forgotten. Can always resubmit.

### How It Connects
- Activates the conda env created by `deploy_surrey.sh`.
- Runs `server/main.py` which loads models from cache (placed by `deploy_surrey.sh`).
- Prints `$HOSTNAME` to the log file — you read this to know which node to tunnel to.
- The running server is what `tunnel.sh` connects to.

---

## File 3: `server/tunnel.sh` — SSH Tunnel Helper

### What It Does
1. Takes a GPU node name as a command-line argument (e.g., `gpu-node02`)
2. Validates the argument (exits with error if missing)
3. Opens an SSH tunnel: `ssh -N -L 8000:<node>:8000 REDACTED_USER@REDACTED_HOST`

### SSH Flags Explained
| Flag | Meaning |
|------|---------|
| `-N` | Don't open a remote shell — only maintain the tunnel connection |
| `-L 8000:gpu-node02:8000` | Forward laptop's port 8000 → through login node → to gpu-node02's port 8000 |

### Data Flow Through the Tunnel
```
client/transmitter.py → sends audio to localhost:8000
    │
    ▼
laptop port 8000 → [SSH encrypted connection] → REDACTED_HOST login node
    │
    ▼
gpu-node02:8000 → server/main.py → Canary/Whisper → JSON response
    │
    ▼
Response travels back the same path in reverse
```

### Key Decisions
- **`-N` flag**: We only need port forwarding, not a shell session. Keeps the process clean.
- **Both ports are 8000**: Matches `server/main.py` (listens on 8000) and `client/transmitter.py` (connects to localhost:8000). Zero code changes needed.
- **Input validation**: Script exits with usage instructions if no node name provided. Prevents broken SSH commands.

### How It Connects
- Invisible to the application — `transmitter.py` already targets `localhost:8000`.
- Bridges the gap between your laptop (public internet) and the GPU node (private HPC network).
- Requires `surrey_job.sh` to be running first (the server must exist to tunnel to).

---

## Per-Session Workflow

```
ONE TIME (already done after deploy_surrey.sh):
  Conda env "transcribe" exists with all packages
  Model weights cached in ~/.cache/

EVERY SESSION:
  1. SSH into REDACTED_HOST login node
  2. sbatch server/surrey_job.sh         → submits job to Slurm queue
  3. squeue -u REDACTED_USER                   → check job status, find assigned node
  4. cat transcribe_<job_id>.log         → confirm server started, read node name
  5. On laptop: bash tunnel.sh gpu-node02  → opens SSH tunnel
  6. Run client app → connects to localhost:8000 → tunnel forwards to GPU
  7. When done: scancel <job_id>           → stops server, frees GPU
     Ctrl+C on tunnel.sh                   → closes tunnel
```

---

## Connection Map — How Everything Fits Together

```
EXISTING (unchanged):
  client/main.py → launches UI
  client/audio/capture.py → captures system audio via WASAPI
  client/audio/youtube.py → downloads YouTube audio via yt-dlp
  client/network/transmitter.py → sends audio to localhost:8000
  client/ui/overlay.py → PyQt6 overlay displaying transcripts
  client/ui/workers.py → async bridges between audio, network, UI
  server/main.py → FastAPI with /health, /ws/transcribe, /api/transcribe
  server/models/transcriber.py → Canary primary + Whisper fallback + confidence router
  server/deploy.sh → OLD DigitalOcean setup (kept for reference)

NEW (Phase 4A):
  server/deploy_surrey.sh → one-time: conda env + deps + model cache
  server/surrey_job.sh → per-session: Slurm GPU request + server start
  server/tunnel.sh → per-session: SSH tunnel from laptop to GPU node
```

---

## Updated Project Structure

```
transcription-app/
├── .env
├── .env.example
├── .gitignore
├── requirements.txt
├── gpu_sniper.py
├── PHASE_4A_REFERENCE.md          ← NEW
├── client/
│   ├── main.py
│   ├── audio/
│   │   ├── __init__.py
│   │   ├── capture.py
│   │   └── youtube.py
│   ├── network/
│   │   ├── __init__.py
│   │   ├── cloud_control.py
│   │   └── transmitter.py
│   └── ui/
│       ├── __init__.py
│       ├── overlay.py
│       └── workers.py
└── server/
    ├── main.py
    ├── deploy.sh                  (old DigitalOcean — kept for reference)
    ├── deploy_surrey.sh           ← NEW
    ├── surrey_job.sh              ← NEW
    ├── tunnel.sh                  ← NEW
    ├── requirements.txt
    ├── endpoints/
    │   └── __init__.py
    └── models/
        ├── __init__.py
        └── transcriber.py
```

---

## Concepts Learned — Phase 4A

| Concept | Definition |
|---------|-----------|
| `#SBATCH` directives | Lines in a Slurm script that look like comments but are instructions to the job scheduler |
| `--gres=gpu:1` | Slurm directive requesting 1 GPU (Generic RESource) |
| `--partition=gpu` | Slurm directive specifying which hardware pool to use |
| Foreground vs background execution | Foreground (`python main.py`) blocks the script and keeps Slurm job alive; background (`&`) lets the script exit and Slurm kills the job |
| `0.0.0.0` binding | Server accepts connections from all network interfaces (required for SSH tunnel traffic from internal network) |
| `127.0.0.1` binding | Server only accepts connections from the same machine (would reject tunnel traffic) |
| `ssh -N` | Opens SSH connection for port forwarding only, no remote shell |
| `ssh -L` | Local port forwarding — makes a remote service appear as localhost |
| `$HOSTNAME` | Slurm environment variable containing the assigned compute node's name |
| `$1` in bash | The first command-line argument passed to a script |
| `set -e` | Bash directive: stop the script immediately if any command fails |
| `exit 1` | Terminate script with error code 1 (non-zero = failure) |
| NFS-shared home directory | Same home folder visible from all cluster nodes — install once, access everywhere |
| CUDA backward compatibility | A newer CUDA driver (12.2) can run code compiled for an older toolkit (12.1) within the same major version |

---

## Interview Prep — Phase 4A

**Q: Why use an SSH tunnel instead of exposing the server directly?**
A: HPC GPU compute nodes sit on a private internal network with no public IP addresses. You can't reach them from outside the cluster. SSH tunnels use the login node (which has a public IP) as a middleman to forward traffic. This requires zero admin privileges on the shared cluster — SSH is already your only entry point.

**Q: Why does the Slurm job script run the server in the foreground?**
A: Slurm keeps a job alive as long as the script's main process is running. If you run the server in the background with `&`, bash reaches the end of the script immediately, Slurm sees the script finished, and kills the job — including the backgrounded server. Foreground execution keeps the script blocked on the server process, so Slurm keeps the job alive for the full time allocation.

**Q: What happens if deploy_surrey.sh fails halfway through?**
A: The `set -e` flag causes the script to stop at the point of failure. When you re-run it, the conda env check (`if conda env list | grep -q "transcribe"`) skips creation if the env already exists. Pip and conda installs are also idempotent — they skip packages already installed. So re-running is safe and picks up where it left off.

---

*Phase 4A complete. Three infrastructure scripts built. No existing code modified. Ready for Step 4b (wire end-to-end live pipeline) after GitHub push.*
