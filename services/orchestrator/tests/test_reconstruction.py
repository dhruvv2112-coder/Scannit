"""Tests for reconstruction worker utilities — config validation and CLI arg building.

These tests import only the utility functions, avoiding the Celery dependency
so they can run in environments where Celery is not installed.
"""

import importlib
import sys
from unittest.mock import MagicMock

import pytest

from scannit_schemas.enums import JobStatus


# --- Mock celery before importing the worker module ---
# The worker module imports from app.celery_app which requires celery.
# We mock it at the module level so the import succeeds without celery installed.

_celery_mock = MagicMock()
sys.modules.setdefault("celery", _celery_mock)
sys.modules.setdefault("app.celery_app", MagicMock(celery=_celery_mock))

from app.workers.reconstruction import (  # noqa: E402
    ALLOWED_CONFIG_KEYS,
    _build_cli_args,
    _validate_config,
)


class TestConfigValidation:
    """Test that only allowed config keys pass through."""

    def test_allowed_keys_pass(self):
        config = {"dense": True, "max_frames": 500, "mesh_quality": "high"}
        result = _validate_config(config)
        assert result == config

    def test_unknown_keys_stripped(self):
        config = {"dense": True, "rm_rf": "/", "shell_cmd": "evil"}
        result = _validate_config(config)
        assert result == {"dense": True}
        assert "rm_rf" not in result
        assert "shell_cmd" not in result

    def test_empty_config(self):
        assert _validate_config({}) == {}

    def test_all_allowed_keys_documented(self):
        """Verify the allowed keys set is non-empty and contains expected entries."""
        assert "dense" in ALLOWED_CONFIG_KEYS
        assert "max_frames" in ALLOWED_CONFIG_KEYS
        assert "scale_reference_path" in ALLOWED_CONFIG_KEYS
        assert len(ALLOWED_CONFIG_KEYS) > 5


class TestCliArgs:
    """Test CLI argument construction — no shell injection possible."""

    def test_basic_args(self):
        args = _build_cli_args(
            stage=JobStatus.VALIDATING,
            job_id="abc-123",
            dataset_path="/data/datasets/abc",
            work_dir="/data/jobs/abc-123",
            config={},
        )
        assert args[0] == "scannit-recon"
        assert "--stage" in args
        assert "validating" in args
        assert "--job-id" in args
        assert "abc-123" in args

    def test_config_flags_added(self):
        args = _build_cli_args(
            stage=JobStatus.MESH,
            job_id="j1",
            dataset_path="/d",
            work_dir="/w",
            config={"dense": False, "max_frames": 500},
        )
        assert "--dense" in args
        assert "False" in args
        assert "--max-frames" in args
        assert "500" in args

    def test_nested_config_ignored(self):
        """Nested dicts/lists should not become CLI args."""
        args = _build_cli_args(
            stage=JobStatus.MESH,
            job_id="j1",
            dataset_path="/d",
            work_dir="/w",
            config={"dense": True, "nested": {"evil": "payload"}, "list_val": [1, 2]},
        )
        # Only the simple "dense" key should produce a flag
        flag_count = sum(
            1 for a in args
            if a.startswith("--") and a not in ("--stage", "--job-id", "--dataset-path", "--work-dir")
        )
        assert flag_count == 1

    def test_underscores_to_hyphens(self):
        args = _build_cli_args(
            stage=JobStatus.MESH,
            job_id="j1",
            dataset_path="/d",
            work_dir="/w",
            config={"scale_reference_path": "/ref/scale.json"},
        )
        assert "--scale-reference-path" in args
