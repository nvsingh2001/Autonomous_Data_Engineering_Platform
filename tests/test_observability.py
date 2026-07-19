"""Tier 3 observability: structured logging (through the Redis run capture)
and the /api/ready readiness probe."""

import logging
import re

import pytest

import config
from app import run_store
from logging_setup import get_logger, setup_logging


# ---------------------------------------------------------------- logging core


def test_log_line_format(capsys):
    setup_logging()
    get_logger("Flow").info("Designing schema...")
    line = capsys.readouterr().out.strip()
    assert re.match(r"^\d\d:\d\d:\d\d INFO \[Flow\] Designing schema\.\.\.$", line)


def test_level_filtering(capsys):
    setup_logging()
    logger = get_logger("Flow")
    logger.debug("hidden at INFO")
    assert "hidden at INFO" not in capsys.readouterr().out


def test_third_party_loggers_are_quieted(capsys):
    setup_logging()
    logging.getLogger("httpx").info("chatty request line")
    assert capsys.readouterr().out == ""


def test_flow_log_lines_reach_activity_and_step_markers(tmp_path):
    """The whole contract chain: Flow logger → late-bound stdout handler →
    the Celery task's Redis redirector → activity feed + step markers."""
    from app.tasks import _RedisLogRedirector

    run_id = run_store.start_run("", {})
    redirector = _RedisLogRedirector(run_id)
    redirector.log_path = str(tmp_path / "execution.log")
    with redirector:
        get_logger("Flow").info("Designing schema...")
        get_logger("Flow").warning("Could not count source rows: boom")
    state = run_store.get_state()
    assert state["active_step"] == "schema"  # marker matched inside the log line
    assert "Designing schema..." in state["activity"]
    assert "Could not count source rows: boom" in state["activity"]
    on_disk = (tmp_path / "execution.log").read_text()
    assert "[Flow] Designing schema..." in on_disk


# ------------------------------------------------------------------- readiness


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app import server

    return TestClient(server.app)


class _DeadRedis:
    def ping(self):
        raise ConnectionError("no redis here")


def test_ready_when_redis_up_and_dirs_writable(client):
    resp = client.get("/api/ready")
    assert resp.status_code == 200
    assert resp.json() == {"ready": True}


def test_not_ready_when_redis_down(client):
    run_store.use_client(_DeadRedis())
    resp = client.get("/api/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["ready"] is False
    assert any("redis" in p for p in body["problems"])


def test_ready_is_public_but_status_is_not(client, monkeypatch):
    monkeypatch.setattr(config, "WEB_API_KEY", "sekrit")
    assert client.get("/api/ready").status_code == 200
    assert client.get("/api/status").status_code == 401


# ----------------------------------------------------- config validation bits


def test_invalid_log_level_is_flagged(monkeypatch):
    monkeypatch.setattr(config, "LOG_LEVEL", "LOUD")
    assert any("LOG_LEVEL" in p for p in config.validate_config())


def test_bedrock_accepts_shared_credentials_file(monkeypatch, tmp_path):
    creds = tmp_path / "credentials"
    creds.write_text("[default]\naws_access_key_id=AKIATEST\n")
    monkeypatch.setattr(config, "PIPELINE_MODEL", "ollama/somemodel")
    monkeypatch.setattr(config, "SQL_MODEL", "bedrock/some.model")
    monkeypatch.setattr(config, "BI_MODEL", None)
    monkeypatch.setattr(config, "LANGSMITH_TRACING", False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(creds))
    assert config.validate_config() == []
