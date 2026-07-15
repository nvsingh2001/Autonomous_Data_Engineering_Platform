---
name: export_csv
description: Export a query result as a downloadable CSV file when the user asks to export, download, or save data or results as a file.
---
Use `export_csv` with the SQL query whose FULL result you want exported — not
run_duckdb_query, which truncates large results — and a short descriptive label (e.g.
'top_products_by_revenue'). Include the tool's returned markdown download link verbatim
in your final answer, plus 1-2 sentences describing what was exported and how many rows.
