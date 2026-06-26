import os
from typing import Type
from crewai.tools import BaseTool
from pydantic import BaseModel, Field, PrivateAttr
import polars as pl

from .csv_loader import CSVLoader, TypeInspector, SchemaShiftDetector


class ProfileCSVInput(BaseModel):
    file_path: str = Field(..., description="Path to file.")


class ProfileCSVFileTool(BaseTool):
    name: str = "profile_csv_file"
    description: str = "Profiles a dataset file returning row counts, column types, and anomaly signals."
    args_schema: Type[BaseModel] = ProfileCSVInput
    _data_dir: str = PrivateAttr()

    def __init__(self, data_dir: str, **kwargs):
        super().__init__(**kwargs)
        self._data_dir = data_dir

    def _load(self, full_path: str) -> tuple:
        ext = os.path.splitext(full_path)[1].lower()
        if ext == ".csv":
            lf, enc = CSVLoader.scan(full_path)
        elif ext == ".json":
            try:
                lf = pl.scan_ndjson(full_path)
            except Exception:
                lf = pl.read_json(full_path).lazy()
            enc = "utf-8"
        elif ext in (".xlsx", ".xls"):
            lf = pl.read_excel(full_path, engine="calamine").lazy()
            enc = "n/a"
        else:
            raise ValueError(f"Unsupported format: {ext}")
        return lf.rename({c: c.strip() for c in lf.collect_schema().names()}), enc

    def _run(self, file_path: str) -> str:
        try:
            full_path = os.path.join(self._data_dir, os.path.basename(file_path))
            lf, encoding = self._load(full_path)

            total_rows = lf.select(pl.len()).collect(engine="streaming").item()
            unique_rows = (
                lf.unique().select(pl.len()).collect(engine="streaming").item()
            )
            duplicates = total_rows - unique_rows
            columns = lf.collect_schema().names()

            exprs = []
            for col in columns:
                exprs.extend(
                    [
                        pl.col(col).null_count().alias(f"{col}_nulls"),
                        pl.col(col).n_unique().alias(f"{col}_uniques"),
                    ]
                )
            agg_df = lf.select(exprs).collect(engine="streaming")
            df_sample = lf.head(500).collect(engine="streaming")

            col_types = TypeInspector.infer(df_sample)
            shift_info = SchemaShiftDetector.detect(lf, total_rows, columns)

            lines = [
                f"Dataset Profile: {file_path}",
                f"Encoding: {encoding}",
                f"Total Rows: {total_rows}",
                f"Total Columns: {len(columns)}",
                f"Duplicate Rows: {duplicates}",
            ]
            if shift_info["detected"]:
                lines.append(
                    f"Schema Shift: DETECTED at approximately row {shift_info['approximate_row']}"
                )
                lines.append(
                    f"  Affected columns: {[s['column'] for s in shift_info['affected_columns']]}"
                )
                lines.append(f"  Recommendation: {shift_info['recommendation']}")
            else:
                lines.append("Schema Shift: NOT DETECTED")

            lines += [
                "",
                "Column Details:",
                f"{'Column':<28} | {'Detected Type':<14} | {'Date Format':<18} | "
                f"{'Non-Null':<9} | {'Nulls':<9} | {'Unique':<10}",
            ]
            for col in columns:
                null_count = agg_df.get_column(f"{col}_nulls")[0]
                unique_count = agg_df.get_column(f"{col}_uniques")[0]
                non_null = total_rows - null_count
                type_info = col_types.get(col, {})
                detected = type_info.get("detected_type", "STRING")
                date_fmt = type_info.get("date_format", "")
                lines.append(
                    f"{col:<28} | {detected:<14} | {date_fmt:<18} | "
                    f"{non_null:<9} | {null_count:<9} | {unique_count:<10}"
                )

            lines.append("\nSamples (up to 3 distinct non-null values per column):")
            for col in columns:
                samples = [
                    str(x)
                    for x in df_sample[col].drop_nulls().unique().head(3).to_list()
                ]
                lines.append(f"- {col}: {', '.join(samples)}")

            return "\n".join(lines)
        except Exception as e:
            return f"Error profiling dataset: {str(e)}"

    def profile_as_dict(self, file_path: str) -> dict:
        full_path = os.path.join(self._data_dir, os.path.basename(file_path))
        lf, encoding = self._load(full_path)

        total_rows = lf.select(pl.len()).collect(engine="streaming").item()
        unique_rows = lf.unique().select(pl.len()).collect(engine="streaming").item()
        duplicates = total_rows - unique_rows
        columns = lf.collect_schema().names()

        exprs = []
        for col in columns:
            exprs.extend(
                [
                    pl.col(col).null_count().alias(f"{col}_nulls"),
                    pl.col(col).n_unique().alias(f"{col}_uniques"),
                ]
            )
        agg_df = lf.select(exprs).collect(engine="streaming")
        df_sample = lf.head(500).collect(engine="streaming")

        col_types = TypeInspector.infer(df_sample)
        shift_info = SchemaShiftDetector.detect(lf, total_rows, columns)

        col_details = []
        sample_values: dict = {}
        for col in columns:
            null_count = int(agg_df.get_column(f"{col}_nulls")[0])
            unique_count = int(agg_df.get_column(f"{col}_uniques")[0])
            null_pct = round(null_count / total_rows * 100, 5) if total_rows else 0.0
            type_info = col_types.get(col, {})
            col_details.append(
                {
                    "name": col,
                    "datatype": type_info.get("detected_type", "STRING"),
                    "unique_count": unique_count,
                    "null_count": null_count,
                    "null_percentage": null_pct,
                }
            )
            samples = [
                str(x) for x in df_sample[col].drop_nulls().unique().head(3).to_list()
            ]
            if samples:
                sample_values[col] = samples

        anomalies = []
        if shift_info["detected"]:
            anomalies.append(
                f"Schema Shift: DETECTED at approximately row {shift_info['approximate_row']}. "
                f"Affected columns: {[s['column'] for s in shift_info['affected_columns']]}"
            )

        return {
            "file_name": file_path,
            "row_count": total_rows,
            "column_count": len(columns),
            "duplicate_rows": duplicates,
            "encoding": encoding,
            "columns": col_details,
            "anomalies": anomalies,
            "sample_values": sample_values,
        }
