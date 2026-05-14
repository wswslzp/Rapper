# Daemon Polling Fix: Non-Blocking Task Execution

## Issue Summary (Pitfall #28)

**Problem**: When Agent Board HTTP server was killed during task execution, the Daemon's polling loop appeared to freeze, with logs stopping at the last "Connection refused" error. The daemon was actually alive but unresponsive.

**Root Cause**: The daemon used a single-threaded architecture where `_run_task_sync()` blocked the entire main thread during task execution. When tasks were running, the polling loop couldn't continue, making the daemon appear frozen.

```python
# OLD PROBLEMATIC CODE:
while self.running:
    self._poll_and_execute_tasks()  # This method could block for hours
    self.shutdown_event.wait(poll_interval)
```

Inside `_poll_and_execute_tasks()`, the blocking call was:
```python
# This blocks the entire daemon thread:
self.task_runner._run_task_sync(internal_task, timeout=3600, max_turns=200)
```

## Solution: Multi-Threaded Task Execution

Implemented a **ThreadPoolExecutor**-based solution that executes tasks in background threads while keeping the main polling loop responsive:

### Key Changes

1. **Added ThreadPoolExecutor to RapperDaemon**:
   ```python
   self.task_executor = ThreadPoolExecutor(
       max_workers=self.config.get('tasks', {}).get('max_concurrent_tasks', 5),
       thread_name_prefix="task-executor"
   )
   self.running_task_futures: Dict[str, Future] = {}
   ```

2. **Non-blocking task submission**:
   ```python
   # NEW: Submit to background thread instead of blocking
   future = self.task_executor.submit(self._execute_task_in_background, task_id, internal_task)
   with self.running_tasks_lock:
       self.running_task_futures[task_id] = future
   ```

3. **Background task execution method**:
   ```python
   def _execute_task_in_background(self, board_task_id: str, internal_task: Task):
       """Execute task in background thread to avoid blocking main polling loop."""
       # All the task execution logic moved here
   ```

4. **Enhanced concurrency tracking**:
   ```python
   with self.running_tasks_lock:
       thread_pool_running = len(self.running_task_futures)
   sqlite_running_count = self._count_running_tasks()
   total_running = max(sqlite_running_count, thread_pool_running)
   ```

5. **Continuous polling with status logging**:
   ```python
   if active_tasks > 0:
       self.logger.debug(f"Polling for new tasks ({active_tasks} currently executing in background)")
   ```

### Benefits

- **✅ Daemon Responsiveness**: Main polling loop never blocks, continues logging every poll interval
- **✅ Network Error Visibility**: Connection errors are logged immediately, daemon doesn't appear frozen
- **✅ Concurrent Task Execution**: Multiple tasks can run simultaneously up to `max_concurrent_tasks` limit
- **✅ Graceful Shutdown**: Proper cleanup of thread pool and running tasks
- **✅ Backward Compatibility**: All existing functionality preserved

### Test Results

**Before Fix** (blocking):
- 5 poll cycles in 10 seconds (blocked during 5-second task)
- Daemon appears frozen when Agent Board is down

**After Fix** (threaded):
- 10 poll cycles in 10 seconds (continuous polling)
- Daemon logs "Agent Board connection error" messages, remains responsive

## Files Modified

- `lib/daemon.py`: Main implementation with threading changes
- Added comprehensive test files demonstrating the fix

## Testing

Run the verification test:
```bash
python test_polling_vs_blocking.py
```

Expected output:
```
✅ SUCCESS: New approach allows more polling (daemon appears responsive)
✅ FIX CONFIRMED: Daemon will no longer appear frozen during task execution
```

## Impact

This fix resolves the QA testing issue where daemon appeared frozen after Agent Board server was killed, allowing destructive tests to be run without falsely appearing to break the daemon.