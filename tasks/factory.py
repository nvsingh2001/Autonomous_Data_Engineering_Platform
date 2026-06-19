from crewai import Task

class TaskFactory:
    def __init__(self, agents_dict: dict):
        self._agents = agents_dict

    def create_profiling_task(self, files_list: list) -> Task:
        files_str = ", ".join(files_list)
        return Task(
            description=f"Profile the following CSV datasets in the 'data' folder: {files_str}. "
                        "Determine row and column count, data types, null counts, duplicate counts, and structural characteristics.",
            expected_output="JSON profiling summary outlining statistics, duplicate count, and null columns for each file.",
            agent=self._agents["profiler"]
        )

    def create_quality_task(self, profiling_context: Task) -> Task:
        return Task(
            description="Perform a thorough data quality analysis of the raw CSV files. "
                        "Identify format inconsistencies (dates, phones), duplicate IDs, negative values, and referential integrity issues. "
                        "Compute a final dataset quality score (out of 100) and list anomalies.",
            expected_output="Markdown quality report identifying bugs and ending with a clear line: 'Quality Score: [number]/100'.",
            agent=self._agents["quality_engineer"],
            context=[profiling_context]
        )

    def create_schema_design_task(self, profiling_context: Task) -> Task:
        return Task(
            description="Inspect column relationships and design a Star Schema for an analytics data warehouse. "
                        "Identify which tables are Fact tables and which are Dimension tables (e.g. DimCustomer, DimProduct, DimDate). "
                        "Specify relationships, primary keys, and foreign keys.",
            expected_output="Markdown document outlining the star schema layout, Fact and Dimension tables, and key mappings.",
            agent=self._agents["warehouse_architect"],
            context=[profiling_context]
        )

    def create_transformation_task(self, quality_context: Task, schema_context: Task) -> Task:
        return Task(
            description="Generate SQL data transformation scripts (using DuckDB SQL syntax) to clean raw tables and populate the Star Schema. "
                        "Deduplicate records, standardize dates and phones, handle missing values, filter negative values, and ensure referential integrity. "
                        "Generate CREATE TABLE and INSERT INTO SQL queries.",
            expected_output="SQL script containing clean DuckDB SQL statements for creating and loading the star schema tables.",
            agent=self._agents["warehouse_architect"],
            context=[quality_context, schema_context]
        )

    def create_business_insights_task(self, transformation_context: Task) -> Task:
        return Task(
            description="Query the star schema tables to calculate business KPIs (revenue, sales volumes, category distributions, support ratings). "
                        "Identify patterns like customer tier revenue share and ticket resolution metrics.",
            expected_output="Markdown report with key business KPIs and summary of findings.",
            agent=self._agents["analytics_engineer"],
            context=[transformation_context]
        )

    def create_final_report_task(self, context_list: list) -> Task:
        return Task(
            description="Review all data warehouse deliverables (Profiling, Quality, Schema, SQL, KPI reports). "
                        "Synthesize them into a final executive summary and recommendations package.",
            expected_output="Comprehensive Markdown executive summary documenting the warehouse engineering results.",
            agent=self._agents["lead_architect"],
            context=context_list
        )
