"""
Entry point: python code/main.py [--sample] [--out PATH] [--verbose]

Reads dataset/ , runs the agent over requests.csv, and writes output.csv to the
repository root with the exact required schema.
"""

from __future__ import annotations

import argparse
import sys

import config
from agent import FinancialAgent
from dataio import write_csv


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Buy-or-Wait financial agent")
    parser.add_argument("--sample", action="store_true",
                        help="Run on sample_requests.csv instead of requests.csv")
    parser.add_argument("--out", default=None, help="Output CSV path")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    agent = FinancialAgent(use_sample_requests=args.sample, verbose=args.verbose)
    rows = agent.run()

    out_path = args.out or config.OUTPUT_PATH
    write_csv(out_path, config.OUTPUT_COLUMNS, rows)

    print(f"Wrote {len(rows)} predictions to {out_path}")
    if agent.resolver.stats:
        print(f"Image amounts: {agent.resolver.stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
