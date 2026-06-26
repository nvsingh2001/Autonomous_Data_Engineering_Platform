import io
import duckdb
import polars as pl


class CSVLoader:
    NULL_STRINGS = [
        "NULL",
        "null",
        "Null",
        "NA",
        "N/A",
        "n/a",
        "NaN",
        "nan",
        "NAN",
        "None",
        "none",
        "NONE",
        "NIL",
        "Nil",
        "nill",
        "Nill",
        "NILL",
        "#N/A",
        "#NULL!",
        "#VALUE!",
        "-",
        "--",
        "---",
        "?",
        "undefined",
        "UNDEFINED",
        "missing",
        "MISSING",
        "n.a.",
        "N.A.",
    ]

    @staticmethod
    def detect_encoding(file_path: str) -> str:
        try:
            from charset_normalizer import from_path

            result = from_path(
                file_path, cp_isolation=["utf-8", "latin-1", "cp1252"]
            ).best()
            return str(result.encoding) if result else "utf-8"
        except Exception:
            return "utf-8"

    @classmethod
    def _is_utf8(cls, encoding: str) -> bool:
        return encoding.lower().replace("-", "").replace("_", "") in (
            "utf8",
            "ascii",
            "utf8sig",
        )

    @classmethod
    def read(cls, file_path: str, **kwargs) -> pl.DataFrame:
        encoding = cls.detect_encoding(file_path)
        if cls._is_utf8(encoding):
            return pl.read_csv(
                file_path, null_values=cls.NULL_STRINGS, infer_schema_length=0, **kwargs
            )
        with open(file_path, "r", encoding=encoding, errors="replace") as f:
            content = f.read()
        return pl.read_csv(
            io.BytesIO(content.encode("utf-8")),
            null_values=cls.NULL_STRINGS,
            infer_schema_length=0,
            **kwargs,
        )

    @classmethod
    def scan(cls, file_path: str) -> tuple:
        encoding = cls.detect_encoding(file_path)
        if cls._is_utf8(encoding):
            return pl.scan_csv(
                file_path, null_values=cls.NULL_STRINGS, infer_schema_length=0
            ), encoding
        with open(file_path, "r", encoding=encoding, errors="replace") as f:
            content = f.read()
        lf = pl.read_csv(
            io.BytesIO(content.encode("utf-8")),
            null_values=cls.NULL_STRINGS,
            infer_schema_length=0,
        ).lazy()
        return lf, encoding


class TypeInspector:
    DATE_FORMATS = [
        ("%Y-%m-%d", "ISO"),
        ("%m/%d/%Y", "US"),
        ("%d/%m/%Y", "EU"),
        ("%m-%d-%y", "US-short"),
        ("%d-%m-%Y", "EU-long"),
        ("%Y/%m/%d", "ISO-slash"),
        ("%m/%d/%y", "US-short-slash"),
        ("%d/%m/%y", "EU-short-slash"),
        ("%Y%m%d", "compact"),
        ("%d-%b-%Y", "DD-Mon-YYYY"),
        ("%b %d, %Y", "Mon-DD-YYYY"),
    ]
    _THRESHOLD = 0.80

    @classmethod
    def infer(cls, df_sample: pl.DataFrame) -> dict:
        if df_sample.is_empty():
            return {}
        results = {}
        sample_count = max(len(df_sample), 1)
        conn = duckdb.connect(":memory:")
        conn.register("__sample__", df_sample)
        for col in df_sample.columns:
            qcol = col.replace('"', '""')
            detected = "STRING"
            date_fmt = None
            try:
                n = conn.execute(
                    f'SELECT COUNT(*) FILTER (WHERE TRY_CAST("{qcol}" AS BIGINT) IS NOT NULL'
                    f" AND \"{qcol}\" != '') FROM __sample__"
                ).fetchone()[0]
                if n / sample_count >= cls._THRESHOLD:
                    detected = "INTEGER"
                else:
                    n = conn.execute(
                        f'SELECT COUNT(*) FILTER (WHERE TRY_CAST("{qcol}" AS DOUBLE) IS NOT NULL'
                        f" AND \"{qcol}\" != '') FROM __sample__"
                    ).fetchone()[0]
                    if n / sample_count >= cls._THRESHOLD:
                        detected = "FLOAT"
                    else:
                        for fmt, _ in cls.DATE_FORMATS:
                            n = conn.execute(
                                f"SELECT COUNT(*) FILTER (WHERE TRY_STRPTIME(\"{qcol}\", '{fmt}') IS NOT NULL"
                                f" AND \"{qcol}\" != '') FROM __sample__"
                            ).fetchone()[0]
                            if n / sample_count >= cls._THRESHOLD:
                                detected = "DATE"
                                date_fmt = fmt
                                break
            except Exception:
                pass
            results[col] = {"detected_type": detected}
            if date_fmt:
                results[col]["date_format"] = date_fmt
        conn.close()
        return results


class SchemaShiftDetector:
    _SLICE_SIZE = 300
    _NULL_JUMP_THRESHOLD = 0.50
    _MIN_ROWS = 400
    _BINARY_SEARCH_ITERS = 8

    @classmethod
    def _locate_shift_row(cls, lf: pl.LazyFrame, col: str, total_rows: int) -> int:
        lo, hi = 0, total_rows
        for _ in range(cls._BINARY_SEARCH_ITERS):
            mid = (lo + hi) // 2
            try:
                chunk = lf.slice(mid, min(100, total_rows - mid)).collect(
                    engine="streaming"
                )
                null_rate = chunk[col].null_count() / max(len(chunk), 1)
                if null_rate > 0.5:
                    hi = mid
                else:
                    lo = mid
            except Exception:
                break
        return lo

    @classmethod
    def detect(cls, lf: pl.LazyFrame, total_rows: int, columns: list) -> dict:
        if total_rows < cls._MIN_ROWS:
            return {"detected": False}
        slice_size = min(cls._SLICE_SIZE, total_rows // 6)
        mid_start = total_rows // 2
        try:
            head_df = lf.head(slice_size).collect(engine="streaming")
            mid_df = lf.slice(mid_start, slice_size).collect(engine="streaming")
            tail_df = lf.tail(slice_size).collect(engine="streaming")
        except Exception:
            return {"detected": False}
        shift_signals = []
        for col in columns:
            try:
                h = head_df[col].null_count() / max(len(head_df), 1)
                m = mid_df[col].null_count() / max(len(mid_df), 1)
                t = tail_df[col].null_count() / max(len(tail_df), 1)
                if max(abs(m - h), abs(t - m), abs(t - h)) > cls._NULL_JUMP_THRESHOLD:
                    shift_signals.append(
                        {
                            "column": col,
                            "head_null_pct": round(h * 100, 1),
                            "mid_null_pct": round(m * 100, 1),
                            "tail_null_pct": round(t * 100, 1),
                        }
                    )
            except Exception:
                pass
        if shift_signals:
            approx_row = cls._locate_shift_row(
                lf, shift_signals[0]["column"], total_rows
            )
            return {
                "detected": True,
                "approximate_row": approx_row,
                "affected_columns": shift_signals,
                "recommendation": (
                    f"Layout changes near row {approx_row}. Use CASE WHEN with row index "
                    "or partition the source table to handle two column layouts."
                ),
            }
        return {"detected": False}
