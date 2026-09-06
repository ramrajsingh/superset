#!/usr/bin/env python3
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
"""blastradius — claim vs. reality for AI-authored commits.

Reads what a change *said* it did (an Entire Checkpoint, or the commit itself)
and what the Entire Graph says it *reaches*, then reports the gap:

    confirmed  claimed and reachable
    silent     reachable, never mentioned   <- the finding
    overclaim  claimed, absent from the graph

Every row cites file:line so a reviewer can verify it against source, and every
row carries a confidence label, because the graph is evidence and not an oracle:

    CONFIRMED   a resolved static relation whose endpoint file parsed cleanly
    HEURISTIC   a statistical co-change edge, or an endpoint in a file the graph
                failed to parse — real signal, weaker evidence
    UNVERIFIED  an *absence*. The graph found no edge; that is not proof that no
                edge exists. Every UNVERIFIED row prints the command that
                settles it.

Usage:
    python tools/blastradius/blastradius.py --symbol get_guest_rls_filters
    python tools/blastradius/blastradius.py --symbol X --ref HEAD --depth 2
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

# Relation names as the graph actually emits them (uppercase, singular key).
EDGE_LABELS = {
    "CALLS": "call",
    "ASYNC_CALLS": "call",
    "PARAM_TYPE": "type consumer",
    "USES_TYPE": "type consumer",
    "RETURNS_TYPE": "type consumer",
    "DATA_FLOWS": "data flow",
    "FILE_CHANGES_WITH": "co-change",
    "HANDLES_ROUTE": "route",
}

# Sections of `entire graph impact` that describe what a change REACHES.
# `callees` is deliberately excluded: those are what the symbol depends on,
# not what breaks when it changes.
IMPACT_SECTIONS = ("callers", "type_consumers", "data_flows", "co_changes")

CONFIRMED = "CONFIRMED"
HEURISTIC = "HEURISTIC"
UNVERIFIED = "UNVERIFIED"

# Relations the graph resolves structurally, by reading the source. ASYNC_CALLS
# is the async spelling of CALLS and is resolved the same way.
STRUCTURAL_RELATIONS = frozenset(
    {"CALLS", "ASYNC_CALLS", "PARAM_TYPE", "USES_TYPE", "RETURNS_TYPE", "DATA_FLOWS"}
)

# Relations derived from commit history rather than from code. A file that
# changes alongside another file is a correlation, not a structural fact.
STATISTICAL_RELATIONS = frozenset({"FILE_CHANGES_WITH"})

# A diagram past this many nodes stops being readable, and an unreadable
# picture is a worse failure than a table.
MERMAID_MAX_NODES = 40

# Three variables, three loci, each encoded twice so colour is never the only
# carrier: reach confidence lives on the EDGE (weight + hue), coverage on the
# node BORDER (hue + dash), blast radius on the node FILL (alpha).
#
# Fills are alpha over whatever the renderer's own background is, never opaque:
# the diagram is embedded in Markdown that may be rendered light or dark, and a
# solid fill would fight the theme.
# Border — what we know about tests.
MERMAID_BORDERS = {
    "covered": "stroke:#3fb950,stroke-width:2px",
    "unresolved": "stroke:#8b949e,stroke-width:2px,stroke-dasharray:3 4",
    "nocoverage": "stroke:#8b949e,stroke-width:1px,stroke-dasharray:1 3",
}

# Fill — distance from the change, denser nearer the epicentre. Alpha over the
# renderer's own background, never opaque: this is embedded in Markdown that may
# be rendered light or dark, and a solid fill would fight the theme.
MERMAID_HOP_FILLS = {1: "fill:#58a6ff38", 2: "fill:#58a6ff14"}

# Edge hue by reach confidence, applied by index through `linkStyle`.
MERMAID_EDGE_COLOURS = {
    CONFIRMED: "#3fb950",
    HEURISTIC: "#d29922",
    "TESTS": "#bc8cff",
}

MERMAID_LEGEND = (
    "**Edge** — how we know the change reaches it: "
    "thick green `==>` CONFIRMED (resolved static relation) · "
    "thin amber `-.->` HEURISTIC (co-change, or an endpoint in a file the graph "
    "could not parse).",
    "**Node border** — what we know about tests: "
    "solid green a TESTS edge was found · "
    "dashed grey no TESTS edge found (unresolved, not a claim of no coverage) · "
    "faint dotted coverage not evaluated.",
    "**Node fill** — blast radius: denser fill is one hop from the change, "
    "fainter is two. Distance, not danger — a two-hop reach can matter more than "
    "a one-hop one.",
    "Every variable is encoded twice (weight and hue, dash and hue, alpha), so "
    "no reading depends on colour alone. A test file reached by a `TESTS` edge "
    "is a purple test node; a test file that merely *co-changes* is a co-change "
    "node, because that is the weaker claim.",
)


# Static attribution cannot follow dispatch through a module-level singleton.
# This is the case we hit in this repo, kept concrete so the caveat is checkable
# rather than boilerplate.
DYNAMIC_DISPATCH_NOTE = (
    "Calls dispatched through a module-level singleton are not attributed. "
    "Measured case in this repo: callers of get_guest_rls_filters at "
    "superset/jinja_context.py:297 and superset/connectors/sqla/models.py:891 "
    "reach it via the `security_manager` singleton and carry no CALLS edge."
)


@dataclass(frozen=True)
class Symbol:
    """A graph node, with the jump target that makes a finding verifiable."""

    name: str
    file_path: str
    line: int
    kind: str = "symbol"

    @property
    def location(self) -> str:
        return f"{self.file_path}:{self.line}"

    @property
    def short_name(self) -> str:
        return self.name.rsplit(".", 1)[-1]

    @property
    def display_name(self) -> str:
        """How to name this node to a human.

        A file node has no dotted qualification to strip — splitting one would
        leave its extension, so `UPDATING.md` would read as `md`.
        """
        if self.kind == "file":
            return self.file_path.rsplit("/", 1)[-1] or self.name
        return self.short_name


@dataclass(frozen=True)
class Reach:
    symbol: Symbol
    distance: int
    relation: str
    confidence: str = CONFIRMED
    caveat: str = ""

    @property
    def label(self) -> str:
        return EDGE_LABELS.get(self.relation, self.relation.lower())


@dataclass(frozen=True)
class Coverage:
    """What the graph could say about tests for one reachable symbol."""

    symbol: Symbol
    reach_confidence: str
    tests: tuple[str, ...] = ()
    queried: bool = True  # False when the TESTS query itself failed to answer

    @property
    def confidence(self) -> str:
        # A TESTS edge is positive evidence, and inherits the reach's standing.
        # No edge is an absence, which is never better than UNVERIFIED.
        if self.tests:
            return CONFIRMED if self.reach_confidence == CONFIRMED else HEURISTIC
        return UNVERIFIED


@dataclass
class Impact:
    """Parsed `entire graph impact` output, with its own completeness caveats."""

    reach: list[Reach] = field(default_factory=list)
    partial_files: frozenset[str] = frozenset()
    warnings: list[str] = field(default_factory=list)
    scope: dict[str, Any] = field(default_factory=dict)


@dataclass
class Claim:
    """What the change said about itself."""

    summary: str = ""
    files: tuple[str, ...] = ()
    source: str = "commit"
    names: set[str] = field(default_factory=set)


class GraphError(RuntimeError):
    """The graph could not answer."""


def run_json(argv: list[str], timeout: float = 900.0) -> Any:
    """Run a command and parse JSON, or raise GraphError with the stderr."""
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as exc:
        raise GraphError(f"{argv[0]} not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GraphError(f"timed out: {' '.join(argv)}") from exc
    if proc.returncode != 0:
        raise GraphError(f"{' '.join(argv)} exited {proc.returncode}: {proc.stderr.strip()}")
    try:
        return json.loads(proc.stdout or "null")
    except json.JSONDecodeError as exc:
        raise GraphError(f"non-JSON output from {' '.join(argv)}") from exc


def git(*args: str) -> str:
    proc = subprocess.run(
        ("git", *args), capture_output=True, text=True, check=False, timeout=30
    )
    return proc.stdout if proc.returncode == 0 else ""


def parse_symbol(raw: dict[str, Any]) -> Symbol:
    """The graph emits file_path/start_line, and qualified_name where it has one."""
    return Symbol(
        name=str(raw.get("qualified_name") or raw.get("name") or "<anonymous>"),
        file_path=str(raw.get("file_path") or "<unknown>"),
        line=int(raw.get("start_line") or raw.get("line") or 0),
        kind=str(raw.get("kind") or "symbol"),
    )


def classify_reach(relation: str, file_path: str, partial_files: frozenset[str]) -> tuple[str, str]:
    """Confidence for one edge, and the reason when it is not CONFIRMED.

    The partial-parse check comes first on purpose: a CALLS edge is only as good
    as the parse of the file it points into, so an endpoint the graph could not
    read cleanly is downgraded no matter how structural the relation looks.
    """
    if file_path in partial_files:
        return HEURISTIC, "endpoint is in a file the graph parsed with errors"
    if relation in STRUCTURAL_RELATIONS:
        return CONFIRMED, ""
    if relation in STATISTICAL_RELATIONS:
        return HEURISTIC, "co-change is a commit-history correlation, not a code relation"
    return HEURISTIC, f"{relation} is not a resolved static relation"


def parse_impact(payload: Any) -> Impact:
    """Turn one `entire graph impact` payload into labelled reach.

    Pure, so a saved payload — including one with partial_failures — can be
    replayed in a test without the graph.
    """
    if not isinstance(payload, dict):
        raise GraphError("impact returned no object")

    failures = [f for f in payload.get("partial_failures", []) if isinstance(f, dict)]
    partial_files = frozenset(str(f.get("file_path")) for f in failures if f.get("file_path"))

    seen: dict[str, Reach] = {}
    for section in IMPACT_SECTIONS:
        for entry in (payload.get(section) or {}).get("entries", []):
            endpoint = entry.get("endpoint")
            if not isinstance(endpoint, dict):
                continue
            symbol = parse_symbol(endpoint)
            relation = str(entry.get("relation") or "UNKNOWN")
            confidence, caveat = classify_reach(relation, symbol.file_path, partial_files)
            hop = Reach(
                symbol=symbol,
                distance=int(entry.get("depth") or 1),
                relation=relation,
                confidence=confidence,
                caveat=caveat,
            )
            # Nearest origin wins, so the evidence chain is the shortest one.
            key = f"{hop.symbol.location}:{hop.symbol.name}"
            if key not in seen or hop.distance < seen[key].distance:
                seen[key] = hop

    warnings = [
        f"{f.get('file_path')}: {f.get('effect_on_semantic_completeness')}" for f in failures
    ]
    scope = payload.get("completeness_scope")
    return Impact(
        reach=sorted(seen.values(), key=lambda r: (r.distance, r.symbol.location)),
        partial_files=partial_files,
        warnings=warnings,
        scope=scope if isinstance(scope, dict) else {},
    )


def graph_impact(symbol: str, repo: str, depth: int) -> Impact:
    """One-shot blast radius, with the graph's own completeness caveats kept."""
    return parse_impact(
        run_json(
            [
                "entire", "graph", "impact",
                "--symbol", symbol,
                "--repo", repo,
                "--depth", str(min(depth, 2)),
                "--format", "json",
            ]
        )
    )


def load_claim(ref: str, repo: str) -> Claim:
    """Prefer the checkpoint's own account; fall back to the commit.

    A checkpoint records why a change happened. When none is linked yet, the
    commit message and its file list are the only claim on record — weaker
    evidence, and the report says which one it used.
    """
    try:
        payload = run_json(
            ["entire", "checkpoint", "explain", ref, "--format", "json"], timeout=60
        )
        if isinstance(payload, dict) and payload:
            summary = str(
                payload.get("summary") or payload.get("description") or payload.get("title") or ""
            )
            raw_files = payload.get("files") or payload.get("files_touched") or []
            files = tuple(
                str(f.get("path") if isinstance(f, dict) else f) for f in raw_files
            )
            if summary or files:
                return Claim(summary, files, "checkpoint", claimed_names(summary))
    except GraphError:
        pass

    summary = git("-C", repo, "log", "-1", "--format=%B", ref).strip()
    files = tuple(
        p for p in git("-C", repo, "show", "--name-only", "--format=", ref).splitlines() if p
    )
    return Claim(summary, files, "commit message", claimed_names(summary))


def claimed_names(text: str) -> set[str]:
    """Code-shaped tokens only.

    A bare English word in prose is not a claim about a symbol, so only tokens
    that look like identifiers count. Deliberately structural: no model call,
    so the tool keeps working with no network and no API key.
    """
    names: set[str] = set()
    for token in text.replace("(", " ").replace(")", " ").replace("`", " ").split():
        token = token.strip(".,:;'\"")
        if "_" in token or "." in token or (token[:1].isalpha() and not token.islower()):
            names.add(token)
            names.add(token.rsplit(".", 1)[-1])
    return names


def select_tests(reach: list[Reach], repo: str) -> list[Coverage]:
    """What the graph can say about tests for each reachable symbol.

    Asks for TESTS edges pointing at the symbol, which is real evidence rather
    than a filename heuristic. A symbol with no incoming edge is *not* reported
    as untested — the graph missing an edge and the edge not existing are
    different things, and only the second one is a coverage gap. Those rows come
    back UNVERIFIED, with the command that settles them.
    """
    coverages: list[Coverage] = []

    for hop in reach:
        if hop.symbol.file_path.startswith("tests/"):
            continue  # a test is not something a test needs to cover
        if hop.symbol.kind == "file":
            continue  # a co-changed file is not a symbol a TESTS edge can point at
        try:
            payload = run_json(
                [
                    "entire", "graph", "neighbors",
                    "--symbol", f"{hop.symbol.file_path}:{hop.symbol.line}",
                    "--repo", repo,
                    "--relation", "TESTS",
                    "--direction", "in",
                    "--format", "json",
                ],
                timeout=120,
            )
        except GraphError:
            # The query itself failed, so we know nothing either way. Keep the
            # row so the reviewer sees the hole instead of a shorter report.
            coverages.append(Coverage(hop.symbol, hop.confidence, (), queried=False))
            continue

        tests: list[str] = []
        for section in ("neighbors", "incoming", "in", "entries"):
            block = payload.get(section) if isinstance(payload, dict) else None
            entries = block.get("entries", []) if isinstance(block, dict) else (
                block if isinstance(block, list) else []
            )
            for entry in entries:
                endpoint = entry.get("endpoint", entry) if isinstance(entry, dict) else {}
                path = endpoint.get("file_path")
                if path:
                    tests.append(str(path))

        coverages.append(Coverage(hop.symbol, hop.confidence, tuple(sorted(set(tests)))))

    return coverages


def gating_rows(coverages: list[Coverage]) -> list[Coverage]:
    """Rows `--ci` is allowed to fail on.

    The gate keys off the *reach* confidence: we act only where the graph
    resolved the path from the change to the symbol structurally, and then
    found no test attached to it. Reach we could not resolve — a co-change
    edge, an endpoint in an unparsed file, a TESTS query that errored — is
    reported and never gated, because failing a build on evidence we would not
    defend teaches people to ignore the gate.
    """
    return [
        c for c in coverages if c.reach_confidence == CONFIRMED and not c.tests and c.queried
    ]


def coverage_verify(symbol: Symbol) -> list[str]:
    """The two commands that settle an UNVERIFIED coverage row."""
    # A file node has no dotted qualification to strip; splitting one would grep
    # for its extension.
    name = shlex.quote(symbol.display_name)
    return [f"grep -rn {name} tests/", f"pytest -k {name}"]


def grep_command(name: str) -> str:
    return f"grep -rn {shlex.quote(name)} --include='*.py' ."


def reach_verify(hop: Reach) -> str:
    """The command that settles a HEURISTIC reach row.

    A co-change edge is a claim about commit history, so history is what checks
    it; every other downgraded edge is a claim about code, so grep checks it.
    """
    if hop.relation in STATISTICAL_RELATIONS or hop.symbol.kind == "file":
        return f"git log --oneline -10 -- {shlex.quote(hop.symbol.file_path)}"
    return grep_command(hop.symbol.short_name)


def test_command(coverages: list[Coverage], reach: list[Reach]) -> str | None:
    """The pytest invocation a CI job should run for this change."""
    paths = {path for c in coverages for path in c.tests}
    # Test files that co-change with the touched code are weaker evidence than a
    # TESTS edge, but they are still a better guess than running the whole suite.
    paths |= {r.symbol.file_path for r in reach if r.symbol.file_path.startswith("tests/")}
    return f"pytest {' '.join(sorted(paths))}" if paths else None


def mermaid_classdefs() -> list[str]:
    """One class per (border, fill) pair, rather than two classes per node.

    Mermaid joins a multi-class assignment into a single literal class attribute
    — `class="node default covered,hop1"` — so neither `.covered` nor `.hop1`
    matches and the styling silently does nothing. Composing the pairs up front
    keeps every node on exactly one class.
    """
    defs = [
        "classDef focusnode stroke:#58a6ff,stroke-width:3px,fill:#58a6ff66",
        "classDef tests stroke:#bc8cff,stroke-width:2px,fill:#bc8cff20",
    ]
    defs += [
        f"classDef {border}{hop} {style},{fill}"
        for border, style in MERMAID_BORDERS.items()
        for hop, fill in MERMAID_HOP_FILLS.items()
    ]
    return defs


def mermaid_escape(text: str) -> str:
    """Mermaid label text, as numeric entities.

    `#` is substituted first because every escape we emit starts with one.
    Angle brackets matter more than they look: mermaid parses labels as HTML, so
    an unescaped `Mod.fn<T>` renders as `fn` — the type parameter is swallowed
    with no error at all. Silent character loss is the one failure this tool
    cannot afford.
    """
    return (
        text.replace("#", "#35;")
        .replace('"', "#quot;")
        .replace("<", "#60;")
        .replace(">", "#62;")
        .replace("\n", " ")
    )


def mermaid_node(node_id: str, symbol: Symbol, shape: str = "square") -> str:
    """A node whose label carries file:line, so the picture stays checkable."""
    label = mermaid_escape(symbol.display_name)
    detail = symbol.location if symbol.line else symbol.file_path
    if detail not in ("", "<unknown>", symbol.display_name):
        label += f"<br/>{mermaid_escape(detail)}"
    open_, close = {
        "focus": ("((", "))"),
        "file": ("{{", "}}"),
        "test": ("[/", "/]"),
        "square": ("[", "]"),
    }[shape]
    return f'    {node_id}{open_}"{label}"{close}'


def mermaid(
    symbol: str,
    impact: Impact,
    coverages: list[Coverage] | None = None,
    max_nodes: int = MERMAID_MAX_NODES,
) -> str:
    """The blast radius as a mermaid flowchart.

    Two independent visual channels, so the confidence model survives the
    translation into a picture: the *arrow* says how well we know the change
    reaches a symbol, and the *node border* says what we know about tests for
    it. Neither is allowed to imply the other.

    Pure — it renders a parsed payload and never queries the graph.
    """
    by_location = {c.symbol.location: c for c in coverages or []}
    shown = impact.reach[:max_nodes]

    lines = ["graph LR"]
    lines += [f"    {d}" for d in mermaid_classdefs()]
    lines.append("")
    lines.append(mermaid_node("focus", Symbol(symbol, "", 0), "focus") + ":::focusnode")

    test_ids: dict[str, str] = {}
    edges: list[str] = []
    edge_colours: list[str] = []

    for index, hop in enumerate(shown):
        node_id = f"n{index}"
        shape = "file" if hop.symbol.kind == "file" else "square"

        coverage = by_location.get(hop.symbol.location)
        if coverage is None:
            border = "nocoverage"
        elif coverage.tests:
            border = "covered"
        else:
            border = "unresolved"

        hop_band = min(max(hop.distance, 1), max(MERMAID_HOP_FILLS))
        lines.append(mermaid_node(node_id, hop.symbol, shape) + f":::{border}{hop_band}")

        arrow = "==>" if hop.confidence == CONFIRMED else "-.->"
        edges.append(f"    focus {arrow}|{mermaid_escape(hop.label)}| {node_id}")
        edge_colours.append(hop.confidence)

        for path in coverage.tests if coverage else ():
            if path not in test_ids:
                test_ids[path] = f"t{len(test_ids)}"
                lines.append(
                    mermaid_node(test_ids[path], Symbol(path, path, 0, "file"), "test")
                    + ":::tests"
                )
            edges.append(f"    {test_ids[path]} -->|TESTS| {node_id}")
            edge_colours.append("TESTS")

    omitted = len(impact.reach) - len(shown)
    if omitted > 0:
        lines.append(
            f'    more["… {omitted} more reachable, not drawn"]:::nocoverage2'
        )
        edges.append("    focus -.->|truncated| more")
        edge_colours.append(HEURISTIC)

    # linkStyle is index-based, so it is only safe because we emitted the edges.
    link_styles = []
    for kind, colour in MERMAID_EDGE_COLOURS.items():
        indices = [i for i, k in enumerate(edge_colours) if k == kind]
        if indices:
            width = "2.5px" if kind == CONFIRMED else "1.5px"
            link_styles.append(
                f"    linkStyle {','.join(str(i) for i in indices)} "
                f"stroke:{colour},stroke-width:{width}"
            )

    return "\n".join(lines + [""] + edges + link_styles)


def mermaid_block(symbol: str, impact: Impact, coverages: list[Coverage] | None = None) -> str:
    """The fenced diagram plus its legend, ready to paste into Markdown."""
    return "\n".join(
        [f"```mermaid", mermaid(symbol, impact, coverages), "```", ""]
        + [f"- {line}" for line in MERMAID_LEGEND]
        + [""]
    )


def bucket(claim: Claim, reach: list[Reach]) -> tuple[list[Reach], list[Reach]]:
    """confirmed = claimed and reachable; silent = reachable and unmentioned."""
    confirmed: list[Reach] = []
    silent: list[Reach] = []
    for hop in reach:
        mentioned = (
            hop.symbol.name in claim.names
            or hop.symbol.short_name in claim.names
            or hop.symbol.file_path in claim.files
        )
        (confirmed if mentioned else silent).append(hop)
    return confirmed, silent


def tag(confidence: str) -> str:
    return f"[{confidence:<10}]"


def completeness_lines(impact: Impact) -> list[str]:
    """What the graph admits it could not read, scoped to the language analysed."""
    if not impact.warnings:
        return []
    scope = impact.scope
    language = str(scope.get("language") or "")
    elsewhere = int(scope.get("other_language_failures") or 0)
    in_language = len(impact.warnings) - elsewhere

    out = [f"Graph completeness: {len(impact.warnings)} file(s) parsed with errors."]
    if language:
        others = ", ".join(str(x) for x in scope.get("other_failure_languages") or [])
        out.append(
            f"  {in_language} in {language} (the analysed language)"
            + (f"; {elsewhere} elsewhere ({others})" if elsewhere else "")
        )
        if in_language <= 0:
            out.append(
                f"  No {language} file failed to parse, so no row above was downgraded for it."
            )
    out += [f"  ~ {line}" for line in impact.warnings[:3]]
    if len(impact.warnings) > 3:
        out.append(f"  ~ ... and {len(impact.warnings) - 3} more")
    return out


def report(
    symbol: str,
    claim: Claim,
    confirmed: list[Reach],
    silent: list[Reach],
    impact: Impact,
    coverages: list[Coverage] | None = None,
    command: str | None = None,
) -> str:
    out = [f"\nblastradius  {symbol}", ""]
    out += [
        "Confidence  CONFIRMED  resolved static relation, endpoint file parsed cleanly",
        "            HEURISTIC  co-change, or endpoint in a file the graph could not parse",
        "            UNVERIFIED an absence — the graph found nothing, which is not proof",
        "                       that nothing is there. Verify command printed per row.",
        "",
    ]

    if claim.summary:
        out += [f"The change said (from {claim.source}):", ""]
        out += [f"    {line}" for line in claim.summary.splitlines() if line.strip()]
        out.append("")

    if silent:
        out.append(f"{len(silent)} reachable, never mentioned:")
        width = max(len(r.symbol.location) for r in silent)
        for hop in silent:
            out.append(
                f"  ! {tag(hop.confidence)} {hop.symbol.location.ljust(width)}  "
                f"{hop.symbol.name}  [{hop.distance} hop, {hop.label}]"
            )
            if hop.confidence != CONFIRMED:
                out.append(f"        {hop.caveat}; confirm with:")
                out.append(f"        {reach_verify(hop)}")
    else:
        out.append(
            "No reachable symbol went unmentioned in what the graph resolved "
            f"{tag(UNVERIFIED)}"
        )
        out.append("  — an edge the graph did not resolve would not appear here either.")
    out.append("")

    if confirmed:
        out.append(f"{len(confirmed)} reachable and accounted for:")
        for hop in confirmed:
            out.append(
                f"  . {tag(hop.confidence)} {hop.symbol.location}  {hop.symbol.name}"
            )
        out.append("")

    if coverages is not None:
        unresolved = [c for c in coverages if not c.tests]
        covered = [c for c in coverages if c.tests]
        gated = gating_rows(coverages)
        if covered:
            out.append(f"{len(covered)} reachable symbol(s) with a TESTS edge:")
            for c in covered:
                out.append(f"  . {tag(c.confidence)} {c.symbol.location}  {c.symbol.name}")
            out.append("")
        if unresolved:
            out.append(
                f"{len(unresolved)} reachable symbol(s) with no TESTS edge found — "
                "unresolved, verify manually:"
            )
            for c in unresolved:
                gate = " (gates --ci)" if c in gated else ""
                out.append(
                    f"  ? {tag(c.confidence)} {c.symbol.location}  {c.symbol.name}"
                    f"  [reach {c.reach_confidence}]{gate}"
                )
                if not c.queried:
                    out.append("        the TESTS query itself failed; nothing is known here")
                for line in coverage_verify(c.symbol):
                    out.append(f"        {line}")
            out.append("")

    if command:
        out += [
            "Run these tests (from TESTS edges, plus test files that co-change):",
            "",
            f"    {command}",
            "",
        ]

    out += [
        f"Absence of evidence {tag(UNVERIFIED)}",
        f"  {DYNAMIC_DISPATCH_NOTE}",
        f"  Check this run against source: {grep_command(symbol)}",
        "",
    ]

    out += completeness_lines(impact)
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbol", required=True, help="symbol the change touched")
    parser.add_argument("--ref", default="HEAD", help="checkpoint id or commit (default: HEAD)")
    parser.add_argument("--repo", default=".", help="repository path")
    parser.add_argument("--depth", type=int, default=2, help="caller depth (max 2)")
    parser.add_argument(
        "--no-tests", action="store_true", help="skip test selection (faster)"
    )
    parser.add_argument(
        "--from-json",
        metavar="PATH",
        help="replay a saved `entire graph impact` payload instead of querying "
             "the graph; with --no-tests this makes the whole run offline",
    )
    parser.add_argument(
        "--mermaid",
        action="store_true",
        help="emit the blast radius as a fenced mermaid diagram instead of the "
             "text report (exit codes are unchanged)",
    )
    parser.add_argument(
        "--ci",
        action="store_true",
        help="exit non-zero on CONFIRMED reach with no TESTS edge found",
    )
    args = parser.parse_args(argv)

    try:
        if args.from_json:
            with open(args.from_json, encoding="utf-8") as handle:
                impact = parse_impact(json.load(handle))
        else:
            impact = graph_impact(args.symbol, args.repo, args.depth)
    except (GraphError, OSError, json.JSONDecodeError) as exc:
        print(f"blastradius: {exc}", file=sys.stderr)
        return 2

    claim = load_claim(args.ref, args.repo)
    confirmed, silent = bucket(claim, impact.reach)

    coverages: list[Coverage] | None = None
    command: str | None = None
    if not args.no_tests:
        coverages = select_tests(impact.reach, args.repo)
        command = test_command(coverages, impact.reach)

    if args.mermaid:
        print(mermaid_block(args.symbol, impact, coverages))
    else:
        print(report(args.symbol, claim, confirmed, silent, impact, coverages, command))

    if args.ci:
        # The gate fires on reach the graph resolved structurally and found no
        # test for. Reach we could not resolve is reported, never gated.
        gated = gating_rows(coverages or [])
        if gated:
            print(
                f"blastradius: {len(gated)} symbol(s) with CONFIRMED reach and no TESTS "
                "edge found. Gate keyed on reach confidence, not on a claim that these "
                "symbols are uncovered — verify with the commands above.",
                file=sys.stderr,
            )
            return 1
        return 0
    return 1 if silent else 0


if __name__ == "__main__":
    sys.exit(main())
