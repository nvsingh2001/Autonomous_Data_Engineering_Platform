"""Tier 2.5: per-stage checkpointing so a crashed/timed-out pipeline run
resumes from the last completed stage instead of restarting."""

from unittest.mock import MagicMock

import fakeredis
import pytest

import crew
from app import run_store
from crew import DataEngineeringFlow


@pytest.fixture()
def store():
    run_store.use_client(fakeredis.FakeRedis(decode_responses=True))
    yield run_store
    run_store.use_client(None)


_STAGE_CLASSES = [
    "ProfileStep",
    "IntentValidatorStep",
    "QualityStep",
    "SchemaStep",
    "TransformStep",
    "AnalyticsStep",
    "VerifyStep",
    "ReportStep",
]


@pytest.fixture()
def mock_steps(monkeypatch):
    mocks = {}
    for name in _STAGE_CLASSES:
        instance = MagicMock()
        cls = MagicMock(return_value=instance)
        monkeypatch.setattr(crew, name, cls)
        mocks[name] = cls
    return mocks


def _isolate_paths(flow: DataEngineeringFlow, tmp_path) -> None:
    """Point a fresh-run flow at a scratch dir so _clear_previous_run() never
    touches the repo's real data/reports directories."""
    flow.state.data_dir = str(tmp_path / "data")
    flow.state.reports_dir = str(tmp_path / "reports")
    flow.state.db_path = str(tmp_path / "data" / "warehouse.db")


def test_fresh_run_executes_every_stage(mock_steps, tmp_path):
    flow = DataEngineeringFlow()
    _isolate_paths(flow, tmp_path)
    flow.kickoff()
    for name, cls in mock_steps.items():
        cls.return_value.run.assert_called_once()
    assert flow.state.completed_stages == [
        "profile_datasets",
        "validate_intent",
        "assess_quality",
        "check_quality_threshold",
        "design_schema",
        "plan_transformations",
        "run_analytics",
        "verify_answers",
        "compile_final_report",
    ]


def test_resume_skips_completed_stages(mock_steps):
    flow = DataEngineeringFlow()
    checkpoint = {
        "completed_stages": [
            "profile_datasets",
            "validate_intent",
            "assess_quality",
            "check_quality_threshold",
            "design_schema",
        ],
        "star_schema": "already designed",
    }
    flow.kickoff(inputs=checkpoint)

    mock_steps["ProfileStep"].return_value.run.assert_not_called()
    mock_steps["IntentValidatorStep"].return_value.run.assert_not_called()
    mock_steps["QualityStep"].return_value.run.assert_not_called()
    mock_steps["SchemaStep"].return_value.run.assert_not_called()
    mock_steps["TransformStep"].return_value.run.assert_called_once()
    mock_steps["AnalyticsStep"].return_value.run.assert_called_once()
    mock_steps["VerifyStep"].return_value.run.assert_called_once()
    mock_steps["ReportStep"].return_value.run.assert_called_once()
    assert flow.state.star_schema == "already designed"


def test_resume_fetches_missing_warehouse_from_storage(mock_steps, tmp_path, monkeypatch):
    mock_backend = MagicMock()
    mock_backend.download_file.return_value = False
    monkeypatch.setattr(crew, "get_storage_backend", lambda: mock_backend)

    flow = DataEngineeringFlow()
    missing_db_path = str(tmp_path / "data" / "warehouse.db")
    checkpoint = {
        "data_dir": str(tmp_path / "data"),
        "reports_dir": str(tmp_path / "reports"),
        "db_path": missing_db_path,
        "completed_stages": [
            "profile_datasets",
            "validate_intent",
            "assess_quality",
            "check_quality_threshold",
            "design_schema",
        ],
        "star_schema": "already designed",
    }
    flow.kickoff(inputs=checkpoint)

    mock_backend.download_file.assert_any_call("warehouse/warehouse.db", missing_db_path)
    mock_backend.sync_dir_down.assert_any_call("data", str(tmp_path / "data"))


def test_mark_done_checkpoints_to_redis_only_when_run_id_set(store, mock_steps, tmp_path):
    flow = DataEngineeringFlow()
    _isolate_paths(flow, tmp_path)
    flow.state.run_id = "run-123"
    flow.kickoff()
    checkpoint = store.load_checkpoint("run-123")
    assert checkpoint is not None
    assert "compile_final_report" in checkpoint["completed_stages"]


def test_checkpoint_write_failure_does_not_kill_the_run(store, mock_steps, tmp_path, monkeypatch):
    monkeypatch.setattr(
        run_store,
        "save_checkpoint",
        lambda *a, **kw: (_ for _ in ()).throw(ConnectionError("redis down")),
    )
    flow = DataEngineeringFlow()
    _isolate_paths(flow, tmp_path)
    flow.state.run_id = "run-456"
    flow.kickoff()  # must not raise despite every checkpoint write failing
    assert flow.state.completed_stages[-1] == "compile_final_report"


def test_no_checkpoint_written_without_run_id(store, mock_steps, tmp_path):
    flow = DataEngineeringFlow()
    _isolate_paths(flow, tmp_path)
    flow.kickoff()
    assert store.load_checkpoint("") is None


def test_pipeline_task_hydrates_from_checkpoint(store, monkeypatch, mock_steps):
    from app import tasks

    run_id = store.start_run("ignored", {})
    store.save_checkpoint(
        run_id,
        {
            "run_id": run_id,
            "completed_stages": [
                "profile_datasets",
                "validate_intent",
                "assess_quality",
                "check_quality_threshold",
                "design_schema",
                "plan_transformations",
                "run_analytics",
                "verify_answers",
            ],
            "star_schema": "kept from before the crash",
        },
    )
    from tools import HumanLoopService

    monkeypatch.setattr(HumanLoopService, "set_strategy", lambda *a, **kw: None)
    tasks.run_pipeline(run_id, "ignored", {})

    mock_steps["ProfileStep"].return_value.run.assert_not_called()
    mock_steps["TransformStep"].return_value.run.assert_not_called()
    mock_steps["ReportStep"].return_value.run.assert_called_once()
    assert store.get_status(run_id) == "completed"
