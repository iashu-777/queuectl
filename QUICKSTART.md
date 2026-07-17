# QueueCTL Quick Start Guide

## Setup
1. Save the `queuectl.py` file to your project directory
2. Ensure Python 3.6+ is installed
3. Make it executable: `chmod +x queuectl.py`

## Running
```bash
# Add jobs to queue
python queuectl.py enqueue '{"id":"job1","command":"echo Hello"}'

# Start 3 parallel workers (blocks, processes jobs continuously)
python queuectl.py worker start --count 3

# In another terminal, check status
python queuectl.py status

# List jobs by state
python queuectl.py list --state pending

# View dead letter queue
python queuectl.py dlq list

# Retry a failed job
python queuectl.py dlq retry job1

# Configure max retries
python queuectl.py config set max-retries 5
```

**All data persists in `queue.db`** (SQLite file created automatically). Press Ctrl+C on workers for graceful shutdown.
