---
name: sqlite-task-storage
description: SQLite database module for task management, replacing JSON persistence with improved concurrency and performance
metadata:
  type: specs
---

# SQLite Task Storage Design

## Overview

Replace the current JSON file-based task persistence in Rapper with SQLite database storage. This migration will improve concurrency handling, query performance, and data integrity while maintaining full backward compatibility.

## Current State

**Problem:** Current JSON-based persistence creates race conditions with concurrent task access and requires filesystem scanning for queries like `get_running_count()`. Each task is stored as individual JSON files in `~/.rapper/tasks/`:

- `{task_id}.json` - Task metadata and state
- `{task_id}.log` - Raw Claude output stream  
- `{task_id}.audit.json` - Structured audit events
- `{task_id}.progress` - Human-readable progress messages

**Solution:** Migrate task metadata to SQLite while preserving log/audit/progress files as filesystem artifacts.

## Architecture

### Database Schema

```sql
CREATE TABLE tasks (
    id TEXT PRIMARY KEY,           -- 20260512-232228-qsko
    name TEXT,                     -- mcp-workdir-fix
    status TEXT,                   -- pending|running|completed|failed|cancelled
    pid INTEGER,
    board_task_id TEXT,
    worktree_path TEXT,
    branch_name TEXT,
    repo_workdir TEXT,
    result TEXT,
    structured_result TEXT,        -- JSON string
    error TEXT,
    fail_reason TEXT,
    session_id TEXT,
    claude_version TEXT,
    max_budget_usd REAL,
    fallback_model TEXT,
    created_at TEXT,               -- ISO 8601 timestamp
    updated_at TEXT,               -- Auto-updated on save
    completed_at TEXT              -- Set when status becomes completed/failed/cancelled
);

-- Performance indexes
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_created_at ON tasks(created_at DESC);
CREATE INDEX idx_tasks_board_task_id ON tasks(board_task_id) WHERE board_task_id IS NOT NULL;
```

### Module Structure

**File:** `/app/rapper/lib/db.py`

```python
# Core API (matches existing Task interface)
def init_db(path: str = "~/.rapper/tasks.db") -> None
def get_running_count() -> int  
def save_task(task_dict: dict) -> None
def load_task(task_id: str) -> dict | None
def list_tasks(status: str | None = None, limit: int = 20) -> list[dict]

# Migration utilities
def migrate_from_json() -> tuple[int, list[str]]  # (migrated_count, errors)
def archive_json_files() -> bool
```

## Migration Strategy

### Automatic Migration Flow

1. **Detection:** `init_db()` checks if `~/.rapper/tasks.db` exists
2. **JSON Discovery:** If no DB, scan `~/.rapper/tasks/*.json` files
3. **Migration:** Parse each JSON file and INSERT into SQLite with validation
4. **Archival:** Move JSON files to `~/.rapper/tasks-archive/` on success
5. **Rollback:** On failure, leave JSON files in place and log errors

**Why:** Zero-downtime migration that preserves data integrity and allows manual recovery.

### Migration Error Handling

```python
def migrate_from_json() -> tuple[int, list[str]]:
    """
    Returns: (successfully_migrated_count, list_of_error_messages)
    
    Error cases:
    - Malformed JSON: Log error, continue with next file
    - Missing required fields: Use default values where possible
    - Database constraint violations: Skip duplicate IDs
    - Filesystem errors: Fail fast with clear error message
    """
```

**Partial Migration Recovery:** If migration fails partway through, successful records remain in SQLite, failed JSON files stay in original location for manual inspection.

## Data Flow Integration

### Task Lifecycle

```python
# Current (task_runner.py):
task = Task(...)
task.save()  # → JSON file

# New (via db.py):
task = Task(...)
save_task(task.__dict__)  # → SQLite + preserve Task interface
```

### Backward Compatibility

The `Task` dataclass remains unchanged. Only persistence layer (`task.save()` and `Task.load()`) gets updated to use `lib/db.py` instead of direct JSON operations.

**Migration Timeline:**
- Phase 1: Implement `lib/db.py` module
- Phase 2: Update `Task.save()` and `Task.load()` methods
- Phase 3: Update CLI commands (`rapper --status`, `rapper --tasks`)
- Phase 4: Remove JSON fallback code

## Error Handling & Recovery

### Database Corruption
- SQLite PRAGMA integrity_check on init
- Automatic backup creation before migration
- Fallback to read-only mode if corruption detected

### Concurrent Access
- Connection pooling with proper locking
- Atomic transactions for multi-operation updates
- Retry logic for SQLITE_BUSY errors

### Migration Failures
- Preserve original JSON files until migration fully completes
- Detailed error logging with file-specific failure reasons
- Manual recovery command: `rapper --recover-migration`

## Testing Strategy

### Unit Tests
- SQLite schema validation
- JSON migration accuracy (field mapping)
- Concurrent access scenarios
- Error handling coverage

### Integration Tests
- End-to-end task lifecycle with SQLite
- Migration from real task JSON files
- CLI command compatibility
- Performance comparison (JSON vs SQLite for common queries)

## Performance Impact

### Query Performance
- `get_running_count()`: O(n) file scan → O(1) indexed query
- `list_tasks(status)`: O(n) file parsing → O(log n) indexed retrieval
- Large task history: Linear degradation → Constant performance

### Storage Efficiency  
- JSON redundancy eliminated (field names stored once vs per record)
- Compressed TEXT storage for large fields (result, structured_result)
- Estimated 30-40% storage reduction for mature deployments

## Acceptance Criteria

### Functional Requirements
- [ ] `tasks.db` auto-created on first `init_db()` call
- [ ] All existing Task fields preserved in SQLite schema
- [ ] JSON migration completes without data loss
- [ ] Original JSON files moved to `tasks-archive/` post-migration
- [ ] `get_running_count()` returns identical results to JSON method
- [ ] CLI commands work identically with SQLite backend

### Non-Functional Requirements
- [ ] Migration completes in <10 seconds for 1000+ tasks
- [ ] Zero downtime: existing CLI commands work during migration
- [ ] Database file handles properly closed (no file descriptor leaks)
- [ ] Compatible with existing systemd service configuration

### Error Scenarios
- [ ] Migration failure leaves JSON files in original location
- [ ] Database corruption detected and handled gracefully
- [ ] Missing directory creation handled automatically
- [ ] Invalid task data migration skipped with logging

## Implementation Notes

### SQLite Pragmas
```sql
PRAGMA journal_mode = WAL;          -- Better concurrent access
PRAGMA synchronous = NORMAL;        -- Balanced durability/performance  
PRAGMA foreign_keys = ON;           -- Data integrity
PRAGMA temp_store = MEMORY;         -- Faster temp operations
```

### Connection Management
- Single connection per process (Rapper is predominantly single-threaded)
- Connection timeout handling for daemon mode
- Proper cleanup in signal handlers

**Why SQLite:** Embedded, zero-config, ACID compliance, and excellent Python support. No external database dependencies while gaining SQL query capabilities.

## Risk Mitigation

**Risk:** Migration corrupts existing task data
**Mitigation:** Atomic migration with rollback capability, comprehensive test coverage

**Risk:** Performance regression vs JSON for small task counts  
**Mitigation:** Benchmark testing, optimized indexes, connection pooling

**Risk:** SQLite file corruption in production
**Mitigation:** Regular integrity checks, backup automation, fallback procedures

This design maintains the simplicity of the current Task interface while providing the performance and concurrency benefits of proper database storage.