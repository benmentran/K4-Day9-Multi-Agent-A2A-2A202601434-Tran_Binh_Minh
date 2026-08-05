"""
Orchestrator: coordinates the 7-agent pipeline for each case.

Design principle: verify all claims against CSV data first, then apply policy.
"Không tin message khiếu nại ngay, phải join order/payment/item, check verify trước"

Agent pipeline (all deterministic except Verifier):
  Coordinator  — case intake + data pre-fetch from CSV
  Customer     — deterministic: customer_unique_id + related orders
  OrderProd    — deterministic: order/item/product/category extraction
  Payment      — deterministic: reconciliation
  Delivery     — deterministic: variance computation
  Policy       — deterministic: EC_POLICY_V2 rule engine
  Verifier     — gpt-4o-mini: schema correction (only LLM call)
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from datetime import datetime
from typing import Optional

from src import tools
from src import agents


def run_case(case_file: Path, output_dir: Path, trace_file: Path) -> dict:
    """
    Process a single case through the full 7-agent pipeline.
    Returns the final output dict and appends one line to trace.jsonl.
    """
    with open(case_file, "r", encoding="utf-8") as f:
        case_input = json.load(f)

    case_id = case_input["case_id"]
    # The claimed_order_id from the customer is NOT trusted yet —
    # it is used only as a lookup key; all facts come from CSV joins.
    claimed_order_id: str = case_input["customer_request"]["claimed_order_id"]

    trace_entry: dict = {
        "case_id": case_id,
        "order_id": claimed_order_id,
        "timestamp": datetime.now().isoformat(),
        "agents": [],
        "status": "pending",
    }

    try:
        # ====================================================================
        # COORDINATOR — Data gathering (verify claim against CSV)
        # ====================================================================
        trace_entry["agents"].append({"agent": "coordinator", "status": "gathering"})

        order_data = tools.get_order(claimed_order_id)
        if not order_data:
            raise ValueError(f"Order '{claimed_order_id}' not found in dataset")

        # All subsequent facts come from CSV, not from the customer message
        order_id: str = order_data["order_id"]  # canonical ID from CSV
        items_data = tools.get_order_items(order_id)
        payments_data = tools.get_payments(order_id)

        customer_id = order_data.get("customer_id")
        customer_data = tools.get_customer(customer_id) if customer_id else None

        # Related orders via customer_unique_id (CSV join, not customer-supplied)
        related_order_ids: list[str] = []
        if customer_data:
            cuid = customer_data.get("customer_unique_id")
            if cuid:
                all_related = tools.get_related_orders(cuid, limit=6)
                related_order_ids = [oid for oid in all_related if oid != order_id][:5]

        # Enrich items with English category_name from product catalogue
        enriched_items: list[dict] = []
        for item in items_data:
            item_copy = item.copy()
            product_id = item.get("product_id")
            if product_id:
                product = tools.get_product(product_id)
                if product:
                    cat_pt = product.get("product_category_name")
                    cat_en = tools.get_category_translation(cat_pt)
                    item_copy["category_name"] = cat_en
            enriched_items.append(item_copy)

        # Deterministic computations (no LLM)
        payment_reconciliation = tools.reconcile_payment(payments_data, items_data)
        delivery_variance = tools.compute_delivery_variance(
            order_data.get("order_delivered_customer_date"),
            order_data.get("order_estimated_delivery_date"),
        )
        handoff_variances = tools.compute_handoff_variances(
            items_data, order_data.get("order_delivered_carrier_date")
        )

        trace_entry["agents"][-1]["status"] = "success"

        # ====================================================================
        # CUSTOMER AGENT — deterministic
        # ====================================================================
        customer_tools_data = {
            "order": order_data,
            "customer": customer_data,
            "related_orders": related_order_ids,
        }
        customer_output = agents.customer_agent(order_id, customer_tools_data)
        trace_entry["agents"].append({
            "agent": "customer", "model": agents.MODEL_CUSTOMER, "status": "success"
        })

        # ====================================================================
        # ORDER & PRODUCT AGENT — deterministic
        # ====================================================================
        order_product_tools_data = {
            "order": order_data,
            "items": enriched_items,
        }
        order_product_output = agents.order_product_agent(order_id, order_product_tools_data)
        # Inject authoritative counts from raw items_data (bypass any LLM drift)
        order_product_output["_items_count"] = len(items_data)
        order_product_output["_distinct_sellers"] = list(dict.fromkeys(
            i.get("seller_id") for i in items_data if i.get("seller_id")
        ))
        order_product_output["_category_names"] = list(dict.fromkeys(
            i.get("category_name") for i in enriched_items if i.get("category_name")
        ))
        trace_entry["agents"].append({
            "agent": "order_product", "model": agents.MODEL_ORDER_PRODUCT, "status": "success"
        })

        # ====================================================================
        # PAYMENT AGENT — deterministic
        # ====================================================================
        payment_output = agents.payment_agent(
            order_id,
            payment_reconciliation,
            payment_reconciliation.get("payment_types", []),
        )
        trace_entry["agents"].append({
            "agent": "payment", "model": "deterministic", "status": "success"
        })

        # ====================================================================
        # DELIVERY AGENT — deterministic
        # ====================================================================
        delivery_output = agents.delivery_agent(
            order_data, delivery_variance, handoff_variances
        )
        trace_entry["agents"].append({
            "agent": "delivery", "model": "deterministic", "status": "success"
        })

        # ====================================================================
        # POLICY AGENT — deterministic EC_POLICY_V2 rule engine
        # Decisions based on verified CSV data, NOT on customer's claimed message
        # ====================================================================
        policy_output = agents.policy_agent(
            order_id,
            customer_output,
            order_product_output,
            payment_output,
            delivery_output,
            payment_rows_count=len(payments_data),
        )
        trace_entry["agents"].append({
            "agent": "policy", "model": agents.MODEL_POLICY, "status": "success"
        })

        # ====================================================================
        # ASSEMBLE final output
        # ====================================================================
        final_output = _assemble_output(
            case_id,
            order_id,
            customer_output,
            order_product_output,
            payment_output,
            delivery_output,
            policy_output,
            items_data,
            payments_data,
            enriched_items,
        )

        # ====================================================================
        # VERIFIER AGENT — gpt-4o-mini (only LLM call in the pipeline)
        # ====================================================================
        validation_errors = tools.validate_output(final_output, order_id)
        verifier_output = agents.verifier_agent(final_output, order_id, validation_errors)
        trace_entry["agents"].append({
            "agent": "verifier", "model": agents.MODEL_VERIFIER, "status": "success"
        })

        if not verifier_output.get("validated", True):
            corrected = verifier_output.get("corrected_output")
            if corrected and isinstance(corrected, dict):
                final_output = corrected

        # ====================================================================
        # WRITE output file
        # ====================================================================
        output_file = output_dir / f"{case_id}.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(final_output, f, ensure_ascii=False, indent=2)

        trace_entry["status"] = "success"
        trace_entry["verifier_errors"] = verifier_output.get("errors", [])

    except Exception as e:
        trace_entry["status"] = "error"
        trace_entry["error"] = str(e)
        final_output = {"case_id": case_id, "error": str(e)}
        output_file = output_dir / f"{case_id}.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(final_output, f, ensure_ascii=False, indent=2)

    with open(trace_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(trace_entry, ensure_ascii=False) + "\n")

    return final_output


def _assemble_output(
    case_id: str,
    order_id: str,
    customer_output: dict,
    order_product_output: dict,
    payment_output: dict,
    delivery_output: dict,
    policy_output: dict,
    items_data: list[dict],
    payments_data: list[dict],
    enriched_items: list[dict],
) -> dict:
    """
    Assemble the final README §6 output from all agent outputs.
    All array limits enforced here.
    """
    # --- Affected entities ---
    item_ids = [
        f"{order_id}:{item.get('order_item_id')}"
        for item in items_data
        if item.get("order_item_id")
    ][:5]

    payment_ids = [
        f"{order_id}:{p.get('payment_sequential')}"
        for p in payments_data
        if p.get("payment_sequential")
    ][:5]

    responsible_parties = policy_output.get("responsible_parties", [])
    responsible_seller_ids = [
        rp["party_id"] for rp in responsible_parties
        if rp.get("party_type") == "seller" and rp.get("party_id")
    ]
    all_seller_ids = list(dict.fromkeys(
        item.get("seller_id") for item in items_data if item.get("seller_id")
    ))
    seller_ids_for_output = (
        responsible_seller_ids[:3] if responsible_seller_ids else all_seller_ids[:3]
    )

    # --- Evidence IDs (fully deterministic) ---
    root_cause_code = policy_output.get("root_cause_code")
    evidence_ids = tools.generate_evidence_ids(
        order_id, items_data, payments_data, responsible_seller_ids, root_cause_code
    )

    # --- Customer context ---
    related_order_ids = (customer_output.get("related_order_ids") or [])[:5]

    # --- Product context (from CSV-enriched items, not LLM) ---
    product_ids = list(dict.fromkeys(
        item.get("product_id") for item in enriched_items if item.get("product_id")
    ))[:5]
    category_names = list(dict.fromkeys(
        item.get("category_name") for item in enriched_items if item.get("category_name")
    ))[:5]
    # Fallback to order_product_output if enriched had none
    if not category_names:
        category_names = list(dict.fromkeys(
            c for c in order_product_output.get("category_names", []) if c
        ))[:5]

    # --- Root cause ---
    ranked_causes = []
    if root_cause_code:
        ranked_causes.append({"cause_code": root_cause_code, "rank": 1})

    # --- Financial ---
    refund_brl = round(float(policy_output.get("refund_brl") or 0), 2)
    case_status = "action_required" if refund_brl > 0 else "no_action"
    confidence = round(
        max(0.0, min(1.0, float(policy_output.get("confidence") or 0.9))), 4
    )

    return {
        "case_id": case_id,
        "case_assessment": {
            "primary_issue": policy_output.get("primary_issue"),
            "secondary_issues": policy_output.get("secondary_issues", []),
            "case_status": case_status,
            "confidence": confidence,
        },
        "affected_entities": {
            "order_ids": [order_id],
            "item_ids": item_ids,
            "seller_ids": seller_ids_for_output,
            "payment_ids": payment_ids,
        },
        "customer_context": {
            "customer_unique_id": customer_output.get("customer_unique_id"),
            "related_order_ids": related_order_ids,
        },
        "product_context": {
            "product_ids": product_ids,
            "category_names": category_names,
        },
        "delivery_analysis": {
            "delivered_at": delivery_output.get("delivered_at"),
            "estimated_delivery_at": delivery_output.get("estimated_delivery_at"),
            "carrier_handoff_at": delivery_output.get("carrier_handoff_at"),
            "delivery_variance_hours": delivery_output.get("delivery_variance_hours"),
            "seller_handoff_analysis": delivery_output.get("seller_handoff_analysis", []),
            "late_handoff_seller_ids": delivery_output.get("late_handoff_seller_ids", []),
        },
        "payment_reconciliation": {
            "currency": "BRL",
            "item_total_brl": payment_output.get("item_total_brl"),
            "freight_total_brl": payment_output.get("freight_total_brl"),
            "expected_total_brl": payment_output.get("expected_total_brl"),
            "payment_total_brl": payment_output.get("payment_total_brl"),
            "difference_brl": payment_output.get("difference_brl"),
            "reconciled": payment_output.get("reconciled"),
            "payment_types": payment_output.get("payment_types", []),
        },
        "root_cause_analysis": {
            "ranked_causes": ranked_causes[:3],
            "responsible_parties": responsible_parties[:3],
        },
        "evidence_ids": evidence_ids[:20],
        "financial_resolution": {
            "currency": "BRL",
            "recommended_refund_brl": refund_brl,
        },
        "resolution_actions": policy_output.get("resolution_actions", [])[:5],
    }
