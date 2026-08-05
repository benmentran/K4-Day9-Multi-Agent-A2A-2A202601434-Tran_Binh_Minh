#!/usr/bin/env python3
"""
Main entry point for the Multi-Agent E-commerce Dispute Resolution system.
Processes all 50 cases from input/ and writes results to output/.

Usage:
    python main.py                  # Process all 50 cases
    python main.py --case EC_001    # Process a single case
    python main.py --retry-errors   # Re-run only cases that have error in output
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

if not os.environ.get("OPENAI_API_KEY"):
    print("ERROR: OPENAI_API_KEY not set in .env", file=sys.stderr)
    sys.exit(1)

from src.orchestrator import run_case


def main():
    parser = argparse.ArgumentParser(description="Multi-Agent E-commerce Dispute Resolution")
    parser.add_argument("--case", type=str, default=None,
                        help="Process a single case (e.g. EC_001)")
    parser.add_argument("--retry-errors", action="store_true",
                        help="Only re-run cases whose output contains an error field")
    args = parser.parse_args()

    base_dir = Path(__file__).parent
    input_dir = base_dir / "input"
    output_dir = base_dir / "output"
    trace_file = base_dir / "logging" / "trace.jsonl"

    output_dir.mkdir(exist_ok=True)
    trace_file.parent.mkdir(exist_ok=True)

    if args.case:
        # Single case mode — overwrite trace for this run
        trace_file.write_text("", encoding="utf-8")
        case_file = input_dir / f"{args.case}.json"
        if not case_file.exists():
            print(f"ERROR: {case_file} not found", file=sys.stderr)
            sys.exit(1)
        print(f"Processing {args.case}...")
        result = run_case(case_file, output_dir, trace_file)
        print(f"Done → output/{args.case}.json")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    # Batch mode
    case_files = sorted(input_dir.glob("EC_*.json"))
    if not case_files:
        print("ERROR: No case files found in input/", file=sys.stderr)
        sys.exit(1)

    if args.retry_errors:
        # Only re-run cases with errors in existing output
        to_run = []
        for cf in case_files:
            out_path = output_dir / cf.name
            if not out_path.exists():
                to_run.append(cf)
                continue
            with open(out_path) as f:
                d = json.load(f)
            if "error" in d:
                to_run.append(cf)
        print(f"Retrying {len(to_run)} failed cases...")
    else:
        to_run = case_files
        # Clear trace for a full fresh run
        trace_file.write_text("", encoding="utf-8")
        print(f"Processing {len(to_run)} cases...")

    success = 0
    errors = 0

    for i, case_file in enumerate(to_run):
        case_id = case_file.stem
        print(f"  [{case_id}] Processing...", end=" ", flush=True)
        try:
            result = run_case(case_file, output_dir, trace_file)
            if "error" in result:
                print(f"ERROR: {result['error'][:80]}")
                errors += 1
            else:
                primary = result.get("case_assessment", {}).get("primary_issue", "unknown")
                print(f"OK ({primary})")
                success += 1
        except Exception as e:
            print(f"EXCEPTION: {e}")
            errors += 1

        # Small sleep to stay within OpenAI rate limits
        if i < len(to_run) - 1:
            time.sleep(0.3)

    print(f"\nCompleted: {success} success, {errors} errors out of {len(to_run)} cases")
    print(f"Outputs in: {output_dir}")
    print(f"Trace in:   {trace_file}")


if __name__ == "__main__":
    main()
