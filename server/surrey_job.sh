#!/bin/bash

# server/surrey_job.sh — Slurm Job Script for HPC cluster
# Submit this to start a transcription server session on a GPU node
#
# Usage: sbatch surrey_job.sh
# Check status: squeue -u REDACTED_USER
# Cancel job: scancel <job_id>

# ============================================
# SBATCH DIRECTIVES — Instructions to Slurm
# ============================================
# These lines look like comments but they are NOT.
# Slurm reads every line starting with #SBATCH as a configuration instruction.
# They tell the job scheduler exactly what resources your job needs.

#SBATCH --job-name=transcribe-server
# Names your job in the queue. When you run squeue, you'll see this name
# instead of a generic ID. Makes it easy to identify your job.

#SBATCH --partition=gpu
# Which group of machines to run on. "gpu" = the partition that has
# NVIDIA A100 GPUs. The cluster also has CPU-only partitions — we skip those.

#SBATCH --gres=gpu:1
# gres = Generic RESource. gpu:1 = "I need 1 GPU."
# The cluster has 6x A100 GPUs across 2 nodes. This requests one of them.
# You could request gpu:2 for two GPUs, but our models only need one.

#SBATCH --mem=32G
# Request 32 GB of system RAM (not GPU VRAM — that's separate).
# Our models need ~16 GB VRAM on the GPU. System RAM is used for:
# - Loading model files from disk before they transfer to GPU
# - FastAPI server overhead
# - Audio data buffering
# 32 GB gives comfortable headroom.

#SBATCH --time=04:00:00
# Maximum wall time: 4 hours. Format is HH:MM:SS.
# If your job is still running after 4 hours, Slurm kills it automatically.
# Cluster maximum is 7 days. We chose 4 hours because:
# - Long enough for development and testing sessions
# - Short enough that you don't waste queue time if you forget to cancel
# - You can always submit a new job when this one expires

#SBATCH --output=transcribe_%j.log
# Where to save everything the server prints to the terminal.
# %j = Slurm job ID (a unique number). So the file becomes something like:
# transcribe_12345.log
# This captures all your FastAPI server output, model loading messages,
# and any errors — essential for debugging when you can't see the terminal.

#SBATCH --error=transcribe_%j.err
# Same as --output but specifically for error messages (stderr).
# Separating stdout and stderr makes it easier to find problems.

# ============================================
# ENVIRONMENT SETUP — Runs on the GPU node
# ============================================
# Everything below this point executes on whichever GPU node Slurm assigns.
# The GPU node starts with a clean shell — no modules loaded, no conda active.
# We must set up the environment from scratch every time.

echo "=========================================="
echo "  TRANSCRIPTION SERVER — STARTING"
echo "  Job ID: $SLURM_JOB_ID"
echo "  Node: $HOSTNAME"
echo "  GPU: $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null || echo 'checking...')"
echo "  Time: $(date)"
echo "=========================================="

# Load the same modules we used during setup
# module load = makes software available in this shell session
# Must match what deploy_surrey.sh used, or packages won't be found
module load Anaconda3/2024.02-1
module load CUDA/12.2.2

# Activate the conda environment we created with deploy_surrey.sh
# This points Python to our installed packages (PyTorch, FastAPI, NeMo, etc.)
source activate transcribe

echo ""
echo "[INFO] Python: $(which python) — $(python --version)"
echo "[INFO] PyTorch CUDA: $(python -c 'import torch; print(torch.cuda.is_available())')"
echo "[INFO] Working directory: $(pwd)"

# ============================================
# PRINT CONNECTION INFO
# ============================================
# This is critical — it tells you which node to tunnel to.
# After submitting the job, check the .log file for this output.
# You need the hostname to set up your SSH tunnel.

echo ""
echo "=========================================="
echo "  CONNECTION INFO"
echo "=========================================="
echo "  Server will start on: $HOSTNAME:8000"
echo ""
echo "  To connect from your laptop, open a NEW terminal and run:"
echo "  ssh -L 8000:$HOSTNAME:8000 REDACTED_USER@REDACTED_HOST"
echo ""
echo "  Then point your client to: localhost:8000"
echo "=========================================="
echo ""

# ============================================
# START THE SERVER
# ============================================
# cd to the server directory where main.py lives
# python main.py starts the FastAPI server on 0.0.0.0:8000
#
# 0.0.0.0 means "listen on all network interfaces" — this is necessary
# because the SSH tunnel connects through the cluster's internal network,
# not localhost. If we bound to 127.0.0.1, the tunnel couldn't reach it.
#
# The server runs in the foreground (not background). When Slurm's time
# limit hits or you run scancel, the process terminates and the job ends.

cd "$HOME/transcription-app/server"
echo "[SERVER] Starting FastAPI on 0.0.0.0:8000..."
python main.py
