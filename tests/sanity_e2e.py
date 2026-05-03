# tests/sanity_e2e.py — single-command end-to-end sanity check
#
# WHAT this script does:
#   one command, runs every check that does not need eyes (env, server,
#   websocket, dependencies, module imports, dedup smoke), then launches
#   the live app once for a short user-driven session, tails its stdout,
#   and prints a single grid of every feature with PASS / FAIL / EYES /
#   SKIP. replaces the 25-row checklist from Session 25 with one report.
#
# WHY this script exists:
#   running 25 tests by hand and tracking which passed was slow and easy
#   to lose track of. one consolidated script means one command, one
#   summary, no spreadsheet. catches drift between code and config (like
#   the post-scrub .env mismatch that bit us in Session 25) on every run.
#
# HOW to run:
#   from project root, with the SSH tunnel already up and venv active:
#       python tests/sanity_e2e.py
#   wait a few seconds for headless checks, then the app launches.
#   use the app for ~30 sec (Start, play audio, toggle ghost, Stop),
#   close the window when done. final grid prints automatically.
#
#   to skip the GUI half (useful in CI or when the server is down):
#       python tests/sanity_e2e.py --no-live
#
# WINDOWS:
#   audio device discovery is Windows-only. on Linux those checks are
#   marked SKIP with a clear note. everything else runs cross-platform.

import os
import sys
import json
import time
import shutil
import subprocess
import importlib
from pathlib import Path


# ===========================================================
# IMPORT SHIM — make client/ importable from tests/
# ===========================================================
HERE         = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
CLIENT_DIR   = PROJECT_ROOT / "client"

# adding client/ first so "from ui.workers import ..." resolves
# same trick the existing tests use — workers.py uses sibling imports
if str(CLIENT_DIR) not in sys.path:
    sys.path.insert(0, str(CLIENT_DIR))

# loading .env now — every probe below reads vars off os.getenv
# wrapped in try/except because dotenv being missing should not
# crash the script before we even start checking
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass


# ===========================================================
# RESULT MODEL — every check appends one row to RESULTS
# ===========================================================
PASS = "PASS"
FAIL = "FAIL"
EYES = "EYES"   # needs human eyes — not auto-checkable
SKIP = "SKIP"   # not applicable on this OS / explicitly skipped

RESULTS = []   # list of (section, name, status, note)

# ANSI colors so the live output is easier to scan
# Git Bash supports these out of the box on Windows
COLORS = {
    PASS: "\033[92m",   # green
    FAIL: "\033[91m",   # red
    EYES: "\033[93m",   # yellow
    SKIP: "\033[90m",   # grey
}
RESET = "\033[0m"


def record(section: str, name: str, status: str, note: str = "") -> None:
    """append one row to the grid and echo it live so progress is visible"""
    RESULTS.append((section, name, status, note))
    color = COLORS.get(status, "")
    suffix = f"  ({note})" if note else ""
    print(f"  {color}{status:5}{RESET}  {name}{suffix}")


# ===========================================================
# SECTION 1 — PRE-FLIGHT
#   .env present, vars set, /health reachable, websocket handshake ok
# ===========================================================
def section_preflight() -> None:
    print("\n[1/5] PRE-FLIGHT")

    # confirming .env is on disk before checking individual vars
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        record("preflight", ".env file exists", PASS, str(env_path))
    else:
        record("preflight", ".env file exists", FAIL, "create one from .env.example")
        return  # no point checking vars if the file is missing

    # every var the app actually reads — keeps the grid honest about config
    required = ["HPC_USER", "HPC_LOGIN_NODE", "SERVER_IP", "SERVER_PORT"]
    for k in required:
        v = os.getenv(k)
        if v:
            # masking sensitive values — only first 3 chars shown
            shown = v[:3] + "***" if len(v) > 3 else "***"
            record("preflight", f"env var {k} set", PASS, shown)
        else:
            record("preflight", f"env var {k} set", FAIL, "missing or empty")

    # /health probe — proves the tunnel is open AND the server is up
    server_ip   = os.getenv("SERVER_IP", "localhost")
    server_port = os.getenv("SERVER_PORT", "8000")
    health_url  = f"http://{server_ip}:{server_port}/health"
    try:
        import requests
        r = requests.get(health_url, timeout=3)
        if r.status_code == 200:
            body = r.text.strip()[:60]
            record("preflight", f"GET {health_url} -> 200", PASS, body)
        else:
            record("preflight", f"GET {health_url}", FAIL, f"HTTP {r.status_code}")
    except Exception as e:
        record("preflight", f"GET {health_url}", FAIL, f"{type(e).__name__}: {e}")

    # websocket handshake probe — connect and immediately close
    # this confirms /ws/transcribe accepts upgrades, no audio sent
    ws_url = f"ws://{server_ip}:{server_port}/ws/transcribe"
    try:
        import asyncio
        import websockets

        async def probe() -> bool:
            # ping_interval=None so we never send a keepalive in this short probe
            async with websockets.connect(ws_url, ping_interval=None, close_timeout=2):
                return True

        ok = asyncio.run(asyncio.wait_for(probe(), timeout=5))
        record("preflight", f"WSS handshake {ws_url}", PASS if ok else FAIL)
    except Exception as e:
        record("preflight", f"WSS handshake {ws_url}", FAIL, f"{type(e).__name__}: {e}")


# ===========================================================
# SECTION 2 — DEPENDENCIES
#   ffmpeg, yt-dlp, audio devices (Windows-only)
# ===========================================================
def section_dependencies() -> None:
    print("\n[2/5] DEPENDENCIES")

    # ffmpeg is needed by yt-dlp for audio extraction in Bulk mode
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        record("deps", "ffmpeg on PATH", PASS, ffmpeg_path)
    else:
        record("deps", "ffmpeg on PATH", FAIL, "needed for yt-dlp audio extraction")

    # yt-dlp as a Python module — the app imports it directly, not the CLI
    # yt_dlp.version is a module (not a dict), so version is read off the
    # module attribute __version__ via getattr to stay safe across versions
    try:
        import yt_dlp  # noqa: F401
        try:
            from yt_dlp.version import __version__ as ytdlp_version
        except Exception:
            ytdlp_version = "unknown"
        record("deps", "yt-dlp module importable", PASS, ytdlp_version)
    except Exception as e:
        record("deps", "yt-dlp module importable", FAIL, f"{type(e).__name__}: {e}")

    # audio device probes only make sense on Windows — pyaudiowpatch is
    # WASAPI-bound and does not exist on Linux/macOS
    if sys.platform != "win32":
        record("deps", "loopback device found",  SKIP, f"not Windows ({sys.platform})")
        record("deps", "microphone device found", SKIP, f"not Windows ({sys.platform})")
        return

    # loopback = system audio output captured as input
    # this is the path that records whatever Windows is playing
    try:
        import pyaudiowpatch as pyaudio
        p = pyaudio.PyAudio()
        try:
            host = p.get_default_wasapi_loopback()
            name = host.get("name", "")[:50]
            record("deps", "loopback device found", PASS, name)
        finally:
            p.terminate()
    except Exception as e:
        record("deps", "loopback device found", FAIL, f"{type(e).__name__}: {e}")

    # default microphone — the [You] source in the live panel
    try:
        import pyaudiowpatch as pyaudio
        p = pyaudio.PyAudio()
        try:
            mic = p.get_default_input_device_info()
            name = mic.get("name", "")[:50]
            record("deps", "microphone device found", PASS, name)
        finally:
            p.terminate()
    except Exception as e:
        record("deps", "microphone device found", FAIL, f"{type(e).__name__}: {e}")


# ===========================================================
# SECTION 3 — MODULE IMPORTS
#   every project module must import cleanly — catches typos, missing
#   dependencies, broken refactors before the app even launches
# ===========================================================
PROJECT_MODULES = [
    # client side — paths are sibling-relative because client/ is on sys.path
    ("audio.capture",               "client/audio/capture.py"),
    ("audio.youtube",               "client/audio/youtube.py"),
    ("audio.window_selector",       "client/audio/window_selector.py"),
    ("network.connection_manager",  "client/network/connection_manager.py"),
    ("network.cloud_control",       "client/network/cloud_control.py"),
    ("network.transmitter",         "client/network/transmitter.py"),
    ("video.vision_transmitter",    "client/video/vision_transmitter.py"),
    ("ui.overlay",                  "client/ui/overlay.py"),
    ("ui.workers",                  "client/ui/workers.py"),
]


def section_imports() -> None:
    print("\n[3/5] IMPORTS")
    for mod_name, label in PROJECT_MODULES:
        try:
            importlib.import_module(mod_name)
            record("imports", label, PASS)
        except Exception as e:
            record("imports", label, FAIL, f"{type(e).__name__}: {str(e)[:60]}")


# ===========================================================
# SECTION 4 — SMOKE TESTS
#   pure-logic checks on functions we cannot exercise via the GUI
#   alone — proves the algorithm itself is healthy
# ===========================================================
def section_smoke() -> None:
    print("\n[4/5] SMOKE TESTS")

    try:
        from ui.workers import deduplicate_transcript
    except Exception as e:
        record("smoke", "import deduplicate_transcript", FAIL, str(e))
        record("smoke", "dedup exact match",            SKIP, "import failed")
        record("smoke", "dedup fuzzy match",            SKIP, "import failed")
        record("smoke", "dedup edge cases (empty)",     SKIP, "import failed")
        return

    # exact match — last 3 words of old equal first 3 of new -> drop them
    try:
        out = deduplicate_transcript("hello world how are you", "how are you doing today")
        ok = out.strip() == "doing today"
        record("smoke", "dedup exact match", PASS if ok else FAIL,
               "" if ok else f"expected 'doing today', got '{out}'")
    except Exception as e:
        record("smoke", "dedup exact match", FAIL, f"{type(e).__name__}: {e}")

    # fuzzy match — wording differs but >= 60% similar -> still drop overlap
    try:
        out = deduplicate_transcript(
            "the meeting is starting now",
            "the meeting starts now let us begin"
        )
        # the dedup should have stripped the overlapping prefix and left
        # at least the genuinely new tail "let us begin"
        ok = "let us begin" in out
        record("smoke", "dedup fuzzy match", PASS if ok else FAIL,
               "" if ok else f"got '{out}'")
    except Exception as e:
        record("smoke", "dedup fuzzy match", FAIL, f"{type(e).__name__}: {e}")

    # edge cases — empty inputs must not crash and must return sane output
    try:
        a = deduplicate_transcript("", "anything")
        b = deduplicate_transcript("anything", "")
        c = deduplicate_transcript("", "")
        ok = a == "anything" and b == "" and c == ""
        record("smoke", "dedup edge cases (empty)", PASS if ok else FAIL,
               "" if ok else f"a={a!r} b={b!r} c={c!r}")
    except Exception as e:
        record("smoke", "dedup edge cases (empty)", FAIL, f"{type(e).__name__}: {e}")


# ===========================================================
# SECTION 5 — LIVE SESSION
#   launch the GUI, tail its stdout, parse log markers after it exits
# ===========================================================
# log markers we expect to see during a healthy short session
# label = friendly name in the grid, marker = substring in stdout
LIVE_LOG_MARKERS = [
    ("startup banner",          "[STARTUP] Launching transcription overlay"),
    ("yt-dlp self-update ran",  "[STARTUP] Checking for yt-dlp updates"),
    ("window scanner ran",      "[WINDOW] Found"),
    ("websocket connected",     "[WSS] Connected"),
    ("loopback capture on",     "[AUDIO] Loopback capture started"),
    ("microphone capture on",   "[MIC] Microphone capture started"),
    ("dual capture on",         "[DUAL] Dual capture started"),
    ("frame grabber on",        "[FRAME] Started"),
    ("ghost mode toggled",      "[GHOST] Screen capture invisibility"),
    ("health monitor on",       "[HEALTH] Monitor started"),
    ("server reachable check",  "[HEALTH] Server is reachable"),
    ("loopback capture off",    "[AUDIO] Loopback capture stopped"),
    ("microphone capture off",  "[MIC] Microphone capture stopped"),
    ("dual capture off",        "[DUAL] Dual capture stopped"),
    ("frame grabber off",       "[FRAME] Stopped"),
]


def section_live(skip_live: bool) -> None:
    print("\n[5/5] LIVE SESSION")

    if skip_live:
        # explicit SKIP for every live row — keeps the grid complete
        for label, _ in LIVE_LOG_MARKERS:
            record("live", label, SKIP, "--no-live mode")
        record("live", "no python traceback",                    SKIP, "--no-live mode")
        record("live", "transcript text appeared in panel",      SKIP, "--no-live mode")
        record("live", "compact recording bar visible",          SKIP, "--no-live mode")
        record("live", "ghost mode hid overlay from capture",    SKIP, "--no-live mode")
        record("live", "Save .txt produces non-empty file",      SKIP, "--no-live mode")
        return

    # one short, clear instruction block — Session 25 lesson:
    # don't bury actions in prose, list them as numbered steps
    print("""
  Launching the app now. Use it for one short session:

      1. wait for the overlay to appear
      2. click Start Listening
      3. play 15 sec of clear-speech audio (YouTube, podcast)
      4. click the Ghost: ON button, then click it back to Ghost: OFF
      5. click Stop
      6. click Save .txt and pick any path
      7. close the app window when done

  the script is reading the app's stdout — close the window to finish.
""")

    main_py = CLIENT_DIR / "main.py"
    if not main_py.exists():
        record("live", "app process launched", FAIL, "client/main.py missing")
        return

    # launching with cwd = project root so the app's relative paths
    # (e.g. .env, downloads/) all resolve the same way as `python client/main.py`
    #
    # -u + PYTHONUNBUFFERED forces the child to flush stdout per print()
    # call. without this, Python switches to block-buffered mode whenever
    # stdout is a pipe (our case here), so app prints sit in a 4KB buffer
    # and the script sees nothing until the buffer fills or the process
    # exits — a short live session ends with zero captured logs.
    #
    # PYTHONIOENCODING=utf-8 forces the child's stdio to use UTF-8 instead
    # of the Windows default cp1252 (charmap). without this, any print that
    # contains '→' or '—' (used in audio status logs and every [OCR] log)
    # raises UnicodeEncodeError when stdout is a pipe, silently killing
    # the speaker capture loop and the OCR drain loop. yes, this happened.
    child_env = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    # encoding="utf-8" matches PYTHONIOENCODING above — the child writes
    # UTF-8, so the parent must decode UTF-8 too. without this, text=True
    # falls back to locale encoding (cp1252 on Windows) and unicode chars
    # like '→' and '—' come out as 'â†'' / 'â€"' mojibake even though the
    # underlying bytes are correct. errors="replace" keeps the loop alive
    # if any single line ever does contain truly invalid bytes.
    proc = subprocess.Popen(
        [sys.executable, "-u", str(main_py)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        cwd=str(PROJECT_ROOT),
        env=child_env,
    )
    record("live", "app process launched", PASS, f"PID {proc.pid}")

    # tailing the app stdout into a buffer until the process exits
    # mirroring each line back to the user so they see what is happening
    log_lines: list[str] = []
    print("  app stdout below — close the app window to end the probe")
    print("  " + "-" * 60)
    try:
        for line in proc.stdout:                       # type: ignore[union-attr]
            line = line.rstrip()
            log_lines.append(line)
            print(f"  | {line}")
    except KeyboardInterrupt:
        # if the user Ctrl-C's the script instead of closing the window,
        # send a clean terminate and let the loop drain
        proc.terminate()
    proc.wait()
    print("  " + "-" * 60)

    joined = "\n".join(log_lines)

    # one row per log marker — did we see it during the session?
    for label, marker in LIVE_LOG_MARKERS:
        if marker in joined:
            record("live", label, PASS)
        else:
            record("live", label, FAIL, "marker not seen in stdout")

    # any traceback in stdout = automatic FAIL even if everything else passed
    if "Traceback" in joined:
        record("live", "no python traceback", FAIL, "Traceback found in stdout")
    else:
        record("live", "no python traceback", PASS)

    # things only the user can confirm — mark EYES, not auto-checkable
    # the script cannot read the QLabel text or detect a translucent window
    record("live", "transcript text appeared in panel",
           EYES, "did speaker text show up in the live panel?")
    record("live", "compact recording bar visible",
           EYES, "did the thin top bar with REC blink during recording?")
    record("live", "ghost mode hid overlay from capture",
           EYES, "verify in OBS / Zoom screen-share if you have time")
    record("live", "Save .txt produces non-empty file",
           EYES, "did the save dialog work and the file open with text?")


# ===========================================================
# FINAL GRID — one tidy report grouped by section
# ===========================================================
def print_grid() -> int:
    print("\n" + "=" * 72)
    print(f"SANITY GRID — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 72)

    # group rows by section in execution order — matches the user's mental
    # model of how the script ran
    section_order = [
        ("preflight", "PRE-FLIGHT"),
        ("deps",      "DEPENDENCIES"),
        ("imports",   "IMPORTS"),
        ("smoke",     "SMOKE TESTS"),
        ("live",      "LIVE SESSION"),
    ]
    for sec_id, title in section_order:
        rows = [r for r in RESULTS if r[0] == sec_id]
        if not rows:
            continue
        print(f"\n{title}")
        for _, name, status, note in rows:
            color = COLORS.get(status, "")
            line = f"  {color}{status:5}{RESET}  {name}"
            if note:
                line += f"  ({note})"
            print(line)

    # tally for the one-line summary
    counts = {PASS: 0, FAIL: 0, EYES: 0, SKIP: 0}
    for _, _, status, _ in RESULTS:
        counts[status] += 1

    print("\n" + "=" * 72)
    print(
        f"SUMMARY: "
        f"{COLORS[PASS]}{counts[PASS]} PASS{RESET}  ·  "
        f"{COLORS[FAIL]}{counts[FAIL]} FAIL{RESET}  ·  "
        f"{COLORS[EYES]}{counts[EYES]} EYES{RESET}  ·  "
        f"{COLORS[SKIP]}{counts[SKIP]} SKIP{RESET}"
    )
    print("=" * 72)

    # exit code 1 when anything failed — useful if we ever wire this into CI
    return 1 if counts[FAIL] > 0 else 0


# ===========================================================
# VENV AUTO-RELAUNCH
#   if a project venv/ exists and we are not already running inside
#   it, re-exec the script using the venv interpreter. catches the
#   classic mistake of running from a fresh shell where venv was
#   never activated — without this, every dep import fails and the
#   grid is a wall of red for one boring root cause.
# ===========================================================
def _relaunch_in_venv_if_needed() -> None:
    """re-exec inside the project venv if not already there"""
    # Windows layout first — project is Windows-primary
    venv_python = PROJECT_ROOT / "venv" / "Scripts" / "python.exe"
    if not venv_python.exists():
        # mac / linux fallback so the script still works cross-platform
        venv_python = PROJECT_ROOT / "venv" / "bin" / "python"
    if not venv_python.exists():
        return  # no venv at all — let the caller use whatever python they have

    current = Path(sys.executable).resolve()
    target  = venv_python.resolve()
    if current == target:
        return  # already inside the venv — nothing to do

    print(f"[BOOT] re-launching inside project venv: {target}")
    print(f"[BOOT] (was running under: {current})")
    # using subprocess.run instead of os.execv because os.execv on Windows
    # does not handle paths with spaces correctly — usernames like "nakul ari"
    # get split at the space and the relaunch crashes with a corrupted path
    cmd = [str(target), str(Path(__file__).resolve())] + sys.argv[1:]
    result = subprocess.run(cmd)
    sys.exit(result.returncode)


# ===========================================================
# MAIN — chain the five sections, then print the grid
# ===========================================================
def main() -> None:
    # auto-relaunch into venv if the user forgot to activate it
    # must run before anything that needs venv-only dependencies
    _relaunch_in_venv_if_needed()

    # supporting --no-live so the headless half can run without the GUI
    # useful when the SSH tunnel is down or for scripted CI checks
    skip_live = "--no-live" in sys.argv

    print("=" * 72)
    print("TRANSCRIPTION APP — SINGLE-COMMAND SANITY PASS")
    print("=" * 72)
    print(f"  project root : {PROJECT_ROOT}")
    print(f"  python       : {sys.executable}")
    print(f"  platform     : {sys.platform}")
    if skip_live:
        print("  mode         : --no-live (skipping subprocess GUI launch)")

    section_preflight()
    section_dependencies()
    section_imports()
    section_smoke()
    section_live(skip_live)

    sys.exit(print_grid())


if __name__ == "__main__":
    main()
