"""
simulate_attack.py — EDR Malware Attack Simulator
==================================================
Simulates real-world malware behaviour patterns so the EDR agent
detects and reports them in the dashboard.

Patterns simulated:
  1. Reconnaissance      — whoami, ipconfig, net user, systeminfo
  2. Lateral movement    — net view, ping sweep, netstat
  3. Persistence         — schtasks query, registry Run key reads
  4. Credential access   — cmdkey /list, lsass query (read-only)
  5. C2 beacon mimic     — repeated HTTP requests to external host
  6. Living-off-the-land — encoded PowerShell (benign payload)
  7. Process storm       — rapid child process spawns (injection mimic)

All commands are READ-ONLY / harmless — nothing is written or deleted.

Usage:
    python simulate_attack.py              # run all 7 stages
    python simulate_attack.py --stage 3   # run only stage 3
    python simulate_attack.py --delay 1   # 1s between stages
"""

import subprocess, time, sys, os, argparse, base64

RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"


def banner(text, colour=CYAN):
    print(f"\n{colour}{BOLD}{'='*60}{RESET}")
    print(f"{colour}{BOLD}  {text}{RESET}")
    print(f"{colour}{BOLD}{'='*60}{RESET}")


def step(stage, name):
    print(f"\n{YELLOW}[STAGE {stage}]{RESET} {BOLD}{name}{RESET}")


def run(cmd, shell=True, timeout=8):
    print(f"  {CYAN}>{RESET} {cmd}")
    try:
        r = subprocess.run(
            cmd, shell=shell, capture_output=True, text=True,
            timeout=timeout, errors="replace"
        )
        out = (r.stdout or r.stderr or "").strip()
        if out:
            lines = out.splitlines()[:5]
            for line in lines:
                print(f"    {line}")
            if len(out.splitlines()) > 5:
                print(f"    ... (+{len(out.splitlines())-5} more lines)")
    except subprocess.TimeoutExpired:
        print(f"  {YELLOW}[timeout]{RESET}")
    except Exception as e:
        print(f"  {RED}[error: {e}]{RESET}")


# ── Attack stages ──────────────────────────────────────────────────────────────

def stage1_recon():
    """MITRE T1082/T1033 — host fingerprinting."""
    step(1, "Reconnaissance — Host Fingerprinting")
    time.sleep(0.5)
    for cmd in ["whoami", "hostname", "ipconfig /all", "systeminfo",
                "net user", "net localgroup administrators"]:
        run(cmd)
        time.sleep(0.3)
    print(f"  {GREEN}✔ Recon events sent to agent{RESET}")


def stage2_lateral_movement():
    """MITRE T1018/T1049 — network discovery."""
    step(2, "Lateral Movement Prep — Network Discovery")
    time.sleep(0.5)
    for cmd in ["net view", "arp -a", "netstat -an"]:
        run(cmd)
        time.sleep(0.3)
    for octet in range(1, 6):
        run(f"ping -n 1 -w 200 192.168.1.{octet}", timeout=3)
        time.sleep(0.1)
    print(f"  {GREEN}✔ Lateral movement events sent{RESET}")


def stage3_persistence():
    """MITRE T1053/T1547 — read-only persistence discovery."""
    step(3, "Persistence — Scheduled Task & Registry Discovery")
    time.sleep(0.5)
    for cmd in [
        "schtasks /query /fo LIST /v",
        r"reg query HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
        r"reg query HKLM\Software\Microsoft\Windows\CurrentVersion\Run",
    ]:
        run(cmd)
        time.sleep(0.3)
    print(f"  {GREEN}✔ Persistence-related events sent{RESET}")


def stage4_credential_access():
    """MITRE T1555 — read-only credential store query."""
    step(4, "Credential Access — Stored Credentials")
    time.sleep(0.5)
    for cmd in ["cmdkey /list", "net accounts", 'tasklist /fi "IMAGENAME eq lsass.exe"']:
        run(cmd)
        time.sleep(0.3)
    print(f"  {GREEN}✔ Credential access events sent{RESET}")


def stage5_c2_beacon():
    """MITRE T1071 — repeated outbound HTTP calls."""
    step(5, "C2 Beacon Simulation — Repeated Outbound Requests")
    time.sleep(0.5)
    for i in range(1, 4):
        print(f"  Beacon #{i}…")
        run("curl -s --max-time 3 http://httpbin.org/get", timeout=5)
        time.sleep(1)
    run(
        'powershell -Command "Invoke-WebRequest -Uri http://httpbin.org/get '
        '-UseBasicParsing -TimeoutSec 3 | Select-Object StatusCode"',
        timeout=6,
    )
    print(f"  {GREEN}✔ C2 beacon events sent{RESET}")


def stage6_lolbin():
    """MITRE T1059.001/T1140 — encoded PowerShell (benign payload)."""
    step(6, "Living-off-the-Land — Encoded PowerShell")
    time.sleep(0.5)
    ps_cmd  = "Write-Output 'EDR-Test-Beacon-Safe'"
    encoded = base64.b64encode(ps_cmd.encode("utf-16-le")).decode()
    run(f"powershell -EncodedCommand {encoded}")
    time.sleep(0.3)
    run("where wscript")
    run("where cscript")
    print(f"  {GREEN}✔ LOLBin events sent{RESET}")


def stage7_process_storm():
    """MITRE T1055 pattern — rapid child process spawning."""
    step(7, "Process Storm — Rapid Child Process Spawn (Injection Mimic)")
    time.sleep(0.5)
    print("  Spawning burst of 10 child processes…")
    procs = []
    for i in range(10):
        p = subprocess.Popen(
            ["cmd.exe", "/c", f"echo EDR-STORM-{i}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        procs.append(p)
        time.sleep(0.05)
    for p in procs:
        try:
            p.wait(timeout=3)
        except Exception:
            p.kill()
    print(f"  {GREEN}✔ Process storm: 10 rapid spawns sent to agent{RESET}")


# ── Main ───────────────────────────────────────────────────────────────────────

STAGES = {
    1: stage1_recon,
    2: stage2_lateral_movement,
    3: stage3_persistence,
    4: stage4_credential_access,
    5: stage5_c2_beacon,
    6: stage6_lolbin,
    7: stage7_process_storm,
}


def main():
    os.system("")   # enable ANSI on Windows 10+

    parser = argparse.ArgumentParser(description="EDR Attack Simulator")
    parser.add_argument("--stage", type=int, choices=STAGES.keys(),
                        help="Run only one stage (1-7). Default: all.")
    parser.add_argument("--delay", type=float, default=2.0,
                        help="Seconds between stages (default: 2)")
    args = parser.parse_args()

    banner("EDR Malware Attack Simulator v1.0", RED)
    print(f"""
  {YELLOW}PURPOSE:{RESET}  Simulate real malware patterns for EDR testing.
  {YELLOW}SAFE:{RESET}     All commands are READ-ONLY. Nothing is installed or deleted.
  {YELLOW}WATCH:{RESET}    Keep the EDR dashboard open — events appear within 2-4 s.
    """)

    if args.stage:
        STAGES[args.stage]()
    else:
        keys = sorted(STAGES.keys())
        print(f"  Running all {len(keys)} stages with {args.delay}s delay between each.\n")
        for i, num in enumerate(keys):
            STAGES[num]()
            if i < len(keys) - 1:
                print(f"\n  {CYAN}Waiting {args.delay}s…{RESET}")
                time.sleep(args.delay)

    banner("Simulation Complete — Check your EDR Dashboard!", GREEN)
    print(f"""
  {YELLOW}What to verify:{RESET}
  ✦ Live Events feed shows whoami, ipconfig, powershell, cmd (×10 storm).
  ✦ Alerts fire for encoded PowerShell (Stage 6) and process storm (Stage 7).
  ✦ Your system appears in the Monitored Systems panel.
    """)


if __name__ == "__main__":
    main()
