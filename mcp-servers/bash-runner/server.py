#!/usr/bin/env python3
"""
bash-runner MCP Server for Rapper

Safe shell execution with dangerous command blocking.
Based on Pixel's bash_mcp.py implementation.

Features:
- Dangerous command detection and blocking
- Background task support
- Auto-background promotion
- Async execution to avoid blocking
"""
import asyncio
import json
import os
import re
import time
from typing import Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("bash-runner")

# --- Safety: blocked command patterns ---
# Ported from Pixel's bash_mcp.py
BLOCKED_PATTERNS = [
    # Destructive file operations
    r'\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+|.*-rf\b|.*-fr\b)',  # rm -rf, rm -fr, rm -f
    r'\brm\s+-[a-zA-Z]*r[a-zA-Z]*\s+/',                     # rm -r /...
    r'\brm\s+(-[a-zA-Z]*\s+)*/(?:etc|usr|bin|sbin|boot|var|root|home\b(?!/\w+/))',  # rm on system dirs
    r'\bmkfs\b',                                              # format filesystem
    r'\bdd\s+.*of=/',                                         # dd to device/root
    r'>\s*/dev/sd',                                           # overwrite block device
    r':\(\)\s*\{\s*:\|:\s*&\s*\}\s*;',                       # fork bomb
    # System damage
    r'\bshutdown\b',
    r'\breboot\b',
    r'\binit\s+[0-6]\b',
    r'\bsystemctl\s+(stop|disable|mask)\b',
    r'\bkillall\b',
    r'\bpkill\s+-9\b',
    # Credential/key theft
    r'\bcurl\b.*\b(password|token|secret|credential)',
    r'\bwget\b.*\b(password|token|secret|credential)',
    # Network abuse
    r'\bnc\s+-[a-zA-Z]*l',                                   # netcat listen
    r'\bnmap\b',
    # Permission escalation
    r'\bchmod\s+(-[a-zA-Z]*\s+)*777\s+/',                    # chmod 777 /...
    r'\bchown\s+.*\s+/',                                      # chown /...
    r'\bsudo\b',
    r'\bsu\s+-?\s*$',
    r'\bsu\s+root\b',
]

BLOCKED_COMPILED = [re.compile(p, re.IGNORECASE) for p in BLOCKED_PATTERNS]

# Indirect execution patterns that could hide dangerous commands
INDIRECT_EXEC_PATTERNS = [
    r'\bsh\s+-c\b',
    r'\bbash\s+-c\b',
    r'\bzsh\s+-c\b',
    r'\bksh\s+-c\b',
    r'\beval\b\s+[^$|]',     # `eval foo` — allow $FOO interp but not arbitrary arg
    r'\bexec\s+[a-z/]',       # `exec /bin/sh …`
]
INDIRECT_EXEC_COMPILED = [re.compile(p, re.IGNORECASE) for p in INDIRECT_EXEC_PATTERNS]


def _check_command(command: str) -> Optional[str]:
    """
    Check if a command is safe to execute.
    
    Returns:
        None if safe, error message string if blocked
    """
    # Check for blocked patterns
    for pattern in BLOCKED_COMPILED:
        if pattern.search(command):
            return f"BLOCKED: command matches safety pattern: {pattern.pattern}"
    
    # Check for indirect execution (could hide dangerous commands)
    for pattern in INDIRECT_EXEC_COMPILED:
        if pattern.search(command):
            return f"BLOCKED: indirect shell execution not allowed (security): {pattern.pattern}"
    
    return None


def _check_broad_search(command: str) -> Optional[str]:
    """
    Detect overly broad find/grep on high-level paths.
    Returns error message if too broad, None if OK.
    """
    cmd = command.strip()
    
    # Paths that are too broad for recursive search
    broad_roots = [
        r'/home/scratch[^/\s]*(?:\s|$|/\s)',  # /home/scratch.* root
        r'/home/[a-z]\w*\s',                   # /home/<user> root
        r'/\s',                                 # filesystem root
    ]
    
    # Only check find and grep-like commands
    if not re.search(r'\b(find|grep\s+-r|grep\s+-R|rg)\b', cmd):
        return None
    
    for pattern in broad_roots:
        if re.search(pattern, cmd):
            return (
                f"Command appears to search a very broad path. "
                f"Please specify a more specific directory to avoid scanning thousands of files."
            )
    
    return None


# Background task tracking
_background_tasks: dict[str, dict] = {}


async def _run_foreground(command: str, cwd: str, timeout: int) -> str:
    """Run command in foreground, wait for completion."""
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
            env={**os.environ, "TERM": "dumb"},
        )
        
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout
            )
            output = stdout.decode("utf-8", errors="replace") if stdout else ""
            
            # Truncate if too long
            if len(output) > 100000:
                output = output[:50000] + "\n\n... [truncated] ...\n\n" + output[-50000:]
            
            if proc.returncode != 0:
                output += f"\n(exit code: {proc.returncode})"
            
            return output or "(no output)"
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return f"Command timed out after {timeout} seconds"
    except Exception as e:
        return f"Error: {e}"


async def _run_background(command: str, cwd: str) -> str:
    """Run command in background, return immediately with PID."""
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
            env={**os.environ, "TERM": "dumb"},
            start_new_session=True,  # Detach from parent
        )
        
        task_id = f"bg-{proc.pid}-{int(time.time())}"
        _background_tasks[task_id] = {
            "pid": proc.pid,
            "command": command[:100],
            "cwd": cwd,
            "started": time.time(),
            "proc": proc,
        }
        
        return f"Background task started: {task_id} (PID {proc.pid})"
    except Exception as e:
        return f"Error starting background task: {e}"


async def _run_auto_background(command: str, cwd: str, wait_seconds: int) -> str:
    """Run command, auto-promote to background if still running after wait_seconds."""
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
            env={**os.environ, "TERM": "dumb"},
        )
        
        try:
            # Wait for specified seconds
            stdout, _ = await asyncio.wait_for(
                proc.communicate(),
                timeout=wait_seconds
            )
            # Completed within time
            output = stdout.decode("utf-8", errors="replace") if stdout else ""
            if proc.returncode != 0:
                output += f"\n(exit code: {proc.returncode})"
            return output or "(no output)"
        except asyncio.TimeoutError:
            # Still running, promote to background
            task_id = f"bg-{proc.pid}-{int(time.time())}"
            _background_tasks[task_id] = {
                "pid": proc.pid,
                "command": command[:100],
                "cwd": cwd,
                "started": time.time(),
                "proc": proc,
            }
            
            return (f"Command still running after {wait_seconds}s — "
                    f"promoted to background: {task_id} (PID {proc.pid})")
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
async def run_bash(
    command: str,
    timeout: int = 300,
    workdir: Optional[str] = None,
    background: bool = False,
    auto_background: Optional[int] = None,
) -> str:
    """
    Execute a bash command safely.
    
    Args:
        command: The bash command to execute
        timeout: Maximum execution time in seconds (default: 300, max: 600)
        workdir: Working directory for the command
        background: If True, run in background and return immediately with task ID
        auto_background: If set, run for N seconds then auto-promote to background if still running
    
    Returns:
        Command output, or error message if blocked/failed
    
    Safety:
        Dangerous commands (rm -rf, sudo, etc.) are automatically blocked.
    """
    timeout = min(timeout, 600)
    
    if not command:
        return "Error: command is required"
    
    # Safety check
    blocked = _check_command(command)
    if blocked:
        return blocked
    
    # Check for overly broad searches
    broad_error = _check_broad_search(command)
    if broad_error:
        return broad_error
    
    # Resolve working directory
    # Use RAPPER_WORKDIR env var if set (injected by task_runner for --background tasks),
    # otherwise fall back to os.getcwd().  This ensures bash commands run in the task's
    # specified --workdir rather than the bash-runner server's own CWD (/app/rapper).
    cwd = workdir or os.environ.get("RAPPER_WORKDIR") or os.getcwd()
    if not os.path.isdir(cwd):
        return f"Error: working directory does not exist: {cwd}"
    
    # Background mode
    if background:
        return await _run_background(command, cwd)
    
    # Auto-background mode
    if auto_background is not None and auto_background > 0:
        return await _run_auto_background(command, cwd, auto_background)
    
    # Normal foreground execution
    return await _run_foreground(command, cwd, timeout)


@mcp.tool()
async def check_background_task(task_id: str) -> str:
    """
    Check the status of a background task.
    
    Args:
        task_id: The task ID returned by run_bash with background=True
    
    Returns:
        Status information about the task
    """
    if task_id not in _background_tasks:
        return f"Task {task_id} not found"
    
    task = _background_tasks[task_id]
    proc = task.get("proc")
    
    if proc is None:
        return "Error: process handle not available"
    
    runtime = time.time() - task["started"]
    
    if proc.returncode is not None:
        return f"Task {task_id}: completed (exit code: {proc.returncode}, runtime: {runtime:.1f}s)"
    
    # Check if still running
    try:
        os.kill(proc.pid, 0)
        return f"Task {task_id}: running (PID {proc.pid}, runtime: {runtime:.1f}s)"
    except ProcessLookupError:
        return f"Task {task_id}: completed (runtime: {runtime:.1f}s)"


@mcp.tool()
async def kill_background_task(task_id: str) -> str:
    """
    Kill a background task.
    
    Args:
        task_id: The task ID to kill
    
    Returns:
        Result message
    """
    if task_id not in _background_tasks:
        return f"Task {task_id} not found"
    
    task = _background_tasks[task_id]
    proc = task.get("proc")
    
    if proc is None:
        return "Error: process handle not available"
    
    try:
        proc.terminate()
        await asyncio.sleep(0.5)
        if proc.returncode is None:
            proc.kill()
        
        del _background_tasks[task_id]
        return f"Task {task_id} killed"
    except Exception as e:
        return f"Error killing task: {e}"


@mcp.tool()
async def list_background_tasks() -> str:
    """
    List all background tasks.
    
    Returns:
        List of tasks with their status
    """
    if not _background_tasks:
        return "No background tasks"
    
    lines = ["Background tasks:"]
    for task_id, task in _background_tasks.items():
        proc = task.get("proc")
        status = "unknown"
        
        if proc and proc.returncode is not None:
            status = f"completed (exit {proc.returncode})"
        elif proc:
            try:
                os.kill(proc.pid, 0)
                status = "running"
            except ProcessLookupError:
                status = "completed"
        
        runtime = time.time() - task["started"]
        lines.append(f"  - {task_id}: {status} ({runtime:.1f}s) - {task.get('command', '?')}")
    
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
