#!/usr/bin/env python3
"""
Daemon mode for Rapper — persistent running mode for Agent Board integration.

Provides:
1. Agent Board registration and heartbeat
2. Task polling and execution
3. Webhook server for instant notifications
4. Graceful shutdown handling

Usage:
    python lib/daemon.py --config ~/.rapper/config.yaml [--agent-id my-agent]
"""

import json
import logging
import os
import signal
import socket
import sys
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import yaml

# Add lib to path for imports
sys.path.insert(0, os.path.dirname(__file__))
from task_runner import Task, TaskRunner, generate_task_id


@dataclass
class AgentInfo:
    """Agent registration info for Agent Board."""
    id: str
    name: str
    status: str = "active"  # active, idle, offline
    capabilities: List[str] = None
    webhook_url: Optional[str] = None
    last_heartbeat: float = None
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.capabilities is None:
            self.capabilities = ["task_execution", "code_generation", "file_operations"]
        if self.metadata is None:
            self.metadata = {
                "version": "1.0.0",
                "language": "python",
                "framework": "rapper"
            }
        if self.last_heartbeat is None:
            self.last_heartbeat = time.time()


class AgentBoardClient:
    """Client for Agent Board API communication."""

    def __init__(self, base_url: str, api_key: Optional[str] = None, timeout: int = 30):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout
        self.logger = logging.getLogger(__name__ + ".client")

    def _make_request(self, method: str, endpoint: str, data: Any = None) -> Dict[str, Any]:
        """Make HTTP request to Agent Board API."""
        url = urljoin(self.base_url + '/', endpoint.lstrip('/'))

        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'Rapper-Agent/1.0'
        }

        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'

        body = json.dumps(data).encode('utf-8') if data else None

        try:
            req = Request(url, data=body, headers=headers, method=method)
            with urlopen(req, timeout=self.timeout) as resp:
                if resp.status >= 400:
                    raise HTTPError(url, resp.status, f"HTTP {resp.status}", headers, None)
                response_data = resp.read().decode('utf-8')
                return json.loads(response_data) if response_data else {}
        except HTTPError as e:
            self.logger.error(f"HTTP {e.code} error for {method} {url}: {e}")
            raise
        except URLError as e:
            self.logger.error(f"Network error for {method} {url}: {e}")
            raise
        except json.JSONDecodeError as e:
            self.logger.error(f"Invalid JSON response from {method} {url}: {e}")
            raise

    def register_agent(self, agent: AgentInfo) -> Dict[str, Any]:
        """Register agent with Agent Board."""
        return self._make_request('POST', '/api/agents', asdict(agent))

    def unregister_agent(self, agent_id: str) -> bool:
        """Unregister agent from Agent Board."""
        try:
            self._make_request('DELETE', f'/api/agents/{agent_id}')
            return True
        except HTTPError as e:
            if e.code == 404:
                # Agent not found, consider it already unregistered
                return True
            return False
        except (URLError, json.JSONDecodeError):
            return False

    def get_tasks(self, assignee: str, column: str = "todo") -> List[Dict[str, Any]]:
        """Get tasks assigned to this agent."""
        try:
            response = self._make_request('GET', f'/api/tasks?assignee={assignee}&column={column}')
            return response.get('tasks', [])
        except (HTTPError, URLError, json.JSONDecodeError):
            return []

    def update_task_status(self, task_id: str, status: str, comment: Optional[str] = None) -> bool:
        """Update task status on Agent Board."""
        try:
            data = {'status': status}
            if comment:
                data['comment'] = comment
            self._make_request('PATCH', f'/api/tasks/{task_id}', data)
            return True
        except (HTTPError, URLError, json.JSONDecodeError):
            return False


class WebhookHandler(BaseHTTPRequestHandler):
    """HTTP handler for webhook notifications."""

    def __init__(self, daemon_ref, *args, **kwargs):
        self.daemon = daemon_ref
        super().__init__(*args, **kwargs)

    def do_POST(self):
        """Handle incoming webhook POST requests."""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)

            if content_length > 0:
                webhook_data = json.loads(post_data.decode('utf-8'))
                self.daemon.handle_webhook(webhook_data)

            # Send response
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"status": "ok"}')

        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
        except Exception as e:
            self.send_error(500, str(e))

    def log_message(self, format, *args):
        """Override to use our logger."""
        self.daemon.logger.info("Webhook: " + format % args)


class RapperDaemon:
    """Main daemon class for persistent Rapper operation."""

    def __init__(self, config_path: str, agent_id: Optional[str] = None):
        self.config_path = config_path
        self.config = self._load_config()
        self.agent_id = agent_id or self.config.get('agent_board', {}).get('agent_id') or self._generate_agent_id()
        self.running = False
        self.shutdown_event = threading.Event()

        # Setup logging
        self._setup_logging()

        # Initialize components
        self.client = AgentBoardClient(
            self.config['agent_board']['url'],
            self.config['agent_board'].get('api_key')
        )
        self.task_runner = TaskRunner()

        # Webhook server
        self.webhook_server = None
        self.webhook_thread = None

        # Agent info
        self.agent_info = AgentInfo(
            id=self.agent_id,
            name=f"rapper-{socket.gethostname()}-{self.agent_id[:8]}",
            webhook_url=self._get_webhook_url()
        )

        # Current task
        self.current_task = None

    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from YAML file."""
        with open(self.config_path, 'r') as f:
            config = yaml.safe_load(f)

        # Merge with defaults
        defaults = {
            'agent_board': {
                'url': 'http://localhost:3456',
                'poll_interval': 30,
                'webhook_port': 18789
            }
        }

        # Simple recursive merge
        for key, value in defaults.items():
            if key not in config:
                config[key] = value
            elif isinstance(value, dict):
                config[key] = {**value, **config.get(key, {})}

        return config

    def _setup_logging(self):
        """Setup logging configuration."""
        log_level = self.config.get('logging', {}).get('level', 'info').upper()

        logging.basicConfig(
            level=getattr(logging, log_level),
            format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        self.logger = logging.getLogger(__name__ + ".daemon")

    def _generate_agent_id(self) -> str:
        """Generate a unique agent ID."""
        import random
        import string
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        suffix = ''.join(random.choices(string.ascii_lowercase, k=6))
        return f"rapper-{ts}-{suffix}"

    def _get_webhook_url(self) -> Optional[str]:
        """Get webhook URL for this agent."""
        port = self.config['agent_board']['webhook_port']
        try:
            # Try to determine external IP
            # For simplicity, use localhost - in production this should be
            # the actual accessible IP/hostname
            return f"http://localhost:{port}/webhook"
        except Exception:
            return None

    def _start_webhook_server(self):
        """Start webhook HTTP server in background thread."""
        port = self.config['agent_board']['webhook_port']

        try:
            def handler_factory(*args, **kwargs):
                return WebhookHandler(self, *args, **kwargs)

            self.webhook_server = HTTPServer(('0.0.0.0', port), handler_factory)
            self.webhook_thread = threading.Thread(
                target=self.webhook_server.serve_forever,
                daemon=True
            )
            self.webhook_thread.start()
            self.logger.info(f"Webhook server listening on port {port}")

        except OSError as e:
            self.logger.error(f"Failed to start webhook server on port {port}: {e}")
            self.webhook_server = None

    def _stop_webhook_server(self):
        """Stop webhook server gracefully."""
        if self.webhook_server:
            self.webhook_server.shutdown()
            self.webhook_server.server_close()
            if self.webhook_thread:
                self.webhook_thread.join(timeout=5)
            self.logger.info("Webhook server stopped")

    def handle_webhook(self, data: Dict[str, Any]):
        """Handle incoming webhook notification."""
        event_type = data.get('type')

        if event_type in ['task.assign', 'comment.add']:
            self.logger.info(f"Received webhook: {event_type}")
            # Wake up the main polling loop
            self.shutdown_event.set()
            # Immediately clear it so we don't actually shutdown
            threading.Timer(0.1, self.shutdown_event.clear).start()
        else:
            self.logger.debug(f"Ignored webhook event: {event_type}")

    def _register_with_agent_board(self):
        """Register this agent with Agent Board."""
        try:
            response = self.client.register_agent(self.agent_info)
            self.logger.info(f"Registered with Agent Board: {response}")
        except Exception as e:
            self.logger.error(f"Failed to register with Agent Board: {e}")
            raise

    def _unregister_from_agent_board(self):
        """Unregister this agent from Agent Board."""
        try:
            if self.client.unregister_agent(self.agent_id):
                self.logger.info("Unregistered from Agent Board")
            else:
                self.logger.warning("Failed to unregister from Agent Board")
        except Exception as e:
            self.logger.error(f"Error unregistering from Agent Board: {e}")

    def _poll_and_execute_tasks(self):
        """Poll for tasks and execute them."""
        try:
            tasks = self.client.get_tasks(self.agent_id, 'todo')

            if not tasks:
                self.logger.debug("No tasks found")
                return

            # Process first available task
            board_task = tasks[0]
            task_id = board_task['id']

            self.logger.info(f"Found task: {task_id}")

            # Create internal task
            internal_task = Task(
                id=generate_task_id(),
                name=board_task.get('title', f"board-{task_id}"),
                prompt=board_task.get('description', ''),
                workdir=os.getcwd(),
                status='pending'
            )

            # Update status to in-progress
            self.client.update_task_status(task_id, 'in_progress', f"Started by agent {self.agent_id}")

            self.current_task = (task_id, internal_task)

            try:
                # Execute task synchronously
                self.logger.info(f"Executing task: {internal_task.id}")
                self.task_runner._run_task_sync(internal_task, timeout=3600, max_turns=200)

                # Check result
                if internal_task.status == 'completed':
                    self.client.update_task_status(task_id, 'done', internal_task.result or 'Task completed successfully')
                    self.logger.info(f"Task {task_id} completed successfully")
                else:
                    error_msg = internal_task.error or 'Task failed for unknown reason'
                    self.client.update_task_status(task_id, 'failed', error_msg)
                    self.logger.error(f"Task {task_id} failed: {error_msg}")

            except Exception as e:
                self.logger.error(f"Error executing task {task_id}: {e}")
                self.client.update_task_status(task_id, 'failed', f"Execution error: {e}")

            finally:
                self.current_task = None

        except Exception as e:
            self.logger.error(f"Error in task polling: {e}")

    def _handle_shutdown(self, signum, frame):
        """Handle shutdown signals gracefully."""
        self.logger.info(f"Received signal {signum}, shutting down gracefully...")

        # Mark current task as failed if running
        if self.current_task:
            board_task_id, internal_task = self.current_task
            try:
                self.client.update_task_status(
                    board_task_id,
                    'failed',
                    'Task interrupted by agent shutdown'
                )
                self.logger.info(f"Marked task {board_task_id} as failed due to shutdown")
            except Exception as e:
                self.logger.error(f"Failed to mark task as failed: {e}")

        self.shutdown()

    def start(self):
        """Start the daemon."""
        if self.running:
            return

        self.logger.info(f"Starting Rapper daemon (agent_id: {self.agent_id})")

        # Setup signal handlers
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

        # Start webhook server
        self._start_webhook_server()

        try:
            # Register with Agent Board
            self._register_with_agent_board()

            self.running = True
            poll_interval = self.config['agent_board']['poll_interval']

            self.logger.info(f"Daemon started, polling every {poll_interval}s")

            # Main event loop
            while self.running:
                self._poll_and_execute_tasks()

                # Wait for next poll or webhook wake-up
                if self.shutdown_event.wait(poll_interval):
                    if not self.running:
                        break
                    # Clear the event for next iteration
                    self.shutdown_event.clear()

        except KeyboardInterrupt:
            self.logger.info("Interrupted by user")
        except Exception as e:
            self.logger.error(f"Daemon error: {e}")
            raise
        finally:
            self._cleanup()

    def shutdown(self):
        """Shutdown the daemon."""
        self.running = False
        self.shutdown_event.set()

    def _cleanup(self):
        """Cleanup resources."""
        self.logger.info("Cleaning up...")
        self._stop_webhook_server()
        self._unregister_from_agent_board()
        self.logger.info("Daemon stopped")


def main():
    """CLI entry point for daemon mode."""
    import argparse

    parser = argparse.ArgumentParser(description="Rapper Daemon")
    parser.add_argument("--config", default="~/.rapper/config.yaml",
                      help="Config file path")
    parser.add_argument("--agent-id", help="Override agent ID")
    parser.add_argument("--log-level", choices=['debug', 'info', 'warning', 'error'],
                      help="Override log level")

    args = parser.parse_args()

    config_path = os.path.expanduser(args.config)
    if not os.path.exists(config_path):
        print(f"Error: Config file not found: {config_path}")
        sys.exit(1)

    daemon = RapperDaemon(config_path, args.agent_id)

    if args.log_level:
        daemon.logger.setLevel(getattr(logging, args.log_level.upper()))

    try:
        daemon.start()
    except Exception as e:
        print(f"Daemon failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()