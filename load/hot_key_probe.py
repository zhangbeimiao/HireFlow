"""Send a synchronized cold-key burst and quantify database-origin amplification."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    parser.add_argument("--token", required=True)
    parser.add_argument("--requests", type=int, default=60)
    args = parser.parse_args()
    if not 2 <= args.requests <= 500:
        raise SystemExit("--requests must be between 2 and 500")

    barrier = Barrier(args.requests)
    headers = {"Authorization": f"Bearer {args.token}"}

    def read_hot_key(_: int) -> tuple[str, str]:
        barrier.wait(timeout=15)
        with httpx.Client(base_url=args.base_url, timeout=20) as client:
            response = client.get("/v1/jobs", headers=headers)
        response.raise_for_status()
        return response.headers.get("X-Cache", "MISSING"), response.headers.get(
            "X-HireFlow-Instance", "MISSING"
        )

    with ThreadPoolExecutor(max_workers=args.requests) as executor:
        results = list(executor.map(read_hot_key, range(args.requests)))

    outcomes = Counter(outcome for outcome, _ in results)
    instances = sorted({instance for _, instance in results})
    database_loads = outcomes["MISS"]
    report = {
        "requests": args.requests,
        "instances": instances,
        "instance_count": len(instances),
        "cache_outcomes": dict(sorted(outcomes.items())),
        "database_origin_loads": database_loads,
        "ideal_database_origin_loads": 1,
        "database_origin_amplification": database_loads,
    }
    print(json.dumps(report, indent=2, sort_keys=True))

    if len(instances) < 2:
        raise SystemExit("load balancer did not route the burst to multiple API instances")
    if database_loads != 1:
        raise SystemExit(f"expected exactly one database origin load, observed {database_loads}")
    if outcomes["HIT"] != args.requests - 1:
        raise SystemExit(f"expected the remaining requests to hit Redis, got {outcomes}")


if __name__ == "__main__":
    main()

