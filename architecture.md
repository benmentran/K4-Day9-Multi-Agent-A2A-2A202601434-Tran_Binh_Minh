# Multi-Agent Architecture: E-commerce Dispute Resolution

## Overview

A 7-agent sequential handoff pipeline processes 50 Olist e-commerce dispute cases. Each agent
specializes in one domain, performs LLM reasoning over deterministic Python-computed data, and
hands off structured JSON to the next agent. The final output matches README §6 schema.

## Constraints
- Each agent model ≤ 10B parameters
- API keys in .env (never committed)
- Model names in code + metadata.json

## Agent Roster

### 1. Coordinator Agent — gpt-4o-mini (~8B)
- **Role**: Case intake, pipeline orchestration, final synthesis, trace logging
- **Input**: EC_XXX.json case file
- **Output**: Initiates chain, collects results, writes final JSON
- **Tools called**: get_order, get_order_items (initial lookup)

### 2. Customer Agent — gpt-4.1-nano (~1-2B)
- **Role**: Customer identity resolution + order history
- **Input**: claimed_order_id
- **Output**: customer_context {customer_unique_id, related_order_ids}
- **Tools called**: get_order → get_customer → get_related_orders

### 3. Order & Product Agent — gpt-4.1-nano (~1-2B)
- **Role**: Order status, items, products, categories, sellers
- **Input**: claimed_order_id
- **Output**: order_detail, items, product_context, sellers
- **Tools called**: get_order, get_order_items, get_product, get_seller, get_category_translation

### 4. Payment Agent — gpt-4.1-nano (~1-2B)
- **Role**: Payment aggregation + reconciliation
- **Input**: claimed_order_id
- **Output**: payment_reconciliation {currency, item_total, freight_total, expected_total, payment_total, difference, reconciled, payment_types}
- **Tools called**: get_payments, reconcile_payment

### 5. Delivery Agent — gpt-4.1-nano (~1-2B)
- **Role**: Delivery variance + seller handoff analysis
- **Input**: claimed_order_id
- **Output**: delivery_analysis {delivered_at, estimated_delivery_at, carrier_handoff_at, delivery_variance_hours, seller_handoff_analysis[], late_handoff_seller_ids[]}
- **Tools called**: get_order, get_order_items, compute_delivery_variance, compute_handoff_variances

### 6. Policy Agent — gpt-4o-mini (~8B)
- **Role**: Apply EC_POLICY_V2, determine primary/secondary issues, root cause, refund, actions
- **Input**: All prior agent outputs
- **Output**: policy_decision {primary_issue, secondary_issues, case_status, confidence, root_cause_code, responsible_parties, refund_brl, resolution_actions}
- **Tools called**: None (pure reasoning over received data)
- **Note**: responsible_parties party_type must be "platform", "seller", or "logistics_provider" (NOT "logistics")

### 7. Verifier Agent — gpt-4.1-nano (~1-2B)
- **Role**: Schema validation, array limits, evidence ID verification, monotonic checks
- **Input**: Final assembled output JSON
- **Output**: Verified/corrected final JSON
- **Tools called**: validate_output, get_order (evidence cross-check)

## Handoff Flow

```
Input EC_XXX
  → [1] Coordinator: parse → dispatch chain
  → [2] Customer: customer_context → JSON
  → [3] Order&Product: order_detail + items + products → JSON
  → [4] Payment: payment_reconciliation → JSON
  → [5] Delivery: delivery_analysis → JSON
  → [6] Policy: policy_decision (receives [2]+[3]+[4]+[5]) → JSON
  → [7] Verifier: validated final output (receives policy + all context) → JSON
  → output/EC_XXX.json + trace.jsonl
```

## Tool Layer (deterministic, Python+pandas)

Located in `src/tools.py`. All functions are pure-deterministic — no LLM arithmetic.

| Function | Returns |
|----------|---------|
| get_order(order_id) | order row dict or None |
| get_order_items(order_id) | list of item dicts |
| get_payments(order_id) | list of payment dicts |
| get_customer(customer_id) | customer dict |
| get_related_orders(customer_unique_id, limit=5) | list of order_ids |
| get_product(product_id) | product dict |
| get_seller(seller_id) | seller dict |
| get_category_translation(category_name) | English category string |
| compute_delivery_variance(delivered, estimated) | float hours (2 decimals) or None |
| compute_handoff_variances(items, carrier_date) | list of {seller_id, shipping_limit_at, handoff_variance_hours, late_handoff} |
| reconcile_payment(payments, items) | {payment_total, item_total, freight_total, expected_total, difference, reconciled, payment_types} |
| generate_evidence_ids(...) | list of evidence ID strings |
| validate_output(output, order_id) | list of error strings (empty = valid) |

## Model Assignment Summary

| Tier | Model | Params | Agents |
|------|-------|--------|--------|
| Reasoning | gpt-4o-mini | ~8B | Coordinator, Policy |
| Mechanical | gpt-4.1-nano | ~1-2B | Customer, Order&Product, Payment, Delivery, Verifier |

## Output Artifacts

| File | Location | Purpose |
|------|----------|---------|
| output/EC_001.json … EC_050.json | output/ | Submission zip contents |
| architecture.md | repo root | This file |
| metadata.json | repo root | Model + framework metadata |
| trace.jsonl | repo root | Execution trace (50 lines) |
| individual_5SoCuoiMHV_HoVaTen.md | repo root | Individual report |
| requirements.txt | repo root | Python dependencies |
