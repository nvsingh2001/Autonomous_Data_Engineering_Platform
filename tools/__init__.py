from .db_tools import (
    RunDuckDBQueryTool,
    ProfileCSVFileTool,
    ReadCSVPreviewTool,
    DatabaseService,
    CSVLoader,
    TypeInspector,
    SchemaShiftDetector,
)
from .memory_tools import SavePastExecutionTool, SearchPastExecutionsTool
from .human_loop import HumanLoopService, ApprovalStrategy, CLIApprovalStrategy, WebApprovalStrategy
from .entity_classifier import EntityClassifier, ECommerceEntity


class ToolRegistry:
    def __init__(
        self,
        data_dir: str,
        chroma_db_path: str,
        db_path: str = ":memory:",
        entity_map: dict | None = None,
    ):
        self._data_dir = data_dir
        self._chroma_db_path = chroma_db_path
        self._db_path = db_path
        # Derive dataset fingerprint from entity types (not filenames — stays stable across renames)
        entity_types = sorted(set((entity_map or {}).values()))
        self._dataset_tag = ",".join(entity_types) if entity_types else "unknown"
        self._entity_types = entity_types

    def get_db_tools(self) -> list:
        return [
            RunDuckDBQueryTool(data_dir=self._data_dir, db_path=self._db_path),
            ProfileCSVFileTool(data_dir=self._data_dir),
            ReadCSVPreviewTool(data_dir=self._data_dir),
        ]

    def get_memory_tools(self) -> list:
        return [
            SavePastExecutionTool(
                chroma_db_path=self._chroma_db_path,
                dataset_tag=self._dataset_tag,
                entity_types=self._entity_types,
            ),
            SearchPastExecutionsTool(
                chroma_db_path=self._chroma_db_path,
                dataset_tag=self._dataset_tag,
                entity_types=self._entity_types,
            ),
        ]

    def get_all_tools(self) -> list:
        return self.get_db_tools() + self.get_memory_tools()
