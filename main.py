import os
os.environ["OTEL_SDK_DISABLED"] = "true"

from flow import DataEngineeringFlow

if __name__ == "__main__":
    flow = DataEngineeringFlow()
    flow.kickoff()
