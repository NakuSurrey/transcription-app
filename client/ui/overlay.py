# client/ui/overlay.py — Stealth Transcription Overlay
# Phase 5: The Face
#
# Features:
#   1. Borderless translucent dark-mode overlay (Cluely-style)
#   2. Ghost Feature — invisible to screen sharing software
#   3. Cloud Switch — start/stop Digital Ocean GPU droplet
#   4. Dual Mode — toggle between Live Mode and Bulk Mode
#   5. Export Suite — copy to clipboard, save as .txt or .srt
#
# RUNS ON: Your Windows laptop (client-side)
# REQUIRES: PyQt6, ctypes (built into Python on Windows)

import sys
import os
import ctypes
import asyncio
import threading
from datetime import timedelta

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTextEdit, QLineEdit, QFileDialog,
    QStackedWidget, QFrame, QMessageBox
)
from PyQt6.QtCore import Qt, QPoint, pyqtSignal, QObject, QTimer
from PyQt6.QtGui import QFont, QColor, QPalette, QIcon

# Import the async workers that connect audio → network → UI
from ui.workers import LiveWorker, BulkWorker
from network.cloud_control import CloudController


# ============================================
# GHOST FEATURE — Screen Share Invisibility
# ============================================
# Uses Windows API SetWindowDisplayAffinity with
# WDA_EXCLUDEFROMCAPTURE flag.
#
# HOW IT WORKS:
#   Windows Desktop Window Manager (DWM) renders screen as layers.
#   Screen capture software asks DWM for all layers composited.
#   WDA_EXCLUDEFROMCAPTURE tells DWM: "skip my layer in captures."
#   Physical monitor still shows it (gets full composite).
#   Result: visible to your eyes, invisible to Zoom/Teams/OBS.
#
# WHY ctypes?
#   PyQt6 doesn't expose Windows-specific low-level functions.
#   ctypes = Python's bridge to call C-level Windows API directly.
#   We're reaching past Python into the operating system itself.

# Windows API constants
WDA_EXCLUDEFROMCAPTURE = 0x00000011

def enable_ghost_mode(window_handle):
    """
    Make a window invisible to all screen capture software.
    
    Args:
        window_handle: The Windows HWND (handle) of the PyQt6 window
    """
    try:
        ctypes.windll.user32.SetWindowDisplayAffinity(
            window_handle,
            WDA_EXCLUDEFROMCAPTURE
        )
        print("[GHOST] Screen capture invisibility enabled")
    except Exception as e:
        print(f"[GHOST] Failed to enable (non-Windows OS?): {e}")

def disable_ghost_mode(window_handle):
    """Restore window visibility to screen capture software."""
    try:
        ctypes.windll.user32.SetWindowDisplayAffinity(window_handle, 0x00000000)
        print("[GHOST] Screen capture invisibility disabled")
    except Exception as e:
        print(f"[GHOST] Failed to disable: {e}")


# ============================================
# STYLESHEET — Dark Mode Translucent Theme
# ============================================
# PyQt6 uses CSS-like stylesheets for appearance.
# All colors use RGBA where A (alpha) < 255 = translucent.

STYLESHEET = """
    QMainWindow {
        background-color: rgba(15, 15, 20, 200);
    }
    QLabel {
        color: #E8E8E8;
        font-size: 13px;
    }
    QLabel#title {
        color: #FFFFFF;
        font-size: 16px;
        font-weight: bold;
    }
    QLabel#status {
        color: #888888;
        font-size: 11px;
    }
    QTextEdit {
        background-color: rgba(25, 25, 35, 180);
        color: #F0F0F0;
        border: 1px solid rgba(255, 255, 255, 30);
        border-radius: 8px;
        padding: 10px;
        font-size: 14px;
        font-family: 'Segoe UI', 'Consolas', monospace;
    }
    QLineEdit {
        background-color: rgba(25, 25, 35, 180);
        color: #F0F0F0;
        border: 1px solid rgba(255, 255, 255, 30);
        border-radius: 6px;
        padding: 8px 12px;
        font-size: 13px;
    }
    QLineEdit:focus {
        border: 1px solid rgba(100, 150, 255, 150);
    }
    QPushButton {
        background-color: rgba(60, 60, 80, 200);
        color: #E0E0E0;
        border: 1px solid rgba(255, 255, 255, 20);
        border-radius: 6px;
        padding: 8px 16px;
        font-size: 12px;
        font-weight: bold;
    }
    QPushButton:hover {
        background-color: rgba(80, 80, 110, 220);
        border: 1px solid rgba(100, 150, 255, 100);
    }
    QPushButton:pressed {
        background-color: rgba(40, 40, 60, 220);
    }
    QPushButton#server_on {
        background-color: rgba(30, 120, 60, 200);
        border: 1px solid rgba(50, 200, 100, 100);
    }
    QPushButton#server_off {
        background-color: rgba(120, 30, 30, 200);
        border: 1px solid rgba(200, 50, 50, 100);
    }
    QPushButton#mode_active {
        background-color: rgba(50, 100, 180, 200);
        border: 1px solid rgba(80, 150, 255, 150);
    }
    QFrame#separator {
        background-color: rgba(255, 255, 255, 20);
        max-height: 1px;
    }
"""


# ============================================
# ASYNC WORKER — Runs async code from PyQt6
# ============================================
# PyQt6 runs its own event loop (for UI).
# asyncio runs its own event loop (for network).
# They can't share a loop — so we run asyncio in
# a separate thread and communicate via Qt signals.

class AsyncSignals(QObject):
    """Signals to communicate between async thread and UI thread."""
    transcript_received = pyqtSignal(str, float, str, bool)  # text, confidence, model, fallback
    bulk_complete = pyqtSignal(str)       # full transcript text
    connection_status = pyqtSignal(bool)  # connected True/False
    server_status = pyqtSignal(str)       # "booting", "alive", "offline"
    error = pyqtSignal(str)               # error message
    download_progress = pyqtSignal(float, str)  # percent, status


# ============================================
# MAIN WINDOW — The Stealth Overlay
# ============================================

class TranscriptionOverlay(QMainWindow):
    """
    Main application window.
    Borderless, translucent, draggable, ghost-mode capable.
    """

    def __init__(self):
        super().__init__()
        self.signals = AsyncSignals()
        self.ghost_enabled = False
        self.is_live = False
        self.drag_position = None

        # Transcript storage for export
        self.current_transcript = ""
        self.transcript_segments = []  # List of (timestamp, text) for SRT export

        self._setup_window()
        self._build_ui()
        self._connect_signals()

        # Initialize the async workers that connect audio → network → UI
        self.live_worker = LiveWorker(self.signals)
        self.bulk_worker = BulkWorker(self.signals)
        self.cloud = CloudController()

    # ------------------------------------------
    # WINDOW SETUP
    # ------------------------------------------

    def _setup_window(self):
        """Configure the borderless, translucent, always-on-top window."""
        self.setWindowTitle("Transcription")
        self.setFixedSize(480, 620)

        # FramelessWindowHint — removes title bar, min/max/close buttons
        # WindowStaysOnTopHint — overlay floats above all other windows
        # WA_TranslucentBackground — allows rgba alpha transparency
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # Apply the dark stylesheet
        self.setStyleSheet(STYLESHEET)

    # ------------------------------------------
    # BUILD THE UI LAYOUT
    # ------------------------------------------

    def _build_ui(self):
        """Construct all UI elements."""
        # Central widget — everything goes inside this
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(16, 12, 16, 12)
        main_layout.setSpacing(8)

        # --- TOP BAR: Title + Close button ---
        top_bar = QHBoxLayout()

        title = QLabel("Transcription")
        title.setObjectName("title")
        top_bar.addWidget(title)

        top_bar.addStretch()

        # Ghost toggle button
        self.ghost_btn = QPushButton("Ghost: OFF")
        self.ghost_btn.setFixedWidth(100)
        self.ghost_btn.clicked.connect(self._toggle_ghost)
        top_bar.addWidget(self.ghost_btn)

        # Close button (since we removed the title bar)
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(30, 30)
        close_btn.clicked.connect(self.close)
        top_bar.addWidget(close_btn)

        main_layout.addLayout(top_bar)

        # --- SEPARATOR ---
        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFrameShape(QFrame.Shape.HLine)
        main_layout.addWidget(sep)

        # --- SERVER STATUS BAR ---
        server_bar = QHBoxLayout()

        self.server_status_label = QLabel("Server: Offline")
        self.server_status_label.setObjectName("status")
        server_bar.addWidget(self.server_status_label)

        server_bar.addStretch()

        self.server_btn = QPushButton("Start Server")
        self.server_btn.setObjectName("server_off")
        self.server_btn.setFixedWidth(120)
        self.server_btn.clicked.connect(self._toggle_server)
        server_bar.addWidget(self.server_btn)

        main_layout.addLayout(server_bar)

        # --- MODE TOGGLE ---
        mode_bar = QHBoxLayout()

        self.live_btn = QPushButton("Live Mode")
        self.live_btn.setObjectName("mode_active")
        self.live_btn.clicked.connect(lambda: self._switch_mode("live"))
        mode_bar.addWidget(self.live_btn)

        self.bulk_btn = QPushButton("Bulk Mode")
        self.bulk_btn.clicked.connect(lambda: self._switch_mode("bulk"))
        mode_bar.addWidget(self.bulk_btn)

        main_layout.addLayout(mode_bar)

        # --- STACKED WIDGET (switches between Live and Bulk views) ---
        self.stack = QStackedWidget()

        # Live Mode Panel
        self.live_panel = self._build_live_panel()
        self.stack.addWidget(self.live_panel)

        # Bulk Mode Panel
        self.bulk_panel = self._build_bulk_panel()
        self.stack.addWidget(self.bulk_panel)

        main_layout.addWidget(self.stack)

    def _build_live_panel(self) -> QWidget:
        """Build the Live Mode view — floating transcript display."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 8, 0, 0)

        # Live transcript display
        self.live_text = QTextEdit()
        self.live_text.setReadOnly(True)
        self.live_text.setPlaceholderText(
            "Live transcript will appear here...\n\n"
            "1. Start the server\n"
            "2. Click 'Start Listening' below\n"
            "3. Play audio through your speakers"
        )
        layout.addWidget(self.live_text)

        # Model info label
        self.model_label = QLabel("")
        self.model_label.setObjectName("status")
        layout.addWidget(self.model_label)

        # Start/Stop listening button
        self.listen_btn = QPushButton("Start Listening")
        self.listen_btn.clicked.connect(self._toggle_listening)
        layout.addWidget(self.listen_btn)

        return panel

    def _build_bulk_panel(self) -> QWidget:
        """Build the Bulk Mode view — URL input + transcript viewer + export."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 8, 0, 0)

        # URL input row
        url_row = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Paste YouTube URL here...")
        url_row.addWidget(self.url_input)

        self.download_btn = QPushButton("Transcribe")
        self.download_btn.setFixedWidth(100)
        self.download_btn.clicked.connect(self._start_bulk_transcription)
        url_row.addWidget(self.download_btn)

        layout.addLayout(url_row)

        # Progress/status label
        self.bulk_status = QLabel("")
        self.bulk_status.setObjectName("status")
        layout.addWidget(self.bulk_status)

        # Transcript viewer
        self.bulk_text = QTextEdit()
        self.bulk_text.setReadOnly(True)
        self.bulk_text.setPlaceholderText("Transcript will appear here after processing...")
        layout.addWidget(self.bulk_text)

        # Export buttons row
        export_row = QHBoxLayout()

        copy_btn = QPushButton("Copy to Clipboard")
        copy_btn.clicked.connect(self._copy_to_clipboard)
        export_row.addWidget(copy_btn)

        save_txt_btn = QPushButton("Save .txt")
        save_txt_btn.clicked.connect(lambda: self._save_transcript("txt"))
        export_row.addWidget(save_txt_btn)

        save_srt_btn = QPushButton("Save .srt")
        save_srt_btn.clicked.connect(lambda: self._save_transcript("srt"))
        export_row.addWidget(save_srt_btn)

        layout.addLayout(export_row)

        return panel

    # ------------------------------------------
    # SIGNAL CONNECTIONS
    # ------------------------------------------

    def _connect_signals(self):
        """Wire up async signals to UI update methods."""
        self.signals.transcript_received.connect(self._on_transcript_received)
        self.signals.bulk_complete.connect(self._on_bulk_complete)
        self.signals.server_status.connect(self._on_server_status)
        self.signals.error.connect(self._on_error)
        self.signals.download_progress.connect(self._on_download_progress)

    # ------------------------------------------
    # GHOST MODE
    # ------------------------------------------

    def _toggle_ghost(self):
        """Toggle screen capture invisibility on/off."""
        hwnd = int(self.winId())  # Get the Windows handle for this window

        if self.ghost_enabled:
            disable_ghost_mode(hwnd)
            self.ghost_btn.setText("Ghost: OFF")
            self.ghost_enabled = False
        else:
            enable_ghost_mode(hwnd)
            self.ghost_btn.setText("Ghost: ON")
            self.ghost_enabled = True

    # ------------------------------------------
    # SERVER CONTROL (Cloud Switch)
    # ------------------------------------------

    def _toggle_server(self):
        """
        Start or stop the Digital Ocean GPU droplet.
        Sends API request to Digital Ocean to power on/off.
        """
        current_text = self.server_btn.text()

        if current_text == "Start Server":
            self.server_btn.setText("Booting...")
            self.server_btn.setEnabled(False)
            self.server_status_label.setText("Server: Booting...")

            # Fire Digital Ocean API request in background thread
            def _boot():
                loop = asyncio.new_event_loop()
                success = loop.run_until_complete(self.cloud.start_server())
                loop.close()
                if success:
                    # Start heartbeat to protect credits
                    server_ip = os.getenv("SERVER_IP", "localhost")
                    server_port = os.getenv("SERVER_PORT", "8000")
                    self.cloud.start_heartbeat(server_ip, server_port)
                    self.signals.server_status.emit("booting")
                else:
                    self.signals.error.emit("Failed to start server")
                    self.signals.server_status.emit("offline")

            threading.Thread(target=_boot, daemon=True).start()

            # Poll server health until it's ready
            self._poll_server_ready()
        else:
            # Stop heartbeat first, then power off
            self.cloud.stop_heartbeat()

            def _shutdown():
                loop = asyncio.new_event_loop()
                loop.run_until_complete(self.cloud.stop_server())
                loop.close()

            threading.Thread(target=_shutdown, daemon=True).start()

            self.server_btn.setText("Start Server")
            self.server_btn.setObjectName("server_off")
            self.server_btn.setStyle(self.server_btn.style())
            self.server_status_label.setText("Server: Offline")

    def _poll_server_ready(self):
        """
        Poll the server health endpoint every 5 seconds until it responds.
        Server takes ~1-2 min to boot + load models.
        """
        self._poll_count = 0
        self._poll_timer = QTimer()
        self._poll_timer.setInterval(5000)  # Check every 5 seconds

        def _check():
            self._poll_count += 1

            # Give up after 2 minutes (24 checks * 5 seconds)
            if self._poll_count > 24:
                self._poll_timer.stop()
                self.server_btn.setText("Start Server")
                self.server_btn.setObjectName("server_off")
                self.server_btn.setStyle(self.server_btn.style())
                self.server_btn.setEnabled(True)
                self.signals.error.emit("Server boot timed out (2 minutes)")
                return

            # Check health in background
            def _health_check():
                loop = asyncio.new_event_loop()
                from network.transmitter import LiveTransmitter
                t = LiveTransmitter()
                healthy = loop.run_until_complete(t.check_server_health())
                loop.close()
                if healthy:
                    self._poll_timer.stop()
                    self.signals.server_status.emit("online")

            threading.Thread(target=_health_check, daemon=True).start()

        self._poll_timer.timeout.connect(_check)
        self._poll_timer.start()

    # ------------------------------------------
    # MODE SWITCHING
    # ------------------------------------------

    def _switch_mode(self, mode: str):
        """Toggle between Live Mode and Bulk Mode views."""
        if mode == "live":
            self.stack.setCurrentIndex(0)
            self.live_btn.setObjectName("mode_active")
            self.bulk_btn.setObjectName("")
        else:
            self.stack.setCurrentIndex(1)
            self.bulk_btn.setObjectName("mode_active")
            self.live_btn.setObjectName("")

        # Force style refresh on both buttons
        self.live_btn.setStyle(self.live_btn.style())
        self.bulk_btn.setStyle(self.bulk_btn.style())

    # ------------------------------------------
    # LIVE MODE CONTROLS
    # ------------------------------------------

    def _toggle_listening(self):
        """Start or stop live audio capture and streaming."""
        if not self.is_live:
            self.is_live = True
            self.listen_btn.setText("Stop Listening")
            self.live_text.clear()
            self.model_label.setText("Connecting...")
            # Start the live pipeline: AudioCapturer → WSS → Server → Text back
            self.live_worker.start()
        else:
            self.is_live = False
            self.listen_btn.setText("Start Listening")
            self.model_label.setText("Stopped")
            # Stop capture and disconnect WSS
            self.live_worker.stop()

    def _on_transcript_received(self, text: str, confidence: float,
                                 model: str, fallback: bool):
        """Called when live transcript text arrives from server."""
        self.live_text.append(text)
        fallback_note = " (fallback)" if fallback else ""
        self.model_label.setText(
            f"Model: {model}{fallback_note} | Confidence: {confidence:.0%}"
        )

    # ------------------------------------------
    # BULK MODE CONTROLS
    # ------------------------------------------

    def _start_bulk_transcription(self):
        """Download YouTube audio and send to server for transcription."""
        url = self.url_input.text().strip()
        if not url:
            self._on_error("Please paste a YouTube URL")
            return

        self.download_btn.setEnabled(False)
        self.download_btn.setText("Working...")
        self.bulk_status.setText("Downloading audio...")
        self.bulk_text.clear()
        # Start the bulk pipeline: yt-dlp download → HTTP POST → transcript back
        self.bulk_worker.start(url)

    def _on_bulk_complete(self, transcript: str):
        """Called when bulk transcription finishes."""
        self.current_transcript = transcript
        self.bulk_text.setText(transcript)
        self.bulk_status.setText("Transcription complete")
        self.download_btn.setEnabled(True)
        self.download_btn.setText("Transcribe")

    def _on_download_progress(self, percent: float, status: str):
        """Update progress during YouTube download."""
        self.bulk_status.setText(f"{status}: {percent:.0f}%")

    # ------------------------------------------
    # EXPORT SUITE
    # ------------------------------------------

    def _copy_to_clipboard(self):
        """Copy transcript to system clipboard."""
        text = self.bulk_text.toPlainText()
        if not text:
            self._on_error("No transcript to copy")
            return

        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        self.bulk_status.setText("Copied to clipboard!")

    def _save_transcript(self, format_type: str):
        """
        Save transcript as .txt or .srt file.
        Opens a native file save dialog.
        """
        text = self.bulk_text.toPlainText()
        if not text:
            self._on_error("No transcript to save")
            return

        if format_type == "txt":
            filter_str = "Text Files (*.txt)"
            default_ext = ".txt"
        else:
            filter_str = "Subtitle Files (*.srt)"
            default_ext = ".srt"

        filepath, _ = QFileDialog.getSaveFileName(
            self, "Save Transcript", f"transcript{default_ext}", filter_str
        )

        if not filepath:
            return  # User cancelled

        try:
            if format_type == "srt":
                content = self._generate_srt(text)
            else:
                content = text

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)

            self.bulk_status.setText(f"Saved to {os.path.basename(filepath)}")
        except Exception as e:
            self._on_error(f"Save failed: {e}")

    def _generate_srt(self, text: str) -> str:
        """
        Convert plain transcript text to SRT subtitle format.
        
        SRT format:
            1
            00:00:01,000 --> 00:00:04,000
            Hello and welcome to this video
            
            2
            00:00:04,000 --> 00:00:07,000
            Today we're going to talk about...
        
        Each segment gets a sequential number, timestamps, and text.
        """
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        srt_blocks = []
        seconds_per_line = 3  # Approximate duration per subtitle line

        for i, line in enumerate(lines):
            start_seconds = i * seconds_per_line
            end_seconds = start_seconds + seconds_per_line

            start_ts = self._seconds_to_srt_time(start_seconds)
            end_ts = self._seconds_to_srt_time(end_seconds)

            srt_blocks.append(f"{i + 1}\n{start_ts} --> {end_ts}\n{line}\n")

        return "\n".join(srt_blocks)

    @staticmethod
    def _seconds_to_srt_time(seconds: int) -> str:
        """Convert seconds to SRT timestamp format: HH:MM:SS,mmm"""
        td = timedelta(seconds=seconds)
        total_seconds = int(td.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        secs = total_seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d},000"

    # ------------------------------------------
    # ERROR HANDLING
    # ------------------------------------------

    def _on_error(self, message: str):
        """Display error message in the status label."""
        self.bulk_status.setText(f"Error: {message}")
        self.bulk_status.setStyleSheet("color: #FF6B6B;")
        # Reset color after 3 seconds
        QTimer.singleShot(3000, lambda: self.bulk_status.setStyleSheet(""))

    def _on_server_status(self, status: str):
        """Update server status display and button state."""
        self.server_status_label.setText(f"Server: {status.capitalize()}")

        if status == "online":
            self.server_btn.setText("Stop Server")
            self.server_btn.setObjectName("server_on")
            self.server_btn.setStyle(self.server_btn.style())
            self.server_btn.setEnabled(True)
        elif status == "offline":
            self.server_btn.setText("Start Server")
            self.server_btn.setObjectName("server_off")
            self.server_btn.setStyle(self.server_btn.style())
            self.server_btn.setEnabled(True)
        elif status == "booting":
            self.server_btn.setText("Booting...")
            self.server_btn.setEnabled(False)

    # ------------------------------------------
    # DRAGGABLE WINDOW (since no title bar)
    # ------------------------------------------
    # Without a title bar, the user can't drag the window.
    # We manually track mouse press → hold → move → release.

    def mousePressEvent(self, event):
        """Record where the mouse clicked (for drag calculation)."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        """Move window to follow the mouse while dragging."""
        if self.drag_position and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_position)

    def mouseReleaseEvent(self, event):
        """Clear drag state when mouse released."""
        self.drag_position = None

    # ------------------------------------------
    # CLOSE EVENT — Auto-shutdown server
    # ------------------------------------------

    def closeEvent(self, event):
        """
        Called when window closes. Shuts down everything to save credits.
        
        PROBLEM: What if app crashes? closeEvent won't fire.
        SOLUTION: Heartbeat system — server auto-shuts down if no
        ping received for 5 minutes. Belt and suspenders.
        """
        # Stop live capture if running
        if self.is_live:
            self.live_worker.stop()
            self.is_live = False

        # Stop any bulk operation
        self.bulk_worker.stop()

        # Stop heartbeat
        self.cloud.stop_heartbeat()

        # Shut down the cloud server to save credits
        def _shutdown():
            loop = asyncio.new_event_loop()
            loop.run_until_complete(self.cloud.stop_server())
            loop.close()

        shutdown_thread = threading.Thread(target=_shutdown, daemon=True)
        shutdown_thread.start()
        shutdown_thread.join(timeout=5)  # Wait max 5 sec for shutdown

        print("[APP] Shutdown complete")
        event.accept()


# ============================================
# APPLICATION ENTRY POINT
# ============================================

def run_app():
    """Launch the transcription overlay application."""
    app = QApplication(sys.argv)

    # Set application-wide font
    font = QFont("Segoe UI", 10)
    app.setFont(font)

    # Create and show the overlay
    window = TranscriptionOverlay()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    run_app()
