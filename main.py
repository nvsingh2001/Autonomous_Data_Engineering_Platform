from crew import DataEngineeringFlow
from pipeline.core import set_thread, new_thread_id

if __name__ == "__main__":
    set_thread(new_thread_id("pipeline"))
    flow = DataEngineeringFlow()
    flow.kickoff()
