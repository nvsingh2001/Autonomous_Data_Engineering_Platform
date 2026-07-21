from app.celery_app import celery


def test_stdout_redirect_disabled_to_avoid_doubled_activity_log():
    """Celery's own stdout redirect would re-emit each already-formatted
    [Flow] line as a second log record, doubling every entry the SPA
    activity feed shows — our _RedisLogRedirector already owns capture."""
    assert celery.conf.worker_redirect_stdouts is False


def test_root_logger_hijack_disabled():
    assert celery.conf.worker_hijack_root_logger is False
