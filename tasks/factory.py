from crewai import Task


class TaskFactory:
    def __init__(self, agents_dict: dict):
        self._agents = agents_dict

    def create_profiling_task(self) -> Task:
        return Task(
            description="Profile the CSV datasets in the 'data' folder: {files}. "
            "Determine row and column count, data types, null counts, duplicate counts, and structural characteristics.",
            expected_output="JSON profiling summary outlining statistics, duplicate count, and null columns for each file.",
            agent=self._agents["profiler"],
        )

    def create_quality_task(self) -> Task:
        return Task(
            description="Perform a thorough data quality analysis of the raw CSV files. "
            "Use the following profiling summary for reference: {profiling_results}. "
            "Identify format inconsistencies (dates, phones), duplicate IDs, negative values, and referential integrity issues. "
            "Compute a final dataset quality score (out of 100) and list anomalies.",
            expected_output="Markdown quality report identifying bugs and ending with a clear line: 'Quality Score: [number]/100'.",
            agent=self._agents["quality_engineer"],
        )

    def create_schema_design_task(self) -> Task:
        return Task(
            description="Inspect column relationships and design a Star Schema for an analytics data warehouse. "
            "Refer to the profiling results: {profiling_results}. "
            "Identify which tables are Fact tables and which are Dimension tables (e.g. DimCustomer, DimProduct, DimDate). "
            "Specify relationships, primary keys, and foreign keys.",
            expected_output="Markdown document outlining the star schema layout, Fact and Dimension tables, and key mappings.",
            agent=self._agents["warehouse_architect"],
        )

    def create_transformation_task(self) -> Task:
        return Task(
            description="Generate SQL data transformation scripts (using DuckDB SQL syntax) to clean raw tables and populate the Star Schema. "
            "Refer to the quality report: {quality_report} and star schema design: {star_schema}. "
            "Deduplicate records, standardize dates and phones, handle missing values, filter negative values, and ensure referential integrity. "
            "Generate CREATE TABLE and INSERT INTO SQL queries.",
            expected_output="SQL script containing clean DuckDB SQL statements for creating and loading the star schema tables.",
            agent=self._agents["warehouse_architect"],
        )

    def create_business_insights_task(self) -> Task:
        return Task(
            description="Query the star schema tables to calculate business KPIs (revenue, sales volumes, category distributions, support ratings). "
            "Refer to the transformations SQL script: {clean_sql}. "
            "Identify patterns like customer tier revenue share and ticket resolution metrics.",
            expected_output="Markdown report with key business KPIs and summary of findings.",
            agent=self._agents["analytics_engineer"],
        )

    def create_final_report_task(self) -> Task:
        return Task(
            description="Review all data warehouse deliverables. "
            "Profiling Results: {profiling_results}\n"
            "Quality Report: {quality_report}\n"
            "Star Schema Design: {star_schema}\n"
            "Transformations SQL: {clean_sql}\n"
            "KPI Report: {kpi_report}\n"
            "Synthesize them into a final executive summary and recommendations package.",
            expected_output="Comprehensive Markdown executive summary documenting the warehouse engineering results.",
            agent=self._agents["lead_architect"],
        )
