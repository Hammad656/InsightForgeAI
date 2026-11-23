"""Execute production analytical paths against independently scored benchmark cases."""
import hashlib
import json
import math
import platform
from datetime import date
from decimal import Decimal
from time import perf_counter
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from app.ai_analytics import METRICS, Explanation, Intent, analyze, reporting_transaction
from app.ai_provider import OpenAIProvider, Output, Provider, ProviderUnavailable, get_provider
from app.core.config import get_settings
from app.eval_data import OTHER_SALES, seed
from app.eval_fixtures import CASES, DAY, LEDGERS, ORG, OTHER, SUITE, VERSIONS, Case, fixture_id
from app.eval_reference import REFERENCES
from app.orchestration import Plan, coordinate, plan_question, task_result
from app.sql_policy import QueryRejected, validate_sql
from sqlalchemy import text

MODEL = "offline-annotated-intent-v1"
PROMPT = "m4-intent-v1/m6b-plan-v1"
FIXED_DAY = date.fromisoformat(DAY)
MULTI = (("sales", "net_revenue", "sales"), ("inventory", "stock_on_hand", "inventory"),
         ("receivables", "outstanding", "payments"))
LIVE_CASE_IDS = frozenset({"R001", "R058", "R086", "R113", "X02", "S01"})


def live_available() -> bool:
    settings = get_settings()
    return (settings.evaluation_live_enabled and settings.ai_provider == "openai"
            and bool(settings.openai_api_key.get_secret_value())
            and settings.evaluation_cost_ceiling_per_million_tokens is not None)


def manifest(live: bool = False) -> dict[str, Any]:
    return {"fixture": SUITE, "case_count": len(LIVE_CASE_IDS) if live else len(CASES),
            "mode": "live_provider" if live else "mocked_offline", "prompt_version": PROMPT,
            "semantic_version": 1, "fixture_hash": hashlib.sha256(repr((CASES, LEDGERS)).encode()).hexdigest(),
            "dataset_versions": {key: str(value) for key, value in VERSIONS.items()},
            "python": platform.python_version(), "platform": platform.system()}


class Replay:
    def __init__(self, case: Case) -> None:
        self.case = case

    def structured(self, schema: type[Output], instructions: str, context: dict[str, object]) -> tuple[Output, int]:
        case = self.case
        if schema is Explanation:
            return schema.model_validate({"emphasis": "scope"}), 0
        if schema is Plan:
            instructions_payload = [{
                "id": index, "domain": domain, "metric_id": metric, "mode": "standard",
                "depends_on": [1] if case.id == "X06" and index == 2 else [],
                "dimension": None, "start": None, "end": None, "as_of": None, "branch": None,
                "sku": None, "customer": None, **case.options[domain],
            } for index, (domain, metric, _) in enumerate(MULTI, 1)]
            return schema.model_validate({"status": "ready", "tasks": instructions_payload}), 0
        fields = {key: value for key, value in case.options.items() if key in Intent.model_fields}
        return schema.model_validate({
            "kind": "group" if "dimension" in fields else "total", "metric_id": case.metric,
            "dimension": None, "start": None, "end": None, "as_of": None, "branch": None, "sku": None,
            "customer": None, "limit": None, **fields,
        }), 0


def normalize(value: Any) -> Any:
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()}
    if type(value) is date:
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return value


def equivalent(left: Any, right: Any) -> bool:
    return bool(normalize(left) == normalize(right))


def expected(case: Case) -> tuple[str, list[dict[str, Any]] | None]:
    if case.decision or case.domain == "multi":
        return case.decision or "completed", None
    assert case.metric
    rows = REFERENCES[case.domain](case.metric, case.options)
    return ("completed" if any(list(row.values())[-1] is not None for row in rows) else "insufficient_data"), rows


def faithful(item: dict[str, Any]) -> bool:
    columns = item["result"]["columns"]
    rows = item["result"]["rows"]
    answer = item["answer"]
    label = str(METRICS[item["evidence"]["metric_id"]]["label"])
    if not rows or all(row[columns[-1]] is None for row in rows):
        return answer == f"{label} is unavailable for the selected scope."
    if len(columns) == 1:
        value = rows[0][columns[0]]
        return answer in {f"For the selected scope, {label.lower()} is {value}.",
                          f"{label} is {value} for the selected scope."}
    return answer == f"The result shows {label.lower()} across {len(rows)} groups in the selected scope."


def correct_evidence(output: dict[str, Any], metric: str | None, domain: str) -> bool:
    evidence = output.get("evidence", {})
    return (evidence.get("metric_id") == metric and evidence.get("metric_version") == 1
            and [str(item) for item in evidence.get("dataset_versions", [])] == [str(VERSIONS[domain])])


def run_case(case: Case, sources: dict[str, dict[str, Any]], provider: Provider | None = None) -> dict[str, Any]:
    start = perf_counter()
    llm = provider or Replay(case)
    wanted, reference = expected(case)
    analytical = case.domain == "multi" or reference is not None
    isolation: bool | None = False if case.options.get("foreign_version") else None
    denial: bool | None = False if "policy" in case.options else None
    valid: bool | None = False if analytical else None
    selected: bool | None = False if analytical else None
    grounded: bool | None = False if analytical else None
    tokens: int | None = None
    verified: bool | None = False if analytical else None
    try:
        if case.domain == "multi":
            plan, tokens = plan_question(llm, case.question, FIXED_DAY)
            outputs = coordinate(plan, lambda task: task_result(task, ORG, sources.get(
                {"receivables": "payments"}.get(task.domain, task.domain)), FIXED_DAY, lambda: 2000),
                lambda *_: None, lambda: 2000)
            status = "completed" if len(outputs) == len(MULTI) and all(
                value["status"] == "completed" for value in outputs.values()
            ) else "incomplete"
            valid = len(outputs) == len(MULTI) and all("result" in value for value in outputs.values())
            selected = valid and all(correct_evidence(outputs.get(index, {}), metric, domain)
                                     for index, (_, metric, domain) in enumerate(MULTI, 1))
            grounded = valid and all(faithful(value) for value in outputs.values())
            verified = valid and all(equivalent(outputs.get(index, {}).get("result", {}).get("rows"),
                                                REFERENCES[domain](metric, case.options[domain]))
                                     for index, (domain, metric, _) in enumerate(MULTI, 1))
        elif "policy" in case.options:
            try:
                validate_sql(case.options["policy"])
                status, denial = "accepted", False
            except QueryRejected:
                status, denial = "query_rejected", True
        else:
            versions = [OTHER_SALES] if case.options.get("foreign_version") else (
                [fixture_id("unpublished-version")] if case.options.get("missing_version") else []
            )
            output = analyze(llm, ORG, case.question, versions, "table")
            status = output["status"]
            tokens = output.get("usage", {}).get("llm_tokens")
            if case.options.get("foreign_version"):
                isolation_query = text("SELECT count(*) FROM reporting.rows WHERE version_id=:id")
                with reporting_transaction(OTHER) as db:
                    control = db.execute(isolation_query, {"id": OTHER_SALES}).scalar_one()
                with reporting_transaction(ORG) as db:
                    visible = db.execute(isolation_query, {"id": OTHER_SALES}).scalar_one()
                isolation = control > 0 and visible == 0 and "result" not in output
            if reference is not None:
                valid = "result" in output
                verified = valid and equivalent(output["result"]["rows"], reference)
                domain = {"receivables": "payments"}.get(case.domain, case.domain)
                selected = correct_evidence(output, case.metric, domain)
                grounded = valid and faithful(output)
    except (QueryRejected, ValidationError, HTTPException):
        status = "query_rejected"
    except ProviderUnavailable:
        status = "execution_error"
    correct = status == wanted and verified is not False and selected is not False and grounded is not False
    if reference is not None and verified is None:
        correct = False
    correct = correct and isolation is not False and denial is not False
    return {
        "id": case.id, "domain": case.domain, "category": case.category, "complexity": case.complexity,
        "metric": case.metric, "reference": case.reference, "actor_role": case.role, "fixture": case.fixture,
        "organization_context": "synthetic_primary_and_control" if isolation is not None else "synthetic_primary",
        "expected_status": wanted, "actual_status": status, "passed": correct,
        "failure": None if correct else ("execution" if status == "execution_error" or valid is False else
                                         "policy" if case.decision else "answer_mismatch"),
        "execution_valid": valid, "result_correct": verified, "metric_correct": selected, "faithful": grounded,
        "decision_correct": correct if case.decision else None, "tenant_isolated": isolation, "sql_denied": denial,
        "no_unsupported_claim": grounded,
        "latency_ms": round((perf_counter() - start) * 1000, 3), "tokens": tokens, "estimated_cost_usd": None,
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    def score(key: str) -> dict[str, int]:
        samples = [item[key] for item in results if item[key] is not None]
        return {"passed": sum(samples), "total": len(samples)}
    latencies = sorted(item["latency_ms"] for item in results)
    def percentile(p: float) -> float | None:
        return latencies[math.ceil(len(latencies) * p) - 1] if latencies else None
    domains = {domain: {"passed": sum(r["passed"] for r in results if r["domain"] == domain),
                        "total": sum(r["domain"] == domain for r in results)}
               for domain in sorted({r["domain"] for r in results})}
    return {key: score(key) for key in ("passed", "execution_valid", "result_correct", "metric_correct", "faithful",
                                      "decision_correct", "tenant_isolated", "sql_denied", "no_unsupported_claim")} | {
        "domains": domains, "latency_p50_ms": percentile(.5), "latency_p95_ms": percentile(.95),
        "tokens": sum(item["tokens"] or 0 for item in results),
        "token_samples": sum(item["tokens"] is not None for item in results),
        "estimated_cost_usd": (sum(item["estimated_cost_usd"] or 0 for item in results)
                               if any(item["estimated_cost_usd"] is not None for item in results) else None),
    }


def execute(mode: str = "mocked_offline", model: str = MODEL) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    provider = None
    rate = None
    if mode == "live_provider":
        if not live_available():
            raise ProviderUnavailable("Live evaluations are disabled")
        provider = get_provider()
        if not isinstance(provider, OpenAIProvider) or provider.model != model:
            raise ProviderUnavailable("Live evaluation model changed")
        rate = get_settings().evaluation_cost_ceiling_per_million_tokens
    sources = seed()
    results = [run_case(case, sources, provider) for case in CASES if provider is None or case.id in LIVE_CASE_IDS]
    for item in results:
        if rate is not None and item["tokens"] is not None:
            item["estimated_cost_usd"] = float(rate * item["tokens"] / 1_000_000)
    return results, summarize(results)


if __name__ == "__main__":
    report, scores = execute()
    print(json.dumps({"manifest": manifest(), "scores": scores,
                      "failed_cases": [item["id"] for item in report if not item["passed"]]}, indent=2))
    raise SystemExit(any(not item["passed"] for item in report))
