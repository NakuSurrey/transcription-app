# client/audio/youtube.py — YouTube Audio Extraction
# Phase 3: The Ears (Bulk Mode)
#
# This module does ONE job:
#   Download ONLY the audio track from a YouTube URL
#   Save it as a file on your local machine
#   That's it. yt-dlp's job ENDS when the file is saved.
#
# A SEPARATE module (client/network/) picks up this file
# and sends it to the server. These two don't know each other.
# They share a file on your local filesystem.
#
# RUNS ON: Your Windows laptop (client-side)
# REQUIRES: yt-dlp (pip install yt-dlp)

import subprocess
import sys
import os
import yt_dlp


# ============================================
# AUTO-UPDATE YT-DLP ON LAUNCH
# ============================================
# YouTube constantly changes its internal code to block
# download tools. yt-dlp releases updates to counter this.
# Running this on app launch = self-healing.
#
# WHY: If yt-dlp is outdated by even a few days,
# YouTube extraction can silently fail.

def auto_update_ytdlp():
    """
    Silently upgrade yt-dlp to latest version.
    Runs on every app launch to stay ahead of YouTube's anti-bot changes.
    """
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
            capture_output=True,  # Don't show pip output to user
            timeout=30            # Don't hang forever if network is slow
        )
        print("[YT-DLP] Auto-update complete")
    except Exception as e:
        # Update failed — not fatal, existing version might still work
        print(f"[YT-DLP] Auto-update failed (not critical): {e}")


# ============================================
# COOKIE FILE PATH
# ============================================
# yt-dlp can use your browser cookies to look like a real
# logged-in human instead of a bot.
#
# WITHOUT cookies: YouTube may block you, show CAPTCHAs, or
#   restrict age-gated content
# WITH cookies: YouTube sees an authenticated browser session,
#   treats you as a normal user
#
# Export cookies from your browser using a browser extension
# (like "Get cookies.txt") and save as cookies.txt in project root.
# This file is in .gitignore — NEVER committed.

COOKIES_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "cookies.txt")


# ============================================
# YOUTUBE AUDIO DOWNLOADER
# ============================================

class YouTubeExtractor:
    """
    Downloads audio-only from YouTube URLs using yt-dlp.

    Usage:
        extractor = YouTubeExtractor()
        filepath = extractor.download("https://www.youtube.com/watch?v=...")
        # filepath is now a local .wav file ready to send to server
    """

    def __init__(self, output_dir=None):
        """
        Args:
            output_dir: Where to save downloaded audio files.
                        Defaults to a 'downloads' folder in project root.
        """
        if output_dir is None:
            output_dir = os.path.join(
                os.path.dirname(__file__), "..", "..", "downloads"
            )
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def download(self, url: str, progress_callback=None) -> str:
        """
        Download audio from a YouTube URL.

        Args:
            url: YouTube video URL
            progress_callback: Optional function called with download progress
                             Signature: callback(percent: float, status: str)
                             Used by UI to show progress bar.

        Returns:
            Filepath to the downloaded audio file (.wav)

        Raises:
            Exception if download fails (bad URL, blocked, network error)
        """
        # yt-dlp configuration
        ydl_opts = {
            # Extract ONLY audio, no video
            # WHY: Video data is massive and useless for transcription.
            # A 1-hour video might be 2GB with video, 50MB audio only.
            "format": "bestaudio/best",

            # Convert to WAV format after download
            # WHY WAV? Uncompressed audio — AI models work best with
            # raw uncompressed audio. No quality lost to compression.
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "wav",
                "preferredquality": "192",
            }],

            # Output file path template
            # %(title)s = video title, used as filename
            "outtmpl": os.path.join(self.output_dir, "%(title)s.%(ext)s"),

            # Quiet mode — don't spam console with download progress
            "quiet": True,
            "no_warnings": True,
        }

        # Add cookies if the file exists
        # Cookie file is optional — works without it for most videos,
        # but needed for age-restricted or region-locked content
        cookies_path = os.path.abspath(COOKIES_FILE)
        if os.path.exists(cookies_path):
            ydl_opts["cookiefile"] = cookies_path
            print("[YT-DLP] Using browser cookies for authentication")
        else:
            print("[YT-DLP] No cookies.txt found — proceeding without auth")

        # Add progress hook if callback provided
        if progress_callback:
            def progress_hook(d):
                if d["status"] == "downloading":
                    percent = d.get("_percent_str", "0%").strip()
                    progress_callback(float(percent.replace("%", "")), "Downloading")
                elif d["status"] == "finished":
                    progress_callback(100.0, "Processing audio")

            ydl_opts["progress_hooks"] = [progress_hook]

        # Execute the download
        print(f"[YT-DLP] Downloading audio from: {url}")
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # extract_info downloads + returns video metadata
                info = ydl.extract_info(url, download=True)
                title = info.get("title", "unknown")

                # The final file path after conversion to WAV
                filepath = os.path.join(self.output_dir, f"{title}.wav")

                # Verify file exists
                if not os.path.exists(filepath):
                    # Sometimes yt-dlp sanitizes the title differently
                    # Look for any .wav file in the output dir
                    wav_files = [f for f in os.listdir(self.output_dir)
                                 if f.endswith(".wav")]
                    if wav_files:
                        filepath = os.path.join(self.output_dir, wav_files[-1])
                    else:
                        raise FileNotFoundError("Download succeeded but WAV file not found")

                file_size_mb = os.path.getsize(filepath) / (1024 * 1024)
                print(f"[YT-DLP] Saved: {filepath} ({file_size_mb:.1f} MB)")
                return filepath

        except Exception as e:
            print(f"[YT-DLP] Download failed: {e}")
            raise

    def get_video_info(self, url: str) -> dict:
        """
        Get video metadata without downloading.
        Used by UI to show video title/duration before user commits to download.
        """
        ydl_opts = {"quiet": True, "no_warnings": True}

        # Add cookies if available
        cookies_path = os.path.abspath(COOKIES_FILE)
        if os.path.exists(cookies_path):
            ydl_opts["cookiefile"] = cookies_path

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                "title": info.get("title", "Unknown"),
                "duration_seconds": info.get("duration", 0),
                "duration_formatted": self._format_duration(info.get("duration", 0)),
                "channel": info.get("channel", "Unknown"),
                "thumbnail": info.get("thumbnail", None),
            }

    @staticmethod
    def _format_duration(seconds: int) -> str:
        """Convert seconds to HH:MM:SS format."""
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"
