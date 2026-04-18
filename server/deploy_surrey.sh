#!/bin/bash
set -e

# server/deploy_surrey.sh — one-time HPC environment setup
# run this ONCE on the HPC login node
# creates conda environment, installs all dependencies, downloads AI models
#
# Usage: bash deploy_surrey.sh

echo "=========================================="
echo "  HPC — ONE-TIME ENVIRONMENT SETUP"
echo "=========================================="

# ============================================
# STAGE 1: Load Cluster Modules
# ============================================
# module load = activates pre-installed software on the HPC cluster
# It modifies your PATH so the shell can find the programs
# Anaconda3 gives us conda (package manager that handles Python + CUDA together)
# CUDA gives us NVIDIA's GPU programming toolkit (needed by PyTorch)

echo ""
echo "[1/5] Loading cluster modules..."
module load Anaconda3/2024.02-1
module load CUDA/12.2.2

echo "  Anaconda3 loaded: $(conda --version)"
echo "  CUDA loaded: $(nvcc --version | grep release)"

# ============================================
# STAGE 2: Create Conda Environment
# ============================================
# conda create = makes an isolated Python environment
# -n transcribe = names it "transcribe" (you activate it later with: conda activate transcribe)
# python=3.10 = installs Python 3.10 inside this environment
# -y = auto-confirm (don't prompt yes/no)
#
# WHY Python 3.10? NeMo (Canary's framework) has best compatibility with 3.10.
# 3.11+ can cause import errors with some NeMo dependencies.

echo ""
echo "[2/5] Creating conda environment 'transcribe'..."

# Check if environment already exists (safe to re-run)
if conda env list | grep -q "transcribe"; then
    echo "  Environment 'transcribe' already exists — skipping creation"
else
    conda create -n transcribe python=3.10 -y
    echo "  Environment created"
fi

# Activate the environment
# From this point, all pip/conda installs go into ~/anaconda3/envs/transcribe/
source activate transcribe

echo "  Python location: $(which python)"
echo "  Python version: $(python --version)"

# ============================================
# STAGE 3: Install PyTorch with CUDA Support
# ============================================
# conda install pytorch = installs PyTorch (the deep learning framework both models use)
# pytorch-cuda=12.1 = tells conda to bundle CUDA 12.1 libraries INSIDE the conda env
#
# WHY 12.1 not 12.2? PyTorch's official conda builds target 12.1.
# CUDA is backward-compatible within the same major version,
# so 12.1 libraries work perfectly with the cluster's 12.2 driver.
# The driver (on the GPU node) must be >= the toolkit version (in conda). 12.2 >= 12.1 ✓
#
# -c pytorch = use PyTorch's official conda channel (repository of packages)

echo ""
echo "[3/5] Installing PyTorch with CUDA support..."
conda install pytorch torchaudio pytorch-cuda=12.1 -c pytorch -c nvidia -y

# Verify CUDA is accessible to PyTorch
python -c "import torch; print(f'  PyTorch {torch.__version__}, CUDA available: {torch.cuda.is_available()}')"

# ============================================
# STAGE 4: Install Project Dependencies
# ============================================
# These are the same packages from requirements.txt, adapted for the HPC environment
# pip install inside a conda env = packages go into the conda env, not system Python
#
# Server dependencies:
#   fastapi = the web framework that creates our API endpoints
#   uvicorn = the ASGI server that runs FastAPI (listens for HTTP/WebSocket connections)
#   python-multipart = lets FastAPI accept file uploads (needed for /api/transcribe)
#
# AI model dependencies:
#   nemo_toolkit[asr] = NVIDIA NeMo framework (loads and runs Canary model)
#   transformers = Hugging Face library (loads and runs Whisper model)
#   soundfile = reads audio bytes into numpy arrays for model input
#   librosa = audio resampling (converts sample rates to 16kHz if needed)

echo ""
echo "[4/5] Installing project dependencies..."
pip install fastapi uvicorn python-multipart
pip install nemo_toolkit[asr]
pip install transformers soundfile librosa

# ============================================
# STAGE 5: Pre-Download AI Model Weights
# ============================================
# Model weights = the trained parameters (numbers) that make the AI work
# First download caches them in ~/.cache/huggingface/ and ~/.cache/torch/NeMo/
# These files total ~7 GB
# After this, surrey_job.sh loads from cache (fast) instead of downloading (slow)
#
# WHY pre-download? Slurm jobs have time limits. If your 4-hour job spends
# 30 minutes downloading models, that's 30 minutes less for actual work.
# Also, compute nodes may have restricted internet access on some clusters.

echo ""
echo "[5/5] Pre-downloading AI model weights (this takes 10-20 minutes)..."

echo "  Downloading Whisper Large-v3..."
python -c "
from transformers import WhisperForConditionalGeneration, WhisperProcessor
WhisperProcessor.from_pretrained('openai/whisper-large-v3')
WhisperForConditionalGeneration.from_pretrained('openai/whisper-large-v3')
print('  Whisper Large-v3 cached successfully')
"

echo "  Downloading Canary 1B..."
python -c "
import nemo.collections.asr as nemo_asr
nemo_asr.models.ASRModel.from_pretrained('nvidia/canary-1b')
print('  Canary 1B cached successfully')
"

# ============================================
# DONE
# ============================================
echo ""
echo "=========================================="
echo "  SETUP COMPLETE"
echo "=========================================="
echo ""
echo "  Conda environment: transcribe"
echo "  Python: $(python --version)"
echo "  PyTorch CUDA: $(python -c 'import torch; print(torch.cuda.is_available())')"
echo "  Models cached in: ~/.cache/"
echo ""
echo "  Next step: sbatch server/surrey_job.sh"
echo "=========================================="
