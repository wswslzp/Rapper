#!/usr/bin/env python3
"""
TaskRunner — Long-running Claude Code task manager for Rapper.

Manages background Claude tasks:
- Start tasks in subprocess with --dangerously-skip-permissions
- Monitor progress via stream-json output
- Save results to task files for Hermes to poll
- Support task cancellation and status queries

Task states: pending → running → completed | failed | cancelled
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from typing import Any, Callable
from lib.db import init_db, save_task as db_save, load_task as db_load, list_tasks as db_list

try:
    import yaml
except ImportError:
    yaml = None

# Task directory
TASK_DIR = Path(os.path.expanduser("~/.rapper/tasks"))
TASK_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class Task:
    """Represents a background Claude task."""
    id: str
    name: str
    prompt: str
    workdir: str
    status: str = "pending"  # pending, running, completed, failed, cancelled
    pid: int | None = None
    start_time: float | None = None
    end_time: float | None = None
    exit_code: int | None = None
    result: str | None = None
    structured_result: dict | None = None  # Parsed structured output from Claude
    error: str | None = None
    fail_reason: str | None = None   # error_max_turns | error_budget | (other)
    session_id: str | None = None    # Claude session ID for --resume continuation
    max_budget_usd: float | None = None   # Cost cap in USD
    fallback_model: str | None = None     # Fallback model on overload
    worktree_path: str | None = None      # 绝对路径，如 /app/rapper/.claude/worktrees/feat-auth
    branch_name: str | None = None        # 如 rapper/feat-auth
    repo_workdir: str | None = None       # 主 repo 路径（worktree 模式下与 workdir 不同）
    claude_version: str | None = None     # Claude Code version when task started
    board_task_id: str | None = None      # Agent Board task ID (e.g., task_7f25a48f)
    progress: list[dict] = field(default_factory=list)  # tool calls
    created_at: str | None = None         # ISO timestamp when task was first created
    completed_at: str | None = None       # ISO timestamp when task reached terminal status
    
    @property
    def task_file(self) -> Path:
        return TASK_DIR / f"{self.id}.json"
    
    @property
    def log_file(self) -> Path:
        return TASK_DIR / f"{self.id}.log"

    @property
    def audit_file(self) -> Path:
        return TASK_DIR / f"{self.id}.audit.json"

    @property
    def progress_file(self) -> Path:
        return TASK_DIR / f"{self.id}.progress"

    def save(self):
        """Save task state to database."""
        # Auto-set timestamps
        current_time_iso = datetime.fromtimestamp(time.time()).isoformat()

        # Set created_at if this is the first save
        if self.created_at is None:
            self.created_at = current_time_iso

        # Set completed_at if status is terminal and not already set
        terminal_statuses = {'completed', 'failed', 'cancelled'}
        if self.status in terminal_statuses and self.completed_at is None:
            self.completed_at = current_time_iso

        data = {
            "id": self.id,
            "name": self.name,
            "prompt": self.prompt[:500] + "..." if len(self.prompt) > 500 else self.prompt,
            "workdir": self.workdir,
            "workdir_effective": os.getcwd(),  # Actual current working directory
            "status": self.status,
            "pid": self.pid,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "exit_code": self.exit_code,
            "result": self.result,
            "structured_result": self.structured_result,
            "error": self.error,
            "fail_reason": self.fail_reason,
            "session_id": self.session_id,
            "max_budget_usd": self.max_budget_usd,
            "fallback_model": self.fallback_model,
            "worktree_path": self.worktree_path,
            "branch_name": self.branch_name,
            "repo_workdir": self.repo_workdir,
            "claude_version": self.claude_version,
            "board_task_id": self.board_task_id,
            "progress": self.progress[-20:],  # Keep last 20 tool calls
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "updated_at": time.time(),
        }
        db_save(data)
    
    @classmethod
    def load(cls, task_id: str) -> Task | None:
        """Load task from database."""
        data = db_load(task_id)
        if not data:
            return None
        try:
            task = cls(
                id=data["id"],
                name=data["name"],
                prompt=data["prompt"],
                workdir=data["workdir"],
                status=data.get("status", "unknown"),
                pid=data.get("pid"),
                start_time=data.get("start_time"),
                end_time=data.get("end_time"),
                exit_code=data.get("exit_code"),
                result=data.get("result"),
                structured_result=data.get("structured_result"),
                error=data.get("error"),
                fail_reason=data.get("fail_reason"),
                session_id=data.get("session_id"),
                max_budget_usd=data.get("max_budget_usd"),
                fallback_model=data.get("fallback_model"),
                worktree_path=data.get("worktree_path"),
                branch_name=data.get("branch_name"),
                repo_workdir=data.get("repo_workdir"),
                claude_version=data.get("claude_version"),
                board_task_id=data.get("board_task_id"),
                progress=data.get("progress", []),
                created_at=data.get("created_at"),
                completed_at=data.get("completed_at"),
            )
            return task
        except Exception:
            return None
    
    def elapsed(self) -> float:
        """Elapsed time in seconds."""
        if self.start_time is None:
            return 0
        end = self.end_time or time.time()
        return end - self.start_time
    
    def elapsed_str(self) -> str:
        """Human-readable elapsed time."""
        secs = int(self.elapsed())
        if secs < 60:
            return f"{secs}s"
        elif secs < 3600:
            return f"{secs // 60}m {secs % 60}s"
        else:
            return f"{secs // 3600}h {(secs % 3600) // 60}m"


def load_config() -> dict:
    """Load configuration from ~/.rapper/config.yaml with defaults."""
    config_path = os.path.expanduser("~/.rapper/config.yaml")
    defaults = {
        "progress_reporting": {
            "enabled": True,
            "report_every_n_tools": 5,
            "board_url": "http://localhost:3456"
        },
        "agent_board": {
            "api_key": ""
        },
        "board_tools": {
            "enabled": True,
            "api_url": "http://localhost:3456",
            "api_key": "sk-4429c0b2e53522a890b1c5ab6c0d1fcb",
            "agent_id": "rapper-1"
        }
    }

    if not yaml or not os.path.exists(config_path):
        return defaults

    try:
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}

        # Merge with defaults
        result = defaults.copy()
        if "progress_reporting" in config:
            result["progress_reporting"].update(config["progress_reporting"])
        if "agent_board" in config:
            result["agent_board"].update(config["agent_board"])
        if "board_tools" in config:
            result["board_tools"].update(config["board_tools"])

        return result
    except Exception:
        return defaults


def post_board_comment(board_task_id: str, message: str, config: dict) -> bool:
    """Post a comment to the Board task. Returns True if successful, False otherwise."""
    if not board_task_id:
        return False

    try:
        progress_config = config.get("progress_reporting", {})
        board_url = progress_config.get("board_url", "http://localhost:3456")
        api_key = config.get("agent_board", {}).get("api_key", "")

        url = f"{board_url}/api/tasks/{board_task_id}/comments"

        # Prepare the request data
        data = json.dumps({
            "content": message,
            "author": "rapper-agent"
        }).encode('utf-8')

        # Create the request
        req = urllib.request.Request(url, data=data, method='POST')
        req.add_header('Content-Type', 'application/json')

        if api_key:
            req.add_header('Authorization', f'Bearer {api_key}')

        # Make the request with timeout
        with urllib.request.urlopen(req, timeout=3) as response:
            return response.status == 200 or response.status == 201

    except Exception:
        # Silent failure - don't interrupt task execution
        return False


def generate_task_id() -> str:
    """Generate a unique task ID."""
    import random
    import string
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = ''.join(random.choices(string.ascii_lowercase, k=4))
    return f"{ts}-{suffix}"


def write_audit_event(task_id: str, event_type: str, **kwargs):
    """Write an audit event to the audit trail file.

    Args:
        task_id: Task identifier
        event_type: Type of event (task_start, tool_summary, task_end, error)
        **kwargs: Additional event data
    """
    audit_file = TASK_DIR / f"{task_id}.audit.json"

    event = {
        "type": event_type,
        "time": int(time.time()),
        **kwargs
    }

    # Load existing events or create new list
    events = []
    if audit_file.exists():
        try:
            with open(audit_file, "r") as f:
                data = json.load(f)
                events = data.get("events", [])
        except (json.JSONDecodeError, KeyError):
            events = []

    # Append new event
    events.append(event)

    # Write audit file
    audit_data = {
        "task_id": task_id,
        "events": events
    }

    # Atomic write
    tmp_file = audit_file.with_suffix(".tmp")
    with open(tmp_file, "w") as f:
        json.dump(audit_data, f, indent=2)
    tmp_file.rename(audit_file)


def write_progress(task_id: str, message: str):
    """Write a progress message to the progress file.

    Args:
        task_id: Task identifier
        message: Progress message to append
    """
    progress_file = TASK_DIR / f"{task_id}.progress"

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}\n"

    # Append to progress file
    with open(progress_file, "a") as f:
        f.write(line)


def setup_worktree(name: str, workdir: str) -> tuple[str, str]:
    """Create a git worktree for isolated development.

    Returns (worktree_path, branch_name).
    Raises subprocess.CalledProcessError if git commands fail.
    """
    import subprocess
    branch_name = f"rapper/{name}"
    worktree_path = os.path.join(workdir, ".claude", "worktrees", name)

    # Ensure parent directory exists
    os.makedirs(os.path.dirname(worktree_path), exist_ok=True)

    # Create worktree with new branch
    subprocess.run(
        ["git", "worktree", "add", worktree_path, "-b", branch_name],
        cwd=workdir,
        check=True,
        capture_output=True,
        text=True,
    )

    # Ensure git user identity is configured in the worktree so auto-commit never
    # fails due to missing user.email / user.name (worktrees in fresh repos may not
    # inherit a value if neither local nor global git config is set).
    for key, val in [("user.email", "rapper@localhost"), ("user.name", "Rapper Agent")]:
        chk = subprocess.run(
            ["git", "config", key],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
        if chk.returncode != 0 or not chk.stdout.strip():
            subprocess.run(
                ["git", "config", key, val],
                cwd=worktree_path,
                capture_output=True,
                text=True,
            )

    return worktree_path, branch_name


def _make_worktree_safe_prompt(prompt: str, repo_workdir: str, worktree_path: str) -> str:
    """Rewrite a prompt for safe execution inside a git worktree.

    Two transformations:
    1. Replace absolute paths pointing to the main repo with relative paths,
       so Claude Code's file tools operate inside the worktree instead of main repo.
    2. Prepend a strict isolation instruction so Claude Code knows it must
       stay within the worktree directory and use relative paths only.

    Args:
        prompt:        Original task prompt.
        repo_workdir:  Absolute path to the main repo (e.g. /app/myproject).
        worktree_path: Absolute path to this task's worktree (e.g. /app/myproject/.claude/worktrees/task-slug).

    Returns:
        Modified prompt with isolation guard prepended and absolute repo paths rewritten.
    """
    import re

    # Normalize: ensure repo_workdir has no trailing slash
    repo_root = repo_workdir.rstrip("/")

    # Replace occurrences of the repo root path with "." (relative to worktree cwd)
    # e.g. "/app/myproject/src/foo.py"  →  "./src/foo.py"
    # e.g. "/app/myproject/"            →  "./"
    # e.g. "/app/myproject and"         →  ". and"
    # Use word-boundary-aware replacement to avoid partial matches
    safe_prompt = re.sub(
        re.escape(repo_root) + r"(/)",
        r"./",
        prompt,
    )
    # Then replace standalone repo path (followed by space/end)
    safe_prompt = re.sub(
        re.escape(repo_root) + r"(?=\s|$)",
        ".",
        safe_prompt,
    )

    # Prepend isolation guard
    guard = (
        f"⚠️ WORKTREE ISOLATION GUARD ⚠️\n"
        f"You are running inside an isolated git worktree: {worktree_path}\n"
        f"The main repository is at: {repo_root}\n"
        f"CRITICAL RULES — you MUST follow these or your work will be lost:\n"
        f"1. Use ONLY relative paths for all file read/write/edit operations.\n"
        f"2. NEVER use the absolute path '{repo_root}' or any path outside your worktree.\n"
        f"3. Your current working directory IS the project root — treat '.' as the repo root.\n"
        f"4. Example: to edit 'src/foo.py', use path 'src/foo.py' or './src/foo.py', NOT '{repo_root}/src/foo.py'.\n"
        f"5. Do NOT run 'git checkout', 'git branch', or any command that switches branches.\n"
        f"--- END GUARD ---\n\n"
    )

    return guard + safe_prompt


def _get_board_tools_instructions() -> str:
    """Generate board tools instructions if enabled."""
    try:
        # Check if board tools are enabled
        config = load_config()
        board_config = config.get("board_tools", {})

        if not board_config.get("enabled", True):
            return ""

        rapper_dir = os.environ.get("RAPPER_DIR", "/app/rapper")

        board_instructions = f"""

AGENT BOARD TOOLS: You have access to native Agent Board integration via Python.
Import and use these functions to interact with the Kanban board:

```python
import sys
sys.path.append('{rapper_dir}/lib')
from board_tools import board_move_task, board_add_comment, board_get_task, board_my_tasks, board_create_task

# Move a task to different column
result = board_move_task("task_abc123", "doing")
print(result)

# Add comment to task
result = board_add_comment("task_abc123", "Started working on this")
print(result)

# Get task details
result = board_get_task("task_abc123")
print(result)

# List my assigned tasks
result = board_my_tasks("todo", 5)
print(result)

# Create a new task (cross-project example with workdir)
result = board_create_task(
    title="Fix auth bug in agent-board",
    description="Fix authentication issue in the Agent Board project",
    workdir="/app/agent-board/repo",  # Cross-project working directory
    assignee="rapper-2",
    priority="high"
)
print(result)
```

Functions available:
- **board_create_task(title, description, assignee=None, workdir=None, column='todo', priority='normal', project_id=None)**: Create new task (workdir enables cross-project tasks)
- **board_move_task(task_id, column)**: Move task to 'todo', 'doing', 'done', 'failed', etc.
- **board_add_comment(task_id, comment, author=None)**: Add comment to task
- **board_get_task(task_id)**: Get detailed task information
- **board_my_tasks(status=None, limit=10)**: List assigned tasks (filter by status, limit results)

API endpoint: {board_config.get('api_url', 'http://localhost:3456')}
Agent ID: {board_config.get('agent_id', 'rapper-1')}"""

        return board_instructions

    except Exception:
        # Silent failure - don't break task execution if board tools aren't available
        return ""


def _parse_structured_result(result_text: str) -> dict | None:
    """Parse structured result JSON from Claude's text output.

    Looks for JSON blocks in multiple formats and attempts to extract or infer
    the structured result. Returns a dict with status, output_path, pr_url, errors.
    """
    import re

    if not result_text:
        return None

    # Pattern 1: Look for JSON code blocks with structured_result wrapper
    json_pattern = r'```json\s*(\{[^`]*\})\s*```'
    matches = re.findall(json_pattern, result_text, re.DOTALL | re.IGNORECASE)

    for match in matches:
        try:
            parsed = json.loads(match)
            if isinstance(parsed, dict):
                # If it has structured_result key, return that
                if "structured_result" in parsed:
                    return parsed["structured_result"]

                # If it looks like a structured result itself (has status), use it directly
                if "status" in parsed:
                    return parsed

        except json.JSONDecodeError:
            continue

    # Pattern 2: Look for standalone JSON objects at the end
    lines = result_text.strip().split('\n')
    for i in range(len(lines) - 1, max(len(lines) - 15, 0) - 1, -1):
        line = lines[i].strip()
        if line.startswith('{') and line.endswith('}'):
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    # Check if it's a structured result wrapper or direct result
                    if "structured_result" in parsed:
                        return parsed["structured_result"]
                    elif "status" in parsed:
                        return parsed
            except json.JSONDecodeError:
                continue

    # Pattern 3: Look for any JSON-like object with key indicators throughout text
    json_objects = re.finditer(r'\{[^{}]*(?:"status"|"output_path"|"pr_url")[^{}]*\}', result_text)
    for match in json_objects:
        try:
            parsed = json.loads(match.group())
            if isinstance(parsed, dict) and ("status" in parsed or "output_path" in parsed):
                return parsed
        except json.JSONDecodeError:
            continue

    # Fallback: Create structured result from text analysis
    return _infer_structured_result(result_text)


def _infer_structured_result(result_text: str) -> dict:
    """Infer structured result from task output text when explicit JSON is not found.

    Analyzes the text for success/failure indicators and common patterns
    to create a basic structured result.
    """
    import re

    result = {
        "status": "unknown",
        "output_path": None,
        "pr_url": None,
        "errors": []
    }

    if not result_text:
        result["status"] = "failed"
        result["errors"] = ["No output generated"]
        return result

    text_lower = result_text.lower()

    # Detect status based on common indicators
    success_indicators = [
        "task completed", "successfully completed", "implementation complete",
        "done", "finished successfully", "task is complete", "✅", "✓",
        "all tests pass", "implementation successful"
    ]

    failure_indicators = [
        "error", "failed", "exception", "❌", "✗", "could not", "unable to",
        "failed to", "error occurred", "something went wrong"
    ]

    # Check for success indicators
    if any(indicator in text_lower for indicator in success_indicators):
        result["status"] = "completed"

    # Check for failure indicators (overrides success if found)
    if any(indicator in text_lower for indicator in failure_indicators):
        result["status"] = "failed"
        # Extract error messages
        error_lines = []
        for line in result_text.split('\n'):
            line_lower = line.lower()
            if any(indicator in line_lower for indicator in ["error:", "failed:", "exception:"]):
                error_lines.append(line.strip())
        if error_lines:
            result["errors"] = error_lines

    # If no clear indicators, assume partial completion
    if result["status"] == "unknown":
        result["status"] = "partial"

    # Try to detect output paths
    path_patterns = [
        r'(?:created?|wrote|generated?|saved?|modified?)\s+[`\'"]?([^\s`\'"]+\.[a-zA-Z]{1,4})[`\'"]?',
        r'(?:file|path|output):\s*[`\'"]?([^\s`\'"]+)[`\'"]?',
        r'[`\'"]([^\s`\'"]*\.[a-zA-Z]{2,4})[`\'"]',  # Generic file paths in quotes
    ]

    for pattern in path_patterns:
        matches = re.findall(pattern, result_text, re.IGNORECASE)
        if matches:
            # Filter out obviously non-path matches and take the first reasonable one
            for match in matches:
                if len(match) > 3 and ('/' in match or '\\' in match or '.' in match):
                    # Convert absolute paths to relative if they're under the workdir
                    if match.startswith('/'):
                        # Try to make it relative (basic heuristic)
                        path_parts = match.split('/')
                        if len(path_parts) > 2:
                            result["output_path"] = '/'.join(path_parts[-2:])  # last 2 parts
                        else:
                            result["output_path"] = match
                    else:
                        result["output_path"] = match
                    break
        if result["output_path"]:
            break

    # Try to detect PR URLs
    pr_patterns = [
        r'(?:pull request|PR|pr).*?(https://github\.com/[^\s]+)',
        r'(https://github\.com/[^\s]+/pull/\d+)',
    ]

    for pattern in pr_patterns:
        matches = re.findall(pattern, result_text, re.IGNORECASE)
        if matches:
            result["pr_url"] = matches[0]
            break

    return result


def _add_structured_result_instructions(prompt: str, task_id: str | None = None) -> str:
    """Add structured result output instructions to the prompt.

    Appends instructions for Claude to output structured result JSON
    at the end of the task completion, and optionally progress reporting instructions.
    """
    structured_instructions = """

🔥 CRITICAL: STRUCTURED RESULT REQUIRED 🔥

When you complete this task, you MUST include a structured result at the end of your response.
Use EXACTLY this JSON format (copy and paste the template):

```json
{"status": "completed", "output_path": "relative/path/to/main/file", "pr_url": null, "errors": []}
```

Required fields:
- status: MUST be "completed", "failed", or "partial"
- output_path: relative path to the primary file you created/modified (use null if none)
- pr_url: GitHub PR URL if you created one (use null otherwise)
- errors: array of error messages (use [] if none)

Examples:
✅ Success: {"status": "completed", "output_path": "src/auth.py", "pr_url": null, "errors": []}
✅ With PR: {"status": "completed", "output_path": "components/Login.tsx", "pr_url": "https://github.com/user/repo/pull/123", "errors": []}
❌ Failure: {"status": "failed", "output_path": null, "pr_url": null, "errors": ["Could not connect to database"]}

⚠️  This JSON will be parsed automatically - any formatting errors will cause integration failures!"""

    if task_id:
        progress_instructions = f"""

PROGRESS REPORTING: For long-running tasks, you can report progress by writing messages to your progress file. Use bash commands like:

```bash
echo "Started implementation phase" >> ~/.rapper/tasks/{task_id}.progress
echo "Completed file analysis, found 5 components" >> ~/.rapper/tasks/{task_id}.progress
echo "Generated tests, running validation" >> ~/.rapper/tasks/{task_id}.progress
```

These progress messages will be monitored by Hermes and can trigger notifications. Use this for major milestones or when tasks will take more than a few minutes."""
        structured_instructions += progress_instructions

    # Add board tools instructions if enabled
    board_tools_instructions = _get_board_tools_instructions()
    if board_tools_instructions:
        structured_instructions += board_tools_instructions

    return prompt + structured_instructions


def auto_commit_worktree(task: "Task") -> bool:
    """Auto-commit any uncommitted changes in a worktree after task completes.

    This ensures the branch has a proper commit so `rapper --merge` can merge it.
    Returns True if a commit was made or the worktree was already clean.
    Returns False if commit failed.
    """
    if not task.worktree_path or not os.path.isdir(task.worktree_path):
        print(
            f"[rapper/auto-commit] WARNING: task {task.id} has no valid worktree_path ({task.worktree_path})",
            file=sys.stderr,
        )
        return False

    try:
        # Ensure git user identity is set in the worktree so commits never fail
        # due to missing user.email / user.name (even in fresh repos with no local config).
        # We only set it if the worktree doesn't already inherit a value.
        for key, val in [("user.email", "rapper@localhost"), ("user.name", "Rapper Agent")]:
            chk = subprocess.run(
                ["git", "config", key],
                cwd=task.worktree_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if chk.returncode != 0 or not chk.stdout.strip():
                subprocess.run(
                    ["git", "config", key, val],
                    cwd=task.worktree_path,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )

        # Check for uncommitted changes
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=task.worktree_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        dirty = result.stdout.strip()
        if not dirty:
            return True  # Already clean, nothing to commit

        print(
            f"[rapper/auto-commit] task {task.id}: staging+committing uncommitted changes",
            file=sys.stderr,
        )

        # Stage all changes
        add_result = subprocess.run(
            ["git", "add", "-A"],
            cwd=task.worktree_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if add_result.returncode != 0:
            print(
                f"[rapper/auto-commit] ERROR: git add -A failed for task {task.id}: {add_result.stderr}",
                file=sys.stderr,
            )
            return False

        # Commit with task metadata
        task_name = task.name or task.id
        branch_short = (task.branch_name or "").replace("rapper/", "")
        commit_msg = f"feat({branch_short}): task '{task_name}' completed by rapper"
        commit_result = subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=task.worktree_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if commit_result.returncode != 0:
            print(
                f"[rapper/auto-commit] ERROR: git commit failed for task {task.id}: {commit_result.stderr}",
                file=sys.stderr,
            )
            return False

        print(
            f"[rapper/auto-commit] task {task.id}: committed — {commit_msg}",
            file=sys.stderr,
        )
        return True

    except subprocess.CalledProcessError as e:
        print(
            f"[rapper/auto-commit] ERROR: subprocess error for task {task.id}: {e}",
            file=sys.stderr,
        )
        return False
    except Exception as e:
        print(
            f"[rapper/auto-commit] ERROR: unexpected error for task {task.id}: {e}",
            file=sys.stderr,
        )
        return False


def remove_worktree(worktree_path: str, workdir: str) -> bool:
    """Remove a git worktree.

    Returns True on success, False on failure.
    """
    import subprocess
    try:
        subprocess.run(
            ["git", "worktree", "remove", worktree_path, "--force"],
            cwd=workdir,
            check=True,
            capture_output=True,
            text=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def list_tasks(status: str | None = None, limit: int = 20) -> list[Task]:
    """List all tasks, optionally filtered by status."""
    task_data_list = db_list(status)
    tasks = []
    for data in task_data_list:
        try:
            task = Task(
                id=data["id"],
                name=data["name"],
                prompt=data.get("prompt", ""),
                workdir=data.get("workdir", ""),
                status=data.get("status", "unknown"),
                pid=data.get("pid"),
                start_time=data.get("start_time"),
                end_time=data.get("end_time"),
                exit_code=data.get("exit_code"),
                result=data.get("result"),
                structured_result=data.get("structured_result"),
                error=data.get("error"),
                fail_reason=data.get("fail_reason"),
                session_id=data.get("session_id"),
                max_budget_usd=data.get("max_budget_usd"),
                fallback_model=data.get("fallback_model"),
                worktree_path=data.get("worktree_path"),
                branch_name=data.get("branch_name"),
                repo_workdir=data.get("repo_workdir"),
                claude_version=data.get("claude_version"),
                board_task_id=data.get("board_task_id"),
                progress=data.get("progress", []),
                created_at=data.get("created_at"),
                completed_at=data.get("completed_at"),
            )
            tasks.append(task)
            if len(tasks) >= limit:
                break
        except Exception:
            continue  # Skip invalid task data
    return tasks


def get_task(task_id: str) -> Task | None:
    """Get a task by ID, board task ID, or name prefix."""
    # Try exact match first
    task = Task.load(task_id)
    if task:
        return task

    # Try board task ID match
    for task_file in TASK_DIR.glob("*.json"):
        t = Task.load(task_file.stem)
        if t and t.board_task_id == task_id:
            return t

    # Try name prefix match
    for task_file in TASK_DIR.glob("*.json"):
        t = Task.load(task_file.stem)
        if t and t.name.startswith(task_id):
            return t

    return None


def cancel_task(task_id: str) -> bool:
    """Cancel a running task."""
    task = get_task(task_id)
    if not task:
        return False
    
    if task.status != "running" or not task.pid:
        return False
    
    try:
        # Kill process group
        os.killpg(os.getpgid(task.pid), signal.SIGTERM)
        time.sleep(0.5)
        # Force kill if still running
        try:
            os.killpg(os.getpgid(task.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        
        task.status = "cancelled"
        task.end_time = time.time()
        task.save()
        return True
    except ProcessLookupError:
        task.status = "cancelled"
        task.end_time = time.time()
        task.save()
        return True
    except Exception:
        return False


class TaskRunner:
    """Runs Claude Code tasks in background."""
    
    def __init__(self,
                 claude_path: str = "claude",
                 default_model: str = "claude-sonnet-4-20250514",
                 rapper_dir: str | None = None):
        self.claude_path = claude_path
        self.default_model = default_model
        self.rapper_dir = rapper_dir or os.environ.get("RAPPER_DIR", "/app/rapper")
        self._running_tasks: dict[str, subprocess.Popen] = {}
        init_db()
    
    def start_task(self,
                   name: str,
                   prompt: str,
                   workdir: str | None = None,
                   model: str | None = None,
                   max_turns: int = 50,
                   timeout: int = 3600,
                   allowed_tools: list[str] | None = None,
                   max_budget_usd: float | None = None,
                   fallback_model: str | None = None,
                   board_task_id: str | None = None,
                   on_complete: Callable[[Task], None] | None = None,
                   ) -> Task:
        """Start a new background task.
        
        Args:
            name: Human-readable task name
            prompt: The task prompt for Claude
            workdir: Working directory (default: current dir)
            model: Model to use (default: configured model)
            max_turns: Maximum tool-call turns
            timeout: Task timeout in seconds
            allowed_tools: List of allowed tools (default: all)
            on_complete: Callback when task completes
        
        Returns:
            Task object with ID for tracking
        """
        task_id = generate_task_id()
        workdir = workdir or os.getcwd()
        model = model or self.default_model

        # Add structured result and progress instructions to prompt
        enhanced_prompt = _add_structured_result_instructions(prompt, task_id)

        # Capture Claude Code version
        claude_version = None
        try:
            result = subprocess.run(
                [self.claude_path, "--version"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                claude_version = result.stdout.strip().split('\n')[0]  # First line only
        except Exception:
            pass  # Version capture is optional, don't fail task creation

        task = Task(
            id=task_id,
            name=name,
            prompt=enhanced_prompt,
            workdir=workdir,
            status="pending",
            max_budget_usd=max_budget_usd,
            fallback_model=fallback_model,
            claude_version=claude_version,
            board_task_id=board_task_id,
        )
        task.save()
        
        # Build command
        cmd = [
            self.claude_path,
            "-p",
            "--model", model,
            "--output-format", "stream-json",
            "--verbose",
            "--max-turns", str(max_turns),
            "--dangerously-skip-permissions",
        ]
        
        if max_budget_usd is not None:
            cmd.extend(["--max-budget-usd", str(max_budget_usd)])
        
        if fallback_model:
            cmd.extend(["--fallback-model", fallback_model])
        
        if allowed_tools:
            cmd.extend(["--allowedTools", ",".join(allowed_tools)])
        
        cmd.extend(["--", prompt])
        
        # Environment
        env = os.environ.copy()
        env["RAPPER_SCHEDULED"] = "1"  # Activate outbound guard
        env["RAPPER_TASK_ID"] = task_id
        env["RAPPER_DIR"] = self.rapper_dir
        # Pass workdir so bash-runner MCP (launched by claude) uses it as default CWD.
        # The bash-runner server always starts in /app/rapper (via uv --directory), so
        # os.getcwd() inside it is always /app/rapper regardless of Popen(cwd=...).
        # Setting RAPPER_WORKDIR lets it resolve the correct task working directory.
        env["RAPPER_WORKDIR"] = workdir
        
        # Start process
        log_file = open(task.log_file, "w")
        
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # Merge stderr into stdout for logging
                text=True,
                cwd=workdir,
                start_new_session=True,  # Create new process group
                env=env,
            )
        except Exception as e:
            task.status = "failed"
            task.error = str(e)
            task.end_time = time.time()

            # Write audit event for early failure
            write_audit_event(
                task.id,
                "error",
                message=str(e),
                duration_sec=int(task.elapsed()) if task.start_time else 0
            )

            task.save()
            log_file.close()
            return task

        task.status = "running"
        task.pid = proc.pid
        task.start_time = time.time()
        task.save()

        # Write audit event for task start
        write_audit_event(task.id, "task_start", agent_id="rapper-1")

        self._running_tasks[task_id] = proc
        
        # Start monitor thread
        monitor = threading.Thread(
            target=self._monitor_task,
            args=(task, proc, log_file, timeout, on_complete),
            daemon=True,
        )
        monitor.start()
        
        return task
    
    def _monitor_task(self,
                      task: Task,
                      proc: subprocess.Popen,
                      log_file,
                      timeout: int,
                      on_complete: Callable[[Task], None] | None):
        """Monitor a running task, collect output, handle completion."""
        start = time.time()
        text_parts = []
        final_result = None

        # Load configuration for progress reporting
        config = load_config()
        progress_config = config.get("progress_reporting", {})
        progress_enabled = progress_config.get("enabled", True)
        report_every = progress_config.get("report_every_n_tools", 5)
        tool_call_count = 0
        
        try:
            for line in proc.stdout:
                # Check timeout
                if time.time() - start > timeout:
                    proc.terminate()
                    time.sleep(1)
                    proc.kill()
                    task.status = "failed"
                    task.error = f"Timeout after {timeout}s"
                    break
                
                stripped = line.strip()
                if not stripped:
                    continue
                
                # Log raw output
                log_file.write(line)
                log_file.flush()
                
                try:
                    event = json.loads(stripped)
                    
                    # Extract tool calls for progress
                    tool = self._extract_tool_name(event)
                    if tool:
                        task.progress.append({
                            "tool": tool,
                            "time": time.time() - start,
                        })
                        task.save()

                        # Progress reporting to Board
                        if progress_enabled and task.board_task_id:
                            tool_call_count += 1
                            if tool_call_count % report_every == 0:
                                elapsed_str = f"{int(time.time() - start)}s"
                                progress_msg = (
                                    f"🔄 Progress update: {tool_call_count} steps completed "
                                    f"({elapsed_str} elapsed). Latest: {tool}"
                                )
                                post_board_comment(task.board_task_id, progress_msg, config)
                    
                    # Extract text
                    text = self._extract_text(event)
                    if text:
                        text_parts.append(text)
                    
                    # Check for final result
                    if event.get("type") == "result":
                        final_result = event.get("result", "")
                        subtype = event.get("subtype", "")
                        if event.get("session_id"):
                            task.session_id = event.get("session_id")
                        if subtype == "error_max_turns":
                            task.error = "Max turns exceeded"
                            task.fail_reason = "error_max_turns"
                        elif subtype == "error_budget":
                            task.error = "Budget exceeded"
                            task.fail_reason = "error_budget"
                        elif subtype and subtype != "success":
                            task.fail_reason = subtype
                
                except json.JSONDecodeError:
                    # Non-JSON output (rare)
                    text_parts.append(stripped)
            
            # Wait for process to finish
            proc.wait(timeout=10)
            
            # Set final status
            if task.status == "running":
                task.exit_code = proc.returncode
                if proc.returncode == 0:
                    task.status = "completed"
                    task.result = final_result or "\n".join(text_parts[-10:])
                    # Parse structured result from the text output
                    full_text = "\n".join(text_parts)
                    task.structured_result = _parse_structured_result(task.result or full_text)
                    # Auto-commit worktree changes so branch has a proper commit for --merge
                    if task.worktree_path:
                        auto_commit_worktree(task)
                else:
                    task.status = "failed"
                    task.error = task.error or f"Exit code {proc.returncode}"
                    task.result = "\n".join(text_parts[-10:])
        
        except Exception as e:
            task.status = "failed"
            task.error = str(e)
        
        finally:
            task.end_time = time.time()

            # Generate tool usage summary for audit
            if task.progress:
                tool_counts = Counter(entry['tool'] for entry in task.progress)
                write_audit_event(
                    task.id,
                    "tool_summary",
                    tools_used=list(tool_counts.keys()),
                    total_calls=len(task.progress),
                    tool_counts=dict(tool_counts)
                )

            # Write final audit event
            if task.status == "completed":
                write_audit_event(
                    task.id,
                    "task_end",
                    status="completed",
                    duration_sec=int(task.elapsed())
                )
            elif task.status == "failed":
                write_audit_event(
                    task.id,
                    "error",
                    message=task.error or "Task failed",
                    duration_sec=int(task.elapsed())
                )
            else:
                # Handle other statuses like cancelled
                write_audit_event(
                    task.id,
                    "task_end",
                    status=task.status,
                    duration_sec=int(task.elapsed())
                )

            task.save()

            # Post completion/failure comment to Board
            if progress_enabled and task.board_task_id:
                if task.status == "completed":
                    elapsed_str = f"{int(task.elapsed())}s"
                    steps_count = len(task.progress)
                    result_summary = ""
                    if task.structured_result:
                        status = task.structured_result.get('status', 'unknown')
                        output_path = task.structured_result.get('output_path', '')
                        if output_path:
                            result_summary = f" Output: {output_path}"
                    completion_msg = (
                        f"✅ Task completed in {elapsed_str} with {steps_count} steps.{result_summary}"
                    )
                    post_board_comment(task.board_task_id, completion_msg, config)
                elif task.status == "failed":
                    elapsed_str = f"{int(task.elapsed())}s"
                    steps_count = len(task.progress)
                    reason = task.fail_reason or "unknown error"
                    failure_msg = (
                        f"❌ Task failed after {elapsed_str} with {steps_count} steps. "
                        f"Reason: {reason}. Check: rapper --status {task.id}"
                    )
                    post_board_comment(task.board_task_id, failure_msg, config)

            log_file.close()
            self._running_tasks.pop(task.id, None)

            if on_complete:
                try:
                    on_complete(task)
                except Exception:
                    pass
    
    def _run_task_sync(self, task: Task, timeout: int = 3600, max_turns: int = 200):
        """Run a task synchronously (for daemon process)."""
        model = self.default_model
        
        # Build command
        cmd = [
            self.claude_path,
            "-p",
            "--model", model,
            "--output-format", "stream-json",
            "--verbose",
            "--max-turns", str(max_turns),
            "--dangerously-skip-permissions",
        ]

        if task.max_budget_usd is not None:
            cmd.extend(["--max-budget-usd", str(task.max_budget_usd)])

        if task.fallback_model:
            cmd.extend(["--fallback-model", task.fallback_model])

        cmd.extend(["--", task.prompt])
        
        # Environment
        env = os.environ.copy()
        env["RAPPER_SCHEDULED"] = "1"
        env["RAPPER_TASK_ID"] = task.id
        env["RAPPER_DIR"] = self.rapper_dir
        # Pass workdir so bash-runner MCP (launched by claude) uses it as default CWD.
        env["RAPPER_WORKDIR"] = task.workdir
        
        # Open log file
        log_file = open(task.log_file, "w")
        
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=task.workdir,
                start_new_session=False,  # Already in new session from fork
                env=env,
            )
        except Exception as e:
            task.status = "failed"
            task.error = str(e)
            task.end_time = time.time()

            # Write audit event for early failure
            write_audit_event(
                task.id,
                "error",
                message=str(e),
                duration_sec=int(task.elapsed()) if task.start_time else 0
            )

            task.save()
            log_file.close()
            return

        task.status = "running"
        task.pid = proc.pid
        task.start_time = time.time()
        task.save()

        # Write audit event for task start
        write_audit_event(task.id, "task_start", agent_id="rapper-1")

        # Monitor synchronously
        start = time.time()
        text_parts = []
        final_result = None

        # Load configuration for progress reporting
        config = load_config()
        progress_config = config.get("progress_reporting", {})
        progress_enabled = progress_config.get("enabled", True)
        report_every = progress_config.get("report_every_n_tools", 5)
        tool_call_count = 0
        
        try:
            for line in proc.stdout:
                if time.time() - start > timeout:
                    proc.terminate()
                    time.sleep(1)
                    proc.kill()
                    task.status = "failed"
                    task.error = f"Timeout after {timeout}s"
                    break
                
                stripped = line.strip()
                if not stripped:
                    continue
                
                log_file.write(line)
                log_file.flush()
                
                try:
                    event = json.loads(stripped)

                    tool = self._extract_tool_name(event)
                    if tool:
                        task.progress.append({
                            "tool": tool,
                            "time": time.time() - start,
                        })
                        task.save()

                        # Progress reporting to Board
                        if progress_enabled and task.board_task_id:
                            tool_call_count += 1
                            if tool_call_count % report_every == 0:
                                elapsed_str = f"{int(time.time() - start)}s"
                                progress_msg = (
                                    f"🔄 Progress update: {tool_call_count} steps completed "
                                    f"({elapsed_str} elapsed). Latest: {tool}"
                                )
                                post_board_comment(task.board_task_id, progress_msg, config)

                    text = self._extract_text(event)
                    if text:
                        text_parts.append(text)
                    
                    if event.get("type") == "result":
                        final_result = event.get("result", "")
                        subtype = event.get("subtype", "")
                        if event.get("session_id"):
                            task.session_id = event.get("session_id")
                        if subtype == "error_max_turns":
                            task.error = "Max turns exceeded"
                            task.fail_reason = "error_max_turns"
                        elif subtype == "error_budget":
                            task.error = "Budget exceeded"
                            task.fail_reason = "error_budget"
                        elif subtype and subtype != "success":
                            task.fail_reason = subtype
                
                except json.JSONDecodeError:
                    text_parts.append(stripped)
            
            proc.wait(timeout=10)
            
            if task.status == "running":
                task.exit_code = proc.returncode
                if proc.returncode == 0:
                    task.status = "completed"
                    task.result = final_result or "\n".join(text_parts[-10:])
                    # Parse structured result from the text output
                    full_text = "\n".join(text_parts)
                    task.structured_result = _parse_structured_result(task.result or full_text)
                    # Auto-commit worktree changes so branch has a proper commit for --merge
                    if task.worktree_path:
                        auto_commit_worktree(task)
                else:
                    task.status = "failed"
                    task.error = task.error or f"Exit code {proc.returncode}"
                    task.result = "\n".join(text_parts[-10:])
        
        except Exception as e:
            task.status = "failed"
            task.error = str(e)
        
        finally:
            task.end_time = time.time()

            # Generate tool usage summary for audit
            if task.progress:
                tool_counts = Counter(entry['tool'] for entry in task.progress)
                write_audit_event(
                    task.id,
                    "tool_summary",
                    tools_used=list(tool_counts.keys()),
                    total_calls=len(task.progress),
                    tool_counts=dict(tool_counts)
                )

            # Write final audit event
            if task.status == "completed":
                write_audit_event(
                    task.id,
                    "task_end",
                    status="completed",
                    duration_sec=int(task.elapsed())
                )
            elif task.status == "failed":
                write_audit_event(
                    task.id,
                    "error",
                    message=task.error or "Task failed",
                    duration_sec=int(task.elapsed())
                )
            else:
                # Handle other statuses like cancelled
                write_audit_event(
                    task.id,
                    "task_end",
                    status=task.status,
                    duration_sec=int(task.elapsed())
                )

            task.save()

            # Post completion/failure comment to Board
            if progress_enabled and task.board_task_id:
                if task.status == "completed":
                    elapsed_str = f"{int(task.elapsed())}s"
                    steps_count = len(task.progress)
                    result_summary = ""
                    if task.structured_result:
                        status = task.structured_result.get('status', 'unknown')
                        output_path = task.structured_result.get('output_path', '')
                        if output_path:
                            result_summary = f" Output: {output_path}"
                    completion_msg = (
                        f"✅ Task completed in {elapsed_str} with {steps_count} steps.{result_summary}"
                    )
                    post_board_comment(task.board_task_id, completion_msg, config)
                elif task.status == "failed":
                    elapsed_str = f"{int(task.elapsed())}s"
                    steps_count = len(task.progress)
                    reason = task.fail_reason or "unknown error"
                    failure_msg = (
                        f"❌ Task failed after {elapsed_str} with {steps_count} steps. "
                        f"Reason: {reason}. Check: rapper --status {task.id}"
                    )
                    post_board_comment(task.board_task_id, failure_msg, config)

            log_file.close()
    
    def _extract_tool_name(self, event: dict) -> str | None:
        """Extract tool name from stream event."""
        if event.get("type") == "assistant":
            message = event.get("message", {})
            content = message.get("content", [])
            for block in content:
                if block.get("type") == "tool_use":
                    return block.get("name")
        return None
    
    def _extract_text(self, event: dict) -> str | None:
        """Extract text content from stream event."""
        if event.get("type") == "assistant":
            message = event.get("message", {})
            content = message.get("content", [])
            for block in content:
                if block.get("type") == "text":
                    return block.get("text", "")
        return None


# CLI interface
def main():
    """CLI for task management."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Rapper Task Runner")
    sub = parser.add_subparsers(dest="command")
    
    # list
    list_p = sub.add_parser("list", help="List tasks")
    list_p.add_argument("--status", help="Filter by status")
    list_p.add_argument("--limit", type=int, default=10)
    
    # status
    status_p = sub.add_parser("status", help="Get task status")
    status_p.add_argument("task_id", help="Task ID or name")
    
    # logs
    logs_p = sub.add_parser("logs", help="View task logs")
    logs_p.add_argument("task_id", help="Task ID or name")
    logs_p.add_argument("--lines", "-n", type=int, default=50)
    
    # cancel
    cancel_p = sub.add_parser("cancel", help="Cancel a task")
    cancel_p.add_argument("task_id", help="Task ID or name")
    
    # run (for testing)
    run_p = sub.add_parser("run", help="Run a task")
    run_p.add_argument("name", help="Task name")
    run_p.add_argument("prompt", help="Task prompt")
    run_p.add_argument("--workdir", "-w", default=".")
    run_p.add_argument("--budget", type=float, help="Budget cap in USD")
    run_p.add_argument("--fallback", help="Fallback model on overload")
    run_p.add_argument("--worktree", action="store_true", help="Use git worktree isolation")
    run_p.add_argument("--max-turns", type=int, default=200, dest="max_turns", help="Maximum Claude turns (default: 200)")
    run_p.add_argument("--board-task-id", help="Agent Board task ID for binding")
    
    args = parser.parse_args()
    
    if args.command == "list":
        tasks = list_tasks(status=args.status, limit=args.limit)
        if not tasks:
            print("No tasks found.")
            return
        print(f"{'ID':<24} {'Name':<20} {'Status':<12} {'Elapsed':<10}")
        print("-" * 70)
        for t in tasks:
            print(f"{t.id:<24} {t.name[:20]:<20} {t.status:<12} {t.elapsed_str():<10}")
    
    elif args.command == "status":
        task = get_task(args.task_id)
        if not task:
            print(f"Task not found: {args.task_id}")
            sys.exit(1)
        print(f"ID:      {task.id}")
        print(f"Name:    {task.name}")
        print(f"Status:  {task.status}")
        print(f"Workdir: {task.workdir}")
        if task.board_task_id:
            print(f"Board ID: {task.board_task_id}")
        if task.worktree_path:
            print(f"Worktree: {task.worktree_path}")
        if task.branch_name:
            print(f"Branch:   {task.branch_name}")
        print(f"Elapsed: {task.elapsed_str()}")
        if task.pid:
            print(f"PID:     {task.pid}")
        if task.error:
            print(f"Error:   {task.error}")
        if task.fail_reason:
            print(f"Reason:  {task.fail_reason}")
        if task.session_id:
            print(f"Session: {task.session_id}  # use with: claude -p '...' --resume <session_id>")
        if task.structured_result:
            print(f"\nStructured Result:")
            print(f"  Status:      {task.structured_result.get('status', 'unknown')}")
            print(f"  Output Path: {task.structured_result.get('output_path') or '(none)'}")
            print(f"  PR URL:      {task.structured_result.get('pr_url') or '(none)'}")
            print(f"  Errors:      {task.structured_result.get('errors') or []}")
        else:
            print(f"\nStructured Result: Not available (task may have completed before structured result parsing was implemented)")

        # For Hermes integration, also output a machine-readable JSON line
        if task.status in ["completed", "failed"]:
            import json
            hermes_result = {
                "task_id": task.id,
                "status": task.status,
                "structured_result": task.structured_result or {
                    "status": task.status,
                    "output_path": None,
                    "pr_url": None,
                    "errors": [task.error] if task.error else []
                }
            }
            print(f"\n# HERMES_INTEGRATION_JSON: {json.dumps(hermes_result)}")
        if task.result:
            print(f"\nResult:\n{task.result[:1000]}")
        if task.progress:
            print(f"\nRecent tools ({len(task.progress)} total):")
            for p in task.progress[-5:]:
                print(f"  - {p['tool']} ({p['time']:.1f}s)")
    
    elif args.command == "logs":
        task = get_task(args.task_id)
        if not task:
            print(f"Task not found: {args.task_id}")
            sys.exit(1)
        if not task.log_file.exists():
            print("No log file.")
            return
        with open(task.log_file) as f:
            lines = f.readlines()
        for line in lines[-args.lines:]:
            print(line.rstrip())
    
    elif args.command == "cancel":
        if cancel_task(args.task_id):
            print(f"Cancelled: {args.task_id}")
        else:
            print(f"Failed to cancel: {args.task_id}")
            sys.exit(1)
    
    elif args.command == "run":
        # For CLI run, we need to daemonize properly
        # First fork detaches from parent
        task_id = generate_task_id()
        workdir = os.path.abspath(args.workdir)

        # Setup worktree if requested
        worktree_path = None
        branch_name = None
        repo_workdir = None
        prompt = args.prompt
        if args.worktree:
            try:
                repo_workdir = workdir  # Save original repo path before overwriting
                worktree_path, branch_name = setup_worktree(args.name, workdir)
                workdir = worktree_path
                # Rewrite prompt: replace absolute repo paths with relative paths
                # and prepend isolation guard instruction
                prompt = _make_worktree_safe_prompt(prompt, repo_workdir, worktree_path)
            except subprocess.CalledProcessError as e:
                print(f"Failed to create worktree: {e}")
                sys.exit(1)

        # Add structured result and progress instructions to prompt
        enhanced_prompt = _add_structured_result_instructions(prompt, task_id)

        task = Task(
            id=task_id,
            name=args.name,
            prompt=enhanced_prompt,
            workdir=workdir,
            status="pending",
            max_budget_usd=args.budget,
            fallback_model=args.fallback,
            worktree_path=worktree_path,
            branch_name=branch_name,
            repo_workdir=repo_workdir,
            board_task_id=getattr(args, 'board_task_id', None),
        )
        task.save()
        
        print(f"Started task: {task.id}")
        print(f"Status: {task.status}")
        print(f"Log: {task.log_file}")
        
        # Double-fork to daemonize
        pid = os.fork()
        if pid > 0:
            # Parent exits immediately
            sys.exit(0)
        
        # First child: create new session
        os.setsid()
        
        pid = os.fork()
        if pid > 0:
            # First child exits
            os._exit(0)
        
        # Second child: the actual daemon
        # Close standard file descriptors
        sys.stdin.close()

        # Change to task workdir before running Claude
        try:
            os.chdir(task.workdir)
        except OSError as e:
            task.status = "failed"
            task.error = f"Failed to change directory to {task.workdir}: {e}"
            task.end_time = time.time()

            # Write audit event for directory change failure
            write_audit_event(
                task.id,
                "error",
                message=f"Failed to change directory to {task.workdir}: {e}",
                duration_sec=0
            )

            task.save()
            os._exit(1)

        # Run the task synchronously in daemon process
        runner = TaskRunner()
        runner._run_task_sync(task, max_turns=args.max_turns)
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
