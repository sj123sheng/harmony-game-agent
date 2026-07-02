"""server.py /export 端点单测。"""

import io
import os
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from starlette.testclient import TestClient

import server


def _make_generated(tmp: Path) -> Path:
    gen = tmp / "generated"
    gen.mkdir()
    (gen / "character").mkdir()
    (gen / "character" / "Foo.ets").write_text("@Component struct Foo {}", encoding="utf-8")
    (gen / "Bar.json").write_text('{"k":1}', encoding="utf-8")
    return gen


def _patch_base_dir(tmp: Path):
    import server as srv
    orig = srv.BASE_DIR
    srv.BASE_DIR = tmp
    return orig


def test_export_single_file_ets():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _make_generated(tmp)
        orig = _patch_base_dir(tmp)
        try:
            client = TestClient(server.app)
            resp = client.get("/export", params={"path": "character/Foo.ets"})
            assert resp.status_code == 200
            assert "Foo" in resp.text
            assert "text/plain" in resp.headers["content-type"]
            assert 'attachment' in resp.headers["content-disposition"]
        finally:
            server.BASE_DIR = orig
    print("[OK] test_export_single_file_ets")


def test_export_single_file_json():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _make_generated(tmp)
        orig = _patch_base_dir(tmp)
        try:
            client = TestClient(server.app)
            resp = client.get("/export", params={"path": "Bar.json"})
            assert resp.status_code == 200
            assert "application/json" in resp.headers["content-type"]
        finally:
            server.BASE_DIR = orig
    print("[OK] test_export_single_file_json")


def test_export_directory_returns_zip():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _make_generated(tmp)
        orig = _patch_base_dir(tmp)
        try:
            client = TestClient(server.app)
            resp = client.get("/export", params={"path": "character"})
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "application/zip"
            zf = zipfile.ZipFile(io.BytesIO(resp.content))
            names = zf.namelist()
            assert "Foo.ets" in names
        finally:
            server.BASE_DIR = orig
    print("[OK] test_export_directory_returns_zip")


def test_export_path_traversal_rejected():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _make_generated(tmp)
        orig = _patch_base_dir(tmp)
        try:
            client = TestClient(server.app)
            resp = client.get("/export", params={"path": "../../etc/passwd"})
            assert resp.status_code == 400
        finally:
            server.BASE_DIR = orig
    print("[OK] test_export_path_traversal_rejected")


def test_export_nonexistent_returns_404():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _make_generated(tmp)
        orig = _patch_base_dir(tmp)
        try:
            client = TestClient(server.app)
            resp = client.get("/export", params={"path": "nope"})
            assert resp.status_code == 404
        finally:
            server.BASE_DIR = orig
    print("[OK] test_export_nonexistent_returns_404")


def test_export_missing_path_returns_400():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _make_generated(tmp)
        orig = _patch_base_dir(tmp)
        try:
            client = TestClient(server.app)
            resp = client.get("/export")
            assert resp.status_code == 400
        finally:
            server.BASE_DIR = orig
    print("[OK] test_export_missing_path_returns_400")


def test_build_zip_no_slip():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        gen = _make_generated(tmp)
        data = server._build_zip(gen / "character")
        zf = zipfile.ZipFile(io.BytesIO(data))
        for name in zf.namelist():
            assert not name.startswith("/")
            assert ".." not in name.split("/")
    print("[OK] test_build_zip_no_slip")


def main():
    test_export_single_file_ets()
    test_export_single_file_json()
    test_export_directory_returns_zip()
    test_export_path_traversal_rejected()
    test_export_nonexistent_returns_404()
    test_export_missing_path_returns_400()
    test_build_zip_no_slip()
    print("\n全部通过。")


if __name__ == "__main__":
    main()
