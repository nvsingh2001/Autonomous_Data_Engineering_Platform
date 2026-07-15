---
name: schema_lookup
description: Look up the warehouse's full star schema (fact/dimension tables, grain definitions, foreign keys) when a question needs multi-table joins or asks how the warehouse is structured.
---
Call `lookup_schema` (no arguments) before writing SQL that joins across multiple
Fact_/Dim_ tables, or when the user asks how the warehouse is organized. It returns the
full schema design document. You don't need it for simple single-table questions —
SHOW TABLES and DESCRIBE already tell you column names.
