# Pitfall #31 Fix: Historical Todo Tasks Blocking Daemon Pickup

## Problem Description

**Phenomenon**: Board 上存在大量历史 `todo` 任务（实际已完成但状态未更新）时，Daemon 的 `picked_tasks` 去重集合会阻止新任务被领取。

**Root Cause**: Daemon polls `column=todo&assignee=rapper-N` and fetches ALL historical tasks. The `picked_tasks` deduplication set grows indefinitely because:
1. Task IDs are added to `~/.rapper/daemon_picked.json` when picked
2. **No cleanup mechanism** removes completed task IDs during normal operation
3. File is only cleared at daemon startup (`_clear_old_picked_tasks()`)

**Impact**: If historical `todo` tasks accumulate (due to Hermes forgetting to move to `done`), the deduplication logic blocks new task pickup.

## Diagnosis

Check for historical task bloat:
```bash
curl -H "X-API-Key: ..." "http://localhost:3456/api/tasks?assignee=rapper-1&column=todo" \
  | python3 -c "import sys,json; ts=json.load(sys.stdin); print(len(ts), 'todo tasks')"
```
**High risk**: > 10 historical todo tasks assigned to agent

## Solution Implementation

### 1. Periodic Cleanup (`_cleanup_completed_picked_tasks`)

**What**: Runs every 10 poll cycles to query Board for terminal states and remove them from `picked_tasks` file.

**Logic**:
```python
def _cleanup_completed_picked_tasks(self):
    # Query Board for done/failed tasks
    done_tasks = self.client.get_tasks(None, 'done')
    failed_tasks = self.client.get_tasks(None, 'failed')
    terminal_task_ids = {t['id'] for t in done_tasks + failed_tasks}
    
    # Remove terminal IDs from picked_tasks file
    cleaned_picked_tasks = picked_tasks - terminal_task_ids
```

**Frequency**: Every 10th poll cycle to balance cleanup vs API overhead.

### 2. Immediate Cleanup (`_remove_from_picked_tasks`)

**What**: Removes task ID from `picked_tasks` file as soon as task execution completes (success or failure).

**Logic**:
```python
def _remove_from_picked_tasks(self, task_id: str):
    picked_tasks = self._load_picked_tasks()
    if task_id in picked_tasks:
        picked_tasks.remove(task_id)
        # Save back to file
```

**When**: Called in both success and exception paths of `_execute_task_in_background()`.

### 3. Integration Points

| Location | Change | Purpose |
|----------|--------|---------|
| `__init__()` | Add `_poll_cycle_count = 0` | Track cycles for periodic cleanup |
| `_poll_and_execute_tasks()` | Increment counter, call cleanup every 10 cycles | Periodic maintenance |
| `_execute_task_in_background()` | Call `_remove_from_picked_tasks()` on completion | Immediate cleanup |

## Code Changes

**Files Modified**:
- `lib/daemon.py`: Add cleanup methods and integration points

**New Methods**:
- `_cleanup_completed_picked_tasks()`: Periodic cleanup by querying Board
- `_remove_from_picked_tasks()`: Immediate single-task removal

**Integration**:
- Poll cycle counter for cleanup scheduling  
- Cleanup calls in task completion paths

## Verification

**Test Script**: `test_pitfall_31_fix.py`

**Test Cases**:
1. Periodic cleanup removes terminal tasks correctly
2. Immediate cleanup removes specific tasks on completion
3. Cleanup frequency matches expected schedule (every 10 cycles)
4. Non-existent task removal doesn't crash

**Production Monitoring**:
```bash
# Check picked_tasks file size before/after
ls -la ~/.rapper/daemon_picked.json

# Monitor daemon logs for cleanup activity
grep -i "cleaned.*picked_tasks" ~/.rapper/logs/daemon.log
```

## Related Issues

- **Pitfall #32**: `_count_running_tasks` slow scanning (same root cause: data bloat)
- **Hermes Integration**: Root issue is Hermes not moving tasks to `done` status properly

## Long-term Prevention

1. **Hermes fix**: Ensure Hermes properly updates task status to `done` when tasks complete
2. **Board maintenance**: Periodic archival of old done/failed tasks
3. **Agent Board cleanup**: Built-in task lifecycle management to prevent accumulation

---

**Status**: ✅ Implemented and ready for testing
**Priority**: High (blocks daemon task pickup entirely when triggered)
**Backward Compatibility**: Full - no breaking changes to existing behavior