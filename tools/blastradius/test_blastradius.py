# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
"""Confidence labelling under incomplete graph analysis.

The fixture in `fixtures/impact_partial_failures.json` is a saved
`entire graph impact` payload whose `partial_failures` list names a Python file
that one of the reported callers lives in. That is the incomplete-analysis case:
the graph still emits a CALLS edge into a file it could not parse cleanly, and
the report must not present that edge as settled.

Run:  pytest tools/blastradius/test_blastradius.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from blastradius import (  # noqa: E402
    CONFIRMED,
    HEURISTIC,
    UNVERIFIED,
    Claim,
    Coverage,
    Impact,
    Reach,
    Symbol,
    bucket,
    coverage_verify,
    gating_rows,
    mermaid,
    mermaid_block,
    mermaid_escape,
    mermaid_node,
    parse_impact,
    report,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "impact_partial_failures.json"


@pytest.fixture(name="impact")
def _impact() -> Impact:
    return parse_impact(json.loads(FIXTURE.read_text()))


def by_name(impact: Impact, name: str) -> Reach:
    for hop in impact.reach:
        if name in (hop.symbol.name, hop.symbol.short_name):
            return hop
    raise AssertionError(f"{name} not in reach: {[r.symbol.name for r in impact.reach]}")


def test_endpoint_in_an_unparsed_file_is_downgraded(impact: Impact) -> None:
    """A CALLS edge is only as good as the parse of the file it points into."""
    hop = by_name(impact, "get_sqla_row_level_filters")
    assert hop.relation == "CALLS"
    assert hop.symbol.file_path in impact.partial_files
    assert hop.confidence == HEURISTIC
    assert "parsed with errors" in hop.caveat


def test_co_change_is_never_confirmed(impact: Impact) -> None:
    """FILE_CHANGES_WITH is a commit-history correlation, not a code relation."""
    hop = by_name(impact, "UPDATING.md")
    assert hop.relation == "FILE_CHANGES_WITH"
    assert hop.symbol.file_path not in impact.partial_files
    assert hop.confidence == HEURISTIC


def test_resolved_static_relations_still_confirm(impact: Impact) -> None:
    """Fully-resolved evidence keeps behaving exactly as it did."""
    for name in ("get_guest_rls_filters_str", "GuestTokenRlsRule", "get_rls_cache_key"):
        hop = by_name(impact, name)
        assert hop.confidence == CONFIRMED, name
        assert hop.caveat == ""


def test_reach_ordering_and_locations_are_unchanged(impact: Impact) -> None:
    """Confidence is an added column, not a re-ordering."""
    assert [(r.distance, r.symbol.location) for r in impact.reach] == [
        (1, "UPDATING.md:0"),
        (1, "superset/connectors/sqla/models.py:891"),
        (1, "superset/security/guest_token.py:134"),
        (1, "superset/security/manager.py:5240"),
        (2, "superset/security/manager.py:5245"),
    ]


def test_ci_gates_on_confirmed_reach_only(impact: Impact) -> None:
    """The gate keys off reach confidence, so unresolved reach never fails a build."""
    confirmed_reach = by_name(impact, "get_rls_cache_key").symbol
    downgraded_reach = by_name(impact, "get_sqla_row_level_filters").symbol

    assert gating_rows([Coverage(downgraded_reach, HEURISTIC)]) == []
    assert gating_rows([Coverage(confirmed_reach, CONFIRMED, queried=False)]) == []
    assert gating_rows([Coverage(confirmed_reach, CONFIRMED, ("tests/x.py",))]) == []

    gated = gating_rows([Coverage(confirmed_reach, CONFIRMED)])
    assert [c.symbol.short_name for c in gated] == ["get_rls_cache_key"]


def test_missing_tests_edge_is_reported_as_unresolved_not_as_untested(impact: Impact) -> None:
    """The wording for an absence must never assert the absence is a fact."""
    coverages = [
        Coverage(by_name(impact, "get_rls_cache_key").symbol, CONFIRMED),
        Coverage(by_name(impact, "get_sqla_row_level_filters").symbol, HEURISTIC),
    ]
    text = report("get_guest_rls_filters", Claim(), [], impact.reach, impact, coverages)

    assert all(c.confidence == UNVERIFIED for c in coverages)
    assert "no TESTS edge found — unresolved, verify manually" in text
    # The claim we are not entitled to make.
    assert "untested" not in text.lower()
    assert "not covered" not in text.lower()


def test_every_unresolved_row_prints_its_verification_command(impact: Impact) -> None:
    coverages = [Coverage(by_name(impact, "get_rls_cache_key").symbol, CONFIRMED)]
    text = report("get_guest_rls_filters", Claim(), [], impact.reach, impact, coverages)

    assert "grep -rn get_rls_cache_key tests/" in text
    assert "pytest -k get_rls_cache_key" in text
    # ...and a downgraded reach row says how to confirm the edge itself.
    assert "grep -rn get_sqla_row_level_filters --include='*.py' ." in text
    # A co-change edge is a claim about history, so history is what checks it.
    assert "git log --oneline -10 -- UPDATING.md" in text


def test_report_labels_every_reach_row(impact: Impact) -> None:
    confirmed, silent = bucket(Claim(names={"get_rls_cache_key"}), impact.reach)
    text = report("get_guest_rls_filters", Claim(), confirmed, silent, impact, [])

    for hop in impact.reach:
        line = next(ln for ln in text.splitlines() if hop.symbol.location in ln)
        assert f"[{hop.confidence}" in line, line


def test_completeness_note_scopes_failures_to_the_analysed_language(impact: Impact) -> None:
    """Of two parse failures, one is Python and one is not — say so, don't total them."""
    text = report("get_guest_rls_filters", Claim(), [], impact.reach, impact, [])
    assert "2 file(s) parsed with errors" in text
    assert "1 in Python (the analysed language)" in text
    assert "1 elsewhere (YAML 1)" in text


def test_a_clean_payload_downgrades_nothing() -> None:
    """No partial_failures means the labels are exactly the relation's own standing."""
    payload = json.loads(FIXTURE.read_text())
    payload["partial_failures"] = []
    impact = parse_impact(payload)

    assert impact.partial_files == frozenset()
    assert by_name(impact, "get_sqla_row_level_filters").confidence == CONFIRMED
    assert by_name(impact, "UPDATING.md").confidence == HEURISTIC  # still statistical
    assert report("x", Claim(), [], impact.reach, impact, []).count("Graph completeness") == 0


def test_absence_of_evidence_footer_names_the_known_dynamic_dispatch_gap() -> None:
    text = report("get_guest_rls_filters", Claim(), [], [], Impact(), None)
    assert "security_manager" in text
    assert "superset/jinja_context.py:297" in text
    assert f"[{UNVERIFIED}" in text


def test_symbol_short_name_survives_qualification() -> None:
    assert Symbol("A.B.c", "f.py", 1).short_name == "c"
    assert Symbol("c", "f.py", 1).short_name == "c"


def test_file_nodes_are_not_asked_to_verify_their_own_extension() -> None:
    """A co-changed file has no dotted qualification to strip."""
    assert coverage_verify(Symbol("UPDATING.md", "UPDATING.md", 0, kind="file")) == [
        "grep -rn UPDATING.md tests/",
        "pytest -k UPDATING.md",
    ]


# --- the diagram ------------------------------------------------------------


def node_ids(diagram: str) -> list[str]:
    return re.findall(r"^    ([A-Za-z][A-Za-z0-9_]*)[\[({]", diagram, re.MULTILINE)


def test_mermaid_node_ids_carry_no_path_punctuation(impact: Impact) -> None:
    """`.` `/` `:` in an id is a mermaid parse error, so ids are synthetic."""
    ids = node_ids(mermaid("get_guest_rls_filters", impact))
    assert ids, "no nodes emitted"
    assert all(re.fullmatch(r"[a-z]+[0-9]*", node) for node in ids), ids
    assert len(ids) == len(set(ids)), "duplicate node id"


def test_mermaid_labels_keep_file_line_so_the_picture_stays_checkable(
    impact: Impact,
) -> None:
    diagram = mermaid("get_guest_rls_filters", impact)
    assert '"get_rls_cache_key<br/>superset/security/manager.py:5245"' in diagram


def test_mermaid_arrow_encodes_reach_confidence(impact: Impact) -> None:
    """Thick arrow for a resolved relation, dotted for one we could not resolve."""
    diagram = mermaid("get_guest_rls_filters", impact)
    arrows = {
        node: arrow
        for arrow, node in re.findall(r"focus (==>|-\.->)\|[^|]*\| (n\d+)", diagram)
    }
    labels = {
        node: line for line in diagram.splitlines()
        for node in [line.strip().split("[")[0].split("{")[0].strip()]
        if node.startswith("n")
    }

    confirmed_node = next(n for n, line in labels.items() if "get_rls_cache_key" in line)
    heuristic_node = next(n for n, line in labels.items() if "UPDATING.md" in line)
    downgraded_node = next(
        n for n, line in labels.items() if "get_sqla_row_level_filters" in line
    )

    assert arrows[confirmed_node] == "==>"
    assert arrows[heuristic_node] == "-.->"
    # Downgraded by partial_failures even though the relation is CALLS.
    assert arrows[downgraded_node] == "-.->"


def test_mermaid_border_encodes_coverage_independently_of_reach(impact: Impact) -> None:
    """The two channels must not imply each other."""
    downgraded = by_name(impact, "get_sqla_row_level_filters")
    confirmed = by_name(impact, "get_rls_cache_key")
    diagram = mermaid(
        "get_guest_rls_filters",
        impact,
        [
            # HEURISTIC reach, but a test was found: dotted arrow, solid border.
            Coverage(downgraded.symbol, downgraded.confidence, ("tests/unit_tests/a.py",)),
            # CONFIRMED reach, nothing found: thick arrow, dashed border.
            Coverage(confirmed.symbol, confirmed.confidence),
        ],
    )
    covered_line = next(
        ln for ln in diagram.splitlines() if "get_sqla_row_level_filters" in ln
    )
    unresolved_line = next(ln for ln in diagram.splitlines() if "get_rls_cache_key" in ln)

    assert covered_line.endswith(":::covered")
    assert unresolved_line.endswith(":::unresolved")
    assert "-->|TESTS|" in diagram


def test_mermaid_marks_reach_it_did_not_evaluate_for_coverage(impact: Impact) -> None:
    """No coverage argument is not the same as coverage that came back empty."""
    diagram = mermaid("get_guest_rls_filters", impact)
    assert ":::unresolved" not in diagram
    assert diagram.count(":::nocoverage") == len(impact.reach)


def test_mermaid_truncates_rather_than_drawing_an_unreadable_hairball(
    impact: Impact,
) -> None:
    diagram = mermaid("get_guest_rls_filters", impact, None, max_nodes=2)
    assert "3 more reachable, not drawn" in diagram
    assert len(node_ids(diagram)) == 4  # focus + 2 drawn + the truncation node


def test_mermaid_escapes_label_punctuation_that_breaks_the_parser() -> None:
    hostile = Symbol('weird"name#1', "a/b.py", 7)
    assert mermaid_escape(hostile.display_name) == 'weird#quot;name#35;1'
    assert '"weird#quot;name#35;1<br/>a/b.py:7"' in mermaid_node("n0", hostile)


def test_mermaid_names_a_file_node_by_basename_not_by_extension() -> None:
    node = mermaid_node("n0", Symbol("UPDATING.md", "UPDATING.md", 0, "file"), "file")
    assert node == '    n0{{"UPDATING.md"}}'
    nested = mermaid_node(
        "n1", Symbol("manager_test.py", "tests/unit_tests/manager_test.py", 0, "file"), "file"
    )
    assert '"manager_test.py<br/>tests/unit_tests/manager_test.py"' in nested


def test_mermaid_block_is_pasteable_markdown(impact: Impact) -> None:
    block = mermaid_block("get_guest_rls_filters", impact)
    assert block.startswith("```mermaid\ngraph LR")
    assert "\n```\n" in block
    assert "**Arrow**" in block and "**Node border**" in block
    # The legend must not restate an absence as a coverage claim.
    assert "untested" not in block.lower()
