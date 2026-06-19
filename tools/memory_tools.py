import os
import chromadb
from crewai.tools import tool

CHROMA_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.chroma"))

def get_chroma_client():
    os.makedirs(CHROMA_PATH, exist_ok=True)
    return chromadb.PersistentClient(path=CHROMA_PATH)

@tool("save_past_execution")
def save_past_execution(category: str, key: str, content: str) -> str:
    """Saves metadata about a past execution to ChromaDB.
    category can be 'schema_design', 'data_quality', or 'sql_fix'."""
    try:
        client = get_chroma_client()
        collection = client.get_or_create_collection(name=category)
        collection.add(
            documents=[content],
            ids=[key],
            metadatas=[{"category": category, "key": key}]
        )
        return f"Successfully saved '{key}' under category '{category}' in memory."
    except Exception as e:
        return f"Error saving to memory: {str(e)}"

@tool("search_past_executions")
def search_past_executions(category: str, query: str, limit: int = 2) -> str:
    """Searches memory store for matching past executions (schemas, fixes, quality reports)."""
    try:
        client = get_chroma_client()
        collection = client.get_or_create_collection(name=category)
        results = collection.query(
            query_texts=[query],
            n_results=limit
        )
        if not results or not results['documents'] or not results['documents'][0]:
            return f"No historical records found in '{category}' memory matching '{query}'."
        docs = results['documents'][0]
        ids = results['ids'][0]
        output = [f"Matching entries in '{category}' memory:", "=========================================="]
        for doc_id, doc in zip(ids, docs):
            output.append(f"Memory Key: {doc_id}")
            output.append(f"Content:\n{doc}")
            output.append("-" * 40)
        return "\n".join(output)
    except Exception as e:
        return f"Error searching memory: {str(e)}"
