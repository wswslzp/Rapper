#!/usr/bin/env python3
"""
Tests for launch_daemons.py reviewer validation and backoff logic.

This module tests the validation features that should be added to launch_daemons.py:
- Reviewer config file validation before startup
- Backoff logic to prevent restart storms on missing config
- ENABLE_REVIEWERS feature flag support
- Gradual rollout: reviewer-1 by default, reviewer-2/3 disabled

These tests start as RED (failing) since the validation features are not yet implemented.
Reference: requirements.md v1.1 AC-07, AC-14; design.md v2.1 §6.3

FUNCTIONS TO IMPLEMENT IN launch_daemons.py TO MAKE TESTS PASS:
===============================================================

Core Functions:
- build_daemon_specs() -> List[Tuple[str, Path, Path]]
  Returns daemon specs based on ENABLE_REVIEWERS env var and available configs

Validation Functions:
- validate_daemon_config(agent_id: str, config_path: Path, settings_path: Path) -> bool
  Validates config files exist and are properly formatted
- validate_reviewer_config(config_path: Path) -> bool
  Validates reviewer-specific config schema (role, poll_columns, etc.)

Backoff/Restart Functions:
- restart_with_backoff(agent_id: str, config_path: str, log_path: str, attempt: int)
  Restarts daemon with exponential backoff on repeated failures
- track_restart_failure(agent_id: str)
  Tracks consecutive restart failures per agent
- reset_failure_count(agent_id: str)
  Resets failure count on successful restart
- get_failure_count(agent_id: str) -> int
  Gets current failure count for agent
- should_attempt_restart(agent_id: str, attempt: int) -> bool
  Determines if restart should be attempted based on max attempts limit

Expected Behavior:
- By default, only rapper-1/2/3 enabled (no reviewers)
- ENABLE_REVIEWERS=1 enables available reviewer configs
- ENABLE_REVIEWERS=gradual enables only reviewer-1
- Missing configs/settings logged clearly, no restart storms
- Exponential backoff: attempt^2 seconds (1, 4, 9, 16...)
- Max restart attempts: 5 before giving up permanently
"""

import os
import sys
import tempfile
import time
import unittest.mock
from pathlib import Path
from unittest.mock import Mock, patch, call
import pytest
import subprocess
import signal
import yaml

# Import the module under test
sys_path_backup = None

def setup_module():
    """Add rapper root dir to sys.path for testing."""
    global sys_path_backup
    sys_path_backup = sys.path.copy()
    # Add the rapper root directory to import launch_daemons.py directly
    sys.path.insert(0, '/app/rapper')


def teardown_module():
    """Restore original sys.path."""
    global sys_path_backup
    if sys_path_backup:
        sys.path[:] = sys_path_backup

# Import launch_daemons after setup
try:
    import launch_daemons
except ImportError:
    # If still can't import, handle it gracefully for testing
    import importlib.util
    spec = importlib.util.spec_from_file_location("launch_daemons", "/app/rapper/launch_daemons.py")
    launch_daemons = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launch_daemons)


class TestLaunchDaemonsValidation:
    """Test suite for launch_daemons.py reviewer validation logic."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = Path(tempfile.mkdtemp())
        self.config_dir = self.temp_dir / ".rapper"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir = self.config_dir / "logs"
        self.log_dir.mkdir(exist_ok=True)

        # Clear failure counts between tests
        try:
            clear_all_failure_counts = getattr(launch_daemons, 'clear_all_failure_counts', None)
            if clear_all_failure_counts:
                clear_all_failure_counts()
        except AttributeError:
            pass

    def teardown_method(self):
        """Clean up test fixtures."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def create_config_file(self, agent_id: str, config_data: dict = None):
        """Helper to create a config YAML file."""
        config_path = self.config_dir / f"config-{agent_id}.yaml"
        if config_data is None:
            config_data = {
                "agent_board": {
                    "url": "http://localhost:3456",
                    "api_key": f"sk-{agent_id}",
                    "agent_id": agent_id,
                    "poll_interval": 30
                },
                "tasks": {"max_concurrent_tasks": 5}
            }
        with open(config_path, 'w') as f:
            yaml.dump(config_data, f)
        return config_path

    def create_settings_file(self, agent_id: str):
        """Helper to create a settings JSON file."""
        settings_path = self.config_dir / f"settings-{agent_id}.json"
        settings_data = {
            "permissions": {
                "allow": ["Read", "Grep"],
                "deny": ["Write", "Edit"]
            }
        }
        with open(settings_path, 'w') as f:
            import json
            json.dump(settings_data, f)
        return settings_path

    @patch.dict(os.environ, {}, clear=False)
    def test_reviewers_disabled_by_default(self):
        """Test that reviewers are not included in daemon_specs by default."""
        # Create rapper configs but no reviewer configs
        self.create_config_file("rapper-1")
        self.create_config_file("rapper-2")
        self.create_config_file("rapper-3")

        # Mock Path.home to return our temp directory
        with patch('launch_daemons.Path.home', return_value=self.temp_dir):
            with patch('launch_daemons.start_daemon') as mock_start:
                with patch('launch_daemons.time.sleep'):
                    # This should fail because the validation logic is not yet implemented
                    try:
                        # In the future implementation, this should call build_daemon_specs()
                        # which should return only rapper specs when ENABLE_REVIEWERS is not set
                        daemon_specs = getattr(launch_daemons, 'build_daemon_specs', None)
                        if daemon_specs is None:
                            pytest.fail("build_daemon_specs function not implemented yet (expected for RED test)")

                        specs = daemon_specs()
                        agent_ids = [spec[0] for spec in specs]

                        # Should only include rappers, no reviewers
                        assert "rapper-1" in agent_ids
                        assert "rapper-2" in agent_ids
                        assert "rapper-3" in agent_ids
                        assert "reviewer-1" not in agent_ids
                        assert "reviewer-2" not in agent_ids
                        assert "reviewer-3" not in agent_ids

                    except (AttributeError, NameError):
                        pytest.fail("build_daemon_specs function not implemented yet (expected for RED test)")

    @patch.dict(os.environ, {"ENABLE_REVIEWERS": "1"}, clear=False)
    def test_reviewers_enabled_with_feature_flag(self):
        """Test that reviewers are included when ENABLE_REVIEWERS=1."""
        # Create all configs
        self.create_config_file("rapper-1")
        self.create_config_file("rapper-2")
        self.create_config_file("rapper-3")
        self.create_config_file("reviewer-1", {
            "agent_board": {
                "url": "http://localhost:3456",
                "api_key": "sk-reviewer1",
                "agent_id": "reviewer-1",
                "role": "reviewer",
                "poll_columns": ["review"]
            },
            "claude": {"settings_path": str(self.config_dir / "settings-reviewer-1.json")},
            "tasks": {"max_concurrent_tasks": 1}
        })

        with patch('launch_daemons.Path.home', return_value=self.temp_dir):
            try:
                daemon_specs = getattr(launch_daemons, 'build_daemon_specs', None)
                if daemon_specs is None:
                    pytest.fail("build_daemon_specs function not implemented yet (expected for RED test)")

                specs = daemon_specs()
                agent_ids = [spec[0] for spec in specs]

                # Should include both rappers and reviewers
                assert "rapper-1" in agent_ids
                assert "rapper-2" in agent_ids
                assert "rapper-3" in agent_ids
                assert "reviewer-1" in agent_ids

            except (AttributeError, NameError):
                pytest.fail("build_daemon_specs function not implemented yet (expected for RED test)")

    @patch.dict(os.environ, {"ENABLE_REVIEWERS": "1"}, clear=False)
    def test_only_reviewer_1_enabled_by_default_when_feature_enabled(self):
        """Test gradual rollout: only reviewer-1 by default, not reviewer-2/3."""
        # Create reviewer-1 config but not reviewer-2/3
        self.create_config_file("reviewer-1", {
            "agent_board": {
                "role": "reviewer",
                "poll_columns": ["review"]
            }
        })

        with patch('launch_daemons.Path.home', return_value=self.temp_dir):
            try:
                daemon_specs = getattr(launch_daemons, 'build_daemon_specs', None)
                if daemon_specs is None:
                    pytest.fail("build_daemon_specs function not implemented yet (expected for RED test)")

                specs = daemon_specs()
                agent_ids = [spec[0] for spec in specs]

                # Should include reviewer-1 but not reviewer-2/3 (gradual rollout)
                assert "reviewer-1" in agent_ids
                assert "reviewer-2" not in agent_ids
                assert "reviewer-3" not in agent_ids

            except (AttributeError, NameError):
                pytest.fail("build_daemon_specs function not implemented yet (expected for RED test)")

    def test_config_validation_prevents_startup(self):
        """Test that missing reviewer config prevents daemon startup with clear logging."""
        # Create rapper configs but missing reviewer config
        self.create_config_file("rapper-1")

        reviewer_config_path = self.config_dir / "config-reviewer-1.yaml"
        # Ensure reviewer config does NOT exist
        if reviewer_config_path.exists():
            reviewer_config_path.unlink()

        with patch('launch_daemons.Path.home', return_value=self.temp_dir):
            with patch('launch_daemons.start_daemon') as mock_start:
                with patch('builtins.print') as mock_print:
                    try:
                        validate_daemon_config = getattr(launch_daemons, 'validate_daemon_config', None)
                        if validate_daemon_config is None:
                            pytest.fail("validate_daemon_config function not implemented yet (expected for RED test)")

                        # Should return False for missing config
                        is_valid = validate_daemon_config("reviewer-1", reviewer_config_path, None)
                        assert is_valid is False

                        # Should log the missing config clearly
                        log_calls = [str(call) for call in mock_print.call_args_list]
                        assert any("reviewer-1" in call and "config" in call and "missing" in call
                                  for call in log_calls), f"Expected missing config log, got: {log_calls}"

                        # Should not have called start_daemon for this agent
                        start_calls = [call[0][0] for call in mock_start.call_args_list]
                        assert "reviewer-1" not in start_calls

                    except (AttributeError, NameError):
                        pytest.fail("validate_daemon_config function not implemented yet (expected for RED test)")

    def test_settings_path_validation(self):
        """Test that missing settings_path is detected and logged clearly."""
        # Create reviewer config but missing settings file
        reviewer_config = {
            "agent_board": {
                "role": "reviewer",
                "poll_columns": ["review"]
            },
            "claude": {
                "settings_path": str(self.config_dir / "settings-reviewer-1.json")
            }
        }
        config_path = self.create_config_file("reviewer-1", reviewer_config)

        # Ensure settings file does NOT exist
        settings_path = self.config_dir / "settings-reviewer-1.json"
        if settings_path.exists():
            settings_path.unlink()

        with patch('builtins.print') as mock_print:
            try:
                validate_daemon_config = getattr(launch_daemons, 'validate_daemon_config', None)
                if validate_daemon_config is None:
                    pytest.fail("validate_daemon_config function not implemented yet (expected for RED test)")

                is_valid = validate_daemon_config("reviewer-1", config_path, settings_path)
                assert is_valid is False

                # Should log the missing settings file clearly
                log_calls = [str(call) for call in mock_print.call_args_list]
                assert any("reviewer-1" in call and "settings" in call and "missing" in call
                          for call in log_calls), f"Expected missing settings log, got: {log_calls}"

            except (AttributeError, NameError):
                pytest.fail("validate_daemon_config function not implemented yet (expected for RED test)")

    @patch('launch_daemons.time.sleep')
    def test_restart_backoff_prevents_storm(self, mock_sleep):
        """Test that consecutive restart failures trigger backoff to prevent restart storms."""
        # Create config but mock start_daemon to always fail
        self.create_config_file("reviewer-1")
        self.create_settings_file("reviewer-1")

        with patch('launch_daemons.Path.home', return_value=self.temp_dir):
            with patch('launch_daemons.start_daemon', side_effect=Exception("Mock failure")):
                with patch('builtins.print') as mock_print:
                    try:
                        # This should call a function that implements backoff logic
                        restart_with_backoff = getattr(launch_daemons, 'restart_with_backoff', None)
                        if restart_with_backoff is None:
                            pytest.fail("restart_with_backoff function not implemented yet (expected for RED test)")

                        # Simulate multiple consecutive failures
                        for attempt in range(1, 4):
                            restart_with_backoff("reviewer-1", "dummy-config", "dummy-log", attempt)

                        # Should have called sleep with increasing backoff
                        expected_sleeps = [1, 4, 9]  # backoff = attempt^2 seconds
                        actual_sleeps = [call[0][0] for call in mock_sleep.call_args_list]
                        assert actual_sleeps == expected_sleeps, f"Expected backoff sleeps {expected_sleeps}, got {actual_sleeps}"

                        # Should log backoff warnings
                        log_calls = [str(call) for call in mock_print.call_args_list]
                        assert any("backoff" in call.lower() for call in log_calls), f"Expected backoff log, got: {log_calls}"

                    except (AttributeError, NameError):
                        pytest.fail("restart_with_backoff function not implemented yet (expected for RED test)")

    def test_failure_count_tracking(self):
        """Test that consecutive failure count is tracked per agent to control backoff."""
        with patch('builtins.print'):
            try:
                # This should call a function that tracks failure counts
                track_failure = getattr(launch_daemons, 'track_restart_failure', None)
                reset_failures = getattr(launch_daemons, 'reset_failure_count', None)
                get_failure_count = getattr(launch_daemons, 'get_failure_count', None)

                if None in (track_failure, reset_failures, get_failure_count):
                    pytest.fail("Failure tracking functions not implemented yet (expected for RED test)")

                # Initially no failures
                assert get_failure_count("reviewer-1") == 0

                # Track some failures
                track_failure("reviewer-1")
                track_failure("reviewer-1")
                assert get_failure_count("reviewer-1") == 2

                # Reset should clear
                reset_failures("reviewer-1")
                assert get_failure_count("reviewer-1") == 0

            except (AttributeError, NameError):
                pytest.fail("Failure tracking functions not implemented yet (expected for RED test)")

    def test_max_restart_attempts_limit(self):
        """Test that there's a maximum restart attempts limit to prevent infinite loops."""
        with patch('launch_daemons.start_daemon', side_effect=Exception("Persistent failure")):
            with patch('builtins.print') as mock_print:
                with patch('launch_daemons.time.sleep'):
                    try:
                        should_attempt_restart = getattr(launch_daemons, 'should_attempt_restart', None)
                        if should_attempt_restart is None:
                            pytest.fail("should_attempt_restart function not implemented yet (expected for RED test)")

                        # After max attempts (e.g., 5), should stop trying
                        agent_id = "reviewer-1"
                        max_attempts = 5

                        for attempt in range(1, max_attempts + 2):
                            should_restart = should_attempt_restart(agent_id, attempt)
                            if attempt <= max_attempts:
                                assert should_restart is True, f"Should attempt restart on attempt {attempt}"
                            else:
                                assert should_restart is False, f"Should stop attempting restart after {max_attempts} attempts"

                        # Should log that max attempts reached
                        log_calls = [str(call) for call in mock_print.call_args_list]
                        assert any("max" in call.lower() and "attempt" in call.lower()
                                  for call in log_calls), f"Expected max attempts log, got: {log_calls}"

                    except (AttributeError, NameError):
                        pytest.fail("should_attempt_restart function not implemented yet (expected for RED test)")

    @patch.dict(os.environ, {"ENABLE_REVIEWERS": "gradual"}, clear=False)
    def test_gradual_feature_flag_mode(self):
        """Test that ENABLE_REVIEWERS=gradual enables only reviewer-1."""
        # Create all reviewer configs
        for i in range(1, 4):
            self.create_config_file(f"reviewer-{i}", {
                "agent_board": {"role": "reviewer", "poll_columns": ["review"]}
            })

        with patch('launch_daemons.Path.home', return_value=self.temp_dir):
            try:
                daemon_specs = getattr(launch_daemons, 'build_daemon_specs', None)
                if daemon_specs is None:
                    pytest.fail("build_daemon_specs function not implemented yet (expected for RED test)")

                specs = daemon_specs()
                agent_ids = [spec[0] for spec in specs]

                # Should include only reviewer-1 in gradual mode
                assert "reviewer-1" in agent_ids
                assert "reviewer-2" not in agent_ids
                assert "reviewer-3" not in agent_ids

            except (AttributeError, NameError):
                pytest.fail("build_daemon_specs function not implemented yet (expected for RED test)")

    def test_reviewer_config_schema_validation(self):
        """Test that reviewer configs are validated for required fields."""
        # Create invalid reviewer config missing required fields
        invalid_config = {
            "agent_board": {
                # Missing role, poll_columns
                "url": "http://localhost:3456"
            }
        }
        config_path = self.create_config_file("reviewer-1", invalid_config)

        with patch('builtins.print') as mock_print:
            try:
                validate_reviewer_config = getattr(launch_daemons, 'validate_reviewer_config', None)
                if validate_reviewer_config is None:
                    pytest.fail("validate_reviewer_config function not implemented yet (expected for RED test)")

                is_valid = validate_reviewer_config(config_path)
                assert is_valid is False

                # Should log specific validation errors
                log_calls = [str(call) for call in mock_print.call_args_list]
                assert any("role" in call and "missing" in call for call in log_calls), \
                    f"Expected missing role validation error, got: {log_calls}"
                assert any("poll_columns" in call and "missing" in call for call in log_calls), \
                    f"Expected missing poll_columns validation error, got: {log_calls}"

            except (AttributeError, NameError):
                pytest.fail("validate_reviewer_config function not implemented yet (expected for RED test)")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])