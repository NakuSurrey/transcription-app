#!/bin/bash

# server/tunnel.sh — SSH tunnel from laptop to HPC GPU node
# running this on the laptop (Windows Git Bash / WSL / Mac terminal)
# opens a tunnel so localhost:8000 forwards to the GPU node
#
# reads login details from env vars — never hardcoded here:
#   HPC_USER         — your HPC account username
#   HPC_LOGIN_NODE   — the SSH login node hostname
#
# set these in your shell before running, or export from .env
#
# Usage: bash tunnel.sh <gpu-node-name>
#
# How to find the node name:
#   1. After running: sbatch surrey_job.sh
#   2. Check the log: cat transcribe_<job_id>.log
#   3. Look for "Server will start on: gpu-nodeXX:8000"
#   4. Use that node name as the argument to this script

# ============================================
# INPUT VALIDATION
# ============================================
# $1 = the first argument you pass after the script name
# Example: bash tunnel.sh gpu-node02  →  $1 = "gpu-node02"
# -z tests if the string is empty (zero length)
# If no argument provided, print usage instructions and exit

if [ -z "$1" ]; then
    echo "=========================================="
    echo "  ERROR: No GPU node specified"
    echo "=========================================="
    echo ""
    echo "  Usage: bash tunnel.sh <gpu-node-name>"
    echo ""
    echo "  Example: bash tunnel.sh gpu-node02"
    echo ""
    echo "  How to find the node name:"
    echo "    1. SSH into the HPC login node"
    echo "    2. Run: squeue -u \$HPC_USER"
    echo "    3. Look at the NODELIST column"
    echo "    4. Use that name here"
    echo ""
    exit 1
fi

# Store the argument in a readable variable name
GPU_NODE="$1"

# ============================================
# CONFIGURATION
# ============================================
# login details come from the shell environment — never hardcoded
# export them once (or source from .env) before running this script:
#   export HPC_USER=your_hpc_username
#   export HPC_LOGIN_NODE=your_hpc_login_hostname

if [ -z "$HPC_USER" ] || [ -z "$HPC_LOGIN_NODE" ]; then
    echo "=========================================="
    echo "  ERROR: HPC credentials not set"
    echo "=========================================="
    echo ""
    echo "  Export these env vars before running:"
    echo "    export HPC_USER=your_hpc_username"
    echo "    export HPC_LOGIN_NODE=your_hpc_login_hostname"
    echo ""
    echo "  Or source them from your .env file"
    echo ""
    exit 1
fi

LOCAL_PORT=8000
REMOTE_PORT=8000

# ============================================
# OPEN THE TUNNEL
# ============================================
# ssh -N -L <local_port>:<destination>:<remote_port> <user>@<middleman>
#
# Breaking down each flag:
#
# -N = "Do not execute any remote command."
#      Normally when you ssh into a server, it opens a shell prompt.
#      We don't want a shell — we only want the tunnel. -N says:
#      "Just keep the connection open for port forwarding, nothing else."
#
# -L = "Local port forwarding." This is the tunnel itself.
#      Format: local_port:destination_host:destination_port
#
#      local_port (8000) = the port on YOUR laptop
#      destination_host (gpu-node02) = where traffic should end up
#      destination_port (8000) = the port on the destination (FastAPI server)
#
# The middleman (${HPC_USER}@${HPC_LOGIN_NODE}) = the login node
# the laptop can reach the login node (it has a public IP)
# the login node can reach the GPU node (internal network)
# the laptop CANNOT reach the GPU node directly (no public IP)
# the tunnel chains these two connections together
#
# Data flow:
#   localhost:8000 → [encrypted SSH] → login node → gpu-nodeXX:8000
#   Response travels back the same path in reverse

echo "=========================================="
echo "  SSH TUNNEL — CONNECTING"
echo "=========================================="
echo ""
echo "  Local:   localhost:${LOCAL_PORT}"
echo "  Through: ${HPC_USER}@${HPC_LOGIN_NODE}"
echo "  To:      ${GPU_NODE}:${REMOTE_PORT}"
echo ""
echo "  Your client app connects to: localhost:${LOCAL_PORT}"
echo "  Traffic forwards to: ${GPU_NODE}:${REMOTE_PORT}"
echo ""
echo "  Press Ctrl+C to close the tunnel"
echo "=========================================="
echo ""

ssh -N -L ${LOCAL_PORT}:${GPU_NODE}:${REMOTE_PORT} ${HPC_USER}@${HPC_LOGIN_NODE}
