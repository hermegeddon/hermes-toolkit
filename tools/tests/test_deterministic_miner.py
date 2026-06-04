"""Unit tests for deterministic_miner.py — the embedding-enhanced intent miner.

All tests are fully offline: no live embedding endpoint, no real state.db.
Mock strategy mirrors the intent-handlers-core test suite standard.

Run: python -m pytest tools/tests/test_deterministic_miner.py -v
  (from /opt/hermes/toolkit, or from tools/tests/ after conftest puts the path in)
"""

from __future__ import annotations

import math
import sys
import os
from collections import Counter
from typing import List, Optional
from unittest.mock import MagicMock, patch

import pytest

# conftest.py adds tools/ to sys.path
import deterministic_miner as dm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _unit_vec(x: float, y: float, z: float) -> List[float]:
    """Return a unit vector from raw components (for readability in tests)."""
    return dm._normalize([x, y, z])


def _make_item(text: str, count: int = 1, tools: Optional[Counter] = None,
               api_calls: Optional[List[int]] = None) -> dict:
    return {
        "text": text,
        "count": count,
        "api_calls": api_calls if api_calls is not None else [1] * count,
        "tools": tools if tools is not None else Counter(),
        "sources": Counter(),
    }


# ===========================================================================
# 1. Cosine clustering
# ===========================================================================

class TestGreedyClustering:
    """greedy_cluster: threshold, identical vectors, dissimilar vectors."""

    def test_identical_vectors_cluster_together(self):
        items = [_make_item("a", 2), _make_item("b", 1)]
        v = _unit_vec(1.0, 0.0, 0.0)
        clusters = dm.greedy_cluster(items, [v, v], threshold=0.83)
        sizes = sorted(len(c) for c in clusters)
        assert sizes == [2], f"expected one cluster of 2, got {sizes}"

    def test_near_vectors_cluster_together(self):
        # cosine of these two is ~0.9997 — well above 0.83
        items = [_make_item("what is 2+2", 3), _make_item("what is two plus two", 1)]
        v_a = _unit_vec(1.0, 0.0, 0.01)
        v_b = _unit_vec(1.0, 0.0, 0.02)
        clusters = dm.greedy_cluster(items, [v_a, v_b], threshold=0.83)
        sizes = sorted(len(c) for c in clusters)
        assert sizes == [2], sizes

    def test_dissimilar_vectors_separate_clusters(self):
        items = [_make_item("math question"), _make_item("deploy the gateway")]
        v_a = _unit_vec(1.0, 0.0, 0.0)
        v_c = _unit_vec(0.0, 1.0, 0.0)  # cosine = 0.0
        clusters = dm.greedy_cluster(items, [v_a, v_c], threshold=0.83)
        sizes = sorted(len(c) for c in clusters)
        assert sizes == [1, 1], sizes

    def test_threshold_arg_controls_clustering(self):
        """Same vectors: low threshold → one cluster; high threshold → two."""
        items = [_make_item("a", 2), _make_item("b", 1)]
        # cosine ~0.866 (60° apart)
        v_a = _unit_vec(1.0, 0.0, 0.0)
        v_b = _unit_vec(0.866, 0.5, 0.0)
        clusters_loose = dm.greedy_cluster(items, [v_a, v_b], threshold=0.80)
        clusters_tight = dm.greedy_cluster(items, [v_a, v_b], threshold=0.90)
        assert sorted(len(c) for c in clusters_loose) == [2]
        assert sorted(len(c) for c in clusters_tight) == [1, 1]

    def test_none_vector_becomes_singleton_cluster(self):
        """A prompt whose embedding is None is placed in its own singleton cluster
        (it cannot be compared to anything, so it never joins another cluster)."""
        items = [_make_item("real question", 3), _make_item("un-embeddable prompt", 1)]
        v_real = _unit_vec(1.0, 0.0, 0.0)
        clusters = dm.greedy_cluster(items, [v_real, None], threshold=0.83)
        # Two clusters: one for the real item, one singleton for the None item
        assert len(clusters) == 2
        sizes = sorted(len(c) for c in clusters)
        assert sizes == [1, 1], sizes

    def test_all_none_vectors_each_singleton(self):
        """When ALL embeddings are None, every item becomes its own singleton."""
        items = [_make_item(f"prompt {i}", 1) for i in range(3)]
        clusters = dm.greedy_cluster(items, [None, None, None], threshold=0.83)
        assert len(clusters) == 3
        assert all(len(c) == 1 for c in clusters)

    def test_three_item_mixed_cluster(self):
        """High-freq item + two near-neighbours → one cluster of 3."""
        items = [
            _make_item("what is 2+2", 5),
            _make_item("what is two plus two", 2),
            _make_item("what does 2+2 equal", 1),
        ]
        v = _unit_vec(1.0, 0.0, 0.0)
        v_near1 = _unit_vec(0.999, 0.001, 0.0)
        v_near2 = _unit_vec(0.998, 0.002, 0.0)
        clusters = dm.greedy_cluster(items, [v, v_near1, v_near2], threshold=0.83)
        sizes = sorted(len(c) for c in clusters)
        assert sizes == [3], sizes


# ===========================================================================
# 2. Scoring
# ===========================================================================

class TestScoring:
    """score_cluster: frequency × determinism × api_call_count; latency NOT used;
    delegate_task/web_search/vision penalize determinism."""

    def test_pure_text_scores_determinism_1(self):
        """No tools at all → pure determinism = 1.0."""
        items = [_make_item("what time is it", 10, api_calls=[1]*10)]
        s = dm.score_cluster(items, [0])
        assert s["determinism"] == 1.0, s
        assert s["size"] == 10
        assert s["avg_llm_calls"] == 1.0

    def test_delegate_task_penalizes_determinism(self):
        items = [_make_item("deploy the gateway", 5,
                            tools=Counter({"delegate_task": 5}),
                            api_calls=[8]*5)]
        s = dm.score_cluster(items, [0])
        assert s["determinism"] <= 0.2, s
        assert s["has_nondeterministic_tool"] is True

    def test_web_search_penalizes_determinism(self):
        items = [_make_item("search the web for news", 3,
                            tools=Counter({"web_search": 3}),
                            api_calls=[4]*3)]
        s = dm.score_cluster(items, [0])
        assert s["has_nondeterministic_tool"] is True
        assert s["determinism"] <= 0.2, s

    def test_vision_not_in_nondeterministic_tools(self):
        # 'vision' is NOT in NONDETERMINISTIC_TOOLS — it is a single dominant
        # tool with no diversity, so dominance=1.0 and the heuristic scores it
        # as fully deterministic. The reviewer cron rejects vision candidates
        # via its checklist (not the miner score). Verify the scoring matches
        # this documented behaviour.
        items = [_make_item("what's in this image", 2,
                            tools=Counter({"vision": 2}),
                            api_calls=[3]*2)]
        s = dm.score_cluster(items, [0])
        assert s["has_nondeterministic_tool"] is False
        # single dominant tool, no diversity → scores as deterministic
        assert s["determinism"] == 1.0, s

    def test_api_call_count_reflected_in_avg(self):
        items = [_make_item("expensive prompt", 1, api_calls=[15])]
        s = dm.score_cluster(items, [0])
        assert s["avg_llm_calls"] == 15.0

    def test_rank_score_uses_frequency_and_determinism(self):
        """rank_score = size * det * (1 + 0.1 * cost); latency not used."""
        s_high = {"size": 20, "determinism": 1.0, "avg_llm_calls": 2.0}
        s_low = {"size": 5, "determinism": 0.1, "avg_llm_calls": 2.0}
        assert dm.rank_score(s_high) > dm.rank_score(s_low)

    def test_rank_score_formula(self):
        s = {"size": 10, "determinism": 0.5, "avg_llm_calls": 4.0}
        expected = round(10 * 0.5 * (1.0 + 0.1 * 4.0), 3)
        assert dm.rank_score(s) == expected

    def test_no_api_calls_defaults_cost_to_1(self):
        """avg_llm_calls=None → rank_score uses cost=1.0."""
        s = {"size": 10, "determinism": 1.0, "avg_llm_calls": None}
        expected = round(10 * 1.0 * (1.0 + 0.1 * 1.0), 3)
        assert dm.rank_score(s) == expected

    def test_single_dominant_tool_scores_higher_than_diverse(self):
        """Single dominant tool = more deterministic than many tools."""
        item_single = _make_item("x", 5, tools=Counter({"read_file": 5}), api_calls=[2]*5)
        item_diverse = _make_item("y", 5, tools=Counter({"read_file": 2, "write_file": 2,
                                                         "list_dir": 1}), api_calls=[2]*5)
        s_single = dm.score_cluster([item_single], [0])
        s_diverse = dm.score_cluster([item_diverse], [0])
        assert s_single["determinism"] >= s_diverse["determinism"]

    def test_multi_member_cluster_size_sums_counts(self):
        """size = sum of member counts."""
        items = [_make_item("a", 3), _make_item("b", 7)]
        s = dm.score_cluster(items, [0, 1])
        assert s["size"] == 10


# ===========================================================================
# 3. Min-cluster gate
# ===========================================================================

class TestMinClusterGate:
    """build_candidates: clusters below min_cluster are excluded from output."""

    def test_below_gate_excluded(self):
        items = [_make_item("small prompt", 2)]
        v = _unit_vec(1.0, 0.0, 0.0)
        clusters = dm.greedy_cluster(items, [v], threshold=0.83)
        cands = dm.build_candidates(items, clusters, min_cluster=5)
        assert cands == [], f"Expected empty candidates, got {cands}"

    def test_exactly_at_gate_included(self):
        items = [_make_item("medium prompt", 5)]
        v = _unit_vec(1.0, 0.0, 0.0)
        clusters = dm.greedy_cluster(items, [v], threshold=0.83)
        cands = dm.build_candidates(items, clusters, min_cluster=5)
        assert len(cands) == 1

    def test_above_gate_included(self):
        items = [_make_item("frequent prompt", 20)]
        v = _unit_vec(1.0, 0.0, 0.0)
        clusters = dm.greedy_cluster(items, [v], threshold=0.83)
        cands = dm.build_candidates(items, clusters, min_cluster=10)
        assert len(cands) == 1

    def test_mixed_gate_partial_output(self):
        """One cluster above gate, one below → only the big one emitted."""
        items = [
            _make_item("frequent prompt", 15),
            _make_item("rare unrelated prompt", 2),
        ]
        v_big = _unit_vec(1.0, 0.0, 0.0)
        v_small = _unit_vec(0.0, 1.0, 0.0)
        clusters = dm.greedy_cluster(items, [v_big, v_small], threshold=0.83)
        cands = dm.build_candidates(items, clusters, min_cluster=10)
        assert len(cands) == 1
        assert cands[0]["size"] == 15


# ===========================================================================
# 4. Already-covered exclusion
# ===========================================================================

class TestCoveredExclusion:
    """covered_intent and build_candidates: already-covered clusters are flagged
    and sorted to the bottom (not silently dropped but marked already_covered)."""

    def test_weather_excluded(self):
        assert dm.covered_intent("what's the weather") == "weather"
        assert dm.covered_intent("what is the weather forecast") == "weather"

    def test_time_excluded(self):
        assert dm.covered_intent("what time is it") == "time/date"
        assert dm.covered_intent("what's today's date") == "time/date"
        assert dm.covered_intent("what day is it") == "time/date"

    def test_disk_excluded(self):
        assert dm.covered_intent("how much disk space is free") == "disk"
        assert dm.covered_intent("disk usage") == "disk"

    def test_service_status_excluded(self):
        assert dm.covered_intent("is the hermes gateway running") == "service-status"
        assert dm.covered_intent("status of hermes-gateway") == "service-status"

    def test_uncovered_intent_returns_none(self):
        assert dm.covered_intent("summarize this changelog") is None
        assert dm.covered_intent("what is a neural network") is None
        assert dm.covered_intent("list my cron jobs") is None

    def test_empty_input_returns_none(self):
        assert dm.covered_intent("") is None
        assert dm.covered_intent("   ") is None

    def test_build_candidates_marks_covered_and_sinks_to_bottom(self):
        """A large covered cluster still appears but is flagged and ranked last."""
        items = [
            _make_item("what time is it", 50),        # covered: time/date
            _make_item("list my running services", 20),  # uncovered (no strong match)
        ]
        v_a = _unit_vec(1.0, 0.0, 0.0)
        v_b = _unit_vec(0.0, 1.0, 0.0)
        clusters = dm.greedy_cluster(items, [v_a, v_b], threshold=0.83)
        cands = dm.build_candidates(items, clusters, min_cluster=5)
        # Both above gate; covered one should be last
        assert len(cands) == 2
        assert cands[0]["already_covered"] is None      # uncovered first
        assert cands[-1]["already_covered"] is not None  # covered last


# ===========================================================================
# 5. Noise filtering
# ===========================================================================

class TestNoiseFiltering:
    """is_noise: eval/harness/self-prompt rows are dropped."""

    def test_harness_max_iterations_is_noise(self):
        assert dm.is_noise(
            "You've reached the maximum number of tool-calling iterations allowed."
        ) is True

    def test_reply_with_single_word_is_noise(self):
        assert dm.is_noise("Reply with the single word: pong") is True

    def test_reply_with_exactly_is_noise(self):
        assert dm.is_noise("Reply with exactly one word.") is True

    def test_please_provide_final_response_is_noise(self):
        assert dm.is_noise(
            "Please provide a final response summarizing what you've found."
        ) is True

    def test_smoke_ok_is_noise(self):
        assert dm.is_noise("smoke_ok") is True

    def test_profile_ok_is_noise(self):
        assert dm.is_noise("profile_ok") is True

    def test_empty_string_is_noise(self):
        assert dm.is_noise("") is True
        assert dm.is_noise("   ") is True

    def test_real_prompts_not_noise(self):
        for q in [
            "how much disk is free",
            "what time is it",
            "is the hermes gateway running",
            "summarize the changelog",
            "list my cron jobs",
            "what is 2+2",
        ]:
            assert dm.is_noise(q) is False, q


# ===========================================================================
# 6. None-embedding regression (the bug)
# ===========================================================================

class TestNoneEmbeddingRegression:
    """BUG: when the embed endpoint returns None for a prompt, the miner used
    to crash in _normalize or dim calculation. After the fix it must SKIP the
    prompt and COMPLETE without error."""

    def test_none_embedding_does_not_crash_miner(self, tmp_path):
        """Mock embed returning None for one prompt → miner completes, no crash."""
        db_path = str(tmp_path / "state.db")
        cache_path = str(tmp_path / "embed_cache.json")

        # Seed a minimal state.db
        import sqlite3
        con = sqlite3.connect(db_path)
        con.executescript("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                api_call_count INTEGER,
                source TEXT,
                estimated_cost_usd REAL
            );
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY,
                session_id TEXT,
                role TEXT,
                content TEXT,
                tool_calls TEXT,
                tool_name TEXT
            );
            INSERT INTO sessions VALUES ('s1', 3, 'test', 0.01);
            INSERT INTO sessions VALUES ('s2', 3, 'test', 0.01);
            INSERT INTO sessions VALUES ('s3', 3, 'test', 0.01);
            INSERT INTO sessions VALUES ('s4', 2, 'test', 0.01);
            INSERT INTO sessions VALUES ('s5', 2, 'test', 0.01);
            INSERT INTO messages VALUES (1,'s1','user','what is 2+2',NULL,NULL);
            INSERT INTO messages VALUES (2,'s2','user','what is 2+2',NULL,NULL);
            INSERT INTO messages VALUES (3,'s3','user','what is 2+2',NULL,NULL);
            INSERT INTO messages VALUES (4,'s4','user','embed will fail for me',NULL,NULL);
            INSERT INTO messages VALUES (5,'s5','user','embed will fail for me',NULL,NULL);
        """)
        con.commit()
        con.close()

        good_vec = [0.1, 0.2, 0.3]

        # Simulate: first prompt gets a good embedding, second gets None
        call_count = [0]
        def _mock_post_embeddings(endpoint, model, inputs):
            results = []
            for inp in inputs:
                call_count[0] += 1
                if "fail" in inp:
                    results.append(None)  # the buggy endpoint returning None
                else:
                    results.append(list(good_vec))
            return results

        with patch.object(dm, "_post_embeddings", side_effect=_mock_post_embeddings):
            vectors, dim = dm.embed_prompts(
                ["what is 2+2", "embed will fail for me"],
                endpoint="http://fake:11434/v1/embeddings",
                model="nomic-embed-text",
                prefix="search_document: ",
                cache_path=cache_path,
            )

        # The good vector should be present; the None one should remain None
        assert vectors[0] is not None, "good prompt should have a vector"
        assert vectors[1] is None, "failing prompt vector should remain None"
        # dim should reflect the good embedding
        assert dim == 3

    def test_none_embedding_miner_completes_end_to_end(self, tmp_path):
        """Full miner run with one None-embedding prompt completes without exception."""
        db_path = str(tmp_path / "state.db")
        cache_path = str(tmp_path / "embed_cache.json")

        import sqlite3
        con = sqlite3.connect(db_path)
        con.executescript("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                api_call_count INTEGER,
                source TEXT,
                estimated_cost_usd REAL
            );
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY,
                session_id TEXT,
                role TEXT,
                content TEXT,
                tool_calls TEXT,
                tool_name TEXT
            );
            INSERT INTO sessions VALUES ('s1', 2, 'test', 0.01);
            INSERT INTO sessions VALUES ('s2', 2, 'test', 0.01);
            INSERT INTO sessions VALUES ('s3', 2, 'test', 0.01);
            INSERT INTO sessions VALUES ('s4', 2, 'test', 0.01);
            INSERT INTO sessions VALUES ('s5', 2, 'test', 0.01);
            INSERT INTO sessions VALUES ('s6', 2, 'test', 0.01);
            INSERT INTO messages VALUES (1,'s1','user','what is 2+2',NULL,NULL);
            INSERT INTO messages VALUES (2,'s2','user','what is 2+2',NULL,NULL);
            INSERT INTO messages VALUES (3,'s3','user','what is 2+2',NULL,NULL);
            INSERT INTO messages VALUES (4,'s4','user','what is 2+2',NULL,NULL);
            INSERT INTO messages VALUES (5,'s5','user','what is 2+2',NULL,NULL);
            INSERT INTO messages VALUES (6,'s6','user','none embed prompt here',NULL,NULL);
        """)
        con.commit()
        con.close()

        good_vec = [1.0, 0.0, 0.0]

        def _mock_post_embeddings(endpoint, model, inputs):
            results = []
            for inp in inputs:
                if "none embed" in inp:
                    results.append(None)
                else:
                    results.append(list(good_vec))
            return results

        # This must NOT raise; it must complete and return a valid result
        with patch.object(dm, "_post_embeddings", side_effect=_mock_post_embeddings):
            result_code = dm.main([
                "--db", db_path,
                "--cache", cache_path,
                "--endpoint", "http://fake:11434/v1/embeddings",
                "--min-cluster", "5",
                "--json-only",
            ])

        assert result_code == 0

    def test_all_none_embeddings_miner_completes(self, tmp_path):
        """When ALL embeddings are None (endpoint broken, no cache), miner
        must complete gracefully — every item becomes a singleton, no crash."""
        db_path = str(tmp_path / "state.db")
        cache_path = str(tmp_path / "embed_cache.json")

        import sqlite3
        con = sqlite3.connect(db_path)
        con.executescript("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                api_call_count INTEGER,
                source TEXT,
                estimated_cost_usd REAL
            );
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY,
                session_id TEXT,
                role TEXT,
                content TEXT,
                tool_calls TEXT,
                tool_name TEXT
            );
            INSERT INTO sessions VALUES ('s1', 2, 'test', 0.01);
            INSERT INTO sessions VALUES ('s2', 2, 'test', 0.01);
            INSERT INTO messages VALUES (1,'s1','user','prompt alpha',NULL,NULL);
            INSERT INTO messages VALUES (2,'s2','user','prompt beta',NULL,NULL);
        """)
        con.commit()
        con.close()

        def _mock_post_embeddings(endpoint, model, inputs):
            return [None] * len(inputs)

        with patch.object(dm, "_post_embeddings", side_effect=_mock_post_embeddings):
            result_code = dm.main([
                "--db", db_path,
                "--cache", cache_path,
                "--endpoint", "http://fake:11434/v1/embeddings",
                "--min-cluster", "1",
                "--json-only",
            ])

        assert result_code == 0


# ===========================================================================
# 7. Empty-data: no user prompts
# ===========================================================================

class TestEmptyData:
    """No user prompts → clean empty output, no crash."""

    def test_empty_db_returns_zero_candidates(self, tmp_path):
        db_path = str(tmp_path / "state.db")
        cache_path = str(tmp_path / "embed_cache.json")

        import sqlite3
        con = sqlite3.connect(db_path)
        con.executescript("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                api_call_count INTEGER,
                source TEXT,
                estimated_cost_usd REAL
            );
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY,
                session_id TEXT,
                role TEXT,
                content TEXT,
                tool_calls TEXT,
                tool_name TEXT
            );
        """)
        con.commit()
        con.close()

        result_code = dm.main([
            "--db", db_path,
            "--cache", cache_path,
            "--endpoint", "http://fake:11434/v1/embeddings",
            "--min-cluster", "1",
            "--json-only",
        ])
        assert result_code == 0

    def test_only_noise_rows_returns_zero_candidates(self, tmp_path):
        """Rows that are all harness noise → treated as empty prompts."""
        db_path = str(tmp_path / "state.db")
        cache_path = str(tmp_path / "embed_cache.json")

        import sqlite3
        con = sqlite3.connect(db_path)
        con.executescript("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                api_call_count INTEGER,
                source TEXT,
                estimated_cost_usd REAL
            );
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY,
                session_id TEXT,
                role TEXT,
                content TEXT,
                tool_calls TEXT,
                tool_name TEXT
            );
            INSERT INTO sessions VALUES ('s1', 1, 'test', 0.0);
            INSERT INTO messages VALUES
                (1,'s1','user','Reply with the single word: pong',NULL,NULL);
            INSERT INTO messages VALUES
                (2,'s1','user','smoke_ok',NULL,NULL);
        """)
        con.commit()
        con.close()

        result_code = dm.main([
            "--db", db_path,
            "--cache", cache_path,
            "--endpoint", "http://fake:11434/v1/embeddings",
            "--min-cluster", "1",
            "--json-only",
        ])
        assert result_code == 0

    def test_load_prompts_empty_db(self, tmp_path):
        db_path = str(tmp_path / "state.db")
        import sqlite3
        con = sqlite3.connect(db_path)
        con.executescript("""
            CREATE TABLE sessions (id TEXT PRIMARY KEY, api_call_count INTEGER,
                                   source TEXT, estimated_cost_usd REAL);
            CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT,
                                   content TEXT, tool_calls TEXT, tool_name TEXT);
        """)
        con.commit()
        con.close()
        result = dm.load_prompts(db_path)
        assert result == []


# ===========================================================================
# 8. Embedding endpoint unreachable
# ===========================================================================

class TestEmbeddingEndpointUnreachable:
    """Graceful failure/skip when endpoint is unreachable — no stack trace."""

    def test_endpoint_error_with_no_cache_raises_runtime(self, tmp_path):
        """With no cached vectors, a total endpoint failure raises RuntimeError
        (caught by the caller, not a crash)."""
        import urllib.error
        cache_path = str(tmp_path / "embed_cache.json")

        def _fail(endpoint, model, inputs):
            raise urllib.error.URLError("connection refused")

        with patch.object(dm, "_post_embeddings", side_effect=_fail):
            with pytest.raises(RuntimeError, match="embedding endpoint unreachable"):
                dm.embed_prompts(
                    ["any prompt"],
                    endpoint="http://dead:11434/v1/embeddings",
                    model="nomic-embed-text",
                    prefix="search_document: ",
                    cache_path=cache_path,
                )

    def test_endpoint_error_with_partial_cache_continues(self, tmp_path):
        """With some cached vectors, a later batch failure warns and continues."""
        import urllib.error
        import json as _json
        cache_path = str(tmp_path / "embed_cache.json")

        # Pre-seed the cache with the first prompt's embedding
        good_vec = [0.5, 0.5, 0.0]
        key = dm._embed_key("nomic-embed-text", "search_document: ", "cached prompt")
        cache_data = {key: good_vec}
        with open(cache_path, "w") as f:
            _json.dump(cache_data, f)

        call_count = [0]
        def _fail_on_second(endpoint, model, inputs):
            call_count[0] += 1
            raise urllib.error.URLError("connection refused")

        with patch.object(dm, "_post_embeddings", side_effect=_fail_on_second):
            vectors, dim = dm.embed_prompts(
                ["cached prompt", "uncached will fail"],
                endpoint="http://dead:11434/v1/embeddings",
                model="nomic-embed-text",
                prefix="search_document: ",
                cache_path=cache_path,
            )

        # cached prompt's vector should be present
        assert vectors[0] == good_vec
        # uncached prompt's vector stays None (endpoint was down)
        assert vectors[1] is None

    def test_main_returns_error_code_on_total_embed_failure(self, tmp_path):
        """main() returns exit code 2 if embedding completely fails (no cache)."""
        db_path = str(tmp_path / "state.db")
        cache_path = str(tmp_path / "embed_cache.json")

        import sqlite3, urllib.error
        con = sqlite3.connect(db_path)
        con.executescript("""
            CREATE TABLE sessions (id TEXT PRIMARY KEY, api_call_count INTEGER,
                                   source TEXT, estimated_cost_usd REAL);
            CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT,
                                   content TEXT, tool_calls TEXT, tool_name TEXT);
            INSERT INTO sessions VALUES ('s1', 2, 'test', 0.01);
            INSERT INTO messages VALUES (1,'s1','user','any real question here',NULL,NULL);
        """)
        con.commit()
        con.close()

        def _fail(endpoint, model, inputs):
            raise urllib.error.URLError("connection refused")

        with patch.object(dm, "_post_embeddings", side_effect=_fail):
            result_code = dm.main([
                "--db", db_path,
                "--cache", cache_path,
                "--endpoint", "http://dead:11434/v1/embeddings",
                "--min-cluster", "1",
                "--json-only",
            ])

        assert result_code == 2
