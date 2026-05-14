#!/usr/bin/env python3
"""
Board tools for Agent Board integration — Native Kanban tools for Rapper.

Provides direct tool calls for Rapper Claude Code sessions to interact with
Agent Board without requiring manual curl commands or Hermes MCP intervention.

Tools:
- board_move_task — Move task to specified column
- board_add_comment — Add comment to task
- board_get_task — Get task details
- board_my_tasks — Get current agent's task list
"""

import json
import os
import urllib.request
import urllib.parse
from typing import Dict, List, Any, Optional


def get_board_config() -> Dict[str, Any]:
    """Load board tools configuration."""
    # Default configuration
    config = {
        "enabled": True,
        "api_url": "http://localhost:3456",
        "api_key": "sk-4429c0b2e53522a890b1c5ab6c0d1fcb",
        "agent_id": "rapper-1"
    }

    # Try to load from environment or config files
    rapper_dir = os.environ.get("RAPPER_DIR", "/app/rapper")

    # Check if we have a config file override
    try:
        import yaml
        config_path = os.path.expanduser("~/.rapper/config.yaml")
        if os.path.exists(config_path):
            with open(config_path) as f:
                user_config = yaml.safe_load(f) or {}
            if "board_tools" in user_config:
                config.update(user_config["board_tools"])
    except ImportError:
        pass  # yaml not available, use defaults
    except Exception:
        pass  # config load failed, use defaults

    # Environment variable overrides
    if os.environ.get("RAPPER_BOARD_API_URL"):
        config["api_url"] = os.environ["RAPPER_BOARD_API_URL"]
    if os.environ.get("RAPPER_BOARD_API_KEY"):
        config["api_key"] = os.environ["RAPPER_BOARD_API_KEY"]
    if os.environ.get("RAPPER_BOARD_AGENT_ID"):
        config["agent_id"] = os.environ["RAPPER_BOARD_AGENT_ID"]

    return config


def make_board_request(method: str, endpoint: str, data: Optional[Dict] = None) -> Dict[str, Any]:
    """Make HTTP request to Agent Board API."""
    config = get_board_config()

    if not config.get("enabled", True):
        raise RuntimeError("Board tools are disabled in configuration")

    url = f"{config['api_url']}/api{endpoint}"

    # Prepare headers
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Rapper-BoardTools/1.0"
    }

    if config.get("api_key"):
        headers["X-API-Key"] = config["api_key"]

    # Prepare request body
    body = None
    if data:
        body = json.dumps(data).encode('utf-8')

    try:
        req = urllib.request.Request(url, data=body, headers=headers, method=method)

        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status >= 400:
                raise RuntimeError(f"HTTP {response.status}: {response.reason}")

            response_text = response.read().decode('utf-8')
            if not response_text:
                return {}

            return json.loads(response_text)

    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode('utf-8')
        except:
            pass
        raise RuntimeError(f"HTTP {e.code} error: {e.reason}. {error_body}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error: {e.reason}")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON response: {e}")
    except Exception as e:
        raise RuntimeError(f"Request failed: {e}")


# Tool Function Implementations

def board_move_task(task_id: str, column: str, reason: Optional[str] = None) -> str:
    """Move a task to the specified column.

    Args:
        task_id: Task ID (e.g., 'task_7f25a48f')
        column: Target column ('todo', 'doing', 'blocked', 'done', 'failed', etc.)
        reason: Reason for blocking (required when moving to 'blocked')

    Returns:
        Success message with task details
    """
    try:
        # Get task details first to show what we're moving
        task = make_board_request("GET", f"/tasks/{task_id}")
        task_title = task.get("title", "Unknown Task")
        old_column = task.get("column", "unknown")

        # Prepare move data
        move_data = {"column": column}
        if reason:
            move_data["reason"] = reason
        elif column == "blocked":
            return f"❌ Error: Reason is required when moving task to 'blocked' column"

        # Move the task
        result = make_board_request("POST", f"/tasks/{task_id}/move", move_data)

        success_msg = f"✅ Moved task '{task_title}' from '{old_column}' to '{column}'"
        if column == "blocked" and reason:
            success_msg += f" (Reason: {reason})"

        return success_msg

    except Exception as e:
        return f"❌ Failed to move task {task_id}: {e}"


def board_add_comment(task_id: str, comment: str, author: Optional[str] = None) -> str:
    """Add a comment to the specified task.

    Args:
        task_id: Task ID (e.g., 'task_7f25a48f')
        comment: Comment text to add
        author: Comment author (defaults to current agent ID)

    Returns:
        Success message with comment details
    """
    try:
        config = get_board_config()

        if not author:
            author = config.get("agent_id", "rapper-agent")

        comment_data = {
            "author": author,
            "text": comment
        }

        result = make_board_request("POST", f"/tasks/{task_id}/comments", comment_data)

        # Get task title for better feedback
        try:
            task = make_board_request("GET", f"/tasks/{task_id}")
            task_title = task.get("title", "Unknown Task")
            return f"✅ Added comment to '{task_title}' as {author}"
        except:
            return f"✅ Added comment to task {task_id} as {author}"

    except Exception as e:
        return f"❌ Failed to add comment to task {task_id}: {e}"


def board_get_task(task_id: str) -> str:
    """Get detailed information about a specific task.

    Args:
        task_id: Task ID (e.g., 'task_7f25a48f')

    Returns:
        Task details in formatted text
    """
    try:
        task = make_board_request("GET", f"/tasks/{task_id}")

        # Format task details
        details = [
            f"📋 Task: {task.get('title', 'Untitled')}",
            f"🏷️  ID: {task.get('id', 'unknown')}",
            f"📍 Column: {task.get('column', 'unknown')}",
            f"👤 Assignee: {task.get('assignee', 'unassigned')}",
            f"🚦 Priority: {task.get('priority', 'normal')}",
        ]

        if task.get("description"):
            details.append(f"📝 Description: {task['description']}")

        if task.get("tags"):
            details.append(f"🏷️  Tags: {', '.join(task['tags'])}")

        if task.get("blockReason"):
            details.append(f"⛔ Block Reason: {task['blockReason']}")

        if task.get("dependencies"):
            details.append(f"🔗 Dependencies: {', '.join(task['dependencies'])}")

        if task.get("deadline"):
            details.append(f"⏰ Deadline: {task['deadline']}")

        if task.get("comments"):
            details.append(f"💬 Comments: {len(task['comments'])}")

        if task.get("createdAt"):
            details.append(f"📅 Created: {task['createdAt']}")

        if task.get("updatedAt"):
            details.append(f"🔄 Updated: {task['updatedAt']}")

        return "\n".join(details)

    except Exception as e:
        return f"❌ Failed to get task {task_id}: {e}"


def board_my_tasks(status: Optional[str] = None, limit: int = 10) -> str:
    """Get list of tasks assigned to the current agent.

    Args:
        status: Filter by task status/column ('todo', 'doing', 'done', etc.)
        limit: Maximum number of tasks to return (default 10)

    Returns:
        Formatted list of assigned tasks
    """
    try:
        config = get_board_config()
        agent_id = config.get("agent_id", "rapper-1")

        # Build query parameters
        params = {"assignee": agent_id}
        if status:
            params["status"] = status

        query_string = urllib.parse.urlencode(params)

        tasks = make_board_request("GET", f"/tasks?{query_string}")

        if not tasks or len(tasks) == 0:
            status_filter = f" in '{status}'" if status else ""
            return f"📭 No tasks found for {agent_id}{status_filter}"

        # Limit results
        if len(tasks) > limit:
            tasks = tasks[:limit]

        # Format task list
        result = [f"📋 Tasks for {agent_id} (showing {len(tasks)} tasks):"]

        for task in tasks:
            task_id = task.get("id", "unknown")
            title = task.get("title", "Untitled")
            column = task.get("column", "unknown")
            priority = task.get("priority", "normal")

            priority_emoji = {
                "urgent": "🔴",
                "high": "🟡",
                "normal": "🟢",
                "low": "🔵"
            }.get(priority, "⚪")

            result.append(f"  {priority_emoji} [{column}] {task_id}: {title}")

        if len(tasks) == limit and limit < 50:  # Suggest checking for more
            result.append(f"\n💡 Showing first {limit} tasks. Use board_my_tasks() with higher limit to see more.")

        return "\n".join(result)

    except Exception as e:
        return f"❌ Failed to get tasks: {e}"


def board_create_task(title: str, description: str, assignee: Optional[str] = None,
                      workdir: Optional[str] = None, column: str = "todo",
                      priority: str = "normal", project_id: Optional[str] = None) -> str:
    """Create a new task on the Agent Board.

    Args:
        title: Task title (required)
        description: Task description (required)
        assignee: Task assignee (optional, defaults to current agent)
        workdir: Working directory for task execution (optional, enables cross-project tasks)
        column: Initial column/status (default: 'todo')
        priority: Task priority ('urgent', 'high', 'normal', 'low')
        project_id: Project ID (optional)

    Returns:
        Success message with created task details
    """
    try:
        config = get_board_config()

        if not assignee:
            assignee = config.get("agent_id", "rapper-1")

        # Prepare task data
        task_data = {
            "title": title,
            "description": description,
            "assignee": assignee,
            "column": column,
            "priority": priority
        }

        # Add optional fields only if provided
        if workdir:
            task_data["workdir"] = workdir
        if project_id:
            task_data["projectId"] = project_id

        # Create the task
        result = make_board_request("POST", "/tasks", task_data)

        task_id = result.get("id", "unknown")
        success_msg = f"✅ Created task '{title}' (ID: {task_id})"

        if workdir:
            success_msg += f"\n📁 Workdir: {workdir}"

        success_msg += f"\n👤 Assignee: {assignee}"
        success_msg += f"\n📍 Column: {column}"

        if priority != "normal":
            success_msg += f"\n🚦 Priority: {priority}"

        return success_msg

    except Exception as e:
        return f"❌ Failed to create task: {e}"


# Tool Schema Definitions for Claude Code Integration

BOARD_TOOLS = {
    "board_move_task": {
        "description": "Move a task to a different column (status) on the Agent Board",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID (e.g., 'task_7f25a48f')"
                },
                "column": {
                    "type": "string",
                    "description": "Target column: 'backlog', 'todo', 'doing', 'blocked', 'done', 'failed', etc.",
                    "enum": ["backlog", "ready", "todo", "doing", "blocked", "review", "done", "failed"]
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for blocking (required when moving to 'blocked' column)"
                }
            },
            "required": ["task_id", "column"]
        }
    },
    "board_add_comment": {
        "description": "Add a comment to a task on the Agent Board",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID (e.g., 'task_7f25a48f')"
                },
                "comment": {
                    "type": "string",
                    "description": "Comment text to add to the task"
                },
                "author": {
                    "type": "string",
                    "description": "Comment author (optional, defaults to current agent)"
                }
            },
            "required": ["task_id", "comment"]
        }
    },
    "board_get_task": {
        "description": "Get detailed information about a specific task",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID (e.g., 'task_7f25a48f')"
                }
            },
            "required": ["task_id"]
        }
    },
    "board_my_tasks": {
        "description": "Get list of tasks assigned to the current agent",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter by status/column (optional): 'todo', 'doing', 'done', etc."
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of tasks to return (default 10, max 50)",
                    "minimum": 1,
                    "maximum": 50
                }
            },
            "required": []
        }
    },
    "board_create_task": {
        "description": "Create a new task on the Agent Board",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Task title"
                },
                "description": {
                    "type": "string",
                    "description": "Task description"
                },
                "assignee": {
                    "type": "string",
                    "description": "Task assignee (optional, defaults to current agent)"
                },
                "workdir": {
                    "type": "string",
                    "description": "Working directory for task execution (enables cross-project tasks, e.g. '/app/agent-board/repo')"
                },
                "column": {
                    "type": "string",
                    "description": "Initial column/status (default: 'todo')",
                    "enum": ["backlog", "ready", "todo", "doing", "blocked", "review", "done", "failed"],
                    "default": "todo"
                },
                "priority": {
                    "type": "string",
                    "description": "Task priority (default: 'normal')",
                    "enum": ["urgent", "high", "normal", "low"],
                    "default": "normal"
                },
                "project_id": {
                    "type": "string",
                    "description": "Project ID (optional)"
                }
            },
            "required": ["title", "description"]
        }
    }
}