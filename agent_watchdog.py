# -*- coding: utf-8 -*-
"""
agent_watchdog.py — Agent Watchdog Wrapper
==========================================
Monitors the EDR agent process and automatically restarts it on crash.

SETUP:
  1. Configure agent.py with your server settings first
  2. Run this instead of agent.py:
     python agent_watchdog.py

The watchdog will:
  • Start agent.py as a subprocess
  • Monitor its health via stdout/stderr
  • Restart automatically on crash or hang
  • Log all restarts for audit
  • Exponential backoff on rapid failures (prevents boot loop)

Production deployment:
  • Windows Service: Use NSSM to wrap this script as a service
  • Linux systemd: Create a systemd unit (use Restart=always)
  • Task Scheduler: Create a task that runs this on startup
"""

import os
import sys
import time
import subprocess
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler

# ── Configuration ─────────────────────────────────────────────────────────────
AGENT_SCRIPT = os.path.join(os.path.dirname(__file__), "agent.py")
MAX_RAPID_RESTARTS = 5      # Max restarts within RESTART_WINDOW before backoff
RESTART_WINDOW = 300        # seconds (5 minutes)
BACKOFF_SECONDS = 60        # Delay after rapid restart threshold
HEALTH_CHECK_INTERVAL = 30  # seconds between health checks
RESTART_DELAY = 3           # seconds to wait before restart

# ── Logging ───────────────────────────────────────────────────────────────────
_LOG_FILE = os.path.join(os.path.dirname(__file__), "watchdog.log")
logging.basicConfig(
    handlers=[
        RotatingFileHandler(_LOG_FILE, maxBytes=10*1024*1024, backupCount=5, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("watchdog")

# ── Restart tracking ──────────────────────────────────────────────────────────
_restart_times = []


def _should_backoff() -> bool:
    """Return True if restart rate is too high (circuit breaker)."""
    global _restart_times
    now = time.time()
    # Remove restarts outside the window
    _restart_times = [t for t in _restart_times if now - t < RESTART_WINDOW]
    
    if len(_restart_times) >= MAX_RAPID_RESTARTS:
        log.warning(
            f"⚠️  {len(_restart_times)} restarts in {RESTART_WINDOW}s — "
            f"backing off for {BACKOFF_SECONDS}s to prevent boot loop"
        )
        return True
    return False


def _record_restart():
    """Record a restart timestamp."""
    global _restart_times
    _restart_times.append(time.time())


def _start_agent():
    """Start agent.py as a subprocess with output forwarding."""
    log.info(f"Starting agent: {AGENT_SCRIPT}")
    try:
        proc = subprocess.Popen(
            [sys.executable, AGENT_SCRIPT],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            bufsize=1,  # Line buffered
        )
        return proc
    except Exception as e:
        log.error(f"Failed to start agent: {e}")
        return None


def _monitor_agent(proc):
    """
    Monitor agent process health.
    Returns True if process exited cleanly, False if crashed.
    """
    last_output = time.time()
    
    try:
        # Forward agent output to watchdog log
        for line in iter(proc.stdout.readline, ""):
            if line:
                # Strip newline and log
                log.info(f"[AGENT] {line.rstrip()}")
                last_output = time.time()
            
            # Check if process has exited
            if proc.poll() is not None:
                break
            
            # Hang detection (no output for 5 minutes)
            if time.time() - last_output > 300:
                log.warning("⚠️  Agent appears hung (no output for 5 min) — restarting")
                proc.terminate()
                time.sleep(3)
                if proc.poll() is None:
                    proc.kill()
                return False
        
        # Process exited - check return code
        return_code = proc.wait()
        if return_code == 0:
            log.info("Agent exited cleanly (code 0)")
            return True
        else:
            log.error(f"Agent crashed with exit code {return_code}")
            return False
            
    except KeyboardInterrupt:
        log.info("Watchdog interrupted by user — stopping agent")
        proc.terminate()
        proc.wait(timeout=10)
        raise
    except Exception as e:
        log.error(f"Monitor error: {e}")
        return False


def main():
    """Main watchdog loop."""
    log.info("=" * 70)
    log.info("EDR Agent Watchdog Started")
    log.info(f"Monitoring: {AGENT_SCRIPT}")
    log.info(f"Max restarts: {MAX_RAPID_RESTARTS} per {RESTART_WINDOW}s")
    log.info("=" * 70)
    
    if not os.path.exists(AGENT_SCRIPT):
        log.error(f"❌ Agent script not found: {AGENT_SCRIPT}")
        sys.exit(1)
    
    restart_count = 0
    
    while True:
        try:
            # Check if we should back off due to rapid restarts
            if _should_backoff():
                log.info(f"Backing off for {BACKOFF_SECONDS}s...")
                time.sleep(BACKOFF_SECONDS)
                _restart_times.clear()  # Reset after backoff
            
            # Start the agent
            proc = _start_agent()
            if proc is None:
                log.error("Failed to start agent — retrying in 30s")
                time.sleep(30)
                continue
            
            log.info(f"Agent started (PID {proc.pid})")
            
            # Monitor until it exits
            clean_exit = _monitor_agent(proc)
            
            if clean_exit:
                log.info("Agent exited cleanly — stopping watchdog")
                break
            
            # Crashed - prepare to restart
            restart_count += 1
            _record_restart()
            
            log.warning(f"⚠️  Agent crashed (restart #{restart_count})")
            log.info(f"Restarting in {RESTART_DELAY}s...")
            time.sleep(RESTART_DELAY)
            
        except KeyboardInterrupt:
            log.info("Watchdog stopped by user")
            break
        except Exception as e:
            log.error(f"Watchdog error: {e}")
            time.sleep(10)
    
    log.info("Watchdog exiting")


if __name__ == "__main__":
    main()
