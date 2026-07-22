import re

# TypeInspector (tools/csv_loader.py) only recognizes bare dates, not datetime-with-time
# strings like "2018-02-22 21:04:23" — extremely common (order/delivery timestamps), so
# such columns profile as STRING, not DATE. Trusting that cached label alone would leave
# every temporal gate permanently unreachable on real e-commerce data. Instead, temporal
# gates fall back to a real check of their own: parse the actual sample values.
_DATETIME_PATTERNS = [
    re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}:\d{2})?$"),
    re.compile(r"^\d{2}/\d{2}/\d{4}$"),
]


def _looks_temporal(sample_values: list | None) -> bool:
    values = [str(v) for v in (sample_values or [])]
    if not values:
        return False
    hits = sum(1 for v in values if any(p.match(v) for p in _DATETIME_PATTERNS))
    return hits / len(values) >= 0.5


_STRUCTURAL_GATES: dict[str, dict] = {
    "monetary_amount": {"datatypes": {"INTEGER", "FLOAT"}},
    "quantity": {"datatypes": {"INTEGER", "FLOAT"}},
    "temporal": {"datatypes": {"DATE", "STRING"}, "temporal_fallback_for": {"STRING"}},
    "unique_identifier": {"datatypes": {"INTEGER", "STRING"}, "min_uniqueness": 0.98},
    "categorical_label": {"datatypes": {"STRING", "INTEGER"}, "max_uniqueness": 0.5},
    "geographic": {"datatypes": {"STRING", "FLOAT", "INTEGER"}},
    "free_text": {"datatypes": {"STRING"}, "min_uniqueness": 0.5},
}

# Identifier-family business roles get a permissive gate (no uniqueness bound) — a
# foreign key like order_id on order_items.csv is deliberately non-unique, but must
# still ground as order_identifier or the order-id-matcher fix this exists for is defeated.
_IDENTIFIER_BUSINESS_ROLES = {
    "order_identifier",
    "customer_identifier",
    "product_identifier",
    "seller_identifier",
}

_BUSINESS_GATES: dict[str, dict] = {
    "payment_type": {"datatypes": {"STRING", "INTEGER"}, "max_null_pct": 50},
    "order_status": {"datatypes": {"STRING"}, "max_null_pct": 50},
    "review_score": {"datatypes": {"INTEGER", "FLOAT"}, "max_null_pct": 50},
    "shipping_method": {"datatypes": {"STRING"}, "max_null_pct": 50},
    "estimated_delivery_date": {
        "datatypes": {"DATE", "STRING"},
        "temporal_fallback_for": {"STRING"},
        "max_null_pct": 50,
    },
    "actual_delivery_date": {
        "datatypes": {"DATE", "STRING"},
        "temporal_fallback_for": {"STRING"},
        "max_null_pct": 100,
    },
    "category_label": {"datatypes": {"STRING"}, "max_null_pct": 50},
    "category_translation_reference": {"datatypes": {"STRING"}, "max_null_pct": 50},
    "none": {},
    **{role: {"datatypes": {"STRING", "INTEGER"}, "max_null_pct": 5} for role in _IDENTIFIER_BUSINESS_ROLES},
}


def _column_stats(profiling_data: dict, file: str, column: str) -> dict | None:
    profile = profiling_data.get(file)
    if not profile:
        return None
    row_count = profile.get("row_count") or 0
    sample_values = (profile.get("sample_values") or {}).get(column, [])
    for c in profile.get("columns", []):
        if c.get("name") == column:
            unique_count = c.get("unique_count") or 0
            return {
                "datatype": c.get("datatype", "STRING"),
                "null_percentage": c.get("null_percentage", 0.0),
                "uniqueness_ratio": (unique_count / row_count) if row_count else 0.0,
                "sample_values": sample_values,
            }
    return None


def _passes_gate(stats: dict, gate: dict) -> bool:
    datatypes = gate.get("datatypes")
    if datatypes is not None and stats["datatype"] not in datatypes:
        return False
    if stats["datatype"] in gate.get("temporal_fallback_for", set()) and not _looks_temporal(
        stats.get("sample_values")
    ):
        return False
    if "min_uniqueness" in gate and stats["uniqueness_ratio"] < gate["min_uniqueness"]:
        return False
    if "max_uniqueness" in gate and stats["uniqueness_ratio"] > gate["max_uniqueness"]:
        return False
    if "max_null_pct" in gate and stats["null_percentage"] > gate["max_null_pct"]:
        return False
    return True


class SemanticGrounder:
    """Verifies every LLM-proposed column role against real, already-computed profiling
    stats before anything downstream trusts it — same "a check an LLM can fake is not a
    check" discipline WarehouseMetrics already applies to table/column name guesses.
    Pure Python, no LLM. A failed proposal is kept in the output (flagged ungrounded, for
    transparency) rather than dropped, so consumers can see what was rejected and why."""

    @staticmethod
    def ground(raw_columns: list[dict], profiling_data: dict) -> list[dict]:
        grounded: list[dict] = []
        for proposal in raw_columns:
            stats = _column_stats(profiling_data, proposal["file"], proposal["column"])
            entry = dict(proposal)
            if stats is None:
                entry["structural_grounded"] = False
                entry["business_grounded"] = False
                entry["grounding_note"] = "column not found in profiling data"
                grounded.append(entry)
                continue

            struct_gate = _STRUCTURAL_GATES.get(proposal["structural_role"], {})
            entry["structural_grounded"] = _passes_gate(stats, struct_gate)

            business_role = proposal.get("business_role", "none")
            business_gate = _BUSINESS_GATES.get(business_role, {})
            entry["business_grounded"] = _passes_gate(stats, business_gate)

            entry["uniqueness_ratio"] = round(stats["uniqueness_ratio"], 4)
            grounded.append(entry)
        return grounded

    @staticmethod
    def build_lookup(grounded_columns: list[dict]) -> dict[str, list[dict]]:
        """Groups by normalized column name (same normalization utils/metrics.py already
        uses: lower(), strip underscores) into occurrence lists — cross-file occurrences of
        the same name are kept separate, never merged, since e.g. order_id is a unique PK
        in orders.csv but a repeating FK in order_items.csv."""
        lookup: dict[str, list[dict]] = {}
        for entry in grounded_columns:
            key = entry["column"].lower().replace("_", "")
            lookup.setdefault(key, []).append(entry)
        return lookup
