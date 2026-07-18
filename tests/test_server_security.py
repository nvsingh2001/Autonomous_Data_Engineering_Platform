import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

import config
from app import server


@pytest.fixture()
def client():
    return TestClient(server.app)


@pytest.fixture()
def auth_off(monkeypatch):
    monkeypatch.setattr(config, "WEB_API_KEY", None)


@pytest.fixture()
def auth_on(monkeypatch):
    monkeypatch.setattr(config, "WEB_API_KEY", "test-key-123")


# --- path traversal -------------------------------------------------------

TRAVERSAL_NAMES = ["..%2F..%2Fconfig.py", "..%5C..%5Cconfig.py", ".env"]


@pytest.mark.parametrize("name", TRAVERSAL_NAMES)
def test_report_read_rejects_traversal(client, auth_off, name):
    assert client.get(f"/api/reports/{name}").status_code == 404


@pytest.mark.parametrize("name", TRAVERSAL_NAMES)
def test_report_download_rejects_traversal(client, auth_off, name):
    assert client.get(f"/api/reports/download/{name}").status_code == 404


@pytest.mark.parametrize("name", TRAVERSAL_NAMES + ["..%2F..%2FREADME.md"])
def test_delete_rejects_traversal(client, auth_off, name):
    assert client.delete(f"/api/files/{name}").status_code == 404
    assert os.path.exists("README.md")


def test_report_read_serves_legit_report(client, auth_off):
    path = os.path.join(server.REPORTS_DIR, "security_test_report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("hello")
    try:
        res = client.get("/api/reports/security_test_report.md")
        assert res.status_code == 200
        assert res.json()["content"] == "hello"
    finally:
        os.remove(path)


def test_upload_strips_directory_components(client, auth_off):
    res = client.post(
        "/api/upload",
        files={"files": ("../../evil_upload_test.csv", b"a,b\n1,2\n", "text/csv")},
    )
    assert res.status_code == 200
    assert res.json()["saved"] == ["evil_upload_test.csv"]
    inside = os.path.join(server.DATA_DIR, "evil_upload_test.csv")
    assert os.path.isfile(inside)
    assert not os.path.exists("evil_upload_test.csv")
    os.remove(inside)


def test_upload_rejects_disguised_extension(client, auth_off):
    res = client.post(
        "/api/upload",
        files={"files": ("innocent.csv/../../evil.py", b"print(1)", "text/csv")},
    )
    assert res.status_code == 422


def test_sanitize_upload_name():
    assert server._sanitize_upload_name("../../etc/passwd") == "passwd"
    assert server._sanitize_upload_name("..\\..\\boot.ini") == "boot.ini"
    assert server._sanitize_upload_name(".hidden.csv") == "hidden.csv"
    assert server._sanitize_upload_name(None) == "upload"
    assert server._sanitize_upload_name("sales data (2024).csv") == "sales data _2024_.csv"


# --- authentication -------------------------------------------------------


def test_api_requires_key_when_configured(client, auth_on):
    assert client.get("/api/status").status_code == 401
    assert (
        client.get("/api/status", headers={"X-API-Key": "wrong"}).status_code == 401
    )
    assert (
        client.get("/api/status", headers={"X-API-Key": "test-key-123"}).status_code
        == 200
    )


def test_auth_disabled_when_key_unset(client, auth_off):
    assert client.get("/api/status").status_code == 200


def test_capability_routes_stay_public(client, auth_on):
    # Charts/exports are fetched by <img>/anchor tags that cannot send headers;
    # they must 404 (unknown file), never 401.
    assert client.get(f"/api/charts/{'a' * 32}.png").status_code == 404
    assert client.get(f"/api/exports/{'a' * 32}.csv").status_code == 404


def test_spa_shell_stays_public(client, auth_on):
    assert client.get("/").status_code == 200
