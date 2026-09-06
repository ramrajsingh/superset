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

def _default_data_path() -> str:
    """Look for `data/` next to this notebook, wherever it was imported.

    Hardcoding an absolute path would work for exactly one workspace and one
    user, and would fail quietly into the embedded copy for everybody else.
    """
    try:
        ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
        return "file:/Workspace" + ctx.notebookPath().get().rsplit("/", 1)[0] + "/data"
    except Exception:  # noqa: BLE001 - not every runtime exposes the context
        return "file:/Workspace/Shared/blastradius/data"


dbutils.widgets.text("data_path", _default_data_path(), "CSV folder")
DATA = dbutils.widgets.get("data_path").rstrip("/")
print(f"reading from {DATA}")

# COMMAND ----------

import io

import pandas as pd

# The dataset is 125 rows. Embedding it keeps this notebook a single-file import
# with nothing to upload first; the CSVs in `databricks/data/` stay the source of
# truth and are preferred whenever they are actually reachable.
EMBEDDED = {
    "graph_nodes": """node_id,name,qualified_name,kind,file_path,start_line,language,is_focus
superset/security/manager.py:5033,get_guest_rls_filters,SupersetSecurityManager.get_guest_rls_filters,method,superset/security/manager.py,5033,Python,True
UPDATING.md:0,UPDATING.md,UPDATING.md,file,UPDATING.md,0,,False
superset/connectors/sqla/models.py:184,BaseDatasource,BaseDatasource,class,superset/connectors/sqla/models.py,184,,False
superset/explorables/base.py:181,Explorable,Explorable,class,superset/explorables/base.py,181,,False
superset/security/guest_token.py:134,GuestTokenRlsRule,GuestTokenRlsRule,class,superset/security/guest_token.py,134,,False
superset/security/manager.py:1738,SupersetSecurityManager,SupersetSecurityManager,tool,superset/security/manager.py,1738,,False
superset/security/manager.py:5240,get_guest_rls_filters_str,SupersetSecurityManager.get_guest_rls_filters_str,method,superset/security/manager.py,5240,,False
tests/unit_tests/security/manager_test.py:0,manager_test.py,manager_test.py,file,tests/unit_tests/security/manager_test.py,0,,False
superset/security/manager.py:5245,get_rls_cache_key,SupersetSecurityManager.get_rls_cache_key,method,superset/security/manager.py,5245,,False
tools/blastradius/blastradius.py:137,graph_impact,graph_impact,function,tools/blastradius/blastradius.py,137,Python,True
tools/blastradius/blastradius.py:348,main,main,function,tools/blastradius/blastradius.py,348,,False
tools/blastradius/blastradius.py:78,Reach,Reach,class,tools/blastradius/blastradius.py,78,,False
tools/blastradius/blastradius.py:0,blastradius.py,blastradius.py,file,tools/blastradius/blastradius.py,0,,False
tools/blastradius/blastradius.py:225,select_tests,select_tests,function,tools/blastradius/blastradius.py,225,Python,True
tools/blastradius/blastradius.py:64,Symbol,Symbol,class,tools/blastradius/blastradius.py,64,,False""",
    "graph_edges": """focus,src,dst,relation,edge_label,depth,confidence,caveat,structural
get_guest_rls_filters,superset/security/manager.py:5033,UPDATING.md:0,FILE_CHANGES_WITH,co-change,1,HEURISTIC,"co-change is a commit-history correlation, not a code relation",False
get_guest_rls_filters,superset/security/manager.py:5033,superset/connectors/sqla/models.py:184,PARAM_TYPE,type consumer,1,CONFIRMED,,True
get_guest_rls_filters,superset/security/manager.py:5033,superset/explorables/base.py:181,PARAM_TYPE,type consumer,1,CONFIRMED,,True
get_guest_rls_filters,superset/security/manager.py:5033,superset/security/guest_token.py:134,RETURNS_TYPE,type consumer,1,CONFIRMED,,True
get_guest_rls_filters,superset/security/manager.py:5033,superset/security/manager.py:1738,CALLS,call,1,CONFIRMED,,True
get_guest_rls_filters,superset/security/manager.py:5033,superset/security/manager.py:5240,CALLS,call,1,CONFIRMED,,True
get_guest_rls_filters,superset/security/manager.py:5033,tests/unit_tests/security/manager_test.py:0,FILE_CHANGES_WITH,co-change,1,HEURISTIC,"co-change is a commit-history correlation, not a code relation",False
get_guest_rls_filters,superset/security/manager.py:5033,superset/security/manager.py:5245,CALLS,call,2,CONFIRMED,,True
graph_impact,tools/blastradius/blastradius.py:137,tools/blastradius/blastradius.py:348,CALLS,call,1,CONFIRMED,,True
graph_impact,tools/blastradius/blastradius.py:137,tools/blastradius/blastradius.py:78,RETURNS_TYPE,type consumer,1,CONFIRMED,,True
graph_impact,tools/blastradius/blastradius.py:137,tools/blastradius/blastradius.py:0,CALLS,call,2,CONFIRMED,,True
select_tests,tools/blastradius/blastradius.py:225,tools/blastradius/blastradius.py:348,CALLS,call,1,CONFIRMED,,True
select_tests,tools/blastradius/blastradius.py:225,tools/blastradius/blastradius.py:64,RETURNS_TYPE,type consumer,1,CONFIRMED,,True
select_tests,tools/blastradius/blastradius.py:225,tools/blastradius/blastradius.py:78,PARAM_TYPE,type consumer,1,CONFIRMED,,True
select_tests,tools/blastradius/blastradius.py:225,tools/blastradius/blastradius.py:0,CALLS,call,2,CONFIRMED,,True""",
    "graph_partial_failures": """focus,file_path,code,severity,effect
get_guest_rls_filters,docs/src/pages/community.tsx,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
get_guest_rls_filters,helm/superset/templates/configmap-superset.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
get_guest_rls_filters,helm/superset/templates/deployment-beat.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
get_guest_rls_filters,helm/superset/templates/deployment-flower.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
get_guest_rls_filters,helm/superset/templates/deployment-mcp.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
get_guest_rls_filters,helm/superset/templates/deployment-worker.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
get_guest_rls_filters,helm/superset/templates/deployment-ws.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
get_guest_rls_filters,helm/superset/templates/deployment.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
get_guest_rls_filters,helm/superset/templates/hpa-node.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
get_guest_rls_filters,helm/superset/templates/hpa-worker.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
get_guest_rls_filters,helm/superset/templates/httproute.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
get_guest_rls_filters,helm/superset/templates/ingress.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
get_guest_rls_filters,helm/superset/templates/init-job.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
get_guest_rls_filters,helm/superset/templates/pdb-beat.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
get_guest_rls_filters,helm/superset/templates/pdb-flower.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
get_guest_rls_filters,helm/superset/templates/pdb-mcp.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
get_guest_rls_filters,helm/superset/templates/pdb-worker.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
get_guest_rls_filters,helm/superset/templates/pdb-ws.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
get_guest_rls_filters,helm/superset/templates/pdb.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
get_guest_rls_filters,helm/superset/templates/secret-env.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
get_guest_rls_filters,helm/superset/templates/secret-superset-config.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
get_guest_rls_filters,helm/superset/templates/secret-ws.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
get_guest_rls_filters,helm/superset/templates/service-flower.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
get_guest_rls_filters,helm/superset/templates/service-mcp.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
get_guest_rls_filters,helm/superset/templates/service-ws.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
get_guest_rls_filters,helm/superset/templates/service.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
get_guest_rls_filters,helm/superset/templates/serviceaccount.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
get_guest_rls_filters,superset-frontend/packages/superset-ui-core/src/components/CodeSyntaxHighlighter/CodeSyntaxHighlighter.stories.tsx,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
get_guest_rls_filters,superset-frontend/plugins/plugin-chart-echarts/src/Radar/utils.ts,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
get_guest_rls_filters,superset-frontend/plugins/plugin-chart-horizon/src/transformData.ts,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
graph_impact,docs/src/pages/community.tsx,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
graph_impact,evidence/01-definition.json,E_MINIFIED,warning,file record emitted but symbol parsing skipped
graph_impact,evidence/02-impact-before-change.json,E_MINIFIED,warning,file record emitted but symbol parsing skipped
graph_impact,helm/superset/templates/configmap-superset.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
graph_impact,helm/superset/templates/deployment-beat.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
graph_impact,helm/superset/templates/deployment-flower.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
graph_impact,helm/superset/templates/deployment-mcp.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
graph_impact,helm/superset/templates/deployment-worker.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
graph_impact,helm/superset/templates/deployment-ws.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
graph_impact,helm/superset/templates/deployment.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
graph_impact,helm/superset/templates/hpa-node.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
graph_impact,helm/superset/templates/hpa-worker.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
graph_impact,helm/superset/templates/httproute.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
graph_impact,helm/superset/templates/ingress.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
graph_impact,helm/superset/templates/init-job.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
graph_impact,helm/superset/templates/pdb-beat.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
graph_impact,helm/superset/templates/pdb-flower.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
graph_impact,helm/superset/templates/pdb-mcp.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
graph_impact,helm/superset/templates/pdb-worker.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
graph_impact,helm/superset/templates/pdb-ws.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
graph_impact,helm/superset/templates/pdb.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
graph_impact,helm/superset/templates/secret-env.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
graph_impact,helm/superset/templates/secret-superset-config.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
graph_impact,helm/superset/templates/secret-ws.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
graph_impact,helm/superset/templates/service-flower.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
graph_impact,helm/superset/templates/service-mcp.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
graph_impact,helm/superset/templates/service-ws.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
graph_impact,helm/superset/templates/service.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
graph_impact,helm/superset/templates/serviceaccount.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
graph_impact,superset-frontend/packages/superset-ui-core/src/components/CodeSyntaxHighlighter/CodeSyntaxHighlighter.stories.tsx,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
graph_impact,superset-frontend/plugins/plugin-chart-echarts/src/Radar/utils.ts,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
graph_impact,superset-frontend/plugins/plugin-chart-horizon/src/transformData.ts,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
select_tests,docs/src/pages/community.tsx,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
select_tests,evidence/01-definition.json,E_MINIFIED,warning,file record emitted but symbol parsing skipped
select_tests,evidence/02-impact-before-change.json,E_MINIFIED,warning,file record emitted but symbol parsing skipped
select_tests,evidence/impact_graph_impact.json,E_MINIFIED,warning,file record emitted but symbol parsing skipped
select_tests,helm/superset/templates/configmap-superset.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
select_tests,helm/superset/templates/deployment-beat.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
select_tests,helm/superset/templates/deployment-flower.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
select_tests,helm/superset/templates/deployment-mcp.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
select_tests,helm/superset/templates/deployment-worker.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
select_tests,helm/superset/templates/deployment-ws.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
select_tests,helm/superset/templates/deployment.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
select_tests,helm/superset/templates/hpa-node.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
select_tests,helm/superset/templates/hpa-worker.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
select_tests,helm/superset/templates/httproute.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
select_tests,helm/superset/templates/ingress.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
select_tests,helm/superset/templates/init-job.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
select_tests,helm/superset/templates/pdb-beat.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
select_tests,helm/superset/templates/pdb-flower.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
select_tests,helm/superset/templates/pdb-mcp.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
select_tests,helm/superset/templates/pdb-worker.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
select_tests,helm/superset/templates/pdb-ws.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
select_tests,helm/superset/templates/pdb.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
select_tests,helm/superset/templates/secret-env.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
select_tests,helm/superset/templates/secret-superset-config.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
select_tests,helm/superset/templates/secret-ws.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
select_tests,helm/superset/templates/service-flower.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
select_tests,helm/superset/templates/service-mcp.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
select_tests,helm/superset/templates/service-ws.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
select_tests,helm/superset/templates/service.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
select_tests,helm/superset/templates/serviceaccount.yaml,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
select_tests,superset-frontend/packages/superset-ui-core/src/components/CodeSyntaxHighlighter/CodeSyntaxHighlighter.stories.tsx,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
select_tests,superset-frontend/plugins/plugin-chart-echarts/src/Radar/utils.ts,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete
select_tests,superset-frontend/plugins/plugin-chart-horizon/src/transformData.ts,E_PARSE_ERROR,warning,file parsed with syntax errors; semantic facts may be incomplete""",
}


def load(name: str) -> pd.DataFrame:
    """CSV where we can reach one, embedded copy otherwise.

    Three ways in, most authoritative first: Spark, local pandas, then the copy
    baked into this file. The notebook prints which one it used, because a
    stale embedded copy and a fresh CSV are not the same evidence.
    """
    try:
        df = spark.read.csv(f"{DATA}/{name}.csv", header=True, inferSchema=True).toPandas()
        print(f"{name}: spark")
        return df
    except Exception:  # noqa: BLE001 - notebook also runs with no Spark at all
        pass
    try:
        df = pd.read_csv(f"{DATA.replace('file:', '')}/{name}.csv")
        print(f"{name}: local csv")
        return df
    except Exception:  # noqa: BLE001 - expected on a bare workspace import
        print(f"{name}: embedded copy (no CSV reachable at {DATA})")
        return pd.read_csv(io.StringIO(EMBEDDED[name]))


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
