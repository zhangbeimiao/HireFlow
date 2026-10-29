from hireflow.health import readiness_response


def test_readiness_reports_all_critical_dependencies_ready() -> None:
    status_code, payload = readiness_response(postgres_ok=True, redis_ok=True)

    assert status_code == 200
    assert payload == {
        "status": "ready",
        "dependencies": {"postgres": "ok", "redis": "ok"},
    }


def test_readiness_degrades_when_optional_redis_is_unavailable() -> None:
    status_code, payload = readiness_response(postgres_ok=True, redis_ok=False)

    assert status_code == 200
    assert payload == {
        "status": "degraded",
        "dependencies": {"postgres": "ok", "redis": "degraded"},
    }


def test_readiness_fails_when_postgres_is_unavailable_without_leaking_details() -> None:
    status_code, payload = readiness_response(postgres_ok=False, redis_ok=False)

    assert status_code == 503
    assert payload == {
        "status": "not_ready",
        "dependencies": {"postgres": "unavailable", "redis": "degraded"},
    }
    serialized = str(payload).lower()
    assert "password" not in serialized
    assert "postgresql://" not in serialized
