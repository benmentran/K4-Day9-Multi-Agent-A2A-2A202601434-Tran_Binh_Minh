#!/usr/bin/env python3
"""Script to check all 50 output files for anomalies."""
import json
from pathlib import Path

output_dir = Path('output')
anomalies = []

for case_num in range(1, 51):
    fname = f'EC_{case_num:03d}.json'
    fpath = output_dir / fname
    with open(fpath, encoding='utf-8') as f:
        d = json.load(f)

    cid = d.get('case_id')
    ca = d.get('case_assessment', {})
    primary = ca.get('primary_issue')
    secondary = ca.get('secondary_issues', [])
    status = ca.get('case_status')

    ae = d.get('affected_entities', {})
    delivery = d.get('delivery_analysis', {})
    payment = d.get('payment_reconciliation', {})
    fres = d.get('financial_resolution', {})
    rca = d.get('root_cause_analysis', {})
    actions = d.get('resolution_actions', [])
    evidence = d.get('evidence_ids', [])

    issues = []

    # 1. case_status vs refund mismatch
    refund = fres.get('recommended_refund_brl', 0)
    if refund > 0 and status == 'no_action':
        issues.append(f'MISMATCH: refund={refund} but status=no_action')
    if refund == 0 and status == 'action_required':
        issues.append('MISMATCH: refund=0 but status=action_required')

    # 2. canceled/unavailable orders: carrier_handoff_at should be null
    if primary in ('canceled_order_paid', 'unavailable_order_paid'):
        ch = delivery.get('carrier_handoff_at')
        if ch is not None:
            issues.append(f'ANOMALY: {primary} but carrier_handoff_at={ch} (should be null for non-delivered order)')

    # 3. late_delivery_* but variance is None or <=0
    if primary in ('late_delivery_seller', 'late_delivery_logistics'):
        dv = delivery.get('delivery_variance_hours')
        if dv is None:
            issues.append(f'ANOMALY: {primary} but delivery_variance_hours=null')
        elif dv <= 0:
            issues.append(f'ANOMALY: {primary} but variance={dv} (<=0, not actually late!)')

    # 4. unsupported_late_claim but delivery_variance > 0 (should be late_delivery!)
    if primary == 'unsupported_late_claim':
        dv = delivery.get('delivery_variance_hours')
        if dv is not None and dv > 0:
            issues.append(f'WRONG POLICY: unsupported_late_claim but variance={dv}>0 (should be late_delivery_*!)')

    # 5. valid_split_payment but fewer than 2 payment_ids
    if primary == 'valid_split_payment':
        payment_ids = ae.get('payment_ids', [])
        if len(payment_ids) < 2:
            issues.append(f'ANOMALY: valid_split_payment but only {len(payment_ids)} payment_ids')
        ptypes = payment.get('payment_types', [])
        if len(ptypes) < 2:
            issues.append(f'NOTE: valid_split_payment with only 1 payment_type: {ptypes}')

    # 6. late_delivery_seller missing seller evidence
    if primary == 'late_delivery_seller':
        has_seller_ev = any(e.startswith('seller:') for e in evidence)
        if not has_seller_ev:
            issues.append('MISSING EVIDENCE: late_delivery_seller but no seller:xxx evidence ID')

    # 7. multi_seller_order secondary missing coordinate_multi_seller_case action
    if 'multi_seller_order' in secondary and 'coordinate_multi_seller_case' not in actions:
        if primary not in ('canceled_order_paid', 'unavailable_order_paid'):
            issues.append('MISSING ACTION: multi_seller_order secondary but no coordinate_multi_seller_case')

    # 8. split_payment secondary (non-valid_split_payment) missing verify_payment_allocation
    if 'split_payment' in secondary and primary != 'valid_split_payment':
        if 'verify_payment_allocation' not in actions:
            issues.append('MISSING ACTION: split_payment secondary but no verify_payment_allocation')

    # 9. item_ids populated but product_ids empty
    item_ids = ae.get('item_ids', [])
    prod_ids = d.get('product_context', {}).get('product_ids', [])
    if item_ids and not prod_ids:
        issues.append(f'ANOMALY: has {len(item_ids)} item_ids but product_ids is empty')

    # 10. delivery_variance null but items exist (should be computable)
    if delivery.get('delivered_at') and delivery.get('estimated_delivery_at'):
        if delivery.get('delivery_variance_hours') is None:
            issues.append('ANOMALY: both delivered_at and estimated_delivery_at exist but variance is null')

    # 11. EC_010: valid_split_payment but payment_types shows only credit_card
    # (this is just a 1-type valid split)

    if issues:
        anomalies.append({'case': cid, 'primary': primary, 'status': status, 'refund': refund, 'issues': issues})

print(f'Total cases with issues: {len(anomalies)} / 50')
print()
for a in anomalies:
    print(f'{a["case"]} [{a["primary"]}] status={a["status"]} refund={a["refund"]}:')
    for issue in a['issues']:
        print(f'  - {issue}')
    print()

# Summary by issue type
from collections import Counter
all_issues = []
for a in anomalies:
    all_issues.extend(a['issues'])

print('--- ISSUE TYPE SUMMARY ---')
type_counts = Counter()
for i in all_issues:
    t = i.split(':')[0]
    type_counts[t] += 1
for t, c in type_counts.most_common():
    print(f'  {t}: {c}')
