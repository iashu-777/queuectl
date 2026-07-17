# QueueCTL - Production-Grade Background Job Queue System

A lightweight, CLI-based background job queue system built with Python. Manage background jobs, handle retries with exponential backoff, and maintain a Dead Letter Queue (DLQ) for permanently failed jobs.

## Features

✅ **Enqueue & Execute Jobs** - Add jobs via CLI, execute them with worker processes  
✅ **Multiple Workers** - Run parallel worker threads without race conditions  
✅ **Exponential Backoff Retries** - Automatic retry with configurable exponential backoff  
✅ **Dead Letter Queue (DLQ)** - Permanent failures moved to DLQ for inspection  
✅ **Persistent Storage** - SQLite database ensures job data survives restarts  
✅ **Configuration Management** - CLI-based config for retry count, backoff, etc.  
✅ **Graceful Shutdown** - Workers finish current jobs before exit (Ctrl+C)  

## Setup Instructions

### Prerequisites
- Python 3.6+
- No external dependencies (uses only Python stdlib)

### Installation

1. **Clone or download `queuectl.py`**
   ```bash
   wget https://[your-github-url]/queuectl.py
   # or save the file manually
   ```

2. **Make executable (optional)**
   ```bash
   chmod +x queuectl.py
   ```

3. **Verify installation**
   ```bash
   python queuectl.py --help
   ```

On first run, `queue.db` (SQLite database) is created automatically in the current directory.

## Usage Examples

### 1. Enqueue a Job
```bash
python queuectl.py enqueue '{"id":"job1","command":"echo Hello World"}'
# Output: Job job1 enqueued

python queuectl.py enqueue '{"id":"job2","command":"sleep 2 && echo Done"}'

# Custom max retries
python queuectl.py enqueue '{"id":"job3","command":"python script.py","max_retries":5}'
```

### 2. Start Workers
```bash
# Start 1 worker (default)
python queuectl.py worker start

# Start 3 parallel workers
python queuectl.py worker start --count 3
# Output: Started 3 workers
# Workers will continuously process pending jobs
# Press Ctrl+C to gracefully shutdown
```

### 3. Check System Status
```bash
python queuectl.py status
# Output:
# Job Status Summary:
#   pending: 2
#   processing: 1
#   completed: 5
#   failed: 0
#   dead: 0
# Active Workers: 3
```

### 4. List Jobs
```bash
# List all jobs
python queuectl.py list
# ID: job1, Command: echo Hello, State: completed, Attempts: 1/3
# ID: job2, Command: sleep 2, State: pending, Attempts: 0/3

# Filter by state
python queuectl.py list --state pending
python queuectl.py list --state completed
python queuectl.py list --state failed
```

### 5. Dead Letter Queue (DLQ)
```bash
# View all dead jobs
python queuectl.py dlq list
# ID: job_fail, Command: false, Attempts: 3/3

# Retry a dead job (resets to pending, attempts=0)
python queuectl.py dlq retry job_fail
# Output: Job job_fail moved back to pending
```

### 6. Configuration
```bash
# Set max retries (default: 3)
python queuectl.py config set max-retries 5

# Set backoff base (default: 2, means delays of 2^attempts)
python queuectl.py config set backoff-base 3
```

## Architecture Overview

### Job Lifecycle
```
pending → processing → completed (success)
  ↓           ↓
  └→ failed → retry (with backoff delay) → processing
       ↓
       dead (if attempts >= max_retries)
```

### Data Persistence
- **Database**: SQLite (`queue.db`)
- **Jobs Table**: Stores id, command, state, attempts, max_retries, created_at, updated_at
- **Config Table**: Stores configuration key-value pairs
- **Atomicity**: SQLite transactions (`BEGIN IMMEDIATE`) prevent duplicate processing

### Worker Concurrency Model
1. Workers use **atomic database transactions** to claim jobs
2. When claiming a job, state atomically changes from `pending` → `processing`
3. Only one worker can claim the same job (no race conditions)
4. After execution, state transitions to `completed` or `failed`

### Retry & Exponential Backoff
```
Exponential Backoff Formula:
  delay = backoff_base ^ attempt_number (seconds)

Example (base=2):
  Attempt 1: 2^1 = 2 seconds
  Attempt 2: 2^2 = 4 seconds
  Attempt 3: 2^3 = 8 seconds
```

When a job fails:
1. attempts counter increments
2. If attempts < max_retries: state → `failed`, then `pending` after backoff
3. If attempts >= max_retries: state → `dead` (moved to DLQ)

### Graceful Shutdown
- Workers listen for `STOP_EVENT` (set by Ctrl+C / SIGINT)
- Workers finish current job before exiting
- Main thread waits for all workers to complete

## Assumptions & Trade-offs

### Design Decisions
1. **SQLite over JSON Files**
   - ✅ ACID compliance prevents data loss
   - ✅ Atomic transactions prevent race conditions
   - ✅ Efficient querying by state
   - Trade-off: Requires sqlite3 (part of Python stdlib, no external dep)

2. **Threading over Multiprocessing**
   - ✅ Lower memory overhead (multiple processes = higher RAM)
   - ✅ Simpler shared state (SQLite DB connection)
   - ✅ Faster startup
   - Trade-off: Python GIL limits CPU-bound concurrency (OK for I/O-bound jobs)

3. **Synchronous Job Execution**
   - ✅ Simple, reliable job tracking
   - ✅ Easy debugging and monitoring
   - Trade-off: Worker blocks until command completes (use `&` in shell for async)

4. **Command Execution via Shell**
   - ✅ Supports complex bash commands (pipes, redirects, etc.)
   - Trade-off: Requires shell escaping; consider pre-validating untrusted commands

5. **Backoff Not Applied on Initial Failure**
   - First failure immediately retried
   - Backoff only applies to subsequent retries
   - Rationale: Transient failures often succeed on immediate retry

### Simplifications
- No job timeouts (could hang on blocking commands)
- No job priority queues (all jobs processed FIFO)
- No scheduled jobs (no `run_at` support)
- No output capture (job stdout/stderr not logged)
- No metrics/stats collection

These are available as bonus features if time permits.

## Testing Instructions

### Test 1: Basic Job Completion
```bash
# Terminal 1: Start worker
python queuectl.py worker start --count 1

# Terminal 2: Enqueue a simple job
python queuectl.py enqueue '{"id":"test1","command":"echo Success"}'

# Wait 1 second, check status
python queuectl.py list --state completed
# Should show: ID: test1, Command: echo Success, State: completed, Attempts: 1/3
```

### Test 2: Failed Job with Retry
```bash
python queuectl.py enqueue '{"id":"test2","command":"false","max_retries":2}'
python queuectl.py worker start --count 1

# Wait 5 seconds
python queuectl.py dlq list
# Should show: ID: test2 in DLQ after 2 retries
```

### Test 3: Multiple Workers (No Overlap)
```bash
# Enqueue 5 jobs
for i in {1..5}; do
  python queuectl.py enqueue "{\"id\":\"job$i\",\"command\":\"sleep 1 && echo Done$i\"}"
done

# Start 2 workers, monitor output
python queuectl.py worker start --count 2

# In another terminal, watch status
watch -n 1 "python queuectl.py status"
# Should never see same job_id in processing twice
```

### Test 4: Data Persistence
```bash
# Enqueue jobs
python queuectl.py enqueue '{"id":"persist1","command":"sleep 10"}'

# Start worker
python queuectl.py worker start --count 1

# Ctrl+C immediately to interrupt
# Ctrl+C again

# Verify job state persisted
python queuectl.py status
# Jobs should still exist in queue
```

### Test 5: DLQ Retry
```bash
python queuectl.py enqueue '{"id":"dlq_test","command":"false","max_retries":1}'
python queuectl.py worker start --count 1

# Wait for job to move to DLQ
sleep 3

python queuectl.py dlq list  # Shows dlq_test

# Retry it
python queuectl.py dlq retry dlq_test
python queuectl.py list --state pending  # Should show dlq_test now
```

## Database Schema

### jobs table
```sql
CREATE TABLE jobs (
  id TEXT PRIMARY KEY,
  command TEXT,
  state TEXT,              -- 'pending','processing','completed','failed','dead'
  attempts INTEGER,
  max_retries INTEGER,
  created_at TEXT,         -- ISO 8601 timestamp
  updated_at TEXT          -- ISO 8601 timestamp
)
```

### config table
```sql
CREATE TABLE config (
  key TEXT PRIMARY KEY,    -- 'max-retries', 'backoff-base'
  value TEXT
)
```

## Troubleshooting

### Jobs not processing?
- Check if workers are running: `python queuectl.py status`
- Verify jobs are in 'pending' state: `python queuectl.py list --state pending`
- Check command syntax: Ensure commands work in shell before enqueueing

### Job stuck in 'processing'?
- Worker crashed before completing. Restart worker.
- Manually reset: Delete `queue.db` and restart (data loss!).

### DLQ queue growing fast?
- Review commands for issues: `python queuectl.py dlq list`
- Increase max_retries: `python queuectl.py config set max-retries 5`
- Check logs/output from manual command execution

### Database locked error?
- Only one `queuectl.py` instance can write at a time
- Close all other instances and retry

## Performance Considerations

- **Polling Interval**: Workers check for jobs every 0.5 seconds (tunable in code)
- **DB Connections**: New connection per claim (lightweight, acceptable for small workloads)
- **Max Concurrent Jobs**: Limited by `--count` flag (default 1)
- **Database Size**: 1M jobs ≈ 300MB (SQLite efficient)

For production at scale (>1M jobs/hour), consider:
- Redis/RabbitMQ backend
- Connection pooling
- Async/non-blocking execution

## File Structure
```
.
├── queuectl.py          # Main CLI application
├── queue.db             # SQLite database (auto-created)
├── README.md            # This file
└── test_validation.py   # Optional: validation script
```

## License
This is a learning/internship project. Modify freely.

## Support
For issues or questions, refer to the code comments or test scenarios above.
