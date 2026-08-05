"""
Deterministic tool layer — all CSV lookups and arithmetic computations.
Zero LLM involvement here. Called by agents via function calling.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional
from datetime import datetime, timedelta

import pandas as pd

# ---------------------------------------------------------------------------
# Data loading (singleton, lazy)
# ---------------------------------------------------------------------------

_data_dir = Path(__file__).parent.parent / "data"
_dfs: dict[str, pd.DataFrame] = {}


def _load(name: str) -> pd.DataFrame:
    if name not in _dfs:
        path = _data_dir / name
        _dfs[name] = pd.read_csv(str(path), dtype=str, keep_default_na=False)
    return _dfs[name]


def _orders() -> pd.DataFrame:
    return _load("olist_orders_dataset.csv")


def _order_items() -> pd.DataFrame:
    return _load("olist_order_items_dataset.csv")


def _order_payments() -> pd.DataFrame:
    return _load("olist_order_payments_dataset.csv")


def _customers() -> pd.DataFrame:
    return _load("olist_customers_dataset.csv")


def _products() -> pd.DataFrame:
    return _load("olist_products_dataset.csv")


def _sellers() -> pd.DataFrame:
    return _load("olist_sellers_dataset.csv")


def _category_translation() -> pd.DataFrame:
    return _load("product_category_name_translation.csv")


def _none_if_empty(val: Any) -> Optional[str]:
    """Return None for empty or NaN strings."""
    if val is None:
        return None
    s = str(val).strip()
    return None if (s == "" or s.lower() in ("nan", "none", "nat")) else s


def _parse_ts(val: Any) -> Optional[str]:
    """Return a clean YYYY-MM-DD HH:MM:SS timestamp or None."""
    s = _none_if_empty(val)
    if s is None:
        return None
    # Try parsing to validate format then normalise
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# Core lookups
# ---------------------------------------------------------------------------


def get_order(order_id: str) -> Optional[dict]:
    """Look up a single order by order_id. Returns dict or None."""
    df = _orders()
    rows = df[df["order_id"] == order_id]
    if rows.empty:
        return None
    row = rows.iloc[0].to_dict()
    # Normalise timestamps
    ts_cols = [
        "order_purchase_timestamp",
        "order_approved_at",
        "order_delivered_carrier_date",
        "order_delivered_customer_date",
        "order_estimated_delivery_date",
    ]
    for col in ts_cols:
        row[col] = _parse_ts(row.get(col))
    for k, v in row.items():
        if k not in ts_cols:
            row[k] = _none_if_empty(v)
    return row


def get_order_items(order_id: str) -> list[dict]:
    """Return all order_items rows for an order (list may be empty)."""
    df = _order_items()
    rows = df[df["order_id"] == order_id].copy()
    if rows.empty:
        return []
    result = []
    for _, row in rows.iterrows():
        d = row.to_dict()
        for k, v in d.items():
            d[k] = _none_if_empty(v) if k != "shipping_limit_date" else _parse_ts(v)
        result.append(d)
    return result


def get_payments(order_id: str) -> list[dict]:
    """Return all payment rows for an order."""
    df = _order_payments()
    rows = df[df["order_id"] == order_id].copy()
    if rows.empty:
        return []
    result = []
    for _, row in rows.iterrows():
        d = row.to_dict()
        d["payment_value"] = _none_if_empty(d.get("payment_value"))
        d["payment_sequential"] = _none_if_empty(d.get("payment_sequential"))
        d["payment_type"] = _none_if_empty(d.get("payment_type"))
        d["payment_installments"] = _none_if_empty(d.get("payment_installments"))
        result.append(d)
    return result


def get_customer(customer_id: str) -> Optional[dict]:
    """Look up a customer by customer_id."""
    df = _customers()
    rows = df[df["customer_id"] == customer_id]
    if rows.empty:
        return None
    row = rows.iloc[0].to_dict()
    return {k: _none_if_empty(v) for k, v in row.items()}


def get_related_orders(customer_unique_id: str, limit: int = 5) -> list[str]:
    """
    Find all orders for the same customer_unique_id via the customers table,
    excluding the current lookup (caller should filter the main order_id out
    if needed). Returns up to `limit` order_ids.
    """
    customers_df = _customers()
    cust_rows = customers_df[customers_df["customer_unique_id"] == customer_unique_id]
    if cust_rows.empty:
        return []
    cust_ids = cust_rows["customer_id"].tolist()
    orders_df = _orders()
    related = orders_df[orders_df["customer_id"].isin(cust_ids)]["order_id"].tolist()
    # Deduplicate, preserve order
    seen: set[str] = set()
    result: list[str] = []
    for oid in related:
        if oid not in seen:
            seen.add(oid)
            result.append(oid)
        if len(result) >= limit:
            break
    return result


def get_product(product_id: str) -> Optional[dict]:
    """Look up a product by product_id."""
    df = _products()
    rows = df[df["product_id"] == product_id]
    if rows.empty:
        return None
    row = rows.iloc[0].to_dict()
    return {k: _none_if_empty(v) for k, v in row.items()}


def get_seller(seller_id: str) -> Optional[dict]:
    """Look up a seller by seller_id."""
    df = _sellers()
    rows = df[df["seller_id"] == seller_id]
    if rows.empty:
        return None
    row = rows.iloc[0].to_dict()
    return {k: _none_if_empty(v) for k, v in row.items()}


def get_category_translation(category_name: Optional[str]) -> Optional[str]:
    """Translate Portuguese category name to English. Returns None if not found."""
    if not category_name:
        return None
    df = _category_translation()
    # Column names: product_category_name, product_category_name_english
    port_col = next(
        (c for c in df.columns if "portuguese" in c.lower()),
        next((c for c in df.columns if c == "product_category_name"), None)
    )
    eng_col = next(
        (c for c in df.columns if "english" in c.lower()),
        None
    )
    if not port_col or not eng_col:
        return category_name
    rows = df[df[port_col] == category_name]
    if rows.empty:
        return category_name  # return original if no translation
    return _none_if_empty(rows.iloc[0][eng_col]) or category_name


# ---------------------------------------------------------------------------
# Computed / derived functions
# ---------------------------------------------------------------------------


def compute_delivery_variance(
    delivered: Optional[str], estimated: Optional[str]
) -> Optional[float]:
    """
    Compute delivery_variance_hours = delivered - estimated (in hours, 2 decimals).
    Positive means late; negative means early.
    Returns None if either timestamp is None.
    """
    if not delivered or not estimated:
        return None
    try:
        fmt = "%Y-%m-%d %H:%M:%S"
        dt_delivered = datetime.strptime(delivered, fmt)
        dt_estimated = datetime.strptime(estimated, fmt)
        delta = dt_delivered - dt_estimated
        hours = delta.total_seconds() / 3600.0
        return round(hours, 2)
    except Exception:
        return None


def compute_handoff_variances(
    items: list[dict], carrier_handoff_date: Optional[str]
) -> list[dict]:
    """
    For each seller in items, compute handoff_variance_hours =
    order_delivered_carrier_date - shipping_limit_date (per-seller earliest limit).

    Returns list of {seller_id, shipping_limit_at, handoff_variance_hours, late_handoff}.
    Late means carrier_handoff > shipping_limit (variance > 0).

    Edge cases:
    - Multiple items for same seller: use earliest shipping_limit_date
    - None carrier_handoff_date: returns None variance, late_handoff=False
    """
    if not items:
        return []

    # Group by seller_id → earliest shipping_limit_date
    seller_limits: dict[str, Optional[str]] = {}
    for item in items:
        sid = item.get("seller_id")
        if not sid:
            continue
        limit = item.get("shipping_limit_date")
        if sid not in seller_limits:
            seller_limits[sid] = limit
        else:
            # Keep the earliest limit
            existing = seller_limits[sid]
            if existing is None:
                seller_limits[sid] = limit
            elif limit is not None:
                fmt = "%Y-%m-%d %H:%M:%S"
                try:
                    dt_existing = datetime.strptime(existing, fmt)
                    dt_limit = datetime.strptime(limit, fmt)
                    if dt_limit < dt_existing:
                        seller_limits[sid] = limit
                except Exception:
                    pass

    result = []
    fmt = "%Y-%m-%d %H:%M:%S"
    for sid, shipping_limit_at in seller_limits.items():
        if carrier_handoff_date and shipping_limit_at:
            try:
                dt_carrier = datetime.strptime(carrier_handoff_date, fmt)
                dt_limit = datetime.strptime(shipping_limit_at, fmt)
                delta = dt_carrier - dt_limit
                variance = round(delta.total_seconds() / 3600.0, 2)
                late_handoff = variance > 0
            except Exception:
                variance = None
                late_handoff = False
        else:
            variance = None
            late_handoff = False

        result.append(
            {
                "seller_id": sid,
                "shipping_limit_at": shipping_limit_at,
                "handoff_variance_hours": variance,
                "late_handoff": late_handoff,
            }
        )

    return result


def reconcile_payment(
    payments: list[dict], items: list[dict]
) -> dict:
    """
    Aggregate payment rows and reconcile against item + freight totals.

    Returns:
    {
      payment_total_brl, item_total_brl, freight_total_brl,
      expected_total_brl, difference_brl, reconciled, payment_types
    }

    If items is empty, monetary fields are None and reconciled is None.
    """
    # Compute item and freight totals
    if not items:
        item_total = None
        freight_total = None
        expected_total = None
    else:
        try:
            item_total = round(
                sum(float(i.get("price") or 0) for i in items), 2
            )
            freight_total = round(
                sum(float(i.get("freight_value") or 0) for i in items), 2
            )
            expected_total = round(item_total + freight_total, 2)
        except Exception:
            item_total = None
            freight_total = None
            expected_total = None

    # Compute payment total
    if not payments:
        payment_total = None
    else:
        try:
            payment_total = round(
                sum(float(p.get("payment_value") or 0) for p in payments), 2
            )
        except Exception:
            payment_total = None

    # Difference and reconciled
    if payment_total is not None and expected_total is not None:
        difference = round(payment_total - expected_total, 2)
        reconciled = abs(difference) <= 0.10
    else:
        difference = None
        reconciled = None

    # Unique payment types (stable order)
    payment_types: list[str] = []
    seen_types: set[str] = set()
    for p in payments:
        pt = _none_if_empty(p.get("payment_type"))
        if pt and pt not in seen_types:
            seen_types.add(pt)
            payment_types.append(pt)

    return {
        "item_total_brl": item_total,
        "freight_total_brl": freight_total,
        "expected_total_brl": expected_total,
        "payment_total_brl": payment_total,
        "difference_brl": difference,
        "reconciled": reconciled,
        "payment_types": payment_types,
    }


def generate_evidence_ids(
    order_id: str,
    items: list[dict],
    payments: list[dict],
    responsible_seller_ids: list[str],
    root_cause_code: Optional[str],
    max_items: int = 5,
    max_payments: int = 5,
    max_sellers: int = 3,
    max_total: int = 20,
) -> list[str]:
    """
    Build evidence ID list from actual CSV-traceable data.
    Format:
      order:<order_id>
      item:<order_id>:<order_item_id>
      payment:<order_id>:<payment_sequential>
      seller:<seller_id>
      policy:<root_cause_code>
    """
    evidence: list[str] = []

    # 1. Order evidence
    evidence.append(f"order:{order_id}")

    # 2. Item evidence (up to max_items)
    for item in items[:max_items]:
        iid = item.get("order_item_id")
        if iid:
            evidence.append(f"item:{order_id}:{iid}")

    # 3. Payment evidence (up to max_payments)
    for pay in payments[:max_payments]:
        seq = pay.get("payment_sequential")
        if seq:
            evidence.append(f"payment:{order_id}:{seq}")

    # 4. Seller evidence (only responsible parties, up to max_sellers)
    for sid in responsible_seller_ids[:max_sellers]:
        evidence.append(f"seller:{sid}")

    # 5. Policy evidence
    if root_cause_code:
        evidence.append(f"policy:{root_cause_code}")

    # Enforce total limit
    return evidence[:max_total]


def validate_output(output: dict, order_id: str) -> list[str]:
    """
    Validate final output against README §6 schema.
    Returns list of error strings (empty = valid).
    """
    errors: list[str] = []

    required_top = [
        "case_id", "case_assessment", "affected_entities",
        "customer_context", "product_context", "delivery_analysis",
        "payment_reconciliation", "root_cause_analysis",
        "evidence_ids", "financial_resolution", "resolution_actions",
    ]
    for key in required_top:
        if key not in output:
            errors.append(f"Missing top-level key: {key}")

    # Array limits
    ae = output.get("affected_entities", {})
    if len(ae.get("order_ids", [])) > 5:
        errors.append("Too many order_ids (max 5)")
    if len(ae.get("item_ids", [])) > 5:
        errors.append("Too many item_ids (max 5)")
    if len(ae.get("seller_ids", [])) > 3:
        errors.append("Too many seller_ids (max 3)")
    if len(ae.get("payment_ids", [])) > 5:
        errors.append("Too many payment_ids (max 5)")

    cc = output.get("customer_context", {})
    if len(cc.get("related_order_ids", [])) > 5:
        errors.append("Too many related_order_ids (max 5)")

    pc = output.get("product_context", {})
    if len(pc.get("product_ids", [])) > 5:
        errors.append("Too many product_ids (max 5)")
    if len(pc.get("category_names", [])) > 5:
        errors.append("Too many category_names (max 5)")

    rca = output.get("root_cause_analysis", {})
    if len(rca.get("ranked_causes", [])) > 3:
        errors.append("Too many ranked_causes (max 3)")
    if len(rca.get("responsible_parties", [])) > 3:
        errors.append("Too many responsible_parties (max 3)")

    if len(output.get("evidence_ids", [])) > 20:
        errors.append("Too many evidence_ids (max 20)")

    if len(output.get("resolution_actions", [])) > 5:
        errors.append("Too many resolution_actions (max 5)")

    # Confidence range
    ca = output.get("case_assessment", {})
    conf = ca.get("confidence")
    if conf is not None:
        try:
            c = float(conf)
            if not (0.0 <= c <= 1.0):
                errors.append(f"confidence out of range [0,1]: {c}")
        except (ValueError, TypeError):
            errors.append(f"confidence is not numeric: {conf}")

    # case_status valid values
    cs = ca.get("case_status")
    if cs not in ("action_required", "no_action"):
        errors.append(f"Invalid case_status: {cs}")

    # Evidence format check
    valid_prefixes = {"order:", "item:", "payment:", "seller:", "policy:"}
    for eid in output.get("evidence_ids", []):
        if not any(eid.startswith(p) for p in valid_prefixes):
            errors.append(f"Invalid evidence_id format: {eid}")

    return errors
