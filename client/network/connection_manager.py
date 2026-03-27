# client/network/connection_manager.py — Server Connection Mode Manager
# Step 4b: Handles the difference between HPC and DigitalOcean infrastructure
#
# This module reads SERVER_MODE from .env and provides a unified interface
# for the UI to check server status without needing to know which
# infrastructure is running underneath.
#
# TWO MODES:
#   "hpc"          → Server managed manually (sbatch/scancel/tunnel)
#                    App only checks if server is reachable
#   "digitalocean" → Server managed by app via DigitalOcean API
#                    App can start/stop the droplet
#
# RUNS ON: Your Windows laptop (client-side)
# USED BY: overlay.py (UI server status button)

import os
import aiohttp
from dotenv import load_dotenv

load_dotenv()

# ============================================
# READ MODE FROM .env
# ============================================
# os.getenv reads the value of SERVER_MODE from the .env file
# .lower() converts to lowercase so "HPC", "Hpc", "hpc" all work
# Default is "hpc" if the variable is missing or empty

SERVER_MODE = os.getenv("SERVER_MODE", "hpc").lower()
SERVER_IP = os.getenv("SERVER_IP", "localhost")
SERVER_PORT = os.getenv("SERVER_PORT", "8000")


class ConnectionManager:
    """
    Unified interface for server status and control.

    The UI calls this instead of directly using CloudController.
    ConnectionManager decides what to do based on SERVER_MODE.

    Usage:
        manager = ConnectionManager()
        print(manager.get_mode())                  # "hpc" or "digitalocean"
        is_alive = await manager.is_server_available()  # True/False
        msg = manager.get_status_message(is_alive)      # Human-readable string
    """

    def __init__(self):
        self.mode = SERVER_MODE
        self.cloud_controller = None

        # Only create CloudController if we're in DigitalOcean mode
        # This avoids importing DigitalOcean-specific code when using HPC
        if self.mode == "digitalocean":
            from network.cloud_control import CloudController
            self.cloud_controller = CloudController()

    # ============================================
    # MODE DETECTION
    # ============================================

    def get_mode(self) -> str:
        """
        Returns the current server mode.

        Returns:
            "hpc" or "digitalocean"
        """
        return self.mode

    def is_hpc_mode(self) -> bool:
        """Returns True if running in HPC mode."""
        return self.mode == "hpc"

    def is_cloud_mode(self) -> bool:
        """Returns True if running in DigitalOcean mode."""
        return self.mode == "digitalocean"

    # ============================================
    # SERVER HEALTH CHECK
    # ============================================
    # This works the same in both modes.
    # It pings the /health endpoint on whatever SERVER_IP:SERVER_PORT
    # is set in .env. In HPC mode, that's localhost:8000 (through tunnel).
    # In DO mode, that's the droplet's public IP.

    async def is_server_available(self) -> bool:
        """
        Ping the server's /health endpoint.
        Returns True if the server responds with {"status": "alive"}.

        Works in both modes — just checks if the server is reachable,
        regardless of how it was started.
        """
        health_url = f"http://{SERVER_IP}:{SERVER_PORT}/health"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    health_url,
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("status") == "alive"
            return False
        except Exception:
            return False

    # ============================================
    # STATUS MESSAGES FOR THE UI
    # ============================================
    # These return human-readable strings that overlay.py displays
    # next to the server status button.

    def get_status_message(self, is_available: bool) -> str:
        """
        Returns a status message appropriate for the current mode.

        Args:
            is_available: Result from is_server_available()

        Returns:
            Human-readable status string for the UI
        """
        if self.mode == "hpc":
            if is_available:
                return "HPC Connected — Server Ready"
            else:
                return "HPC Not Reachable — Run sbatch + tunnel.sh"
        else:
            if is_available:
                return "DigitalOcean — Server Ready"
            else:
                return "DigitalOcean — Server Offline"

    def get_button_label(self, is_available: bool) -> str:
        """
        Returns what the server button should say based on mode and state.

        Args:
            is_available: Result from is_server_available()

        Returns:
            Button label string
        """
        if self.mode == "hpc":
            # HPC mode: button only checks connection, never starts/stops
            return "Check Connection"
        else:
            # DigitalOcean mode: button toggles server on/off
            if is_available:
                return "Stop Server"
            else:
                return "Start Server"

    # ============================================
    # SERVER CONTROL (DigitalOcean only)
    # ============================================
    # These methods only do something in DigitalOcean mode.
    # In HPC mode, they return immediately with a message.
    # This prevents the UI from accidentally trying to call
    # DigitalOcean API when using the HPC tunnel.

    async def start_server(self) -> dict:
        """
        Start the server. Only works in DigitalOcean mode.

        Returns:
            dict with "success" (bool) and "message" (str)
        """
        if self.mode == "hpc":
            return {
                "success": False,
                "message": "HPC Mode — Start the server manually:\n"
                           "1. SSH into REDACTED_HOST\n"
                           "2. Run: sbatch server/surrey_job.sh\n"
                           "3. Run tunnel.sh from your laptop"
            }

        if self.cloud_controller:
            result = await self.cloud_controller.start_server()
            if result:
                return {"success": True, "message": "DigitalOcean droplet starting..."}
            else:
                return {"success": False, "message": "Failed to start droplet"}

        return {"success": False, "message": "CloudController not initialized"}

    async def stop_server(self) -> dict:
        """
        Stop the server. Only works in DigitalOcean mode.

        Returns:
            dict with "success" (bool) and "message" (str)
        """
        if self.mode == "hpc":
            return {
                "success": False,
                "message": "HPC Mode — Stop the server manually:\n"
                           "1. Run: scancel <job_id>\n"
                           "2. Close the tunnel with Ctrl+C"
            }

        if self.cloud_controller:
            result = await self.cloud_controller.stop_server()
            if result:
                return {"success": True, "message": "DigitalOcean droplet stopping..."}
            else:
                return {"success": False, "message": "Failed to stop droplet"}

        return {"success": False, "message": "CloudController not initialized"}
