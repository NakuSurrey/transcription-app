# client/main.py — Application Entry Point
# Launches the transcription overlay UI
#
# This is the file you run: python client/main.py

from ui.overlay import run_app
from audio.youtube import auto_update_ytdlp

if __name__ == "__main__":
    # Self-heal yt-dlp before anything else
    print("[STARTUP] Checking for yt-dlp updates...")
    auto_update_ytdlp()

    # Launch the UI
    print("[STARTUP] Launching transcription overlay...")
    run_app()
