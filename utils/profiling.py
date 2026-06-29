def extract_columns_from_raw(raw_profile: dict, fname: str) -> tuple[list, int]:
    """Extract column names and row count from a raw profiling dict when Pydantic output is unavailable."""
    expected = (
        "columns",
        "row_count",
        "total_rows",
        "column_details",
        "structural_metadata",
        "files",
    )

    def _unwrap(parsed: dict) -> dict:
        if any(k in parsed for k in expected):
            return parsed
        for key in (f"data/{fname}", fname):
            inner = parsed.get(key)
            if isinstance(inner, dict):
                if any(k in inner for k in expected):
                    return inner
                for key2 in (f"data/{fname}", fname):
                    inner2 = inner.get(key2)
                    if isinstance(inner2, dict) and any(k in inner2 for k in expected):
                        return inner2
        return parsed

    profile = _unwrap(raw_profile)

    if "files" in profile and isinstance(profile["files"], list):
        for entry in profile["files"]:
            if isinstance(entry, dict):
                cols = entry.get("columns", [])
                if cols:
                    if isinstance(cols[0], dict):
                        return (
                            [
                                c.get("column_name", c.get("name", ""))
                                for c in cols
                                if isinstance(c, dict)
                            ],
                            profile.get("row_count", 0),
                        )
                    return cols, profile.get("row_count", 0)

    meta = profile.get("structural_metadata")
    if isinstance(meta, list) and meta and isinstance(meta[0], dict):
        names = [
            c.get("column", c.get("column_name", c.get("name", "")))
            for c in meta
            if isinstance(c, dict)
        ]
        names = [n for n in names if n]
        if names:
            return names, profile.get(
                "row_count", profile.get("file_footprint", {}).get("row_count", 0)
            )

    raw_cols = profile.get("columns") or list(profile.get("column_details", {}).keys())
    if isinstance(raw_cols, dict):
        return list(raw_cols.keys()), profile.get(
            "row_count", profile.get("total_rows", 0)
        )
    if raw_cols and isinstance(raw_cols[0], dict):
        return (
            [
                c.get("column_name", c.get("name", ""))
                for c in raw_cols
                if isinstance(c, dict)
            ],
            profile.get("row_count", profile.get("total_rows", 0)),
        )
    return raw_cols or [], profile.get("row_count", profile.get("total_rows", 0))
