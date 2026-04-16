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
    QStackedWidget, QFrame, QMessageBox, QComboBox, QCheckBox
)
from PyQt6.QtCore import Qt, QPoint, pyqtSignal, QObject, QTimer
from PyQt6.QtGui import QFont, QColor, QPalette, QIcon

# Import the async workers that connect audio → network → UI
from ui.workers import LiveWorker, BulkWorker
from network.connection_manager import ConnectionManager
from audio.window_selector import list_windows


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
    QWidget#title_bar {
        background-color: rgba(30, 30, 40, 220);
        border-top-left-radius: 8px;
        border-top-right-radius: 8px;
    }
    QLabel#grip {
        color: #555555;
        font-size: 14px;
    }
    QLabel#title_bar_text {
        color: #CCCCCC;
        font-size: 12px;
        font-weight: bold;
    }
    QPushButton#title_close {
        background-color: transparent;
        color: #888888;
        border: none;
        font-size: 14px;
        font-weight: bold;
        padding: 2px 8px;
    }
    QPushButton#title_close:hover {
        color: #FF6B6B;
        background-color: rgba(255, 80, 80, 40);
        border-radius: 4px;
    }
    QComboBox {
        background-color: rgba(25, 25, 35, 180);
        color: #F0F0F0;
        border: 1px solid rgba(255, 255, 255, 30);
        border-radius: 6px;
        padding: 6px 10px;
        font-size: 12px;
    }
    QComboBox:hover {
        border: 1px solid rgba(100, 150, 255, 100);
    }
    QComboBox::drop-down {
        border: none;
        width: 20px;
    }
    QComboBox::down-arrow {
        image: none;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 6px solid #888888;
        margin-right: 8px;
    }
    QComboBox QAbstractItemView {
        background-color: rgba(25, 25, 35, 240);
        color: #F0F0F0;
        border: 1px solid rgba(100, 150, 255, 100);
        selection-background-color: rgba(60, 60, 80, 200);
        padding: 4px;
    }
    QCheckBox {
        color: #E0E0E0;
        font-size: 12px;
        spacing: 6px;
    }
    QCheckBox::indicator {
        width: 16px;
        height: 16px;
        border: 1px solid rgba(255, 255, 255, 40);
        border-radius: 3px;
        background-color: rgba(25, 25, 35, 180);
    }
    QCheckBox::indicator:checked {
        background-color: rgba(50, 100, 180, 200);
        border: 1px solid rgba(80, 150, 255, 150);
    }
    QPushButton#refresh_btn {
        padding: 6px 10px;
        font-size: 11px;
        min-width: 30px;
    }
    QLabel#window_section_label {
        color: #999999;
        font-size: 11px;
        font-weight: bold;
    }
"""


# ============================================
# RECORDING PANEL — Floating Transcript Display
# ============================================
# A floating dark panel that appears during live recording.
# Shows real-time transcript in a scrollable text area.
# Invisible to screen sharing (ghost mode).
#
# WHY A SEPARATE WINDOW?
#   We could resize the main window, but that requires hiding/showing
#   dozens of widgets and managing two layouts in one widget tree.
#   A separate QMainWindow is cleaner — each has its own layout,
#   and they share the same LiveWorker and AsyncSignals objects.
#
# WHY QTextEdit INSTEAD OF QLabel?
#   QLabel is a single-line static text widget — no scroll bar, clips overflow.
#   QTextEdit is a scrollable multi-line document widget — supports HTML,
#   auto-scrolls to bottom on append, and shows full transcript history.

RECORDING_PANEL_STYLESHEET = """
    QMainWindow {
        background-color: rgba(15, 15, 20, 235);
    }
    QWidget#panel_title_bar {
        background-color: rgba(25, 25, 35, 245);
        border-top-left-radius: 8px;
        border-top-right-radius: 8px;
    }
    QLabel#panel_grip {
        color: #444444;
        font-size: 12px;
    }
    QLabel#panel_title_text {
        color: #999999;
        font-size: 11px;
        font-weight: bold;
    }
    QLabel#rec_indicator {
        color: #FF4444;
        font-size: 12px;
        font-weight: bold;
    }
    QLabel#timer_label {
        color: #888888;
        font-size: 11px;
        font-family: 'Consolas', monospace;
    }
    QPushButton#stop_btn {
        background-color: rgba(180, 40, 40, 220);
        color: #FFFFFF;
        border: 1px solid rgba(255, 80, 80, 150);
        border-radius: 4px;
        padding: 4px 12px;
        font-size: 11px;
        font-weight: bold;
    }
    QPushButton#stop_btn:hover {
        background-color: rgba(220, 50, 50, 240);
    }
    QPushButton#panel_close {
        background-color: transparent;
        color: #666666;
        border: none;
        font-size: 13px;
        font-weight: bold;
        padding: 2px 6px;
    }
    QPushButton#panel_close:hover {
        color: #FF6B6B;
    }
    QTextEdit#panel_transcript {
        background-color: rgba(20, 20, 30, 200);
        color: #F0F0F0;
        border: none;
        border-bottom-left-radius: 8px;
        border-bottom-right-radius: 8px;
        padding: 8px 12px;
        font-size: 13px;
        font-family: 'Segoe UI', 'Consolas', monospace;
    }
    QScrollBar:vertical {
        background-color: rgba(30, 30, 40, 150);
        width: 6px;
        border-radius: 3px;
    }
    QScrollBar::handle:vertical {
        background-color: rgba(100, 100, 120, 180);
        border-radius: 3px;
        min-height: 20px;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
        height: 0px;
    }
"""


class CompactBar(QMainWindow):
    """
    Floating recording panel shown during live transcription.

    Features:
      - 420x280 dark translucent floating panel
      - Title bar with grip icon, REC indicator, timer, Stop button, close
      - Scrollable QTextEdit for live transcript (auto-scrolls on new text)
      - Ghost mode (invisible to screen capture)
      - Draggable from title bar to any position on screen

    Signals:
      - stop_requested: emitted when Stop is clicked. The main window
        listens for this to show itself and display the full transcript.
    """

    # Signal emitted when user clicks Stop on the recording panel
    stop_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.drag_position = None

        # Store transcript lines for transfer to main window on stop
        # Each entry: (source_label, text) e.g. ("[Speaker]", "hello world")
        self._transcript_lines = []
        # Maximum lines to keep in memory (older ones discarded)
        self._max_lines = 500

        # Elapsed time tracking
        self._elapsed_seconds = 0

        self._setup_window()
        self._build_ui()
        self._setup_timers()

    def _setup_window(self):
        """Configure the floating, borderless, always-on-top panel."""
        self.setWindowTitle("Recording")
        self.setFixedSize(420, 280)

        # Position at top-right of screen (out of the way but visible)
        screen = QApplication.primaryScreen().geometry()
        self.move(screen.width() - 440, 20)

        # Frameless + always on top + Tool (no taskbar entry) + translucent
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(RECORDING_PANEL_STYLESHEET)

    def _build_ui(self):
        """Build the recording panel: title bar on top, transcript area below."""
        central = QWidget()
        self.setCentralWidget(central)
        outer_layout = QVBoxLayout(central)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # =============================================
        # TITLE BAR — grip + REC + timer + Stop + close
        # =============================================
        title_bar = QWidget()
        title_bar.setObjectName("panel_title_bar")
        title_bar.setFixedHeight(36)
        tb_layout = QHBoxLayout(title_bar)
        tb_layout.setContentsMargins(10, 4, 10, 4)
        tb_layout.setSpacing(8)

        # Grip icon — drag handle indicator
        grip = QLabel("⠿")
        grip.setObjectName("panel_grip")
        tb_layout.addWidget(grip)

        # REC indicator (blinking red dot + text)
        self.rec_label = QLabel("● REC")
        self.rec_label.setObjectName("rec_indicator")
        self.rec_label.setFixedWidth(55)
        tb_layout.addWidget(self.rec_label)

        # Elapsed time display
        self.timer_label = QLabel("00:00")
        self.timer_label.setObjectName("timer_label")
        self.timer_label.setFixedWidth(45)
        tb_layout.addWidget(self.timer_label)

        tb_layout.addStretch()

        # Stop button
        self.stop_btn = QPushButton("■ Stop")
        self.stop_btn.setObjectName("stop_btn")
        self.stop_btn.setFixedWidth(70)
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        tb_layout.addWidget(self.stop_btn)

        # Close button
        close_btn = QPushButton("✕")
        close_btn.setObjectName("panel_close")
        close_btn.setFixedSize(22, 22)
        close_btn.clicked.connect(self._on_stop_clicked)
        tb_layout.addWidget(close_btn)

        outer_layout.addWidget(title_bar)

        # =============================================
        # TRANSCRIPT AREA — scrollable QTextEdit
        # =============================================
        # QTextEdit is a scrollable multi-line document widget.
        # Unlike QLabel, it supports: scroll bars, HTML formatting,
        # auto-scroll on append, and full transcript history.
        self.transcript_area = QTextEdit()
        self.transcript_area.setObjectName("panel_transcript")
        self.transcript_area.setReadOnly(True)
        self.transcript_area.setPlaceholderText("Listening for audio...")
        # Disable the text edit's own frame (we handle borders via stylesheet)
        self.transcript_area.setFrameShape(QFrame.Shape.NoFrame)
        outer_layout.addWidget(self.transcript_area)

    def _setup_timers(self):
        """
        Set up two timers:
        1. Blink timer — toggles REC indicator every 800ms
        2. Elapsed timer — updates MM:SS display every 1000ms
        """
        # --- Blink timer (REC indicator) ---
        self._blink_visible = True
        self._blink_timer = QTimer()
        self._blink_timer.setInterval(800)
        self._blink_timer.timeout.connect(self._blink_rec)

        # --- Elapsed timer (recording duration) ---
        self._elapsed_timer = QTimer()
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)

    def _blink_rec(self):
        """Toggle REC indicator between red dot and dimmed state."""
        self._blink_visible = not self._blink_visible
        if self._blink_visible:
            self.rec_label.setText("● REC")
            self.rec_label.setStyleSheet("color: #FF4444;")
        else:
            self.rec_label.setText("  REC")
            self.rec_label.setStyleSheet("color: #888888;")

    def _tick_elapsed(self):
        """Increment elapsed seconds and update timer display."""
        self._elapsed_seconds += 1
        minutes = self._elapsed_seconds // 60
        seconds = self._elapsed_seconds % 60
        self.timer_label.setText(f"{minutes:02d}:{seconds:02d}")

    # ------------------------------------------
    # PUBLIC METHODS (called by main window)
    # ------------------------------------------

    def start_recording(self):
        """Show the panel, start timers, enable ghost mode."""
        # Reset state
        self._transcript_lines.clear()
        self.transcript_area.clear()
        self._elapsed_seconds = 0
        self.timer_label.setText("00:00")

        # Start both timers
        self._blink_timer.start()
        self._elapsed_timer.start()

        # Show and enable ghost mode
        self.show()
        try:
            hwnd = int(self.winId())
            enable_ghost_mode(hwnd)
        except Exception:
            pass  # Ghost mode is optional — may fail on non-Windows

    def stop_recording(self):
        """Hide the panel and stop all timers."""
        self._blink_timer.stop()
        self._elapsed_timer.stop()
        self.hide()

    def add_transcript(self, text: str, source: str):
        """
        Append a new transcript line to the scrollable display.

        Uses QTextEdit.append() which:
        1. Adds the HTML-formatted line at the bottom
        2. Automatically scrolls to show the new line

        Args:
            text: The transcribed text
            source: "speaker" or "mic"
        """
        if not text or not text.strip():
            return

        label = "[You]" if source == "mic" else "[Speaker]"
        self._transcript_lines.append((label, text.strip()))

        # Trim stored lines (memory safety)
        if len(self._transcript_lines) > self._max_lines:
            self._transcript_lines = self._transcript_lines[-self._max_lines:]

        # Color code by source: blue for You, light gray for Speaker
        if source == "mic":
            color = "#7EC8E3"
        else:
            color = "#C0C0C0"

        # Append HTML-formatted line to the QTextEdit
        # QTextEdit.append() adds to the bottom and auto-scrolls
        self.transcript_area.append(
            f'<span style="color: {color}; font-weight: bold;">{label}</span> '
            f'<span style="color: #F0F0F0;">{text.strip()}</span>'
        )

    def get_all_transcript_lines(self):
        """
        Return all stored transcript lines for the main window to display.

        Returns:
            List of (label, text) tuples
        """
        return list(self._transcript_lines)

    # ------------------------------------------
    # INTERNAL
    # ------------------------------------------

    def _on_stop_clicked(self):
        """Handle Stop button click — emit signal for main window."""
        self.stop_recording()
        self.stop_requested.emit()

    # ------------------------------------------
    # DRAGGABLE (free movement from title bar area)
    # ------------------------------------------

    def mousePressEvent(self, event):
        """Record click position for drag."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        """Move panel to follow mouse during drag."""
        if self.drag_position and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_position)

    def mouseReleaseEvent(self, event):
        """Clear drag state on release."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = None


# ============================================
# ASYNC WORKER — Runs async code from PyQt6
# ============================================
# PyQt6 runs its own event loop (for UI).
# asyncio runs its own event loop (for network).
# They can't share a loop — so we run asyncio in
# a separate thread and communicate via Qt signals.

class AsyncSignals(QObject):
    """Signals to communicate between async thread and UI thread."""
    transcript_received = pyqtSignal(str, float, str, bool, str)  # text, confidence, model, fallback, source
    bulk_complete = pyqtSignal(str)       # full transcript text
    connection_status = pyqtSignal(bool)  # connected True/False
    server_status = pyqtSignal(str)       # "booting", "alive", "offline"
    error = pyqtSignal(str)              # error message
    download_progress = pyqtSignal(float, str)  # percent, status

    # --- Phase 5: Production Hardening signals ---
    # connection_event: carries status updates from transmitter reconnection
    #   and health monitor. Two arguments:
    #     status (str): "reconnecting", "reconnected", "failed",
    #                   "health_lost", "health_restored"
    #     message (str): human-readable description for the UI
    connection_event = pyqtSignal(str, str)


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

        # Initialize connection manager before building UI — _build_ui()
        # checks self.connection.is_hpc_mode() to configure button labels
        self.connection = ConnectionManager()

        self._setup_window()
        self._build_ui()
        self._connect_signals()

        # Initialize the async workers that connect audio → network → UI
        # LiveWorker receives connection_manager for health monitoring
        self.live_worker = LiveWorker(self.signals, connection_manager=self.connection)
        self.bulk_worker = BulkWorker(self.signals)

        # --- Compact Recording Bar ---
        # Separate window shown during live recording. The main window hides
        # and the compact bar takes over to give a minimal, unobtrusive view.
        # When Stop is clicked on the compact bar, the main window reappears
        # with the full transcript ready for copy/save.
        self.compact_bar = CompactBar()
        self.compact_bar.stop_requested.connect(self._on_compact_stop)

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

        # --- CUSTOM TITLE BAR ---
        # A distinct strip at the top with grip icon, title, and close button.
        # Visually separates the "grab here" area from the content below.
        # QWidget container gives it its own background color via stylesheet.
        title_bar = QWidget()
        title_bar.setObjectName("title_bar")
        title_bar.setFixedHeight(32)
        title_bar_layout = QHBoxLayout(title_bar)
        title_bar_layout.setContentsMargins(10, 4, 10, 4)
        title_bar_layout.setSpacing(8)

        # Grip icon — universal drag indicator (six-dot braille pattern)
        grip = QLabel("⠿")
        grip.setObjectName("grip")
        title_bar_layout.addWidget(grip)

        # Title text
        title_text = QLabel("Transcription")
        title_text.setObjectName("title_bar_text")
        title_bar_layout.addWidget(title_text)

        title_bar_layout.addStretch()

        # Ghost toggle button
        self.ghost_btn = QPushButton("Ghost: OFF")
        self.ghost_btn.setFixedWidth(100)
        self.ghost_btn.clicked.connect(self._toggle_ghost)
        title_bar_layout.addWidget(self.ghost_btn)

        # Close button
        close_btn = QPushButton("✕")
        close_btn.setObjectName("title_close")
        close_btn.setFixedSize(24, 24)
        close_btn.clicked.connect(self.close)
        title_bar_layout.addWidget(close_btn)

        main_layout.addWidget(title_bar)

        # --- SEPARATOR ---
        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFrameShape(QFrame.Shape.HLine)
        main_layout.addWidget(sep)

        # --- SERVER STATUS BAR ---
        server_bar = QHBoxLayout()

        # Initial label depends on server mode (HPC vs DigitalOcean)
        if self.connection.is_hpc_mode():
            self.server_status_label = QLabel("Server: HPC Mode")
        else:
            self.server_status_label = QLabel("Server: Offline")
        self.server_status_label.setObjectName("status")
        server_bar.addWidget(self.server_status_label)

        server_bar.addStretch()

        # Button label depends on mode:
        #   HPC: "Check Connection" (only checks if tunnel + server are reachable)
        #   DigitalOcean: "Start Server" (controls droplet power)
        initial_btn_label = self.connection.get_button_label(is_available=False)
        self.server_btn = QPushButton(initial_btn_label)
        self.server_btn.setObjectName("server_off")
        self.server_btn.setFixedWidth(140)
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
        """Build the Live Mode view — window picker + mic toggle + transcript + export."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 8, 0, 0)

        # =============================================
        # WINDOW PICKER SECTION
        # =============================================
        # lets the user choose which app to capture audio from.
        # "All System Audio" = original mode (no PID filter).
        # any other item = per-app mode (captures only that app's audio).

        window_label = QLabel("AUDIO SOURCE")
        window_label.setObjectName("window_section_label")
        layout.addWidget(window_label)

        # --- Dropdown + Refresh button row ---
        picker_row = QHBoxLayout()

        self.window_combo = QComboBox()
        self.window_combo.setMinimumHeight(32)
        # first item is always "All System Audio" — the original mode
        self.window_combo.addItem("All System Audio", None)
        picker_row.addWidget(self.window_combo, stretch=1)

        # refresh button — re-scans open windows
        # useful when the user opens a new app after launching the overlay
        refresh_btn = QPushButton("↻")
        refresh_btn.setObjectName("refresh_btn")
        refresh_btn.setFixedSize(32, 32)
        refresh_btn.setToolTip("Refresh window list")
        refresh_btn.clicked.connect(self._refresh_window_list)
        picker_row.addWidget(refresh_btn)

        layout.addLayout(picker_row)

        # --- Mic toggle checkbox ---
        # checked = mic on (meetings, conversations)
        # unchecked = mic off (solo lectures, playback only)
        self.mic_checkbox = QCheckBox("Include microphone")
        self.mic_checkbox.setChecked(True)
        self.mic_checkbox.setToolTip(
            "Turn off for solo lectures — only captures app audio, not your voice"
        )
        layout.addWidget(self.mic_checkbox)

        # thin separator line between picker section and transcript
        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)

        # populate the dropdown with current windows
        self._refresh_window_list()

        # =============================================
        # TRANSCRIPT AREA (unchanged from before)
        # =============================================

        # Live transcript display
        self.live_text = QTextEdit()
        self.live_text.setReadOnly(True)
        self.live_text.setPlaceholderText(
            "Live transcript will appear here...\n\n"
            "1. Start the server\n"
            "2. Pick a window above (or leave on 'All System Audio')\n"
            "3. Click 'Start Listening' below\n"
            "4. Audio from the selected source will be transcribed"
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

        # --- Export buttons for live transcript ---
        # Same functionality as bulk mode export. Visible after recording stops
        # so the user can copy/save what was transcribed during the session.
        live_export_row = QHBoxLayout()

        live_copy_btn = QPushButton("Copy to Clipboard")
        live_copy_btn.clicked.connect(self._copy_live_to_clipboard)
        live_export_row.addWidget(live_copy_btn)

        live_save_txt_btn = QPushButton("Save .txt")
        live_save_txt_btn.clicked.connect(lambda: self._save_live_transcript("txt"))
        live_export_row.addWidget(live_save_txt_btn)

        live_save_srt_btn = QPushButton("Save .srt")
        live_save_srt_btn.clicked.connect(lambda: self._save_live_transcript("srt"))
        live_export_row.addWidget(live_save_srt_btn)

        layout.addLayout(live_export_row)

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
        self.signals.connection_event.connect(self._on_connection_event)

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
        Handle server button click. Behavior depends on SERVER_MODE:
          HPC mode: Only checks if server is reachable (you manage sbatch/tunnel manually)
          DigitalOcean mode: Starts/stops the droplet via API
        """
        if self.connection.is_hpc_mode():
            # ==========================================
            # HPC MODE — Check connection only
            # ==========================================
            # Button always says "Check Connection"
            # Clicking it pings /health to see if tunnel + server are alive
            self.server_btn.setText("Checking...")
            self.server_btn.setEnabled(False)
            self.server_status_label.setText("Server: Checking...")

            def _check():
                loop = asyncio.new_event_loop()
                is_available = loop.run_until_complete(
                    self.connection.is_server_available()
                )
                loop.close()
                message = self.connection.get_status_message(is_available)
                if is_available:
                    self.signals.server_status.emit("online")
                else:
                    self.signals.server_status.emit("hpc_offline")

            threading.Thread(target=_check, daemon=True).start()

        else:
            # ==========================================
            # DIGITALOCEAN MODE — Start/stop droplet
            # ==========================================
            current_text = self.server_btn.text()

            if current_text == "Start Server":
                self.server_btn.setText("Booting...")
                self.server_btn.setEnabled(False)
                self.server_status_label.setText("Server: Booting...")

                # Fire Digital Ocean API request in background thread
                def _boot():
                    loop = asyncio.new_event_loop()
                    result = loop.run_until_complete(
                        self.connection.start_server()
                    )
                    loop.close()
                    if result["success"]:
                        # Start heartbeat to protect credits
                        if self.connection.cloud_controller:
                            server_ip = os.getenv("SERVER_IP", "localhost")
                            server_port = os.getenv("SERVER_PORT", "8000")
                            self.connection.cloud_controller.start_heartbeat(
                                server_ip, server_port
                            )
                        self.signals.server_status.emit("booting")
                    else:
                        self.signals.error.emit(result["message"])
                        self.signals.server_status.emit("offline")

                threading.Thread(target=_boot, daemon=True).start()

                # Poll server health until it's ready
                self._poll_server_ready()
            else:
                # Stop heartbeat first, then power off
                if self.connection.cloud_controller:
                    self.connection.cloud_controller.stop_heartbeat()

                def _shutdown():
                    loop = asyncio.new_event_loop()
                    loop.run_until_complete(self.connection.stop_server())
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
    # WINDOW PICKER
    # ------------------------------------------

    def _refresh_window_list(self):
        """
        Re-scan all open windows and repopulate the dropdown.

        Called once when the live panel is built, and again each time the
        user clicks the refresh button. Keeps the first item as
        "All System Audio" (no PID filter) and adds every real app window
        after it.

        Each combo item stores a WindowInfo object as its userData.
        "All System Audio" stores None — DualCapturer treats None as
        system-wide mode.
        """
        # remember what was selected so we can re-select it if still open
        prev_data = self.window_combo.currentData()

        self.window_combo.clear()
        self.window_combo.addItem("All System Audio", None)

        try:
            windows = list_windows()
            for w in windows:
                self.window_combo.addItem(w.display_name(), w)
        except Exception as e:
            print(f"[UI] Failed to list windows: {e}")

        # try to re-select the previously selected window
        if prev_data is not None:
            for i in range(self.window_combo.count()):
                item_data = self.window_combo.itemData(i)
                if item_data and item_data.pid == prev_data.pid:
                    self.window_combo.setCurrentIndex(i)
                    break

    # ------------------------------------------
    # LIVE MODE CONTROLS
    # ------------------------------------------

    def _toggle_listening(self):
        """
        Start or stop live audio capture and streaming.

        Start flow:
          1. Read the selected window from the dropdown
          2. Read the mic checkbox state
          3. Recreate LiveWorker with those settings
          4. Hide main window, show compact bar
          5. Start the pipeline

        Stop: called via _on_compact_stop when compact bar's Stop is clicked.
        Can also be called directly if user clicks "Stop Listening" in main window.
        """
        if not self.is_live:
            # --- START RECORDING ---
            self.is_live = True
            self.listen_btn.setText("Stop Listening")
            self.live_text.clear()
            self.model_label.setText("Connecting...")

            # --- Read user selections from the UI ---
            # selected_window is either None (All System Audio) or a WindowInfo object
            selected_window = self.window_combo.currentData()
            target_pid = selected_window.pid if selected_window else None
            enable_mic = self.mic_checkbox.isChecked()

            # store the selected window info — Phase 7C FrameGrabber needs the HWND
            self._selected_window = selected_window

            # --- Recreate LiveWorker with the selected settings ---
            # creating a fresh worker each time so the DualCapturer inside
            # gets the correct target_pid and enable_mic for this session.
            # reusing an old worker would keep the old PID from a previous session.
            self.live_worker = LiveWorker(
                self.signals,
                connection_manager=self.connection,
                target_pid=target_pid,
                enable_mic=enable_mic
            )

            # Hide main window, show compact bar
            self.hide()
            self.compact_bar.start_recording()

            # Start the live pipeline: DualCapturer → WSS → Server → Text back
            self.live_worker.start()
        else:
            # --- STOP RECORDING ---
            self._stop_live()

    def _stop_live(self):
        """
        Stop recording and return to full window.
        Called by both _toggle_listening (Stop button in main window)
        and _on_compact_stop (Stop button in compact bar).
        """
        self.is_live = False
        self.listen_btn.setText("Start Listening")
        self.model_label.setText("Stopped")

        # Stop capture and disconnect WSS
        self.live_worker.stop()

        # Hide compact bar (may already be hidden if called from compact bar)
        self.compact_bar.stop_recording()

        # Transfer transcript from compact bar to main window's live_text
        # so the user can see the full transcript and copy/save it
        lines = self.compact_bar.get_all_transcript_lines()
        self.live_text.clear()
        for label, text in lines:
            if label == "[You]":
                self.live_text.append(
                    f'<span style="color: #7EC8E3;">{label}</span> {text}'
                )
            elif label == "[Speaker]":
                self.live_text.append(
                    f'<span style="color: #C0C0C0;">{label}</span> {text}'
                )
            else:
                self.live_text.append(text)

        # Show main window
        self.show()

    def _on_compact_stop(self):
        """
        Called when the compact bar's Stop button is clicked.
        Bridges the compact bar's stop_requested signal to _stop_live().
        """
        self._stop_live()

    def _on_transcript_received(self, text: str, confidence: float,
                                 model: str, fallback: bool, source: str):
        """
        Called when live transcript text arrives from server.

        Feeds transcript to BOTH:
          1. Compact bar (visible during recording — shows latest lines)
          2. Main window's live_text (hidden during recording — accumulates
             full transcript for copy/save when recording stops)

        Args:
            text: The transcribed text (already de-duplicated by worker)
            confidence: Model confidence score (0.0 to 1.0)
            model: Name of the model that produced this transcript
            fallback: True if Whisper was used instead of Canary
            source: "speaker" (system audio) or "mic" (your voice)
        """
        # Feed to compact bar (updates the visible ticker during recording)
        self.compact_bar.add_transcript(text, source)

        # Also feed to main window's text area (hidden, accumulating)
        if source == "mic":
            label = "You"
            self.live_text.append(f'<span style="color: #7EC8E3;">[{label}]</span> {text}')
        elif source == "speaker":
            label = "Speaker"
            self.live_text.append(f'<span style="color: #C0C0C0;">[{label}]</span> {text}')
        else:
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

    # ------------------------------------------
    # LIVE EXPORT SUITE
    # ------------------------------------------
    # These methods mirror the bulk export methods but operate on
    # the live_text QTextEdit (which accumulates transcript during
    # recording and is populated from compact bar on stop).

    def _copy_live_to_clipboard(self):
        """Copy live transcript to system clipboard."""
        text = self.live_text.toPlainText()
        if not text:
            self._on_error("No transcript to copy")
            return
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        self.model_label.setText("Copied to clipboard!")

    def _save_live_transcript(self, format_type: str):
        """Save live transcript as .txt or .srt file."""
        text = self.live_text.toPlainText()
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
            self, "Save Live Transcript", f"live_transcript{default_ext}", filter_str
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

            self.model_label.setText(f"Saved to {os.path.basename(filepath)}")
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
        """Display error message in the appropriate status label."""
        print(f"[ERROR] {message}")

        # Show error on whichever panel is currently active
        if self.stack.currentIndex() == 0:
            # Live mode — show in model_label (below transcript area)
            self.model_label.setText(f"Error: {message}")
            self.model_label.setStyleSheet("color: #FF6B6B;")
            QTimer.singleShot(5000, lambda: self.model_label.setStyleSheet(""))
        else:
            # Bulk mode — show in bulk_status
            self.bulk_status.setText(f"Error: {message}")
            self.bulk_status.setStyleSheet("color: #FF6B6B;")
            QTimer.singleShot(3000, lambda: self.bulk_status.setStyleSheet(""))

    def _on_server_status(self, status: str):
        """Update server status display and button state."""
        if status == "online":
            if self.connection.is_hpc_mode():
                # HPC mode: server is reachable through tunnel
                self.server_status_label.setText("Server: HPC Connected")
                self.server_btn.setText("Check Connection")
            else:
                # DigitalOcean mode: droplet is running
                self.server_status_label.setText("Server: Online")
                self.server_btn.setText("Stop Server")
            self.server_btn.setObjectName("server_on")
            self.server_btn.setStyle(self.server_btn.style())
            self.server_btn.setEnabled(True)
        elif status == "hpc_offline":
            # HPC mode only: tunnel or server not reachable
            self.server_status_label.setText("Server: Not Reachable")
            self.server_btn.setText("Check Connection")
            self.server_btn.setObjectName("server_off")
            self.server_btn.setStyle(self.server_btn.style())
            self.server_btn.setEnabled(True)
        elif status == "offline":
            self.server_status_label.setText("Server: Offline")
            self.server_btn.setText("Start Server")
            self.server_btn.setObjectName("server_off")
            self.server_btn.setStyle(self.server_btn.style())
            self.server_btn.setEnabled(True)
        elif status == "booting":
            self.server_btn.setText("Booting...")
            self.server_btn.setEnabled(False)

    # ------------------------------------------
    # CONNECTION EVENT HANDLER (Phase 5)
    # ------------------------------------------
    # Receives all connection-related status updates from:
    #   - LiveTransmitter (reconnection attempts)
    #   - BulkTransmitter (upload retries)
    #   - ConnectionManager health monitor (periodic checks)
    #
    # Updates the server status label and model label to show
    # what's happening without requiring user action.

    def _on_connection_event(self, status: str, message: str):
        """
        Handle connection state changes from transmitters and health monitor.

        Args:
            status: one of:
                "reconnecting"   — WebSocket is trying to reconnect
                "reconnected"    — WebSocket connection restored
                "failed"         — reconnection/retry gave up
                "retrying"       — BulkTransmitter retrying upload
                "recovered"      — BulkTransmitter retry succeeded
                "health_lost"    — health monitor detected server is unreachable
                "health_restored"— health monitor detected server is back
            message: human-readable description
        """
        if status == "reconnecting":
            # WebSocket is trying to reconnect — show amber/warning state
            self.server_status_label.setText(f"Server: Reconnecting...")
            self.server_status_label.setStyleSheet("color: #FFB347;")  # amber
            self.model_label.setText(message)

        elif status == "reconnected":
            # Connection restored — show green/healthy state
            if self.connection.is_hpc_mode():
                self.server_status_label.setText("Server: HPC Connected")
            else:
                self.server_status_label.setText("Server: Online")
            self.server_status_label.setStyleSheet("color: #77DD77;")  # green
            self.model_label.setText("Connection restored")
            # Reset color after 3 seconds
            QTimer.singleShot(3000, lambda: self.server_status_label.setStyleSheet(""))

        elif status == "failed":
            # Reconnection or retry completely failed — show red/error state
            self.server_status_label.setText("Server: Connection Lost")
            self.server_status_label.setStyleSheet("color: #FF6B6B;")  # red
            self.model_label.setText(message)

        elif status == "retrying":
            # BulkTransmitter is retrying an upload
            self.bulk_status.setText(message)
            self.bulk_status.setStyleSheet("color: #FFB347;")  # amber

        elif status == "recovered":
            # BulkTransmitter retry succeeded
            self.bulk_status.setText(message)
            self.bulk_status.setStyleSheet("color: #77DD77;")  # green
            QTimer.singleShot(3000, lambda: self.bulk_status.setStyleSheet(""))

        elif status == "health_lost":
            # Health monitor detected server went down
            self.server_status_label.setText("Server: Connection Lost")
            self.server_status_label.setStyleSheet("color: #FF6B6B;")  # red
            self.server_btn.setObjectName("server_off")
            self.server_btn.setStyle(self.server_btn.style())

        elif status == "health_restored":
            # Health monitor detected server came back
            if self.connection.is_hpc_mode():
                self.server_status_label.setText("Server: HPC Connected")
            else:
                self.server_status_label.setText("Server: Online")
            self.server_status_label.setStyleSheet("color: #77DD77;")  # green
            self.server_btn.setObjectName("server_on")
            self.server_btn.setStyle(self.server_btn.style())
            QTimer.singleShot(3000, lambda: self.server_status_label.setStyleSheet(""))

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
        # Stop live capture if running (also stops health monitor)
        if self.is_live:
            self.live_worker.stop()
            self.is_live = False

        # Close compact bar if it's open
        self.compact_bar.stop_recording()
        self.compact_bar.close()

        # Stop health monitor explicitly in case live mode was never started
        self.connection.stop_health_monitor()

        # Stop any bulk operation
        self.bulk_worker.stop()

        # Only shut down cloud server in DigitalOcean mode
        # In HPC mode, the server is managed manually (scancel)
        if self.connection.is_cloud_mode() and self.connection.cloud_controller:
            # Stop heartbeat
            self.connection.cloud_controller.stop_heartbeat()

            # Shut down the cloud server to save credits
            def _shutdown():
                loop = asyncio.new_event_loop()
                loop.run_until_complete(self.connection.stop_server())
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
