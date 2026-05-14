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
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging
import os
import signal
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, asdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import yaml

# Add lib to path for imports
sys.path.insert(0, os.path.dirname(__file__))
from task_runner import Task, TaskRunner, generate_task_id
from lib.db import init_db, get_running_count


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
            headers['X-API-Key'] = self.api_key

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

    def get_tasks(self, assignee: Optional[str] = None, column: str = "todo") -> List[Dict[str, Any]]:
        """Get tasks by column, optionally filtered by assignee.

        Args:
            assignee: Filter by assignee. If None, returns all tasks in column.
                     For backward compatibility, also accepts assignee as first positional arg.
            column: Column to query (default: "todo")

        Returns:
            List of tasks matching criteria
        """
        try:
            # Build query parameters
            params = [f'column={column}']

            # Handle both old and new calling patterns:
            # Old: get_tasks(assignee_string, column_string)
            # New: get_tasks(None, column_string) for column-only queries
            if assignee is not None:
                params.append(f'assignee={assignee}')

            query_string = '&'.join(params)
            response = self._make_request('GET', f'/api/tasks?{query_string}')

            # API returns a list directly, not a {"tasks": [...]} wrapper
            if isinstance(response, list):
                return response
            return response.get('tasks', [])
        except (HTTPError, URLError, json.JSONDecodeError):
            return []

    def claim_task(self, task_id: str, agent_id: str, retries: int = 3) -> bool:
        """Atomically claim a task by moving it to 'doing' column and setting assignee.

        This is the core of Method A: claim BEFORE execution so the task
        is no longer visible in todo on the next poll, even if execution
        crashes or the daemon restarts.

        Args:
            task_id: Board task ID to claim.
            agent_id: Agent ID to assign the task to.
            retries: Number of retry attempts on transient errors.

        Returns:
            True if task was successfully moved to 'doing', False otherwise.
        """
        payload = {
            'column': 'doing',
            'assignee': agent_id,
            'lastHeartbeat': datetime.utcnow().isoformat() + 'Z',
        }
        for attempt in range(1, retries + 1):
            try:
                self._make_request('PATCH', f'/api/tasks/{task_id}', payload)
                # Leave a breadcrumb comment so the audit trail shows who claimed it
                try:
                    self._make_request('POST', f'/api/tasks/{task_id}/comments',
                                       {'author': agent_id, 'text': 'Started by agent ' + agent_id})
                except Exception:
                    pass  # comment failure is non-fatal
                return True
            except (HTTPError, URLError, json.JSONDecodeError) as e:
                self.logger.warning(
                    f"claim_task attempt {attempt}/{retries} failed for {task_id}: {e}"
                )
                if attempt < retries:
                    time.sleep(1)
        return False

    def update_task_status(self, task_id: str, status: str, comment: Optional[str] = None,
                           author: Optional[str] = None) -> bool:
        """Update task status on Agent Board."""
        # Map status strings to column names
        status_to_column = {
            'in_progress': 'doing',
            'doing': 'doing',
            'done': 'done',
            'failed': 'failed',
            'todo': 'todo',
        }
        column = status_to_column.get(status, status)
        try:
            self._make_request('PATCH', f'/api/tasks/{task_id}', {'column': column})
            if comment and author:
                self._make_request('POST', f'/api/tasks/{task_id}/comments',
                                   {'author': author, 'text': comment})
            return True
        except (HTTPError, URLError, json.JSONDecodeError):
            return False

    def update_task_heartbeat(self, task_id: str) -> bool:
        """Update task heartbeat timestamp on Agent Board."""
        try:
            self._make_request('PATCH', f'/api/tasks/{task_id}', {
                'lastHeartbeat': datetime.utcnow().isoformat() + 'Z'
            })
            return True
        except (HTTPError, URLError, json.JSONDecodeError) as e:
            self.logger.debug(f"Failed to update heartbeat for task {task_id}: {e}")
            return False

    def add_comment(self, task_id: str, author: str, text: str) -> bool:
        """Post a comment to a Board task."""
        try:
            self._make_request('POST', f'/api/tasks/{task_id}/comments',
                              {'author': author, 'text': text})
            return True
        except Exception as e:
            self.logger.warning(f"Failed to add comment to {task_id}: {e}")
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

        # Initialize database
        init_db()

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

        # Progress tracking for current task
        self._last_progress_step = 0

        # Deduplication file path
        self.picked_tasks_file = os.path.expanduser("~/.rapper/daemon_picked.json")

        # Poll error backoff counter
        self._poll_error_count = 0

        # Cleanup cycle tracking (for Pitfall #31 mitigation)
        self._poll_cycle_count = 0

        # Thread pool for background task execution
        self.task_executor = ThreadPoolExecutor(
            max_workers=self.config.get('tasks', {}).get('max_concurrent_tasks', 5),
            thread_name_prefix="task-executor"
        )

        # Track running task futures to prevent blocking the main polling loop
        self.running_task_futures: Dict[str, Future] = {}
        self.running_tasks_lock = threading.Lock()

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

    def _load_picked_tasks(self) -> Set[str]:
        """Load previously picked task IDs from deduplication file."""
        try:
            if os.path.exists(self.picked_tasks_file):
                with open(self.picked_tasks_file, 'r') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return set(data)
        except Exception as e:
            self.logger.warning(f"Failed to load picked tasks file: {e}")
        return set()

    def _save_picked_task(self, task_id: str):
        """Add task ID to deduplication file."""
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.picked_tasks_file), exist_ok=True)

            picked_tasks = self._load_picked_tasks()
            picked_tasks.add(task_id)

            with open(self.picked_tasks_file, 'w') as f:
                json.dump(list(picked_tasks), f)
        except Exception as e:
            self.logger.warning(f"Failed to save picked task: {e}")

    def _remove_from_picked_tasks(self, task_id: str):
        """Remove task ID from picked_tasks file (immediate cleanup on completion)."""
        try:
            picked_tasks = self._load_picked_tasks()
            if task_id in picked_tasks:
                picked_tasks.remove(task_id)

                # Ensure directory exists
                os.makedirs(os.path.dirname(self.picked_tasks_file), exist_ok=True)

                with open(self.picked_tasks_file, 'w') as f:
                    json.dump(list(picked_tasks), f)

                self.logger.debug(f"Removed completed task {task_id} from picked_tasks file")
        except Exception as e:
            self.logger.warning(f"Failed to remove task from picked_tasks file: {e}")

    def _clear_old_picked_tasks(self):
        """Clear old picked tasks file (called at startup)."""
        try:
            if os.path.exists(self.picked_tasks_file):
                os.remove(self.picked_tasks_file)
                self.logger.info("Cleared old picked tasks deduplication file")
        except Exception as e:
            self.logger.warning(f"Failed to clear picked tasks file: {e}")

    def _cleanup_completed_picked_tasks(self):
        """Remove completed/failed task IDs from picked_tasks file to prevent bloat.

        This is the fix for Pitfall #31: historical todo tasks blocking new pickup.
        Query Board for terminal states and remove them from deduplication file.
        """
        try:
            picked_tasks = self._load_picked_tasks()
            if not picked_tasks:
                return

            original_count = len(picked_tasks)

            # Query Board for tasks in terminal states (done, failed)
            # These are safe to remove from picked_tasks since they won't be re-picked
            done_tasks = self.client.get_tasks(None, 'done')
            failed_tasks = self.client.get_tasks(None, 'failed')
            terminal_task_ids = {t['id'] for t in done_tasks + failed_tasks}

            # Remove terminal task IDs from picked_tasks
            cleaned_picked_tasks = picked_tasks - terminal_task_ids
            removed_count = original_count - len(cleaned_picked_tasks)

            if removed_count > 0:
                # Save cleaned picked_tasks back to file
                with open(self.picked_tasks_file, 'w') as f:
                    json.dump(list(cleaned_picked_tasks), f)
                self.logger.info(f"Cleaned {removed_count} completed tasks from picked_tasks file "
                               f"({original_count} → {len(cleaned_picked_tasks)})")
            else:
                self.logger.debug(f"No cleanup needed for picked_tasks file ({original_count} entries)")

        except Exception as e:
            self.logger.warning(f"Failed to cleanup picked tasks file: {e}")

    def _start_webhook_server(self):
        """Start webhook HTTP server in background thread."""
        port = self.config['agent_board']['webhook_port']

        try:
            def handler_factory(*args, **kwargs):
                return WebhookHandler(self, *args, **kwargs)

            self.webhook_server = HTTPServer(('0.0.0.0', port), handler_factory)
            # Allow address reuse to prevent "Address already in use" errors during rapid restarts
            self.webhook_server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

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
        except HTTPError as e:
            if e.code == 409:
                self.logger.warning(f"Agent {self.agent_id} already registered with Agent Board (409 Conflict), continuing...")
            else:
                self.logger.error(f"Failed to register with Agent Board: HTTP {e.code}")
                raise
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
            # Increment poll cycle counter
            self._poll_cycle_count += 1

            # Periodic cleanup of completed tasks from picked_tasks file (Pitfall #31 fix)
            # Run every 10 poll cycles to prevent picked_tasks bloat without excessive API calls
            if self._poll_cycle_count % 10 == 0:
                self._cleanup_completed_picked_tasks()

            # Log polling activity to show daemon is responsive even during task execution
            with self.running_tasks_lock:
                active_tasks = len(self.running_task_futures)
            if active_tasks > 0:
                self.logger.debug(f"Polling for new tasks ({active_tasks} currently executing in background)")

            # Query both todo and ready columns for comprehensive task pickup
            # This allows daemon to pickup both manually assigned (todo) and auto-promoted (ready) tasks
            all_todo_tasks = self.client.get_tasks(None, 'todo')
            all_ready_tasks = self.client.get_tasks(None, 'ready')
            all_tasks = all_todo_tasks + all_ready_tasks
            self._poll_error_count = 0  # reset on successful poll

            if not all_tasks:
                self.logger.debug("No tasks found in todo or ready columns")
                return

            # Filter for tasks this agent can claim:
            # 1. Unassigned tasks (assignee=null) - these can be claimed
            # 2. Tasks already assigned to this agent - these can be resumed
            # 3. Exclude tasks assigned to other agents
            claimable_tasks = []
            for task in all_tasks:
                assignee = task.get('assignee')
                if assignee is None or assignee == self.agent_id:
                    claimable_tasks.append(task)

            if not claimable_tasks:
                assigned_count = len([t for t in all_tasks if t.get('assignee')])
                unassigned_count = len(all_tasks) - assigned_count
                self.logger.debug(
                    f"No claimable tasks found in todo/ready ({unassigned_count} unassigned, "
                    f"{assigned_count} assigned to other agents, {len(all_todo_tasks)} todo, {len(all_ready_tasks)} ready)"
                )
                return

            # Load picked tasks for deduplication (Solution B — file-based)
            picked_tasks = self._load_picked_tasks()

            # Solution A (board-side): Also fetch tasks already in 'doing' for this agent.
            # This survives daemon restarts: if a prior run claimed a task but the daemon
            # crashed before finishing, the task stays in 'doing' and we must NOT re-pick it.
            try:
                doing_tasks = self.client.get_tasks(self.agent_id, 'doing')
                for t in doing_tasks:
                    picked_tasks.add(t['id'])
                if doing_tasks:
                    self.logger.debug(
                        f"Excluding {len(doing_tasks)} already-doing task(s) from consideration"
                    )
            except Exception as e:
                self.logger.warning(f"Could not fetch doing tasks for deduplication: {e}")

            # Filter out already picked / already-doing tasks
            available_tasks = [task for task in claimable_tasks if task['id'] not in picked_tasks]

            if not available_tasks:
                self.logger.debug(f"No new tasks found (filtered {len(claimable_tasks)} already picked)")
                return

            # Check concurrency limit before processing tasks
            # Count both SQLite running tasks and our thread pool futures
            with self.running_tasks_lock:
                thread_pool_running = len(self.running_task_futures)
            sqlite_running_count = self._count_running_tasks()
            total_running = max(sqlite_running_count, thread_pool_running)  # Use max for safety

            max_concurrent = self.config.get('tasks', {}).get('max_concurrent_tasks', 5)

            if total_running >= max_concurrent:
                self.logger.warning(f"Concurrency limit reached: {total_running}/{max_concurrent} (SQLite: {sqlite_running_count}, threads: {thread_pool_running}), skipping task execution")
                return

            # Clean up completed futures before starting new task
            self._cleanup_completed_futures()

            # Process first available task
            board_task = available_tasks[0]
            task_id = board_task['id']

            self.logger.info(f"Picked task: {task_id} (current load: {total_running}/{max_concurrent})")

            # ── Method A: Claim task on the board BEFORE execution ────────────────
            # Move todo → doing immediately so the next poll never sees it again,
            # even if this daemon restarts or execution crashes before reporting done.
            self._save_picked_task(task_id)  # Solution B: file-based dedup (same-process guard)

            claimed = self.client.claim_task(task_id, self.agent_id)
            if claimed:
                self.logger.info(f"Claimed task {task_id} → doing (pre-execution)")
            else:
                # Claim failed (transient network error?). Still proceed: file-based dedup
                # will prevent re-pickup within this process lifetime, but log a warning
                # so operators know the board state may be stale.
                self.logger.warning(
                    f"Could not claim task {task_id} on board (PATCH todo→doing failed). "
                    "Proceeding anyway; task may be re-picked after daemon restart if "
                    "execution does not report done."
                )

            # Create internal task
            # Use workdir from board task if specified, otherwise use daemon's cwd
            task_workdir = board_task.get('workdir') or os.getcwd()
            internal_task = Task(
                id=generate_task_id(),
                name=board_task.get('title', f"board-{task_id}"),
                prompt=board_task.get('description', ''),
                workdir=task_workdir,
                status='pending',
                board_task_id=task_id
            )

            # Submit task execution to background thread instead of blocking
            future = self.task_executor.submit(self._execute_task_in_background, task_id, internal_task)

            # Track the future
            with self.running_tasks_lock:
                self.running_task_futures[task_id] = future

            self.logger.info(f"Task {task_id} submitted to background executor (internal_task: {internal_task.id})")

        except (HTTPError, URLError) as e:
            self._poll_error_count += 1
            with self.running_tasks_lock:
                active_tasks = len(self.running_task_futures)
            self.logger.warning(f'Agent Board connection error (attempt {self._poll_error_count}, {active_tasks} tasks running): {e}')
        except Exception as e:
            self._poll_error_count += 1
            self.logger.error(f'Error in task polling (attempt {self._poll_error_count}): {e}')

    def _execute_task_in_background(self, board_task_id: str, internal_task: Task):
        """Execute a task in background thread to avoid blocking the main polling loop."""
        try:
            # Set current task for heartbeat tracking
            self.current_task = (board_task_id, internal_task)
            self._last_progress_step = 0  # Reset progress tracking for new task

            # Start heartbeat thread to periodically update task status during execution
            heartbeat_stop_event = threading.Event()
            heartbeat_thread = threading.Thread(
                target=self._heartbeat_worker,
                args=(board_task_id, heartbeat_stop_event),
                daemon=True
            )
            heartbeat_thread.start()

            start_time = time.time()  # BUG-P14: record start time for elapsed calculation
            try:
                # Execute task synchronously within background thread
                self.logger.info(f"Executing task: {internal_task.id} (board: {board_task_id})")
                self.task_runner._run_task_sync(internal_task, timeout=3600, max_turns=200)
            finally:
                # Stop heartbeat thread
                heartbeat_stop_event.set()
                heartbeat_thread.join(timeout=5)

            # Check result and update board
            if internal_task.status == 'completed':
                self.client.update_task_status(board_task_id, 'done', internal_task.result or 'Task completed successfully')
                self.logger.info(f"Task {board_task_id} completed successfully")
                # BUG-P14: Post terminal completion comment
                elapsed = int(time.time() - start_time)
                steps = len(getattr(internal_task, 'progress', []) or [])
                sr = getattr(internal_task, 'structured_result', None) or {}
                output_path = sr.get('output_path', '') if isinstance(sr, dict) else ''
                text = f"✅ 任务完成\n耗时：{elapsed}s | 步数：{steps}"
                if output_path:
                    text += f"\n输出：{output_path}"
                try:
                    self.client.add_comment(board_task_id, self.agent_id, text)
                except Exception as e:
                    self.logger.warning(f"Failed to post completion comment: {e}")
            else:
                error_msg = internal_task.error or 'Task failed for unknown reason'
                self.client.update_task_status(board_task_id, 'failed', error_msg)
                self.logger.error(f"Task {board_task_id} failed: {error_msg}")
                # BUG-P14: Post terminal failure comment
                elapsed = int(time.time() - start_time)
                steps = len(getattr(internal_task, 'progress', []) or [])
                text = f"❌ 任务失败\n耗时：{elapsed}s | 步数：{steps}\n原因：{error_msg[:300]}"
                try:
                    self.client.add_comment(board_task_id, self.agent_id, text)
                except Exception as e:
                    self.logger.warning(f"Failed to post failure comment: {e}")

            # Immediate cleanup: remove completed task from picked_tasks file (Pitfall #31 mitigation)
            self._remove_from_picked_tasks(board_task_id)

        except Exception as e:
            self.logger.error(f"Error executing task {board_task_id}: {e}")
            error_msg = f"Execution error: {e}"
            try:
                self.client.update_task_status(board_task_id, 'failed', error_msg)
            except Exception:
                pass  # Ignore board update errors during cleanup
            # BUG-P14: Post terminal failure comment for exception path
            try:
                elapsed = int(time.time() - start_time) if 'start_time' in locals() else 0
                steps = len(getattr(internal_task, 'progress', []) or [])
                text = f"❌ 任务失败\n耗时：{elapsed}s | 步数：{steps}\n原因：{str(e)[:300]}"
                self.client.add_comment(board_task_id, self.agent_id, text)
            except Exception as ce:
                self.logger.warning(f"Failed to post failure comment: {ce}")

            # Immediate cleanup for failed tasks too (Pitfall #31 mitigation)
            self._remove_from_picked_tasks(board_task_id)

        finally:
            # Clear current task if it was ours
            if self.current_task and self.current_task[0] == board_task_id:
                self.current_task = None
                self._last_progress_step = 0  # Reset progress tracking

            # Remove from futures tracking
            with self.running_tasks_lock:
                self.running_task_futures.pop(board_task_id, None)

    def _cleanup_completed_futures(self):
        """Remove completed futures from tracking dict."""
        with self.running_tasks_lock:
            completed_tasks = []
            for task_id, future in self.running_task_futures.items():
                if future.done():
                    completed_tasks.append(task_id)

            for task_id in completed_tasks:
                future = self.running_task_futures.pop(task_id)
                # Log any exceptions from completed tasks
                try:
                    future.result()  # This will raise if the task failed
                except Exception as e:
                    self.logger.warning(f"Background task {task_id} completed with exception: {e}")

            if completed_tasks:
                self.logger.debug(f"Cleaned up {len(completed_tasks)} completed task futures")

    def _heartbeat_worker(self, task_id: str, stop_event: threading.Event):
        """Background worker to send periodic heartbeat updates for a task."""
        heartbeat_interval = 30  # Send heartbeat every 30 seconds

        while not stop_event.is_set():
            if stop_event.wait(heartbeat_interval):
                # Stop event was set, exit
                break

            # Send heartbeat update
            success = self.client.update_task_heartbeat(task_id)
            if success:
                self.logger.debug(f"Heartbeat sent for task {task_id}")
            else:
                self.logger.warning(f"Failed to send heartbeat for task {task_id}")

            # Send progress update comment if there's new progress
            if self.current_task:
                board_task_id, internal_task = self.current_task
                if board_task_id == task_id:
                    self._send_progress_update(board_task_id, internal_task)

    def _send_progress_update(self, board_task_id: str, internal_task):
        """Send progress update comment to Board task if there's new progress."""
        try:
            # Reload task from disk to get latest progress
            fresh_task = Task.load(internal_task.id)
            progress = (getattr(fresh_task, 'progress', []) if fresh_task else []) or []
            if not progress:
                # Fall back to in-memory task progress
                progress = getattr(internal_task, 'progress', []) or []
            if not progress:
                return

            step_count = len(progress)

            # Only send update if there's new progress
            if step_count <= self._last_progress_step:
                return

            # Get the latest tool call for summary
            latest = progress[-1] if progress else {}
            tool_name = latest.get('tool', latest.get('name', '?'))

            # Create progress message matching spec format
            text = f"⏳ 执行中：已完成 {step_count} 步 | 最近工具：{tool_name}"

            # Post comment to Board
            try:
                self.client.add_comment(board_task_id, self.agent_id, text)
                self._last_progress_step = step_count
                self.logger.debug(f"Posted progress update for task {board_task_id}: {step_count} steps")
            except Exception as e:
                self.logger.warning(f"Failed to post progress comment: {e}")

        except Exception as e:
            self.logger.debug(f"Error sending progress update for task {board_task_id}: {e}")

    def _make_request(self, method: str, endpoint: str, data: Any = None) -> Dict[str, Any]:
        """Direct API request using daemon's own credentials (bypasses outbound_guard)."""
        return self.client._make_request(method, endpoint, data)

    def _count_running_tasks(self) -> int:
        """Count running tasks using SQLite query."""
        try:
            return get_running_count()
        except Exception as e:
            self.logger.error(f'Error counting running tasks: {e}')
            return 0

    def _archive_old_task_files(self, max_age_days: int = 7):
        """Move completed/failed task files older than max_age_days to archive dir."""
        import glob as _glob
        import shutil as _shutil
        import time as _time
        tasks_dir = os.path.expanduser('~/.rapper/tasks')
        archive_dir = os.path.join(tasks_dir, 'archive')
        os.makedirs(archive_dir, exist_ok=True)
        cutoff = _time.time() - (max_age_days * 86400)
        archived = 0
        for f in _glob.glob(os.path.join(tasks_dir, '*.json')):
            try:
                if os.path.getmtime(f) > cutoff:
                    continue
                with open(f) as fp:
                    d = json.load(fp)
                if d.get('status') not in ('completed', 'failed', 'cancelled'):
                    continue
                # Move json + associated files (.log, .audit.json, .progress)
                base = f[:-5]  # strip .json
                for ext in ['.json', '.log', '.audit.json', '.progress']:
                    src = base + ext
                    if os.path.exists(src):
                        _shutil.move(src, archive_dir)
                archived += 1
            except Exception:
                continue
        if archived > 0:
            self.logger.info(f'Archived {archived} old task files to {archive_dir}')

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

        # Mark any other running tasks in thread pool as failed
        with self.running_tasks_lock:
            for task_id, future in list(self.running_task_futures.items()):
                if not future.done():
                    try:
                        self.client.update_task_status(
                            task_id,
                            'failed',
                            'Task interrupted by agent shutdown'
                        )
                        self.logger.info(f"Marked background task {task_id} as failed due to shutdown")
                    except Exception as e:
                        self.logger.error(f"Failed to mark background task {task_id} as failed: {e}")

        self.shutdown()

    def start(self):
        """Start the daemon."""
        if self.running:
            return

        self.logger.info(f"Starting Rapper daemon (agent_id: {self.agent_id})")

        # Clear old picked tasks (fresh start)
        self._clear_old_picked_tasks()

        # Archive old task files to keep tasks dir lean
        self._archive_old_task_files()

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

        # Shutdown task executor and wait for running tasks
        if hasattr(self, 'task_executor'):
            self.logger.info("Shutting down task executor...")
            # Cancel any remaining futures
            with self.running_tasks_lock:
                for task_id, future in self.running_task_futures.items():
                    if not future.done():
                        self.logger.warning(f"Cancelling running task {task_id}")
                        future.cancel()

            # Shutdown executor and wait for tasks to finish
            self.task_executor.shutdown(wait=True)
            self.logger.info("Task executor shut down")

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