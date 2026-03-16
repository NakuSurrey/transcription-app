# client/network/cloud_control.py — Digital Ocean GPU Droplet Controller
# The Cloud Switch + Heartbeat System
#
# TWO JOBS:
#   1. Start/stop the GPU droplet via Digital Ocean API
#   2. Heartbeat — ping server every 60 seconds so it knows app is alive
#      If no ping for 5 minutes, server shuts itself down (crash protection)
#
# RUNS ON: Your Windows laptop (client-side)
# REQUIRES: aiohttp, python-dotenv

import os
import asyncio
import threading
import aiohttp
from dotenv import load_dotenv

load_dotenv()

DO_API_TOKEN = os.getenv("DO_API_TOKEN", "")
DO_DROPLET_ID = os.getenv("DO_DROPLET_ID", "")
DO_API_BASE = "https://api.digitalocean.com/v2"

# Heartbeat interval (seconds) — how often we tell server "I'm alive"
HEARTBEAT_INTERVAL = 60


class CloudController:
    """
    Controls the Digital Ocean GPU droplet lifecycle.
    
    Usage:
        cloud = CloudController()
        await cloud.start_server()   # Powers on droplet
        await cloud.stop_server()    # Powers off droplet
        cloud.start_heartbeat()      # Begin pinging server
        cloud.stop_heartbeat()       # Stop pinging
    """

    def __init__(self):
        self.server_running = False
        self.heartbeat_thread = None
        self.heartbeat_active = False
        self.headers = {
            "Authorization": f"Bearer {DO_API_TOKEN}",
            "Content-Type": "application/json"
        }

    async def start_server(self) -> bool:
        """
        Power on the Digital Ocean GPU droplet.
        
        Sends POST to Digital Ocean API:
            /v2/droplets/{id}/actions with {"type": "power_on"}
        
        Returns True if request accepted, False on failure.
        Boot time: ~1-2 minutes (server OS boots + models load into GPU)
        """
        if not DO_API_TOKEN or not DO_DROPLET_ID:
            print("[CLOUD] ERROR: DO_API_TOKEN or DO_DROPLET_ID not set in .env")
            return False

        url = f"{DO_API_BASE}/droplets/{DO_DROPLET_ID}/actions"
        payload = {"type": "power_on"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload,
                                        headers=self.headers) as resp:
                    if resp.status in (200, 201):
                        self.server_running = True
                        print("[CLOUD] Server power-on request accepted")
                        return True
                    else:
                        error = await resp.text()
                        print(f"[CLOUD] Power-on failed ({resp.status}): {error}")
                        return False
        except Exception as e:
            print(f"[CLOUD] Power-on request error: {e}")
            return False

    async def stop_server(self) -> bool:
        """
        Power off the Digital Ocean GPU droplet.
        Called on app close to save cloud credits.
        """
        if not DO_API_TOKEN or not DO_DROPLET_ID:
            return False

        url = f"{DO_API_BASE}/droplets/{DO_DROPLET_ID}/actions"
        payload = {"type": "power_off"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload,
                                        headers=self.headers) as resp:
                    if resp.status in (200, 201):
                        self.server_running = False
                        print("[CLOUD] Server power-off request accepted")
                        return True
                    else:
                        error = await resp.text()
                        print(f"[CLOUD] Power-off failed ({resp.status}): {error}")
                        return False
        except Exception as e:
            print(f"[CLOUD] Power-off request error: {e}")
            return False

    async def get_server_status(self) -> str:
        """
        Check current droplet status.
        Returns: "active", "off", "new", or "error"
        """
        if not DO_API_TOKEN or not DO_DROPLET_ID:
            return "error"

        url = f"{DO_API_BASE}/droplets/{DO_DROPLET_ID}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        status = data.get("droplet", {}).get("status", "unknown")
                        return status
                    return "error"
        except Exception:
            return "error"

    # ------------------------------------------
    # HEARTBEAT SYSTEM
    # ------------------------------------------
    # Belt and suspenders for when app crashes.
    # If closeEvent never fires (crash/force quit),
    # server would run forever burning credits.
    # Heartbeat: app pings server every 60 seconds.
    # Server watches: no ping for 5 min → auto shutdown.

    def start_heartbeat(self, server_ip: str, server_port: str):
        """Start the heartbeat ping in a background thread."""
        self.heartbeat_active = True
        self.heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(server_ip, server_port),
            daemon=True
        )
        self.heartbeat_thread.start()
        print("[HEARTBEAT] Started — pinging every 60 seconds")

    def stop_heartbeat(self):
        """Stop the heartbeat."""
        self.heartbeat_active = False
        if self.heartbeat_thread:
            self.heartbeat_thread.join(timeout=2)
        print("[HEARTBEAT] Stopped")

    def _heartbeat_loop(self, server_ip: str, server_port: str):
        """Background loop that pings the server's health endpoint."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        url = f"http://{server_ip}:{server_port}/health"

        while self.heartbeat_active:
            try:
                loop.run_until_complete(self._ping(url))
            except Exception:
                pass  # Server might be booting, don't crash heartbeat

            # Sleep in small increments so stop() is responsive
            for _ in range(HEARTBEAT_INTERVAL):
                if not self.heartbeat_active:
                    break
                import time
                time.sleep(1)

        loop.close()

    async def _ping(self, url: str):
        """Send a single heartbeat ping."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url,
                                       timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        pass  # Server is alive, all good
                    else:
                        print(f"[HEARTBEAT] Server responded with {resp.status}")
        except Exception:
            print("[HEARTBEAT] Server not responding")
