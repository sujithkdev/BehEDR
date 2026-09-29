"""
Comprehensive API Testing Script
Tests all major API endpoints for the EDR system
"""
import requests
import json
import time

BASE_URL = "http://localhost:5000"
API_KEY = "123456789"

def test_health():
    """Test health endpoint"""
    print("\n[TEST] Health Endpoint")
    r = requests.get(f"{BASE_URL}/health")
    print(f"  Status: {r.status_code}")
    print(f"  Response: {r.json()}")
    return r.status_code == 200

def test_auth_login():
    """Test authentication"""
    print("\n[TEST] Authentication - Login")
    r = requests.post(
        f"{BASE_URL}/auth/login",
        json={"username": "admin", "password": "change-me-now"}
    )
    print(f"  Status: {r.status_code}")
    data = r.json()
    print(f"  Response: {data}")
    if r.status_code == 200 and "token" in data:
        print(f"  ✅ JWT Token received")
        return data["token"]
    else:
        print(f"  ❌ Authentication failed")
        return None

def test_auth_me(token):
    """Test /auth/me endpoint"""
    print("\n[TEST] Get Current User")
    r = requests.get(
        f"{BASE_URL}/auth/me",
        headers={"Authorization": f"Bearer {token}"}
    )
    print(f"  Status: {r.status_code}")
    print(f"  Response: {r.json()}")
    return r.status_code == 200

def test_api_status(token):
    """Test dashboard status endpoint"""
    print("\n[TEST] Dashboard Status")
    r = requests.get(
        f"{BASE_URL}/api/status",
        headers={"Authorization": f"Bearer {token}"}
    )
    print(f"  Status: {r.status_code}")
    data = r.json()
    print(f"  Systems: {len(data.get('systems', []))}")
    print(f"  Alerts: {data.get('alert_count', 0)}")
    print(f"  Events: {data.get('event_count', 0)}")
    return r.status_code == 200

def test_agent_register():
    """Test agent registration"""
    print("\n[TEST] Agent Registration")
    r = requests.post(
        f"{BASE_URL}/register",
        headers={"API-Key": API_KEY},
        json={
            "system_id": "TEST-QA-PC",
            "version": "3.0",
            "os": "Windows 11 Pro",
            "python_version": "3.11.0"
        }
    )
    print(f"  Status: {r.status_code}")
    print(f"  Response: {r.json()}")
    return r.status_code == 200

def test_agent_heartbeat():
    """Test agent heartbeat"""
    print("\n[TEST] Agent Heartbeat")
    r = requests.post(
        f"{BASE_URL}/heartbeat",
        headers={"API-Key": API_KEY},
        json={
            "system_id": "TEST-QA-PC",
            "cpu": 45.5,
            "mem": 62.3,
            "disk": 78.1,
            "uptime": 3600
        }
    )
    print(f"  Status: {r.status_code}")
    print(f"  Response: {r.json()}")
    return r.status_code == 200

def test_process_event():
    """Test process event ingestion and detection"""
    print("\n[TEST] Process Event Detection")
    r = requests.post(
        f"{BASE_URL}/events",
        headers={"API-Key": API_KEY},
        json={
            "events": [{
                "system_id": "TEST-QA-PC",
                "event": "process_spawn",
                "parent": "winword.exe",
                "child": "powershell.exe",
                "pid": 9999,
                "cmdline": "powershell.exe -nop -w hidden -enc SQBFAFgAIAA=",
                "sha256": "",
                "exe_path": "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
                "time": int(time.time())
            }]
        }
    )
    print(f"  Status: {r.status_code}")
    data = r.json()
    print(f"  Alerts generated: {len(data.get('alerts', []))}")
    if data.get('alerts'):
        alert = data['alerts'][0]
        print(f"  Risk Level: {alert.get('risk_level')}")
        print(f"  Risk Score: {alert.get('risk_score')}")
        print(f"  Reason: {alert.get('reason', '')[:80]}")
    return r.status_code == 200

def test_invalid_auth():
    """Test with invalid credentials"""
    print("\n[TEST] Invalid Authentication")
    r = requests.post(
        f"{BASE_URL}/auth/login",
        json={"username": "hacker", "password": "wrongpass"}
    )
    print(f"  Status: {r.status_code}")
    print(f"  Response: {r.json()}")
    return r.status_code == 401

def test_missing_api_key():
    """Test agent endpoint without API key"""
    print("\n[TEST] Missing API Key")
    r = requests.post(
        f"{BASE_URL}/events",
        json={"events": []}
    )
    print(f"  Status: {r.status_code}")
    print(f"  Response: {r.json()}")
    return r.status_code == 401

def test_get_alerts(token):
    """Test getting alerts"""
    print("\n[TEST] Get Alerts")
    r = requests.get(
        f"{BASE_URL}/api/alerts",
        headers={"Authorization": f"Bearer {token}"}
    )
    print(f"  Status: {r.status_code}")
    data = r.json()
    print(f"  Alerts returned: {len(data.get('alerts', []))}")
    print(f"  Total count: {data.get('total', 0)}")
    return r.status_code == 200

def test_get_incidents(token):
    """Test getting incidents"""
    print("\n[TEST] Get Incidents")
    r = requests.get(
        f"{BASE_URL}/api/incidents",
        headers={"Authorization": f"Bearer {token}"}
    )
    print(f"  Status: {r.status_code}")
    data = r.json()
    print(f"  Incidents returned: {len(data.get('incidents', []))}")
    return r.status_code == 200

if __name__ == "__main__":
    print("=" * 70)
    print("  EDR API COMPREHENSIVE TEST SUITE")
    print("=" * 70)
    
    results = {}
    
    # Public endpoints
    results['health'] = test_health()
    
    # Authentication
    token = test_auth_login()
    if token:
        results['auth_login'] = True
        results['auth_me'] = test_auth_me(token)
    else:
        results['auth_login'] = False
        results['auth_me'] = False
        print("\n❌ Cannot continue - authentication failed")
        exit(1)
    
    # Dashboard API
    results['api_status'] = test_api_status(token)
    results['get_alerts'] = test_get_alerts(token)
    results['get_incidents'] = test_get_incidents(token)
    
    # Agent endpoints
    results['agent_register'] = test_agent_register()
    results['agent_heartbeat'] = test_agent_heartbeat()
    results['process_event'] = test_process_event()
    
    # Security tests
    results['invalid_auth'] = test_invalid_auth()
    results['missing_api_key'] = test_missing_api_key()
    
    # Summary
    print("\n" + "=" * 70)
    print("  TEST SUMMARY")
    print("=" * 70)
    passed = sum(1 for v in results.values() if v)
    failed = len(results) - passed
    
    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {status}  {test_name}")
    
    print("\n" + "=" * 70)
    print(f"  TOTAL: {passed} passed, {failed} failed out of {len(results)}")
    print("=" * 70)
    
    exit(0 if failed == 0 else 1)
