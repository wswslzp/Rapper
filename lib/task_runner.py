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
from typing import Any, Callable

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
        """Save task state to disk."""
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
            "updated_at": time.time(),
        }
        # Atomic write
        tmp = self.task_file.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        tmp.rename(self.task_file)
    
    @classmethod
    def load(cls, task_id: str) -> Task | None:
        """Load task from disk."""
        task_file = TASK_DIR / f"{task_id}.json"
        if not task_file.exists():
            return None
        try:
            with open(task_file) as f:
                data = json.load(f)
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
            )
            # Note: workdir_effective is not stored in Task object, only in JSON for status reporting
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


def _parse_structured_result(result_text: str) -> dict | None:
    """Parse structured result JSON from Claude's text output.

    Looks for JSON blocks in the format:
    ```json
    {"structured_result": {...}}
    ```

    Returns the structured_result dict if found, None otherwise.
    """
    import re

    if not result_text:
        return None

    # Look for JSON code blocks
    json_pattern = r'```json\s*(\{[^`]+\})\s*```'
    matches = re.findall(json_pattern, result_text, re.DOTALL | re.IGNORECASE)

    for match in matches:
        try:
            parsed = json.loads(match)
            if isinstance(parsed, dict) and "structured_result" in parsed:
                return parsed["structured_result"]
        except json.JSONDecodeError:
            continue

    # Also try to find standalone JSON at end of text
    lines = result_text.strip().split('\n')
    for i in range(len(lines) - 1, max(len(lines) - 10, 0) - 1, -1):
        line = lines[i].strip()
        if line.startswith('{') and line.endswith('}'):
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict) and "structured_result" in parsed:
                    return parsed["structured_result"]
            except json.JSONDecodeError:
                continue

    return None


def _add_structured_result_instructions(prompt: str, task_id: str | None = None) -> str:
    """Add structured result output instructions to the prompt.

    Appends instructions for Claude to output structured result JSON
    at the end of the task completion, and optionally progress reporting instructions.
    """
    structured_instructions = """

IMPORTANT: When you complete this task, include a structured result at the end of your response in the following JSON format:

```json
{"structured_result": {"status": "completed", "output_path": "path/to/artifact", "pr_url": null, "errors": []}}
```

The structured_result should contain:
- status: "completed", "failed", or "partial"
- output_path: relative path to main artifact/file created/modified (if any)
- pr_url: GitHub pull request URL if one was created (null otherwise)
- errors: array of error messages (if any)

This structured result will be parsed automatically for integration with Hermes."""

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

    return prompt + structured_instructions


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
    tasks = []
    for task_file in sorted(TASK_DIR.glob("*.json"), reverse=True):
        task = Task.load(task_file.stem)
        if task and (status is None or task.status == status):
            tasks.append(task)
        if len(tasks) >= limit:
            break
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
            print(f"  Status: {task.structured_result.get('status', 'unknown')}")
            if task.structured_result.get('output_path'):
                print(f"  Output: {task.structured_result['output_path']}")
            if task.structured_result.get('pr_url'):
                print(f"  PR URL: {task.structured_result['pr_url']}")
            if task.structured_result.get('errors'):
                print(f"  Errors: {task.structured_result['errors']}")
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
