import sqlite3
import json
import os
import pathlib
import shutil
import datetime

DEFAULT_DB_PATH = os.path.expanduser('~/.rapper/tasks.db')

# Global database path variable
db_path = None

def init_db(path=None):
    """Initialize database and migrate from JSON files if needed."""
    global db_path
    db_path = path if path is not None else DEFAULT_DB_PATH

    # Ensure parent directory exists
    pathlib.Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")

    # Create tasks table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            name TEXT,
            status TEXT,
            pid INTEGER,
            board_task_id TEXT,
            worktree_path TEXT,
            branch_name TEXT,
            repo_workdir TEXT,
            result TEXT,
            structured_result TEXT,
            error TEXT,
            fail_reason TEXT,
            session_id TEXT,
            claude_version TEXT,
            max_budget_usd REAL,
            fallback_model TEXT,
            created_at TEXT,
            updated_at TEXT,
            completed_at TEXT
        )
    """)

    # Check if table is empty
    cursor = conn.execute("SELECT COUNT(*) FROM tasks")
    count = cursor.fetchone()[0]

    if count == 0:
        # Check for existing JSON files to migrate
        json_dir = pathlib.Path.home() / ".rapper" / "tasks"
        if json_dir.exists():
            json_files = list(json_dir.glob("*.json"))

            if json_files:
                print(f"Migrating {len(json_files)} tasks from JSON to SQLite...")

                for json_file in json_files:
                    try:
                        with open(json_file, 'r') as f:
                            task_data = json.load(f)

                        # Insert task data into database
                        conn.execute("""
                            INSERT INTO tasks (
                                id, name, status, pid, board_task_id, worktree_path,
                                branch_name, repo_workdir, result, structured_result,
                                error, fail_reason, session_id, claude_version,
                                max_budget_usd, fallback_model, created_at,
                                updated_at, completed_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            task_data.get('id'),
                            task_data.get('name'),
                            task_data.get('status'),
                            task_data.get('pid'),
                            task_data.get('board_task_id'),
                            task_data.get('worktree_path'),
                            task_data.get('branch_name'),
                            task_data.get('repo_workdir'),
                            task_data.get('result'),
                            json.dumps(task_data.get('structured_result')) if task_data.get('structured_result') else None,
                            task_data.get('error'),
                            task_data.get('fail_reason'),
                            task_data.get('session_id'),
                            task_data.get('claude_version'),
                            task_data.get('max_budget_usd'),
                            task_data.get('fallback_model'),
                            task_data.get('created_at'),
                            task_data.get('updated_at'),
                            task_data.get('completed_at')
                        ))
                    except Exception as e:
                        print(f"Warning: Failed to migrate {json_file}: {e}")

                # Move JSON files to archive
                today = datetime.datetime.now().strftime("%Y-%m-%d")
                archive_dir = json_dir.parent / "tasks-archive" / today
                archive_dir.mkdir(parents=True, exist_ok=True)

                for json_file in json_files:
                    try:
                        shutil.move(str(json_file), str(archive_dir / json_file.name))
                    except Exception as e:
                        print(f"Warning: Failed to archive {json_file}: {e}")

                print(f"Migration complete. JSON files archived to {archive_dir}")

    conn.commit()
    conn.close()

def get_running_count():
    """Get count of running tasks."""
    conn = sqlite3.connect(db_path)
    cursor = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='running'")
    count = cursor.fetchone()[0]
    conn.close()
    return count

def save_task(d):
    """Save task data to database."""
    conn = sqlite3.connect(db_path)

    # Handle structured_result serialization
    structured_result = None
    if d.get('structured_result'):
        if isinstance(d['structured_result'], dict):
            structured_result = json.dumps(d['structured_result'])
        else:
            structured_result = d['structured_result']

    conn.execute("""
        INSERT OR REPLACE INTO tasks (
            id, name, status, pid, board_task_id, worktree_path,
            branch_name, repo_workdir, result, structured_result,
            error, fail_reason, session_id, claude_version,
            max_budget_usd, fallback_model, created_at,
            updated_at, completed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        d.get('id'),
        d.get('name'),
        d.get('status'),
        d.get('pid'),
        d.get('board_task_id'),
        d.get('worktree_path'),
        d.get('branch_name'),
        d.get('repo_workdir'),
        d.get('result'),
        structured_result,
        d.get('error'),
        d.get('fail_reason'),
        d.get('session_id'),
        d.get('claude_version'),
        d.get('max_budget_usd'),
        d.get('fallback_model'),
        d.get('created_at'),
        d.get('updated_at'),
        d.get('completed_at')
    ))

    conn.commit()
    conn.close()

def load_task(task_id):
    """Load task data from database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    cursor = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        # Convert to dict and handle structured_result deserialization
        task = dict(row)
        if task.get('structured_result'):
            try:
                task['structured_result'] = json.loads(task['structured_result'])
            except json.JSONDecodeError:
                pass  # Keep as string if not valid JSON
        return task

    return None

def list_tasks(status=None):
    """List tasks from database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    if status:
        cursor = conn.execute("SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC", (status,))
    else:
        cursor = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC")

    rows = cursor.fetchall()
    conn.close()

    # Convert to list of dicts and handle structured_result deserialization
    tasks = []
    for row in rows:
        task = dict(row)
        if task.get('structured_result'):
            try:
                task['structured_result'] = json.loads(task['structured_result'])
            except json.JSONDecodeError:
                pass  # Keep as string if not valid JSON
        tasks.append(task)

    return tasks