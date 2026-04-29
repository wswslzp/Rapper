#!/usr/bin/env python3
"""Simple test for bash-runner MCP"""
import subprocess
import json

def send_request(proc, request):
    """Send JSON-RPC request. Returns response dict for requests, None for notifications."""
    request_str = json.dumps(request) + "\n"
    proc.stdin.write(request_str)
    proc.stdin.flush()
    # Notifications (no "id" field) get no response — don't block on readline()
    if "id" not in request:
        return None
    response = proc.stdout.readline()
    return json.loads(response) if response else None

def main():
    import os, time

    # Start MCP server — clear PYTHONPATH to prevent /data/packages (Python 3.12
    # compiled pydantic_core) from polluting the rapper venv (Python 3.11).
    env = os.environ.copy()
    env["PYTHONPATH"] = ""
    proc = subprocess.Popen(
        ["uv", "run", "python", "mcp-servers/bash-runner/server.py"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd="/app/rapper",
        env=env,
    )

    time.sleep(1)  # Wait for MCP server to start up

    try:
        # 1. Initialize
        print("1. Sending initialize...")
        resp = send_request(proc, {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"}
            },
            "id": 1
        })
        if resp is None:
            print("   ERROR: No response to initialize")
            return
        print(f"   Response: {resp.get('result', {}).get('serverInfo', {})}")
        
        # 2. Initialized notification
        send_request(proc, {
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        })
        
        # 3. Test safe command: echo
        print("\n2. Testing safe command: echo hello")
        resp = send_request(proc, {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "run_bash",
                "arguments": {"command": "echo hello world"}
            },
            "id": 2
        })
        if resp is None:
            print("   ERROR: No response")
        elif "result" in resp:
            content = resp["result"].get("content", [{}])[0].get("text", "")
            print(f"   Result: {content[:200]}")
        else:
            print(f"   Error: {resp.get('error')}")
        
        # 4. Test dangerous command: rm -rf
        print("\n3. Testing dangerous command: rm -rf /")
        resp = send_request(proc, {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "run_bash",
                "arguments": {"command": "rm -rf /"}
            },
            "id": 3
        })
        if resp is None:
            print("   ERROR: No response")
        elif "result" in resp:
            content = resp["result"].get("content", [{}])[0].get("text", "")
            print(f"   Result: {content[:200]}")
        else:
            print(f"   Error: {resp.get('error')}")
        
        # 5. Test another safe command: pwd
        print("\n4. Testing safe command: pwd")
        resp = send_request(proc, {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "run_bash",
                "arguments": {"command": "pwd"}
            },
            "id": 4
        })
        if resp is None:
            print("   ERROR: No response")
        elif "result" in resp:
            content = resp["result"].get("content", [{}])[0].get("text", "")
            print(f"   Result: {content[:200]}")
        else:
            print(f"   Error: {resp.get('error')}")
            
        print("\n✅ All tests completed!")
        
    finally:
        proc.terminate()
        proc.wait()

if __name__ == "__main__":
    main()
