from .connection_manager import ConnectionManager
from .db_tools import RunDuckDBQueryTool, DatabaseService
from .profiler_tool import ProfileCSVFileTool
from .preview_tool import ReadCSVPreviewTool
from .memory_tools import SavePastExecutionTool, SearchPastExecutionsTool
from .claim_tools import RecordClaimsTool
from .human_loop import HumanLoopService, WebApprovalStrategy
from .entity_classifier import EntityClassifier, ECommerceEntity


class ToolRegistry:
    def __init__(
        self,
        data_dir: str,
        chroma_db_path: str,
        db_path: str = ":memory:",
        entity_map: dict | None = None,
        connection_manager: ConnectionManager | None = None,
        reports_dir: str = "reports",
    ):
        self._data_dir = data_dir
        self._chroma_db_path = chroma_db_path
        self._db_path = db_path
        self._cm = connection_manager
        self._reports_dir = reports_dir
        entity_types = sorted(set((entity_map or {}).values()))
        self._dataset_tag = ",".join(entity_types) if entity_types else "unknown"
        self._entity_types = entity_types

    def get_db_tools(self) -> list:
        return [
            RunDuckDBQueryTool(
                data_dir=self._data_dir,
                db_path=self._db_path,
                connection_manager=self._cm,
            ),
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

    def get_claim_tools(self) -> list:
        return [RecordClaimsTool(reports_dir=self._reports_dir)]

    def get_all_tools(self) -> list:
        return self.get_db_tools() + self.get_memory_tools() + self.get_claim_tools()
