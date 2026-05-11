#!/usr/bin/env python3
"""
Test script for KANBAN-007: Create a task with workdir field via API
"""

import json
import urllib.request
import urllib.parse
import sys

def create_task_with_workdir(title, description, workdir, assignee="rapper-1", column="todo", project_id="proj_6698248b156b0ba0"):
    """Create a task with workdir field via Agent Board API"""

    api_url = "http://localhost:3456"
    api_key = "sk-4429c0b2e53522a890b1c5ab6c0d1fcb"

    # Prepare task data
    task_data = {
        "title": title,
        "description": description,
        "column": column,
        "assignee": assignee,
        "workdir": workdir,
        "projectId": project_id
    }

    # Prepare headers
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": api_key,
        "User-Agent": "Rapper-KANBAN-007-Test/1.0"
    }

    # Make request
    try:
        url = f"{api_url}/api/tasks"
        body = json.dumps(task_data).encode('utf-8')

        req = urllib.request.Request(url, data=body, headers=headers, method='POST')

        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status >= 400:
                raise RuntimeError(f"HTTP {response.status}: {response.reason}")

            response_text = response.read().decode('utf-8')
            return json.loads(response_text) if response_text else {}

    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode('utf-8')
        except:
            pass
        raise RuntimeError(f"HTTP {e.code} error: {e.reason}. Body: {error_body}")
    except Exception as e:
        raise RuntimeError(f"Failed to create task: {e}")

def get_task(task_id):
    """Get task details to verify workdir field"""
    api_url = "http://localhost:3456"
    api_key = "sk-4429c0b2e53522a890b1c5ab6c0d1fcb"

    headers = {
        "X-API-Key": api_key,
        "User-Agent": "Rapper-KANBAN-007-Test/1.0"
    }

    try:
        url = f"{api_url}/api/tasks/{task_id}"
        req = urllib.request.Request(url, headers=headers, method='GET')

        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status >= 400:
                raise RuntimeError(f"HTTP {response.status}: {response.reason}")

            response_text = response.read().decode('utf-8')
            return json.loads(response_text) if response_text else {}

    except Exception as e:
        raise RuntimeError(f"Failed to get task: {e}")

def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python test_k007_create_task.py create <workdir>")
        print("  python test_k007_create_task.py get <task_id>")
        sys.exit(1)

    command = sys.argv[1].lower()

    if command == "create":
        if len(sys.argv) < 3:
            print("Usage: python test_k007_create_task.py create <workdir>")
            sys.exit(1)

        workdir = sys.argv[2]

        try:
            result = create_task_with_workdir(
                title="QA-K007-T1: Test workdir field",
                description="Test task for KANBAN-007 workdir field validation",
                workdir=workdir,
                assignee="rapper-1",
                column="todo"
            )

            print("✅ Task created successfully:")
            print(json.dumps(result, indent=2))

            if "id" in result:
                print(f"\n🔍 Task ID: {result['id']}")

        except Exception as e:
            print(f"❌ Error: {e}")
            sys.exit(1)

    elif command == "get":
        if len(sys.argv) < 3:
            print("Usage: python test_k007_create_task.py get <task_id>")
            sys.exit(1)

        task_id = sys.argv[2]

        try:
            task = get_task(task_id)
            print("✅ Task details:")
            print(json.dumps(task, indent=2))

            # Check workdir field specifically
            if "workdir" in task:
                print(f"\n🏁 Workdir field: {task['workdir']}")
            else:
                print("\n❌ Workdir field not found in task")

        except Exception as e:
            print(f"❌ Error: {e}")
            sys.exit(1)

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)

if __name__ == "__main__":
    main()