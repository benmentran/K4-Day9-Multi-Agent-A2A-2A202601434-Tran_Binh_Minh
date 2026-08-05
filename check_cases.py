#!/usr/bin/env python3
from src.tools import get_order, get_payments, get_order_items

# Check EC_010 order - valid_split_payment with only 1 payment_type
order_id = '919baca007d9525b6668c18f79a33197'
order = get_order(order_id)
payments = get_payments(order_id)
items = get_order_items(order_id)
print('=== EC_010 ===')
print('order status:', order.get('order_status'))
print('Payments:')
for p in payments:
    seq = p.get('payment_sequential')
    ptype = p.get('payment_type')
    val = p.get('payment_value')
    print(f'  seq={seq} type={ptype} value={val}')
print()

# Check EC_047 order - canceled but carrier_handoff_at not null
order_id2 = '2cfc79d9582e9135c0a9b61fa60e6b21'
order2 = get_order(order_id2)
print('=== EC_047 ===')
print('order status:', order2.get('order_status'))
print('delivered_carrier_date:', order2.get('order_delivered_carrier_date'))
print('delivered_customer_date:', order2.get('order_delivered_customer_date'))
print('estimated_delivery:', order2.get('order_estimated_delivery_date'))
print()

# Also cross-check policy logic edge cases
# Check if any canceled orders have carrier_handoff in the CSV dataset
print('=== Checking all 50 input orders for status/carrier_handoff combinations ===')
import json
from pathlib import Path

input_dir = Path('input')
for case_num in range(1, 51):
    fname = f'EC_{case_num:03d}.json'
    with open(input_dir / fname) as f:
        inp = json.load(f)
    oid = inp['customer_request']['claimed_order_id']
    o = get_order(oid)
    if o:
        status = o.get('order_status')
        carrier = o.get('order_delivered_carrier_date')
        delivered = o.get('order_delivered_customer_date')
        if status in ('canceled', 'unavailable') and carrier:
            print(f'  EC_{case_num:03d}: status={status}, carrier_handoff={carrier}, delivered={delivered}')
