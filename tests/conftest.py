"""Shared fixtures for offline generation and isolated state directories.

The repository root stays on the import path for `compare_typst.py`. Pipeline
tests receive explicit configuration, state and output paths.
"""

import os
import shutil
import socket
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMITTED_CONFIGURATION = os.path.join(ROOT, "state", "active-configuration.toml")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture(autouse=True, scope="session")
def _run_from_repository_root():
    previous = os.getcwd()
    os.chdir(ROOT)
    yield
    os.chdir(previous)


@pytest.fixture
def offline_run(tmp_path, monkeypatch):
    """
    An offline generation against `fixtures/2026` with real network access blocked. The working
    directory is an empty scratch directory, separate from the state and output directories the
    test passes in, so a test can prove a generation writes nothing relative to it.
    """
    working_directory = tmp_path / "cwd"
    working_directory.mkdir()
    monkeypatch.chdir(working_directory)
    monkeypatch.setenv("JGP_FIXTURES_DIR", os.path.join(ROOT, "fixtures", "2026"))

    def guard(*args, **kwargs):
        raise AssertionError("network access attempted during an offline fixture run")

    monkeypatch.setattr(socket.socket, "connect", guard)
    return tmp_path


@pytest.fixture
def state_directory(offline_run):
    """A scratch state directory holding a copy of the committed configuration and nothing else."""
    directory = offline_run / "state"
    directory.mkdir()
    shutil.copy(COMMITTED_CONFIGURATION, directory)
    return directory
