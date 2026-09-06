# Databricks notebook source
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

# MAGIC %md
# MAGIC # blastradius — the Entire Graph as data
# MAGIC
# MAGIC What a change *reaches*, and how well we know it.
# MAGIC
# MAGIC Every edge carries a confidence label, because a graph edge and the absence of
# MAGIC one are not the same quality of evidence:
# MAGIC
# MAGIC | Label | Meaning |
# MAGIC |---|---|
# MAGIC | `CONFIRMED` | A resolved static relation (`CALLS`, `PARAM_TYPE`, `USES_TYPE`, `RETURNS_TYPE`, `DATA_FLOWS`) whose endpoint file parsed cleanly. |
# MAGIC | `HEURISTIC` | A `FILE_CHANGES_WITH` co-change edge — a commit-history correlation, not a code relation — or any endpoint in a file the graph failed to parse. |
# MAGIC
# MAGIC Absences (`UNVERIFIED`) are deliberately **not** rows in this dataset. You cannot
# MAGIC put "no edge was found" in an edge table without it reading as a fact; it is
# MAGIC reported by the CLI with the command that settles it.
# MAGIC
# MAGIC Source: `tools/blastradius/blastradius.py`, payloads in `evidence/`.

# COMMAND ----------

dbutils.widgets.text("data_path", "file:/Workspace/Shared/blastradius/data", "CSV folder")
DATA = dbutils.widgets.get("data_path").rstrip("/")
print(f"reading from {DATA}")

# COMMAND ----------

import pandas as pd


def load(name: str) -> pd.DataFrame:
    """Spark where it is available, pandas otherwise — the data is tiny either way."""
    try:
        return spark.read.csv(f"{DATA}/{name}.csv", header=True, inferSchema=True).toPandas()
    except Exception as exc:  # noqa: BLE001 - notebook runs outside Spark too
        print(f"spark read failed ({exc.__class__.__name__}), falling back to pandas")
        return pd.read_csv(f"{DATA.replace('file:', '')}/{name}.csv")


nodes = load("graph_nodes")
edges = load("graph_edges")
failures = load("graph_partial_failures")

print(f"{len(nodes)} nodes · {len(edges)} edges · {len(failures)} parse failures")
display(edges)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. How much of the blast radius is actually resolved?
# MAGIC
# MAGIC The bar that matters is `HEURISTIC`: those are edges we report but would not
# MAGIC gate a build on.

# COMMAND ----------

import matplotlib.pyplot as plt

CONFIDENCE_COLOURS = {"CONFIRMED": "#3fb950", "HEURISTIC": "#d29922"}

by_conf = edges.groupby(["focus", "confidence"]).size().unstack(fill_value=0)
ax = by_conf.plot(
    kind="barh",
    stacked=True,
    figsize=(9, 3.2),
    color=[CONFIDENCE_COLOURS.get(c, "#8b949e") for c in by_conf.columns],
)
ax.set_xlabel("reachable symbols")
ax.set_ylabel("")
ax.set_title("Reach by confidence, per analysed symbol")
ax.legend(title=None, frameon=False)
ax.spines[["top", "right"]].set_visible(False)
plt.tight_layout()
display(plt.gcf())
plt.close()

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Blast radius by distance
# MAGIC
# MAGIC Distance is *not* danger. A two-hop reach can matter more than a one-hop one —
# MAGIC `get_rls_cache_key` is exactly that case: change what the RLS filters mean and
# MAGIC the cache key is downstream, so one key can serve rows scoped to another tenant.

# COMMAND ----------

depth_summary = (
    edges.groupby(["depth", "confidence"]).size().reset_index(name="edges")
    .pivot(index="depth", columns="confidence", values="edges").fillna(0).astype(int)
)
display(depth_summary.reset_index())

ax = depth_summary.plot(
    kind="bar",
    figsize=(6, 3.2),
    color=[CONFIDENCE_COLOURS.get(c, "#8b949e") for c in depth_summary.columns],
)
ax.set_xlabel("hops from the change")
ax.set_ylabel("edges")
ax.set_title("Blast radius by distance")
ax.legend(title=None, frameon=False)
ax.spines[["top", "right"]].set_visible(False)
plt.xticks(rotation=0)
plt.tight_layout()
display(plt.gcf())
plt.close()

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. The graph itself
# MAGIC
# MAGIC Same encoding as the CLI's mermaid output, so the two views cannot disagree:
# MAGIC **edge colour** is reach confidence, **node alpha** is distance from the change.
# MAGIC
# MAGIC `networkx` ships with the ML runtime; uncomment the `%pip` cell below on a
# MAGIC bare runtime.

# COMMAND ----------

# MAGIC %pip install networkx

# COMMAND ----------

import networkx as nx

FOCUS = edges["focus"].iloc[0]
sub = edges[edges["focus"] == FOCUS]

g = nx.DiGraph()
labels = nodes.set_index("node_id")["name"].to_dict()
for _, e in sub.iterrows():
    g.add_edge(e["src"], e["dst"], confidence=e["confidence"], depth=int(e["depth"]))

pos = nx.spring_layout(g, seed=7, k=1.4)
fig, ax = plt.subplots(figsize=(12, 7))

for confidence, colour in CONFIDENCE_COLOURS.items():
    subset = [(u, v) for u, v, d in g.edges(data=True) if d["confidence"] == confidence]
    nx.draw_networkx_edges(
        g, pos, edgelist=subset, edge_color=colour, ax=ax,
        width=2.5 if confidence == "CONFIRMED" else 1.2,
        style="solid" if confidence == "CONFIRMED" else "dashed",
        arrowsize=14, alpha=0.9,
    )

for depth, alpha in ((1, 0.85), (2, 0.35)):
    members = [
        n for n in g.nodes
        if n != sub["src"].iloc[0]
        and any(d["depth"] == depth for _, _, d in g.in_edges(n, data=True))
    ]
    nx.draw_networkx_nodes(
        g, pos, nodelist=members, node_color="#58a6ff", alpha=alpha,
        node_size=1900, edgecolors="#8b949e", ax=ax,
    )

nx.draw_networkx_nodes(
    g, pos, nodelist=[sub["src"].iloc[0]], node_color="#58a6ff",
    node_size=2600, edgecolors="#1f6feb", linewidths=2.5, ax=ax,
)
nx.draw_networkx_labels(g, pos, labels={n: labels.get(n, n) for n in g.nodes}, font_size=8, ax=ax)

ax.set_title(f"Blast radius of {FOCUS} — edge colour is confidence, node alpha is distance")
ax.axis("off")
plt.tight_layout()
display(fig)
plt.close()

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. What the graph could not read
# MAGIC
# MAGIC Scoped by language, not totalled. A "degraded" banner over a clean Python
# MAGIC result would be its own kind of overclaim — these failures are all in other
# MAGIC languages, so no Python row above was downgraded because of them.

# COMMAND ----------

failures["language"] = (
    failures["file_path"].str.rsplit(".", n=1).str[-1].fillna("none")
)
summary = (
    failures.drop_duplicates("file_path")
    .groupby("language").size().reset_index(name="files")
    .sort_values("files", ascending=False)
)
display(summary)

downgraded = edges[edges["caveat"].fillna("").str.contains("parsed with errors")]
print(f"edges downgraded because their endpoint file failed to parse: {len(downgraded)}")
if len(downgraded):
    display(downgraded[["focus", "dst", "relation", "confidence", "caveat"]])
else:
    print("none — every parse failure is in a language other than the one analysed")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. The CLI's own diagram, rendered here
# MAGIC
# MAGIC Generated by `blastradius.py --mermaid`, so this and the notebook read from the
# MAGIC same analysis rather than two implementations that can drift.

# COMMAND ----------

MERMAID = """
graph LR
    classDef focusnode stroke:#58a6ff,stroke-width:3px,fill:#58a6ff66
    classDef unresolved1 stroke:#8b949e,stroke-width:2px,stroke-dasharray:3 4,fill:#58a6ff38
    classDef unresolved2 stroke:#8b949e,stroke-width:2px,stroke-dasharray:3 4,fill:#58a6ff14
    classDef nocoverage1 stroke:#8b949e,stroke-width:1px,stroke-dasharray:1 3,fill:#58a6ff38

    focus(("get_guest_rls_filters")):::focusnode
    n0{{"UPDATING.md"}}:::nocoverage1
    n1["BaseDatasource<br/>superset/connectors/sqla/models.py:184"]:::unresolved1
    n2["Explorable<br/>superset/explorables/base.py:181"]:::unresolved1
    n3["GuestTokenRlsRule<br/>superset/security/guest_token.py:134"]:::unresolved1
    n4["SupersetSecurityManager<br/>superset/security/manager.py:1738"]:::unresolved1
    n5["get_guest_rls_filters_str<br/>superset/security/manager.py:5240"]:::unresolved1
    n6{{"manager_test.py<br/>tests/unit_tests/security/manager_test.py"}}:::nocoverage1
    n7["get_rls_cache_key<br/>superset/security/manager.py:5245"]:::unresolved2

    focus -.->|co-change| n0
    focus ==>|type consumer| n1
    focus ==>|type consumer| n2
    focus ==>|type consumer| n3
    focus ==>|call| n4
    focus ==>|call| n5
    focus -.->|co-change| n6
    focus ==>|call| n7
    linkStyle 1,2,3,4,5,7 stroke:#3fb950,stroke-width:2.5px
    linkStyle 0,6 stroke:#d29922,stroke-width:1.5px
"""

displayHTML(
    f"""
    <div style="background:#fff;padding:12px;border-radius:8px">
      <pre class="mermaid">{MERMAID}</pre>
    </div>
    <script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"></script>
    <script>mermaid.initialize({{startOnLoad:true}});</script>
    """
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## What this dataset deliberately does not contain
# MAGIC
# MAGIC - **No `UNVERIFIED` rows.** An absence in an edge table reads as a fact. The
# MAGIC   CLI reports those with the `grep` and `pytest` command that settles each one.
# MAGIC - **No dynamically-dispatched calls.** Static attribution cannot follow a
# MAGIC   module-level singleton. Measured case: callers of `get_guest_rls_filters` at
# MAGIC   `superset/jinja_context.py:297` and `superset/connectors/sqla/models.py:891`
# MAGIC   reach it through `security_manager` and carry no `CALLS` edge. They are
# MAGIC   missing from `graph_edges.csv`, and counting rows here will undercount reach.
# MAGIC - **No severity.** Confidence says how well we know an edge exists. It says
# MAGIC   nothing about how much it should worry you.
