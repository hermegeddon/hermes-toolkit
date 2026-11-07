"""Behavior tests for argument-aware tool-call assertions and Kanban fixtures."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
hermes_eval = importlib.import_module("hermes_eval")


def test_extract_tool_calls_normalizes_json_arguments():
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "function": {
                        "name": "kanban_block",
                        "arguments": '{"reason":"missing token","kind":"needs_input"}',
                    }
                }
            ],
        }
    ]

    assert hermes_eval._extract_tool_calls(messages) == [
        {
            "tool": "kanban_block",
            "arguments": {"reason": "missing token", "kind": "needs_input"},
        }
    ]


def test_tool_call_matches_checks_named_call_arguments():
    res = hermes_eval.CaseResult(
        id="typed",
        category="kanban",
        ok=True,
        latency=0.1,
        response="",
        tool_calls=["kanban_block"],
        tool_call_details=[
            {
                "tool": "kanban_block",
                "arguments": {"reason": "missing token", "kind": "needs_input"},
            }
        ],
    )

    ok, detail = hermes_eval.assert_one(
        {
            "type": "tool_call_matches",
            "tool": "kanban_block",
            "arg_regex": r'"kind"\s*:\s*"needs_input"',
        },
        res,
    )

    assert ok, detail


def test_tool_call_matches_rejects_wrong_kind():
    res = hermes_eval.CaseResult(
        id="typed",
        category="kanban",
        ok=True,
        latency=0.1,
        response="",
        tool_calls=["kanban_block"],
        tool_call_details=[
            {
                "tool": "kanban_block",
                "arguments": {"reason": "network failed", "kind": "transient"},
            }
        ],
    )

    ok, detail = hermes_eval.assert_one(
        {
            "type": "tool_call_matches",
            "tool": "kanban_block",
            "arg_regex": r'"kind"\s*:\s*"needs_input"',
        },
        res,
    )

    assert not ok
    assert "no arguments matched" in detail


def test_prepare_kanban_fixtures_creates_isolated_task_and_workspace(tmp_path):
    cases = [
        {
            "id": "fixture-case",
            "prompt": "task={kanban_task_id} workspace={workspace}",
            "kanban_fixture": {
                "files": {"README.md": "fixture contents\n"},
            },
        }
    ]

    prepared = hermes_eval._prepare_kanban_fixtures(cases, tmp_path)
    case = prepared[0]

    assert case["_env"]["HERMES_KANBAN_TASK"].startswith("t_")
    assert case["_env"]["HERMES_KANBAN_DB"] == str(tmp_path / "kanban.db")
    assert Path(case["_env"]["HERMES_KANBAN_WORKSPACE"], "README.md").read_text() == "fixture contents\n"
    assert "{kanban_task_id}" not in case["prompt"]
    assert "{workspace}" not in case["prompt"]

    with hermes_eval._temporary_env(case["_env"]):
        from hermes_cli import kanban_db as kb

        conn = kb.connect(tmp_path / "kanban.db")
        try:
            task = kb.get_task(conn, case["_env"]["HERMES_KANBAN_TASK"])
        finally:
            conn.close()

    assert task is not None
    assert task.status == "running"
    assert task.current_run_id is not None
    assert case["_env"]["HERMES_KANBAN_RUN_ID"] == str(task.current_run_id)
    assert case["_env"]["HERMES_KANBAN_CLAIM_LOCK"] == task.claim_lock