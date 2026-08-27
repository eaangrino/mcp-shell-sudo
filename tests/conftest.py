from __future__ import annotations

import os

import pytest

SSH_TEST_HOST_ENV = "MCP_SHELL_TEST_SSH_HOST"
DEFAULT_SSH_TEST_HOST = "test-host"


@pytest.fixture(scope="session")
def ssh_test_host() -> str:
    """Return the SSH alias used by tests without binding the suite to one machine."""
    return os.environ.get(SSH_TEST_HOST_ENV, DEFAULT_SSH_TEST_HOST)
