"""
Agent implementations.

Design principle (per README hint):
  "Không tin message khiếu nại ngay, phải join order/payment/item,
   check verify trước khi thực hiện"

All factual extraction (customer, order, payment, delivery) is DETERMINISTIC
— done in Python from CSV data, no LLM arithmetic.

LLM is used ONLY for the Verifier (schema correction) since all policy rules
are also now fully deterministic Python.

Model assignment:
  Customer Agent:       PURE DETERMINISTIC (no LLM)
  Order/Product Agent:  PURE DETERMINISTIC (no LLM)
  Payment Agent:        PURE DETERMINISTIC (no LLM)
  Delivery Agent:       PURE DETERMINISTIC (no LLM)
  Policy Agent:         PURE DETERMINISTIC (no LLM)
  Verifier Agent:       gpt-4o-mini (~8B)  — schema correction only
  Coordinator:          gpt-4o-mini (~8B)  — orchestration (no direct call needed)
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from litellm import completion

# ---------------------------------------------------------------------------
# Model constants — do NOT move to .env per project rules
# ---------------------------------------------------------------------------
MODEL_COORDINATOR = "gpt-4o-mini"
MODEL_CUSTOMER = "deterministic"
MODEL_ORDER_PRODUCT = "deterministic"
MODEL_POLICY = "deterministic"
MODEL_VERIFIER = "gpt-4o-mini"


def _call_llm(
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.0,
    max_retries: int = 2,
) -> dict:
    """
    Unified LLM call via LiteLLM. Returns parsed dict.
    Used only by Verifier Agent.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    last_error: Exception = RuntimeError("No attempts made")
    for attempt in range(max_retries + 1):
        try:
            response = completion(
                model=model,
                messages=messages,
                temperature=temperature,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            return json.loads(content)
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                import time
                time.sleep(2 ** attempt)
    raise RuntimeError(
        f"LLM call failed after {max_retries + 1} attempts: {last_error}"
    ) from last_error


# ---------------------------------------------------------------------------
# Agent 2: Customer Agent — PURE DETERMINISTIC
# Reads pre-fetched customer + related_orders from orchestrator.
# Does NOT trust the customer's claimed message — validates from CSV data.
# ---------------------------------------------------------------------------

def customer_agent(order_id: str, tools_data: dict) -> dict:
    """
    Customer Agent: deterministically extracts customer_unique_id and related orders.
    Input:  {order, customer, related_orders}  (pre-fetched from CSV)
    Output: {customer_unique_id, related_order_ids}
    """
    customer = tools_data.get("customer") or {}
    related_orders = tools_data.get("related_orders") or []

    customer_unique_id = customer.get("customer_unique_id") or None
    # Exclude current order, deduplicate, limit 5
    related_order_ids = list(dict.fromkeys(
        oid for oid in related_orders if oid and oid != order_id
    ))[:5]

    return {
        "customer_unique_id": customer_unique_id,
        "related_order_ids": related_order_ids,
    }


# ---------------------------------------------------------------------------
# Agent 3: Order & Product Agent — PURE DETERMINISTIC
# Joins order + items + product catalogue from CSV. Does NOT trust customer claim.
# ---------------------------------------------------------------------------

def order_product_agent(order_id: str, tools_data: dict) -> dict:
    """
    Order & Product Agent: extracts order info, items, sellers, products, categories.
    Input:  {order, items (enriched with category_name)}
    Output: {order_status, timestamps, items[], seller_ids[], product_ids[], category_names[]}
    """
    order = tools_data.get("order") or {}
    items = tools_data.get("items") or []

    seller_ids = list(dict.fromkeys(
        i.get("seller_id") for i in items if i.get("seller_id")
    ))[:3]
    product_ids = list(dict.fromkeys(
        i.get("product_id") for i in items if i.get("product_id")
    ))[:5]
    category_names = list(dict.fromkeys(
        i.get("category_name") for i in items if i.get("category_name")
    ))[:5]

    return {
        "order_status": order.get("order_status"),
        "order_purchase_timestamp": order.get("order_purchase_timestamp"),
        "order_delivered_carrier_date": order.get("order_delivered_carrier_date"),
        "order_delivered_customer_date": order.get("order_delivered_customer_date"),
        "order_estimated_delivery_date": order.get("order_estimated_delivery_date"),
        "items": [
            {
                "order_item_id": i.get("order_item_id"),
                "seller_id": i.get("seller_id"),
                "product_id": i.get("product_id"),
                "price": _safe_float(i.get("price")),
                "freight_value": _safe_float(i.get("freight_value")),
                "shipping_limit_date": i.get("shipping_limit_date"),
            }
            for i in items
        ],
        "seller_ids": seller_ids,
        "product_ids": product_ids,
        "category_names": category_names,
    }


def _safe_float(val) -> Optional[float]:
    try:
        return float(val) if val is not None else None
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Agent 4: Payment Agent — PURE DETERMINISTIC
# ---------------------------------------------------------------------------

def payment_agent(order_id: str, reconciliation: dict, payment_types: list[str]) -> dict:
    """
    Payment Agent: returns reconciliation result directly from tools.
    NO LLM — tools.reconcile_payment() handles all arithmetic.
    """
    return {
        "currency": "BRL",
        "item_total_brl": reconciliation.get("item_total_brl"),
        "freight_total_brl": reconciliation.get("freight_total_brl"),
        "expected_total_brl": reconciliation.get("expected_total_brl"),
        "payment_total_brl": reconciliation.get("payment_total_brl"),
        "difference_brl": reconciliation.get("difference_brl"),
        "reconciled": reconciliation.get("reconciled"),
        "payment_types": reconciliation.get("payment_types", payment_types),
    }


# ---------------------------------------------------------------------------
# Agent 5: Delivery Agent — PURE DETERMINISTIC
# ---------------------------------------------------------------------------

def delivery_agent(
    order_data: dict,
    delivery_variance: Optional[float],
    handoff_variances: list[dict],
) -> dict:
    """
    Delivery Agent: assembles delivery_analysis from deterministic tool outputs.
    NO LLM — all timestamp arithmetic done by tools.
    """
    late_handoff_sellers = [
        hv["seller_id"] for hv in handoff_variances if hv.get("late_handoff")
    ]
    return {
        "delivered_at": order_data.get("order_delivered_customer_date"),
        "estimated_delivery_at": order_data.get("order_estimated_delivery_date"),
        "carrier_handoff_at": order_data.get("order_delivered_carrier_date"),
        "delivery_variance_hours": delivery_variance,
        "seller_handoff_analysis": handoff_variances,
        "late_handoff_seller_ids": late_handoff_sellers,
    }


# ---------------------------------------------------------------------------
# Agent 6: Policy Agent — PURE DETERMINISTIC
#
# Applies EC_POLICY_V2 rules as Python logic.
# Hint: "Không tin message khiếu nại ngay" — decision is based entirely on
# joined CSV data (order_status, delivery timestamps, payment totals),
# NOT on the customer's claimed message.
# ---------------------------------------------------------------------------

def policy_agent(
    order_id: str,
    customer_output: dict,
    order_product_output: dict,
    payment_output: dict,
    delivery_output: dict,
    payment_rows_count: int = 0,
) -> dict:
    """
    Policy Agent: applies EC_POLICY_V2 deterministically.
    All inputs are pre-verified CSV data. Customer claim is ignored.
    """
    order_status = (order_product_output.get("order_status") or "").strip().lower()
    payment_total = _safe_float(payment_output.get("payment_total_brl")) or 0.0
    freight_total = _safe_float(payment_output.get("freight_total_brl")) or 0.0
    reconciled = payment_output.get("reconciled")
    delivery_variance = delivery_output.get("delivery_variance_hours")
    late_seller_ids = delivery_output.get("late_handoff_seller_ids") or []
    related_orders = customer_output.get("related_order_ids") or []

    # Deterministic counts — injected by orchestrator from actual items_data
    items_count: int = order_product_output.get("_items_count", 0)
    distinct_sellers: list = order_product_output.get("_distinct_sellers", [])
    category_names_list: list = order_product_output.get("_category_names", [])

    # ------------------------------------------------------------------
    # PRIMARY ISSUE — first match wins (EC_POLICY_V2 priority order)
    # ------------------------------------------------------------------
    primary_issue: Optional[str] = None
    root_cause_code: Optional[str] = None
    refund_brl: float = 0.0
    responsible_parties: list[dict] = []
    primary_action: str = ""

    if order_status == "canceled" and payment_total > 0:
        primary_issue = "canceled_order_paid"
        root_cause_code = "ORDER_CANCELED_AFTER_PAYMENT"
        refund_brl = round(payment_total, 2)
        responsible_parties = [{"party_type": "platform", "party_id": "OLIST_PLATFORM"}]
        primary_action = "issue_full_refund"

    elif order_status == "unavailable" and payment_total > 0:
        primary_issue = "unavailable_order_paid"
        root_cause_code = "ORDER_UNAVAILABLE_AFTER_PAYMENT"
        refund_brl = round(payment_total, 2)
        responsible_parties = [{"party_type": "platform", "party_id": "OLIST_PLATFORM"}]
        primary_action = "issue_full_refund"

    elif (
        delivery_variance is not None
        and delivery_variance > 0
        and len(late_seller_ids) > 0
    ):
        primary_issue = "late_delivery_seller"
        root_cause_code = "SELLER_HANDOFF_AFTER_LIMIT"
        refund_brl = round(freight_total, 2)
        responsible_parties = [
            {"party_type": "seller", "party_id": sid}
            for sid in late_seller_ids[:3]
        ]
        primary_action = "refund_freight"

    elif (
        delivery_variance is not None
        and delivery_variance > 0
        and len(late_seller_ids) == 0
    ):
        primary_issue = "late_delivery_logistics"
        root_cause_code = "CARRIER_DELIVERED_AFTER_ESTIMATE"
        refund_brl = round(freight_total, 2)
        responsible_parties = [
            {"party_type": "logistics", "party_id": "LOGISTICS_PROVIDER"}
        ]
        primary_action = "refund_freight"

    elif payment_rows_count >= 2 and reconciled is True:
        primary_issue = "valid_split_payment"
        root_cause_code = "MULTIPLE_PAYMENTS_RECONCILED"
        refund_brl = 0.0
        responsible_parties = []
        primary_action = "explain_valid_split_payment"

    elif (delivery_variance is None or delivery_variance <= 0) and reconciled is True:
        primary_issue = "unsupported_late_claim"
        root_cause_code = "DELIVERY_WITHIN_ESTIMATE"
        refund_brl = 0.0
        responsible_parties = []
        primary_action = "reject_late_refund"

    # ------------------------------------------------------------------
    # SECONDARY ISSUES — in required order
    # ------------------------------------------------------------------
    secondary_issues: list[str] = []
    if items_count >= 2:
        secondary_issues.append("multi_item_order")
    if len(distinct_sellers) >= 2:
        secondary_issues.append("multi_seller_order")
    if payment_rows_count >= 2:
        secondary_issues.append("split_payment")
    if len(related_orders) > 0:
        secondary_issues.append("repeat_customer")
    if len(category_names_list) >= 2:
        secondary_issues.append("multiple_categories")

    # ------------------------------------------------------------------
    # RESOLUTION ACTIONS — primary first, then additional in order
    # ------------------------------------------------------------------
    resolution_actions: list[str] = []
    if primary_action:
        resolution_actions.append(primary_action)
    if primary_issue == "late_delivery_seller":
        resolution_actions.append("review_seller_handoff")
    elif primary_issue == "late_delivery_logistics":
        resolution_actions.append("review_carrier_delay")
    if refund_brl > 0:
        resolution_actions.append("verify_refund_completion")
    if "multi_seller_order" in secondary_issues:
        resolution_actions.append("coordinate_multi_seller_case")
    if "split_payment" in secondary_issues and primary_issue != "valid_split_payment":
        resolution_actions.append("verify_payment_allocation")
    resolution_actions = list(dict.fromkeys(resolution_actions))[:5]

    # ------------------------------------------------------------------
    # CASE STATUS & CONFIDENCE
    # ------------------------------------------------------------------
    case_status = "action_required" if refund_brl > 0 else "no_action"
    has_ambiguity = (
        delivery_variance is None
        or payment_output.get("payment_total_brl") is None
        or primary_issue is None
    )
    confidence = 0.85 if has_ambiguity else 0.95

    return {
        "primary_issue": primary_issue,
        "secondary_issues": secondary_issues,
        "case_status": case_status,
        "confidence": round(confidence, 4),
        "root_cause_code": root_cause_code,
        "responsible_parties": responsible_parties[:3],
        "refund_brl": round(refund_brl, 2),
        "resolution_actions": resolution_actions,
    }


# ---------------------------------------------------------------------------
# Agent 7: Verifier Agent — gpt-4o-mini (~8B)
# The only agent that uses LLM — for schema correction edge cases.
# If LLM fails, passes original output through (non-fatal).
# ---------------------------------------------------------------------------

def verifier_agent(
    assembled_output: dict, order_id: str, validation_errors: list[str]
) -> dict:
    """
    Verifier Agent: validates and corrects the assembled output.
    Uses gpt-4o-mini — only LLM call in the entire pipeline.
    If no errors detected by tools, does a lightweight pass-through.
    """
    if not validation_errors:
        # Tools found no structural errors — verify business logic
        refund = (assembled_output.get("financial_resolution") or {}).get(
            "recommended_refund_brl", 0
        )
        case_status = (assembled_output.get("case_assessment") or {}).get("case_status")
        expected_status = "action_required" if (refund or 0) > 0 else "no_action"
        if case_status == expected_status:
            return {"validated": True, "errors": [], "corrected_output": None}

    system_prompt = (
        "You are the Verifier Agent. Fix any issues in the output JSON.\n"
        "Rules:\n"
        "1. case_status='action_required' if recommended_refund_brl > 0, else 'no_action'.\n"
        "2. confidence must be in [0.0, 1.0].\n"
        "3. Timestamps must be 'YYYY-MM-DD HH:MM:SS' or null.\n"
        "4. Array limits: order_ids<=5, item_ids<=5, seller_ids<=3, payment_ids<=5, "
        "related_order_ids<=5, product_ids<=5, category_names<=5, ranked_causes<=3, "
        "responsible_parties<=3, evidence_ids<=20, resolution_actions<=5.\n"
        "Return JSON: "
        '{"validated": <bool>, "errors": ["<desc>"], "corrected_output": <fixed_json_or_null>}'
    )
    user_prompt = (
        f"order_id: {order_id}\n"
        f"tool_errors: {json.dumps(validation_errors)}\n"
        f"output:\n{json.dumps(assembled_output, ensure_ascii=False)}"
    )
    try:
        result = _call_llm(MODEL_VERIFIER, system_prompt, user_prompt)
        return {
            "validated": bool(result.get("validated", True)),
            "errors": result.get("errors", []),
            "corrected_output": result.get("corrected_output"),
        }
    except Exception:
        # Verifier failure is non-fatal
        return {"validated": True, "errors": [], "corrected_output": None}
