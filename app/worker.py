import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.manager import RunManager, IOStreamRedirector
from pipeline.core import set_thread, new_thread_id


def execute_pipeline(manager: RunManager):
    from crew import DataEngineeringFlow

    with IOStreamRedirector(manager):
        # One LangSmith thread per pipeline run: every crew the flow kicks off
        # (profiling → … → final report) groups into a single conversation view.
        set_thread(new_thread_id("pipeline"))
        try:
            flow = DataEngineeringFlow()
            flow.state.user_instructions = getattr(manager, "instructions", "")
            flow.state.user_intent = getattr(manager, "business_intent", {}) or {}
            flow.kickoff()
            manager.warehouse_db_path = flow.state.db_path
            manager.entity_map = dict(flow.state.entity_map)
            manager.complete()
        except SystemExit:
            manager.fail("Pipeline halted before completion — see logs for details.")
        except Exception as e:
            manager.fail(str(e))
        finally:
            set_thread(None)
