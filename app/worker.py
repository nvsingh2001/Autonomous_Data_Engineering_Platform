import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.manager import RunManager, IOStreamRedirector


def execute_pipeline(manager: RunManager):
    from flow import DataEngineeringFlow

    with IOStreamRedirector(manager):
        try:
            flow = DataEngineeringFlow()
            flow.kickoff()
            manager.complete()
        except SystemExit:
            manager.fail("Pipeline halted — operator rejected quality checks.")
        except Exception as e:
            manager.fail(str(e))
