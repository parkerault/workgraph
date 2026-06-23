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


def test_mermaid_subcommand_prints_mermaid(tmp_path, capsys):
    from workgraph.service import Service

    main(["init", str(tmp_path)])
    Service(str(tmp_path)).ingest([{"id": "a", "gate": {"kind": "none"}}])
    capsys.readouterr()  # drop the init message
    rc = main(["mermaid", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("graph LR") and 'a["' in out  # single edgeless node -> auto LR


def test_mermaid_status_flag_accepts_multiple_states(tmp_path, capsys):
    from workgraph.service import Service

    main(["init", str(tmp_path)])
    s = Service(str(tmp_path))
    s.ingest([{"id": "rdy", "gate": {"kind": "command", "command": "true"}},
              {"id": "act", "gate": {"kind": "command", "command": "true"}}])
    s.claim("act")  # rdy -> ready, act -> active
    capsys.readouterr()
    assert main(["mermaid", str(tmp_path), "--status", "active,ready"]) == 0
    out = capsys.readouterr().out
    assert 'rdy["' in out and 'act["' in out


def test_no_command_returns_nonzero(capsys):
    assert main([]) == 1
