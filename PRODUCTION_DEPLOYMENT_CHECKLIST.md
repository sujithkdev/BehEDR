# PRODUCTION DEPLOYMENT CHECKLIST
## BehEDR v4.0 - Pre-Production Security Hardening

**🚨 DO NOT DEPLOY WITHOUT COMPLETING ALL CRITICAL ITEMS 🚨**

---

## ✅ CRITICAL (Must Do - Takes 5 Minutes)

### 1. Generate Secure Secrets
```bash
# Generate strong API key (for agent authentication)
export EDR_API_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")

# Generate strong JWT secret (for user tokens)
export EDR_JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(48))")

# Generate strong Flask secret (for session cookies)
export FLASK_SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(48))")

# Save these to your environment permanently (.bashrc or deployment config)
echo "export EDR_API_KEY='<your_generated_key>'" >> ~/.bashrc
echo "export EDR_JWT_SECRET='<your_generated_secret>'" >> ~/.bashrc
echo "export FLASK_SECRET_KEY='<your_generated_secret>'" >> ~/.bashrc
```

### 2. Enable TLS/HTTPS
```bash
export EDR_TLS_ENABLED=true

# Optional: Provide your own certificates
export EDR_TLS_CERT=/path/to/your/cert.pem
export EDR_TLS_KEY=/path/to/your/key.pem

# If not provided, server auto-generates self-signed cert
```

### 3. Change Default Admin Password
```bash
# On first login, immediately change from "change-me-now" to a strong password
# Or use the API to create a new admin and delete the default one

curl -X POST http://localhost:5000/auth/users \
  -H "Authorization: Bearer <ADMIN_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"username":"youradmin","password":"<STRONG_PASSWORD>","role":"admin"}'

curl -X DELETE http://localhost:5000/auth/users/admin \
  -H "Authorization: Bearer <ADMIN_TOKEN>"
```

---

## ⚠️ HIGH PRIORITY (Strongly Recommended)

### 4. Configure Alerting Channels
```bash
# Slack
export EDR_NOTIFY_SLACK_URL="https://hooks.slack.com/services/YOUR/WEBHOOK/URL"

# Microsoft Teams
export EDR_NOTIFY_TEAMS_URL="https://outlook.office.com/webhook/YOUR/WEBHOOK/URL"

# Email (SMTP)
export EDR_NOTIFY_EMAIL_HOST="smtp.gmail.com"
export EDR_NOTIFY_EMAIL_PORT="587"
export EDR_NOTIFY_EMAIL_USER="your-email@gmail.com"
export EDR_NOTIFY_EMAIL_PASS="your-app-password"
export EDR_NOTIFY_EMAIL_TO="security-team@yourcompany.com"

# PagerDuty
export EDR_NOTIFY_PD_KEY="your-integration-key"
```

### 5. Use Production WSGI Server
```bash
# Don't use Flask dev server in production!
pip install waitress

# Start with waitress (Windows-native, production-ready)
waitress-serve --host=0.0.0.0 --port=5000 --threads=8 --call server:create_app

# Or on Linux, use gunicorn
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 "server:create_app()"
```

### 6. Configure VirusTotal (Optional but Recommended)
```bash
export VT_API_KEY="your-virustotal-api-key"
```

---

## 📋 MEDIUM PRIORITY (Should Do)

### 7. Fix Password Hashing
**Current:** SHA-256 (fast, less secure)  
**Recommended:** bcrypt

**File to Edit:** `server/auth.py`

```python
# Replace SHA-256 with bcrypt
import bcrypt

# When creating user:
hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt())

# When verifying:
if bcrypt.checkpw(password.encode(), stored_hash):
    # Valid password
```

### 8. Add Login Rate Limiting
**File to Edit:** `server/routes/api.py`

Add rate limiting decorator to `/auth/login` endpoint:
```python
from server.ratelimit import rate_limit

@api_bp.route("/auth/login", methods=["POST"])
@rate_limit(10, 60)  # 10 attempts per 60 seconds
def login():
    # existing code
```

### 9. Install Advanced ML (Optional)
```bash
# For Deep Learning features (L11-L13)
pip install tensorflow torch dgl

# Note: Increases memory usage but improves detection accuracy
```

### 10. Database Backup Schedule
The system auto-backs up every 24 hours. To customize:
```bash
export EDR_BACKUP_INTERVAL_HOURS=12  # Backup every 12 hours
```

Backups are stored in `data/backups/` - set up external backup sync:
```bash
# Example: Copy backups to network storage every hour
0 * * * * rsync -av /path/to/edr_clean/data/backups/ /mnt/backup/edr/
```

---

## 🧪 TESTING (Before Production)

### 11. Run Test Suite
```bash
# Smoke test (detection engine)
python smoke_test.py

# API tests
python test_api.py

# All existing tests
python test_final.py
```

### 12. Load Testing
```bash
# Simulate 100 agents
# (You'll need to create a load test script or use existing test_*.py files)

# Monitor performance:
# - CPU usage (should stay < 80%)
# - Memory growth (check for leaks)
# - Database size growth
# - API response times
```

### 13. Agent Deployment Test
```bash
# Deploy agent to 5-10 test machines first
# Verify:
# - Agent registers successfully
# - Heartbeats arrive every 15 seconds
# - Process events are captured
# - Alerts generate correctly
# - No false positives on normal activity
```

---

## 📊 MONITORING (Post-Deployment)

### 14. Set Up Health Monitoring
```bash
# Add a cron job to check /health endpoint
*/5 * * * * curl -f http://your-edr-server:5000/health || echo "EDR server down!" | mail -s "ALERT" admin@company.com
```

### 15. Watch Key Metrics
- Alert volume (normal baseline: 0-10 per hour)
- Alert storm protection activations
- Agent offline count
- Database size growth (>1GB per week may need PostgreSQL migration)
- API latency (should stay < 500ms)

### 16. Review Logs Daily
```bash
# Check server logs for errors
tail -f logs/*.log

# Check for authentication failures (potential attacks)
grep "401" logs/*.log
```

---

## 🔒 HARDENING (Additional Security)

### 17. Firewall Rules
```bash
# Only allow agents to connect to server
# Only allow admins to access dashboard

# Example iptables rules:
iptables -A INPUT -p tcp --dport 5000 -s 10.0.0.0/8 -j ACCEPT  # Internal network only
iptables -A INPUT -p tcp --dport 5000 -j DROP  # Block all external access
```

### 18. Network Segmentation
- Place EDR server in a secure VLAN
- Restrict dashboard access to SOC/security team IPs only
- Use VPN for remote access

### 19. Audit Log Review
```bash
# Review all admin actions
sqlite3 data/edr.db "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT 100;"
```

---

## 📝 DOCUMENTATION

### 20. Document Your Configuration
Create `production_config.md`:
```
EDR Server IP: <your_ip>
EDR Server Port: 5000
TLS Enabled: Yes
Agent API Key: <redacted>
Admin Users: user1, user2
Alerting: Slack (security-alerts channel)
Backup Schedule: Every 12 hours to /mnt/backup/edr/
```

### 21. Document Agent Deployment
Create `agent_deployment_guide.md`:
```
1. Copy agent.py to endpoint
2. Install: pip install psutil requests pywin32
3. Set: EDR_SERVER_IP=<your_server_ip>
4. Set: EDR_API_KEY=<your_api_key>
5. Set: EDR_TLS_ENABLED=true
6. Set: EDR_TLS_CA_CERT=<path_to_server_cert>
7. Run: python agent.py
8. Verify registration in dashboard
```

---

## ✅ FINAL VERIFICATION

Before declaring production-ready, verify:

- [ ] Server starts without warnings about default secrets
- [ ] HTTPS is enabled (check for padlock in browser)
- [ ] Admin password has been changed
- [ ] API key is NOT "123456789"
- [ ] At least one alerting channel is configured
- [ ] Health endpoint returns 200
- [ ] Test agent connects successfully
- [ ] Malicious test event generates CRITICAL alert
- [ ] Benign test event (VS Code) generates no alert
- [ ] Backup system is working (check data/backups/)
- [ ] Audit log is recording actions
- [ ] Dashboard loads without errors
- [ ] All tests pass (smoke_test.py, test_api.py)

---

## 🚀 PRODUCTION STARTUP

Once all critical items are complete:

```bash
# Final startup sequence
cd /path/to/edr_clean

# Verify environment
python -c "import os; print('API_KEY length:', len(os.environ.get('EDR_API_KEY','')))"  # Should be > 32
python -c "import os; print('TLS:', os.environ.get('EDR_TLS_ENABLED','false'))"  # Should be 'true'

# Start server (use production WSGI, not run.py)
waitress-serve --host=0.0.0.0 --port=5000 --threads=8 --call server:create_app

# Monitor startup logs
# Look for:
# - "HTTPS enabled" (not HTTP)
# - No warnings about default secrets
# - "13-layer detection engine ready"
# - No Python errors

# Test health
curl -k https://your-server:5000/health

# Deploy agents to endpoints
# (Use your configuration management: Ansible, SCCM, GPO, etc.)
```

---

## 🆘 TROUBLESHOOTING

**Server won't start:**
- Check all environment variables are set
- Verify Python dependencies: `pip install -r requirements.txt`
- Check logs in `logs/` directory

**Agents can't connect:**
- Verify EDR_SERVER_IP matches actual server IP
- Check firewall allows port 5000
- Verify API key matches between agent and server
- If TLS enabled, agent needs EDR_TLS_CA_CERT set

**No alerts generating:**
- Check agent is sending events: `curl http://server:5000/api/events -H "Authorization: Bearer <token>"`
- Verify detection rules loaded: Look for "[RULES] L1:68 chains" in startup logs
- Test with known malicious pattern: `simulate_attack.py`

**Too many false positives:**
- Review alerts and create suppressions: POST /api/suppressions
- Adjust heuristic weights in `rules/heuristic_weights.yaml`
- Add safe process pairs to `rules/parent_child_analysis.yaml`

---

## 📞 SUPPORT

**Log Files:**
- Server: `logs/*.log`
- Agent: `agent.log` (on each endpoint)

**Database:**
- Location: `data/edr.db`
- Backup: `data/backups/`

**Useful Queries:**
```sql
-- Recent alerts
SELECT * FROM alerts ORDER BY timestamp DESC LIMIT 50;

-- Systems by alert count
SELECT system_id, COUNT(*) FROM alerts GROUP BY system_id ORDER BY COUNT(*) DESC;

-- Critical alerts only
SELECT * FROM alerts WHERE risk_level='CRITICAL' ORDER BY timestamp DESC;
```

---

**✅ Checklist complete? You're ready for production!**

**🚨 If ANY critical item is incomplete → DO NOT DEPLOY**

---

*This checklist was generated from QA audit findings on 2026-08-15*  
*See QA_AUDIT_REPORT.md for detailed test results and security analysis*
