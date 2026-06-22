from typing import Type
from crewai.tools import BaseTool
from pydantic import BaseModel, Field, PrivateAttr
import chromadb
import os


class ChromaBaseTool(BaseTool):
    _chroma_db_path: str = PrivateAttr()

    def __init__(self, chroma_db_path: str, **kwargs):
        super().__init__(**kwargs)
        self._chroma_db_path = chroma_db_path

    def _get_client(self) -> chromadb.PersistentClient:
        os.makedirs(self._chroma_db_path, exist_ok=True)
        return chromadb.PersistentClient(path=self._chroma_db_path)


class SaveExecutionInput(BaseModel):
    category: str = Field(..., description="Category: 'schema_design', 'data_quality', or 'sql_fix'.")
    key: str = Field(..., description="Descriptive identifier.")
    content: str = Field(..., description="The content to save.")


class SavePastExecutionTool(ChromaBaseTool):
    name: str = "save_past_execution"
    description: str = "Saves execution metadata to ChromaDB."
    args_schema: Type[BaseModel] = SaveExecutionInput

    def _run(self, category: str, key: str, content: str) -> str:
        try:
            client = self._get_client()
            collection = client.get_or_create_collection(name=category)
            collection.add(
                documents=[content],
                ids=[key],
                metadatas=[{"category": category, "key": key}],
            )
            return f"Successfully saved '{key}' under category '{category}'."
        except Exception as e:
            return f"Error saving to memory: {str(e)}"


class SearchExecutionsInput(BaseModel):
    category: str = Field(..., description="Category: 'schema_design', 'data_quality', or 'sql_fix'.")
    query: str = Field(..., description="Search term or issue details.")
    limit: int = Field(2, description="Max number of records to return.")


class SearchPastExecutionsTool(ChromaBaseTool):
    name: str = "search_past_executions"
    description: str = "Searches memory store for matching past executions."
    args_schema: Type[BaseModel] = SearchExecutionsInput

    def _run(self, category: str, query: str, limit: int = 2) -> str:
        try:
            client = self._get_client()
            collection = client.get_or_create_collection(name=category)
            results = collection.query(query_texts=[query], n_results=limit)
            if not results or not results["documents"] or not results["documents"][0]:
                return f"No historical records found in '{category}' matching '{query}'."
            lines = [f"Matching entries in '{category}':", "=" * 40]
            for doc_id, doc in zip(results["ids"][0], results["documents"][0]):
                lines.append(f"Memory Key: {doc_id}")
                lines.append(f"Content:\n{doc}")
                lines.append("-" * 40)
            return "\n".join(lines)
        except Exception as e:
            return f"Error searching memory: {str(e)}"
