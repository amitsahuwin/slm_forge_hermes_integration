# Graphify — codebase Q&A first

This project has a knowledge graph at `graphify-out/` (god nodes, communities, cross-file relationships). The PreToolUse hook on `Read` enforces graphify-first when `graphify-out/graph.json` exists.

## Use this order
1. **Codebase questions** → `graphify query "<question>"` (scoped subgraph; usually much smaller than `GRAPH_REPORT.md` or raw grep).
2. **Symbol relationships** → `graphify path "<A>" "<B>"`.
3. **Focused concept** → `graphify explain "<concept>"`.
4. **Broad navigation** → `graphify-out/wiki/index.md` (if exists).
5. **Architecture review** → `graphify-out/GRAPH_REPORT.md` (only when query/path/explain don't yield enough).
6. **Raw file reads** → only AFTER graphify has oriented you, or to modify/debug specific lines.

## After code edits
- Always `graphify update .` to keep the graph current (AST-only, no API cost).

## Subagents
- Include the graphify-first rule in any subagent prompt that involves code exploration.

## When to skip
- Skip graphify for non-code prompts (docs questions, command lookups, config), or when you already have the exact file path AND only need to edit, not understand the surrounding context.
