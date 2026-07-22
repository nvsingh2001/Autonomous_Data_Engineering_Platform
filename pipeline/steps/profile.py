import re
import os
import json

from tools import EntityClassifier, ECommerceEntity, ProfileCSVFileTool
from utils import extract_columns_from_raw
from pipeline.core import PipelineStep
from logging_setup import get_logger

_LOG = get_logger("Flow")


class ProfileStep(PipelineStep):
    """Profiles every source file and deterministically classifies each into an
    e-commerce entity (with an LLM fallback for low-confidence cases). Writes
    `state.files`, `state.entity_map`, `state.profiling_results`."""

    def run(self) -> None:
        _LOG.info("Starting data profiling...")
        data_dir = self.state.data_dir
        os.makedirs(data_dir, exist_ok=True)
        files = self._discover_files()

        combined_results: dict = {}
        profiler_tool = ProfileCSVFileTool(data_dir=data_dir, connection_manager=self.cm)
        for filename in files:
            _LOG.info(f"Profiling file: {filename}...")
            try:
                combined_results[filename] = profiler_tool.profile_as_dict(filename)
            except Exception as e:
                _LOG.warning(f"Failed to profile {filename}: {e}")
                combined_results[filename] = {"raw_output": str(e)}

        entity_map = self._classify_entities(combined_results)

        os.makedirs(self.reports_dir, exist_ok=True)
        results_json = json.dumps(combined_results, indent=2)
        self._write_report("profiling_report.json", results_json)

        self.state.files = files
        self.state.entity_map = entity_map
        self.state.profiling_results = results_json

    def _discover_files(self) -> list[str]:
        files = [f.name for f in self.cm.data_source.list_files()]
        if not files:
            raise FileNotFoundError("No datasets found in data directory.")
        return files

    def _classify_entities(self, combined_results: dict) -> dict:
        llm_fallback_fn = self._ctx.entity_llm_fn()
        entity_map: dict = {}
        for filename, raw_profile in combined_results.items():
            cols, row_count = extract_columns_from_raw(raw_profile, filename)
            if not cols and "raw_output" in raw_profile:
                cols = re.findall(r'"([^"]+)":\s*\{', raw_profile["raw_output"])

            classification = EntityClassifier.classify(
                cols, row_count=row_count, filename=filename
            )

            if classification["confidence"] < 0.4 and llm_fallback_fn is not None:
                fallback = llm_fallback_fn(cols, filename)
                if fallback and fallback != "unknown":
                    try:
                        classification["entity"] = ECommerceEntity(fallback)
                        classification["confidence"] = 0.0
                        _LOG.info(f"LLM fallback → {fallback} for {filename}")
                    except ValueError:
                        pass

            entity_map[filename] = classification["entity"].value
            combined_results[filename]["_entity"] = classification["entity"].value
            combined_results[filename]["_entity_confidence"] = classification[
                "confidence"
            ]
            combined_results[filename]["_entity_grain"] = classification["grain"]

            if classification["notes"]:
                combined_results[filename]["_entity_notes"] = classification["notes"]

            _LOG.info(
                f"Entity: {filename} → {classification['entity'].value}"
                f" (conf={classification['confidence']:.2f})"
                f"{' | ' + classification['notes'] if classification['notes'] else ''}"
            )

        return entity_map
