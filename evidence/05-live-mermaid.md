```mermaid
graph LR
    classDef focusnode stroke:#58a6ff,stroke-width:3px,fill:#58a6ff66
    classDef tests stroke:#bc8cff,stroke-width:2px,fill:#bc8cff20
    classDef covered1 stroke:#3fb950,stroke-width:2px,fill:#58a6ff38
    classDef covered2 stroke:#3fb950,stroke-width:2px,fill:#58a6ff14
    classDef unresolved1 stroke:#8b949e,stroke-width:2px,stroke-dasharray:3 4,fill:#58a6ff38
    classDef unresolved2 stroke:#8b949e,stroke-width:2px,stroke-dasharray:3 4,fill:#58a6ff14
    classDef nocoverage1 stroke:#8b949e,stroke-width:1px,stroke-dasharray:1 3,fill:#58a6ff38
    classDef nocoverage2 stroke:#8b949e,stroke-width:1px,stroke-dasharray:1 3,fill:#58a6ff14

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
```

- **Edge** — how we know the change reaches it: thick green `==>` CONFIRMED (resolved static relation) · thin amber `-.->` HEURISTIC (co-change, or an endpoint in a file the graph could not parse).
- **Node border** — what we know about tests: solid green a TESTS edge was found · dashed grey no TESTS edge found (unresolved, not a claim of no coverage) · faint dotted coverage not evaluated.
- **Node fill** — blast radius: denser fill is one hop from the change, fainter is two. Distance, not danger — a two-hop reach can matter more than a one-hop one.
- Every variable is encoded twice (weight and hue, dash and hue, alpha), so no reading depends on colour alone. A test file reached by a `TESTS` edge is a purple test node; a test file that merely *co-changes* is a co-change node, because that is the weaker claim.

