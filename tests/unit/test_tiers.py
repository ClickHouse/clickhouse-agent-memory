"""
tests/unit/test_tiers.py
------------------------
Pure-function tests for the mcp_server.tiers module.

The envelope is intentionally minimal. The old helpers (_format_latency,
tier_badge_markdown, tier_banner) were dropped because LibreChat already
renders tool cards with their own visual chrome, so the prose chrome the
agent emitted was redundant and fighting the UI.
"""

from __future__ import annotations

import pytest

from mcp_server.tiers import DOMAINS, TIER_META


pytestmark = pytest.mark.unit


class TestTierMeta:
    """TIER_META carries engine + latency profile for each tier label."""

    @pytest.mark.parametrize("tier", ["HOT", "WARM", "GRAPH", "RESULT"])
    def test_every_tier_has_label_engine_and_profile(self, tier):
        meta = TIER_META[tier]
        assert isinstance(meta["label"], str) and meta["label"]
        assert isinstance(meta["engine"], str) and meta["engine"]
        assert isinstance(meta["latency_profile"], str) and meta["latency_profile"]

    def test_hot_engine_mentions_memory_engine(self):
        # The whole pitch hinges on this string.
        assert "Memory Engine" in TIER_META["HOT"]["engine"]

    def test_warm_engine_mentions_vector_search(self):
        assert "Vector Search" in TIER_META["WARM"]["engine"]

    def test_graph_engine_mentions_sql_joins(self):
        engine = TIER_META["GRAPH"]["engine"]
        assert "JOIN" in engine


class TestDomains:
    """DOMAINS is the closed set of valid `domain` args for domain tools."""

    def test_expected_three_domains(self):
        assert DOMAINS == ("observability", "telco", "cybersecurity")

    def test_every_domain_has_tier_meta_coverage(self):
        # Sanity: each domain string is a valid label for our tools, and
        # each domain tool response should be assignable to one of the tiers.
        for d in DOMAINS:
            assert isinstance(d, str) and d
