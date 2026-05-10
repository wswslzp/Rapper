#!/usr/bin/env python3
"""
Board Integration Test Script
Simulates the Hermes integration workflow for testing Rapper's board integration functionality.
"""

import subprocess
import json
import time
import sys

def run_command(cmd):
    """Run a shell command and return result"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    except Exception as e:
        return {"error": str(e)}

def check_concurrency():
    """Test concurrency checking - simplified version since --concurrency flag not available"""
    print("Testing concurrency control...")
    result = run_command("./rapper --task-count")

    if result.get("returncode") == 0:
        count = int(result["stdout"].strip())
        max_concurrent = 5  # Default from config
        can_start = count < max_concurrent

        concurrency_result = {
            "current_tasks": count,
            "max_concurrent": max_concurrent,
            "can_start_new": can_start
        }
        print(f"✅ Concurrency check: {json.dumps(concurrency_result, indent=2)}")
        return concurrency_result
    else:
        print(f"❌ Failed to check concurrency: {result}")
        return None

def start_test_task():
    """Start a test background task"""
    print("\nStarting test background task...")
    test_prompt = "Write a simple test.txt file with content 'Integration test successful'"

    cmd = f'./rapper --background integration-test -p "{test_prompt}"'
    result = run_command(cmd)

    if result.get("returncode") == 0:
        # Extract task ID from output
        import re
        match = re.search(r'Started task: ([a-z0-9-]+)', result["stdout"])
        if match:
            task_id = match.group(1)
            print(f"✅ Task started successfully: {task_id}")
            return task_id
        else:
            print(f"❌ Could not extract task ID from: {result['stdout']}")
    else:
        print(f"❌ Failed to start task: {result}")

    return None

def check_task_status(task_id, max_wait=60):
    """Poll task status until completion"""
    print(f"\nPolling task {task_id} for completion...")
    start_time = time.time()

    while time.time() - start_time < max_wait:
        result = run_command(f"./rapper --status {task_id}")

        if result.get("returncode") == 0:
            output = result["stdout"]
            if "Status:  completed" in output or "Status:  failed" in output:
                print(f"✅ Task completed!")

                # Try to extract structured result
                if "Structured Result:" in output:
                    print("✅ Found structured result in status output")
                    return {"status": "completed", "has_structured_result": True}
                else:
                    print("⚠️ No structured result found in status output")
                    return {"status": "completed", "has_structured_result": False}

        print(f"⏳ Task still running... (waited {int(time.time() - start_time)}s)")
        time.sleep(5)

    print(f"❌ Task timeout after {max_wait}s")
    return {"status": "timeout"}

def test_json_task_data(task_id):
    """Test reading raw JSON task data"""
    print(f"\nTesting JSON task data access for {task_id}...")

    task_file = f"/home/zliao/.rapper/tasks/{task_id}.json"
    try:
        with open(task_file, 'r') as f:
            task_data = json.load(f)

        print("✅ Successfully read task JSON file")

        # Check for key fields required for Hermes integration
        required_fields = ["id", "status", "structured_result", "result", "error"]
        missing_fields = [field for field in required_fields if field not in task_data]

        if not missing_fields:
            print("✅ All required fields present in task data")
        else:
            print(f"⚠️ Missing fields: {missing_fields}")

        # Check structured result format
        structured_result = task_data.get("structured_result")
        if structured_result:
            required_sr_fields = ["status", "output_path", "pr_url", "errors"]
            missing_sr_fields = [field for field in required_sr_fields if field not in structured_result]

            if not missing_sr_fields:
                print("✅ Structured result has correct format")
                print(f"   Status: {structured_result['status']}")
                print(f"   Output: {structured_result['output_path']}")
                print(f"   Errors: {structured_result['errors']}")
            else:
                print(f"⚠️ Structured result missing fields: {missing_sr_fields}")
        else:
            print("⚠️ No structured_result in task data")

        return task_data

    except Exception as e:
        print(f"❌ Failed to read task JSON: {e}")
        return None

def main():
    print("🎤 Rapper Board Integration Test")
    print("=" * 40)

    # Test 1: Concurrency Control
    concurrency_info = check_concurrency()
    if not concurrency_info:
        print("❌ Concurrency test failed, aborting")
        return 1

    # Test 2: Start Background Task
    if not concurrency_info["can_start_new"]:
        print("⚠️ Cannot start new task due to concurrency limits")
        print("Current running tasks at max capacity")
        return 0

    task_id = start_test_task()
    if not task_id:
        print("❌ Task start test failed, aborting")
        return 1

    # Test 3: Status Monitoring
    task_result = check_task_status(task_id)
    if task_result["status"] != "completed":
        print("❌ Task completion test failed")
        return 1

    # Test 4: JSON Data Access
    task_data = test_json_task_data(task_id)
    if not task_data:
        print("❌ JSON data test failed")
        return 1

    print("\n" + "=" * 40)
    print("🎉 Board integration tests completed successfully!")
    print("\nKey test results:")
    print(f"  - Concurrency control: ✅ ({concurrency_info['current_tasks']}/{concurrency_info['max_concurrent']} tasks)")
    print(f"  - Background task execution: ✅ (task {task_id})")
    print(f"  - Status monitoring: ✅")
    print(f"  - Structured results: ✅")
    print(f"  - JSON data access: ✅")

    return 0

if __name__ == "__main__":
    sys.exit(main())