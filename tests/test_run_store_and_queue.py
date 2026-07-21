"""Redis run store lifecycle + the Celery-backed API endpoints (fakeredis, no
broker: enqueues are monkeypatched at the task boundary)."""

import fakeredis
import pytest

from app import run_store


@pytest.fixture()
def store():
    run_store.use_client(fakeredis.FakeRedis(decode_responses=True))
    yield run_store
    run_store.use_client(None)


# ------------------------------------------------------------------- run store


def test_idle_state_shape(store):
    assert store.get_state() == {
        "status": "idle",
        "error": None,
        "active_step": "idle",
        "activity": [],
        "approval_data": None,
    }


def test_start_run_state_shape_matches_old_manager(store):
    store.start_run("some instructions", {"questions": ["q1"]})
    state = store.get_state()
    assert set(state) == {"status", "error", "active_step", "activity", "approval_data"}
    assert state["status"] == "running"
    assert state["active_step"] == "profiling"
    assert state["error"] is None


def test_log_markers_advance_active_step(store):
    run_id = store.start_run("", {})
    store.append_log(run_id, "[Flow] Designing schema...\n")
    assert store.get_state()["active_step"] == "schema"
    store.append_log(run_id, "\x1b[36m[Flow] Compiling business insights...\x1b[0m\n")
    assert store.get_state()["active_step"] == "analytics"


def test_activity_filters_to_flow_lines_and_strips_ansi(store):
    run_id = store.start_run("", {})
    store.append_log(run_id, "raw agent reasoning that must not leak\n")
    store.append_log(run_id, "\x1b[32m[Flow] Starting data profiling...\x1b[0m\n")
    activity = store.get_state()["activity"]
    assert activity == ["Starting data profiling..."]


def test_complete_records_warehouse_info(store):
    run_id = store.start_run("", {})
    store.complete(run_id, "data/warehouse.db", {"orders.csv": "transaction"})
    assert store.get_status() == "completed"
    db_path, entity_map = store.get_run_info()
    assert db_path == "data/warehouse.db"
    assert entity_map == {"orders.csv": "transaction"}


def test_fail_records_error(store):
    run_id = store.start_run("", {})
    store.fail(run_id, "boom")
    state = store.get_state()
    assert state["status"] == "failed"
    assert state["error"] == "boom"


def test_waiting_approval_exposes_approval_data(store):
    run_id = store.start_run("", {})
    client = store.get_client()
    client.hset(
        f"adep:run:{run_id}",
        mapping={
            "status": "waiting_approval",
            "approval_score": 42,
            "approval_summary": "low quality",
        },
    )
    state = store.get_state()
    assert state["approval_data"] == {"score": 42.0, "summary": "low quality"}


def test_checkpoint_round_trip(store):
    run_id = store.start_run("", {})
    assert store.load_checkpoint(run_id) is None
    store.save_checkpoint(run_id, {"completed_stages": ["profile_datasets"]})
    assert store.load_checkpoint(run_id) == {"completed_stages": ["profile_datasets"]}


def test_is_resumable_requires_failed_status_and_checkpoint(store):
    run_id = store.start_run("", {})
    assert not store.is_resumable(run_id)
    store.save_checkpoint(run_id, {"completed_stages": ["profile_datasets"]})
    assert not store.is_resumable(run_id)  # still "running"
    store.fail(run_id, "timed out")
    assert store.is_resumable(run_id)


def test_complete_clears_checkpoint(store):
    run_id = store.start_run("", {})
    store.save_checkpoint(run_id, {"completed_stages": ["profile_datasets"]})
    store.complete(run_id, "data/warehouse.db", {})
    assert store.load_checkpoint(run_id) is None


def test_resume_run_restores_running_and_current(store):
    run_id = store.start_run("orig instructions", {"questions": ["q"]})
    store.save_checkpoint(run_id, {"completed_stages": ["profile_datasets"]})
    store.fail(run_id, "timed out")
    store.start_run("a different run", {})  # current now points elsewhere
    store.resume_run(run_id)
    assert store.get_status(run_id) == "running"
    assert store.current_run_id() == run_id
    assert store.get_run_args(run_id) == ("orig instructions", {"questions": ["q"]})


def test_new_run_resets_chat_thread(store, monkeypatch):
    import pipeline.core

    monkeypatch.setattr(
        pipeline.core, "new_thread_id", lambda prefix: f"{prefix}-fixed"
    )
    run_id_1 = store.start_run("", {})
    assert store.ensure_chat_thread() == "warehouse-chat-fixed"
    assert store.ensure_chat_thread() == "warehouse-chat-fixed"  # stable per run
    store.start_run("", {})
    client = store.get_client()
    assert client.hget(f"adep:run:{store.current_run_id()}", "chat_thread_id") == ""
    assert store.current_run_id() != run_id_1


# ------------------------------------------------------------- API endpoints


class _FakeAsyncResult:
    def __init__(self, id="task-1"):
        self.id = id


@pytest.fixture()
def client(store, monkeypatch):
    from fastapi.testclient import TestClient
    from app import server

    monkeypatch.setattr(
        server.run_pipeline_task, "apply_async", lambda **kw: _FakeAsyncResult()
    )
    monkeypatch.setattr(
        server.chat_query_task, "delay", lambda *a, **kw: _FakeAsyncResult("chat-1")
    )
    return TestClient(server.app)


def test_run_endpoint_enqueues_and_reports_started(client, store):
    resp = client.post("/api/run", json={"instructions": "analyze revenue"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "started"
    assert store.get_status(body["run_id"]) == "running"
    assert (
        store.get_client().hget(f"adep:run:{body['run_id']}", "task_id") == "task-1"
    )


def test_resume_endpoint_requires_checkpoint(client, store):
    run_id = store.start_run("", {})
    store.fail(run_id, "boom")
    resp = client.post(f"/api/run/{run_id}/resume")
    assert resp.status_code == 400


def test_resume_endpoint_reenqueues_checkpointed_run(client, store):
    run_id = store.start_run("analyze revenue", {})
    store.save_checkpoint(run_id, {"completed_stages": ["profile_datasets"]})
    store.fail(run_id, "timed out")
    resp = client.post(f"/api/run/{run_id}/resume")
    assert resp.status_code == 200
    assert resp.json() == {"status": "resumed", "run_id": run_id}
    assert store.get_status(run_id) == "running"


def test_resume_endpoint_rejects_while_busy(client, store):
    run_id = store.start_run("", {})
    store.save_checkpoint(run_id, {"completed_stages": ["profile_datasets"]})
    store.fail(run_id, "timed out")
    client.post("/api/run", json={"instructions": "another run"})
    resp = client.post(f"/api/run/{run_id}/resume")
    assert resp.status_code == 400


def test_run_endpoint_rejects_while_busy(client, store):
    client.post("/api/run", json={"instructions": ""})
    resp = client.post("/api/run", json={"instructions": ""})
    assert resp.status_code == 400


def test_approve_endpoint_400_when_nothing_pending(client):
    resp = client.post("/api/approve", json={"approved": True})
    assert resp.status_code == 400


def test_approve_endpoint_pushes_decision(client, store):
    run_id = store.start_run("", {})
    store.get_client().hset(f"adep:run:{run_id}", "status", "waiting_approval")
    resp = client.post("/api/approve", json={"approved": True})
    assert resp.status_code == 200
    assert store.get_client().lrange(f"adep:approval:{run_id}", 0, -1) == ["1"]


def test_query_endpoint_requires_completed_run(client):
    resp = client.post("/api/query", json={"question": "total revenue?"})
    assert resp.status_code == 400


def test_query_endpoint_enqueues_on_completed_run(client, store, monkeypatch):
    import pipeline.core

    monkeypatch.setattr(
        pipeline.core, "new_thread_id", lambda prefix: f"{prefix}-fixed"
    )
    run_id = store.start_run("", {})
    store.complete(run_id, "data/warehouse.db", {})
    resp = client.post("/api/query", json={"question": "total revenue?"})
    assert resp.status_code == 200
    assert resp.json() == {"job_id": "chat-1"}


def test_poll_query_maps_celery_states(client, monkeypatch):
    from app import server

    class _Result:
        def __init__(self, state, result=None):
            self._state = state
            self.result = result

        def successful(self):
            return self._state == "SUCCESS"

        def failed(self):
            return self._state == "FAILURE"

    monkeypatch.setattr(
        server, "AsyncResult", lambda job_id, app=None: _Result("SUCCESS", "42 orders")
    )
    assert client.get("/api/query/x").json() == {"status": "done", "answer": "42 orders"}
    monkeypatch.setattr(
        server,
        "AsyncResult",
        lambda job_id, app=None: _Result("FAILURE", RuntimeError("llm down")),
    )
    assert client.get("/api/query/x").json() == {"status": "error", "answer": "llm down"}
    monkeypatch.setattr(
        server, "AsyncResult", lambda job_id, app=None: _Result("PENDING")
    )
    assert client.get("/api/query/x").json() == {"status": "pending", "answer": ""}
