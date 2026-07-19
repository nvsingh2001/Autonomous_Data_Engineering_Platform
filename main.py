import config
from crew import DataEngineeringFlow
from pipeline.core import set_thread, new_thread_id

if __name__ == "__main__":
    config.assert_valid_config()
    set_thread(new_thread_id("pipeline"))
    flow = DataEngineeringFlow()
    flow.kickoff()
