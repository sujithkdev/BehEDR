# -*- coding: utf-8 -*-
"""
sysmon_reader.py — Agent-side Sysmon Event Log reader
======================================================
Reads Microsoft-Windows-Sysmon/Operational via wevtutil (built into every
Windows version since Vista — no extra dependencies required).

Parses Sysmon XML events and converts them to the standard EDR event format
so they flow into the existing 13-layer detection engine on the server.

Supported Sysmon Event IDs
──────────────────────────
  1  ProcessCreate      → process_spawn        → /events (full pipeline)
  3  NetworkConnect     → sysmon_network        → /net_events (L6 beaconing)
  7  ImageLoad (DLL)    → sysmon_dll_load       → /dll_events (L9 hijacking)
  8  CreateRemoteThread → sysmon_inject         → /etw_events (instant CRITICAL)
 10  ProcessAccess      → sysmon_lsass_access   → /etw_events (credential dump)
 11  FileCreate         → sysmon_file_create    → /file_events (L7 ransomware)
 12  RegistryAdd/Delete → sysmon_registry       → /etw_events (persistence)
 13  RegistrySet        → sysmon_registry       → /etw_events (persistence)
 15  FileCreateStream   → sysmon_ads            → /etw_events (ADS malware)
 17  PipeCreated        → sysmon_pipe           → /etw_events (C2 pipe check)
 18  PipeConnected      → sysmon_pipe           → /etw_events (C2 pipe check)
 22  DnsQuery           → sysmon_dns            → /etw_events (IOC domain)
 23  FileDelete         → sysmon_file_delete    → /file_events (L7 wiper)
 25  ProcessTampering   → sysmon_hollowing      → /etw_events (T1055.012)

Usage (called from agent.py)
────────────────────────────
  from sysmon_reader import SysmonReader, is_sysmon_installed

  if is_sysmon_installed():
      reader = SysmonReader(system_id=SYSTEM_ID, stop_event=_stop)
      reader.start(event_queue=_event_queue)
  else:
      log.warning("[SYSMON] Not installed — kernel-level visibility limited")
"""

import os
import re
import time
import queue
import logging
import hashlib
import threading
import subprocess
import xml.etree.ElementTree as ET
from typing import Optional

log = logging.getLogger("agent.sysmon")

# ── Constants ─────────────────────────────────────────────────────────────────
SYSMON_CHANNEL   = "Microsoft-Windows-Sysmon/Operational"
WEVTUTIL_PATH    = r"C:\Windows\System32\wevtutil.exe"
POLL_INTERVAL    = 2      # seconds between wevtutil polls
BATCH_SIZE       = 200    # max events to read per poll
MAX_QUEUE_DROP   = 0      # log a warning after this many queue-full drops

# Sysmon EventIDs we care about
TRACKED_EVENT_IDS = {1, 3, 7, 8, 10, 11, 12, 13, 15, 17, 18, 22, 23, 25}

# Processes that are legitimate LSASS callers (do not alert on these)
_LSASS_SAFE_CALLERS = {
    "lsass.exe", "werfault.exe", "taskmgr.exe", "services.exe",
    "antimalware service executable", "mssense.exe",
}

# Known-malicious Cobalt Strike default named pipe names
_COBALT_STRIKE_PIPES = {
    r"\msagent_",    r"\postex_",    r"\postex_ssh_",
    r"\mojo.",       r"\interprocess_",  r"\ntsvcs",
    r"\wkssvc",      r"\browser",    r"\srvsvc",
    r"\msse-",       r"\status_",    r"\DserNamePipe",
    r"\win_svc",     r"\searchindexer", r"\advapi_",
    r"\ntsvcs",      r"\Winsock2\CatalogChangeListener",
}

# ── Sysmon detection ──────────────────────────────────────────────────────────

def is_sysmon_installed() -> bool:
    """Return True if Sysmon service is installed and the event channel exists."""
    try:
        r = subprocess.run(
            [WEVTUTIL_PATH, "gl", SYSMON_CHANNEL],
            capture_output=True, timeout=5
        )
        return r.returncode == 0
    except Exception:
        return False


def get_sysmon_version() -> str:
    """Return installed Sysmon version string or 'unknown'."""
    try:
        r = subprocess.run(
            ["sc", "query", "Sysmon64"],
            capture_output=True, text=True, timeout=5
        )
        if r.returncode == 0:
            return "Sysmon64"
        r = subprocess.run(
            ["sc", "query", "Sysmon"],
            capture_output=True, text=True, timeout=5
        )
        if r.returncode == 0:
            return "Sysmon"
    except Exception:
        pass
    return "unknown"


# ── XML parsing helpers ───────────────────────────────────────────────────────

_NS = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}


def _xml_get(root: ET.Element, *path: str) -> str:
    """Safe attribute/text extraction from Sysmon XML."""
    try:
        el = root
        for p in path:
            el = el.find(p, _NS)
            if el is None:
                return ""
        return (el.text or "").strip()
    except Exception:
        return ""


def _parse_hashes(hashes_str: str) -> dict:
    """
    Parse Sysmon Hashes field.
    Format: "MD5=abc,SHA256=def,IMPHASH=ghi"
    Returns dict keyed by algorithm (lowercase).
    """
    result = {}
    for part in hashes_str.split(","):
        if "=" in part:
            algo, value = part.split("=", 1)
            result[algo.strip().lower()] = value.strip().lower()
    return result


def _basename(path: str) -> str:
    """Extract filename from a Windows path."""
    return os.path.basename(path.replace("\\", "/")).lower() if path else ""


def _now() -> int:
    return int(time.time())


# ── Per-event-ID parsers ──────────────────────────────────────────────────────

def _parse_ev1(root: ET.Element, system_id: str) -> Optional[dict]:
    """Event 1 — ProcessCreate → process_spawn (richest event, full pipeline)."""
    ed = root.find(".//e:EventData", _NS)
    if ed is None:
        return None

    def _d(name):
        el = ed.find(f"e:Data[@Name='{name}']", _NS)
        return (el.text or "").strip() if el is not None else ""

    hashes  = _parse_hashes(_d("Hashes"))
    sha256  = hashes.get("sha256", "")
    cmdline = _d("CommandLine")[:1024]
    image   = _d("Image")

    return {
        "system_id": system_id,
        "event":     "process_spawn",
        "source":    "sysmon_ev1",
        "parent":    _basename(_d("ParentImage")),
        "child":     _basename(image),
        "exe_path":  image,
        "pid":       int(_d("ProcessId") or 0),
        "ppid":      int(_d("ParentProcessId") or 0),
        "cmdline":   cmdline,
        "sha256":    sha256,
        "user":      _d("User"),
        "logon_id":  _d("LogonId"),
        "integrity": _d("IntegrityLevel"),
        "time":      _now(),
    }


def _parse_ev3(root: ET.Element, system_id: str) -> Optional[dict]:
    """Event 3 — NetworkConnect → sysmon_network (L6 + DNS hostname)."""
    ed = root.find(".//e:EventData", _NS)
    if ed is None:
        return None

    def _d(name):
        el = ed.find(f"e:Data[@Name='{name}']", _NS)
        return (el.text or "").strip() if el is not None else ""

    dest_ip   = _d("DestinationIp")
    dest_port = int(_d("DestinationPort") or 0)
    dest_host = _d("DestinationHostname")

    if not dest_ip or not dest_port:
        return None

    return {
        "system_id":    system_id,
        "event":        "sysmon_network",
        "source":       "sysmon_ev3",
        "process":      _basename(_d("Image")),
        "exe_path":     _d("Image"),
        "remote_ip":    dest_ip,
        "remote_port":  dest_port,
        "remote_host":  dest_host,   # DNS name — feeds IOC domain check
        "local_ip":     _d("SourceIp"),
        "local_port":   int(_d("SourcePort") or 0),
        "protocol":     _d("Protocol"),
        "initiated":    _d("Initiated").lower() == "true",
        "user":         _d("User"),
        "time":         _now(),
        # Classify as internal (RFC1918) for L6 beaconing
        "is_internal":  _is_rfc1918(dest_ip),
    }


def _parse_ev7(root: ET.Element, system_id: str) -> Optional[dict]:
    """Event 7 — ImageLoad → sysmon_dll_load (L9 DLL hijacking)."""
    ed = root.find(".//e:EventData", _NS)
    if ed is None:
        return None

    def _d(name):
        el = ed.find(f"e:Data[@Name='{name}']", _NS)
        return (el.text or "").strip() if el is not None else ""

    hashes  = _parse_hashes(_d("Hashes"))
    signed  = _d("Signed").lower() == "true"

    return {
        "system_id": system_id,
        "event":     "sysmon_dll_load",
        "source":    "sysmon_ev7",
        "process":   _basename(_d("Image")),
        "exe_path":  _d("Image"),
        "dll_name":  _basename(_d("ImageLoaded")),
        "dll_path":  _d("ImageLoaded"),
        "signed":    signed,
        "signature": _d("Signature"),
        "sha256":    hashes.get("sha256", ""),
        "user":      _d("User"),
        "time":      _now(),
    }


def _parse_ev8(root: ET.Element, system_id: str) -> Optional[dict]:
    """Event 8 — CreateRemoteThread → sysmon_inject (instant CRITICAL)."""
    ed = root.find(".//e:EventData", _NS)
    if ed is None:
        return None

    def _d(name):
        el = ed.find(f"e:Data[@Name='{name}']", _NS)
        return (el.text or "").strip() if el is not None else ""

    return {
        "system_id":      system_id,
        "event":          "sysmon_inject",
        "source":         "sysmon_ev8",
        "source_process": _basename(_d("SourceImage")),
        "source_path":    _d("SourceImage"),
        "source_pid":     int(_d("SourceProcessId") or 0),
        "target_process": _basename(_d("TargetImage")),
        "target_path":    _d("TargetImage"),
        "target_pid":     int(_d("TargetProcessId") or 0),
        "start_address":  _d("StartAddress"),
        "start_module":   _d("StartModule"),
        "user":           _d("User"),
        "time":           _now(),
    }


def _parse_ev10(root: ET.Element, system_id: str) -> Optional[dict]:
    """Event 10 — ProcessAccess → sysmon_lsass_access (credential dump T1003)."""
    ed = root.find(".//e:EventData", _NS)
    if ed is None:
        return None

    def _d(name):
        el = ed.find(f"e:Data[@Name='{name}']", _NS)
        return (el.text or "").strip() if el is not None else ""

    target = _d("TargetImage").lower()
    if "lsass" not in target:
        return None   # Only care about LSASS access

    source_proc = _basename(_d("SourceImage"))

    # Skip known-safe callers
    if source_proc.lower() in _LSASS_SAFE_CALLERS:
        return None

    return {
        "system_id":      system_id,
        "event":          "sysmon_lsass_access",
        "source":         "sysmon_ev10",
        "source_process": source_proc,
        "source_path":    _d("SourceImage"),
        "source_pid":     int(_d("SourceProcessId") or 0),
        "target_process": _basename(_d("TargetImage")),
        "target_pid":     int(_d("TargetProcessId") or 0),
        "access_mask":    _d("GrantedAccess"),
        "call_trace":     _d("CallTrace"),
        "user":           _d("SourceUser"),
        "time":           _now(),
    }


def _parse_ev11(root: ET.Element, system_id: str) -> Optional[dict]:
    """Event 11 — FileCreate → sysmon_file_create (L7 ransomware/persistence)."""
    ed = root.find(".//e:EventData", _NS)
    if ed is None:
        return None

    def _d(name):
        el = ed.find(f"e:Data[@Name='{name}']", _NS)
        return (el.text or "").strip() if el is not None else ""

    return {
        "system_id": system_id,
        "event":     "sysmon_file_create",
        "source":    "sysmon_ev11",
        "process":   _basename(_d("Image")),
        "exe_path":  _d("Image"),
        "file_path": _d("TargetFilename"),
        "action":    "write",
        "user":      _d("User"),
        "time":      _now(),
    }


def _parse_ev12_13(root: ET.Element, system_id: str, ev_id: int) -> Optional[dict]:
    """Events 12/13 — RegistryAdd/Set → sysmon_registry (persistence T1547)."""
    ed = root.find(".//e:EventData", _NS)
    if ed is None:
        return None

    def _d(name):
        el = ed.find(f"e:Data[@Name='{name}']", _NS)
        return (el.text or "").strip() if el is not None else ""

    reg_key  = _d("TargetObject")
    ev_type  = _d("EventType")

    return {
        "system_id": system_id,
        "event":     "sysmon_registry",
        "source":    f"sysmon_ev{ev_id}",
        "process":   _basename(_d("Image")),
        "exe_path":  _d("Image"),
        "reg_key":   reg_key,
        "reg_value": _d("Details"),
        "ev_type":   ev_type,
        "user":      _d("User"),
        "time":      _now(),
        # Synthesise child process name from registry write for detection engine
        "parent":    _basename(_d("Image")),
        "child":     _basename(_d("Image")),
        "cmdline":   f"reg set {reg_key}",
    }


def _parse_ev15(root: ET.Element, system_id: str) -> Optional[dict]:
    """Event 15 — FileCreateStreamHash → sysmon_ads (Alternate Data Streams)."""
    ed = root.find(".//e:EventData", _NS)
    if ed is None:
        return None

    def _d(name):
        el = ed.find(f"e:Data[@Name='{name}']", _NS)
        return (el.text or "").strip() if el is not None else ""

    return {
        "system_id":  system_id,
        "event":      "sysmon_ads",
        "source":     "sysmon_ev15",
        "process":    _basename(_d("Image")),
        "exe_path":   _d("Image"),
        "file_path":  _d("TargetFilename"),   # includes ":Zone.Identifier" etc.
        "sha256":     _parse_hashes(_d("Hash")).get("sha256", ""),
        "user":       _d("User"),
        "time":       _now(),
    }


def _parse_ev17_18(root: ET.Element, system_id: str, ev_id: int) -> Optional[dict]:
    """Events 17/18 — PipeCreated/Connected → sysmon_pipe (Cobalt Strike pipes)."""
    ed = root.find(".//e:EventData", _NS)
    if ed is None:
        return None

    def _d(name):
        el = ed.find(f"e:Data[@Name='{name}']", _NS)
        return (el.text or "").strip() if el is not None else ""

    pipe_name = _d("PipeName")

    return {
        "system_id":  system_id,
        "event":      "sysmon_pipe",
        "source":     f"sysmon_ev{ev_id}",
        "action":     "create" if ev_id == 17 else "connect",
        "process":    _basename(_d("Image")),
        "exe_path":   _d("Image"),
        "pipe_name":  pipe_name,
        "user":       _d("User"),
        "time":       _now(),
    }


def _parse_ev22(root: ET.Element, system_id: str) -> Optional[dict]:
    """Event 22 — DNSQuery → sysmon_dns (C2 domain IOC check)."""
    ed = root.find(".//e:EventData", _NS)
    if ed is None:
        return None

    def _d(name):
        el = ed.find(f"e:Data[@Name='{name}']", _NS)
        return (el.text or "").strip() if el is not None else ""

    query = _d("QueryName")
    if not query:
        return None

    return {
        "system_id": system_id,
        "event":     "sysmon_dns",
        "source":    "sysmon_ev22",
        "process":   _basename(_d("Image")),
        "exe_path":  _d("Image"),
        "domain":    query.lower().rstrip("."),
        "result":    _d("QueryResults"),
        "user":      _d("User"),
        "time":      _now(),
    }


def _parse_ev23(root: ET.Element, system_id: str) -> Optional[dict]:
    """Event 23 — FileDelete → sysmon_file_delete (wiper/ransomware)."""
    ed = root.find(".//e:EventData", _NS)
    if ed is None:
        return None

    def _d(name):
        el = ed.find(f"e:Data[@Name='{name}']", _NS)
        return (el.text or "").strip() if el is not None else ""

    return {
        "system_id": system_id,
        "event":     "sysmon_file_delete",
        "source":    "sysmon_ev23",
        "process":   _basename(_d("Image")),
        "exe_path":  _d("Image"),
        "file_path": _d("TargetFilename"),
        "action":    "delete",
        "sha256":    _parse_hashes(_d("Hashes")).get("sha256", ""),
        "user":      _d("User"),
        "time":      _now(),
    }


def _parse_ev25(root: ET.Element, system_id: str) -> Optional[dict]:
    """Event 25 — ProcessTampering → sysmon_hollowing (T1055.012)."""
    ed = root.find(".//e:EventData", _NS)
    if ed is None:
        return None

    def _d(name):
        el = ed.find(f"e:Data[@Name='{name}']", _NS)
        return (el.text or "").strip() if el is not None else ""

    return {
        "system_id":  system_id,
        "event":      "sysmon_hollowing",
        "source":     "sysmon_ev25",
        "process":    _basename(_d("Image")),
        "exe_path":   _d("Image"),
        "pid":        int(_d("ProcessId") or 0),
        "tamper_type": _d("Type"),   # "Image is replaced", "Image is locked"
        "user":       _d("User"),
        "time":       _now(),
    }


# ── Event dispatcher ──────────────────────────────────────────────────────────

_PARSERS = {
    1:  _parse_ev1,
    3:  _parse_ev3,
    7:  _parse_ev7,
    8:  _parse_ev8,
    10: _parse_ev10,
    11: _parse_ev11,
    12: lambda r, s: _parse_ev12_13(r, s, 12),
    13: lambda r, s: _parse_ev12_13(r, s, 13),
    15: _parse_ev15,
    17: lambda r, s: _parse_ev17_18(r, s, 17),
    18: lambda r, s: _parse_ev17_18(r, s, 18),
    22: _parse_ev22,
    23: _parse_ev23,
    25: _parse_ev25,
}


def _parse_sysmon_event(xml_str: str, system_id: str) -> Optional[dict]:
    """
    Parse a single Sysmon event XML string and return the EDR event dict.
    Returns None if the event is not tracked or cannot be parsed.
    """
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        return None

    # Extract EventID
    ev_id_el = root.find(".//e:System/e:EventID", _NS)
    if ev_id_el is None:
        return None

    try:
        ev_id = int(ev_id_el.text.strip())
    except (ValueError, AttributeError):
        return None

    if ev_id not in TRACKED_EVENT_IDS:
        return None

    parser = _PARSERS.get(ev_id)
    if parser is None:
        return None

    try:
        return parser(root, system_id)
    except Exception as e:
        log.debug("[SYSMON] Parse error ev%d: %s", ev_id, e)
        return None


# ── RFC1918 check ─────────────────────────────────────────────────────────────

def _is_rfc1918(ip: str) -> bool:
    """Return True if IP is in a private (RFC 1918) range."""
    try:
        parts = [int(x) for x in ip.split(".")]
        if len(parts) != 4:
            return False
        if parts[0] == 10:
            return True
        if parts[0] == 172 and 16 <= parts[1] <= 31:
            return True
        if parts[0] == 192 and parts[1] == 168:
            return True
        return False
    except Exception:
        return False


# ── wevtutil reader ───────────────────────────────────────────────────────────

class SysmonReader:
    """
    Reads Sysmon events from the Windows Event Log using wevtutil.
    Runs in a background thread; enqueues parsed events into the agent's
    existing _event_queue so they are batched and sent by _sender_loop.

    Architecture fit:
      - SysmonReader.start() spawns a single daemon thread
      - Parsed events are enqueued into the same queue as psutil events
      - No new sender thread needed — reuses existing _sender_loop
      - Special event types (inject, lsass, dns, pipe, hollowing) are
        placed on a separate _sysmon_special_queue and sent to /etw_events
        by the SysmonSpecialSender thread
    """

    def __init__(self, system_id: str, stop_event: threading.Event,
                 poll_interval: int = POLL_INTERVAL):
        self.system_id    = system_id
        self.stop         = stop_event
        self.poll_interval = poll_interval
        self._bookmark    = None   # wevtutil bookmark (resume position)
        self._drop_count  = 0
        # Separate queue for special events that go to /etw_events
        self.special_queue: queue.Queue = queue.Queue(maxsize=2000)

    def start(self, event_queue: queue.Queue):
        """Start the reader thread. event_queue is the agent's main _event_queue."""
        self._eq = event_queue
        t = threading.Thread(target=self._run, name="SysmonReader", daemon=True)
        t.start()
        log.info("[SYSMON] Reader started (channel: %s)", SYSMON_CHANNEL)
        return t

    def _run(self):
        """Main polling loop."""
        # Brief startup delay — wait for agent to finish registering
        self.stop.wait(timeout=3)

        # Initialise bookmark at current end of log (skip historical events)
        self._init_bookmark()

        log.info("[SYSMON] Reading new events every %ds.", self.poll_interval)

        while not self.stop.is_set():
            try:
                self._poll()
            except Exception as e:
                log.error("[SYSMON] Poll error: %s", e)
            self.stop.wait(timeout=self.poll_interval)

        log.info("[SYSMON] Reader stopped.")

    def _init_bookmark(self):
        """Record the current log size so we only read new events going forward."""
        try:
            r = subprocess.run(
                [WEVTUTIL_PATH, "gli", SYSMON_CHANNEL],
                capture_output=True, text=True, timeout=10
            )
            for line in r.stdout.splitlines():
                if line.strip().startswith("numberOfLogRecords"):
                    self._bookmark = line.split(":")[-1].strip()
                    break
        except Exception as e:
            log.debug("[SYSMON] Could not init bookmark: %s", e)
        log.debug("[SYSMON] Bookmark initialised at record ~%s", self._bookmark)

    def _poll(self):
        """
        Query Sysmon channel for new events via wevtutil qe (query events).
        Uses XPath filter for only the event IDs we track.
        """
        # XPath query — only tracked EventIDs
        ids_xpath = " or ".join(f"EventID={i}" for i in sorted(TRACKED_EVENT_IDS))
        xpath = f"*[System[{ids_xpath}]]"

        cmd = [
            WEVTUTIL_PATH, "qe", SYSMON_CHANNEL,
            f"/q:{xpath}",
            f"/c:{BATCH_SIZE}",   # max count
            "/rd:false",          # oldest first
            "/f:xml",
            "/e:Events",          # wrap all in <Events>
        ]

        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=30, encoding="utf-8", errors="replace"
            )
        except subprocess.TimeoutExpired:
            log.warning("[SYSMON] wevtutil timed out")
            return
        except FileNotFoundError:
            log.error("[SYSMON] wevtutil.exe not found — is this a Windows machine?")
            self.stop.set()
            return

        if not r.stdout.strip():
            return

        self._parse_and_enqueue(r.stdout)

    def _parse_and_enqueue(self, xml_block: str):
        """Split wevtutil output into individual events and enqueue."""
        # wevtutil wraps all events in <Events>...</Events>
        # Split on individual <Event> tags
        events_xml = re.findall(
            r"<Event\s[^>]*>.*?</Event>",
            xml_block,
            re.DOTALL
        )

        process_count = 0
        special_count = 0

        for xml_str in events_xml:
            try:
                ev = _parse_sysmon_event(xml_str, self.system_id)
                if ev is None:
                    continue

                event_type = ev.get("event", "")

                # Route event to correct queue
                if event_type == "process_spawn":
                    # Ev1 → main queue → existing process pipeline
                    try:
                        self._eq.put_nowait(ev)
                        process_count += 1
                    except queue.Full:
                        self._drop_count += 1
                        if self._drop_count % 100 == 1:
                            log.warning(
                                "[SYSMON] Event queue full — %d events dropped",
                                self._drop_count
                            )

                elif event_type in ("sysmon_network", "sysmon_file_create",
                                    "sysmon_file_delete", "sysmon_dll_load"):
                    # These map to existing endpoints — put on special queue
                    # with a routing tag so the sender knows where to POST
                    try:
                        self.special_queue.put_nowait(ev)
                        special_count += 1
                    except queue.Full:
                        pass

                else:
                    # Inject/LSASS/DNS/pipe/registry/hollowing/ADS → /etw_events
                    try:
                        self.special_queue.put_nowait(ev)
                        special_count += 1
                    except queue.Full:
                        pass

            except Exception as e:
                log.debug("[SYSMON] Parse/enqueue error: %s", e)

        if process_count or special_count:
            log.debug(
                "[SYSMON] Enqueued %d process + %d special events",
                process_count, special_count
            )
