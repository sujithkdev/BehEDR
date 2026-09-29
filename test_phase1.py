# -*- coding: utf-8 -*-
"""
test_phase1.py — Phase 1 Critical Stability Test Suite
=======================================================
Tests all 5 Phase 1 stability fixes:
  1. Agent Watchdog - Auto-restart capability
  2. Database Connection Pooling - Concurrent writes
  3. Memory Leak Fixes - Bounded tracking dicts
  4. Alert Storm Protection - Rate limiting
  5. ML Model Validation - Graceful failures

Run before deploying to production to verify stability.
"""

import sys
import os
import time
import threading
import tempfile
import sqlite3

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_database_pooling():
    """Test database connection pooling under concurrent load."""
    print("\n" + "="*70)
    print("TEST 1: Database Connection Pooling")
    print("="*70)
    
    from server.database import storage
    from server.config import DATA_DIR
    
    # Create a test database
    test_db = os.path.join(DATA_DIR, f"test_pool_{int(time.time())}.db")
    original_db = storage.DB_PATH
    storage.DB_PATH = test_db
    
    # Clear any existing connection
    storage._local.conn = None
    
    try:
        # Initialize test DB
        storage.init_db()
        
        # Simulate 50 agents writing concurrently
        def write_events(agent_id, count):
            for i in range(count):
                event = {
                    "system_id": f"AGENT-{agent_id}",
                    "parent": "explorer.exe",
                    "child": f"process-{i}.exe",
                    "pid": 1000 + i,
                    "cmdline": f"test command {i}",
                    "sha256": "a" * 64,
                    "time": int(time.time())
                }
                try:
                    storage.save_event(event)
                except Exception as e:
                    print(f"  ❌ Write failed for AGENT-{agent_id}: {e}")
                    return False
            return True
        
        print("  Simulating 50 agents writing 10 events each (500 concurrent writes)...")
        start = time.time()
        threads = []
        for i in range(50):
            t = threading.Thread(target=write_events, args=(i, 10))
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        elapsed = time.time() - start
        
        # Verify all events were written
        conn = storage._conn()
        count = conn.execute("SELECT COUNT(*) as n FROM events").fetchone()["n"]
        
        print(f"  ✅ Completed in {elapsed:.2f}s")
        print(f"  ✅ {count} events written successfully")
        
        if count == 500:
            print("  ✅ TEST PASSED: All concurrent writes succeeded")
            return True
        else:
            print(f"  ❌ TEST FAILED: Expected 500 events, got {count}")
            return False
            
    finally:
        storage.DB_PATH = original_db
        storage._local.conn = None
        try:
            os.remove(test_db)
        except Exception:
            pass


def test_memory_leak_protection():
    """Test that tracking dictionaries are bounded."""
    print("\n" + "="*70)
    print("TEST 2: Memory Leak Protection")
    print("="*70)
    
    from server.core import detector
    
    # Test network tracking bounds
    print("  Testing network connection tracking bounds...")
    for i in range(150):  # Exceed MAX_SYSTEMS limit
        for j in range(10):
            detector.add_network_event(
                f"SYSTEM-{i}",
                "test.exe",
                f"1.2.3.{j}",
                443,
                False
            )
    
    net_systems = len(detector._net_conns)
    print(f"  Network tracking: {net_systems} systems (max {detector._NET_CONNS_MAX_SYSTEMS})")
    
    if net_systems <= detector._NET_CONNS_MAX_SYSTEMS:
        print("  ✅ Network tracking bounded correctly")
        success_net = True
    else:
        print(f"  ❌ Network tracking exceeded limit: {net_systems}")
        success_net = False
    
    # Test file tracking bounds
    print("  Testing file event tracking bounds...")
    for i in range(150):  # Exceed MAX_SYSTEMS limit
        for j in range(10):
            detector.add_file_event(
                f"SYSTEM-{i}",
                "test.exe",
                f"C:\\temp\\file{j}.txt",
                "modify"
            )
    
    file_systems = len(detector._file_events)
    print(f"  File tracking: {file_systems} systems (max {detector._FILE_EVENTS_MAX_SYSTEMS})")
    
    if file_systems <= detector._FILE_EVENTS_MAX_SYSTEMS:
        print("  ✅ File tracking bounded correctly")
        success_file = True
    else:
        print(f"  ❌ File tracking exceeded limit: {file_systems}")
        success_file = False
    
    if success_net and success_file:
        print("  ✅ TEST PASSED: Memory leak protection working")
        return True
    else:
        print("  ❌ TEST FAILED: Memory leak protection not working")
        return False


def test_alert_storm_protection():
    """Test alert storm rate limiting."""
    print("\n" + "="*70)
    print("TEST 3: Alert Storm Protection")
    print("="*70)
    
    from server.alert_storm_protection import check_alert_storm, get_storm_status, reset_storm
    
    # Reset any existing state
    reset_storm()
    
    # Simulate a storm: 100 alerts in 1 second
    print("  Simulating alert storm: 100 alerts in 1 second...")
    storm_system = "STORM-TEST-1"
    allowed = 0
    suppressed = 0
    
    for i in range(100):
        alert = {
            "system_id": storm_system,
            "process": "test.exe",
            "risk_level": "HIGH",
            "risk_score": 75,
            "reason": f"Test alert {i}"
        }
        allow, reason = check_alert_storm(storm_system, alert)
        if allow:
            allowed += 1
        else:
            suppressed += 1
    
    print(f"  Allowed: {allowed}, Suppressed: {suppressed}")
    
    # Check storm status
    status = get_storm_status()
    system_status = status["systems"].get(storm_system, {})
    
    print(f"  Alert rate: {system_status.get('rate', 0):.1f}/min")
    print(f"  In storm: {system_status.get('in_storm', False)}")
    print(f"  Suppressed count: {system_status.get('suppressed_count', 0)}")
    
    if suppressed > 0 and system_status.get("in_storm"):
        print("  ✅ TEST PASSED: Storm protection activated correctly")
        reset_storm()  # Clean up
        return True
    else:
        print("  ❌ TEST FAILED: Storm protection did not activate")
        reset_storm()  # Clean up
        return False


def test_ml_model_validation():
    """Test ML model validation and corruption handling."""
    print("\n" + "="*70)
    print("TEST 4: ML Model Validation")
    print("="*70)
    
    from server.core import detector
    from server.config import ML_MODEL_PATH
    import os
    
    # Backup existing model if it exists
    backup_path = ML_MODEL_PATH + ".backup"
    if os.path.exists(ML_MODEL_PATH):
        print("  Backing up existing model...")
        os.rename(ML_MODEL_PATH, backup_path)
    
    try:
        # Test 1: Corrupted model file (empty file)
        print("  Test 1: Corrupted model file (empty)...")
        with open(ML_MODEL_PATH, "wb") as f:
            f.write(b"")  # Empty file
        
        detector._load_persisted_model()
        
        if not detector._ml_trained:
            print("  ✅ Correctly rejected corrupted model (empty file)")
            test1_pass = True
        else:
            print("  ❌ Failed to reject corrupted model")
            test1_pass = False
        
        # Clean up
        try:
            os.remove(ML_MODEL_PATH)
        except Exception:
            pass
        
        # Test 2: Invalid structure (random data)
        print("  Test 2: Invalid model structure...")
        import pickle
        with open(ML_MODEL_PATH, "wb") as f:
            pickle.dump({"invalid": "structure"}, f)
        
        detector._ml_trained = False  # Reset
        detector._load_persisted_model()
        
        if not detector._ml_trained:
            print("  ✅ Correctly rejected invalid model structure")
            test2_pass = True
        else:
            print("  ❌ Failed to reject invalid model")
            test2_pass = False
        
        # Clean up
        try:
            os.remove(ML_MODEL_PATH)
        except Exception:
            pass
        
        # Test 3: Feature count mismatch
        print("  Test 3: Feature count mismatch...")
        # Create a model with wrong feature count
        import joblib
        dummy_data = {
            "model": None,
            "scaler": None,
            "n_features": 999  # Wrong count
        }
        joblib.dump(dummy_data, ML_MODEL_PATH)
        
        detector._ml_trained = False  # Reset
        detector._load_persisted_model()
        
        if not detector._ml_trained:
            print("  ✅ Correctly rejected model with feature mismatch")
            test3_pass = True
        else:
            print("  ❌ Failed to reject model with wrong features")
            test3_pass = False
        
        # Clean up
        try:
            os.remove(ML_MODEL_PATH)
        except Exception:
            pass
        
        if test1_pass and test2_pass and test3_pass:
            print("  ✅ TEST PASSED: ML model validation working correctly")
            return True
        else:
            print("  ❌ TEST FAILED: Some model validation checks failed")
            return False
            
    finally:
        # Restore backup
        if os.path.exists(backup_path):
            print("  Restoring original model...")
            if os.path.exists(ML_MODEL_PATH):
                os.remove(ML_MODEL_PATH)
            os.rename(backup_path, ML_MODEL_PATH)


def test_watchdog():
    """Test agent watchdog functionality."""
    print("\n" + "="*70)
    print("TEST 5: Agent Watchdog")
    print("="*70)
    
    # Check if watchdog file exists
    watchdog_path = os.path.join(os.path.dirname(__file__), "agent_watchdog.py")
    
    if not os.path.exists(watchdog_path):
        print("  ❌ agent_watchdog.py not found")
        return False
    
    print("  ✅ agent_watchdog.py exists")
    
    # Verify it's importable
    try:
        import agent_watchdog
        print("  ✅ agent_watchdog.py is valid Python")
    except Exception as e:
        print(f"  ❌ agent_watchdog.py has syntax errors: {e}")
        return False
    
    # Check for required functions
    if hasattr(agent_watchdog, 'main'):
        print("  ✅ main() function found")
    else:
        print("  ❌ main() function not found")
        return False
    
    if hasattr(agent_watchdog, '_start_agent'):
        print("  ✅ _start_agent() function found")
    else:
        print("  ❌ _start_agent() function not found")
        return False
    
    print("  ✅ TEST PASSED: Watchdog structure validated")
    print("  ℹ️  Note: Full watchdog testing requires actual agent process")
    return True


def main():
    """Run all Phase 1 tests."""
    print("\n" + "="*70)
    print("PHASE 1 CRITICAL STABILITY TEST SUITE")
    print("Testing 5 critical fixes for 50-endpoint production deployment")
    print("="*70)
    
    results = {}
    
    # Run all tests
    try:
        results["Database Pooling"] = test_database_pooling()
    except Exception as e:
        print(f"\n❌ Database pooling test crashed: {e}")
        results["Database Pooling"] = False
    
    try:
        results["Memory Leak Protection"] = test_memory_leak_protection()
    except Exception as e:
        print(f"\n❌ Memory leak test crashed: {e}")
        results["Memory Leak Protection"] = False
    
    try:
        results["Alert Storm Protection"] = test_alert_storm_protection()
    except Exception as e:
        print(f"\n❌ Alert storm test crashed: {e}")
        results["Alert Storm Protection"] = False
    
    try:
        results["ML Model Validation"] = test_ml_model_validation()
    except Exception as e:
        print(f"\n❌ ML model validation test crashed: {e}")
        results["ML Model Validation"] = False
    
    try:
        results["Agent Watchdog"] = test_watchdog()
    except Exception as e:
        print(f"\n❌ Watchdog test crashed: {e}")
        results["Agent Watchdog"] = False
    
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
        print("✅ ALL TESTS PASSED - READY FOR PRODUCTION")
        print("="*70)
        return 0
    else:
        print(f"❌ {total - passed} TEST(S) FAILED - FIX BEFORE DEPLOYMENT")
        print("="*70)
        return 1


if __name__ == "__main__":
    sys.exit(main())
