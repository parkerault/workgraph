"""Minimal CLI — AC-27 (init writes a valid, loadable store)."""

from __future__ import annotations

from workgraph import store
from workgraph.cli import main


def test_init_creates_loadable_store(tmp_path):
    rc = main(["init", str(tmp_path)])
    assert rc == 0
    g, _ = store.load(str(tmp_path))  # AC-27: loads cleanly
    assert g.nodes == {}


def test_init_is_idempotent(tmp_path):
    assert main(["init", str(tmp_path)]) == 0
    assert main(["init", str(tmp_path)]) == 0


def test_no_command_returns_nonzero(capsys):
    assert main([]) == 1
