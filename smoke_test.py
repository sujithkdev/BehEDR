"""
smoke_test.py — Quick functional validation of the detection engine.
Run: python smoke_test.py
Cleans up after itself (temp DB, no side effects on real data).
"""
import os, sys, tempfile, shutil

tmpdir = tempfile.mkdtemp()
os.environ['EDR_API_KEY']    = 'test-key-smoke'
os.environ['EDR_JWT_SECRET'] = 'test-jwt-secret-32-chars-minimum!!'

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import server.config as cfg
cfg.DB_PATH       = os.path.join(tmpdir, 'test.db')
cfg.DATA_DIR      = tmpdir
cfg.LOG_DIR       = tmpdir
cfg.ML_MODEL_PATH = os.path.join(tmpdir, 'ml_model.joblib')
os.makedirs(cfg.DATA_DIR, exist_ok=True)
os.makedirs(cfg.LOG_DIR, exist_ok=True)

from server.database.storage import init_db
init_db()

from server.core.detector import initialize, analyze
initialize()

print()
print("=" * 65)
print("  BehEDR Detection Engine — Smoke Test")
print("=" * 65)
print()

TEST_CASES = [
    {
        "name": "L1 — Office spawns encoded PowerShell (phishing)",
        "event": {
            "system_id": "TEST-PC", "event": "process_spawn",
            "parent": "winword.exe", "child": "powershell.exe",
            "pid": 1001,
            "cmdline": "-nop -w hidden -enc SQBFAFgAIAAoAE4AZQB3AC0ATwBiAGoAZQBjAHQA",
            "sha256": "", "exe_path": "c:\\windows\\system32\\windowspowershell\\v1.0\\powershell.exe",
            "time": 1700000000,
        },
        "expect_min_score": 85,
        "expect_level": "CRITICAL",
    },
    {
        "name": "L1 — Shadow copy deletion (ransomware prep)",
        "event": {
            "system_id": "TEST-PC", "event": "process_spawn",
            "parent": "cmd.exe", "child": "vssadmin.exe",
            "pid": 1002,
            "cmdline": "vssadmin.exe delete shadows /all /quiet",
            "sha256": "", "exe_path": "c:\\windows\\system32\\vssadmin.exe",
            "time": 1700000001,
        },
        "expect_min_score": 85,
        "expect_level": "CRITICAL",
    },
    {
        "name": "L1 — Credential dump (mimikatz cmdline)",
        "event": {
            "system_id": "TEST-PC", "event": "process_spawn",
            "parent": "cmd.exe", "child": "powershell.exe",
            "pid": 1003,
            "cmdline": "sekurlsa::logonpasswords",
            "sha256": "", "exe_path": "c:\\windows\\system32\\windowspowershell\\v1.0\\powershell.exe",
            "time": 1700000002,
        },
        "expect_min_score": 85,
    },
    {
        "name": "L8 — Process masquerading (svchost from Temp)",
        "event": {
            "system_id": "TEST-PC", "event": "process_spawn",
            "parent": "explorer.exe", "child": "svchost.exe",
            "pid": 9999,
            "cmdline": "c:\\users\\user\\appdata\\local\\temp\\svchost.exe",
            "sha256": "", "exe_path": "c:\\users\\user\\appdata\\local\\temp\\svchost.exe",
            "time": 1700000003,
        },
        "expect_min_score": 90,
    },
    {
        "name": "L2 heuristic cap — must stay <= 84 (not CRITICAL alone)",
        # This cmdline has multiple L2 signals stacking but NO exact L1 pattern match.
        # Signals: downloadstring (35) + webclient (10) + -nop (8) + suspicious_path (18)
        # = ~71 raw → after trusted-path discount stays well under 84.
        # No -enc, no IEX cradle, no 'bypass' keyword, no office parent → no L1 rule fires.
        # Validates that heuristic accumulation alone never reaches CRITICAL (>=85).
        "event": {
            "system_id": "TEST-PC", "event": "process_spawn",
            "parent": "explorer.exe", "child": "powershell.exe",
            "pid": 2001,
            "cmdline": "-nop -noprofile downloadstring( webclient c:\\users\\public\\run.ps1",
            "sha256": "", "exe_path": "c:\\windows\\system32\\windowspowershell\\v1.0\\powershell.exe",
            "time": 1700000004,
        },
        "expect_max_score": 84,   # heuristics alone must never reach CRITICAL
    },
    {
        "name": "Whitelist — Normal boot chain (no alert)",
        "event": {
            "system_id": "TEST-PC", "event": "process_spawn",
            "parent": "services.exe", "child": "svchost.exe",
            "pid": 500,
            "cmdline": "c:\\windows\\system32\\svchost.exe -k netsvcs",
            "sha256": "", "exe_path": "c:\\windows\\system32\\svchost.exe",
            "time": 1700000005,
        },
        "expect_no_alert": True,
    },
    {
        "name": "Whitelist — VS Code spawning node (no alert)",
        "event": {
            "system_id": "TEST-PC", "event": "process_spawn",
            "parent": "code.exe", "child": "node.exe",
            "pid": 3001,
            "cmdline": "node.exe c:\\users\\dev\\.vscode\\extensions\\eslint.js",
            "sha256": "", "exe_path": "c:\\program files\\nodejs\\node.exe",
            "time": 1700000006,
        },
        "expect_no_alert": True,
    },
]

passed = 0
failed = 0

for tc in TEST_CASES:
    name   = tc["name"]
    alerts = analyze(tc["event"])

    # ── no-alert cases ──────────────────────────────────────────
    if tc.get("expect_no_alert"):
        if not alerts:
            print(f"  PASS  {name}")
            passed += 1
        else:
            scores = [a["risk_score"] for a in alerts]
            levels = [a["risk_level"] for a in alerts]
            print(f"  FAIL  {name}")
            print(f"        Got {len(alerts)} alert(s): scores={scores} levels={levels}")
            for a in alerts:
                print(f"        reason: {a['reason'][:80]}")
            failed += 1
        continue

    # ── alert cases ─────────────────────────────────────────────
    if not alerts:
        print(f"  FAIL  {name}  — no alerts generated")
        failed += 1
        continue

    max_score = max(a["risk_score"] for a in alerts)
    top_alert = max(alerts, key=lambda a: a["risk_score"])
    level     = top_alert["risk_level"]
    reason    = top_alert["reason"][:70]

    min_ok  = max_score >= tc.get("expect_min_score", 0)
    max_ok  = max_score <= tc.get("expect_max_score", 999)
    lvl_ok  = (level == tc["expect_level"]) if "expect_level" in tc else True

    if min_ok and max_ok and lvl_ok:
        print(f"  PASS  {name}")
        print(f"        score={max_score}  level={level}  alerts={len(alerts)}")
        print(f"        {reason}")
        passed += 1
    else:
        print(f"  FAIL  {name}")
        min_exp = tc.get("expect_min_score", "-")
        max_exp = tc.get("expect_max_score", "-")
        lvl_exp = tc.get("expect_level", "-")
        print(f"        score={max_score} (min={min_exp} max={max_exp})  level={level} (expected={lvl_exp})")
        print(f"        {reason}")
        failed += 1

    print()

print("=" * 65)
print(f"  Result: {passed} passed, {failed} failed out of {len(TEST_CASES)}")
print("=" * 65)
print()

shutil.rmtree(tmpdir, ignore_errors=True)
sys.exit(0 if failed == 0 else 1)
