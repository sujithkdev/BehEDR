# BehEDR — Behavioral Endpoint Detection and Response

> Final Year Security Project — sujithkdev — 2026

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10+-blue)](https://python.org)
[![Platform](https://img.shields.io/badge/Platform-Windows-blue)](https://microsoft.com)

A custom-built Windows EDR system combining 13-layer behavioral detection,
Sysmon kernel-level telemetry, Machine Learning anomaly detection, and
automated incident response — built entirely in Python.

---

## What It Does

Monitors every Windows endpoint in real time and automatically detects
and responds to attacks — from ransomware to credential theft to process injection.

---

## Key Features

### Detection Engine — 13 Layers

| Layer | Method | Covers |
|---|---|---|
| L0a | Malware Hash DB (300k+ hashes) | Known malware — MalwareBazaar |
| L0b | YARA Signatures (7 rule packs) | Pattern-based detection |
| L0c | Threat Intel IOC Feed | Known bad IPs, domains, hashes |
| L1 | Exact Behaviour Chain Rules (YAML) | Office to PowerShell, macro droppers |
| L2 | Heuristic Weighted Scoring (YAML) | Encoded cmds, suspicious paths |
| L3 | Parent-Child Pair Analysis (YAML) | Unusual process relationships |
| L4 | MITRE ATT&CK Intent Classifier | Keyword-based technique mapping |
| L5 | Isolation Forest ML (23 features) | Unknown attack anomalies |
| L6 | Network Behaviour Analysis | C2 beaconing, lateral movement |
| L7 | File and Registry Behaviour | Ransomware, mass encryption |
| L8 | Process Masquerading (T1036.005) | Fake system processes |
| L9 | DLL Hijacking Detection | Unsigned DLLs from bad paths |
| L10 | Sequence ML | Recon-download-execute chains |

### Sysmon Kernel-Level Detection

- Ev8 CreateRemoteThread — process injection (T1055) — instant CRITICAL
- Ev10 LSASS memory access — credential dump (T1003.001) — instant CRITICAL
- Ev25 ProcessTampering — process hollowing (T1055.012) — instant CRITICAL
- Ev17/18 Named pipes — Cobalt Strike detection (T1559.001)
- Ev22 DNS queries — C2 domain and DGA scoring
- Ev12/13 Registry writes — persistence detection (T1547.001)

### Automated Response

- Network isolation via Windows Firewall (reversible)
- Process termination with protected process list
- Forensic snapshot collection
- Rate-limited auto-kill (max 3 per system per minute)
- 6 built-in response playbooks

### Intelligence and Learning

- Active Learning — analyst TP/FP feedback retrains ML models
- User Behavior Analytics — per-user behavioral baselines
- Explainable AI — every alert explains why it fired
- Hash DB auto-updates daily from MalwareBazaar and ThreatFox
- Threat enrichment — VirusTotal, AlienVault OTX, Shodan, Abuse.ch

---

## Quick Start

### Server

```bash
git clone https://github.com/sujithkdev/BehEDR.git
cd BehEDR
pip install -r requirements.txt
python generate_secrets.py --write
python run.py
```

### Agent (Windows endpoint — admin PowerShell)

```powershell
sysmon64.exe -accepteula -i sysmonconfig.xml
$env:EDR_SERVER_IP = "your-server-ip"
$env:EDR_API_KEY = "your-api-key"
python agent.py
```

### Docker

```bash
docker-compose up -d
```

---

## Project Structure

```
BehEDR/
├── agent.py                  Windows monitoring agent
├── sysmon_reader.py          Sysmon Event Log reader
├── sysmonconfig.xml          Sysmon driver configuration
├── run.py                    Server entry point
├── generate_secrets.py       Secure key generator
├── update_hash_db.py         Malware hash DB updater
├── server/
│   ├── core/
│   │   ├── detector.py       13-layer detection engine
│   │   ├── sysmon_handlers.py  Kernel-level detection
│   │   ├── uba.py            User behavior analytics
│   │   ├── explainable_ai.py   Why-did-this-fire context
│   │   └── active_learning.py  Feedback-driven retraining
│   ├── database/storage.py   SQLite persistence layer
│   ├── routes/api.py         REST API endpoints
│   ├── playbooks.py          Automated response engine
│   ├── notifier.py           Slack/Teams/Email/PagerDuty
│   └── wazuh_bridge.py       Wazuh integration
└── rules/
    ├── yara/                 YARA detection rules
    └── *.yaml                Behaviour chain rules
```

---

## Comparison

| Feature | BehEDR | Wazuh | CrowdStrike |
|---|---|---|---|
| ML Detection | YES | No | Yes (paid) |
| Explainable AI | YES | No | No |
| Kernel via Sysmon | YES | Yes | Yes |
| Active Learning | YES | No | No |
| User Behavior Analytics | YES | No | Yes (paid) |
| Cost | FREE | Free | $15/PC/month |
| Self-hosted | YES | Yes | No |

---

## Requirements

- Python 3.10+
- Windows 10/11 or Windows Server 2016+ (agent)
- Sysmon v15+
- 512 MB RAM minimum (server)
- scikit-learn, Flask, PyYAML, PyJWT, numpy

---

## Security Notice

This tool is built for educational research and authorized defensive security use only.
Unauthorized deployment on systems you do not own is illegal.
See [SECURITY.md](SECURITY.md) for responsible disclosure.

---

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE)

Copyright (C) 2026 sujithkdev
