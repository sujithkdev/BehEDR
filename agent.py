# -*- coding: utf-8 -*-
"""
agent.py — EDR Windows Monitoring Agent v3.0
=============================================
Copy ONLY this file to the monitored Windows machine.

SETUP:
  1. Edit SERVER_IP below  OR  set environment variable EDR_SERVER_IP.
  2. Set API_KEY to match the server  OR  set EDR_API_KEY env var.
  3. pip install psutil requests
  4. python agent.py

Improvements over v2:
  • PID expiry dict — prevents unbounded memory growth
  • Async sender thread — monitor never blocks on network
  • SSL warning logged clearly
  • collect_info returns proper JSON (not str(dict))
  • Environment variable overrides for zero-touch deployment
"""

import os, sys, time, socket, platform, threading, logging, json, queue, hashlib
import requests, psutil
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

# ── Sysmon reader (optional — only active when Sysmon is installed) ───────────
_SYSMON_AVAILABLE = False
_sysmon_reader    = None
try:
    from sysmon_reader import SysmonReader, is_sysmon_installed
    _SYSMON_AVAILABLE = True
except ImportError:
    pass   # sysmon_reader.py not present — graceful degradation

# ── Configuration ─────────────────────────────────────────────────────────────
SERVER_IP   = os.environ.get("EDR_SERVER_IP",  "10.255.74.71")   # ← edit this
SERVER_PORT = int(os.environ.get("EDR_PORT",   "5000"))
API_KEY     = os.environ.get("EDR_API_KEY",    "123456789")

# ── TLS configuration ─────────────────────────────────────────────────────────
# EDR_TLS_ENABLED  : set to "true" to connect over HTTPS.
#                    Defaults to "false" so the agent works with a plain HTTP
#                    server without any cert setup.
#                    Must be set to "true" when the server is started with
#                    EDR_TLS_ENABLED=true.
# EDR_TLS_CA_CERT  : path to the server's CA/self-signed cert PEM for
#                    verification.  Required when TLS is enabled and the server
#                    uses a self-signed cert (set to the path printed by run.py).
#                    Leave empty to use the system CA bundle (production CAs).
_TLS_ENABLED = os.environ.get("EDR_TLS_ENABLED", "false").lower() == "true"
_TLS_CA_CERT = os.environ.get("EDR_TLS_CA_CERT", "")   # path to CA bundle PEM

# Determine the scheme and verify parameter for requests
if _TLS_ENABLED:
    _SCHEME = "https"
    # If a CA cert path is provided, use it; otherwise use the system CA bundle (True)
    _TLS_VERIFY = _TLS_CA_CERT if _TLS_CA_CERT else True
else:
    _SCHEME = "http"
    _TLS_VERIFY = False   # dev/lab only — never use in production

_BASE      = f"{_SCHEME}://{SERVER_IP}:{SERVER_PORT}"
EVENTS_URL = _BASE + "/events"
CMDS_URL   = _BASE + "/commands/" + socket.gethostname()
RESULT_URL = _BASE + "/commands/result"
HB_URL     = _BASE + "/heartbeat"
REG_URL    = _BASE + "/register"
DLL_URL    = _BASE + "/dll_events"       # DLL load events
NET_URL    = _BASE + "/net_events"       # Network connection events
FILE_URL   = _BASE + "/file_events"     # File system events
ETW_URL    = _BASE + "/etw_events"      # ETW telemetry events
YARA_URL   = _BASE + "/yara_hits"      # YARA memory scan hits
SYSMON_URL = _BASE + "/etw_events"     # Sysmon special events share /etw_events
HEADERS    = {"API-Key": API_KEY, "Content-Type": "application/json"}
SYSTEM_ID  = socket.gethostname()

POLL_SECS  = 2      # process scan interval
CMD_SECS   = 5      # command poll interval
HB_SECS    = 15     # heartbeat interval
NET_SECS   = 10     # network scan interval
FILE_SECS  = 5      # file system event flush interval
TIMEOUT    = 5      # HTTP timeout
MAX_RETRY  = 3      # retries per request
PID_TTL    = 300    # seconds before a PID entry expires

# ── Shared state ──────────────────────────────────────────────────────────────
_event_queue: queue.Queue = queue.Queue(maxsize=10_000)
_stop    = threading.Event()
_pid_seen: dict = {}    # {pid: first_seen_timestamp}
_pid_lock = threading.Lock()

# ── Logging ───────────────────────────────────────────────────────────────────
_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.log")
logging.basicConfig(
    handlers=[
        RotatingFileHandler(_LOG_FILE, maxBytes=5*1024*1024, backupCount=3, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("agent")

if not _TLS_ENABLED:
    # Suppress InsecureRequestWarning when TLS is off (the default for dev).
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    log.warning(
        "TLS DISABLED — traffic is unencrypted (HTTP). "
        "Set EDR_TLS_ENABLED=true and EDR_TLS_CA_CERT=<cert> for production."
    )
else:
    log.info(
        "TLS enabled. CA bundle: %s",
        _TLS_CA_CERT if _TLS_CA_CERT else "system default"
    )


# ── PID tracking ──────────────────────────────────────────────────────────────

def _pid_is_new(pid: int) -> bool:
    """Return True if this PID has not been seen recently; tracks with expiry."""
    now = time.time()
    with _pid_lock:
        expired = [p for p, ts in _pid_seen.items() if now - ts > PID_TTL]
        for p in expired:
            del _pid_seen[p]
        if pid in _pid_seen:
            return False
        _pid_seen[pid] = now
        return True


def _seed_baseline():
    """Record all currently running PIDs so they are NOT reported as new."""
    now = time.time()
    with _pid_lock:
        for p in psutil.process_iter(["pid"]):
            _pid_seen[p.pid] = now
    log.info("Baseline: %d existing processes seeded.", len(_pid_seen))


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _post(url, data, label="POST"):
    for attempt in range(1, MAX_RETRY + 1):
        try:
            r = requests.post(
                url, json=data, headers=HEADERS,
                timeout=TIMEOUT, verify=_TLS_VERIFY)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 401:
                log.error("Auth failed — check API_KEY matches server config.")
                return None
            else:
                log.warning("%s HTTP %d (attempt %d/%d)", label, r.status_code, attempt, MAX_RETRY)
        except requests.exceptions.SSLError as e:
            log.error(
                "%s: TLS/SSL error — verify EDR_TLS_CA_CERT points to the correct CA bundle. "
                "Details: %s", label, e)
            break   # SSL errors won't resolve with retries
        except requests.exceptions.ConnectionError:
            log.warning("%s: server unreachable (attempt %d/%d)", label, attempt, MAX_RETRY)
        except requests.exceptions.Timeout:
            log.warning("%s: timeout (attempt %d/%d)", label, attempt, MAX_RETRY)
        except Exception as e:
            log.error("%s error: %s", label, e)
            break
        if attempt < MAX_RETRY:
            time.sleep(2)
    return None


def _get(url, label="GET"):
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, verify=_TLS_VERIFY)
        if r.status_code == 200:
            return r.json()
        elif r.status_code == 401:
            log.error("Auth failed on GET %s", url)
    except requests.exceptions.SSLError as e:
        log.error(
            "%s: TLS/SSL error — verify EDR_TLS_CA_CERT points to the correct CA bundle. "
            "Details: %s", label, e)
    except requests.exceptions.ConnectionError:
        log.debug("%s: server unreachable", label)
    except Exception as e:
        log.error("%s error: %s", label, e)
    return None


# ── Process monitoring ─────────────────────────────────────────────────────────

def _parent_name(ppid):
    try:
        return psutil.Process(ppid).name()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return "unknown"


def _cmdline(proc):
    try:
        parts = proc.cmdline()
        return " ".join(parts)[:2048] if parts else ""
    except Exception:
        return ""


def _sha256_of(proc) -> str:
    """Return SHA-256 of the process executable, or empty string on failure."""
    try:
        path = proc.exe()
        if not path or not os.path.isfile(path):
            return ""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return ""


def _exe_path(proc) -> str:
    """Return the full path of the process executable."""
    try:
        return proc.exe() or ""
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return ""


def _build_event(proc):
    try:
        with proc.oneshot():
            ppid = proc.ppid()
            return {
                "system_id": SYSTEM_ID,
                "event":     "process_spawn",
                "parent":    _parent_name(ppid),
                "child":     proc.name(),
                "pid":       proc.pid,
                "cmdline":   _cmdline(proc),
                "sha256":    _sha256_of(proc),
                "exe_path":  _exe_path(proc),
                "time":      int(datetime.now(timezone.utc).timestamp()),
            }
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return None


# ── DLL monitoring ────────────────────────────────────────────────────────────

_known_dll_loads: dict = {}   # pid → set of dll paths already reported
_dll_lock2 = threading.Lock()

# Cache signing results per DLL path — avoid re-checking the same DLL repeatedly.
# Most processes load the same system DLLs (kernel32, ntdll, etc.) and we only
# need to check each unique path once per agent session.
_dll_sign_cache: dict = {}   # dll_path_lower → bool (signed)
_dll_sign_lock = threading.Lock()


def _is_dll_signed(dll_path: str) -> bool:
    """
    Check if a DLL is authenticode-signed (Windows only, best-effort).
    Results are cached per unique DLL path — each path is only checked once.
    Falls back to True (assume signed) on non-Windows or on error, which is
    the safe direction for this check (path-based checks in dll_monitor.py
    catch hijacks regardless of signing status).
    """
    if os.name != "nt":
        return True

    dll_path_lc = dll_path.lower()

    # Return cached result if available — avoids spawning PowerShell again
    with _dll_sign_lock:
        if dll_path_lc in _dll_sign_cache:
            return _dll_sign_cache[dll_path_lc]

    try:
        import subprocess
        # Single-line PowerShell with -NonInteractive to avoid profile loading
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"(Get-AuthenticodeSignature '{dll_path}').Status"],
            capture_output=True, text=True, timeout=4)
        result = "Valid" in r.stdout
    except Exception:
        result = True   # assume signed if we can't check

    with _dll_sign_lock:
        _dll_sign_cache[dll_path_lc] = result
        # Keep cache bounded — evict oldest half when it exceeds 2000 entries
        if len(_dll_sign_cache) > 2000:
            keys = list(_dll_sign_cache.keys())
            for k in keys[:1000]:
                del _dll_sign_cache[k]

    return result


def _scan_process_dlls(proc) -> list:
    """
    Return list of DLL load events for new DLLs loaded by this process.
    Only reports each DLL once per PID.
    Signing check is performed via PowerShell Get-AuthenticodeSignature —
    results are cached per path so the check only runs once per unique DLL path.
    """
    events = []
    try:
        pid = proc.pid
        with _dll_lock2:
            seen = _known_dll_loads.setdefault(pid, set())

        maps = proc.memory_maps(grouped=True)
        for m in maps:
            path = m.path.lower() if hasattr(m, 'path') else ""
            if not path.endswith(".dll"):
                continue
            if path in seen:
                continue
            with _dll_lock2:
                seen.add(path)

            dll_name = os.path.basename(path)
            # Call the signing check — result cached inside _is_dll_signed
            signed = _is_dll_signed(path)
            events.append({
                "system_id": SYSTEM_ID,
                "event":     "dll_load",
                "process":   proc.name(),
                "pid":       pid,
                "dll_name":  dll_name,
                "dll_path":  path,
                "signed":    signed,
                "time":      int(datetime.now(timezone.utc).timestamp()),
            })
    except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
        pass
    return events


# ── AMSI note ─────────────────────────────────────────────────────────────────
# AMSI scanning has been intentionally removed from this agent.
# Reason: AMSI fires at script-engine load time (before execution). Scanning
# a cmdline AFTER the process is already running provides no prevention value —
# the payload has already started executing. Windows Defender and any installed
# AV product register as AMSI providers automatically; there is no benefit to
# duplicating that scan in a user-space agent.
# Detection of malicious script behaviour is handled server-side by the
# 10-layer detection engine (L1 chain rules, L2 heuristics, L4 MITRE intent,
# L10 sequence ML) which all operate on the cmdline reported in the event.
# ─────────────────────────────────────────────────────────────────────────────


# ── Network connection monitoring ─────────────────────────────────────────────

_known_connections: set = set()   # (pid, laddr, raddr) already reported
_net_lock2 = threading.Lock()


def _scan_network_connections() -> list:
    """
    Scan all active network connections and return new ones.
    Feeds L6 (network beaconing + lateral movement detection) on the server.

    Filtering policy:
      - Loopback (127.x.x.x) and link-local (169.254.x.x) are always skipped —
        these are purely local and never relevant for threat detection.
      - Private RFC1918 IPs (10.x, 172.16-31.x, 192.168.x) ARE reported when
        they involve lateral movement ports (445 SMB, 3389 RDP, 135 DCOM,
        139 NetBIOS, 5985/5986 WinRM).  Lateral movement is 100% internal
        by definition — filtering private IPs made it completely invisible.
      - All external (public) IPs are always reported — feeds C2 beaconing
        detection on the server side.
    """
    LATERAL_PORTS = {445, 3389, 135, 139, 5985, 5986}

    events = []
    try:
        conns = psutil.net_connections(kind="inet")
        now   = int(datetime.now(timezone.utc).timestamp())

        for c in conns:
            if not c.raddr or not c.pid:
                continue
            rip, rport = c.raddr.ip, c.raddr.port

            # Always skip loopback and link-local — never actionable
            if _is_loopback_or_linklocal(rip):
                continue

            # For private IPs, only report lateral movement ports.
            # Reporting every internal connection (browser → intranet, etc.)
            # would generate enormous noise with no detection value.
            if _is_private_ip(rip) and rport not in LATERAL_PORTS:
                continue

            key = (c.pid, str(c.laddr), str(c.raddr))
            with _net_lock2:
                if key in _known_connections:
                    continue
                _known_connections.add(key)
                if len(_known_connections) > 5000:
                    _known_connections.clear()

            try:
                proc_name = psutil.Process(c.pid).name()
            except Exception:
                proc_name = "unknown"

            events.append({
                "system_id":   SYSTEM_ID,
                "event":       "network_connect",
                "process":     proc_name,
                "pid":         c.pid,
                "local_addr":  f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "",
                "remote_ip":   rip,
                "remote_port": rport,
                "status":      c.status,
                # Tag whether this is an internal lateral movement connection
                # so the server can route it to the right L6 detection branch.
                "is_internal": _is_private_ip(rip),
                "time":        now,
            })
    except Exception as e:
        log.debug("Network scan error: %s", e)

    return events


def _is_loopback_or_linklocal(ip: str) -> bool:
    """Return True for 127.x.x.x and 169.254.x.x — always skip these."""
    try:
        parts = list(map(int, ip.split(".")))
        if parts[0] == 127: return True
        if parts[0] == 169 and parts[1] == 254: return True
        return False
    except Exception:
        return True   # unparseable — skip


def _is_private_ip(ip: str) -> bool:
    """Return True if IP is RFC1918 private (but NOT loopback/link-local)."""
    try:
        parts = list(map(int, ip.split(".")))
        if parts[0] == 10: return True
        if parts[0] == 172 and 16 <= parts[1] <= 31: return True
        if parts[0] == 192 and parts[1] == 168: return True
        return False
    except Exception:
        return False   # unparseable external — report it


def _network_loop():
    """Scan network connections every NET_SECS and send to server."""
    log.info("Network monitor started. Scanning every %ds.", NET_SECS)
    while not _stop.is_set():
        events = _scan_network_connections()
        if events:
            _post(NET_URL, {"events": events}, "NET_EVENTS")
        _stop.wait(timeout=NET_SECS)
    log.info("Network monitor stopped.")


# ── File system monitoring ────────────────────────────────────────────────────
# Watches directories that are common staging/execution locations for malware:
#   - User temp dirs       (AppData\Local\Temp, Temp)
#   - Public dirs          (C:\Users\Public, C:\Windows\Temp)
#   - Desktop & Downloads  (common drop locations)
#
# Uses ReadDirectoryChangesW on Windows (built into the winapi via ctypes).
# Falls back to a polling stat-based approach on non-Windows or on error.
# Feeds L7 (ransomware: mass file modification, ransom notes, encrypted extensions).

_file_event_queue: list = []
_file_eq_lock = threading.Lock()

# Directories to watch — only writable user/temp paths that attackers favour
_WATCH_DIRS = [
    os.path.expandvars(r"%TEMP%"),
    os.path.expandvars(r"%LOCALAPPDATA%\Temp"),
    r"C:\Windows\Temp",
    r"C:\Users\Public",
    os.path.expandvars(r"%USERPROFILE%\Desktop"),
    os.path.expandvars(r"%USERPROFILE%\Downloads"),
]

# Only report these extensions — avoids flooding on log/cache churn.
# Includes common document types (ransomware targets) + suspicious extensions.
_WATCH_EXTENSIONS = {
    ".exe", ".dll", ".bat", ".ps1", ".vbs", ".js", ".hta", ".cmd",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".pdf",
    ".zip", ".rar", ".7z",
    ".encrypted", ".locked", ".crypto", ".crypt", ".enc",
    ".txt", ".html", ".hta",   # ransom note formats
}


def _rdcw_watch(watch_dir: str):
    """
    Watch a single directory with ReadDirectoryChangesW (Windows only).
    Pushes (action_str, full_path) tuples into _file_event_queue.
    Runs in its own daemon thread — one thread per watched directory.
    """
    if os.name != "nt":
        return

    import ctypes
    from ctypes import wintypes

    FILE_LIST_DIRECTORY    = 0x0001
    FILE_SHARE_READ        = 0x00000001
    FILE_SHARE_WRITE       = 0x00000002
    FILE_SHARE_DELETE      = 0x00000004
    OPEN_EXISTING          = 3
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000

    FILE_NOTIFY_CHANGE_FILE_NAME  = 0x00000001
    FILE_NOTIFY_CHANGE_SIZE       = 0x00000008
    FILE_NOTIFY_CHANGE_LAST_WRITE = 0x00000010

    _ACTION_MAP = {1: "create", 2: "delete", 3: "modify", 4: "rename", 5: "rename"}

    kernel32 = ctypes.windll.kernel32

    try:
        hDir = kernel32.CreateFileW(
            watch_dir,
            FILE_LIST_DIRECTORY,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            None, OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS, None)
        if hDir == ctypes.wintypes.HANDLE(-1).value:
            log.debug("RDCW: cannot open %s (may not exist)", watch_dir)
            return

        buf  = ctypes.create_string_buffer(65536)
        ret  = ctypes.wintypes.DWORD(0)
        flags = (FILE_NOTIFY_CHANGE_FILE_NAME |
                 FILE_NOTIFY_CHANGE_SIZE |
                 FILE_NOTIFY_CHANGE_LAST_WRITE)

        log.info("File monitor watching: %s", watch_dir)
        while not _stop.is_set():
            ok = kernel32.ReadDirectoryChangesW(
                hDir, buf, ctypes.sizeof(buf), True,
                flags, ctypes.byref(ret), None, None)
            if not ok:
                break

            offset = 0
            while True:
                # Parse FILE_NOTIFY_INFORMATION struct
                next_entry = ctypes.wintypes.DWORD.from_buffer_copy(buf, offset).value
                action     = ctypes.wintypes.DWORD.from_buffer_copy(buf, offset + 4).value
                name_len   = ctypes.wintypes.DWORD.from_buffer_copy(buf, offset + 8).value
                name_raw   = buf.raw[offset + 12: offset + 12 + name_len]
                filename   = name_raw.decode("utf-16-le", errors="replace")
                full_path  = os.path.join(watch_dir, filename)
                ext        = os.path.splitext(filename)[1].lower()

                if ext in _WATCH_EXTENSIONS:
                    action_str = _ACTION_MAP.get(action, "modify")
                    with _file_eq_lock:
                        _file_event_queue.append((action_str, full_path))
                        # Cap queue to avoid unbounded growth during storms
                        if len(_file_event_queue) > 2000:
                            _file_event_queue.clear()

                if next_entry == 0:
                    break
                offset += next_entry

        kernel32.CloseHandle(hDir)
    except Exception as e:
        log.debug("RDCW watcher error (%s): %s", watch_dir, e)


def _start_file_watchers():
    """Start one RDCW watcher thread per watch directory (Windows only)."""
    if os.name != "nt":
        log.info("File monitor: RDCW not available on non-Windows — L7 disabled.")
        return
    for d in _WATCH_DIRS:
        if os.path.isdir(d):
            threading.Thread(
                target=_rdcw_watch, args=(d,),
                name=f"FileWatcher-{os.path.basename(d)}",
                daemon=True).start()
        else:
            log.debug("File monitor: skipping non-existent dir %s", d)


def _file_loop():
    """
    Flush the file event queue every FILE_SECS and send to server.
    Batches events and attributes them to the process most recently
    seen running from the same directory (best-effort; ransomware
    typically runs from Temp/Public so this is usually accurate).
    """
    _start_file_watchers()
    log.info("File event sender started. Flush every %ds.", FILE_SECS)
    now_ts = lambda: int(datetime.now(timezone.utc).timestamp())

    while not _stop.is_set():
        _stop.wait(timeout=FILE_SECS)

        with _file_eq_lock:
            if not _file_event_queue:
                continue
            batch, _file_event_queue[:] = _file_event_queue[:200], _file_event_queue[200:]

        events = []
        ts = now_ts()
        for action, path in batch:
            # Best-effort: find a process whose exe lives in the same dir
            proc_name = "unknown"
            file_dir  = os.path.dirname(path).lower()
            try:
                for p in psutil.process_iter(["name", "exe"]):
                    try:
                        exe = (p.info.get("exe") or "").lower()
                        if exe and os.path.dirname(exe) == file_dir:
                            proc_name = p.info["name"]
                            break
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except Exception:
                pass

            events.append({
                "system_id": SYSTEM_ID,
                "event":     "file_event",
                "process":   proc_name,
                "file_path": path,
                "action":    action,
                "time":      ts,
            })

        if events:
            _post(FILE_URL, {"events": events}, "FILE_EVENTS")
            log.debug("Sent %d file event(s).", len(events))

    log.info("File event sender stopped.")


# ── ETW telemetry ─────────────────────────────────────────────────────────────

def _etw_sender_loop():
    """
    Drain the ETW event queue produced by etw_consumer.etw_loop()
    and send batches to the server via /etw_events.

    Runs as its own thread so it never blocks the psutil monitor loop.
    The ETW consumer thread (started separately) fills etw_consumer.etw_event_queue;
    this thread drains it.
    """
    try:
        from etw_consumer import etw_event_queue, is_available
    except ImportError:
        log.warning("[ETW] etw_consumer.py not found — ETW sender disabled.")
        return

    if not is_available():
        log.info("[ETW] ETW not available — sender thread exiting.")
        return

    log.info("[ETW] ETW event sender started.")
    while not _stop.is_set():
        batch = []
        try:
            batch.append(etw_event_queue.get(timeout=2))
            while len(batch) < 100:
                try:
                    batch.append(etw_event_queue.get_nowait())
                except queue.Empty:
                    break
        except queue.Empty:
            continue

        if batch:
            _post(ETW_URL, {"events": batch}, "ETW_EVENTS")
            log.debug("[ETW] Sent %d ETW event(s).", len(batch))

    log.info("[ETW] ETW event sender stopped.")


def _etw_collection_loop():
    """
    Wrapper that starts the ETW consumer session.
    Runs as its own daemon thread — blocks on ETW ProcessTrace() internally.
    """
    try:
        from etw_consumer import etw_loop
    except ImportError:
        log.warning("[ETW] etw_consumer.py not found — ETW collection disabled.")
        return

    log.info("[ETW] Starting ETW collection session...")
    etw_loop(SYSTEM_ID, _stop)


# ── YARA memory scanning ───────────────────────────────────────────────────────

def _yara_scan_loop():
    """
    Wrapper that starts the YARA in-memory scanner.
    Degrades gracefully if yara-python is not installed.
    """
    try:
        from yara_scanner import yara_scan_loop, is_available
    except ImportError:
        log.warning("[YARA] yara_scanner.py not found — memory scanning disabled.")
        return

    if not is_available():
        log.info("[YARA] yara-python not installed — memory scanning disabled.")
        return

    # Resolve rules dir relative to agent.py location
    here      = os.path.dirname(os.path.abspath(__file__))
    rules_dir = os.path.join(here, "rules", "yara")

    log.info("[YARA] Starting memory scanner...")
    yara_scan_loop(SYSTEM_ID, _stop, rules_dir=rules_dir)


def _yara_sender_loop():
    """
    Drain YARA hits from yara_scanner.yara_hit_queue and POST to /yara_hits.
    Marks the originating PID as priority for follow-up scanning.
    """
    try:
        from yara_scanner import yara_hit_queue, add_priority_pid, is_available
    except ImportError:
        log.warning("[YARA] yara_scanner.py not found — YARA sender disabled.")
        return

    if not is_available():
        return

    log.info("[YARA] YARA hit sender started.")
    while not _stop.is_set():
        batch = []
        try:
            batch.append(yara_hit_queue.get(timeout=2))
            while len(batch) < 50:
                try:
                    batch.append(yara_hit_queue.get_nowait())
                except queue.Empty:
                    break
        except queue.Empty:
            continue

        if batch:
            _post(YARA_URL, {"events": batch}, "YARA_HITS")
            log.warning("[YARA] Sent %d YARA hit(s) to server.", len(batch))

    log.info("[YARA] YARA hit sender stopped.")

def _sysmon_start():
    """
    Start the Sysmon reader if Sysmon is installed on this machine.
    Returns the SysmonReader instance (so main() can start its sender),
    or None if Sysmon is not available.
    """
    global _sysmon_reader
    if not _SYSMON_AVAILABLE:
        log.info("[SYSMON] sysmon_reader.py not found — Sysmon collection disabled.")
        return None

    try:
        if not is_sysmon_installed():
            log.warning(
                "[SYSMON] Sysmon not installed on this machine.\n"
                "         Install it for kernel-level visibility:\n"
                "         sysmon64.exe -accepteula -i sysmonconfig.xml"
            )
            return None

        _sysmon_reader = SysmonReader(system_id=SYSTEM_ID, stop_event=_stop)
        _sysmon_reader.start(event_queue=_event_queue)
        log.info("[SYSMON] ✅ Sysmon reader active — kernel-level visibility ENABLED")
        return _sysmon_reader

    except Exception as e:
        log.error("[SYSMON] Failed to start reader: %s", e)
        return None


def _sysmon_sender_loop():
    """
    Drain the SysmonReader.special_queue and POST batches to /etw_events.
    Handles routing:
      sysmon_network      → /net_events  (feeds L6 beaconing)
      sysmon_dll_load     → /dll_events  (feeds L9 hijacking)
      sysmon_file_create  → /file_events (feeds L7 ransomware)
      sysmon_file_delete  → /file_events (feeds L7 wiper)
      everything else     → /etw_events  (inject/lsass/dns/pipe/registry)
    """
    if not _SYSMON_AVAILABLE or _sysmon_reader is None:
        return

    log.info("[SYSMON] Special-event sender started.")

    # Route event type → (URL, event_key_name)
    _ROUTE = {
        "sysmon_network":     (NET_URL,  "network_connect"),
        "sysmon_dll_load":    (DLL_URL,  "dll_load"),
        "sysmon_file_create": (FILE_URL, "file_event"),
        "sysmon_file_delete": (FILE_URL, "file_event"),
    }

    while not _stop.is_set():
        batch = []
        try:
            batch.append(_sysmon_reader.special_queue.get(timeout=1))
            while len(batch) < 50:
                try:
                    batch.append(_sysmon_reader.special_queue.get_nowait())
                except queue.Empty:
                    break
        except queue.Empty:
            continue

        if not batch:
            continue

        # Group by destination URL
        groups: dict = {}
        for ev in batch:
            etype = ev.get("event", "")
            route = _ROUTE.get(etype)
            if route:
                url, new_type = route
                ev = dict(ev)
                ev["event"] = new_type
            else:
                url = SYSMON_URL

            groups.setdefault(url, []).append(ev)

        for url, evs in groups.items():
            result = _post(url, {"events": evs}, "SYSMON")
            if result:
                log.debug("[SYSMON] Sent %d events → %s", len(evs), url)

    log.info("[SYSMON] Special-event sender stopped.")


def _monitor_loop():
    """Scan for new processes + DLL loads every POLL_SECS; enqueue events."""
    _seed_baseline()
    log.info("Monitor started. Scanning every %ds.", POLL_SECS)
    while not _stop.is_set():
        try:
            for proc in psutil.process_iter(["pid", "name"]):
                pid = proc.pid

                # ── New process detection ──────────────────────────────
                if _pid_is_new(pid):
                    ev = _build_event(proc)
                    if ev:
                        log.debug("NEW: %s -> %s (PID %d)",
                                  ev["parent"], ev["child"], ev["pid"])
                        try:
                            _event_queue.put_nowait(ev)
                        except queue.Full:
                            log.warning("Event queue full — dropping event")

                # ── DLL load scanning (every process, not just new) ────
                # Only scan processes that are script engines or LOLBins
                # to keep CPU low — full DLL scanning is expensive
                try:
                    proc_name = proc.name().lower()
                    if proc_name in (
                            "powershell.exe", "pwsh.exe", "rundll32.exe",
                            "regsvr32.exe", "mshta.exe", "wscript.exe",
                            "cscript.exe", "installutil.exe", "msbuild.exe"):
                        dll_evs = _scan_process_dlls(proc)
                        for dev in dll_evs[:5]:   # max 5 DLL events per scan
                            try:
                                _event_queue.put_nowait(dev)
                            except queue.Full:
                                break
                except Exception:
                    pass

        except Exception as e:
            log.error("Scan error: %s", e)
        _stop.wait(timeout=POLL_SECS)
    log.info("Monitor stopped.")


# ── Async sender ──────────────────────────────────────────────────────────────

def _sender_loop():
    """Drain the local event queue and send batches to the server."""
    log.info("Sender started.")
    while not _stop.is_set():
        batch = []
        try:
            batch.append(_event_queue.get(timeout=1))
            while len(batch) < 50:
                try:
                    batch.append(_event_queue.get_nowait())
                except queue.Empty:
                    break
        except queue.Empty:
            continue

        if batch:
            result = _post(EVENTS_URL, {"events": batch}, "EVENTS")
            if result:
                log.info("Sent %d event(s). Server: %s", len(batch), result)
            else:
                # Re-queue on failure (best effort)
                for ev in batch:
                    try:
                        _event_queue.put_nowait(ev)
                    except queue.Full:
                        break
    log.info("Sender stopped.")


# ── Heartbeat ─────────────────────────────────────────────────────────────────

def _heartbeat_loop():
    log.info("Heartbeat started. Interval: %ds.", HB_SECS)
    while not _stop.is_set():
        _post(HB_URL, {"system_id": SYSTEM_ID}, "HEARTBEAT")
        _stop.wait(timeout=HB_SECS)
    log.info("Heartbeat stopped.")


# ── C2 command handler ────────────────────────────────────────────────────────

def _exec_command(cmd):
    cid    = cmd.get("id")
    action = cmd.get("action", "")
    params = cmd.get("params", {})
    log.info("C2 command: id=%s action=%s params=%s", cid, action, params)
    result = "unknown action"

    if action == "kill_process":
        target = params.get("target", "")
        killed = []
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if proc.name().lower() == target.lower():
                    proc.kill()
                    killed.append(f"{proc.name()} (PID {proc.pid})")
                    log.warning("KILLED: %s", proc.name())
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        result = f"Killed: {', '.join(killed)}" if killed else f"Not found: {target}"

    elif action == "restart_agent":
        result = "Baseline reset and rescanning."
        log.warning("RESTART requested by server.")
        with _pid_lock:
            _pid_seen.clear()
        _seed_baseline()

    elif action == "update_interval":
        result = f"update_interval acknowledged: {params.get('value', 2)}"
        log.info(result)

    elif action == "collect_info":
        disk_path = "C:\\" if os.name == "nt" else "/"
        info = {
            "hostname":     socket.gethostname(),
            "platform":     platform.platform(),
            "cpu_count":    psutil.cpu_count(),
            "cpu_pct":      psutil.cpu_percent(interval=1),
            "ram_total_gb": round(psutil.virtual_memory().total / 1e9, 1),
            "ram_used_pct": psutil.virtual_memory().percent,
            "disk_total_gb": round(psutil.disk_usage(disk_path).total / 1e9, 1),
            "disk_free_gb":  round(psutil.disk_usage(disk_path).free / 1e9, 1),
            "processes":    len(psutil.pids()),
            "agent_ver":    "3.0",
        }
        result = json.dumps(info)
        log.info("collect_info: %s", result)

    elif action == "get_processes":
        procs = []
        for p in psutil.process_iter(["pid", "name", "username", "cpu_percent", "memory_percent"]):
            try:
                procs.append({
                    "pid":  p.pid,
                    "name": p.name(),
                    "user": p.username(),
                    "cpu":  round(p.cpu_percent(), 1),
                    "mem":  round(p.memory_percent(), 1),
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        procs.sort(key=lambda x: x["cpu"], reverse=True)
        result = json.dumps(procs[:100])

    elif action == "get_network":
        conns = []
        for c in psutil.net_connections(kind="inet"):
            try:
                conns.append({
                    "pid":    c.pid,
                    "laddr":  f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "",
                    "raddr":  f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "",
                    "status": c.status,
                })
            except Exception:
                pass
        result = json.dumps(conns[:100])

    elif action == "isolate_network":
        # Block all inbound + outbound traffic except to the EDR server.
        # Uses Windows Firewall (netsh advfirewall) — reversible via
        # restore_network command or manually:
        #   netsh advfirewall set allprofiles firewallpolicy allowinbound,allowoutbound
        #   netsh advfirewall firewall delete rule name="BehEDR-Isolate-*"
        try:
            import subprocess
            server_ip = SERVER_IP   # allow comms back to EDR server

            # Block all inbound
            subprocess.run([
                "netsh", "advfirewall", "firewall", "add", "rule",
                "name=BehEDR-Isolate-BlockInbound",
                "dir=in", "action=block", "profile=any",
                "enable=yes", "description=BehEDR isolation rule",
            ], capture_output=True, timeout=10)

            # Block all outbound
            subprocess.run([
                "netsh", "advfirewall", "firewall", "add", "rule",
                "name=BehEDR-Isolate-BlockOutbound",
                "dir=out", "action=block", "profile=any",
                "enable=yes", "description=BehEDR isolation rule",
            ], capture_output=True, timeout=10)

            # Allow outbound to EDR server (so agent stays connected)
            subprocess.run([
                "netsh", "advfirewall", "firewall", "add", "rule",
                f"name=BehEDR-Isolate-AllowEDR",
                "dir=out", "action=allow", "profile=any",
                f"remoteip={server_ip}",
                "enable=yes", "description=BehEDR isolation allow-EDR",
            ], capture_output=True, timeout=10)

            # Allow inbound from EDR server (so server can send commands)
            subprocess.run([
                "netsh", "advfirewall", "firewall", "add", "rule",
                f"name=BehEDR-Isolate-AllowEDR-In",
                "dir=in", "action=allow", "profile=any",
                f"remoteip={server_ip}",
                "enable=yes", "description=BehEDR isolation allow-EDR",
            ], capture_output=True, timeout=10)

            result = f"Network isolated. Only EDR server ({server_ip}) allowed."
            log.warning("[ISOLATE] %s", result)
        except Exception as e:
            result = f"isolate_network failed: {e}"
            log.error("[ISOLATE] %s", result)

    elif action == "restore_network":
        # Remove all BehEDR isolation firewall rules and restore normal traffic.
        try:
            import subprocess
            for rule in [
                "BehEDR-Isolate-BlockInbound",
                "BehEDR-Isolate-BlockOutbound",
                "BehEDR-Isolate-AllowEDR",
                "BehEDR-Isolate-AllowEDR-In",
            ]:
                subprocess.run([
                    "netsh", "advfirewall", "firewall", "delete", "rule",
                    f"name={rule}",
                ], capture_output=True, timeout=10)
            result = "Network isolation removed. Normal traffic restored."
            log.warning("[RESTORE] %s", result)
        except Exception as e:
            result = f"restore_network failed: {e}"
            log.error("[RESTORE] %s", result)

    elif action == "collect_forensics":
        # Snapshot: running processes, network connections, loaded modules,
        # recently modified files in temp dirs, and basic system info.
        # Result is a JSON string uploaded to the server.
        try:
            import subprocess, hashlib as _h

            snap: dict = {
                "collected_at": int(datetime.now(timezone.utc).timestamp()),
                "system_id":    SYSTEM_ID,
                "hostname":     socket.gethostname(),
                "platform":     platform.platform(),
            }

            # ── Running processes ─────────────────────────────────────────
            procs_snap = []
            for p in psutil.process_iter([
                    "pid", "name", "exe", "cmdline",
                    "username", "create_time", "ppid"]):
                try:
                    info = p.info
                    procs_snap.append({
                        "pid":         info["pid"],
                        "name":        info["name"],
                        "exe":         info.get("exe") or "",
                        "cmdline":     " ".join(info.get("cmdline") or [])[:512],
                        "user":        info.get("username") or "",
                        "ppid":        info.get("ppid") or 0,
                        "created":     int(info.get("create_time") or 0),
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            snap["processes"] = procs_snap

            # ── Network connections ───────────────────────────────────────
            conns_snap = []
            for c in psutil.net_connections(kind="inet"):
                try:
                    conns_snap.append({
                        "pid":    c.pid,
                        "laddr":  f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "",
                        "raddr":  f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "",
                        "status": c.status,
                    })
                except Exception:
                    pass
            snap["connections"] = conns_snap

            # ── Recent files in suspicious dirs (last 10 min) ─────────────
            recent_files = []
            cutoff = time.time() - 600   # 10 minutes
            suspect_dirs = [
                os.path.expandvars(r"%TEMP%"),
                os.path.expandvars(r"%LOCALAPPDATA%\Temp"),
                r"C:\Windows\Temp",
                r"C:\Users\Public",
            ]
            for d in suspect_dirs:
                if not os.path.isdir(d):
                    continue
                try:
                    for fname in os.listdir(d):
                        fpath = os.path.join(d, fname)
                        try:
                            st = os.stat(fpath)
                            if st.st_mtime >= cutoff:
                                recent_files.append({
                                    "path":     fpath,
                                    "size":     st.st_size,
                                    "modified": int(st.st_mtime),
                                })
                        except OSError:
                            pass
                except Exception:
                    pass
            snap["recent_suspicious_files"] = recent_files[:100]

            # ── Startup entries (registry Run keys via reg query) ─────────
            startup = []
            reg_keys = [
                r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
                r"HKLM\Software\Microsoft\Windows\CurrentVersion\Run",
            ]
            for key in reg_keys:
                try:
                    r = subprocess.run(
                        ["reg", "query", key],
                        capture_output=True, text=True, timeout=5)
                    if r.returncode == 0:
                        startup.append({"key": key, "output": r.stdout[:1024]})
                except Exception:
                    pass
            snap["startup_entries"] = startup

            # ── Scheduled tasks ───────────────────────────────────────────
            try:
                r = subprocess.run(
                    ["schtasks", "/query", "/fo", "CSV", "/nh"],
                    capture_output=True, text=True, timeout=10)
                snap["scheduled_tasks"] = r.stdout[:4096] if r.returncode == 0 else ""
            except Exception:
                snap["scheduled_tasks"] = ""

            result = json.dumps(snap)
            log.warning("[FORENSICS] Snapshot collected: %d processes, "
                        "%d connections, %d recent files",
                        len(procs_snap), len(conns_snap), len(recent_files))

        except Exception as e:
            result = f"collect_forensics failed: {e}"
            log.error("[FORENSICS] %s", result)

    _post(RESULT_URL, {"id": cid, "result": result}, "CMD_RESULT")


def _cmd_loop():
    log.info("Command handler started. Polling every %ds.", CMD_SECS)
    while not _stop.is_set():
        data = _get(CMDS_URL, "COMMANDS")
        if data:
            for cmd in data.get("commands", []):
                try:
                    _exec_command(cmd)
                except Exception as e:
                    log.error("Command error: %s", e)
        _stop.wait(timeout=CMD_SECS)
    log.info("Command handler stopped.")


# ── Registration ──────────────────────────────────────────────────────────────

def _register():
    data = {
        "system_id":  SYSTEM_ID,
        "hostname":   socket.gethostname(),
        "platform":   platform.platform(),
        "ip_address": "",
        "agent_ver":  "3.0",
    }
    result = _post(REG_URL, data, "REGISTER")
    if result:
        log.info("Registered with server: %s", result)
    else:
        log.warning("Registration failed — server may not be ready yet.")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    print("=" * 54)
    print("  EDR Agent v3.0")
    print(f"  System  : {SYSTEM_ID}")
    print(f"  Server  : {_BASE}")
    print(f"  Log     : {_LOG_FILE}")
    print("=" * 54)

    log.info("Agent starting. System: %s  Server: %s", SYSTEM_ID, _BASE)
    _register()

    # ── Start Sysmon reader first (sets up special_queue before sender starts) ─
    _sysmon_start()

    threads = [
        threading.Thread(target=_monitor_loop,       name="Monitor",       daemon=True),
        threading.Thread(target=_sender_loop,         name="Sender",        daemon=True),
        threading.Thread(target=_heartbeat_loop,      name="Heartbeat",     daemon=True),
        threading.Thread(target=_cmd_loop,            name="CmdHandler",    daemon=True),
        threading.Thread(target=_network_loop,        name="NetMonitor",    daemon=True),
        threading.Thread(target=_file_loop,           name="FileMonitor",   daemon=True),
        threading.Thread(target=_etw_collection_loop, name="ETWSession",    daemon=True),
        threading.Thread(target=_etw_sender_loop,     name="ETWSender",     daemon=True),
        threading.Thread(target=_yara_scan_loop,      name="YARAScan",      daemon=True),
        threading.Thread(target=_yara_sender_loop,    name="YARASender",    daemon=True),
        threading.Thread(target=_sysmon_sender_loop,  name="SysmonSender",  daemon=True),
    ]
    for t in threads:
        t.start()
        log.info("Thread started: %s", t.name)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Stopping agent…")
        _stop.set()
        for t in threads:
            t.join(timeout=5)
        log.info("Agent stopped.")
        print("\n[*] Agent stopped.")


if __name__ == "__main__":
    main()
