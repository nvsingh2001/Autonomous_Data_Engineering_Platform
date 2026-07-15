---
name: chart
description: Render a bar, line, or pie chart from query results when the user asks to visualize, plot, chart, or graph something.
---
Use `render_chart` on data you already retrieved with `run_duckdb_query` in this same turn —
run the query first, then pass its labels and numeric values to `render_chart`. Never invent
data points. Include the tool's returned markdown image line verbatim in your final answer,
followed by 1-2 sentences summarizing what the chart shows.
