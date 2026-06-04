#!/usr/bin/env python3
"""
hermes_eval.py — a fast, parallel QA harness for Hermes Agent.

WHY THIS EXISTS
---------------
The slow way to QA a Hermes instance is to shell out to the CLI once per check:

    time timeout 90 HERMES_HOME=... hermes chat -Q -q "say hello"

Every invocation pays full process startup + config/memory/context-file loading,
runs serially, and gives you nothing structured back. A dozen checks take minutes.

This harness instead drives Hermes through its documented programmatic surfaces,
runs the whole suite concurrently, and emits a structured report you can diff
against a baseline.

THREE BACKENDS
--------------
  library  (default)  In-process `from run_agent import AIAgent`. Fastest. Gives
                      you the full message trajectory, so tool-call assertions work.
                      Uses skip_memory / skip_context_files / low max_iterations to
                      strip per-call overhead.
  api                 POST to an OpenAI-compatible endpoint (the gateway's
                      api_server adapter, or `hermes serve`). This exercises the
                      REAL gateway path end-to-end. NOTE: the OpenAI-compatible
                      response usually returns only final text, so intermediate
                      tool-call assertions can't be verified here (see _extract).
  cli                 Shells out to `hermes chat` (the slow baseline). Included so
                      you can A/B the old path against the new one. Don't use it
                      for routine QA.

USAGE
-----
  pip install git+https://github.com/NousResearch/hermes-agent.git   # for library mode
  python hermes_eval.py --suite suites/smoke.yaml --backend library --workers 6
  python hermes_eval.py --suite suites/smoke.yaml --backend api \
         --base-url http://localhost:8080/v1 --model anthropic/claude-sonnet-4.6
  python hermes_eval.py --suite suites/*.yaml --baseline last_report.json --md report.md

See the companion SKILL.md for the full suite schema and assertion reference.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import glob
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable

try:
    import yaml  # PyYAML; suites are YAML
except ImportError:  # pragma: no cover
    print("PyYAML required: pip install pyyaml", file=sys.stderr)
    raise


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class CaseResult:
    id: str
    category: str
    ok: bool
    latency: float
    response: str
    tool_calls: list[str] = field(default_factory=list)
    llm_calls: int = 0  # assistant turns in the trajectory (anti delegation-explosion metric)
    assertions: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


# --------------------------------------------------------------------------- #
# Backends — each returns (response_text, tool_calls, error)
# --------------------------------------------------------------------------- #
def _extract_tool_names(messages: list[dict[str, Any]]) -> list[str]:
    """Best-effort extraction of tool names from a run_conversation trajectory.

    Hermes stores messages roughly in OpenAI shape, but the exact keys can drift
    between versions. We look in the common places. If your `tool_called`
    assertions are mysteriously failing, print one `result["messages"]` and adjust
    the lookups below to match your build — this is the one schema-coupled spot.
    """
    names: list[str] = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        # 1) assistant message with OpenAI-style tool_calls
        for tc in m.get("tool_calls") or []:
            fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
            if fn.get("name"):
                names.append(fn["name"])
        # 2) role == "tool"/"function" carrying the tool name
        if m.get("role") in ("tool", "function") and m.get("name"):
            names.append(m["name"])
        # 3) some builds tag the invoked tool directly
        if m.get("tool_name"):
            names.append(m["tool_name"])
    return names


def _count_llm_calls(messages: list[dict[str, Any]]) -> int:
    """Count model invocations in a run_conversation trajectory.

    Why: the core CT-133 failure mode is "delegation explosion" — a simple ops
    query ("is X running") routed through delegate_task spends 6-17 model calls
    over 90-150s and sometimes wanders/timeouts, where the correct answer is ONE
    direct cluster-ops tool call. Counting assistant turns gives the harness a
    cheap proxy for that spend so `max_llm_calls` can fail noisy routing.
    What: returns the number of `role == "assistant"` messages in the trajectory
    (each model turn appends exactly one assistant message, with or without
    tool_calls attached).
    Test: feed [{"role":"user"},{"role":"assistant","tool_calls":[...]},
    {"role":"tool"},{"role":"assistant"}] -> expect 2.
    """
    return sum(1 for m in (messages or []) if isinstance(m, dict) and m.get("role") == "assistant")


def run_library(prompt: str, cfg: dict[str, Any]) -> tuple[str, list[str], int, str | None]:
    from run_agent import AIAgent  # imported lazily so api/cli modes need no install

    kwargs: dict[str, Any] = dict(
        model=cfg["model"],
        quiet_mode=True,        # never print spinners when embedded
        skip_memory=True,       # stateless QA — don't read/write MEMORY.md
        skip_context_files=True,  # don't pull AGENTS.md/.hermes.md into the prompt
        max_iterations=cfg.get("max_iterations", 6),  # cap runaway tool loops
    )
    if cfg.get("toolsets"):
        kwargs["enabled_toolsets"] = cfg["toolsets"]
    if cfg.get("disable_toolsets"):
        kwargs["disabled_toolsets"] = cfg["disable_toolsets"]
    if cfg.get("base_url"):
        kwargs["base_url"] = cfg["base_url"]

    try:
        agent = AIAgent(**kwargs)
        result = agent.run_conversation(user_message=prompt)
        text = result.get("final_response", "") or ""
        msgs = result.get("messages", [])
        tools = _extract_tool_names(msgs)
        return text, tools, _count_llm_calls(msgs), None
    except Exception as exc:  # noqa: BLE001 — surface any failure as a test error
        return "", [], 0, f"{type(exc).__name__}: {exc}"


def run_api(prompt: str, cfg: dict[str, Any]) -> tuple[str, list[str], int, str | None]:
    import urllib.request

    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    body = json.dumps(
        {"model": cfg["model"], "messages": [{"role": "user", "content": prompt}]}
    ).encode()
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=cfg.get("timeout", 120)) as resp:
            data = json.loads(resp.read().decode())
        text = data["choices"][0]["message"]["content"] or ""
        # OpenAI-compatible final response: no reliable intermediate tool list,
        # and no trajectory, so llm_calls is unknown (0). max_llm_calls /
        # not_tool_called are library-backend assertions — see SKILL.md.
        return text, [], 0, None
    except Exception as exc:  # noqa: BLE001
        return "", [], 0, f"{type(exc).__name__}: {exc}"


def run_cli(prompt: str, cfg: dict[str, Any]) -> tuple[str, list[str], int, str | None]:
    """The slow baseline. Here for comparison only."""
    binary = cfg.get("hermes_bin", "hermes")
    cmd = [binary, "chat", "-Q", "-q", prompt]
    if cfg.get("toolsets"):
        cmd += ["--toolsets", ",".join(cfg["toolsets"])]
    env = dict(os.environ)
    if cfg.get("hermes_home"):
        env["HERMES_HOME"] = cfg["hermes_home"]
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=cfg.get("timeout", 120), env=env
        )
        if out.returncode != 0:
            return out.stdout, [], 0, f"exit {out.returncode}: {out.stderr.strip()[:300]}"
        return out.stdout.strip(), [], 0, None
    except subprocess.TimeoutExpired:
        return "", [], 0, "timeout"
    except Exception as exc:  # noqa: BLE001
        return "", [], 0, f"{type(exc).__name__}: {exc}"


BACKENDS: dict[str, Callable[..., tuple[str, list[str], int, str | None]]] = {
    "library": run_library,
    "api": run_api,
    "cli": run_cli,
}


# --------------------------------------------------------------------------- #
# Assertions — each returns (ok, detail)
# --------------------------------------------------------------------------- #
def _ci(s: str) -> str:
    return s.lower()


def assert_one(a: dict[str, Any], res: CaseResult) -> tuple[bool, str]:
    t = a.get("type")
    text = res.response or ""
    ci = a.get("ignore_case", True)
    hay = _ci(text) if ci else text

    if t == "nonempty":
        return bool(text.strip()), "response empty" if not text.strip() else "ok"

    if t == "contains":
        v = a["value"]
        needle = _ci(v) if ci else v
        return needle in hay, f"missing {v!r}"

    if t == "contains_any":
        vals = a["values"]
        hit = next((v for v in vals if (_ci(v) if ci else v) in hay), None)
        return hit is not None, f"none of {vals} present"

    if t == "contains_all":
        vals = a["values"]
        missing = [v for v in vals if (_ci(v) if ci else v) not in hay]
        return not missing, f"missing {missing}"

    if t == "not_contains":
        v = a["value"]
        needle = _ci(v) if ci else v
        return needle not in hay, f"unexpectedly contains {v!r}"

    if t == "regex":
        flags = re.IGNORECASE if ci else 0
        return bool(re.search(a["pattern"], text, flags)), f"no match for /{a['pattern']}/"

    if t == "tool_called":
        want = a["tool"]
        return want in res.tool_calls, f"tool {want!r} not in {res.tool_calls or '[]'}"

    if t == "not_tool_called":
        # Fail if the named tool WAS called. The headline use: assert a simple ops
        # query did NOT reach for delegate_task (it should be one direct call).
        # Library backend only — api/cli expose no trajectory. (See SKILL.md.)
        deny = a["tool"]
        return deny not in res.tool_calls, f"tool {deny!r} was called (got {res.tool_calls or '[]'})"

    if t == "max_tool_calls":
        # Cap TOTAL tool invocations. Catches tool-loop thrash distinct from
        # model-turn count. Library backend only.
        n = int(a["n"])
        return len(res.tool_calls) <= n, f"{len(res.tool_calls)} tool calls > {n}"

    if t == "max_llm_calls":
        # Anti delegation-explosion / anti-wander: fail if the case spent more than
        # N model turns. A deterministic single-command op (service status, disk,
        # logs) should resolve in 1-2 assistant turns; a 6-17 turn run means it got
        # routed through delegate_task and wandered. Library backend only — the api
        # and cli backends return no trajectory, so llm_calls is 0 there and this
        # assertion would trivially pass (a false green); keep it on `library`.
        n = int(a["n"])
        return res.llm_calls <= n, f"{res.llm_calls} llm calls > {n}"

    if t == "no_error":
        return res.error is None, f"error: {res.error}"

    if t == "latency_under":
        return res.latency <= a["seconds"], f"{res.latency:.1f}s > {a['seconds']}s"

    if t == "judge":
        return _judge(a, res)

    return False, f"unknown assertion type: {t!r}"


# --- LLM-as-judge (lazy; only used if a suite has a `judge` assertion) -------- #
_JUDGE_CACHE: dict[str, Any] = {}


def _judge(a: dict[str, Any], res: CaseResult) -> tuple[bool, str]:
    rubric = a["rubric"]
    threshold = float(a.get("threshold", 0.7))
    cfg = _JUDGE_CACHE["cfg"]
    prompt = (
        "You are grading an AI agent's response. Score 0.0-1.0 for how well it "
        "satisfies the rubric. Reply with ONLY compact JSON: "
        '{"score": <float>, "reason": "<short>"}.\n\n'
        f"RUBRIC: {rubric}\n\nRESPONSE:\n{res.response}\n"
    )
    try:
        if cfg["backend"] == "api":
            raw, _, _, err = run_api(prompt, {**cfg, "model": cfg.get("judge_model", cfg["model"])})
        else:
            from run_agent import AIAgent

            judge = _JUDGE_CACHE.get("agent")
            if judge is None:
                judge = AIAgent(
                    model=cfg.get("judge_model", cfg["model"]),
                    quiet_mode=True,
                    skip_memory=True,
                    skip_context_files=True,
                    disabled_toolsets=["terminal", "browser", "web"],
                    max_iterations=1,
                )
                _JUDGE_CACHE["agent"] = judge
            raw = judge.chat(prompt)
            err = None
        if err:
            return False, f"judge error: {err}"
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        score = float(json.loads(m.group(0))["score"]) if m else 0.0
        return score >= threshold, f"judge score {score:.2f} (>= {threshold} required)"
    except Exception as exc:  # noqa: BLE001
        return False, f"judge crashed: {type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------- #
# Suite loading + execution
# --------------------------------------------------------------------------- #
def load_suite(path: str) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    with open(path) as fh:
        doc = yaml.safe_load(fh)
    name = doc.get("suite", os.path.basename(path))
    defaults = doc.get("defaults", {})
    cases = []
    for raw in doc.get("cases", []):
        case = {**defaults, **raw}  # per-case overrides suite defaults
        cases.append(case)
    return name, cases, defaults


def run_case(case: dict[str, Any], runtime: dict[str, Any]) -> CaseResult:
    cfg = {**runtime, **case}
    backend = BACKENDS[runtime["backend"]]
    t0 = time.perf_counter()
    text, tools, llm_calls, err = backend(case["prompt"], cfg)
    latency = time.perf_counter() - t0

    res = CaseResult(
        id=case.get("id", "unnamed"),
        category=case.get("category", "uncategorized"),
        ok=True,
        latency=latency,
        response=text,
        tool_calls=tools,
        llm_calls=llm_calls,
        error=err,
    )
    checks = case.get("assert") or [{"type": "no_error"}, {"type": "nonempty"}]
    for a in checks:
        ok, detail = assert_one(a, res)
        res.assertions.append({"type": a.get("type"), "ok": ok, "detail": detail})
        if not ok:
            res.ok = False
    return res


def run_all(cases: list[dict[str, Any]], runtime: dict[str, Any]) -> list[CaseResult]:
    results: list[CaseResult] = []
    workers = runtime["workers"]
    timeout = runtime.get("timeout", 120)
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(run_case, c, runtime): c for c in cases}
        for fut in cf.as_completed(futs):
            c = futs[fut]
            try:
                # +30s slack over the per-request timeout so we capture the
                # backend's own timeout result rather than masking it.
                results.append(fut.result(timeout=timeout + 30))
            except cf.TimeoutError:
                results.append(
                    CaseResult(
                        id=c.get("id", "unnamed"),
                        category=c.get("category", "uncategorized"),
                        ok=False,
                        latency=float(timeout),
                        response="",
                        error="harness timeout (thread still running in background)",
                        assertions=[{"type": "no_error", "ok": False, "detail": "timeout"}],
                    )
                )
    results.sort(key=lambda r: (r.category, r.id))
    return results


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def summarize(results: list[CaseResult]) -> dict[str, Any]:
    by_cat: dict[str, dict[str, int]] = {}
    for r in results:
        c = by_cat.setdefault(r.category, {"pass": 0, "total": 0})
        c["total"] += 1
        c["pass"] += int(r.ok)
    lats = sorted(r.latency for r in results)

    def pct(p: float) -> float:
        if not lats:
            return 0.0
        return round(lats[min(len(lats) - 1, int(p * len(lats)))], 2)

    passed = sum(r.ok for r in results)
    return {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": round(passed / len(results), 3) if results else 0.0,
        "latency_p50": pct(0.50),
        "latency_p95": pct(0.95),
        "latency_max": round(max(lats), 2) if lats else 0.0,
        "by_category": {k: {**v, "rate": round(v["pass"] / v["total"], 3)} for k, v in by_cat.items()},
    }


def diff_baseline(cur: list[CaseResult], baseline_path: str) -> dict[str, Any]:
    with open(baseline_path) as fh:
        base = {r["id"]: r for r in json.load(fh)["results"]}
    regressions, fixes, slower = [], [], []
    for r in cur:
        b = base.get(r.id)
        if not b:
            continue
        if b["ok"] and not r.ok:
            regressions.append(r.id)
        if not b["ok"] and r.ok:
            fixes.append(r.id)
        if r.latency > b["latency"] * 1.5 and r.latency - b["latency"] > 3:
            slower.append({"id": r.id, "was": round(b["latency"], 1), "now": round(r.latency, 1)})
    return {"regressions": regressions, "fixes": fixes, "slower": slower}


def print_console(name: str, summary: dict[str, Any], results: list[CaseResult], delta: dict | None):
    print(f"\n=== suite: {name} ===")
    print(
        f"{summary['passed']}/{summary['total']} passed "
        f"({summary['pass_rate']*100:.0f}%)  "
        f"p50={summary['latency_p50']}s  p95={summary['latency_p95']}s  max={summary['latency_max']}s"
    )
    for cat, v in sorted(summary["by_category"].items()):
        print(f"  {cat:<14} {v['pass']}/{v['total']}  ({v['rate']*100:.0f}%)")
    fails = [r for r in results if not r.ok]
    if fails:
        print("\n  FAILURES:")
        for r in fails:
            why = "; ".join(f"{a['type']}: {a['detail']}" for a in r.assertions if not a["ok"])
            calls = f", {r.llm_calls} llm calls" if r.llm_calls else ""
            print(f"    ✗ [{r.category}] {r.id} ({r.latency:.1f}s{calls}) — {why}")
    if delta:
        if delta["regressions"]:
            print(f"\n  ⚠ REGRESSIONS vs baseline: {', '.join(delta['regressions'])}")
        if delta["fixes"]:
            print(f"  ✓ fixed vs baseline: {', '.join(delta['fixes'])}")
        if delta["slower"]:
            for s in delta["slower"]:
                print(f"  🐢 slower: {s['id']} {s['was']}s → {s['now']}s")
    print()


def write_markdown(path: str, name: str, summary: dict, results: list[CaseResult], delta: dict | None):
    lines = [f"# Hermes QA report — {name}", ""]
    lines.append(
        f"**{summary['passed']}/{summary['total']} passed "
        f"({summary['pass_rate']*100:.0f}%)** · p50 {summary['latency_p50']}s · "
        f"p95 {summary['latency_p95']}s · max {summary['latency_max']}s"
    )
    lines += ["", "| category | pass | rate |", "|---|---|---|"]
    for cat, v in sorted(summary["by_category"].items()):
        lines.append(f"| {cat} | {v['pass']}/{v['total']} | {v['rate']*100:.0f}% |")
    fails = [r for r in results if not r.ok]
    if fails:
        lines += ["", "## Failures", ""]
        for r in fails:
            why = "; ".join(f"`{a['type']}`: {a['detail']}" for a in r.assertions if not a["ok"])
            lines.append(f"- **{r.id}** _({r.category}, {r.latency:.1f}s)_ — {why}")
    if delta and (delta["regressions"] or delta["slower"]):
        lines += ["", "## Regressions vs baseline", ""]
        for rid in delta["regressions"]:
            lines.append(f"- ⚠ `{rid}` newly failing")
        for s in delta["slower"]:
            lines.append(f"- 🐢 `{s['id']}` {s['was']}s → {s['now']}s")
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> int:
    p = argparse.ArgumentParser(description="Fast parallel QA harness for Hermes Agent")
    p.add_argument("--suite", nargs="+", required=True, help="One or more suite YAML files (globs OK)")
    p.add_argument("--backend", choices=list(BACKENDS), default="library")
    p.add_argument("--model", default=os.environ.get("HERMES_QA_MODEL", "anthropic/claude-sonnet-4.6"))
    p.add_argument("--judge-model", default=None, help="Model for `judge` assertions (defaults to --model)")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--timeout", type=int, default=120, help="Per-request timeout (s)")
    p.add_argument("--base-url", default=os.environ.get("HERMES_QA_BASE_URL"), help="For api backend, e.g. http://localhost:8080/v1")
    p.add_argument("--api-key", default=os.environ.get("HERMES_QA_API_KEY"))
    p.add_argument("--hermes-bin", default="hermes", help="For cli backend")
    p.add_argument("--hermes-home", default=os.environ.get("HERMES_HOME"), help="For cli backend")
    p.add_argument("--baseline", default=None, help="Previous report.json to diff against")
    p.add_argument("--out", default=None, help="Write full JSON report here")
    p.add_argument("--md", default=None, help="Write a Markdown summary here")
    args = p.parse_args()

    if args.backend == "api" and not args.base_url:
        p.error("--backend api requires --base-url")

    runtime = {
        "backend": args.backend,
        "model": args.model,
        "judge_model": args.judge_model or args.model,
        "workers": args.workers,
        "timeout": args.timeout,
        "base_url": args.base_url,
        "api_key": args.api_key,
        "hermes_bin": args.hermes_bin,
        "hermes_home": args.hermes_home,
    }
    _JUDGE_CACHE["cfg"] = runtime

    paths: list[str] = []
    for pat in args.suite:
        paths.extend(sorted(glob.glob(pat)) or [pat])

    all_results: list[CaseResult] = []
    overall_name = ", ".join(os.path.basename(p) for p in paths)
    wall0 = time.perf_counter()
    for path in paths:
        name, cases, _ = load_suite(path)
        results = run_all(cases, runtime)
        summary = summarize(results)
        delta = diff_baseline(results, args.baseline) if args.baseline else None
        print_console(name, summary, results, delta)
        all_results.extend(results)
    wall = time.perf_counter() - wall0

    summary = summarize(all_results)
    summary["wall_seconds"] = round(wall, 1)
    print(f"TOTAL: {summary['passed']}/{summary['total']} passed in {wall:.1f}s wall "
          f"({args.workers} workers, {args.backend} backend)")

    report = {"summary": summary, "results": [asdict(r) for r in all_results]}
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(report, fh, indent=2)
        print(f"wrote {args.out}")
    if args.md:
        write_markdown(args.md, overall_name, summary, all_results, None)
        print(f"wrote {args.md}")

    # Non-zero exit if anything failed — handy in CI / pre-deploy gates.
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
