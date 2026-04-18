# client/audio/window_selector.py — Window Enumerator for Per-App Capture
# Phase 7A: Lists all visible windows so the user can pick a target app.
#
# This module does TWO jobs:
#   1. Walk through every window on screen → collect title, PID, HWND
#   2. Filter out invisible/system windows → return only real app windows
#
# RUNS ON: Your Windows laptop (client-side)
# REQUIRES: pywin32 (pip install pywin32)
#
# USED BY:
#   - overlay.py → populates the window picker dropdown
#   - DualCapturer → receives the PID to filter audio capture
#   - FrameGrabber (Phase 7C) → receives the HWND to screenshot

import ctypes
import ctypes.wintypes


# ============================================
# WINDOWS API CONSTANTS
# ============================================
# These are values defined by Microsoft in their Win32 API documentation.
# Each one is a flag that tells a Windows function what to check or do.

# GWL_EXSTYLE: index for GetWindowLong — retrieves the window's "extended style" flags
# Extended styles control things like: is it a tool window? is it a topmost window?
GWL_EXSTYLE = -20

# WS_EX_TOOLWINDOW: a window style flag. If this flag is set on a window,
# it means "this is a floating toolbar or helper window — don't show it in
# the Alt+Tab list or the taskbar." We use this to filter out these windows
# because they are not real app windows the user would want to capture.
WS_EX_TOOLWINDOW = 0x00000080

# WS_EX_APPWINDOW: a window style flag. If this flag is set, it means
# "this IS a real application window — always show it in Alt+Tab."
# Some windows have both flags; APPWINDOW overrides TOOLWINDOW.
WS_EX_APPWINDOW = 0x00040000

# SW_SHOW states — we check if a window is visible (not minimized/hidden)
# IsWindowVisible returns True if the window is currently shown on screen.
# We also check IsIconic which returns True if the window is minimized.
# Minimized windows are still valid targets — they still produce audio —
# so we include them but mark them as "(minimized)" in the dropdown.


# ============================================
# WINDOW INFO DATA CLASS
# ============================================
# Holds everything we need about one window.
# Using a simple class instead of a dict so the fields are explicit
# and code that uses it gets autocomplete in editors.

class WindowInfo:
    """
    Holds the information about one open window.

    Attributes:
        hwnd: Windows handle (integer) — identifies this specific window
        pid: Process ID (integer) — identifies the program that owns this window
        title: Window title (string) — what the user sees in the title bar
        is_minimized: True if the window is currently minimized to taskbar
    """

    def __init__(self, hwnd: int, pid: int, title: str, is_minimized: bool = False):
        self.hwnd = hwnd
        self.pid = pid
        self.title = title
        self.is_minimized = is_minimized

    def display_name(self) -> str:
        """
        Format the window info for display in a dropdown.
        Shows title with a "(minimized)" note if applicable.

        Returns:
            String like "Telegram (12345)" or "Chrome (8888) (minimized)"
        """
        name = f"{self.title} (PID: {self.pid})"
        if self.is_minimized:
            name += " (minimized)"
        return name

    def __repr__(self):
        return f"WindowInfo(hwnd={self.hwnd}, pid={self.pid}, title='{self.title}')"


# ============================================
# CORE FUNCTIONS
# ============================================

# Loading Windows DLL functions once at module level.
# user32.dll is the Windows library that handles windows, messages, and input.
_user32 = ctypes.windll.user32


def _get_window_thread_process_id(hwnd: int) -> int:
    """
    Get the process ID (PID) of the program that owns a given window.

    Windows API: GetWindowThreadProcessId
    Takes a window handle (HWND), writes the PID into a variable we provide,
    and returns the thread ID (which we don't need).

    Args:
        hwnd: Window handle

    Returns:
        Process ID (integer)
    """
    pid = ctypes.wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _get_window_title(hwnd: int) -> str:
    """
    Get the title bar text of a window.

    Windows API: GetWindowTextW (W = wide/Unicode version)
    Copies the title into a buffer we provide.

    Args:
        hwnd: Window handle

    Returns:
        Title string, or empty string if window has no title
    """
    length = _user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return ""

    # Create a buffer big enough to hold the title + null terminator
    buffer = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _is_real_app_window(hwnd: int) -> bool:
    """
    Determine if a window is a real application window that the user
    would recognize and want to capture.

    Filtering logic:
      1. Must be visible (IsWindowVisible = True)
      2. Must have a non-empty title
      3. Must NOT be a tool window (floating toolbar/helper) UNLESS
         it also has the APPWINDOW flag (which overrides TOOLWINDOW)
      4. Must have a non-zero PID

    This matches what Windows shows in the Alt+Tab switcher — which is
    exactly the list the user would expect to see.

    Args:
        hwnd: Window handle

    Returns:
        True if this is a real app window worth showing to the user
    """
    # Check 1: is it visible at all?
    if not _user32.IsWindowVisible(hwnd):
        return False

    # Check 2: does it have a title?
    title = _get_window_title(hwnd)
    if not title or not title.strip():
        return False

    # Check 3: is it a tool window (like a tooltip or floating toolbar)?
    ex_style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)

    is_tool_window = bool(ex_style & WS_EX_TOOLWINDOW)
    is_app_window = bool(ex_style & WS_EX_APPWINDOW)

    # APPWINDOW flag overrides TOOLWINDOW — if both are set, it IS a real window
    if is_tool_window and not is_app_window:
        return False

    # Check 4: does it have a valid process?
    pid = _get_window_thread_process_id(hwnd)
    if pid == 0:
        return False

    return True


def list_windows() -> list:
    """
    Get a list of all real application windows currently open.

    How it works:
      Step 1 → Call EnumWindows — Windows walks through every window on
               the desktop and calls our callback function for each one
      Step 2 → For each window, check if it's a real app window
      Step 3 → If yes, collect its HWND, PID, title, and minimized state
      Step 4 → Return the full list sorted alphabetically by title

    Returns:
        List of WindowInfo objects, sorted by title (case-insensitive)
    """
    windows = []

    # EnumWindows needs a callback function. Windows calls this function
    # once for each window on the desktop. The callback signature is:
    #   BOOL callback(HWND hwnd, LPARAM lParam)
    # returning True means "keep going", False means "stop enumerating"
    #
    # WNDENUMPROC is the ctypes type for this callback signature.
    # ctypes needs to know the exact parameter and return types so it
    # can convert between Python functions and C function pointers.
    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_bool,              # return type: BOOL
        ctypes.wintypes.HWND,       # param 1: HWND (window handle)
        ctypes.wintypes.LPARAM      # param 2: LPARAM (user data, unused)
    )

    def _enum_callback(hwnd, lparam):
        """Called by Windows for each window on the desktop."""
        if _is_real_app_window(hwnd):
            pid = _get_window_thread_process_id(hwnd)
            title = _get_window_title(hwnd)
            is_minimized = bool(_user32.IsIconic(hwnd))

            windows.append(WindowInfo(
                hwnd=hwnd,
                pid=pid,
                title=title,
                is_minimized=is_minimized
            ))
        return True  # keep enumerating

    # Wrap the Python function as a C callback and call EnumWindows
    callback = WNDENUMPROC(_enum_callback)
    _user32.EnumWindows(callback, 0)

    # Sort alphabetically by title for a clean dropdown
    windows.sort(key=lambda w: w.title.lower())

    print(f"[WINDOW] Found {len(windows)} application windows")
    return windows


def get_window_by_pid(pid: int) -> WindowInfo:
    """
    Find a specific window by its process ID.

    If a process has multiple windows, returns the first one found.
    Useful when you already know the PID and need the HWND for
    frame capture.

    Args:
        pid: Process ID to search for

    Returns:
        WindowInfo for that process, or None if not found
    """
    all_windows = list_windows()
    for w in all_windows:
        if w.pid == pid:
            return w
    return None


def refresh_window_title(hwnd: int) -> str:
    """
    Get the current title of a window by its handle.

    Window titles can change (e.g., Chrome updates the tab title).
    This lets the UI refresh the displayed name without re-enumerating
    all windows.

    Args:
        hwnd: Window handle

    Returns:
        Current title string, or empty string if window no longer exists
    """
    if not _user32.IsWindow(hwnd):
        return ""
    return _get_window_title(hwnd)


def is_window_alive(hwnd: int) -> bool:
    """
    Check if a window still exists (hasn't been closed).

    Used during capture to detect when the target app is closed.
    If the window is gone, we should stop capture and notify the user.

    Args:
        hwnd: Window handle to check

    Returns:
        True if window still exists, False if it was closed
    """
    return bool(_user32.IsWindow(hwnd))
