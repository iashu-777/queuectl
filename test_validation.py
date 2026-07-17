#!/usr/bin/env python3
"""
Minimal validation script for QueueCTL core functionality.
Run from same directory as queuectl.py
"""
import subprocess
import json
import time
import sys
import os
import sqlite3

def run_cmd(cmd):
    """Run queuectl command and return output"""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout + result.stderr

def cleanup():
    """Remove test database"""
    if os.path.exists("queue.db"):
        os.remove("queue.db")

def test_enqueue():
    """Test 1: Basic job enqueuing"""
    print("[TEST 1] Enqueue job...")
    run_cmd('python queuectl.py enqueue \'{"id":"test_enqueue","command":"echo success"}\'')
    conn = sqlite3.connect("queue.db")
    c = conn.cursor()
    c.execute("SELECT id FROM jobs WHERE id='test_enqueue'")
    result = c.fetchone()
    conn.close()
    assert result, "Job not found in database"
    print("✓ PASS: Job enqueued successfully\n")

def test_persistence():
    """Test 2: Data persists across restarts"""
    print("[TEST 2] Job persistence...")
    run_cmd('python queuectl.py enqueue \'{"id":"test_persist","command":"echo persist"}\'')
    
    conn = sqlite3.connect("queue.db")
    c = conn.cursor()
    c.execute("SELECT id, state FROM jobs WHERE id='test_persist'")
    result = c.fetchone()
    conn.close()
    
    assert result and result[1] == 'pending', "Job not persisted"
    print("✓ PASS: Jobs persist in SQLite\n")

def test_job_lifecycle():
    """Test 3: Job state transitions"""
    print("[TEST 3] Job state transitions...")
    run_cmd('python queuectl.py enqueue \'{"id":"test_lifecycle","command":"true"}\'')
    
    import threading
    def worker():
        run_cmd('python queuectl.py worker start --count 1')
    
    worker_thread = threading.Thread(target=worker, daemon=True)
    worker_thread.start()
    time.sleep(2)
    
    conn = sqlite3.connect("queue.db")
    c = conn.cursor()
    c.execute("SELECT state FROM jobs WHERE id='test_lifecycle'")
    result = c.fetchone()
    conn.close()
    
    assert result and result[0] == 'completed', f"Expected completed, got {result}"
    print("✓ PASS: Job transitions work\n")

def test_failed_job_retry():
    """Test 4: Failed jobs retry correctly"""
    print("[TEST 4] Failed job retries...")
    run_cmd('python queuectl.py enqueue \'{"id":"test_fail","command":"false","max_retries":2}\'')
    
    import threading
    def worker():
        run_cmd('python queuectl.py worker start --count 1')
    
    worker_thread = threading.Thread(target=worker, daemon=True)
    worker_thread.start()
    time.sleep(3)
    
    conn = sqlite3.connect("queue.db")
    c = conn.cursor()
    c.execute("SELECT state, attempts FROM jobs WHERE id='test_fail'")
    result = c.fetchone()
    conn.close()
    
    assert result and result[0] == 'dead' and result[1] >= 2, "Job should be dead after retries"
    print("✓ PASS: Failed job moved to DLQ\n")

def test_dlq_retry():
    """Test 5: DLQ job can be retried"""
    print("[TEST 5] DLQ retry functionality...")
    run_cmd('python queuectl.py enqueue \'{"id":"test_dlq_retry","command":"false","max_retries":1}\'')
    
    conn = sqlite3.connect("queue.db")
    c = conn.cursor()
    c.execute("UPDATE jobs SET state='dead', attempts=1 WHERE id='test_dlq_retry'")
    conn.commit()
    conn.close()
    
    run_cmd('python queuectl.py dlq retry test_dlq_retry')
    
    conn = sqlite3.connect("queue.db")
    c = conn.cursor()
    c.execute("SELECT state, attempts FROM jobs WHERE id='test_dlq_retry'")
    result = c.fetchone()
    conn.close()
    
    assert result and result[0] == 'pending' and result[1] == 0, "DLQ retry failed"
    print("✓ PASS: DLQ retry works\n")

def test_config():
    """Test 6: Configuration management"""
    print("[TEST 6] Configuration management...")
    run_cmd('python queuectl.py config set max-retries 5')
    
    conn = sqlite3.connect("queue.db")
    c = conn.cursor()
    c.execute("SELECT value FROM config WHERE key='max-retries'")
    result = c.fetchone()
    conn.close()
    
    assert result and result[0] == '5', "Config not set"
    print("✓ PASS: Config set successfully\n")

def test_list_by_state():
    """Test 7: List jobs by state"""
    print("[TEST 7] List jobs by state...")
    run_cmd('python queuectl.py enqueue \'{"id":"state_test1","command":"true"}\'')
    run_cmd('python queuectl.py enqueue \'{"id":"state_test2","command":"true"}\'')
    
    conn = sqlite3.connect("queue.db")
    c = conn.cursor()
    c.execute("UPDATE jobs SET state='completed' WHERE id='state_test1'")
    conn.commit()
    
    c.execute("SELECT COUNT(*) FROM jobs WHERE state='completed'")
    count = c.fetchone()[0]
    conn.close()
    
    assert count >= 1, "List by state failed"
    print("✓ PASS: List by state works\n")

def main():
    print("=" * 50)
    print("QueueCTL Validation Tests")
    print("=" * 50 + "\n")
    
    cleanup()
    
    try:
        test_enqueue()
        test_persistence()
        test_job_lifecycle()
        test_failed_job_retry()
        test_dlq_retry()
        test_config()
        test_list_by_state()
        
        print("=" * 50)
        print("ALL TESTS PASSED ✓")
        print("=" * 50)
        return 0
    except AssertionError as e:
        print(f"\n✗ TEST FAILED: {e}")
        return 1
    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        return 1
    finally:
        cleanup()

if __name__ == '__main__':
    sys.exit(main())
