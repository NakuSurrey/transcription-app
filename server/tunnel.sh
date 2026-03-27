#!/bin/bash

# server/tunnel.sh — SSH Tunnel to HPC cluster GPU Node
# Run this on YOUR LAPTOP (Windows Git Bash / WSL / Mac terminal)
# Opens a tunnel so localhost:8000 forwards to the GPU node
#
# Usage: bash tunnel.sh gpu-node02
#        bash tunnel.sh gpu-node01
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
    echo "    1. SSH into REDACTED_HOST"
    echo "    2. Run: squeue -u REDACTED_USER"
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
# These values match your HPC cluster account and server setup

SURREY_USER="REDACTED_USER"
LOGIN_NODE="REDACTED_HOST"
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
# The middleman (REDACTED_USER@REDACTED_HOST) = the login node
# Your laptop can reach the login node (it has a public IP).
# The login node can reach gpu-node02 (they're on the same internal network).
# Your laptop CANNOT reach gpu-node02 directly (no public IP).
# The tunnel chains these two connections together.
#
# Data flow:
#   localhost:8000 → [encrypted SSH] → REDACTED_HOST login node → gpu-node02:8000
#   Response travels back the same path in reverse

echo "=========================================="
echo "  SSH TUNNEL — CONNECTING"
echo "=========================================="
echo ""
echo "  Local:   localhost:${LOCAL_PORT}"
echo "  Through: ${SURREY_USER}@${LOGIN_NODE}"
echo "  To:      ${GPU_NODE}:${REMOTE_PORT}"
echo ""
echo "  Your client app connects to: localhost:${LOCAL_PORT}"
echo "  Traffic forwards to: ${GPU_NODE}:${REMOTE_PORT}"
echo ""
echo "  Press Ctrl+C to close the tunnel"
echo "=========================================="
echo ""

ssh -N -L ${LOCAL_PORT}:${GPU_NODE}:${REMOTE_PORT} ${SURREY_USER}@${LOGIN_NODE}
