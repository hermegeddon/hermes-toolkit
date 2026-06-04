#!/usr/bin/env python3
"""Embedding-enhanced deterministic-intent miner — DISCOVERY tool (propose-only).

WHY
---
Hermes already bypasses a handful of fully-deterministic intents (time/date,
disk-free, service-status, weather) in pure Python via the
``intent-handlers-core`` plugin. Every new deterministic intent we bypass is an
LLM call (and its latency/cost) we never have to make again. But hand-picking
those intents doesn't scale: we want the *data* to tell us which user questions
are (a) frequent, (b) cheap-looking to the LLM today, and (c) low tool-call
diversity — i.e. good candidates for a deterministic handler.

This tool MINES that signal from the real conversation history in
``state.db``. It is pure Python: it EMBEDS each distinct user prompt via a local
OpenAI-compatible embeddings endpoint, CLUSTERS by cosine similarity, SCORES each
cluster, EXCLUDES intents already covered by the existing plugin, GATES on a
minimum cluster size, and emits a ranked JSON + Markdown report.

It is **propose-only**. It generates NO handler code and ships nothing. It is the
"find" half of a propose-only flywheel; a separate agent-mode cron reviewer judges
the candidates against a strict determinism checklist and (only on accept) files a
kanban task with a DRAFT handler sketch. Nothing is ever auto-registered.

WHAT
----
- Reads ``state.db`` READ-ONLY (``mode=ro``).
- Embeds distinct user prompts (consistent ``search_document:`` prefix for the
  nomic-embed-text-v2-moe model), with an on-disk embedding cache so re-runs are
  cheap. Endpoint and dimension are auto-detected; bounded timeouts throughout.
- Greedy threshold clustering on cosine similarity (deterministic; CLI-tunable
  threshold, default 0.83).
- Scores each cluster on: frequency (size), avg api_call_count (the cost proxy —
  latency is NOT used, it is corrupt), and a determinism signal (low tool-call
  diversity / single dominant tool / absence of delegate_task).
- Excludes clusters whose representative matches an already-covered intent
  (weather / time / disk / service-status), matched against keyword triggers
  mirroring the existing plugin.
- Gates on ``--min-cluster`` (default 5 for seeding ~270 prompts; >=10 is the real
  bar at scale — documented).
- Prints ranked JSON then a Markdown report to stdout (so a no_agent cron job
  captures it verbatim).

TEST
----
``python deterministic_miner.py --db <state.db> --min-cluster 5`` prints a JSON
object with a ``candidates`` list and a Markdown report. With ``--self-test`` it
runs offline unit checks on the exclusion matcher and clustering and exits 0.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------- #
# Defaults
# --------------------------------------------------------------------------- #

DEFAULT_DB = "/opt/hermes/home/.hermes/state.db"
DEFAULT_ENDPOINT = "http://192.168.1.36:11434/v1/embeddings"
DEFAULT_MODEL = "nomic-embed-text-v2-moe"
DEFAULT_PREFIX = "search_document: "  # nomic-embed-text-v2 recall prefix
DEFAULT_CACHE = os.path.expanduser("~/.hermes/cache/deterministic_miner_embeddings.json")
DEFAULT_THRESHOLD = 0.83  # cosine; tuned band 0.80-0.85
DEFAULT_MIN_CLUSTER = 5   # seeding bar; >=10 is the real bar at scale
HTTP_TIMEOUT_S = 20.0
BATCH = 32

# Cap prompt length before embedding — keeps long pasted blobs from dominating a
# cluster and bounds the request size.
MAX_PROMPT_CHARS = 600

# System/harness text that lands in messages.role='user' but is NOT a real user
# intent (agent self-prompts, eval probes, injected continuation messages). These
# are noise — a deterministic handler for them would be meaningless. Drop them at
# load time so they never reach clustering. Matched case-insensitively as a
# substring against the normalized prompt.
NOISE_SUBSTRINGS = (
    "you've reached the maximum number of tool-calling iterations",
    "you have reached the maximum number of tool-calling iterations",
    "please provide a final response summarizing what you've found",
    "reply with the single word",
    "reply with exactly",
    "reply with:",
    "say the word",
    "smoke_ok",
    "profile_ok",
)


def is_noise(text: str) -> bool:
    """True if ``text`` is harness/self-prompt noise, not a real user intent.

    Why: messages.role='user' includes agent continuation prompts and eval
    probes; mining those produces meaningless 'candidates'.
    Test: is_noise("Reply with the single word: pong") is True;
    is_noise("how much disk is free") is False.
    """
    s = (text or "").strip().lower()
    if not s:
        return True
    return any(sub in s for sub in NOISE_SUBSTRINGS)

# --------------------------------------------------------------------------- #
# Already-covered intents (mirror intent-handlers-core + weather plugin triggers)
# A cluster representative matching any of these is excluded — we never re-propose
# something already bypassed. Keyword-gated, lowercase, substring/regex match.
# --------------------------------------------------------------------------- #

COVERED_PATTERNS: Dict[str, List[str]] = {
    "time/date": [
        r"\bwhat time is it\b", r"\bwhat'?s the time\b", r"\bcurrent time\b",
        r"\btoday'?s date\b", r"\bwhat date is it\b", r"\bwhat day is it\b",
        r"\bwhat day of the week\b", r"\bwhat'?s today'?s date\b",
    ],
    "disk": [
        r"\bdisk space\b", r"\bdisk usage\b", r"\bdisk free\b", r"\bfree disk\b",
        r"\bhow much disk\b", r"\bdisk is (?:free|left)\b",
    ],
    "service-status": [
        r"\bis .{0,30}(?:running|up|active)\b", r"\bstatus of\b",
        r"\bis the (?:hermes )?gateway\b", r"\bgateway running\b",
    ],
    "weather": [
        r"\bweather\b", r"\bforecast\b", r"\btemperature\b", r"\bis it raining\b",
        r"\bhow (?:hot|cold) is it\b", r"\bweather alert\b",
    ],
}

_COVERED_COMPILED = {
    name: [re.compile(p, re.IGNORECASE) for p in pats]
    for name, pats in COVERED_PATTERNS.items()
}


def covered_intent(text: str) -> Optional[str]:
    """Return the name of an already-covered intent matching ``text``, else None.

    Why: never re-propose something the plugin already bypasses.
    What: regex-match the (lowercased) text against the covered trigger sets.
    Test: covered_intent("what time is it") == "time/date"; covered_intent("can
    you summarize this log") is None.
    """
    s = (text or "").strip().lower()
    if not s:
        return None
    for name, pats in _COVERED_COMPILED.items():
        for pat in pats:
            if pat.search(s):
                return name
    return None


# --------------------------------------------------------------------------- #
# DB read (READ-ONLY)
# --------------------------------------------------------------------------- #

def _norm_prompt(text: str) -> str:
    """Collapse whitespace; trim; lower for dedup keying (display keeps original)."""
    return re.sub(r"\s+", " ", (text or "").strip())


def load_prompts(db_path: str) -> List[dict]:
    """Load distinct user prompts with per-turn cost/tool signal from state.db.

    Why: the miner needs, per distinct user prompt, how often it appears, the
    avg api_call_count of the sessions it appears in (cost proxy), and the tools
    used in that turn (determinism signal).
    What: opens state.db with mode=ro, pulls role='user' messages, joins sessions
    on id for api_call_count/source, and collects the tool_calls/tool_name that
    follow each user turn within the same session.
    Test: returns a list of dicts each with keys text, count, api_calls (list),
    tools (Counter), sources.
    """
    uri = f"file:{os.path.abspath(db_path)}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # All messages ordered, so we can attribute tool calls to the preceding user
    # turn within a session.
    rows = cur.execute(
        """
        SELECT m.id, m.session_id, m.role, m.content, m.tool_calls, m.tool_name,
               s.api_call_count AS api_call_count, s.source AS source,
               s.estimated_cost_usd AS cost
        FROM messages m
        LEFT JOIN sessions s ON m.session_id = s.id
        ORDER BY m.session_id, m.id
        """
    ).fetchall()
    con.close()

    # Walk per session; for each user turn, gather tool names until the next user
    # turn.
    agg: Dict[str, dict] = {}
    cur_key: Optional[str] = None
    cur_session: Optional[str] = None

    def _tools_from_row(r) -> List[str]:
        names: List[str] = []
        if r["tool_name"]:
            names.append(str(r["tool_name"]))
        raw = r["tool_calls"]
        if raw:
            try:
                data = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(data, list):
                    for tc in data:
                        if isinstance(tc, dict):
                            fn = (tc.get("function") or {}).get("name") or tc.get("name")
                            if fn:
                                names.append(str(fn))
            except Exception:
                pass
        return names

    for r in rows:
        if r["session_id"] != cur_session:
            cur_session = r["session_id"]
            cur_key = None
        if r["role"] == "user" and r["content"]:
            key = _norm_prompt(r["content"]).lower()
            if not key or is_noise(r["content"]):
                cur_key = None
                continue
            cur_key = key
            entry = agg.setdefault(
                key,
                {
                    "text": _norm_prompt(r["content"])[:MAX_PROMPT_CHARS],
                    "count": 0,
                    "api_calls": [],
                    "tools": Counter(),
                    "sources": Counter(),
                },
            )
            entry["count"] += 1
            if r["api_call_count"] is not None:
                entry["api_calls"].append(int(r["api_call_count"]))
            if r["source"]:
                entry["sources"][str(r["source"])] += 1
        elif cur_key is not None:
            # tool/assistant turn belonging to the current user turn
            for t in _tools_from_row(r):
                agg[cur_key]["tools"][t] += 1

    return list(agg.values())


# --------------------------------------------------------------------------- #
# Embeddings (with on-disk cache, bounded timeouts)
# --------------------------------------------------------------------------- #

def _cache_load(path: str) -> Dict[str, List[float]]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _cache_save(path: str, cache: Dict[str, List[float]]) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh)
        os.replace(tmp, path)
    except Exception as e:  # cache is best-effort
        print(f"[warn] embedding cache write failed: {e}", file=sys.stderr)


def _embed_key(model: str, prefix: str, text: str) -> str:
    h = hashlib.sha1(f"{model}\x00{prefix}\x00{text}".encode("utf-8")).hexdigest()
    return h


def _post_embeddings(endpoint: str, model: str, inputs: Sequence[str]) -> List[List[float]]:
    payload = json.dumps({"model": model, "input": list(inputs)}).encode("utf-8")
    req = urllib.request.Request(
        endpoint, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    items = sorted(data["data"], key=lambda d: d.get("index", 0))
    return [list(map(float, it["embedding"])) for it in items]


def embed_prompts(
    texts: Sequence[str],
    endpoint: str,
    model: str,
    prefix: str,
    cache_path: str,
) -> Tuple[List[Optional[List[float]]], int]:
    """Embed ``texts``; returns (vectors aligned to texts, dimension).

    Why: clustering needs a vector per distinct prompt. Embedding is the only
    network cost; the on-disk cache makes re-runs ~free.
    What: prefixes each text, checks the cache, batches the misses to the
    endpoint, persists the cache. On total endpoint failure raises RuntimeError
    so the caller can report a fallback.
    Test: with a warm cache and the endpoint down, still returns cached vectors.
    """
    cache = _cache_load(cache_path)
    keys = [_embed_key(model, prefix, t) for t in texts]
    vectors: List[Optional[List[float]]] = [cache.get(k) for k in keys]

    misses = [i for i, v in enumerate(vectors) if v is None]
    dim = 0
    for v in vectors:
        if v:
            dim = len(v)
            break

    if misses:
        for start in range(0, len(misses), BATCH):
            batch_idx = misses[start : start + BATCH]
            inputs = [prefix + texts[i] for i in batch_idx]
            for attempt in range(2):
                try:
                    embs = _post_embeddings(endpoint, model, inputs)
                    break
                except (urllib.error.URLError, TimeoutError, OSError) as e:
                    if attempt == 1:
                        if any(vectors):  # we still have a partial / cached set
                            print(
                                f"[warn] embedding endpoint failed on batch "
                                f"{start}: {e}; continuing with cached vectors",
                                file=sys.stderr,
                            )
                            embs = None
                            break
                        raise RuntimeError(f"embedding endpoint unreachable: {e}") from e
                    time.sleep(1.0)
            else:  # pragma: no cover
                embs = None
            if embs:
                for j, idx in enumerate(batch_idx):
                    vectors[idx] = embs[j]
                    cache[keys[idx]] = embs[j]
                    dim = len(embs[j])
        _cache_save(cache_path, cache)

    return vectors, dim


# --------------------------------------------------------------------------- #
# Clustering (deterministic greedy threshold on cosine)
# --------------------------------------------------------------------------- #

def _normalize(v: Sequence[float]) -> List[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _cosine_norm(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def greedy_cluster(
    items: List[dict],
    vectors: List[Optional[List[float]]],
    threshold: float,
) -> List[List[int]]:
    """Greedy threshold clustering on cosine similarity. Deterministic.

    Why: we want a simple, reproducible clustering with one tunable knob, no
    randomness, no external deps (no numpy/sklearn requirement).
    What: sort items by frequency desc (stable), then for each item assign it to
    the first existing cluster whose centroid (first-member vector) cosine >=
    threshold, else start a new cluster. Centroid = the seed (highest-frequency)
    member, which keeps assignment order-independent given the fixed sort.
    Test: two near-identical prompts land in one cluster; an unrelated prompt in
    its own.
    """
    order = sorted(
        range(len(items)),
        key=lambda i: (-items[i]["count"], items[i]["text"]),
    )
    norms: Dict[int, List[float]] = {}
    for i in order:
        if vectors[i] is not None:
            norms[i] = _normalize(vectors[i])

    clusters: List[List[int]] = []
    seeds: List[int] = []
    for i in order:
        if i not in norms:
            clusters.append([i])  # un-embeddable → singleton
            seeds.append(i)
            continue
        best_c, best_sim = -1, -1.0
        for ci, seed in enumerate(seeds):
            if seed not in norms:
                continue
            sim = _cosine_norm(norms[i], norms[seed])
            if sim > best_sim:
                best_sim, best_c = sim, ci
        if best_c >= 0 and best_sim >= threshold:
            clusters[best_c].append(i)
        else:
            clusters.append([i])
            seeds.append(i)
    return clusters


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #

# Tool calls that signal NON-determinism — if a turn delegates or browses/searches,
# its answer depends on live reasoning, not local/public data.
NONDETERMINISTIC_TOOLS = {
    "delegate_task", "create_subagent", "web_search", "browser",
    "web_extract", "deep_research",
}


def score_cluster(items: List[dict], idxs: List[int]) -> dict:
    """Score a cluster on frequency, cost proxy, and a determinism signal.

    Why: rank candidates by how worthwhile a deterministic handler would be.
    What:
      - size: total occurrences across members (frequency).
      - avg_llm_calls: mean api_call_count across member sessions (cost proxy;
        latency deliberately ignored — it is corrupt in this DB).
      - determinism: in [0,1]. High when tool-call diversity is low, a single
        tool dominates (or no tools), and no non-deterministic tool appears.
    Test: a cluster of pure-text Q&A with no tools scores determinism ~1.0; a
    cluster that always calls delegate_task scores near 0.
    """
    members = [items[i] for i in idxs]
    size = sum(m["count"] for m in members)
    api_vals = [v for m in members for v in m["api_calls"]]
    avg_llm = round(sum(api_vals) / len(api_vals), 2) if api_vals else None

    tool_counter: Counter = Counter()
    for m in members:
        tool_counter.update(m["tools"])
    total_tool_calls = sum(tool_counter.values())
    distinct_tools = len(tool_counter)
    dominant = tool_counter.most_common(1)[0] if tool_counter else None

    has_nondet = any(t in NONDETERMINISTIC_TOOLS for t in tool_counter)

    # Determinism heuristic in [0,1].
    if has_nondet:
        determinism = 0.1
    elif total_tool_calls == 0:
        determinism = 1.0  # pure-text answer, no tools at all
    else:
        # one dominant tool, low diversity → more deterministic
        dom_share = dominant[1] / total_tool_calls if dominant else 0.0
        diversity_penalty = min(distinct_tools - 1, 3) * 0.15
        determinism = round(max(0.0, min(1.0, dom_share - diversity_penalty)), 2)

    return {
        "size": size,
        "members": len(members),
        "avg_llm_calls": avg_llm,
        "determinism": round(determinism, 2),
        "distinct_tools": distinct_tools,
        "dominant_tool": dominant[0] if dominant else None,
        "tool_histogram": dict(tool_counter.most_common(5)),
        "has_nondeterministic_tool": has_nondet,
    }


def rank_score(s: dict) -> float:
    """Composite rank key: frequency x determinism, lightly weighted by cost.

    Why: a good candidate is frequent AND deterministic; cheap-looking-today
    intents that are frequent are the best wins. We rank, never auto-act.
    """
    size = s["size"]
    det = s["determinism"]
    cost = s["avg_llm_calls"] or 1.0
    return round(size * det * (1.0 + 0.1 * cost), 3)


# --------------------------------------------------------------------------- #
# Report assembly
# --------------------------------------------------------------------------- #

def build_candidates(
    items: List[dict],
    clusters: List[List[int]],
    min_cluster: int,
) -> List[dict]:
    cands: List[dict] = []
    for idxs in clusters:
        members = [items[i] for i in idxs]
        rep = max(members, key=lambda m: m["count"])  # representative = most frequent
        covered = covered_intent(rep["text"])
        s = score_cluster(items, idxs)
        if s["size"] < min_cluster:
            continue
        examples = sorted({m["text"] for m in members})[:5]
        cands.append(
            {
                "representative": rep["text"],
                "size": s["size"],
                "members": s["members"],
                "examples": examples,
                "avg_llm_calls": s["avg_llm_calls"],
                "determinism": s["determinism"],
                "dominant_tool": s["dominant_tool"],
                "distinct_tools": s["distinct_tools"],
                "tool_histogram": s["tool_histogram"],
                "already_covered": covered,
                "rank_score": rank_score(s),
            }
        )
    # Excluded (covered) candidates sink to the bottom; then by rank_score desc.
    cands.sort(key=lambda c: (c["already_covered"] is not None, -c["rank_score"]))
    return cands


def render_markdown(meta: dict, cands: List[dict]) -> str:
    lines: List[str] = []
    lines.append("# Deterministic-Intent Miner — Candidate Report")
    lines.append("")
    lines.append(
        f"- generated: {meta['generated_at']}  |  db: `{meta['db']}`  |  "
        f"prompts: {meta['distinct_prompts']} distinct / {meta['total_prompts']} total"
    )
    lines.append(
        f"- embeddings: `{meta['model']}` dim={meta['dimension']} "
        f"via {meta['endpoint']}  |  cosine threshold={meta['threshold']}  |  "
        f"min_cluster={meta['min_cluster']}"
    )
    lines.append(
        "- **PROPOSE-ONLY**: this report ships nothing. A handler is built only "
        "after the reviewer's strict determinism gate and a human accept."
    )
    lines.append("")
    fresh = [c for c in cands if not c["already_covered"]]
    covered = [c for c in cands if c["already_covered"]]
    if not fresh:
        lines.append(
            "## No NEW candidates above the gate\n\n"
            "Every cluster that cleared `min_cluster` matches an already-covered "
            "intent (or none cleared). This is the expected, healthy result at "
            "low data volume — **the gate is working**. Nothing to propose."
        )
    else:
        lines.append(f"## {len(fresh)} candidate intent(s) above the gate\n")
        lines.append(
            "| # | Representative | size | det | avg_llm_calls | dominant tool | rank |"
        )
        lines.append("|---|---|---|---|---|---|---|")
        for i, c in enumerate(fresh, 1):
            rep = c["representative"].replace("|", "\\|")[:70]
            lines.append(
                f"| {i} | {rep} | {c['size']} | {c['determinism']} | "
                f"{c['avg_llm_calls']} | {c['dominant_tool'] or '-'} | {c['rank_score']} |"
            )
        lines.append("")
        for i, c in enumerate(fresh, 1):
            lines.append(f"### Candidate {i}: {c['representative'][:80]}")
            lines.append(f"- size={c['size']} members={c['members']} "
                         f"determinism={c['determinism']} "
                         f"avg_llm_calls={c['avg_llm_calls']}")
            lines.append(f"- tools={c['tool_histogram'] or '{}'}")
            lines.append("- example phrasings:")
            for ex in c["examples"]:
                lines.append(f"  - {ex[:100]}")
            lines.append("")
    if covered:
        lines.append("## Already-covered clusters (excluded, shown for transparency)\n")
        for c in covered:
            lines.append(
                f"- [{c['already_covered']}] \"{c['representative'][:70]}\" "
                f"(size={c['size']})"
            )
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Self-test (offline)
# --------------------------------------------------------------------------- #

def _self_test() -> int:
    assert covered_intent("what time is it") == "time/date"
    assert covered_intent("how much disk space is free") == "disk"
    assert covered_intent("is the hermes gateway running") == "service-status"
    assert covered_intent("what's the weather") == "weather"
    assert covered_intent("summarize this changelog for me") is None

    assert is_noise("Reply with the single word: pong")
    assert is_noise("You've reached the maximum number of tool-calling iterations allowed.")
    assert not is_noise("how much disk is free")

    items = [
        {"text": "what is 2+2", "count": 3, "api_calls": [1, 1, 1], "tools": Counter()},
        {"text": "what is two plus two", "count": 1, "api_calls": [1], "tools": Counter()},
        {"text": "deploy the gateway now", "count": 2, "api_calls": [5, 5],
         "tools": Counter({"delegate_task": 2})},
    ]
    # identical-ish first two should cluster; third separate.
    v_a = _normalize([1.0, 0.0, 0.0])
    v_b = _normalize([0.98, 0.02, 0.0])
    v_c = _normalize([0.0, 1.0, 0.0])
    clusters = greedy_cluster(items, [v_a, v_b, v_c], threshold=0.83)
    sizes = sorted(len(c) for c in clusters)
    assert sizes == [1, 2], sizes
    s_math = score_cluster(items, [0, 1])
    assert s_math["determinism"] == 1.0, s_math
    s_deploy = score_cluster(items, [2])
    assert s_deploy["determinism"] <= 0.2, s_deploy
    print("self-test OK")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--endpoint", default=os.environ.get("MINER_EMBED_ENDPOINT", DEFAULT_ENDPOINT))
    ap.add_argument("--model", default=os.environ.get("MINER_EMBED_MODEL", DEFAULT_MODEL))
    ap.add_argument("--prefix", default=DEFAULT_PREFIX)
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                    help="cosine clustering threshold (tuned band 0.80-0.85)")
    ap.add_argument("--min-cluster", type=int, default=DEFAULT_MIN_CLUSTER,
                    help="gate: minimum cluster size to emit (default 5 for seeding; "
                         ">=10 is the real bar at scale)")
    ap.add_argument("--json-only", action="store_true", help="emit JSON only (no markdown)")
    ap.add_argument("--self-test", action="store_true", help="run offline unit checks and exit")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    items = load_prompts(args.db)
    total_prompts = sum(m["count"] for m in items)
    if not items:
        out = {"candidates": [], "meta": {"note": "no user prompts found", "db": args.db}}
        print(json.dumps(out, indent=2))
        return 0

    texts = [m["text"] for m in items]
    try:
        vectors, dim = embed_prompts(texts, args.endpoint, args.model, args.prefix, args.cache)
        embed_ok = any(v is not None for v in vectors)
    except RuntimeError as e:
        print(json.dumps({
            "candidates": [],
            "meta": {"error": f"embedding failed: {e}", "endpoint": args.endpoint,
                     "db": args.db, "distinct_prompts": len(items)},
        }, indent=2))
        return 2

    clusters = greedy_cluster(items, vectors, args.threshold)
    cands = build_candidates(items, clusters, args.min_cluster)

    meta = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "db": args.db,
        "endpoint": args.endpoint,
        "model": args.model,
        "dimension": dim,
        "embed_ok": embed_ok,
        "threshold": args.threshold,
        "min_cluster": args.min_cluster,
        "distinct_prompts": len(items),
        "total_prompts": total_prompts,
        "clusters_total": len(clusters),
        "candidates_above_gate": len(cands),
        "new_candidates": len([c for c in cands if not c["already_covered"]]),
        "propose_only": True,
    }
    out = {"meta": meta, "candidates": cands}
    print(json.dumps(out, indent=2))
    if not args.json_only:
        print()
        print("<!-- MARKDOWN REPORT -->")
        print(render_markdown(meta, cands))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
