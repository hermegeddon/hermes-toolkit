"""Unit tests for the library-backend deployed-config loading (no network).

Covers the fix for t_433e7a95: the `library` backend must build AIAgent from the
DEPLOYED config (model/provider/base_url/api_key/toolsets) so eval mirrors prod,
while keeping explicit --model / suite defaults.model / --bare as overrides.

Run:  python -m pytest tests/test_deployed_config.py -q
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

# Import the harness module that sits one dir up (scripts/hermes_eval.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
hermes_eval = importlib.import_module("hermes_eval")


# --------------------------------------------------------------------------- #
# _resolve_deployed_config: normalization of the deployed config shape
# --------------------------------------------------------------------------- #
def _patch_loader(monkeypatch, cfg: dict):
    """Make _resolve_deployed_config see `cfg` as the loaded project config."""
    import hermes_cli.config as hc  # provided by the agent install / PYTHONPATH
    monkeypatch.setattr(hc, "load_config", lambda: cfg, raising=True)
    hermes_eval._DEPLOYED_CFG_CACHE.clear()


def test_model_mapping_is_normalized(monkeypatch):
    _patch_loader(monkeypatch, {
        "model": {
            "default": "apex-fast:latest",
            "base_url": "http://ollama.example:11434/v1",
            "provider": "custom",
            "api_key": "none",          # literal "none" must be dropped
        },
        "toolsets": ["hermes-cli", "mcp-ocr"],
        "agent": {"disabled_toolsets": ["web", "terminal"]},
    })
    out = hermes_eval._resolve_deployed_config()
    assert out["model"] == "apex-fast:latest"
    assert out["base_url"] == "http://ollama.example:11434/v1"
    assert out["provider"] == "custom"
    assert "api_key" not in out                       # "none" -> no key
    assert out["toolsets"] == ["hermes-cli", "mcp-ocr"]
    assert out["disabled_toolsets"] == ["web", "terminal"]


def test_model_plain_string(monkeypatch):
    _patch_loader(monkeypatch, {"model": "openrouter/some-model"})
    out = hermes_eval._resolve_deployed_config()
    assert out["model"] == "openrouter/some-model"


def test_real_api_key_is_kept(monkeypatch):
    _patch_loader(monkeypatch, {"model": {"default": "m", "api_key": "sk-abc123"}})
    out = hermes_eval._resolve_deployed_config()
    assert out["api_key"] == "sk-abc123"


def test_loader_unavailable_returns_empty(monkeypatch):
    import hermes_cli.config as hc

    def _boom():
        raise RuntimeError("no config")

    monkeypatch.setattr(hc, "load_config", _boom, raising=True)
    hermes_eval._DEPLOYED_CFG_CACHE.clear()
    assert hermes_eval._resolve_deployed_config() == {}


# --------------------------------------------------------------------------- #
# run_library precedence: deployed vs --model vs suite/case vs --bare.
# We stub AIAgent so nothing hits the network; we only assert the kwargs.
# --------------------------------------------------------------------------- #
class _StubAgent:
    last_kwargs: dict = {}

    def __init__(self, **kwargs):
        _StubAgent.last_kwargs = kwargs

    def run_conversation(self, user_message=""):
        return {"final_response": "ok", "messages": []}


@pytest.fixture
def stub_agent(monkeypatch):
    # run_library does `from run_agent import AIAgent`; patch that symbol.
    import run_agent
    monkeypatch.setattr(run_agent, "AIAgent", _StubAgent, raising=True)
    # Fixed deployed config for all precedence cases.
    hermes_eval._DEPLOYED_CFG_CACHE["v"] = {
        "model": "apex-fast:latest",
        "base_url": "http://ollama.example:11434/v1",
        "provider": "custom",
        "toolsets": ["hermes-cli", "mcp-ocr"],
    }
    yield
    hermes_eval._DEPLOYED_CFG_CACHE.clear()


PLACEHOLDER = "anthropic/claude-sonnet-4.6"


def _runtime(**over):
    base = {
        "model": PLACEHOLDER,
        "model_is_default": True,
        "_runtime_model_default": PLACEHOLDER,
        "use_deployed_config": True,
        "max_iterations": 4,
    }
    base.update(over)
    return base


def test_bare_run_adopts_deployed_model(stub_agent):
    hermes_eval.run_library("hi", _runtime())
    k = _StubAgent.last_kwargs
    assert k["model"] == "apex-fast:latest"
    assert k["base_url"] == "http://ollama.example:11434/v1"
    assert k["provider"] == "custom"
    assert k["enabled_toolsets"] == ["hermes-cli", "mcp-ocr"]
    # speed knobs preserved regardless of deployed config
    assert k["quiet_mode"] and k["skip_memory"] and k["skip_context_files"]


def test_explicit_model_wins(stub_agent):
    hermes_eval.run_library("hi", _runtime(model="qwen3:8b", model_is_default=False))
    assert _StubAgent.last_kwargs["model"] == "qwen3:8b"
    # but endpoint/provider still come from deployed config (no explicit override)
    assert _StubAgent.last_kwargs["base_url"] == "http://ollama.example:11434/v1"


def test_suite_defaults_model_wins(stub_agent):
    # runtime placeholder unchanged; a suite/case model arrives merged over runtime.
    cfg = _runtime()
    cfg["model"] = "qwen3:8b"  # i.e. {**runtime, **case} with case={"model": ...}
    hermes_eval.run_library("hi", cfg)
    assert _StubAgent.last_kwargs["model"] == "qwen3:8b"


def test_bare_flag_disables_deployed(stub_agent):
    hermes_eval.run_library("hi", _runtime(use_deployed_config=False))
    k = _StubAgent.last_kwargs
    assert k["model"] == PLACEHOLDER                  # old behavior
    assert "base_url" not in k                         # nothing pulled from deployed
    assert "provider" not in k


def test_per_case_toolsets_override_deployed(stub_agent):
    cfg = _runtime()
    cfg["toolsets"] = ["web"]                           # per-case toolsets
    hermes_eval.run_library("hi", cfg)
    assert _StubAgent.last_kwargs["enabled_toolsets"] == ["web"]
