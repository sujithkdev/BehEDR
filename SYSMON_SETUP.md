# Sysmon Integration Setup Guide

This guide walks you through adding kernel-level visibility to your EDR
deployment using Microsoft Sysinternals Sysmon.

---

## What This Adds

| Without Sysmon | With Sysmon |
|---|---|
| Process spawns via psutil (2s poll — misses short-lived processes) | Every process create instantly, kernel-level |
| No credential dump detection | LSASS access alerts (Mimikatz, ProcDump) |
| No injection detection | CreateRemoteThread alerts (Cobalt Strike, Meterpreter) |
| No DNS query visibility | Per-process DNS queries — C2 domain detection |
| No process hollowing detection | Kernel-level hollowing/herpaderping (Ev25) |
| No named pipe visibility | Cobalt Strike default pipe name detection |
| DLL monitoring for LOLBins only | Every unsigned DLL load from suspicious paths |
| No ADS detection | Alternate Data Stream creation alerts |
| Registry polling only | Real-time registry persistence key writes |

---

## Requirements

- **Windows Vista or later** (Sysmon works on all modern Windows versions)
- **Administrator privileges** for install/uninstall
- **No reboot required** for install, config update, or uninstall
- Sysmon does NOT require any Python packages — `wevtutil.exe` is built into Windows

---

## Step 1 — Download Sysmon

Download from the official Microsoft Sysinternals page:

```
https://learn.microsoft.com/en-us/sysinternals/downloads/sysmon
```

Direct download link (always latest):
```
https://download.sysinternals.com/files/Sysmon.zip
```

Extract and place `sysmon64.exe` (64-bit Windows) alongside your `agent.py`.

---

## Step 2 — Install Sysmon

Open **PowerShell as Administrator** and run:

```powershell
# Install with the EDR-tuned config
.\sysmon64.exe -accepteula -i sysmonconfig.xml
```

Expected output:
```
System Monitor v15.x - System activity monitor
Copyright (C) 2014-2024 Mark Russinovich and Thomas Garnier
...
Sysmon64 installed.
SysmonDrv installed.
```

Verify it is running:
```powershell
Get-Service Sysmon64
# Status should be: Running
```

Verify events are flowing:
```powershell
Get-WinEvent -LogName "Microsoft-Windows-Sysmon/Operational" -MaxEvents 5 |
  Select-Object TimeCreated, Id, Message
```

---

## Step 3 — Start the Agent

No changes needed to your agent startup command. The agent auto-detects
Sysmon and starts the reader thread:

```powershell
# Set your server details
$env:EDR_SERVER_IP = "192.168.1.10"
$env:EDR_API_KEY   = "your-api-key-here"
$env:EDR_TLS_ENABLED = "true"
$env:EDR_TLS_CA_CERT = "C:\path\to\data\server.crt"

python agent.py
```

When Sysmon is installed you will see this in the agent log:

```
[SYSMON] ✅ Sysmon reader active — kernel-level visibility ENABLED
[SYSMON] Reader started (channel: Microsoft-Windows-Sysmon/Operational)
[SYSMON] Reading new events every 2s.
```

When Sysmon is NOT installed:
```
[SYSMON] Sysmon not installed on this machine.
         Install it for kernel-level visibility:
         sysmon64.exe -accepteula -i sysmonconfig.xml
```

---

## Step 4 — Verify Detection is Working

### Test LSASS access detection (Ev10)

On the monitored machine (admin PowerShell):
```powershell
# This generates an Ev10 event — do NOT run on production
$lsass = Get-Process lsass
$handle = [System.Diagnostics.Process]::GetCurrentProcess()
```

Or use ProcExp64.exe to open lsass — you will see a HIGH or CRITICAL alert
in the dashboard within 2 seconds.

### Test process injection detection (Ev8)

Any debugger attach generates Ev8. The config excludes devenv.exe/msvsmon.exe
so Visual Studio debugger does not fire false positives.

### Test DNS detection (Ev22)

```powershell
# Run from PowerShell — this generates Ev22 (powershell.exe DNS query)
Resolve-DnsName -Name "suspicious-domain.xyz" -Type A
```

You will see a MEDIUM or HIGH alert for the `.xyz` TLD from a PowerShell process.

### Test named pipe detection (Ev17)

```powershell
# Creates a named pipe matching Cobalt Strike patterns (Ev17)
$pipe = [System.IO.Pipes.NamedPipeServerStream]::new("msagent_test")
$pipe.Dispose()
```

You will see a CRITICAL alert: `COBALT STRIKE PIPE`.

---

## How the Data Flows

```
Windows Kernel
    │
    ▼ (Sysmon driver hooks)
Sysmon Service
    │
    ▼
Windows Event Log
Microsoft-Windows-Sysmon/Operational
    │
    ▼ (wevtutil qe, every 2s — no extra deps)
sysmon_reader.py  ← runs as SysmonReader thread inside agent.py
    │
    ├─ Ev1  ProcessCreate  ──────────────────► main _event_queue
    │                                            │
    │                                            ▼
    │                                         _sender_loop → POST /events
    │                                            │
    │                                            ▼
    │                                         13-layer detection engine
    │
    └─ Ev3,7,8,10,11,12,13,15,17,18,22,23,25 ► special_queue
                                                │
                                                ▼
                                         _sysmon_sender_loop
                                                │
                                   ┌────────────┼────────────┐
                                   ▼            ▼            ▼
                             /net_events   /dll_events  /etw_events
                                   │            │            │
                                   ▼            ▼            ▼
                             L6 beaconing  L9 hijacking  sysmon_handlers.py
                                                              │
                                               ┌─────────────┼─────────────┐
                                               ▼             ▼             ▼
                                        handle_inject  handle_lsass  handle_dns
                                        handle_pipe    handle_hollow handle_ads
                                        handle_registry              handle_dll
```

---

## Sysmon Events → EDR Detection Mapping

| Sysmon Ev | Name | EDR Handler | MITRE | Alert Level |
|---|---|---|---|---|
| 1 | ProcessCreate | Full 13-layer analyze() | T1059 / various | Any |
| 3 | NetworkConnect | handle_sysmon_network → L6 | T1071 | HIGH/CRITICAL |
| 7 | ImageLoad | handle_sysmon_dll → L9 | T1574.001 | HIGH/CRITICAL |
| 8 | CreateRemoteThread | handle_sysmon_inject | T1055 | **CRITICAL** |
| 10 | ProcessAccess | handle_sysmon_lsass | T1003.001 | **CRITICAL** |
| 11 | FileCreate | add_file_event → L7 | T1486 | HIGH/CRITICAL |
| 12/13 | Registry | handle_sysmon_registry | T1547.001 | HIGH/CRITICAL |
| 15 | FileCreateStreamHash | handle_sysmon_ads | T1564.004 | HIGH |
| 17/18 | PipeEvent | handle_sysmon_pipe | T1559.001 | CRITICAL |
| 22 | DnsQuery | handle_sysmon_dns | T1071.001 | MEDIUM–CRITICAL |
| 23 | FileDelete | add_file_event → L7 | T1485 | HIGH/CRITICAL |
| 25 | ProcessTampering | handle_sysmon_hollowing | T1055.012 | **CRITICAL** |

---

## Updating the Sysmon Configuration

After any changes to `sysmonconfig.xml`:

```powershell
# Apply new config — no restart needed
.\sysmon64.exe -c sysmonconfig.xml
```

---

## Tuning for Your Environment

### Reducing false positives

Add exclusions to `sysmonconfig.xml` for legitimate tools in your environment.

Example — exclude your corporate AV scanner from Ev7 (ImageLoad):
```xml
<ImageLoad onmatch="exclude">
  <Image condition="begin with">C:\Program Files\YourAV\</Image>
</ImageLoad>
```

Example — exclude a trusted deployment tool from Ev1:
```xml
<ProcessCreate onmatch="exclude">
  <Image condition="is">C:\Program Files\YourTool\deploy.exe</Image>
</ProcessCreate>
```

Example — whitelist a LOLBin caller in the agent's `config.py`
`PROCESS_WHITELIST` if Ev1 events from it are expected:
```python
("yourcorp_deploy.exe", "rundll32.exe"),
```

### Reducing CPU/disk impact

If event volume is too high on busy servers:

1. Narrow Ev3 (NetworkConnect) to only lateral movement ports
2. Narrow Ev7 (ImageLoad) to only `<Signed condition="is">false</Signed>`
3. Increase `POLL_INTERVAL` in `sysmon_reader.py` (default 2s → 5s on servers)

---

## Uninstalling Sysmon

```powershell
# Graceful uninstall
.\sysmon64.exe -u

# Force uninstall (if service is stuck)
.\sysmon64.exe -u force
```

The agent degrades gracefully — psutil monitoring continues without Sysmon.

---

## Troubleshooting

### "wevtutil.exe not found"
This should not happen on any Windows machine since Vista. If it does:
```powershell
where.exe wevtutil
# Should return: C:\Windows\System32\wevtutil.exe
```

### No events flowing / SysmonReader silent

Check Sysmon service:
```powershell
Get-Service Sysmon64 | Select-Object Status, StartType
# Both should be Running / Automatic
```

Check channel has events:
```powershell
(Get-WinEvent -LogName "Microsoft-Windows-Sysmon/Operational" -MaxEvents 1).Count
# Should be 1
```

Check agent log for SysmonReader thread:
```
[SYSMON] Reader started (channel: Microsoft-Windows-Sysmon/Operational)
```

### LSASS access alerts not firing

Confirm Ev10 is reaching the event log:
```powershell
Get-WinEvent -LogName "Microsoft-Windows-Sysmon/Operational" |
  Where-Object { $_.Id -eq 10 } | Select-Object -First 3
```

If no Ev10 events appear, check your `sysmonconfig.xml` has the ProcessAccess
include rule for `lsass.exe` and re-apply with `sysmon64.exe -c sysmonconfig.xml`.

### High CPU from SysmonDrv

Most common cause: Ev7 (ImageLoad) logging too much. Narrow the include rules
in the `ImageLoad` section of `sysmonconfig.xml`, then re-apply.

---

## Files Added by This Integration

| File | Purpose |
|---|---|
| `sysmon_reader.py` | Agent-side Sysmon Event Log reader |
| `sysmonconfig.xml` | Sysmon driver configuration (install with `-i`) |
| `server/core/sysmon_handlers.py` | Server-side detection for Sysmon events |
| `SYSMON_SETUP.md` | This guide |

**Modified files:**

| File | Change |
|---|---|
| `agent.py` | Added `_sysmon_start()`, `_sysmon_sender_loop()`, `SysmonSender` thread |
| `server/core/detector.py` | Added `sysmon_` dispatch in `handle_etw_event()` |
| `server/routes/api.py` | Updated `/etw_events` docstring |
