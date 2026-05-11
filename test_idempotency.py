#!/usr/bin/env python3
"""
Test script for KANBAN-003 Idempotency Key functionality.

Tests:
- T1: Same idempotencyKey doesn't create duplicate tasks
- T2: Different idempotencyKeys create different tasks
- T3: No idempotencyKey works normally (backward compatibility)
- T4: Check persistence in idempotency.json
"""

import json
import os
import urllib.request
import urllib.parse
import sys

def make_board_request(method: str, endpoint: str, data: dict = None) -> dict:
    """Make HTTP request to Agent Board API."""
    api_url = "http://localhost:3456"
    api_key = "sk-4429c0b2e53522a890b1c5ab6c0d1fcb"

    url = f"{api_url}/api{endpoint}"

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "IdempotencyTest/1.0",
        "X-API-Key": api_key
    }

    body = None
    if data:
        body = json.dumps(data).encode('utf-8')

    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            response_text = response.read().decode('utf-8')
            if response_text:
                return json.loads(response_text)
            return {}
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        print(f"HTTP Error {e.code}: {error_body}")
        return {"error": f"HTTP {e.code}: {error_body}"}
    except Exception as e:
        print(f"Request failed: {e}")
        return {"error": str(e)}

def create_task(title: str, description: str, idempotency_key: str = None) -> dict:
    """Create a task with optional idempotency key."""
    data = {
        "title": title,
        "description": description,
        "projectId": "proj_6698248b156b0ba0",
        "assignee": "qa-tester"
    }

    if idempotency_key:
        data["idempotencyKey"] = idempotency_key

    return make_board_request("POST", "/tasks", data)

def delete_task(task_id: str) -> dict:
    """Delete a task for cleanup."""
    return make_board_request("DELETE", f"/tasks/{task_id}")

def check_idempotency_file() -> dict:
    """Check the contents of idempotency.json."""
    try:
        with open("/app/agent-board/data/idempotency.json", "r") as f:
            return json.load(f)
    except Exception as e:
        return {"error": f"Failed to read idempotency.json: {e}"}

def run_tests():
    """Run all idempotency tests."""
    results = {
        "T1": {"status": "FAIL", "details": ""},
        "T2": {"status": "FAIL", "details": ""},
        "T3": {"status": "FAIL", "details": ""},
        "T4": {"status": "FAIL", "details": ""}
    }

    created_tasks = []

    print("🧪 Starting KANBAN-003 Idempotency Key Tests")
    print("=" * 50)

    # T1: Same idempotencyKey doesn't create duplicate tasks
    print("\n🔸 T1: Testing same idempotencyKey doesn't create duplicates")

    # Create first task
    task1 = create_task(
        "QA-003 Test Task 1A",
        "First test task for idempotency",
        "qa-003-idem-test-001"
    )

    if "error" in task1:
        results["T1"]["details"] = f"Failed to create first task: {task1['error']}"
    else:
        id_1 = task1.get("id")
        created_tasks.append(id_1)
        print(f"✅ Created first task with ID: {id_1}")

        # Create second task with same key
        task2 = create_task(
            "QA-003 Test Task 1B",  # Different title, same key
            "Second test task with same idempotency key",
            "qa-003-idem-test-001"
        )

        if "error" in task2:
            results["T1"]["details"] = f"Failed to create second task: {task2['error']}"
        else:
            id_2 = task2.get("id")
            print(f"✅ Second request returned ID: {id_2}")

            if id_1 == id_2:
                results["T1"]["status"] = "PASS"
                results["T1"]["details"] = f"Same idempotencyKey returned same task ID: {id_1}"
                print(f"✅ T1 PASS: Same task ID returned ({id_1})")
            else:
                results["T1"]["details"] = f"Different IDs: {id_1} vs {id_2}"
                print(f"❌ T1 FAIL: Different task IDs returned")
                created_tasks.append(id_2)

    # T2: Different idempotencyKeys create different tasks
    print("\n🔸 T2: Testing different idempotencyKeys create different tasks")

    task_a = create_task(
        "QA-003 Test Task A",
        "Task A for different key test",
        "qa-003-idem-test-002"
    )

    task_b = create_task(
        "QA-003 Test Task B",
        "Task B for different key test",
        "qa-003-idem-test-003"
    )

    if "error" in task_a or "error" in task_b:
        results["T2"]["details"] = "Failed to create test tasks"
    else:
        id_a = task_a.get("id")
        id_b = task_b.get("id")
        created_tasks.extend([id_a, id_b])

        if id_a != id_b:
            results["T2"]["status"] = "PASS"
            results["T2"]["details"] = f"Different keys created different tasks: {id_a} vs {id_b}"
            print(f"✅ T2 PASS: Different task IDs ({id_a} vs {id_b})")
        else:
            results["T2"]["details"] = f"Same ID returned: {id_a}"
            print(f"❌ T2 FAIL: Same task ID returned")

    # T3: No idempotencyKey works normally (backward compatibility)
    print("\n🔸 T3: Testing no idempotencyKey (backward compatibility)")

    task_no_key_1 = create_task(
        "QA-003 No Key Test",
        "First task without idempotency key"
    )

    task_no_key_2 = create_task(
        "QA-003 No Key Test",  # Same title, no key
        "Second task without idempotency key"
    )

    if "error" in task_no_key_1 or "error" in task_no_key_2:
        results["T3"]["details"] = "Failed to create tasks without keys"
    else:
        id_no_key_1 = task_no_key_1.get("id")
        id_no_key_2 = task_no_key_2.get("id")
        created_tasks.extend([id_no_key_1, id_no_key_2])

        if id_no_key_1 != id_no_key_2:
            results["T3"]["status"] = "PASS"
            results["T3"]["details"] = f"No keys created different tasks: {id_no_key_1} vs {id_no_key_2}"
            print(f"✅ T3 PASS: Different task IDs without keys ({id_no_key_1} vs {id_no_key_2})")
        else:
            results["T3"]["details"] = f"Same ID returned: {id_no_key_1}"
            print(f"❌ T3 FAIL: Same task ID returned without keys")

    # T4: Check idempotency.json persistence
    print("\n🔸 T4: Testing idempotency.json persistence")

    idempotency_data = check_idempotency_file()
    if "error" in idempotency_data:
        results["T4"]["details"] = idempotency_data["error"]
        print(f"❌ T4 FAIL: {idempotency_data['error']}")
    else:
        # Look for our test keys
        test_keys = ["qa-003-idem-test-001", "qa-003-idem-test-002", "qa-003-idem-test-003"]
        found_keys = []

        for entry in idempotency_data:
            if isinstance(entry, dict) and entry.get("key") in test_keys:
                found_keys.append(entry["key"])

        if len(found_keys) >= 2:  # Should have at least 2 of our keys
            results["T4"]["status"] = "PASS"
            results["T4"]["details"] = f"Found test keys in persistence: {found_keys}"
            print(f"✅ T4 PASS: Found test keys in idempotency.json: {found_keys}")
        else:
            results["T4"]["details"] = f"Only found {len(found_keys)} keys: {found_keys}"
            print(f"❌ T4 FAIL: Expected test keys not found in idempotency.json")

    # Summary
    print("\n" + "=" * 50)
    print("📊 Test Results Summary:")
    for test, result in results.items():
        status_emoji = "✅" if result["status"] == "PASS" else "❌"
        print(f"{status_emoji} {test}: {result['status']} - {result['details']}")

    passed = sum(1 for r in results.values() if r["status"] == "PASS")
    total = len(results)
    print(f"\n🏆 Overall: {passed}/{total} tests passed")

    # Cleanup
    print(f"\n🧹 Cleaning up {len(created_tasks)} test tasks...")
    for task_id in created_tasks:
        if task_id:
            delete_result = delete_task(task_id)
            if "error" not in delete_result:
                print(f"✅ Deleted task {task_id}")
            else:
                print(f"❌ Failed to delete task {task_id}: {delete_result.get('error')}")

    return results

if __name__ == "__main__":
    run_tests()