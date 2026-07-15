---
name: memory_search
description: Search long-term memory for past schema decisions, error patterns, or data-quality notes when the user asks about design rationale, past issues, or why something was built a certain way.
---

Use `search_past_executions` with a category ('schema_decisions', 'error_patterns', or
'data_quality') and a short query describing what you're looking for. Results are already
filtered to this dataset's entity types, so a returned memory is presumed relevant. If
nothing relevant comes back, say so plainly rather than treating the empty result as a
hint to invent an answer.
