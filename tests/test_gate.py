"""C-4 gate runner — AC-6, AC-7, NFR-5."""

from __future__ import annotations

import os
import time

from workgraph.gate import GateResult, run_gate


def _runs(tmp_path):
    d = tmp_path / "runs"
    d.mkdir(exist_ok=True)
    return str(d)


def test_exit_zero(tmp_path):
    r = run_gate("true", cwd=str(tmp_path), timeout=10, runs_dir=_runs(tmp_path))
    assert isinstance(r, GateResult)
    assert r.exit_code == 0


def test_nonzero_exit_propagates(tmp_path):
    r = run_gate("exit 3", cwd=str(tmp_path), timeout=10, runs_dir=_runs(tmp_path))
    assert r.exit_code == 3


def test_captures_stdout_and_stderr(tmp_path):
    r = run_gate(
        "echo out; echo err 1>&2", cwd=str(tmp_path), timeout=10, runs_dir=_runs(tmp_path)
    )
    assert "out" in r.output and "err" in r.output


def test_writes_full_output_to_log(tmp_path):
    r = run_gate("echo hello-log", cwd=str(tmp_path), timeout=10, runs_dir=_runs(tmp_path))
    assert os.path.exists(r.log_path)
    assert "hello-log" in open(r.log_path).read()


def test_timeout_returns_minus_one_promptly(tmp_path):
    start = time.monotonic()
    r = run_gate("sleep 5", cwd=str(tmp_path), timeout=1, runs_dir=_runs(tmp_path))
    assert r.exit_code == -1
    assert time.monotonic() - start < 4  # killed near the timeout, not after the full sleep


def test_output_truncated_to_trailing_4kb_but_log_is_full(tmp_path):
    r = run_gate(
        "python3 -c \"import sys; sys.stdout.write('x'*10000)\"",
        cwd=str(tmp_path),
        timeout=10,
        runs_dir=_runs(tmp_path),
    )
    assert r.exit_code == 0
    assert len(r.output.encode()) <= 4096 + 64  # trailing 4KB + a short marker
    assert r.output.startswith("[truncated")
    assert len(open(r.log_path).read()) == 10000  # log keeps everything


def test_timeout_kills_the_whole_process_group(tmp_path):
    """NFR/AC-7 — a child spawned by the gate is reaped, not orphaned."""
    marker = tmp_path / "child-marker"
    cmd = f"(sleep 2 && touch {marker}) & sleep 10"
    r = run_gate(cmd, cwd=str(tmp_path), timeout=1, runs_dir=_runs(tmp_path))
    assert r.exit_code == -1
    time.sleep(2.5)  # past when the orphaned child would have fired
    assert not marker.exists()
