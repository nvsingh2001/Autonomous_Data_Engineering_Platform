"""Tier 2 reliability: fail-fast config validation, bounded approval wait,
verification-failure surfacing, and LLM timeout/retry wiring."""

import os
import threading

import fakeredis
import pytest

import config
from agents.providers import (
    LLM_RESILIENCE_KWARGS,
    OllamaProvider,
    CloudProvider,
    BedrockProvider,
)
from app import run_store
from crew import _write_verification_failure
from pipeline.core.state import DataEngineeringState


# ---------------------------------------------------------------- validate_config


def _clean_model_env(monkeypatch):
    monkeypatch.setattr(config, "PIPELINE_MODEL", "ollama/somemodel")
    monkeypatch.setattr(config, "SQL_MODEL", None)
    monkeypatch.setattr(config, "BI_MODEL", None)
    monkeypatch.setattr(config, "PIPELINE_API_KEY", None)
    monkeypatch.setattr(config, "LANGSMITH_TRACING", False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)
    # A developer machine may have ~/.aws/credentials — point the shared-file
    # lookup somewhere empty so "no credentials" is actually true in the test.
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/nonexistent-aws-creds")


def test_default_ollama_config_is_valid(monkeypatch):
    _clean_model_env(monkeypatch)
    assert config.validate_config() == []


def test_cloud_model_without_api_key_is_flagged(monkeypatch):
    _clean_model_env(monkeypatch)
    monkeypatch.setattr(config, "PIPELINE_MODEL", "gpt-4o")
    problems = config.validate_config()
    assert len(problems) == 1
    assert "PIPELINE_API_KEY" in problems[0]


def test_cloud_model_with_api_key_is_valid(monkeypatch):
    _clean_model_env(monkeypatch)
    monkeypatch.setattr(config, "PIPELINE_MODEL", "gpt-4o")
    monkeypatch.setattr(config, "PIPELINE_API_KEY", "some-key")
    assert config.validate_config() == []


def test_bedrock_model_without_aws_creds_is_flagged(monkeypatch):
    _clean_model_env(monkeypatch)
    monkeypatch.setattr(config, "SQL_MODEL", "bedrock/us.amazon.nova-pro-v1:0")
    problems = config.validate_config()
    assert len(problems) == 1
    assert "SQL_MODEL" in problems[0]
    assert "AWS" in problems[0]


def test_bedrock_model_with_aws_creds_is_valid(monkeypatch):
    _clean_model_env(monkeypatch)
    monkeypatch.setattr(config, "SQL_MODEL", "bedrock/us.amazon.nova-pro-v1:0")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
    assert config.validate_config() == []


def test_langsmith_tracing_without_key_is_flagged(monkeypatch):
    _clean_model_env(monkeypatch)
    monkeypatch.setattr(config, "LANGSMITH_TRACING", True)
    monkeypatch.setattr(config, "LANGSMITH_API_KEY", None)
    problems = config.validate_config()
    assert any("LANGSMITH_API_KEY" in p for p in problems)


def test_nonpositive_timeouts_are_flagged(monkeypatch):
    _clean_model_env(monkeypatch)
    monkeypatch.setattr(config, "APPROVAL_TIMEOUT_SECONDS", 0)
    assert config.validate_config()


def test_assert_valid_config_raises_with_all_problems(monkeypatch):
    _clean_model_env(monkeypatch)
    monkeypatch.setattr(config, "PIPELINE_MODEL", "gpt-4o")
    monkeypatch.setattr(config, "BI_MODEL", "bedrock/us.amazon.nova-pro-v1:0")
    with pytest.raises(RuntimeError) as exc:
        config.assert_valid_config()
    assert "PIPELINE_MODEL" in str(exc.value)
    assert "BI_MODEL" in str(exc.value)


def test_assert_valid_config_passes_on_valid_config(monkeypatch):
    _clean_model_env(monkeypatch)
    config.assert_valid_config()


# ---------------------------------------------------------- bounded approval wait


@pytest.fixture()
def store():
    run_store.use_client(fakeredis.FakeRedis(decode_responses=True))
    yield run_store
    run_store.use_client(None)


def test_unanswered_approval_times_out_as_rejected(store, monkeypatch):
    monkeypatch.setattr(config, "APPROVAL_TIMEOUT_SECONDS", 1)
    run_id = store.start_run("", {})
    assert store.request_approval(run_id, 40, "low quality") is False
    state = store.get_state()
    assert state["status"] == "running"
    assert state["approval_data"] is None
    assert any("No approval decision" in line for line in state["activity"])


def test_answered_approval_still_works_within_timeout(store, monkeypatch):
    monkeypatch.setattr(config, "APPROVAL_TIMEOUT_SECONDS", 5)
    run_id = store.start_run("", {})
    threading.Timer(0.05, store.submit_decision, args=(True,)).start()
    assert store.request_approval(run_id, 40, "low quality") is True


def test_answered_rejection_within_timeout(store, monkeypatch):
    monkeypatch.setattr(config, "APPROVAL_TIMEOUT_SECONDS", 5)
    run_id = store.start_run("", {})
    threading.Timer(0.05, store.submit_decision, args=(False,)).start()
    assert store.request_approval(run_id, 40, "low quality") is False


def test_submit_decision_without_pending_approval_is_rejected(store):
    assert store.submit_decision(True) is False
    store.start_run("", {})
    assert store.submit_decision(True) is False  # running, not waiting


# ------------------------------------------------- verification-failure surfacing


def test_verification_failure_writes_failed_report(tmp_path):
    state = DataEngineeringState(reports_dir=str(tmp_path))
    _write_verification_failure(state, RuntimeError("duckdb exploded"))
    report_file = tmp_path / "verification_report.md"
    assert report_file.is_file()
    content = report_file.read_text(encoding="utf-8")
    assert "FAILED" in content
    assert "duckdb exploded" in content
    assert state.verification_report == content


def test_verification_failure_survives_unwritable_reports_dir(tmp_path):
    state = DataEngineeringState(reports_dir=str(tmp_path / "does" / "not" / "exist"))
    _write_verification_failure(state, RuntimeError("boom"))
    assert "FAILED" in state.verification_report


# --------------------------------------------------------- LLM timeout + retries


def test_resilience_kwargs_come_from_config():
    assert LLM_RESILIENCE_KWARGS == {
        "timeout": config.LLM_TIMEOUT_SECONDS,
        "max_retries": config.LLM_MAX_RETRIES,
    }


def test_ollama_provider_llm_gets_timeout_and_retries():
    llm = OllamaProvider("ollama/somemodel", "http://localhost:11434").create(0.1)
    assert llm.timeout == config.LLM_TIMEOUT_SECONDS
    assert getattr(llm, "max_retries") == config.LLM_MAX_RETRIES


def test_cloud_provider_llm_gets_timeout_and_retries(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    llm = CloudProvider("gpt-4o").create(0.1)
    assert llm.timeout == config.LLM_TIMEOUT_SECONDS
    assert getattr(llm, "max_retries") == config.LLM_MAX_RETRIES


def test_bedrock_provider_sets_region_without_mutating_env(monkeypatch):
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    monkeypatch.delenv("AWS_REGION_NAME", raising=False)

    sql_llm = BedrockProvider("bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0", "eu-west-1").create(0.1)
    bi_llm = BedrockProvider("bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0", "ap-south-1").create(0.1)

    assert sql_llm.region_name == "eu-west-1"
    assert bi_llm.region_name == "ap-south-1"
    assert "AWS_DEFAULT_REGION" not in os.environ
    assert "AWS_REGION_NAME" not in os.environ
