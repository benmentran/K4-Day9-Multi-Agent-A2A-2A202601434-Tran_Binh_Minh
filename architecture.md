# Multi-Agent Architecture: E-commerce Dispute Resolution

## Overview

A 7-agent sequential handoff pipeline processes 50 Olist e-commerce dispute cases.
Agents are stratified by task complexity — smaller models handle simpler extraction
tasks; the largest available model handles complex policy reasoning.
Two agents (Payment, Delivery) are fully deterministic with no LLM involvement,
eliminating hallucination risk on numeric computations.

## Constraints

- Each agent model ≤ 10B parameters
- API keys in `.env` (never committed)
- Model names hardcoded in `src/agents.py` + declared in `metadata.json`

---

## Agent Roster & Model Assignment

```
┌─────────────────────────────────────────────────────────────────────┐
│                    COORDINATOR (gpt-4o-mini ~8B)                    │
│  Case intake · Data gathering via tools · Final output assembly     │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ pre-fetches ALL raw data (deterministic)
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
 ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
 │   CUSTOMER   │  │ORDER&PRODUCT │  │   PAYMENT    │
 │ gemma-3-1b   │  │ gemma-3-4b   │  │  (no LLM)    │
 │     (1B)     │  │    (4B)      │  │ deterministic│
 └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
        │                 │                 │
        │         ┌───────┘                 │
        │         ▼                         ▼
        │  ┌──────────────┐        ┌──────────────────┐
        │  │   DELIVERY   │        │  PAYMENT output  │
        │  │   (no LLM)   │        │  (passed thru)   │
        │  │ deterministic│        └────────┬─────────┘
        │  └──────┬───────┘                 │
        └─────────┴─────────────────────────┘
                           │ all 4 outputs
                           ▼
              ┌────────────────────────┐
              │     POLICY AGENT       │
              │   gpt-4o-mini (~8B)    │
              │  EC_POLICY_V2 rules    │
              └────────────┬───────────┘
                           │ policy_decision
                           ▼
              ┌────────────────────────┐
              │     VERIFIER AGENT     │
              │ gemini-2.5-flash-lite  │
              │  schema + logic check  │
              └────────────┬───────────┘
                           │ verified output
                           ▼
                    output/EC_XXX.json
                    logging/trace.jsonl
```

---

## Agent Details

### 1. Coordinator — `gpt-4o-mini` (~8B)
**Role**: Case intake, pre-fetch ALL data, orchestrate pipeline, assemble final output.
**Why this model**: Needs to understand the case structure and coordinate; handles errors gracefully.
**Data fetched** (all via deterministic tools, no LLM arithmetic):
- `get_order()`, `get_order_items()`, `get_payments()`
- `get_customer()`, `get_related_orders()`
- `get_product()`, `get_category_translation()` per item
- `reconcile_payment()`, `compute_delivery_variance()`, `compute_handoff_variances()`

### 2. Customer Agent — `gemini/gemma-3-1b-it` (1B)
**Role**: Extract `customer_unique_id` and `related_order_ids` from pre-fetched dict.
**Why 1B**: Simplest task in the pipeline — read two fields from a pre-loaded JSON object. No reasoning required.
**Input**: `{order, customer, related_orders}` (pre-fetched)
**Output**: `{customer_unique_id, related_order_ids}`

### 3. Order & Product Agent — `gemini/gemma-3-4b-it` (4B)
**Role**: Extract order status, timestamps, items list, deduplicate seller/product/category lists.
**Why 4B**: Light reasoning needed for deduplication and proper category name extraction; 1B is too unreliable for structured multi-field output.
**Input**: `{order, items (enriched with category_name)}`
**Output**: `{order_status, timestamps, items[], seller_ids[], product_ids[], category_names[]}`

### 4. Payment Agent — **PURE DETERMINISTIC** (no LLM)
**Role**: Return `payment_reconciliation` section.
**Why no LLM**: `tools.reconcile_payment()` computes all values deterministically with correct 2-decimal rounding. Passing numbers through an LLM introduces hallucination risk with zero accuracy benefit.
**Input**: Pre-computed `reconciliation` dict from tools
**Output**: `payment_reconciliation` (direct passthrough)

### 5. Delivery Agent — **PURE DETERMINISTIC** (no LLM)
**Role**: Return `delivery_analysis` section.
**Why no LLM**: `tools.compute_delivery_variance()` and `tools.compute_handoff_variances()` handle all timestamp arithmetic precisely. Same reasoning as Payment Agent.
**Input**: `order_data`, pre-computed `delivery_variance_hours`, `handoff_variances`
**Output**: `delivery_analysis` (assembled from deterministic results)

### 6. Policy Agent — `gpt-4o-mini` (~8B)
**Role**: Apply EC_POLICY_V2, determine primary/secondary issues, root cause, refund amount, resolution actions.
**Why ~8B (largest available)**: Most complex reasoning in the pipeline — 6 mutually exclusive primary rules with priority order, 5 conditional secondary issues, multiple action composition rules, and edge cases that require understanding of the full business context. Smaller models produce incorrect policy selections on ambiguous cases.
**Input**: Outputs of agents 2–5 (compact summary, not raw data)
**Output**: `{primary_issue, secondary_issues, case_status, confidence, root_cause_code, responsible_parties, refund_brl, resolution_actions}`

### 7. Verifier Agent — `gemini/gemini-2.5-flash-lite` (<10B)
**Role**: Validate schema conformance, array limits, business logic consistency.
**Why flash-lite**: Fast, cheap, accurate at pattern-matching and schema checking. Deep reasoning is not needed — the rules are structural (max 5 items, correct timestamp format, case_status matches refund amount).
**Input**: Assembled output + tool-detected validation errors
**Output**: `{validated, errors, corrected_output}`

---

## Handoff Flow (data contracts)

```
EC_XXX.json
  ↓
[Coordinator] ─── raw_data via tools ──→ all downstream agents
  ↓
[Customer]  ──→  customer_output: {customer_unique_id, related_order_ids}
  ↓
[OrderProd] ──→  order_product_output: {order_status, timestamps, items[], seller_ids[], ...}
  ↓
[Payment]   ──→  payment_output: {item_total_brl, freight_total_brl, expected_total_brl, ...}
  ↓
[Delivery]  ──→  delivery_output: {delivered_at, delivery_variance_hours, seller_handoff_analysis[], ...}
  ↓
[Policy]    ──→  policy_output: {primary_issue, secondary_issues, refund_brl, resolution_actions, ...}
  ↓
[Assemble]  ──→  final_output (full README §6 schema)
  ↓
[Verifier]  ──→  validated output (corrected if needed)
  ↓
output/EC_XXX.json  +  logging/trace.jsonl
```

---

## Tool Layer (deterministic, Python + pandas)

Located in `src/tools.py`. All functions are pure-deterministic — zero LLM involvement.

| Function | Returns |
|---|---|
| `get_order(order_id)` | order row dict or None |
| `get_order_items(order_id)` | list of item dicts |
| `get_payments(order_id)` | list of payment dicts |
| `get_customer(customer_id)` | customer dict |
| `get_related_orders(customer_unique_id, limit)` | list of order_ids |
| `get_product(product_id)` | product dict |
| `get_seller(seller_id)` | seller dict |
| `get_category_translation(category_name)` | English category string |
| `compute_delivery_variance(delivered, estimated)` | float hours (2 dec) or None |
| `compute_handoff_variances(items, carrier_date)` | list of per-seller variance dicts |
| `reconcile_payment(payments, items)` | reconciliation dict |
| `generate_evidence_ids(...)` | list of evidence ID strings |
| `validate_output(output, order_id)` | list of error strings (empty = valid) |

---

## Model Summary

| Agent | Model | Params | Provider |
|---|---|---|---|
| Coordinator | gpt-4o-mini | ~8B | OpenAI |
| Customer | gemma-3-1b-it | 1B | Google Gemini |
| Order & Product | gemma-3-4b-it | 4B | Google Gemini |
| Payment | *(no LLM)* | — | — |
| Delivery | *(no LLM)* | — | — |
| Policy | gpt-4o-mini | ~8B | OpenAI |
| Verifier | gemini-2.5-flash-lite | <10B | Google Gemini |

All models are ≤ 10B parameters as required.

---

## Design Principles

### 1. Deterministic Core + Selective LLM Reasoning
- **Tools layer**: All CSV lookups, arithmetic (variance hours, reconciliation totals), evidence ID generation, and schema validation run in pure Python + pandas. No LLM touches numbers.
- **LLM agents**: Handle interpretation, policy rule selection, and structured output synthesis where reasoning genuinely adds value.
- **Two agents with no LLM at all**: Payment and Delivery — their outputs are 100% verifiable from the CSV data with no ambiguity.

### 2. Right-sizing models to tasks
The pipeline is explicitly **not** uniform — using a 1B model for extraction and an 8B model for policy reasoning reflects the actual difficulty gradient. This reduces cost and latency for simpler agents while preserving quality where it matters.

### 3. Data is pre-fetched by Coordinator
Rather than each agent independently loading CSVs, the Coordinator fetches everything upfront. This means:
- CSV is loaded once (singleton cache in tools.py)
- Agents receive structured dicts, not file paths
- The LLM context contains only the relevant subset of data

### 4. Evidence IDs are always deterministic
`generate_evidence_ids()` is called in the orchestrator, not by any LLM. This eliminates the most common source of false positives (hallucinated IDs).

---

## Framework

**LiteLLM + custom Python orchestrator**
- LiteLLM provides a unified `completion()` interface for both OpenAI and Gemini APIs — switching models requires changing one constant, not rewriting API calls.
- Custom orchestrator gives direct control over agent ordering, error handling, and trace logging without framework overhead.
- Pydantic is used for output schema validation at the tool layer.

---

## Output Artifacts

| File | Location | Purpose |
|---|---|---|
| `EC_001.json` … `EC_050.json` | `output/` | Submission zip contents |
| `architecture.md` | repo root | This file |
| `metadata.json` | repo root | Model + framework metadata |
| `logging/trace.jsonl` | `logging/` | Execution trace (one line per case, 50 total) |
| `individual_01434_TranBinhMinh.md` | repo root | Individual report |
| `requirements.txt` | repo root | Python dependencies |
