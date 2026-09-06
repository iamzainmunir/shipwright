"""Filesystem directory-browser endpoint for the path picker — lists dirs, flags git repos."""
from __future__ import annotations

from app.main import create_app
from fastapi.testclient import TestClient

client = TestClient(create_app())


def test_fs_list_returns_dirs_and_flags_repos(tmp_path):
    (tmp_path / "gateway" / ".git").mkdir(parents=True)  # a git repo
    (tmp_path / "notes").mkdir()  # a plain dir
    (tmp_path / ".hidden").mkdir()  # hidden → omitted
    (tmp_path / "readme.txt").write_text("x")  # a file → omitted

    r = client.get("/api/v1/fs/list", params={"path": str(tmp_path)})
    assert r.status_code == 200
    body = r.json()
    assert body["path"] == str(tmp_path.resolve())
    assert body["parent"] == str(tmp_path.resolve().parent)
    names = {e["name"]: e for e in body["entries"]}
    assert set(names) == {"gateway", "notes"}  # no hidden dir, no file
    assert names["gateway"]["isRepo"] is True
    assert names["notes"]["isRepo"] is False
    assert names["gateway"]["path"] == str((tmp_path / "gateway").resolve())


def test_fs_list_falls_back_to_home_for_bad_path(tmp_path):
    r = client.get("/api/v1/fs/list", params={"path": str(tmp_path / "does-not-exist")})
    assert r.status_code == 200
    # bad path → resolved to the home dir (a real directory), never a 500
    assert r.json()["path"]
