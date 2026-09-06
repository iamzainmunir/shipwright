"""repomap — pruned tree + key files, budgeted, ignored dirs excluded."""
from __future__ import annotations

from app.repomap import build_context, repo_map


def _mkrepo(base):
    (base / "src").mkdir()
    (base / "src" / "app.py").write_text("print('hi')\n")
    (base / "package.json").write_text('{"name":"svc"}\n')
    (base / "routes").mkdir()
    (base / "routes" / "orders.py").write_text("def orders(): ...\n")
    nm = base / "node_modules"
    nm.mkdir()
    (nm / "junk.js").write_text("x" * 5000)
    (base / ".git").mkdir()
    (base / ".git" / "HEAD").write_text("ref: refs/heads/main")
    return str(base)


def test_repo_map_includes_source_excludes_junk(tmp_path):
    m = repo_map(_mkrepo(tmp_path), budget=4000)
    assert "app.py" in m
    assert "package.json" in m
    assert '"name":"svc"' in m  # key-file contents included
    assert "node_modules" not in m
    assert ".git" not in m
    assert len(m) <= 4000


def test_repo_map_respects_budget(tmp_path):
    root = _mkrepo(tmp_path)
    (tmp_path / "routes" / "big.py").write_text("# " + "x" * 9000)
    m = repo_map(root, budget=500)
    assert len(m) <= 500


def test_build_context_labels_each_project(tmp_path):
    d1 = tmp_path / "svc"
    d1.mkdir()
    (d1 / ".git").mkdir()
    (d1 / "a.py").write_text("a")
    d2 = tmp_path / "gw"
    d2.mkdir()
    (d2 / ".git").mkdir()
    (d2 / "b.py").write_text("b")
    ctx = build_context([("svc", str(d1)), ("gw", str(d2))], budget=1000)
    assert "Project: svc" in ctx
    assert "Project: gw" in ctx
