"""Unit tests for the disk-free deterministic handler.

cluster_ops_client.call_tool is monkeypatched so no live network is touched.
"""

import disk_core


def test_matches_disk_questions():
    for q in [
        "how much disk is free",
        "how much disk space is free",
        "how much disk space is left",
        "disk space",
        "disk usage",
        "disk free",
        "free disk space",
        "what's the disk usage",
        "show me disk space",
        "check disk space",
        "disk space left",
    ]:
        assert disk_core.is_disk_intent(q) is True, q


def test_false_positives_fall_through():
    for q in [
        "tell me about disk drives in history",
        "how much time do I have",
        "what should I do about my busy schedule",
        "what services do you offer",
        "buy a new disk",
        "the floppy disk era",
        "/diskfree",
        "",
    ]:
        assert disk_core.is_disk_intent(q) is False, q


def _patch(monkeypatch, payload):
    monkeypatch.setattr(disk_core.cluster_ops_client, "call_tool", lambda *a, **k: payload)


def test_answer_renders_free_and_gb(monkeypatch):
    payload = {
        "host": "hermes",
        "filesystems": [
            {"mount": "/", "total_gb": 97.87, "used_gb": 66.13, "use_pct": 67.6, "fs_type": "ext4"}
        ],
        "count": 1,
    }
    _patch(monkeypatch, payload)
    out = disk_core.answer_disk("disk space")
    assert out is not None
    assert "free" in out.lower()
    assert "GB" in out
    assert "hermes" in out
    assert "/" in out


def test_none_when_unconfigured(monkeypatch):
    _patch(monkeypatch, None)  # cluster-ops returns None (e.g. no creds)
    assert disk_core.answer_disk("disk space") is None


def test_none_when_no_filesystems(monkeypatch):
    _patch(monkeypatch, {"host": "hermes", "filesystems": []})
    assert disk_core.answer_disk("disk space") is None


def test_none_when_malformed_rows(monkeypatch):
    _patch(monkeypatch, {"host": "hermes", "filesystems": [{"mount": "/"}]})  # no totals
    assert disk_core.answer_disk("disk space") is None
