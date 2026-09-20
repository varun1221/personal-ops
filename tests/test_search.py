"""search_notes semantics.

These exist because a live run failed here: the model sent
"I said I'd OR I'll OR I promised" as one literal string, matched nothing, and
reported that the user had no commitments — a confident wrong answer.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVER = PROJECT_ROOT / "servers" / "obsidian" / "server.py"


@pytest.fixture(scope="module")
def search():
    """Call search_notes in-process, with the fixture vault configured."""
    import importlib
    import os

    os.environ["OBSIDIAN_VAULT_PATH"] = str(PROJECT_ROOT / "fixtures" / "vault")
    sys.path.insert(0, str(PROJECT_ROOT))
    module = importlib.import_module("servers.obsidian.server")

    def call(query, limit=10):
        return json.loads(module.search_notes(query, limit))

    return call


def test_plain_substring_match(search):
    result = search("Sarah")
    assert result["result_count"] > 0
    assert any("Sync with Sarah" in r["note_path"] for r in result["results"])


def test_or_returns_union_of_terms(search):
    """The bug: this used to be matched as one literal string and found nothing."""
    result = search("I said I'd OR I promised OR Me: send")
    assert result["result_count"] > 0, "OR alternatives must match independently"
    assert set(result["terms_searched"]) == {"i said i'd", "i promised", "me: send"}


def test_or_finds_the_prose_commitment(search):
    """The flagship query depends on finding a commitment written as prose."""
    result = search("I said I'd OR I'd get her OR Me: send")
    paths = {r["note_path"] for r in result["results"]}
    assert "Meetings/2026-08-13 Sync with Sarah.md" in paths


def test_matched_terms_reported(search):
    result = search("Sarah OR zzzznotpresent")
    hit = next(r for r in result["results"] if "Sarah" in r["note_path"])
    assert hit["matched_terms"] == ["sarah"]


def test_matches_only_lines_containing_a_hit_term(search):
    result = search("Sarah")
    for entry in result["results"]:
        for match in entry["matches"]:
            assert "sarah" in match["text"].lower()


def test_empty_result_says_it_is_inconclusive(search):
    """An empty result must not read as 'there is no such commitment'."""
    result = search("zzzznotpresentanywhere")
    assert result["result_count"] == 0
    assert "does NOT mean" in result["note"]


def test_case_insensitive(search):
    assert search("SARAH")["result_count"] == search("sarah")["result_count"]


def test_empty_query_errors(search):
    assert "error" in search("   ")


def test_limit_is_respected(search):
    assert len(search("the", limit=2)["results"]) <= 2


def test_server_starts_standalone():
    """A syntax or import error here breaks the whole toolset load, silently."""
    proc = subprocess.run(
        [sys.executable, "-c", f"import runpy; runpy.run_path({str(SERVER)!r})"],
        capture_output=True,
        timeout=30,
        input=b"",
    )
    assert b"SyntaxError" not in proc.stderr
    assert b"ImportError" not in proc.stderr
