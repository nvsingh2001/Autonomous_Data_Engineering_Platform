from typing import Type
from crewai.tools import BaseTool
from pydantic import BaseModel, Field, PrivateAttr
import chromadb
import os


class ChromaBaseTool(BaseTool):
    _chroma_db_path: str = PrivateAttr()
    _dataset_tag: str = PrivateAttr()       # sorted entity types, e.g. "orders,payments,products"
    _entity_types: list = PrivateAttr()     # list of entity type strings for this run

    def __init__(self, chroma_db_path: str, dataset_tag: str = "", entity_types: list | None = None, **kwargs):
        super().__init__(**kwargs)
        self._chroma_db_path = chroma_db_path
        self._dataset_tag = dataset_tag
        self._entity_types = entity_types or []

    def _get_client(self) -> chromadb.PersistentClient:
        os.makedirs(self._chroma_db_path, exist_ok=True)
        return chromadb.PersistentClient(path=self._chroma_db_path)


class SaveExecutionInput(BaseModel):
    category: str = Field(
        ..., description=(
            "Category — one of: 'error_patterns', 'schema_decisions', 'data_quality'. "
            "Do NOT store raw SQL or dataset-specific column names. "
            "Store only the error TYPE and fix STRATEGY (e.g. 'UNION branch column count mismatch — "
            "add NULL placeholders to shorter branch'), or entity-level schema decisions "
            "(e.g. 'payments entity → Fact_Payments, grain = one row per installment')."
        )
    )
    key: str = Field(..., description="Descriptive identifier, e.g. 'union_column_count_fix'.")
    content: str = Field(
        ..., description=(
            "Content to save. Must be dataset-agnostic: describe patterns, strategies, and entity-level "
            "decisions — NOT specific column names, table names, or SQL from the current dataset."
        )
    )


class SavePastExecutionTool(ChromaBaseTool):
    name: str = "save_past_execution"
    description: str = (
        "Saves a dataset-agnostic pattern or decision to long-term memory. "
        "Only save reusable insights: error patterns, fix strategies, entity-to-table mapping decisions. "
        "Never save raw SQL or dataset-specific column names."
    )
    args_schema: Type[BaseModel] = SaveExecutionInput

    def _run(self, category: str, key: str, content: str) -> str:
        try:
            client = self._get_client()
            # Enforce allowed categories — reject anything that might store raw SQL
            allowed = {"error_patterns", "schema_decisions", "data_quality"}
            if category not in allowed:
                return (
                    f"Rejected: category '{category}' is not allowed. "
                    f"Use one of: {sorted(allowed)}. "
                    "Do not store raw SQL or dataset-specific content."
                )
            collection = client.get_or_create_collection(name=category)
            # Tag every memory with the current dataset's entity fingerprint
            metadata = {
                "category": category,
                "key": key,
                "dataset_tag": self._dataset_tag,
                "entity_types": ",".join(sorted(self._entity_types)),
            }
            # Use upsert so re-runs update stale memories rather than fail on duplicate ids
            collection.upsert(
                documents=[content],
                ids=[key],
                metadatas=[metadata],
            )
            return f"Saved '{key}' under '{category}' (tagged: {self._dataset_tag or 'untagged'})."
        except Exception as e:
            return f"Error saving to memory: {str(e)}"


class SearchExecutionsInput(BaseModel):
    category: str = Field(
        ..., description="Category to search: 'error_patterns', 'schema_decisions', or 'data_quality'."
    )
    query: str = Field(..., description="Search term or issue details.")
    limit: int = Field(3, description="Max number of records to return (max 5).")


class SearchPastExecutionsTool(ChromaBaseTool):
    name: str = "search_past_executions"
    description: str = (
        "Searches long-term memory for relevant past patterns or decisions. "
        "Results are automatically filtered to only return memories relevant to the current dataset's "
        "entity types — cross-dataset contamination is prevented automatically."
    )
    args_schema: Type[BaseModel] = SearchExecutionsInput

    # Minimum fraction of the stored memory's entity types that must overlap with the current
    # dataset for the memory to be considered relevant.
    _ENTITY_OVERLAP_THRESHOLD: float = 0.4
    # Maximum cosine distance (ChromaDB L2 by default) — memories beyond this are too dissimilar.
    _DISTANCE_THRESHOLD: float = 1.5

    def _run(self, category: str, query: str, limit: int = 3) -> str:
        limit = min(limit, 5)
        try:
            client = self._get_client()
            collection = client.get_or_create_collection(name=category)
            if collection.count() == 0:
                return f"No historical records found in '{category}'."

            # Fetch more than needed so we can filter down
            raw = collection.query(
                query_texts=[query],
                n_results=min(limit * 4, collection.count()),
                include=["documents", "metadatas", "distances"],
            )

            if not raw or not raw["documents"] or not raw["documents"][0]:
                return f"No historical records found in '{category}' matching '{query}'."

            current_entities = set(self._entity_types)
            accepted = []

            for doc_id, doc, meta, dist in zip(
                raw["ids"][0],
                raw["documents"][0],
                raw["metadatas"][0],
                raw["distances"][0],
            ):
                # Guardrail 1 — distance threshold: reject semantically distant memories
                if dist > self._DISTANCE_THRESHOLD:
                    continue

                # Guardrail 2 — entity overlap: only retrieve memories from runs
                # whose entity composition overlaps sufficiently with the current dataset.
                # Skip this check if either side has no entity info (backwards compatibility).
                stored_entities_str = meta.get("entity_types", "")
                if stored_entities_str and current_entities:
                    stored_entities = set(stored_entities_str.split(","))
                    overlap = len(current_entities & stored_entities) / max(len(stored_entities), 1)
                    if overlap < self._ENTITY_OVERLAP_THRESHOLD:
                        continue  # Cross-dataset contamination — skip

                accepted.append((doc_id, doc, meta.get("dataset_tag", "unknown")))
                if len(accepted) >= limit:
                    break

            if not accepted:
                return (
                    f"No relevant records found in '{category}' for the current dataset. "
                    f"(Filtered {len(raw['ids'][0])} candidates by entity overlap and similarity threshold.)"
                )

            lines = [f"Relevant memories from '{category}':", "=" * 40]
            for doc_id, doc, tag in accepted:
                lines.append(f"Memory Key: {doc_id}  [dataset: {tag}]")
                lines.append(f"Content:\n{doc}")
                lines.append("-" * 40)
            return "\n".join(lines)

        except Exception as e:
            return f"Error searching memory: {str(e)}"
