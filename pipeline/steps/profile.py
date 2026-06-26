import re
import os
import json
from tools import EntityClassifier, ECommerceEntity, ProfileCSVFileTool
from pipeline.profiler import extract_columns_from_raw


class ProfileStep:
    def __init__(self, data_dir: str, reports_dir: str, llm_fallback_fn=None):
        self._data_dir = data_dir
        self._reports_dir = reports_dir
        self._llm_fallback_fn = llm_fallback_fn

    def run(self) -> dict:
        print("[Flow] Starting data profiling...")
        os.makedirs(self._data_dir, exist_ok=True)
        files = self._discover_files()

        combined_results: dict = {}
        profiler_tool = ProfileCSVFileTool(data_dir=self._data_dir)
        for filename in files:
            print(f"[Flow] Profiling file: {filename}...")
            try:
                combined_results[filename] = profiler_tool.profile_as_dict(filename)
            except Exception as e:
                print(f"[Flow] Warning: Failed to profile {filename}: {e}")
                combined_results[filename] = {"raw_output": str(e)}

        entity_map = self._classify_entities(combined_results)

        os.makedirs(self._reports_dir, exist_ok=True)
        with open(
            os.path.join(self._reports_dir, "profiling_report.json"),
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(combined_results, f, indent=2)

        return {
            "files": files,
            "entity_map": entity_map,
            "profiling_results": json.dumps(combined_results, indent=2),
        }

    def _discover_files(self) -> list[str]:
        files = [
            f
            for f in os.listdir(self._data_dir)
            if f.endswith((".csv", ".xlsx", ".xls", ".json"))
        ]
        if not files:
            raise FileNotFoundError("No datasets found in data directory.")
        return files

    def _classify_entities(self, combined_results: dict) -> dict:
        entity_map: dict = {}
        for filename, raw_profile in combined_results.items():
            cols, row_count = extract_columns_from_raw(raw_profile, filename)
            if not cols and "raw_output" in raw_profile:
                cols = re.findall(r'"([^"]+)":\s*\{', raw_profile["raw_output"])
            classification = EntityClassifier.classify(
                cols, row_count=row_count, filename=filename
            )
            if classification["confidence"] < 0.4 and self._llm_fallback_fn is not None:
                fallback = self._llm_fallback_fn(cols, filename)
                if fallback and fallback != "unknown":
                    try:
                        classification["entity"] = ECommerceEntity(fallback)
                        classification["confidence"] = 0.0
                        print(f"[Flow] LLM fallback → {fallback} for {filename}")
                    except ValueError:
                        pass
            entity_map[filename] = classification["entity"].value
            combined_results[filename]["_entity"] = classification["entity"].value
            combined_results[filename]["_entity_confidence"] = classification["confidence"]
            combined_results[filename]["_entity_grain"] = classification["grain"]
            if classification["notes"]:
                combined_results[filename]["_entity_notes"] = classification["notes"]
            print(
                f"[Flow] Entity: {filename} → {classification['entity'].value}"
                f" (conf={classification['confidence']:.2f})"
                f"{' | ' + classification['notes'] if classification['notes'] else ''}"
            )
        return entity_map
