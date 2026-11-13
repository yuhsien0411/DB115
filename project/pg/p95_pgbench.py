#!/usr/bin/env python3
"""One run's default, unaggregated pgbench -l logs -> nearest-rank p95.

Usage: python3 pg/p95_pgbench.py 'results/run01/pgbench_log.*'
Use a separate directory for each measured run. No --aggregate-interval.
"""
import argparse
import glob
import json
import math
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pattern', help='quoted glob for a single run')
    args = parser.parse_args()
    paths = sorted(Path(p) for p in glob.glob(args.pattern))
    if not paths:
        parser.error('no logs matched')
    times_us = []
    rejected = {}
    for path in paths:
        with path.open() as handle:
            for line_no, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                fields = line.split()
                # Default: client_id transaction_no time script_no epoch epoch_us.
                if len(fields) < 6 or not all(fields[i].isdigit() for i in (0,1,3,4,5)):
                    parser.error(f'{path}:{line_no}: not a default transaction log')
                token = fields[2]
                if token in ('failed', 'skipped'):
                    rejected[token] = rejected.get(token, 0) + 1
                    continue
                try:
                    latency = float(token)
                except ValueError:
                    parser.error(f'{path}:{line_no}: unsupported latency {token!r}')
                if not math.isfinite(latency) or latency < 0:
                    parser.error(f'{path}:{line_no}: invalid latency')
                times_us.append(latency)
    if not times_us:
        parser.error('no successful samples')
    times_us.sort()
    index = math.ceil(0.95 * len(times_us)) - 1
    print(json.dumps({
        'files': [str(p) for p in paths],
        'successful_samples': len(times_us),
        'non_success_log_rows': rejected,
        'p95_ms': times_us[index] / 1000,
        'method': 'nearest-rank; successful transactions; one run',
        'note': 'Use pgbench summary/stderr for complete error counts and measured TPS.'
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
