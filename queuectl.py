#!/usr/bin/env python3
import sqlite3
import ast
import json
import argparse
import sys
import os
import time
import threading
import subprocess
import signal
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

DB_PATH = "queue.db"
STOP_EVENT = threading.Event()
ACTIVE_WORKERS = []

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS jobs
                 (id TEXT PRIMARY KEY, command TEXT, state TEXT, attempts INTEGER,
                  max_retries INTEGER, created_at TEXT, updated_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS config
                 (key TEXT PRIMARY KEY, value TEXT)''')
    conn.commit()
    conn.close()

def get_config(key, default):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT value FROM config WHERE key=?', (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else default

def set_config(key, value):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)', (key, value))
    conn.commit()
    conn.close()

def enqueue(job_json):
    try:
        job = json.loads(job_json)
    except json.JSONDecodeError:
        # Windows CMD users often pass single-quoted dicts
        try:
            job = ast.literal_eval(job_json)
            if not isinstance(job, dict):
                raise ValueError
        except Exception:
            print("Error: Invalid JSON format")
            print("\nWindows CMD Example:")
            print(r'python queuectl.py enqueue "{\"id\":\"job2\",\"command\":\"echo Hello\"}"')
            print("\nPowerShell Example:")
            print(r"python queuectl.py enqueue '{\"id\":\"job2\",\"command\":\"echo Hello\"}'")
            sys.exit(1)
    
    required = ['id', 'command']
    if not all(k in job for k in required):
        print(f"Error: Missing required fields: {required}")
        sys.exit(1)
    
    job.setdefault('state', 'pending')
    job.setdefault('attempts', 0)
    job.setdefault('max_retries', int(get_config('max_retries', '3')))
    job.setdefault('created_at', datetime.utcnow().isoformat() + 'Z')
    job.setdefault('updated_at', datetime.utcnow().isoformat() + 'Z')
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute('''INSERT INTO jobs (id, command, state, attempts, max_retries, created_at, updated_at)
                     VALUES (?, ?, ?, ?, ?, ?, ?)''',
                  (job['id'], job['command'], job['state'], job['attempts'],
                   job['max_retries'], job['created_at'], job['updated_at']))
        conn.commit()
        print(f"Job {job['id']} enqueued")
    except sqlite3.IntegrityError:
        print(f"Error: Job {job['id']} already exists")
        sys.exit(1)
    finally:
        conn.close()

def claim_job():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('BEGIN IMMEDIATE')
    try:
        c.execute('''SELECT id, command, attempts, max_retries FROM jobs
                     WHERE state='pending' LIMIT 1''')
        row = c.fetchone()
        if row:
            job_id = row[0]
            c.execute('''UPDATE jobs SET state='processing', updated_at=? WHERE id=?''',
                      (datetime.utcnow().isoformat() + 'Z', job_id))
            conn.commit()
            return row
        else:
            conn.commit()
            return None
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def execute_command(cmd):
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, timeout=3600)
        return result.returncode
    except subprocess.TimeoutExpired:
        return 1
    except Exception:
        return 1

def update_job_state(job_id, state, attempts=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if attempts is not None:
        c.execute('''UPDATE jobs SET state=?, attempts=?, updated_at=? WHERE id=?''',
                  (state, attempts, datetime.utcnow().isoformat() + 'Z', job_id))
    else:
        c.execute('''UPDATE jobs SET state=?, updated_at=? WHERE id=?''',
                  (state, datetime.utcnow().isoformat() + 'Z', job_id))
    conn.commit()
    conn.close()

def worker_loop():
    thread_id = threading.current_thread().ident
    while not STOP_EVENT.is_set():
        try:
            claim = claim_job()
            if not claim:
                time.sleep(0.5)
                continue
            
            job_id, cmd, attempts, max_retries = claim
            exit_code = execute_command(cmd)
            
            if exit_code == 0:
                update_job_state(job_id, 'completed', attempts + 1)
            else:
                new_attempts = attempts + 1
                if new_attempts >= max_retries:
                    update_job_state(job_id, 'dead', new_attempts)
                else:
                    update_job_state(job_id, 'failed', new_attempts)
        except Exception as e:
            time.sleep(1)

def start_workers(count):
    global ACTIVE_WORKERS
    STOP_EVENT.clear()
    executor = ThreadPoolExecutor(max_workers=count)
    
    def run_worker():
        try:
            worker_loop()
        except KeyboardInterrupt:
            pass
    
    for _ in range(count):
        future = executor.submit(run_worker)
        ACTIVE_WORKERS.append(future)
    
    print(f"Started {count} workers")
    
    def signal_handler(sig, frame):
        print("\nShutting down workers...")
        STOP_EVENT.set()
        executor.shutdown(wait=True)
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        executor.shutdown(wait=True)
    except KeyboardInterrupt:
        pass

def stop_workers():
    global ACTIVE_WORKERS
    STOP_EVENT.set()
    print("Worker stop signal sent")

def status():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT state, COUNT(*) FROM jobs GROUP BY state')
    rows = c.fetchall()
    conn.close()
    
    state_counts = {row[0]: row[1] for row in rows}
    print("Job Status Summary:")
    for state in ['pending', 'processing', 'completed', 'failed', 'dead']:
        count = state_counts.get(state, 0)
        print(f"  {state}: {count}")
    print(f"Active Workers: {len([w for w in ACTIVE_WORKERS if not w.done()])}")

def list_jobs(state=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if state:
        c.execute('''SELECT id, command, state, attempts, max_retries FROM jobs
                     WHERE state=? ORDER BY created_at DESC''', (state,))
    else:
        c.execute('''SELECT id, command, state, attempts, max_retries FROM jobs
                     ORDER BY created_at DESC''')
    rows = c.fetchall()
    conn.close()
    
    if not rows:
        print(f"No jobs found{' with state=' + state if state else ''}")
        return
    
    for row in rows:
        print(f"ID: {row[0]}, Command: {row[1]}, State: {row[2]}, Attempts: {row[3]}/{row[4]}")

def dlq_list():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT id, command, attempts, max_retries FROM jobs
                 WHERE state='dead' ORDER BY updated_at DESC''')
    rows = c.fetchall()
    conn.close()
    
    if not rows:
        print("No dead jobs")
        return
    
    for row in rows:
        print(f"ID: {row[0]}, Command: {row[1]}, Attempts: {row[2]}/{row[3]}")

def dlq_retry(job_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id FROM jobs WHERE id=? AND state=?', (job_id, 'dead'))
    if not c.fetchone():
        print(f"Job {job_id} not found in DLQ")
        conn.close()
        return
    
    c.execute('''UPDATE jobs SET state='pending', attempts=0, updated_at=?
                 WHERE id=?''', (datetime.utcnow().isoformat() + 'Z', job_id))
    conn.commit()
    conn.close()
    print(f"Job {job_id} moved back to pending")

def config_set(key, value):
    set_config(key, value)
    print(f"Config {key} set to {value}")

def main():
    init_db()
    
    parser = argparse.ArgumentParser(description='CLI Job Queue System')
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    
    enqueue_parser = subparsers.add_parser('enqueue', help='Enqueue a job')
    enqueue_parser.add_argument('job', help='Job JSON string')
    
    worker_parser = subparsers.add_parser('worker', help='Worker management')
    worker_sub = worker_parser.add_subparsers(dest='worker_cmd')
    start_parser = worker_sub.add_parser('start', help='Start workers')
    start_parser.add_argument('--count', type=int, default=1, help='Number of workers')
    worker_sub.add_parser('stop', help='Stop workers')
    
    subparsers.add_parser('status', help='Show system status')
    
    list_parser = subparsers.add_parser('list', help='List jobs')
    list_parser.add_argument('--state', help='Filter by state')
    
    dlq_parser = subparsers.add_parser('dlq', help='Dead Letter Queue operations')
    dlq_sub = dlq_parser.add_subparsers(dest='dlq_cmd')
    dlq_sub.add_parser('list', help='List DLQ jobs')
    retry_parser = dlq_sub.add_parser('retry', help='Retry a DLQ job')
    retry_parser.add_argument('job_id', help='Job ID to retry')
    
    config_parser = subparsers.add_parser('config', help='Configuration')
    config_sub = config_parser.add_subparsers(dest='config_cmd')
    set_parser = config_sub.add_parser('set', help='Set config value')
    set_parser.add_argument('key', help='Config key')
    set_parser.add_argument('value', help='Config value')
    
    args = parser.parse_args()
    
    if args.command == 'enqueue':
        enqueue(args.job)
    elif args.command == 'worker':
        if args.worker_cmd == 'start':
            start_workers(args.count)
        elif args.worker_cmd == 'stop':
            stop_workers()
    elif args.command == 'status':
        status()
    elif args.command == 'list':
        list_jobs(args.state)
    elif args.command == 'dlq':
        if args.dlq_cmd == 'list':
            dlq_list()
        elif args.dlq_cmd == 'retry':
            dlq_retry(args.job_id)
    elif args.command == 'config':
        if args.config_cmd == 'set':
            config_set(args.key, args.value)
    else:
        parser.print_help()

if __name__ == '__main__':
    main()
