# -*- coding: utf-8 -*-
"""
test_phase2.py — Phase 2 Production Hardening Test Suite
=========================================================
Tests all 5 Phase 2 features:
  1. Automated backups
  2. Health monitoring
  3. Password policy
  4. TLS configuration (verification only)
  5. Deployment readiness

Run before deploying to verify Phase 2 is working.
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_backup_system():
    """Test automated backup functionality."""
    print("\n" + "="*70)
    print("TEST 1: Automated Backup System")
    print("="*70)
    
    try:
        from server.backup_manager import create_backup, list_backups, get_backup_status
        
        # Test backup creation
        print("  Creating test backup...")
        backup_path = create_backup(reason="test")
        
        if not backup_path:
            print("  ❌ Backup creation failed")
            return False
        
        print(f"  ✅ Backup created: {os.path.basename(backup_path)}")
        
        # Verify backup file exists
        if not os.path.exists(backup_path):
            print("  ❌ Backup file does not exist")
            return False
        
        print("  ✅ Backup file exists")
        
        # Test backup listing
        backups = list_backups()
        if len(backups) == 0:
            print("  ❌ No backups found")
            return False
        
        print(f"  ✅ Found {len(backups)} backup(s)")
        
        # Test backup status
        status = get_backup_status()
        if "backups" not in status:
            print("  ❌ Backup status invalid")
            return False
        
        print(f"  ✅ Backup status: {len(status['backups'])} backup(s)")
        
        print("  ✅ TEST PASSED: Backup system working")
        return True
        
    except Exception as e:
        print(f"  ❌ TEST FAILED: {e}")
        return False


def test_health_monitoring():
    """Test health monitoring system."""
    print("\n" + "="*70)
    print("TEST 2: Health Monitoring")
    print("="*70)
    
    try:
        from server.health_monitor import record_agent_heartbeat, get_health_status, get_agent_health
        
        # Test heartbeat recording
        print("  Recording test heartbeat...")
        record_agent_heartbeat("TEST-AGENT-1")
        
        # Test health status retrieval
        print("  Checking health status...")
        status = get_health_status()
        
        if "server" not in status or "agents" not in status:
            print("  ❌ Health status structure invalid")
            return False
        
        print(f"  ✅ Server status: {status['server'].get('status', 'unknown')}")
        print(f"  ✅ Server CPU: {status['server'].get('cpu_percent', 0)}%")
        print(f"  ✅ Server memory: {status['server'].get('memory_percent', 0)}%")
        
        # Test agent health
        agent_health = get_agent_health("TEST-AGENT-1")
        if agent_health.get("status") != "online":
            print(f"  ❌ Agent status unexpected: {agent_health.get('status')}")
            return False
        
        print(f"  ✅ Agent status: {agent_health.get('status')}")
        
        print("  ✅ TEST PASSED: Health monitoring working")
        return True
        
    except Exception as e:
        print(f"  ❌ TEST FAILED: {e}")
        return False


def test_password_policy():
    """Test password policy enforcement."""
    print("\n" + "="*70)
    print("TEST 3: Password Policy")
    print("="*70)
    
    try:
        from server.password_policy import validate_password_strength, get_password_policy
        
        # Test policy retrieval
        policy = get_password_policy()
        print(f"  Policy: min_length={policy['min_length']}")
        
        # Test weak passwords (should fail)
        weak_passwords = [
            ("short", "too short"),
            ("alllowercase123!", "no uppercase"),
            ("ALLUPPERCASE123!", "no lowercase"),
            ("NoDigitsHere!", "no digit"),
            ("NoSpecialChar123", "no special char"),
        ]
        
        failed_as_expected = 0
        for password, reason in weak_passwords:
            valid, error = validate_password_strength(password)
            if not valid:
                failed_as_expected += 1
                print(f"  ✅ Rejected weak password ({reason})")
            else:
                print(f"  ❌ Accepted weak password ({reason})")
        
        if failed_as_expected != len(weak_passwords):
            print(f"  ❌ Only {failed_as_expected}/{len(weak_passwords)} weak passwords rejected")
            return False
        
        # Test strong password (should pass)
        valid, error = validate_password_strength("SecureP@ssw0rd123!")
        if not valid:
            print(f"  ❌ Rejected strong password: {error}")
            return False
        
        print("  ✅ Accepted strong password")
        
        print("  ✅ TEST PASSED: Password policy working")
        return True
        
    except Exception as e:
        print(f"  ❌ TEST FAILED: {e}")
        return False


def test_tls_configuration():
    """Test TLS configuration (verification only)."""
    print("\n" + "="*70)
    print("TEST 4: TLS Configuration")
    print("="*70)
    
    try:
        # Check if TLS cert generation code exists in run.py
        run_py_path = os.path.join(os.path.dirname(__file__), "run.py")
        
        if not os.path.exists(run_py_path):
            print("  ❌ run.py not found")
            return False
        
        with open(run_py_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        
        # Check for TLS-related code
        if "_generate_self_signed" not in content:
            print("  ❌ TLS auto-cert generation code not found")
            return False
        
        print("  ✅ TLS auto-cert generation code present")
        
        if "EDR_TLS_ENABLED" not in content:
            print("  ❌ TLS configuration code not found")
            return False
        
        print("  ✅ TLS configuration code present")
        
        print("  ✅ TEST PASSED: TLS configuration verified")
        print("  ℹ️  Note: TLS testing requires server restart with EDR_TLS_ENABLED=true")
        return True
        
    except Exception as e:
        print(f"  ❌ TEST FAILED: {e}")
        return False


def test_deployment_readiness():
    """Test deployment readiness (files exist)."""
    print("\n" + "="*70)
    print("TEST 5: Deployment Readiness")
    print("="*70)
    
    required_files = [
        "agent.py",
        "agent_watchdog.py",
        "run.py",
        "server/__init__.py",
        "server/backup_manager.py",
        "server/health_monitor.py",
        "server/password_policy.py",
    ]
    
    missing_files = []
    for filepath in required_files:
        full_path = os.path.join(os.path.dirname(__file__), filepath)
        if os.path.exists(full_path):
            print(f"  ✅ {filepath}")
        else:
            print(f"  ❌ {filepath} MISSING")
            missing_files.append(filepath)
    
    if missing_files:
        print(f"  ❌ TEST FAILED: {len(missing_files)} file(s) missing")
        return False
    
    print("  ✅ TEST PASSED: All required files present")
    return True


def main():
    """Run all Phase 2 tests."""
    print("\n" + "="*70)
    print("PHASE 2 PRODUCTION HARDENING TEST SUITE")
    print("Testing 5 operational excellence features")
    print("="*70)
    
    results = {}
    
    # Run all tests
    try:
        results["Automated Backups"] = test_backup_system()
    except Exception as e:
        print(f"\n❌ Backup test crashed: {e}")
        results["Automated Backups"] = False
    
    try:
        results["Health Monitoring"] = test_health_monitoring()
    except Exception as e:
        print(f"\n❌ Health monitoring test crashed: {e}")
        results["Health Monitoring"] = False
    
    try:
        results["Password Policy"] = test_password_policy()
    except Exception as e:
        print(f"\n❌ Password policy test crashed: {e}")
        results["Password Policy"] = False
    
    try:
        results["TLS Configuration"] = test_tls_configuration()
    except Exception as e:
        print(f"\n❌ TLS test crashed: {e}")
        results["TLS Configuration"] = False
    
    try:
        results["Deployment Readiness"] = test_deployment_readiness()
    except Exception as e:
        print(f"\n❌ Deployment test crashed: {e}")
        results["Deployment Readiness"] = False
    
    # Print summary
    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for test, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {status}: {test}")
    
    print("\n" + "="*70)
    print(f"RESULT: {passed}/{total} tests passed")
    
    if passed == total:
        print("✅ ALL TESTS PASSED - PHASE 2 COMPLETE")
        print("="*70)
        return 0
    else:
        print(f"❌ {total - passed} TEST(S) FAILED")
        print("="*70)
        return 1


if __name__ == "__main__":
    sys.exit(main())
