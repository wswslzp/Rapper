#!/usr/bin/env python3
"""
Agent Board MCP Server for Rapper

Provides MCP tool interface for Agent Board operations including task creation
with workdir support for cross-project tasks.

Tools:
- mcp_agent_board_board_create_task — Create new task with workdir support
- mcp_agent_board_board_move_task — Move task between columns
- mcp_agent_board_board_add_comment — Add comment to task
- mcp_agent_board_board_get_task — Get task details
- mcp_agent_board_board_my_tasks — List assigned tasks
"""

import os
import sys
from typing import Optional

# Add lib directory to path for board_tools import
lib_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'lib')
sys.path.insert(0, lib_dir)

try:
    from board_tools import (
        board_create_task, board_move_task, board_add_comment,
        board_get_task, board_my_tasks
    )
except ImportError as e:
    print(f"Error: Failed to import board tools: {e}", file=sys.stderr)
    sys.exit(1)

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("agent-board")


@mcp.tool()
async def mcp_agent_board_board_create_task(
    title: str,
    description: str,
    assignee: Optional[str] = None,
    workdir: Optional[str] = None,
    column: str = "todo",
    priority: str = "normal",
    project_id: Optional[str] = None
) -> str:
    """
    Create a new task on the Agent Board.

    Args:
        title: Task title (required)
        description: Task description (required)
        assignee: Task assignee (optional, defaults to current agent)
        workdir: Working directory for task execution (enables cross-project tasks)
        column: Initial column/status (default: 'todo')
        priority: Task priority ('urgent', 'high', 'normal', 'low')
        project_id: Project ID (optional)

    Returns:
        Success message with created task details
    """
    return board_create_task(
        title=title,
        description=description,
        assignee=assignee,
        workdir=workdir,
        column=column,
        priority=priority,
        project_id=project_id
    )


@mcp.tool()
async def mcp_agent_board_board_move_task(
    task_id: str,
    column: str,
    reason: Optional[str] = None
) -> str:
    """
    Move a task to the specified column.

    Args:
        task_id: Task ID (e.g., 'task_7f25a48f')
        column: Target column ('todo', 'doing', 'blocked', 'done', 'failed', etc.)
        reason: Reason for blocking (required when moving to 'blocked')

    Returns:
        Success message with task details
    """
    return board_move_task(task_id=task_id, column=column, reason=reason)


@mcp.tool()
async def mcp_agent_board_board_add_comment(
    task_id: str,
    comment: str,
    author: Optional[str] = None
) -> str:
    """
    Add a comment to the specified task.

    Args:
        task_id: Task ID (e.g., 'task_7f25a48f')
        comment: Comment text to add
        author: Comment author (defaults to current agent ID)

    Returns:
        Success message with comment details
    """
    return board_add_comment(task_id=task_id, comment=comment, author=author)


@mcp.tool()
async def mcp_agent_board_board_get_task(task_id: str) -> str:
    """
    Get detailed information about a specific task.

    Args:
        task_id: Task ID (e.g., 'task_7f25a48f')

    Returns:
        Task details in formatted text
    """
    return board_get_task(task_id=task_id)


@mcp.tool()
async def mcp_agent_board_board_my_tasks(
    status: Optional[str] = None,
    limit: int = 10
) -> str:
    """
    Get list of tasks assigned to the current agent.

    Args:
        status: Filter by task status/column ('todo', 'doing', 'done', etc.)
        limit: Maximum number of tasks to return (default 10)

    Returns:
        Formatted list of assigned tasks
    """
    return board_my_tasks(status=status, limit=limit)


if __name__ == "__main__":
    mcp.run()