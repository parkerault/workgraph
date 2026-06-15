"""C-1 store layer — AC-3, AC-16, AC-17, AC-18, NFR-3, NFR-4."""

from __future__ import annotations

import os

import pytest

from workgraph import store
from workgraph.errors import ConcurrencyError, ValidationError
from workgraph.models import Gate, GateKind, Graph, Node, Status

EMPTY = "version: 1\nworking_dir: .\nnodes: []\n"


def _init(root, yaml_text=EMPTY):
    d = os.path.join(root, ".workgraph")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "graph.yaml"), "w") as f:
        f.write(yaml_text)


def _cmd_node(nid, **kw):
    kw.setdefault("gate", Gate(kind=GateKind.COMMAND, command="true"))
    return Node(id=nid, **kw)


# ---- load -------------------------------------------------------------------

def test_load_empty_store(tmp_path):
    _init(str(tmp_path))
    g, base_hash = store.load(str(tmp_path))
    assert isinstance(g, Graph)
    assert g.nodes == {}
    assert g.version == 1 and g.working_dir == "."
    assert isinstance(base_hash, str) and base_hash


def test_load_parses_a_node(tmp_path):
    _init(
        str(tmp_path),
        "version: 1\nworking_dir: .\nnodes:\n"
        "  - id: a\n    kind: unit\n    deps: []\n    status: ready\n"
        "    gate: {kind: command, command: 'pytest'}\n",
    )
    g, _ = store.load(str(tmp_path))
    assert set(g.nodes) == {"a"}
    a = g.nodes["a"]
    assert a.status == Status.READY
    assert a.gate.kind == GateKind.COMMAND and a.gate.command == "pytest"


def test_load_rejects_duplicate_ids(tmp_path):
    _init(
        str(tmp_path),
        "version: 1\nworking_dir: .\nnodes:\n"
        "  - id: a\n    gate: {kind: none}\n"
        "  - id: a\n    gate: {kind: none}\n",
    )
    with pytest.raises(ValidationError):
        store.load(str(tmp_path))


def test_load_rejects_unparseable_file_fail_closed(tmp_path):
    _init(str(tmp_path), "version: 1\nnodes: [oops: : :\n")
    with pytest.raises(ValidationError):
        store.load(str(tmp_path))


def test_load_rejects_bad_gate_kind(tmp_path):
    _init(
        str(tmp_path),
        "version: 1\nworking_dir: .\nnodes:\n  - id: a\n    gate: {kind: bogus}\n",
    )
    with pytest.raises(ValidationError):
        store.load(str(tmp_path))


# ---- round-trip + save ------------------------------------------------------

def test_round_trip_preserves_node(tmp_path):
    _init(str(tmp_path))
    g, h = store.load(str(tmp_path))
    g.nodes["a"] = _cmd_node("a", deps=[], status=Status.READY)
    store.save(str(tmp_path), g, h)
    g2, _ = store.load(str(tmp_path))
    assert set(g2.nodes) == {"a"}
    assert g2.nodes["a"].gate.command == "true"
    assert g2.nodes["a"].status == Status.READY


def test_save_returns_new_hash_that_loads_back(tmp_path):
    _init(str(tmp_path))
    g, h = store.load(str(tmp_path))
    g.nodes["a"] = _cmd_node("a")
    new_hash = store.save(str(tmp_path), g, h)
    _, h2 = store.load(str(tmp_path))
    assert new_hash == h2


def test_save_rejects_stale_base_hash(tmp_path):
    """AC-18 — a concurrent external edit makes the base hash stale; the write is refused."""
    _init(str(tmp_path))
    g, h = store.load(str(tmp_path))
    # Another writer changes the file underneath us.
    _init(str(tmp_path), "version: 1\nworking_dir: .\nnodes:\n  - id: x\n    gate: {kind: none}\n")
    g.nodes["a"] = _cmd_node("a")
    with pytest.raises(ConcurrencyError):
        store.save(str(tmp_path), g, h)


# ---- validation on save (AC-3) ---------------------------------------------

def test_save_rejects_unknown_dep(tmp_path):
    _init(str(tmp_path))
    g, h = store.load(str(tmp_path))
    g.nodes["a"] = _cmd_node("a", deps=["ghost"])
    with pytest.raises(ValidationError):
        store.save(str(tmp_path), g, h)


def test_save_rejects_unknown_parent(tmp_path):
    _init(str(tmp_path))
    g, h = store.load(str(tmp_path))
    g.nodes["a"] = _cmd_node("a", parent="ghost")
    with pytest.raises(ValidationError):
        store.save(str(tmp_path), g, h)


def test_save_rejects_self_parent(tmp_path):
    _init(str(tmp_path))
    g, h = store.load(str(tmp_path))
    g.nodes["a"] = _cmd_node("a", parent="a")
    with pytest.raises(ValidationError):
        store.save(str(tmp_path), g, h)


def test_save_rejects_parent_cycle(tmp_path):
    _init(str(tmp_path))
    g, h = store.load(str(tmp_path))
    g.nodes["a"] = _cmd_node("a", parent="b")
    g.nodes["b"] = _cmd_node("b", parent="a")
    with pytest.raises(ValidationError):
        store.save(str(tmp_path), g, h)


def test_save_rejects_command_gate_without_command(tmp_path):
    _init(str(tmp_path))
    g, h = store.load(str(tmp_path))
    g.nodes["a"] = Node(id="a", gate=Gate(kind=GateKind.COMMAND, command=None))
    with pytest.raises(ValidationError):
        store.save(str(tmp_path), g, h)


# ---- AC-16: atomicity + minimal diff ---------------------------------------

def test_save_is_atomic_no_partial_on_validation_failure(tmp_path):
    """A rejected save leaves graph.yaml byte-identical (AC-2/AC-3 atomicity, NFR-3)."""
    _init(str(tmp_path))
    g, h = store.load(str(tmp_path))
    g.nodes["a"] = _cmd_node("a")
    store.save(str(tmp_path), g, h)
    before = open(os.path.join(str(tmp_path), ".workgraph", "graph.yaml")).read()
    g2, h2 = store.load(str(tmp_path))
    g2.nodes["bad"] = _cmd_node("bad", deps=["ghost"])
    with pytest.raises(ValidationError):
        store.save(str(tmp_path), g2, h2)
    after = open(os.path.join(str(tmp_path), ".workgraph", "graph.yaml")).read()
    assert before == after


def test_crash_before_rename_leaves_committed_state_intact(tmp_path, monkeypatch):
    """AC-17 / NFR-3 — a fault at the rename point leaves the last committed graph.yaml intact."""
    _init(str(tmp_path))
    g, h = store.load(str(tmp_path))
    g.nodes["a"] = _cmd_node("a")
    store.save(str(tmp_path), g, h)
    committed = open(os.path.join(str(tmp_path), ".workgraph", "graph.yaml")).read()

    g2, h2 = store.load(str(tmp_path))
    g2.nodes["b"] = _cmd_node("b")

    def boom(*a, **k):
        raise OSError("simulated crash before rename")

    monkeypatch.setattr(store.os, "replace", boom)
    with pytest.raises(OSError):
        store.save(str(tmp_path), g2, h2)

    monkeypatch.undo()
    assert open(os.path.join(str(tmp_path), ".workgraph", "graph.yaml")).read() == committed
    g3, _ = store.load(str(tmp_path))
    assert "b" not in g3.nodes and "a" in g3.nodes


def test_save_minimal_diff_touches_only_changed_node(tmp_path):
    """AC-16 — mutating node b leaves a's serialized lines unchanged."""
    _init(str(tmp_path))
    g, h = store.load(str(tmp_path))
    g.nodes["a"] = _cmd_node("a")
    g.nodes["b"] = _cmd_node("b")
    h = store.save(str(tmp_path), g, h)
    text1 = open(os.path.join(str(tmp_path), ".workgraph", "graph.yaml")).read().splitlines()
    g, h = store.load(str(tmp_path))
    g.nodes["b"].status = Status.DONE
    store.save(str(tmp_path), g, h)
    text2 = open(os.path.join(str(tmp_path), ".workgraph", "graph.yaml")).read().splitlines()
    # The only changed lines mention b's new status; a's lines are untouched.
    changed = [ln for ln in text2 if ln not in text1]
    assert changed and all("done" in ln for ln in changed)


# ---- NFR-4: compactness -----------------------------------------------------

def test_serialization_omits_empty_optionals(tmp_path):
    _init(str(tmp_path))
    g, h = store.load(str(tmp_path))
    g.nodes["a"] = _cmd_node("a")  # no parent/rationale/signoff/last_verify
    store.save(str(tmp_path), g, h)
    text = open(os.path.join(str(tmp_path), ".workgraph", "graph.yaml")).read()
    assert "signoff" not in text and "last_verify" not in text and "parent" not in text
    assert "rationale" not in text


# ---- write_rationale --------------------------------------------------------

def test_write_rationale_creates_tracked_md_and_sets_field(tmp_path):
    _init(str(tmp_path))
    g, h = store.load(str(tmp_path))
    g.nodes["a"] = _cmd_node("a")
    store.save(str(tmp_path), g, h)
    path = store.write_rationale(str(tmp_path), "a", "we decided X because Y")
    full = os.path.join(str(tmp_path), ".workgraph", path) if not os.path.isabs(path) else path
    assert os.path.exists(full)
    assert "decided X" in open(full).read()
    g2, _ = store.load(str(tmp_path))
    assert g2.nodes["a"].rationale  # field now points at the md
