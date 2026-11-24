"""Deterministic chronological forecasting and transparent advisory rules."""
from datetime import date, timedelta
from math import ceil
from statistics import fmean, median
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

VERSION = "weekly-seasonal-trend-v1"
METHODS = ("naive", "seasonal_naive", "moving_average", "seasonal_trend")
WEEK = timedelta(days=7)
TARGET_COVERAGE = .8


class Replenishment(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    due_on: date
    units: float = Field(gt=0, le=1_000_000_000)


class ForecastSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    metric: Literal["demand", "net_revenue"] = "demand"
    horizon: Literal[1, 2, 4] = 4
    season: Literal[4, 13, 52] = 4
    as_of: date | None = None
    sku: str | None = Field(default=None, min_length=1, max_length=120, pattern=r"^[^\x00]+$")
    branch: str | None = Field(default=None, min_length=1, max_length=120, pattern=r"^[^\x00]+$")
    category: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[^\x00]+$")
    category_skus: list[str] = Field(default_factory=list, max_length=25)
    complete_sales_feed: bool = False
    lead_days: int | None = Field(default=None, gt=0, le=28)
    replenishments: list[Replenishment] | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def scope(self) -> "ForecastSpec":
        if bool(self.category) != bool(self.category_skus) or self.sku and self.category_skus:
            raise ValueError("Supply a category with explicit distinct SKU identifiers, or select one SKU")
        if (len(set(self.category_skus)) != len(self.category_skus)
                or any(not value.strip() or len(value) > 120 or "\x00" in value for value in self.category_skus)):
            raise ValueError("Invalid category SKU identifiers")
        if self.as_of and not date(2000, 1, 1) <= self.as_of <= date(2099, 12, 31):
            raise ValueError("Unsupported business date")
        return self


def monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def aligned(org_weeks: set[date], scoped: dict[date, float], end: date, complete: bool,
            signed: bool = False) -> tuple[list[date], list[float], int]:
    if not scoped:
        return [], [], 0
    start = max(min(scoped), end - 208 * WEEK)
    days = [start + i * WEEK for i in range((end - start).days // 7)]
    missing = [i for i, day in enumerate(days) if day not in scoped and (not complete or day not in org_weeks)]
    # Work with the latest uninterrupted segment. Interior or trailing gaps are never imputed.
    tail = days[missing[-1] + 1:] if missing else days
    values = [scoped.get(day, 0.0) for day in tail]
    if not signed and any(value < 0 for value in values):
        raise ValueError("Negative demand observations")
    return tail, values, len(missing)


def coefficients(values: list[float], season: int) -> tuple[float, list[float]]:
    slope = median((values[i] - values[i - season]) / season for i in range(season, len(values)))
    return slope, [median(values[j] - slope * j for j in range(phase, len(values), season)) for phase in range(season)]


def forecast(values: list[float], horizon: int, season: int, method: str, nonnegative: bool) -> list[float]:
    if method == "naive":
        return [values[-1]] * horizon
    if method == "moving_average":
        return [fmean(values[-season:])] * horizon
    if method == "seasonal_naive":
        return [values[-season + i % season] for i in range(horizon)]
    # Estimate slope from same-phase differences, then estimate an intercept
    # independently for each phase using only the supplied training prefix.
    slope, intercepts = coefficients(values, season)
    predictions = []
    for i in range(len(values), len(values) + horizon):
        prediction = intercepts[i % season] + slope * i
        predictions.append(max(prediction, 0) if nonnegative else prediction)
    return predictions


def error_metrics(actual: list[float], predicted: list[float]) -> dict[str, float | int | None]:
    errors = [p - a for a, p in zip(actual, predicted, strict=True)]
    denominator = sum(abs(v) for v in actual)
    return {"n": len(errors), "mae": fmean(abs(v) for v in errors), "bias": fmean(errors),
            "wape": sum(abs(v) for v in errors) / denominator if denominator else None}


def fit(days: list[date], values: list[float], spec: ForecastSpec) -> dict[str, Any]:
    horizon, season = spec.horizon, spec.season
    minimum = season * 2 + 16 * horizon
    if len(values) < minimum:
        return {"status": "insufficient_history", "required_weeks": minimum, "observed_weeks": len(values)}
    origins = [len(values) - i * horizon for i in range(16, 0, -1)]
    actual = [value for origin in origins for value in values[origin:origin + horizon]]
    predictions = {method: [point for origin in origins for point in forecast(
        values[:origin], horizon, season, method, spec.metric == "demand",
    )] for method in METHODS}
    tuning_end, calibration_end = 4 * horizon, 12 * horizon
    selected = min(METHODS, key=lambda method: float(error_metrics(
        actual[:tuning_end], predictions[method][:tuning_end],
    )["mae"] or 0))
    widths = []
    for lead in range(horizon):
        errors = sorted(abs(actual[i] - predictions[selected][i])
                        for i in range(tuning_end + lead, calibration_end, horizon))
        widths.append(errors[min(len(errors), ceil((len(errors) + 1) * TARGET_COVERAGE)) - 1])
    holdout = {method: error_metrics(actual[calibration_end:], predicted[calibration_end:])
               for method, predicted in predictions.items()}
    coverage_hits = sum(abs(actual[i] - predictions[selected][i]) <= widths[i % horizon]
                        for i in range(calibration_end, len(actual)))
    points = forecast(values, horizon, season, selected, spec.metric == "demand")
    baseline = float(holdout["seasonal_naive"]["mae"] or 0)
    low = 0 if spec.metric == "demand" else float("-inf")
    parameters: dict[str, Any]
    if selected == "seasonal_trend":
        slope, intercepts = coefficients(values, season)
        parameters = {"slope_per_week": slope, "phase_intercepts": intercepts, "next_index": len(values)}
    else:
        parameters = {"baseline_pattern": points}
    return {
        "status": "completed", "model_version": VERSION, "model": selected,
        "train_start": days[0].isoformat(), "train_end": days[-1].isoformat(),
        "evaluation_start": days[origins[12]].isoformat(), "evaluation_end": days[-1].isoformat(),
        "backtest": holdout, "seasonal_naive_mae_delta": float(holdout[selected]["mae"] or 0) - baseline,
        "interval": {"target": TARGET_COVERAGE, "coverage": coverage_hits / (4 * horizon),
                     "hits": coverage_hits, "samples": 4 * horizon, "calibration_origins": 8},
        "artifact": {"season": season, "horizon": horizon, "widths": widths, "selected": selected,
                     "parameters": parameters},
        "forecast": [{"week": (days[-1] + (index + 1) * WEEK).isoformat(),
                      "point": point, "lower": max(low, point - widths[index]), "upper": point + widths[index]}
                     for index, point in enumerate(points)],
        "history": [{"week": day.isoformat(), "value": value} for day, value in zip(days[-52:], values[-52:], strict=True)],
    }


def anomalies(series: dict[date, float], season: int) -> list[dict[str, Any]]:
    results = []
    for day, value in sorted(series.items())[-12:]:
        reference = [series[day - WEEK * season * i] for i in range(1, 9) if day - WEEK * season * i in series]
        if len(reference) < 4:
            continue
        center = median(reference)
        scale = max(1.4826 * median(abs(item - center) for item in reference), abs(center) * .1, 1)
        score = abs(value - center) / scale
        if score >= 3.5:
            results.append({"week": day.isoformat(), "observed": value, "expected": center,
                            "lower": center - 3.5 * scale, "upper": center + 3.5 * scale,
                            "score": round(score, 3), "severity": "high" if score >= 6 else "review",
                            "reference_count": len(reference)})
    return results


def stockout(result: dict[str, Any], spec: ForecastSpec, stock: float | None, start: date,
             velocity: float) -> dict[str, Any]:
    limits = ["Stock on hand excludes reservations and may overstate available stock."]
    if spec.metric != "demand" or not (spec.sku and spec.branch) or stock is None:
        return {"status": "unavailable", "limits": ["Select a product and branch with observed stock on hand."]}
    if spec.lead_days is None or spec.lead_days > spec.horizon * 7:
        return {"status": "unavailable", "stock_on_hand": stock,
                "limits": limits + ["Provide a supplier lead time within the modeled horizon."]}
    if spec.replenishments is None:
        limits.append("Confirmed replenishment is unavailable; this scenario includes no incoming stock.")
    deliveries = spec.replenishments or []
    points = result["forecast"]
    point = upper = lower = incoming = 0.0
    risk = "within_estimated_stock"
    for offset in range(spec.lead_days):
        day = start + timedelta(days=offset)
        incoming += sum(item.units for item in deliveries if item.due_on == day)
        point += points[offset // 7]["point"] / 7
        lower += points[offset // 7]["lower"] / 7
        upper += points[offset // 7]["upper"] / 7
        if point > stock + incoming:
            risk = "estimated_shortfall"
        elif risk != "estimated_shortfall" and upper > stock + incoming:
            risk = "possible_shortfall"
    return {"status": "limited", "risk": risk, "stock_on_hand": stock, "lead_days": spec.lead_days,
            "confirmed_replenishment": incoming if spec.replenishments is not None else None,
            "demand": point, "lower": lower, "upper": upper, "reorder_threshold_without_inbound": upper,
            "coverage_days": max(stock, 0) / velocity if velocity > 0 else None, "daily_velocity": velocity,
            "limits": limits + ["Advisory estimate. Weekly demand is allocated evenly across days.",
                                "Lead-time bounds sum weekly bounds and have no separate coverage guarantee."]}
