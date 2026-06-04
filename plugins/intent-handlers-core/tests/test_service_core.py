"""Unit tests for the service-status deterministic handler (parse gate + render).

cluster_ops_client.call_tool is monkeypatched so no live network is touched.
"""

import service_core


def test_parse_known_units():
    assert service_core.parse_unit("is the hermes-gateway service running") == "hermes-gateway"
    assert service_core.parse_unit("is hermes-gateway running") == "hermes-gateway"
    assert service_core.parse_unit("is the gateway up") == "hermes-gateway"
    assert service_core.parse_unit("status of hermes-gateway") == "hermes-gateway"
    assert service_core.parse_unit("is the hermes gateway running") == "hermes-gateway"


def test_intent_true_for_known_units():
    for q in [
        "is the hermes-gateway service running",
        "is the gateway up",
        "is hermes-gateway running?",
        "status of the gateway",
        "is the hermes-gateway service active",
    ]:
        assert service_core.is_service_intent(q) is True, q


def test_unknown_unit_falls_through():
    # Shape matches but the unit is unknown -> must NOT fast-path (no guessing).
    for q in [
        "is foobar running",
        "is the postgres service running",
        "is nginx up",
        "status of redis",
    ]:
        assert service_core.parse_unit(q) is None, q
        assert service_core.is_service_intent(q) is False, q


def test_false_positives_fall_through():
    # Prose / weather / non-service questions must not match at all.
    for q in [
        "is it raining",
        "is it cold outside",
        "is my code any good",
        "what services do you offer",
        "is everything ok",
        "is this a good idea",
        "/svcstatus",
        "",
    ]:
        assert service_core.is_service_intent(q) is False, q


def _patch(monkeypatch, payload):
    monkeypatch.setattr(service_core.cluster_ops_client, "call_tool", lambda *a, **k: payload)


def test_answer_running(monkeypatch):
    payload = {
        "host": "hermes", "unit": "hermes-gateway", "pid": 2994012,
        "restarts": 0, "loaded": "loaded", "active": "active",
        "sub_state": "running", "since": "Thu 2026-06-04 16:31:58 UTC",
    }
    _patch(monkeypatch, payload)
    out = service_core.answer_service("is the hermes-gateway service running")
    assert out is not None
    assert "hermes-gateway" in out
    assert "running" in out.lower()


def test_answer_stopped(monkeypatch):
    payload = {"host": "hermes", "unit": "hermes-gateway",
               "loaded": "loaded", "active": "inactive", "sub_state": "dead"}
    _patch(monkeypatch, payload)
    out = service_core.answer_service("is the gateway up")
    assert out is not None
    assert "stopped" in out.lower()


def test_none_unknown_unit(monkeypatch):
    _patch(monkeypatch, {"active": "active", "sub_state": "running"})
    assert service_core.answer_service("is redis running") is None


def test_none_when_unconfigured(monkeypatch):
    _patch(monkeypatch, None)
    assert service_core.answer_service("is the hermes-gateway service running") is None
