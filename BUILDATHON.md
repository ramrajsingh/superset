# blastradius

| | |
|---|---|
| Track | 2 — Build with Graph Intelligence |
| Fork | `github.com/ramrajsingh/superset` |
| Branch | **`blastradius`** — `master` is protected on the fork, so all work lands here |
| Entire mirror | `entire://aws-ap-south-1.entire.io/gh/ramrajsingh/superset` (India region) |
| Final commit | _TODO: update after the Curveball work lands_ |
| Implementation | `tools/blastradius/blastradius.py` |
| Graph evidence | `evidence/` |

## One-sentence summary

Given a change an agent made, blastradius reports what the change actually reaches — and which tests can catch it — by checking the agent's own account of its work against structural evidence from the Entire Graph.

## Problem, intended user and why it matters

The user is whoever has to approve a diff an agent wrote, and whoever owns the CI bill for it.

An agent finishes a change and describes it in its own words: *"refactored the auth middleware, nothing else affected."* A reviewer has no cheap way to check that sentence. The transcript is tens of thousands of tokens; the diff shows three files. Whether the change reaches a security-relevant cache key two call-hops away is exactly what the summary is least likely to mention and the reviewer is least able to see.

The same gap costs money downstream. Not knowing what a change reaches, CI runs everything — on a repo this size that is the difference between a targeted run and a full suite.

So there are two questions with one answer: *what did this change actually touch*, and *which tests can catch it*.

## Selected Entire track and why Entire is essential

**Track 2 — Build with Graph Intelligence.**

Both halves of the product are Entire surfaces, and neither is decoration:

- **Checkpoints supply the claim.** The agent's own summary of what it did is not in the Git diff. Without it there is nothing to check.
- **The Graph supplies the reality.** Callers, type consumers, data flows and co-change relations are what the change reaches. Without it there is nothing to check against.
- **Graph `TESTS` edges supply the test selection.** Coverage becomes a graph relation rather than a filename heuristic.

The report is a set difference:

| Bucket | Meaning |
|---|---|
| Confirmed | The change mentioned it and the graph agrees. |
| **Silent reach** | Reachable from the change, never mentioned. |
| **Untested reach** | Reachable, and no `TESTS` edge points at it. |

## Architecture and main workflow

```
python tools/blastradius/blastradius.py --symbol <name> [--ref HEAD] [--ci]
```

1. **Reach** — `entire graph impact --symbol S --depth 2 --format json`, reading the `callers`, `type_consumers`, `data_flows` and `co_changes` sections. `callees` is deliberately excluded: those are what the symbol depends on, not what breaks when it changes. Duplicates collapse to the nearest hop so the evidence chain is the shortest one.
2. **Claim** — `entire checkpoint explain`, falling back to the commit message and file list when no checkpoint is linked. The report states which source it used, because they are not equally strong evidence.
3. **Bucket** — confirmed vs. silent, matched on symbol name, qualified name and touched file.
4. **Test selection** — `entire graph neighbors --relation TESTS --direction in` per reachable symbol; symbols with no incoming edge are reported as untested reach.
5. **Report** — `file:line` on every row so a reviewer can verify it against source. `--ci` exits non-zero on untested reach.

Claim extraction is deliberately structural — only code-shaped tokens count as a claim — so the tool makes **no model call and needs no network or API key**. That was a design decision, not an omission: a review tool that cannot run offline cannot run in CI.

## Entire Graph findings and verification

**1. Definition lookup** — `evidence/01-definition.json`

```
entire graph def get_guest_rls_filters --repo . --format json
```

Resolved `SupersetSecurityManager.get_guest_rls_filters`, `superset/security/manager.py:5033-5049`, kind `method`.

**2. Impact analysis before a high-risk change** — `evidence/02-impact-before-change.json`

```
entire graph impact --symbol get_guest_rls_filters --repo . --depth 2 --format json
```

Row-level security for embedded dashboards. The analysis returned:

```
d1 CALLS         SupersetSecurityManager.get_guest_rls_filters_str   manager.py:5240
d2 CALLS         SupersetSecurityManager.get_rls_cache_key           manager.py:5245
d1 RETURNS_TYPE  GuestTokenRlsRule                                   guest_token.py:134
d1 PARAM_TYPE    BaseDatasource                                      models.py:184
d1 PARAM_TYPE    Explorable                                          explorables/base.py:181
```

`get_rls_cache_key` at depth 2 is the finding that matters: change what the RLS filters mean and the **cache key** is downstream, so the same key can serve rows scoped to a different tenant.

**3. Verification — where the graph was incomplete, and how we knew**

Grep finds call sites the graph does not report, at `superset/jinja_context.py:297` and `superset/connectors/sqla/models.py:891`. Both reach the function through a module-level `security_manager` singleton, so dynamic dispatch defeats static attribution.

We treated this as the guide instructs — evidence, not an oracle — and it changed the product: the report surfaces the graph's own `partial_failures` (32 files parsed with syntax errors in this repo) as a completeness warning instead of presenting a clean-looking result as fact.

_TODO: paste the final semantic diff of the submitted implementation before 14:40._

## Noon Curveball: what changed and how we adapted

_TODO — constraint verbatim, the assumption it attacked, the checkpoint the fresh session was reconstructed from, the impact analysis run before the change, what changed and what stayed, and the test that proves the new behaviour._

## Checkpoint links and what each checkpoint proves

All checkpoints are on branch `blastradius` and synced to the mirror.

| # | Milestone | Checkpoint | Commit | What it proves |
|---|---|---|---|---|
| 1 | Initial understanding and intended architecture | `01M1TP1KQMM23MXEHZ1FF0W9X8` | `611b59e` | The claim-vs-reality thesis and the first working end-to-end path: checkpoint → graph impact → three buckets. |
| — | Scope extension: test selection | `01M1TPHAQVCNN0GMAAQ8KKQ49Q` | `2cc4b80` | Deciding that reach should name the tests that can catch it, using graph `TESTS` edges rather than filename heuristics. |
| 2 | Last stable state before the Noon Curveball | `01M1TPKG7TYGQCJB01DH20TMF9` | `c2ef90b` | Architecture, graph findings, and the dynamic-dispatch gap we had already found — the state the fresh session reconstructed from. |
| 3 | Response to the Noon Curveball | _TODO_ | _TODO_ | _TODO_ |
| 4 | Final implementation and verification | _TODO_ | _TODO_ | _TODO_ |

**Honest note on checkpoint history:** our first commits were made by hand, outside an agent session, so no checkpoint was captured — Entire records agent sessions, and a bare `git commit` has nothing to record. We found this by checking `git for-each-ref refs/entire/checkpoints` and finding it empty, then re-ran the work through an agent session. The checkpoints above are therefore later than the work they describe, and we would rather say so than present a tidy history.

## Setup, run and test instructions

```bash
# from the repo root, with the graph plugin installed
entire plugin install graph

python tools/blastradius/blastradius.py --symbol get_guest_rls_filters
python tools/blastradius/blastradius.py --symbol get_guest_rls_filters --ci
python tools/blastradius/blastradius.py --symbol X --no-tests   # faster, skips test selection
```

Exit codes: `0` nothing to flag · `1` findings (or, with `--ci`, untested reach) · `2` the graph could not answer.

**Performance note for reviewers:** the first query against a repo this size builds the index — measured at 276s here, with query latency of 79ms once warm. Any edit to the working tree invalidates that cache, so batch edits and query once.

## Databricks use, data sources and limitations (if applicable)

Not applicable — we did not opt into the Best Use of Databricks category.

## Known limitations and next steps

**Limitations**

- Static call attribution misses dynamic dispatch. Documented above with a concrete case; the report warns rather than claiming completeness.
- Claim extraction is structural, not semantic. A symbol described in prose but never named ("the caching layer") is not credited as claimed, so it can appear as silent reach.
- Test selection is only as good as the graph's `TESTS` edges; a test that exercises code through a fixture chain may not be attributed.
- Reach is capped at depth 2, which is the maximum `entire graph impact` accepts.
- _TODO: what the Curveball added._

**Next steps**

- Run in CI on every agent-authored commit, gated on untested reach — `--ci` and the exit codes already exist for this.
- Post the report as a PR comment before a human opens the diff.
- Emit the selected tests as a CI job matrix, so the saving is realised rather than just reported.
- Expose the same report as a tool an agent can call, so the check happens before the change is written rather than after.
