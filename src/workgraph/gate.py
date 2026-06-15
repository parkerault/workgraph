"""C-4 — Gate-runner interface. Run a gate command, capture output, enforce timeout.

Caller-driven (D-8). Subprocess in its own process group (session), inheriting the server env
(env=None → inherit os.environ, so a mise-activated launch puts uv/python on PATH). On timeout the
whole process group is killed so a child spawned by the gate is reaped, not orphaned.
"""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass

TRUNCATE_BYTES = 4096


@dataclass
class GateResult:
    exit_code: int  # -1 on timeout (treated as failure)
    output: str  # captured stdout+stderr, truncated to trailing 4 KB for the evidence surface
    duration_s: float
    log_path: str  # gitignored run log with the full captured output


def _truncate(s: str) -> str:
    b = s.encode("utf-8", errors="replace")
    if len(b) <= TRUNCATE_BYTES:
        return s
    tail = b[-TRUNCATE_BYTES:].decode("utf-8", errors="replace")
    return f"[truncated {len(b) - TRUNCATE_BYTES} bytes]\n{tail}"


def _write_log(runs_dir: str | None, content: str) -> str:
    runs_dir = runs_dir or tempfile.gettempdir()
    os.makedirs(runs_dir, exist_ok=True)
    path = os.path.join(runs_dir, f"gate-{time.time_ns()}-{os.getpid()}.log")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def run_gate(
    command: str,
    cwd: str,
    timeout: int,
    env: dict[str, str] | None = None,
    runs_dir: str | None = None,
) -> GateResult:
    """Run `command` in `cwd` (own session, inheriting env); on timeout kill the whole group."""
    start = time.monotonic()
    proc = subprocess.Popen(
        command,
        shell=True,
        cwd=cwd,
        env=env,  # None → inherit the server's environment (PATH, mise shims, …)
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,  # own process group, so we can kill children too
    )
    try:
        out, _ = proc.communicate(timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        out, _ = proc.communicate()
        code = -1
    duration = time.monotonic() - start
    full = out or ""
    log_path = _write_log(runs_dir, full)
    return GateResult(
        exit_code=code, output=_truncate(full), duration_s=duration, log_path=log_path
    )
