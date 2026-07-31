"""Percentile ranking and cohort blending.

The specified function is

    pct(x, P) = 100 * |{y in P : y < x}| / (n - 1)

which is exact when no two members of P are equal. When there are ties it has to
say something deterministic, and "whichever order the rows came back in" is not
an answer -- two identical companies would get different percentiles depending on
the query plan. So ties take the MID-RANK: x's own tie group is split evenly
around it.

    pct(x, P) = 100 * (below + (equal - 1) / 2) / (n - 1)

`below` counts y < x and `equal` counts y == x, x itself included. With no ties
`equal` is 1, the correction term is 0, and this reduces to the specified formula
exactly. With ties every member of the tie group gets the identical value, which
is the property that matters.

UNAVAILABLE is not zero. With n < 2 the denominator is 0 and there is no ranking
to be had: a population of one has no order statistics. That returns None, and
None propagates into "this metric is not valid", never into a score of 0.

Lower-is-better metrics are inverted AFTER the percentile is taken, by
`invert_if_lower_is_better`. Inverting the raw value instead (1/x, -x) would
change the arithmetic; inverting the percentile is a relabelling of the same
order statistic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Names of every metric where a smaller raw number is the better outcome.
LOWER_IS_BETTER = frozenset({"pe", "pb", "ev_ebitda", "debt_ebitda"})


def percentile(x: float, population: list[float]) -> float | None:
    """Mid-rank percentile of x within population, or None when n < 2."""
    values = [float(v) for v in population]
    n = len(values)
    if n < 2:
        return None
    below = sum(1 for v in values if v < x)
    equal = sum(1 for v in values if v == x)
    # x is normally a member of P. Its own membership is not part of its tie
    # group; without this the maximum of a distinct population would score
    # above 100.
    ties = max(0, equal - 1)
    return 100.0 * (below + ties / 2.0) / (n - 1)


def invert_if_lower_is_better(pct: float | None, metric: str) -> float | None:
    """score = 100 - pct for lower-is-better metrics. Applied after ranking."""
    if pct is None:
        return None
    return 100.0 - pct if metric in LOWER_IS_BETTER else pct


@dataclass(frozen=True)
class RankedMetric:
    """One metric's percentile, with everything needed to re-derive it by hand."""

    metric: str
    raw_value: float | None
    valid: bool
    percentile: float | None            # blended, already inverted where needed
    reason: str | None = None
    # Comparison sets. Stored per the brief: population name, count and cutoff.
    market_population: str = ""
    market_count: int = 0
    market_percentile: float | None = None
    cohort_population: str | None = None
    cohort_count: int = 0
    cohort_percentile: float | None = None
    blend_weight: float = 0.0
    knowledge_cutoff: str = ""
    snapshot_id: str = ""

    def to_json(self) -> dict:
        return {
            "metric": self.metric,
            "raw_value": self.raw_value,
            "valid": self.valid,
            "reason": self.reason,
            "percentile": self.percentile,
            "lower_is_better": self.metric in LOWER_IS_BETTER,
            "comparison": {
                "snapshot_id": self.snapshot_id,
                "knowledge_cutoff": self.knowledge_cutoff,
                "market_population": self.market_population,
                "market_count": self.market_count,
                "market_percentile": self.market_percentile,
                "cohort_population": self.cohort_population,
                "cohort_count": self.cohort_count,
                "cohort_percentile": self.cohort_percentile,
                "blend_weight_w": self.blend_weight,
            },
        }


def blend_weight(cohort_valid_count: int, floor: int, target: int) -> float:
    """w = 0 below the floor, else clamp(n_c / target, 0, 1).

    n_c counts valid observations OF THAT METRIC inside the cohort, not the size
    of the cohort. A cohort of forty companies where only four report EV/EBITDA
    gives a four-observation percentile, and a four-observation percentile is
    noise, so the market population carries it instead.
    """
    if cohort_valid_count < floor:
        return 0.0
    return min(1.0, max(0.0, cohort_valid_count / float(target)))


def blend(
    cohort_pct: float | None, market_pct: float | None, w: float
) -> tuple[float | None, str | None]:
    """pct_final = w * cohort + (1 - w) * market. Returns (value, reason)."""
    if w > 0.0 and cohort_pct is None:
        return None, "cohort percentile unavailable"
    if w < 1.0 and market_pct is None:
        return None, "market percentile unavailable (n < 2)"
    if w <= 0.0:
        return market_pct, None
    if w >= 1.0:
        return cohort_pct, None
    return w * cohort_pct + (1.0 - w) * market_pct, None


@dataclass
class WeightedComponent:
    """A component built from weighted submetrics, with renormalisation.

    Renormalisation happens WITHIN a component and only across that component's
    own valid submetrics. Weight is never moved between components: if Value
    cannot be computed, Value is NULL and the security is unrankable -- Quality
    and Momentum do not grow to fill the gap.
    """

    name: str
    share: float = 1.0                          # the slice being renormalised
    items: list[tuple[str, float, RankedMetric]] = field(default_factory=list)
    fixed: list[dict] = field(default_factory=list)  # absolute, not renormalised
    # Momentum forbids renormalisation outright: its seven weights already sum
    # to 1.00 and a missing input must fail the gate, not inflate the others.
    renormalise: bool = True

    def add(self, key: str, nominal: float, ranked: RankedMetric) -> None:
        self.items.append((key, nominal, ranked))

    def valid_items(self) -> list[tuple[str, float, RankedMetric]]:
        return [
            item for item in self.items
            if item[2].valid and item[2].percentile is not None
        ]

    def effective_weights(self) -> dict[str, float]:
        valid = self.valid_items()
        if not self.renormalise:
            return {key: nominal for key, nominal, _ in valid}
        total = sum(nominal for _, nominal, _ in valid)
        if total <= 0:
            return {}
        return {key: self.share * nominal / total for key, nominal, _ in valid}

    def score(self) -> float | None:
        weights = self.effective_weights()
        if not weights:
            return None
        total = sum(item["weight"] * item["value"] for item in self.fixed)
        for key, _, ranked in self.valid_items():
            total += weights[key] * ranked.percentile
        return total

    def to_json(self) -> dict:
        weights = self.effective_weights()
        submetrics = []
        for item in self.fixed:
            submetrics.append(
                {
                    "metric": item["metric"],
                    "kind": "absolute",
                    "nominal_weight": item["weight"],
                    "valid": True,
                    "effective_weight": item["weight"],
                    "raw_value": item.get("raw_value"),
                    "percentile": None,
                    "value_used": item["value"],
                    "contribution": item["weight"] * item["value"],
                    "detail": item.get("detail"),
                }
            )
        for key, nominal, ranked in self.items:
            effective = weights.get(key, 0.0)
            entry = ranked.to_json()
            entry.update(
                {
                    "kind": "percentile",
                    "nominal_weight": nominal,
                    "effective_weight": effective,
                    "value_used": ranked.percentile,
                    "contribution": (
                        None if ranked.percentile is None else effective * ranked.percentile
                    ),
                }
            )
            submetrics.append(entry)
        return {
            "component": self.name,
            "renormalised_share": self.share,
            "effective_weight_sum": sum(weights.values())
            + sum(item["weight"] for item in self.fixed),
            "submetrics": submetrics,
        }
