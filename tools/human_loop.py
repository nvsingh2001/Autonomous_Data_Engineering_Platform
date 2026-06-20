import sys

class HumanLoopService:
    @staticmethod
    def request_human_approval(score: float, issues_summary: str) -> bool:
        print("\n" + "="*60)
        print("🚨 WARNING: DATA QUALITY ALERT 🚨")
        print(f"Dataset Quality Score: {score}/100 (Threshold is 80)")
        print(f"Key issues identified:\n{issues_summary}")
        print("="*60)
        
        sys.stdout.write("\nDo you approve proceeding with the data warehouse schema design and transformations? (yes/no): ")
        sys.stdout.flush()
        
        line = sys.stdin.readline().strip().lower()
        if line in ["yes", "y", "approve"]:
            print("✅ Operator approved. Proceeding with pipeline...")
            return True
        else:
            print("❌ Operator rejected. Halting pipeline execution.")
            return False
