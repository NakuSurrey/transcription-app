# PHASE 5 REFERENCE — Production Hardening (Reconnection & Error Recovery)
# Session 10 — Step 5 Complete
# Updated: Session 10

---

## What Was Built

A resilience layer that makes the app recover automatically from network failures instead of crashing. Three systems work together:

1. **WebSocket auto-reconnect** — LiveTransmitter retries with exponential backoff when the connection drops mid-stream
2. **HTTP upload retry** — BulkTransmitter retries failed uploads up to 3 times before giving up
3. **Health monitor** — ConnectionManager periodically pings the server and alerts the UI when the connection state changes

---

## Files Changed

| File | Change Type | What Changed |
|------|-------------|-------------|
| `client/network/transmitter.py` | Modified | Added `reconnect()`, retry in `send_chunk()`, retry loop in `upload_file()`, `status_callback` on both classes |
| `client/network/connection_manager.py` | Modified | Added `start_health_monitor()`, `stop_health_monitor()`, background health check loop |
| `client/ui/workers.py` | Modified | `LiveWorker` accepts `connection_manager`, passes callbacks to transmitter + health monitor. `BulkWorker` passes callback to transmitter |
| `client/ui/overlay.py` | Modified | Added `connection_event` signal, `_on_connection_event()` handler, wired signals, updated `closeEvent` |

## Files NOT Changed (and why)

| File | Why No Change |
|------|--------------|
| `client/audio/capture.py` | Audio capture is independent of network state |
| `client/audio/youtube.py` | yt-dlp handles its own download retries internally |
| `client/main.py` | Entry point unchanged |
| `server/*` | Server doesn't need to know about client reconnection — it just accepts new connections |
| `client/network/cloud_control.py` | DigitalOcean API control unchanged |
| `.env` / `.env.example` | No new config variables needed |

---

## System 1: LiveTransmitter Auto-Reconnect

### How It Works

```
send_chunk() called → sends audio → receives response → returns result
                          │
                     CONNECTION DROPS
                          │
                          ▼
              catches ConnectionClosed/ConnectionClosedError
                          │
                          ▼
              calls reconnect()
                          │
                          ▼
              closes old broken socket
                          │
                          ▼
              backoff loop: wait 1s → try connect
                           wait 2s → try connect
                           wait 4s → try connect
                           wait 8s → try connect
                           wait 16s → try connect
                          │
                    ┌─────┴─────┐
                    │           │
               CONNECTED    ALL FAILED
                    │           │
                    ▼           ▼
            retry send_chunk   raise RuntimeError
            (one attempt)      (LiveWorker stops)
```

### Configuration

| Setting | Value | Purpose |
|---------|-------|---------|
| `max_retries` | 5 | Number of reconnection attempts before giving up |
| `base_delay` | 1 second | Wait time before first retry |
| `max_delay` | 30 seconds | Maximum wait time (caps exponential growth) |
| `status_callback` | function | Called on every state change to notify worker/UI |

### Exponential Backoff + Jitter Sequence

Each delay is multiplied by `random.uniform(0.5, 1.5)` to prevent the thundering herd
problem — where multiple clients all retry at the exact same intervals after a shared
server failure, spiking the server the moment it recovers.

| Attempt | Base Time | With Jitter (range) | Formula |
|---------|-----------|---------------------|---------|
| 1 | 1s | 0.5s – 1.5s | min(1 * 2^0, 30) * random(0.5, 1.5) |
| 2 | 2s | 1.0s – 3.0s | min(1 * 2^1, 30) * random(0.5, 1.5) |
| 3 | 4s | 2.0s – 6.0s | min(1 * 2^2, 30) * random(0.5, 1.5) |
| 4 | 8s | 4.0s – 12.0s | min(1 * 2^3, 30) * random(0.5, 1.5) |
| 5 | 16s | min(1 * 2^4, 30) = 16 |
| Total | 31s | Maximum time before giving up |

---

## System 2: BulkTransmitter Retry Logic

### How It Works

```
upload_file() called → creates FormData → sends HTTP POST
                                              │
                                         ┌────┴────┐
                                         │         │
                                    STATUS 200   ERROR
                                         │         │
                                         ▼         ▼
                                    return      What kind?
                                    result         │
                                              ┌────┴────┐
                                              │         │
                                         HTTP 4xx/5xx  Network error
                                         (RuntimeError) (ClientError/
                                              │         Timeout/OSError)
                                              ▼              │
                                         raise immediately   ▼
                                         (no retry)     RETRY with backoff
                                                        (up to 3 attempts)
```

### Why Some Errors Are Retried and Others Are Not

| Error Type | Retried? | Reason |
|------------|----------|--------|
| `aiohttp.ClientError` | Yes | Network failure — server may not have received the request |
| `asyncio.TimeoutError` | Yes | Request may have been interrupted — worth trying again |
| `OSError` | Yes | Low-level network error (DNS, socket reset) |
| `RuntimeError` (HTTP 400/500) | No | Server received and processed the request — the request itself is the problem |

### Configuration

| Setting | Value | Purpose |
|---------|-------|---------|
| `max_retries` | 3 | Fewer than WebSocket — each retry re-uploads a large file |
| `base_delay` | 2 seconds | Longer starting delay — uploads are heavier operations |
| `max_delay` | 15 seconds | Cap on wait time |

---

## System 3: Health Monitor

### How It Works

```
start_health_monitor() called by LiveWorker.start()
         │
         ▼
spawns background thread with asyncio loop
         │
         ▼
every 30 seconds: ping /health endpoint
         │
    ┌────┴────┐
    │         │
 HEALTHY   UNHEALTHY
    │         │
    ▼         ▼
state changed from last check?
    │
    ┌─────┴─────┐
    │           │
   YES          NO
    │           │
    ▼           ▼
call callback   do nothing
(UI updates)    (no redundant updates)
```

### Key Design Decisions

1. **Transition-only callbacks**: Only fires when state changes (healthy→unhealthy or unhealthy→healthy). Prevents flooding the UI with "still healthy" every 30 seconds.

2. **1-second sleep loop**: Instead of `sleep(30)`, sleeps 1 second at a time and checks `_health_running` between each. This lets the monitor exit within 1 second when stopped, instead of waiting up to 30 seconds.

3. **Daemon thread**: `daemon=True` means the thread is automatically killed when the main app exits. Prevents the app from hanging on close.

---

## Signal Flow — How Events Reach the UI

```
TRANSMITTER RECONNECTION:
  transmitter.py → _emit_status("reconnecting", msg)
       │
       ▼
  status_callback (set by LiveWorker.__init__)
       │
       ▼
  workers.py → _on_transmitter_status(status, msg)
       │
       ▼
  signals.connection_event.emit(status, msg)
       │
       ▼
  overlay.py → _on_connection_event(status, msg) → updates UI labels/colors

HEALTH MONITOR:
  connection_manager.py → _async_health_loop detects state change
       │
       ▼
  health_callback (set by LiveWorker.start)
       │
       ▼
  workers.py → _on_health_change(is_healthy, msg)
       │
       ▼
  signals.connection_event.emit("health_lost"/"health_restored", msg)
       │
       ▼
  overlay.py → _on_connection_event(status, msg) → updates UI labels/colors

BULK RETRY:
  transmitter.py → _emit_status("retrying", msg)
       │
       ▼
  status_callback (set by BulkWorker.__init__)
       │
       ▼
  workers.py → _on_transmitter_status(status, msg)
       │
       ▼
  signals.connection_event.emit(status, msg)
       │
       ▼
  overlay.py → _on_connection_event(status, msg) → updates bulk_status label
```

---

## UI Behavior Summary

| Event | Status Label | Color | Model/Bulk Label |
|-------|-------------|-------|-----------------|
| WebSocket reconnecting | "Server: Reconnecting..." | Amber (#FFB347) | "Attempt 2/5 — waiting 4s" |
| WebSocket reconnected | "Server: HPC Connected" | Green (#77DD77, resets after 3s) | "Connection restored" |
| Reconnection failed | "Server: Connection Lost" | Red (#FF6B6B) | "Could not reconnect after 5 attempts" |
| Bulk upload retrying | (unchanged) | (unchanged) | "Upload failed, retrying in 2s (attempt 2/3)" (amber) |
| Bulk retry succeeded | (unchanged) | (unchanged) | "Upload succeeded on attempt 2" (green, resets) |
| Health monitor: server down | "Server: Connection Lost" | Red (#FF6B6B) | (unchanged) |
| Health monitor: server back | "Server: HPC Connected" | Green (#77DD77, resets after 3s) | (unchanged) |

---

## Connection Map — Full System After Phase 5

```
.env
├── SERVER_MODE → connection_manager.py → decides HPC or DO behavior
├── SERVER_IP → transmitter.py → where to send audio
└── SERVER_PORT → transmitter.py → which port

client/
├── main.py → launches UI
├── audio/
│   ├── capture.py → WASAPI loopback audio capture + VAD
│   └── youtube.py → yt-dlp audio download
├── network/
│   ├── connection_manager.py → MODIFIED: + health monitor (background thread)
│   ├── cloud_control.py → DigitalOcean API (only used in DO mode)
│   └── transmitter.py → MODIFIED: + auto-reconnect (WSS) + retry (HTTP)
└── ui/
    ├── overlay.py → MODIFIED: + connection_event signal + handler
    └── workers.py → MODIFIED: + callback wiring + health monitor lifecycle

server/
├── main.py → FastAPI (unchanged)
├── deploy_surrey.sh → HPC one-time setup (unchanged)
├── surrey_job.sh → Slurm job script (unchanged)
├── tunnel.sh → SSH tunnel helper (unchanged)
└── models/
    └── transcriber.py → Canary + Whisper router (unchanged)
```

---

## Concepts Learned — Phase 5

| Concept | Definition |
|---------|-----------|
| Exponential backoff | A retry strategy where the wait time doubles after each failure (1s, 2s, 4s, 8s...), capped at a maximum. Prevents overwhelming a recovering server with simultaneous retry floods. |
| Jitter | A random multiplier applied to backoff delays (e.g., `delay * random(0.5, 1.5)`). Solves the thundering herd problem — without jitter, multiple clients that fail at the same moment all retry at identical intervals, spiking the server simultaneously. Jitter randomizes each client's timing so retries spread across time. |
| Thundering herd problem | When many clients detect the same failure at the same moment and all retry simultaneously, creating a spike that can crash the recovering server again. Solved by adding jitter to backoff delays. |
| Status callback | A function passed into a lower-level component (transmitter) so it can notify higher-level components (worker/UI) about state changes without knowing about them directly. |
| Transition-only notification | Only firing a callback when the state changes, not on every check. Prevents redundant UI updates. |
| Daemon thread | A background thread with `daemon=True` that Python automatically kills when the main thread exits. Prevents the app from hanging because a background thread is still running. |
| Connection-level vs request-level errors | Connection errors (server unreachable, DNS failure) are retryable because the request may never have arrived. Request errors (HTTP 400/500) are NOT retryable because the server received and rejected the request. |
| Graceful degradation | The app keeps working in a reduced state during network issues (showing "Reconnecting..." instead of crashing), then fully recovers when the connection returns. |
| Short-sleep polling | Using a loop of 1-second sleeps instead of one long sleep, checking a stop flag between each. Allows the monitor to exit quickly when told to stop. |

---

## Interview Prep — Phase 5

**Q: How does your application handle network failures during real-time audio streaming?**

A: The LiveTransmitter has an auto-reconnect mechanism with exponential backoff. When `send_chunk()` catches a `ConnectionClosed` exception, it calls `reconnect()` which closes the dead socket and enters a retry loop — waiting 1 second, then 2, then 4, and so on up to 5 attempts. Each attempt emits a status callback that flows through Qt signals to update the UI with "Reconnecting..." in amber. If reconnection succeeds, it retries the failed audio chunk once. If all 5 attempts fail, it raises a RuntimeError and the pipeline stops cleanly. In parallel, a health monitor pings the server every 30 seconds and proactively warns the user if the connection drops before they try to use it.

**Q: Why did you separate the retry logic between LiveTransmitter (WebSocket) and BulkTransmitter (HTTP)?**

A: They have fundamentally different failure characteristics. WebSocket is a persistent connection — when it drops, we need to re-establish the connection itself, then retry the data send. The backoff is on the connection, not the data. HTTP is stateless — each request is independent, so we retry the entire request (re-upload the file). The BulkTransmitter also distinguishes between retryable errors (network failures, timeouts) and non-retryable errors (HTTP 400/500 responses). A 500 means the server received and rejected the request — sending it again won't help.

**Q: What prevents your health monitor from conflicting with the transmitter's reconnection logic?**

A: They both emit through the same `connection_event` Qt signal to the same UI handler. The UI simply displays the most recent update. There's no conflict because they're updating the same label — the last event to arrive determines what the user sees. The health monitor checks every 30 seconds, while reconnection happens in real-time during failures, so the reconnection events naturally take priority during active failures.

---

*Phase 5 complete. Production hardening built. Auto-reconnect, retry logic, and health monitoring all wired through callbacks → signals → UI. Ready for Step 6 (deploy to HPC and test) after GitHub push.*
