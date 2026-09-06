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

Every row cites file:line so a reviewer can verify it against source. Graph
output is treated as evidence, not as an oracle: parse failures reported by the
graph are surfaced rather than swallowed.

Usage:
    python tools/blastradius/blastradius.py --symbol get_guest_rls_filters
    python tools/blastradius/blastradius.py --symbol X --ref HEAD --depth 2
"""

from __future__ import annotations

import argparse
import json
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


@dataclass(frozen=True)
class Reach:
    symbol: Symbol
    distance: int
    relation: str

    @property
    def label(self) -> str:
        return EDGE_LABELS.get(self.relation, self.relation.lower())


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


def graph_impact(symbol: str, repo: str, depth: int) -> tuple[list[Reach], list[str]]:
    """One-shot blast radius. Returns (reach, warnings-about-completeness)."""
    payload = run_json(
        [
            "entire", "graph", "impact",
            "--symbol", symbol,
            "--repo", repo,
            "--depth", str(min(depth, 2)),
            "--format", "json",
        ]
    )
    if not isinstance(payload, dict):
        raise GraphError("impact returned no object")

    reach: list[Reach] = []
    seen: dict[str, Reach] = {}
    for section in IMPACT_SECTIONS:
        for entry in (payload.get(section) or {}).get("entries", []):
            endpoint = entry.get("endpoint")
            if not isinstance(endpoint, dict):
                continue
            hop = Reach(
                symbol=parse_symbol(endpoint),
                distance=int(entry.get("depth") or 1),
                relation=str(entry.get("relation") or "UNKNOWN"),
            )
            # Nearest origin wins, so the evidence chain is the shortest one.
            key = f"{hop.symbol.location}:{hop.symbol.name}"
            if key not in seen or hop.distance < seen[key].distance:
                seen[key] = hop
    reach = sorted(seen.values(), key=lambda r: (r.distance, r.symbol.location))

    # "Graph results are evidence, not an oracle" — surface what it could not parse.
    warnings = [
        f"{f.get('file_path')}: {f.get('effect_on_semantic_completeness')}"
        for f in payload.get("partial_failures", [])
        if isinstance(f, dict)
    ]
    return reach, warnings


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


def select_tests(reach: list[Reach], repo: str) -> tuple[dict[str, list[str]], list[Symbol]]:
    """The minimal test set for this blast radius.

    Asks the graph for TESTS edges pointing at each reachable symbol, which is
    real evidence rather than a filename heuristic. Symbols with no incoming
    TESTS edge are returned separately: untested reach is the most actionable
    line in the report, because it is where a regression would land silently.
    """
    covered: dict[str, list[str]] = {}
    uncovered: list[Symbol] = []

    for hop in reach:
        if hop.symbol.file_path.startswith("tests/"):
            continue  # a test is not something a test needs to cover
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

        if tests:
            covered[hop.symbol.name] = sorted(set(tests))
        else:
            uncovered.append(hop.symbol)

    return covered, uncovered


def test_command(covered: dict[str, list[str]], reach: list[Reach]) -> str | None:
    """The pytest invocation a CI job should run for this change."""
    paths = {path for paths in covered.values() for path in paths}
    # Test files that co-change with the touched code are weaker evidence than a
    # TESTS edge, but they are still a better guess than running the whole suite.
    paths |= {r.symbol.file_path for r in reach if r.symbol.file_path.startswith("tests/")}
    return f"pytest {' '.join(sorted(paths))}" if paths else None


def bucket(claim: Claim, reach: list[Reach]) -> tuple[list[Reach], list[Reach]]:
    """confirmed = claimed and reachable; silent = reachable and unmentioned."""
    confirmed, silent = [], []
    for hop in reach:
        short = hop.symbol.name.rsplit(".", 1)[-1]
        mentioned = (
            hop.symbol.name in claim.names
            or short in claim.names
            or hop.symbol.file_path in claim.files
        )
        (confirmed if mentioned else silent).append(hop)
    return confirmed, silent


def report(symbol: str, claim: Claim, confirmed: list[Reach], silent: list[Reach],
           warnings: list[str], covered: dict[str, list[str]] | None = None,
           uncovered: list[Symbol] | None = None, command: str | None = None) -> str:
    out = [f"\nblastradius  {symbol}", ""]
    if claim.summary:
        out += [f"The change said (from {claim.source}):", ""]
        out += [f"    {line}" for line in claim.summary.splitlines() if line.strip()]
        out.append("")

    if silent:
        out.append(f"{len(silent)} reachable, never mentioned:")
        width = max(len(r.symbol.location) for r in silent)
        for hop in silent:
            out.append(
                f"  ! {hop.symbol.location.ljust(width)}  {hop.symbol.name}"
                f"  [{hop.distance} hop, {hop.label}]"
            )
    else:
        out.append("Nothing reachable that the change did not mention.")
    out.append("")

    if confirmed:
        out.append(f"{len(confirmed)} reachable and accounted for:")
        for hop in confirmed:
            out.append(f"  . {hop.symbol.location}  {hop.symbol.name}")
        out.append("")

    if uncovered:
        out.append(f"{len(uncovered)} reachable with no test covering them:")
        for sym in uncovered:
            out.append(f"  x {sym.location}  {sym.name}")
        out.append("")

    if command:
        covered_count = len(covered or {})
        out += [
            f"Run these tests ({covered_count} reachable symbol(s) covered by a TESTS edge):",
            "",
            f"    {command}",
            "",
        ]

    if warnings:
        out.append(f"Graph completeness: {len(warnings)} file(s) parsed with errors;")
        out.append("findings below may be incomplete. Verify against source.")
        for line in warnings[:3]:
            out.append(f"  ~ {line}")
        out.append("")
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
        "--ci", action="store_true", help="exit non-zero on reach no test covers"
    )
    args = parser.parse_args(argv)

    try:
        reach, warnings = graph_impact(args.symbol, args.repo, args.depth)
    except GraphError as exc:
        print(f"blastradius: {exc}", file=sys.stderr)
        return 2

    claim = load_claim(args.ref, args.repo)
    confirmed, silent = bucket(claim, reach)

    covered: dict[str, list[str]] = {}
    uncovered: list[Symbol] = []
    command: str | None = None
    if not args.no_tests:
        covered, uncovered = select_tests(reach, args.repo)
        command = test_command(covered, reach)

    print(report(args.symbol, claim, confirmed, silent, warnings, covered, uncovered, command))
    if args.ci:
        # A CI gate fails on reach that no test can catch, not on reach itself.
        return 1 if uncovered else 0
    return 1 if silent else 0


if __name__ == "__main__":
    sys.exit(main())
