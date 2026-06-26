from enum import Enum
from typing import Dict, List


class ECommerceEntity(str, Enum):
    ORDERS = "orders"
    ORDER_ITEMS = "order_items"
    PAYMENTS = "payments"
    REVIEWS = "reviews"
    PRODUCTS = "products"
    CUSTOMERS = "customers"
    SELLERS = "sellers"
    SESSIONS = "sessions"
    PAGEVIEWS = "pageviews"
    REFUNDS = "refunds"
    FINANCIALS = "financials"
    INVENTORY = "inventory"
    CAMPAIGNS = "campaigns"
    SHIPMENTS = "shipments"
    GEOLOCATION = "geolocation"
    PROMOTIONS = "promotions"
    CATEGORIES = "categories"
    UNKNOWN = "unknown"


class EntityClassifier:
    _SIGNALS: Dict[ECommerceEntity, Dict] = {
        ECommerceEntity.ORDERS: {
            "required_any": [
                [
                    "order_id",
                    "order id",
                    "orderid",
                    "order_number",
                    "order number",
                    "order_no",
                    "sales_order",
                    "invoice_id",
                    "invoice_no",
                ],
                [
                    "gross_amt",
                    "gross_amount",
                    "gross amt",
                    "net_amount",
                    "total_amount",
                    "sale_amount",
                    "invoice_amount",
                ],
            ],
            "supporting": [
                "customer",
                "total",
                "amount",
                "status",
                "date",
                "shipping",
                "payment",
                "currency",
                "channel",
                "fulfilled",
                "fulfilment",
                "courier",
                "qty",
                "quantity",
                "subtotal",
                "tax",
                "sku",
                "pcs",
                "rate",
                "price",
            ],
            "min_supporting": 2,
            "disqualify_if": ["pageview_id", "refund_id"],
            "grain": "one row per order / sales transaction",
        },
        ECommerceEntity.ORDER_ITEMS: {
            "required_any": [
                ["order_id", "order id", "orderid"],
                ["sku", "product_id", "item_id", "asin", "sku_code"],
            ],
            "supporting": [
                "quantity",
                "qty",
                "unit_price",
                "line_total",
                "discount",
                "product_name",
                "category",
                "size",
                "color",
            ],
            "min_supporting": 2,
            "requires_all_groups": True,
            "disqualify_if": ["session_id", "pageview_id", "refund_id"],
            "grain": "one row per order line item",
        },
        ECommerceEntity.PAYMENTS: {
            "required_any": [
                [
                    "payment_type",
                    "payment_method",
                    "payment_installments",
                    "payment_sequential",
                ],
                ["payment_value", "payment_amount", "amount_paid", "paid_amount"],
            ],
            "supporting": [
                "order_id",
                "installments",
                "voucher",
                "credit_card",
                "boleto",
                "debit_card",
                "currency",
                "transaction_id",
            ],
            "min_supporting": 1,
            "disqualify_if": ["session_id", "pageview_id", "sku", "product_id"],
            "grain": "one row per payment transaction / installment",
        },
        ECommerceEntity.REVIEWS: {
            "required_any": [
                ["review_id", "review_score", "review_comment", "rating_id"],
                ["review_creation_date", "review_answer_timestamp", "answered_at"],
            ],
            "supporting": [
                "order_id",
                "rating",
                "score",
                "comment",
                "title",
                "message",
                "satisfaction",
                "nps",
                "csat",
                "created_at",
            ],
            "min_supporting": 1,
            "disqualify_if": ["session_id", "sku", "payment_type", "product_id"],
            "grain": "one row per customer review / rating",
        },
        ECommerceEntity.PRODUCTS: {
            "required_any": [
                [
                    "sku",
                    "sku_code",
                    "sku code",
                    "product_id",
                    "asin",
                    "item_id",
                    "article_id",
                    "item_code",
                    "product_code",
                    "design_no",
                    "style_id",
                    "style id",
                    "design no",
                    "product_category_name",
                ],
            ],
            "supporting": [
                "name",
                "title",
                "category",
                "price",
                "mrp",
                "cost",
                "color",
                "size",
                "brand",
                "style",
                "weight",
                "description",
                "image",
            ],
            "min_supporting": 2,
            "disqualify_if": [
                "order_id",
                "session_id",
                "gross_amt",
                "gross_amount",
                "gross amt",
                "rate",
                "pcs",
                "pieces",
                "amount",
            ],
            "grain": "one row per product / SKU",
        },
        ECommerceEntity.SELLERS: {
            "required_any": [
                ["seller_id", "vendor_id", "merchant_id", "supplier_id", "store_id"],
            ],
            "supporting": [
                "seller_city",
                "seller_state",
                "seller_zip",
                "seller_name",
                "city",
                "state",
                "zip_code",
                "zip_code_prefix",
                "rating",
                "commission",
            ],
            "min_supporting": 1,
            "disqualify_if": ["order_id", "customer_id", "session_id", "sku"],
            "grain": "one row per seller / vendor / merchant",
        },
        ECommerceEntity.CUSTOMERS: {
            "required_any": [
                ["customer_id", "user_id", "buyer_id", "member_id", "client_id"],
            ],
            "supporting": [
                "name",
                "email",
                "phone",
                "address",
                "city",
                "state",
                "country",
                "zip",
                "postal",
                "signup",
                "created",
                "segment",
            ],
            "min_supporting": 2,
            "disqualify_if": ["order_id", "sku", "session_id"],
            "grain": "one row per customer",
        },
        ECommerceEntity.SESSIONS: {
            "required_any": [
                [
                    "session_id",
                    "website_session_id",
                    "visit_id",
                    "ga_session_id",
                    "websitesessionid",
                ],
            ],
            "supporting": [
                "utm_source",
                "utm_campaign",
                "utm_content",
                "device",
                "channel",
                "user_agent",
                "referrer",
                "landing_page",
                "is_repeat",
            ],
            "min_supporting": 1,
            "disqualify_if": [
                "order_id",
                "sku",
                "refund_id",
                "price_usd",
                "items_purchased",
                "gross_amt",
            ],
            "grain": "one row per web session",
        },
        ECommerceEntity.PAGEVIEWS: {
            "required_any": [
                ["pageview_id", "website_pageview_id"],
                ["page_url", "page_path", "url"],
            ],
            "supporting": [
                "session_id",
                "created_at",
                "page_type",
                "time_on_page",
                "pageview_url",
            ],
            "min_supporting": 1,
            "disqualify_if": ["order_id", "sku", "customer_id"],
            "grain": "one row per page view event",
        },
        ECommerceEntity.REFUNDS: {
            "required_any": [
                ["refund_id", "return_id", "order_item_refund_id", "refund"],
            ],
            "supporting": [
                "order_id",
                "refund_amount",
                "reason",
                "returned_at",
                "item_id",
            ],
            "min_supporting": 1,
            "disqualify_if": ["session_id", "pageview_id"],
            "grain": "one row per refund / return",
        },
        ECommerceEntity.FINANCIALS: {
            "required_any": [
                [
                    "expense",
                    "expanse",
                    "expance",
                    "income",
                    "recived_amount",
                    "received_amount",
                    "receipt",
                    "expenditure",
                    "opex",
                    "profit",
                    "loss",
                    "p_l",
                ],
            ],
            "supporting": [
                "date",
                "category",
                "vendor",
                "description",
                "balance",
                "debit",
                "credit",
                "journal",
            ],
            "min_supporting": 1,
            "disqualify_if": ["order_id", "sku", "session_id", "customer_id"],
            "grain": "one row per financial transaction / ledger entry",
        },
        ECommerceEntity.INVENTORY: {
            "required_any": [
                [
                    "stock",
                    "inventory",
                    "warehouse",
                    "on_hand",
                    "available_qty",
                    "reorder_point",
                    "bin_location",
                ],
            ],
            "supporting": ["sku", "product_id", "quantity", "location", "updated"],
            "min_supporting": 1,
            "disqualify_if": ["order_id", "session_id", "customer_id"],
            "grain": "one row per SKU / warehouse location",
        },
        ECommerceEntity.CAMPAIGNS: {
            "required_any": [
                [
                    "campaign_id",
                    "campaign_name",
                    "utm_campaign",
                    "ad_id",
                    "ad_group",
                    "ad_spend",
                    "impressions",
                    "clicks",
                ],
            ],
            "supporting": [
                "source",
                "medium",
                "cost",
                "revenue",
                "roas",
                "cpc",
                "ctr",
                "conversions",
                "budget",
            ],
            "min_supporting": 1,
            "disqualify_if": ["order_id", "session_id", "sku"],
            "grain": "one row per campaign / ad group",
        },
        ECommerceEntity.SHIPMENTS: {
            "required_any": [
                [
                    "tracking_number",
                    "tracking_id",
                    "tracking_url",
                    "shipment_id",
                    "awb_number",
                ],
                [
                    "carrier",
                    "courier_name",
                    "shipping_carrier",
                    "delivery_company",
                ],
            ],
            "supporting": [
                "order_id",
                "shipped_at",
                "shipped_date",
                "estimated_delivery",
                "delivery_status",
                "delivery_date",
                "dispatched",
                "in_transit",
            ],
            "min_supporting": 1,
            "disqualify_if": ["session_id", "pageview_id", "payment_type", "sku"],
            "grain": "one row per shipment / parcel",
        },
        ECommerceEntity.GEOLOCATION: {
            "required_any": [
                ["geolocation_lat", "lat", "latitude"],
                ["geolocation_lng", "geolocation_lon", "lng", "lon", "longitude"],
            ],
            "supporting": [
                "zip_code",
                "zip_code_prefix",
                "postal_code",
                "city",
                "state",
                "country",
                "geolocation_city",
                "geolocation_state",
            ],
            "min_supporting": 1,
            "requires_all_groups": True,
            "disqualify_if": ["order_id", "session_id", "payment_type", "sku"],
            "grain": "one row per zip code / geographic lookup",
        },
        ECommerceEntity.PROMOTIONS: {
            "required_any": [
                [
                    "coupon_code",
                    "discount_code",
                    "promo_code",
                    "voucher_code",
                    "coupon_id",
                    "promotion_id",
                    "offer_id",
                ],
            ],
            "supporting": [
                "discount_amount",
                "discount_pct",
                "discount_percent",
                "valid_from",
                "valid_until",
                "expiry_date",
                "min_order_value",
                "usage_limit",
                "times_used",
            ],
            "min_supporting": 1,
            "disqualify_if": ["order_id", "session_id", "sku", "tracking_number"],
            "grain": "one row per promotion / coupon definition",
        },
        ECommerceEntity.CATEGORIES: {
            "required_any": [
                [
                    "category_id",
                    "category_name",
                    "product_category_name",
                ],
                [
                    "parent_id",
                    "parent_category",
                    "parent_category_id",
                ],
            ],
            "supporting": [
                "subcategory",
                "breadcrumb",
                "hierarchy_level",
                "category_slug",
                "display_name",
                "sort_order",
            ],
            "min_supporting": 1,
            "disqualify_if": [
                "order_id",
                "session_id",
                "sku",
                "customer_id",
                "payment_type",
                "tracking_number",
            ],
            "grain": "one row per product category / hierarchy node",
        },
    }

    @staticmethod
    def _normalize(col: str) -> str:
        import re

        return re.sub(r"[^a-z0-9]", "_", col.lower().strip())

    @staticmethod
    def _filename_tokens(filename: str) -> List[str]:
        import re as _re

        name = _re.sub(r"\.(csv|xlsx?|json)$", "", filename.lower())
        return _re.sub(r"[^a-z0-9]", " ", name).split()

    @classmethod
    def classify(
        cls, columns: List[str], row_count: int = 0, filename: str = ""
    ) -> Dict:
        normalized = [cls._normalize(c) for c in columns]
        if filename:
            normalized = normalized + cls._filename_tokens(filename)
        scores: Dict[ECommerceEntity, float] = {}
        matched: Dict[ECommerceEntity, List[str]] = {}

        for entity, signals in cls._SIGNALS.items():
            score = 0.0
            hits: List[str] = []
            required_groups = signals.get("required_any", [])
            requires_all = signals.get("requires_all_groups", False)
            groups_met = 0

            for group in required_groups:
                for sig in group:
                    if any(sig in col for col in normalized):
                        groups_met += 1
                        hits.append(sig)
                        break

            if requires_all and groups_met < len(required_groups):
                continue
            if not requires_all and groups_met == 0:
                continue

            score += groups_met * 10.0

            for sig in signals.get("supporting", []):
                if any(sig in col for col in normalized):
                    score += 2.0
                    hits.append(sig)

            for sig in signals.get("disqualify_if", []):
                if any(sig in col for col in normalized):
                    score -= 6.0

            if score > 0:
                scores[entity] = score
                matched[entity] = hits

        if not scores:
            return {
                "entity": ECommerceEntity.UNKNOWN,
                "confidence": 0.0,
                "grain": "unknown",
                "matched_signals": [],
                "notes": "No recognisable e-commerce signals found in column names.",
            }

        best = max(scores, key=scores.get)
        best_score = scores[best]
        max_possible = 10.0 * len(
            cls._SIGNALS[best].get("required_any", [])
        ) + 2.0 * len(cls._SIGNALS[best].get("supporting", []))
        confidence = min(best_score / max(max_possible, 1.0), 1.0)

        sorted_scores = sorted(scores.values(), reverse=True)
        note = ""
        if len(sorted_scores) >= 2 and sorted_scores[1] >= best_score * 0.80:
            second = [e for e, s in scores.items() if s == sorted_scores[1]][0]
            note = f"Ambiguous: also matches '{second.value}' (score {sorted_scores[1]:.0f} vs {best_score:.0f})"

        return {
            "entity": best,
            "confidence": round(confidence, 2),
            "grain": cls._SIGNALS[best].get("grain", ""),
            "matched_signals": matched[best],
            "notes": note,
        }

    @classmethod
    def classify_dataset(cls, file_profiles: Dict[str, Dict]) -> Dict[str, Dict]:
        return {
            filename: cls.classify(
                profile.get("columns")
                or list(profile.get("column_details", {}).keys()),
                row_count=profile.get("row_count", profile.get("total_rows", 0)),
                filename=filename,
            )
            for filename, profile in file_profiles.items()
        }
