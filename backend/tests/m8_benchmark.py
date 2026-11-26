"""Benchmark two synthetic tenants against an isolated loopback stack with a mock AI provider."""

import argparse
import json
import math
import statistics
from concurrent.futures import ThreadPoolExecutor
from platform import platform, processor
from time import perf_counter, sleep
from uuid import uuid4

import httpx2 as httpx

from test_m3 import SALES


def synthetic_sales(rows: int) -> str:
    header, *records = SALES.splitlines()
    generated = []
    for index in range(rows):
        values = records[index % len(records)].split(",")
        values[0] = f"L{index}"
        values[1] = f"{index // len(records)}-{values[1]}"
        generated.append(",".join(values))
    return "\n".join([header, *generated]) + "\n"


def trial(base: str, samples: int, sales: str) -> dict[str, list[tuple[float, bool]]]:
    with httpx.Client(base_url=base, timeout=30, headers={"Origin": "http://localhost:3000", "X-CSRF": "1"}) as client:
        def checked(response: httpx.Response, status: int) -> dict:
            if response.status_code != status:
                raise RuntimeError(f"Synthetic setup expected HTTP {status}, got {response.status_code}")
            return response.json()

        def measure(fn):
            started = perf_counter()
            try:
                ok = bool(fn())
            except httpx.HTTPError:
                ok = False
            return ((perf_counter() - started) * 1000, ok)

        credentials = {"email": f"{uuid4()}@example.test", "password": "synthetic-benchmark-only-password"}
        checked(client.post("/api/v1/auth/register", json=credentials), 201)
        login = measure(lambda: client.post("/api/v1/auth/login", json=credentials).status_code == 200)
        org = checked(client.post("/api/v1/organizations", json={"name": "Synthetic benchmark"}), 201)["id"]
        path = f"/api/v1/organizations/{org}"
        started_import = perf_counter()
        upload = checked(client.post(path + "/datasets/uploads", files={"file": ("sales.csv", sales)}), 201)
        job = checked(client.post(path + "/datasets/imports", json={
            "upload_id": upload["id"], "domain": "sales",
            "mapping": {field: field for field in SALES.splitlines()[0].split(",")},
        }), 202)
        for _ in range(100):
            status = checked(client.get(path + f"/jobs/{job['id']}"), 200)["status"]
            if status == "published":
                break
            if status == "failed":
                raise RuntimeError("Synthetic import failed")
            sleep(0.1)
        else:
            raise RuntimeError("Synthetic import timed out")
        import_result = ((perf_counter() - started_import) * 1000, True)
        dashboard_url = path + "/analytics/sales?start=2026-10-01&end=2026-11-01"
        checked(client.get(dashboard_url), 200)

        def report() -> bool:
            job = checked(client.post("/api/v1/reports", json={
                "organization_id": org, "request_key": str(uuid4()), "start": "2026-10-01", "end": "2026-11-01",
            }), 202)
            for _ in range(150):
                status = checked(client.get(f"/api/v1/reports/{job['id']}", params={"organization_id": org}), 200)["status"]
                if status in {"completed", "failed"}:
                    return status == "completed"
                sleep(0.1)
            return False

        return {
            "login": [login],
            "csv_import": [import_result],
            "dashboard": [measure(lambda: client.get(dashboard_url).status_code == 200) for _ in range(samples)],
            "mock_ai": [measure(lambda: client.post("/api/v1/ai/queries", json={
                "organization_id": org, "question": "What was October net revenue?",
            }).json().get("status") == "completed") for _ in range(samples)],
            "pdf_job": [measure(report)],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--samples", type=int, choices=range(1, 31), default=10)
    parser.add_argument("--tenants", type=int, choices=range(1, 9), default=2)
    parser.add_argument("--rows", type=int, choices=range(8, 5001), metavar="8..5000", default=8)
    args = parser.parse_args()
    started = perf_counter()
    sales = synthetic_sales(args.rows)
    with ThreadPoolExecutor(max_workers=args.tenants) as executor:
        pending = [
            executor.submit(trial, f"http://127.0.0.1:{args.port}", args.samples, sales)
            for _ in range(args.tenants)
        ]
        trials = [job.result() for job in pending]
    result = {}
    for operation in trials[0]:
        observations = [observation for tenant in trials for observation in tenant[operation]]
        durations = sorted(duration for duration, _ in observations)
        result[operation] = {
            "attempts": len(observations),
            "errors": sum(not ok for _, ok in observations),
            "p50_ms": round(statistics.median(durations), 1),
            "p95_ms": round(durations[math.ceil(len(durations) * 0.95) - 1], 1),
        }
    elapsed = perf_counter() - started
    print(json.dumps({
        "host": platform(),
        "processor": processor(),
        "tenants": args.tenants,
        "sales_rows_per_tenant": args.rows,
        "provider": "mock",
        "run_seconds_including_setup": round(elapsed, 2),
        "measured_requests_per_second_including_setup": round(
            sum(item["attempts"] for item in result.values()) / elapsed, 2,
        ),
        "operations": result,
    }, indent=2))
    if any(item["errors"] for item in result.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
