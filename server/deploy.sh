#!/bin/bash
set -e  
# server/deploy.sh — Run this on the GPU droplet after SSH-ing in
# Sets up everything needed to run the transcription server

echo "=========================================="
echo "  TRANSCRIPTION SERVER — DEPLOYMENT SETUP"
echo "=========================================="

# ------------------------------------------
# STAGE 1: System Update
# ------------------------------------------
# WHY: Fresh droplets may have outdated packages.
# Security patches + latest tools.
echo ""
echo "[1/4] Updating system packages..."
apt update && apt upgrade -y

# ------------------------------------------
# STAGE 2: Python Environment
# ------------------------------------------
# WHY: Isolate our packages from system Python
# so we don't break GPU drivers.
echo ""
echo "[2/4] Setting up Python environment..."

cd /root/transcription-server
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# ------------------------------------------
# STAGE 3: Download AI Models
# ------------------------------------------
# WHY: Models are multi-GB files stored on HuggingFace/NVIDIA servers.
# Download once to disk, then load into GPU VRAM at server startup.
# This step takes 10-20 minutes depending on network speed.
echo ""
echo "[3/4] Downloading AI models (this takes a while)..."

# Download Whisper Large-v3 from HuggingFace
python3 -c "
from transformers import WhisperForConditionalGeneration, WhisperProcessor
print('[MODEL] Downloading Whisper Large-v3...')
WhisperProcessor.from_pretrained('openai/whisper-large-v3')
WhisperForConditionalGeneration.from_pretrained('openai/whisper-large-v3')
print('[MODEL] Whisper Large-v3 downloaded')
"

# Download Canary via NeMo
python3 -c "
import nemo.collections.asr as nemo_asr
print('[MODEL] Downloading Canary Qwen 2.5B...')
nemo_asr.models.ASRModel.from_pretrained('nvidia/canary-1b')
print('[MODEL] Canary downloaded')
"
# ------------------------------------------
# STAGE 4: Start the Server
# ------------------------------------------
echo ""
echo "[4/4] Starting transcription server..."
echo "Server will be available on port 8000"

cd /root/transcription-server/server
python3 main.py