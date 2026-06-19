from .db_tools import RunDuckDBQueryTool, ProfileCSVFileTool, ReadCSVPreviewTool
from .memory_tools import SavePastExecutionTool, SearchPastExecutionsTool
from .human_loop import request_human_approval

class ToolRegistry:
    def __init__(self, data_dir: str, chroma_db_path: str):
        self._data_dir = data_dir
        self._chroma_db_path = chroma_db_path

    def get_db_tools(self):
        return [
            RunDuckDBQueryTool(data_dir=self._data_dir),
            ProfileCSVFileTool(data_dir=self._data_dir),
            ReadCSVPreviewTool(data_dir=self._data_dir)
        ]

    def get_memory_tools(self):
        return [
            SavePastExecutionTool(chroma_db_path=self._chroma_db_path),
            SearchPastExecutionsTool(chroma_db_path=self._chroma_db_path)
        ]

    def get_all_tools(self):
        return self.get_db_tools() + self.get_memory_tools()
