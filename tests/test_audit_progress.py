#!/usr/bin/env python3
"""
Test audit logging and progress reporting functionality.
"""

import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add lib to path for imports
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.task_runner import (
    write_audit_event,
    write_progress,
    Task,
    TASK_DIR
)


def test_write_audit_event():
    """Test writing audit events to audit file."""
    test_task_id = "test-20260430-120000-abcd"
    audit_file = TASK_DIR / f"{test_task_id}.audit.json"

    # Clean up any existing audit file
    if audit_file.exists():
        audit_file.unlink()

    # Write first audit event
    write_audit_event(test_task_id, "task_start", agent_id="rapper-1")

    assert audit_file.exists()

    # Read and verify audit file
    with open(audit_file) as f:
        data = json.load(f)

    assert data["task_id"] == test_task_id
    assert len(data["events"]) == 1
    assert data["events"][0]["type"] == "task_start"
    assert data["events"][0]["agent_id"] == "rapper-1"
    assert "time" in data["events"][0]

    # Write second audit event (tool summary)
    write_audit_event(
        test_task_id,
        "tool_summary",
        tools_used=["Read", "Edit"],
        total_calls=5
    )

    # Verify second event was appended
    with open(audit_file) as f:
        data = json.load(f)

    assert len(data["events"]) == 2
    assert data["events"][1]["type"] == "tool_summary"
    assert data["events"][1]["tools_used"] == ["Read", "Edit"]
    assert data["events"][1]["total_calls"] == 5

    # Write completion event
    write_audit_event(test_task_id, "task_end", status="completed", duration_sec=120)

    # Verify final audit structure
    with open(audit_file) as f:
        data = json.load(f)

    assert len(data["events"]) == 3
    assert data["events"][2]["type"] == "task_end"
    assert data["events"][2]["status"] == "completed"
    assert data["events"][2]["duration_sec"] == 120

    # Clean up
    audit_file.unlink()
    print("✅ Audit event logging tests passed")


def test_write_progress():
    """Test writing progress messages to progress file."""
    test_task_id = "test-20260430-120001-efgh"
    progress_file = TASK_DIR / f"{test_task_id}.progress"

    # Clean up any existing progress file
    if progress_file.exists():
        progress_file.unlink()

    # Write progress messages
    write_progress(test_task_id, "Started implementation phase")
    write_progress(test_task_id, "Completed file analysis, found 5 components")
    write_progress(test_task_id, "Generated tests, running validation")

    assert progress_file.exists()

    # Read and verify progress file content
    with open(progress_file) as f:
        lines = f.readlines()

    assert len(lines) == 3
    assert "Started implementation phase" in lines[0]
    assert "Completed file analysis, found 5 components" in lines[1]
    assert "Generated tests, running validation" in lines[2]

    # Each line should have timestamp format [YYYY-MM-DD HH:MM:SS]
    for line in lines:
        assert line.startswith("[")
        assert "] " in line
        timestamp_part = line.split("] ")[0] + "]"
        assert len(timestamp_part) == 21  # [YYYY-MM-DD HH:MM:SS]

    # Clean up
    progress_file.unlink()
    print("✅ Progress reporting tests passed")


def test_task_audit_properties():
    """Test Task class audit and progress file properties."""
    task = Task(
        id="test-20260430-120002-ijkl",
        name="test-task",
        prompt="Test prompt",
        workdir="/tmp"
    )

    # Test property paths
    expected_audit = TASK_DIR / "test-20260430-120002-ijkl.audit.json"
    expected_progress = TASK_DIR / "test-20260430-120002-ijkl.progress"

    assert task.audit_file == expected_audit
    assert task.progress_file == expected_progress

    print("✅ Task audit properties tests passed")


def test_audit_file_format():
    """Test audit file matches expected format specification."""
    test_task_id = "format-test-20260430-120003-mnop"

    # Create a full audit trail as specified
    write_audit_event(test_task_id, "task_start", agent_id="rapper-1")
    write_audit_event(
        test_task_id,
        "tool_summary",
        tools_used=["Read", "Edit", "Bash"],
        total_calls=15,
        tool_counts={"Read": 5, "Edit": 7, "Bash": 3}
    )
    write_audit_event(test_task_id, "task_end", status="completed", duration_sec=120)

    audit_file = TASK_DIR / f"{test_task_id}.audit.json"

    # Verify audit file format matches specification
    with open(audit_file) as f:
        data = json.load(f)

    # Top-level structure
    assert "task_id" in data
    assert "events" in data
    assert data["task_id"] == test_task_id

    events = data["events"]
    assert len(events) == 3

    # Event 1: task_start
    start_event = events[0]
    assert start_event["type"] == "task_start"
    assert isinstance(start_event["time"], int)
    assert start_event["agent_id"] == "rapper-1"

    # Event 2: tool_summary
    tool_event = events[1]
    assert tool_event["type"] == "tool_summary"
    assert isinstance(tool_event["time"], int)
    assert tool_event["tools_used"] == ["Read", "Edit", "Bash"]
    assert tool_event["total_calls"] == 15
    assert tool_event["tool_counts"] == {"Read": 5, "Edit": 7, "Bash": 3}

    # Event 3: task_end
    end_event = events[2]
    assert end_event["type"] == "task_end"
    assert isinstance(end_event["time"], int)
    assert end_event["status"] == "completed"
    assert end_event["duration_sec"] == 120

    # Clean up
    audit_file.unlink()
    print("✅ Audit file format tests passed")


if __name__ == "__main__":
    print("Running audit logging and progress reporting tests...")
    print()

    try:
        test_write_audit_event()
        test_write_progress()
        test_task_audit_properties()
        test_audit_file_format()

        print()
        print("🎉 All tests passed! Audit logging and progress reporting implementation is working correctly.")

    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)