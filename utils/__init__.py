"""Pipeline domain logic, kept out of `pipeline/` so that package holds only the step
framework (`core/`) and the steps themselves (`steps/`).

Convention: a service that binds to per-run collaborators (a `ConnectionManager`, an LLM)
is a **class** (WarehouseMetrics, AnswerVerifier, SchemaPlanner, TableBuilder); a pure,
stateless transform is a **function** (extract_columns_from_raw). That rule — not an
accident of history — is why some of these are classes and one is a function.

Layering: `tools/` ← `utils/` ← `pipeline/`. These modules may import `tools`
(ConnectionManager, DatabaseService) and `tasks`, but never `pipeline`.
"""

from .profiling import extract_columns_from_raw
from .schema_planner import SchemaPlanner
from .sql_executor import TableBuilder
from .metrics import WarehouseMetrics
from .answer_verifier import AnswerVerifier

__all__ = [
    "extract_columns_from_raw",
    "SchemaPlanner",
    "TableBuilder",
    "WarehouseMetrics",
    "AnswerVerifier",
]
