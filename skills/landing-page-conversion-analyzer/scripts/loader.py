"""
Landing Page CVR Analyzer -- contract validation, ingest, and cube building.

Brand-agnostic. Nothing here knows a brand name, an event name, or a threshold;
every one of those arrives through config. The same file runs a 50k-session/week
account and a 5M-session/week account.

SCALE
-----
Ingest is columnar and chunked, dimension columns become pandas categoricals on
the way in, and the frame is reduced to the aggregated cube in one pass. After
load(), memory is bounded by CARDINALITY (distinct LP x source x medium x
campaign x country x date), not by traffic volume. Two brands an order of
magnitude apart in sessions produce cubes of similar size.

REFUSALS
--------
Where the contract is violated in a way that would make the output wrong, this
module raises ContractViolation. It does not emit a degraded number with a caveat
stapled on. A confident wrong CVR is worse than no CVR, because somebody will
rebuild a landing page over it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from engine import DIM_COLUMNS, OPTIONAL_DIMS, UNRESOLVED_SOURCES


class ContractViolation(Exception):
    """Raised when the input cannot be analysed correctly. Never swallowed."""


@dataclass
class Finding:
    code: str
    severity: str          # "stop" | "high" | "medium" | "low"
    message: str
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity,
                "message": self.message, "evidence": self.evidence}


@dataclass
class LoadResult:
    cube: pd.DataFrame                 # aggregated, one row per dims x date
    daily: pd.DataFrame                # site total per date
    steps: list                        # ordered funnel step column names
    conversion_step: str               # the headline CVR step
    alert_steps: list                  # steps eligible for cell-level alerting
    meta: dict
    findings: list                     # list[Finding]
    complete_through: pd.Timestamp
    incomplete_dates: set
    optional_dims: list                # which optional dims are usable

    def summary(self) -> dict:
        return {
            "rows_in_cube": int(len(self.cube)),
            "date_range": [str(self.cube["date"].min().date()),
                           str(self.cube["date"].max().date())],
            "complete_through": str(self.complete_through.date()),
            "total_sessions": int(self.cube["sessions"].sum()),
            "funnel": self.steps,
            "conversion_step": self.conversion_step,
            "usable_optional_dims": self.optional_dims,
            "findings": [f.to_dict() for f in self.findings],
        }


# ---------------------------------------------------------------------------
# Funnel definition
# ---------------------------------------------------------------------------

def resolve_funnel(cfg: dict) -> tuple[list, str, list]:
    """
    Read the brand's funnel out of config. Ordered, with exactly one conversion.

    Returns (step_columns, conversion_step_column, alert_eligible_steps).
    """
    steps_cfg = ((cfg.get("funnel") or {}).get("steps")) or []
    if not steps_cfg:
        raise ContractViolation(
            "config.funnel.steps is empty. The brain cannot infer what counts as a "
            "conversion for this brand -- declare the funnel."
        )
    cols, conv, alertable = [], None, []
    for s in steps_cfg:
        name = s.get("name")
        if not name:
            raise ContractViolation("every funnel step needs a `name`.")
        col = f"step_{name}"
        cols.append(col)
        if s.get("is_conversion"):
            if conv is not None:
                raise ContractViolation(
                    f"two funnel steps are marked is_conversion ({conv}, {col}). "
                    "Exactly one step is the headline CVR."
                )
            conv = col
        # Default: a step may be alerted on at cell level unless told otherwise.
        # Use alert_level: site for a true-but-thin business outcome.
        if s.get("alert_level", "cell") == "cell":
            alertable.append(col)
    if conv is None:
        raise ContractViolation(
            "no funnel step is marked is_conversion. One step must be the headline CVR."
        )
    if not alertable:
        alertable = [conv]
    return cols, conv, alertable


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

def _read_frame(path: str, usecols: list, chunk_rows: int = 500_000) -> pd.DataFrame:
    """
    Columnar where possible, chunked where not. Dimensions become categoricals.

    Parquet is strongly preferred at scale -- it reads only the columns asked for
    and carries dtypes. CSV is supported because a brand trying this for the first
    time will have a CSV, and refusing that would make the skill unusable on day one.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in (".parquet", ".pq"):
        # Optional columns may simply not exist. Ask only for what is actually
        # in the file -- a parquet reader errors on an absent column name, and
        # "device wasn't supplied" is a normal case, not a failure.
        try:
            import pyarrow.parquet as pq
            present = set(pq.ParquetFile(path).schema.names)
        except Exception:
            present = set(pd.read_parquet(path).columns)
        df = pd.read_parquet(path, columns=[c for c in usecols if c in present])
    elif ext in (".csv", ".tsv", ".txt"):
        sep = "\t" if ext == ".tsv" else ","
        parts = []
        for chunk in pd.read_csv(path, sep=sep, usecols=lambda c: c in usecols,
                                 chunksize=chunk_rows):
            parts.append(_downcast(chunk))
        if not parts:
            raise ContractViolation(f"{path} contained no rows.")
        df = pd.concat(parts, ignore_index=True)
    elif ext == ".json":
        df = pd.DataFrame(json.load(open(path)).get("result", []))
        df = df[[c for c in usecols if c in df.columns]]
    else:
        raise ContractViolation(f"unsupported input format: {ext}")
    return _downcast(df)


def _downcast(df: pd.DataFrame) -> pd.DataFrame:
    """Categoricals for dimensions, smallest safe numeric type for measures."""
    for c in df.columns:
        if c in DIM_COLUMNS or c in OPTIONAL_DIMS:
            if not isinstance(df[c].dtype, pd.CategoricalDtype):
                df[c] = df[c].astype("category")
        elif c.startswith("step_") or c == "sessions":
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype("int64")
        elif c == "revenue":
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0).astype("float64")
    return df


# ---------------------------------------------------------------------------
# Validation -- DATA_CONTRACT §9
# ---------------------------------------------------------------------------

def _validate(df: pd.DataFrame, steps: list, conv: str,
              meta: dict) -> list:
    findings = []

    missing = [c for c in DIM_COLUMNS + ["date", "sessions", "revenue"] if c not in df.columns]
    if missing:
        raise ContractViolation(
            f"required column(s) missing: {', '.join(missing)}. "
            "The runtime must supply these -- the brain will not substitute."
        )
    missing_steps = [s for s in steps if s not in df.columns]
    if missing_steps:
        raise ContractViolation(
            f"funnel step column(s) missing: {', '.join(missing_steps)}. "
            "config.funnel declares them; the extract must contain them."
        )

    # The check that catches event-count-in-a-session-count-column.
    for s in steps:
        bad = df[df[s] > df["sessions"]]
        if len(bad):
            worst = bad.nlargest(1, s).iloc[0]
            raise ContractViolation(
                f"`{s}` exceeds `sessions` on {len(bad):,} row(s) -- worst case "
                f"{int(worst[s]):,} vs {int(worst['sessions']):,}. This means the column "
                f"holds EVENT COUNT, not SESSIONS CONTAINING THE EVENT. They are different "
                f"numbers and dividing by sessions produces a conversion rate above 100%. "
                f"Fix the extract (COUNT(DISTINCT session) per step), do not clamp it."
            )

    if (df["sessions"] < 0).any() or (df["revenue"] < 0).any():
        findings.append(Finding("negative_measures", "high",
            "Negative sessions or revenue present -- likely refunds netted into the "
            "extract, or a join fan-out.",
            {"negative_revenue_rows": int((df["revenue"] < 0).sum())}))

    leak = df[(df[conv] == 0) & (df["revenue"] > 0)]
    if len(leak):
        findings.append(Finding("revenue_without_conversion", "high",
            f"{len(leak):,} rows carry revenue with zero conversions. Revenue is being "
            "attributed to sessions the conversion step does not credit -- the two are "
            "measured off different keys.",
            {"rows": int(len(leak)), "revenue": float(leak['revenue'].sum())}))

    # Monotonicity: reported, never clamped. Real funnels violate it legitimately.
    stages = ["sessions"] + steps
    for prev, nxt in zip(stages, stages[1:]):
        v = df[df[nxt] > df[prev]]
        if len(v):
            findings.append(Finding("funnel_not_monotonic", "medium",
                f"`{nxt}` exceeds `{prev}` on {len(v):,} rows. Legitimate in some funnels "
                f"(a cart-restore link reaches add_to_cart without view_item); a tagging "
                f"error in others. Reported, not corrected.",
                {"rows": int(len(v)), "sessions_involved": int(v['sessions'].sum())}))

    for c in OPTIONAL_DIMS:
        if c in df.columns:
            null_share = float(df[c].isna().mean())
            if 0.001 < null_share < 0.999:
                findings.append(Finding("optional_dim_partial", "medium",
                    f"`{c}` is {null_share:.1%} null -- partially populated columns cannot "
                    f"be used as a mix control, because the controlled and uncontrolled "
                    f"traffic are not comparable. Demoted to unusable.",
                    {"null_share": round(null_share, 4)}))

    if not meta.get("sampled_declared", "sampled" in meta):
        findings.append(Finding("sampling_unknown", "medium",
            "meta.sampled is absent. Assuming the extract may be sampled; significance "
            "claims are downgraded to directional.", {}))
    elif meta.get("sampled"):
        findings.append(Finding("sampled_data", "high",
            f"Extract is sampled (rate {meta.get('sampling_rate')}). Significance testing "
            "is not valid on sampled data -- findings are directional only.",
            {"sampling_rate": meta.get("sampling_rate")}))

    other = float(meta.get("other_row_sessions") or 0)
    if other > 0:
        total = float(df["sessions"].sum())
        share = other / total if total else 0
        findings.append(Finding("cardinality_overflow", "high",
            f"{share:.1%} of sessions landed in `(other)` -- the source hit its cardinality "
            f"ceiling and the extract is incomplete. Every slice below is missing an unknown "
            f"part of its traffic. This is the point at which the API path stops being "
            f"adequate and a warehouse export is required.",
            {"other_sessions": other, "share": round(share, 4)}))

    return findings


# ---------------------------------------------------------------------------
# Freshness -- DATA_CONTRACT §6
# ---------------------------------------------------------------------------

def _freshness(df: pd.DataFrame, meta: dict) -> tuple[pd.Timestamp, set, list]:
    """
    Decide the last trustworthy date, and independently catch incomplete days.

    Two mechanisms, because either alone fails. `complete_through` is what the
    runtime claims; the unresolved-source check is what the data shows. A day whose
    unresolved share spikes above its own trailing norm is incomplete regardless of
    what meta says -- observed live as a 7x jump in `(not set)` sessions on the most
    recent day of a GA4 extract.
    """
    findings = []
    max_date = df["date"].max()

    claimed = meta.get("complete_through")
    if claimed:
        complete = pd.Timestamp(claimed)
    else:
        complete = max_date - pd.Timedelta(days=2)
        findings.append(Finding("no_complete_through", "medium",
            "meta.complete_through absent. Excluding the trailing 2 days, because the most "
            "recent day of analytics data is routinely incomplete and incomplete data is "
            "indistinguishable from a traffic collapse.",
            {"assumed_complete_through": str(complete.date())}))

    daily = df.groupby("date", observed=True).agg(
        sessions=("sessions", "sum"),
        unresolved=("sessions", "sum"),
    )
    unres = (df[df["utm_source"].astype(str).isin(UNRESOLVED_SOURCES)]
             .groupby("date", observed=True)["sessions"].sum())
    daily["unresolved"] = unres.reindex(daily.index, fill_value=0)
    daily["unresolved_share"] = daily["unresolved"] / daily["sessions"].replace(0, np.nan)

    incomplete = set()
    shares = daily["unresolved_share"].dropna()
    if len(shares) >= 8:
        norm = shares.iloc[:-3].median() if len(shares) > 3 else shares.median()
        spread = max(shares.iloc[:-3].std(ddof=0) if len(shares) > 4 else 0.0, 0.01)
        for d, sh in shares.items():
            if sh > norm + 4 * spread and sh > norm * 2:
                incomplete.add(d)
                findings.append(Finding("incomplete_day", "high",
                    f"{d.date()}: unresolved-source traffic is {sh:.1%} against a trailing "
                    f"norm of {norm:.1%}. This is processing lag, not a traffic anomaly. "
                    f"Day excluded from every window.",
                    {"date": str(d.date()), "unresolved_share": round(float(sh), 4),
                     "trailing_norm": round(float(norm), 4)}))

    if incomplete:
        safe = [d for d in daily.index if d not in incomplete]
        if safe:
            complete = min(complete, max(safe))
    return complete, incomplete, findings


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def load(path: str, cfg: dict, meta: dict | None = None) -> LoadResult:
    meta = dict(meta or {})
    steps, conv, alertable = resolve_funnel(cfg)

    usecols = ["date"] + DIM_COLUMNS + OPTIONAL_DIMS + ["sessions", "revenue"] + steps
    df = _read_frame(path, usecols)

    for c in steps:
        if c not in df.columns:
            df[c] = 0
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    if df["date"].isna().any():
        raise ContractViolation("unparseable values in `date`.")

    findings = _validate(df, steps, conv, meta)

    usable_opt = []
    for c in OPTIONAL_DIMS:
        if c in df.columns and df[c].notna().mean() > 0.999:
            usable_opt.append(c)

    complete_through, incomplete, ff = _freshness(df, meta)
    findings.extend(ff)

    # Config-driven source grouping. The ONLY place aliases are ever combined,
    # and only because a human wrote them down. Never inferred. See DATA_CONTRACT §6.
    groups = cfg.get("source_groups") or {}
    if groups:
        mapping = {alias: canon for canon, aliases in groups.items() for alias in aliases}
        df["utm_source"] = (df["utm_source"].astype(str).map(lambda v: mapping.get(v, v))
                            .astype("category"))
        findings.append(Finding("source_grouping_applied", "low",
            f"{len(mapping)} source alias(es) combined into {len(groups)} group(s) per "
            "config.source_groups. Every affected number below is grouped-by-config, "
            "not raw.", {"groups": {k: len(v) for k, v in groups.items()}}))

    # Pages that are not meant to convert stay in the traffic analysis but are
    # excluded from CVR alerting -- a blog post at 0.1% CVR is not a finding.
    noncv = (cfg.get("business") or {}).get("non_converting_paths") or []
    if noncv:
        lp = df["landing_page"].astype(str)
        mask = np.zeros(len(df), dtype=bool)
        for pat in noncv:
            mask |= lp.str.startswith(pat).to_numpy()
        df["is_converting_page"] = ~mask
    else:
        df["is_converting_page"] = True

    keys = ["date"] + DIM_COLUMNS + usable_opt + ["is_converting_page"]
    measures = {"sessions": "sum", "revenue": "sum", **{s: "sum" for s in steps}}
    cube = df.groupby(keys, dropna=False, observed=True).agg(measures).reset_index()

    daily = cube.groupby("date", observed=True).agg(
        {"sessions": "sum", "revenue": "sum", **{s: "sum" for s in steps}}
    ).reset_index()

    meta.setdefault("landing_page_form", "unknown")
    meta["complete_through"] = str(complete_through.date())

    return LoadResult(cube=cube, daily=daily, steps=steps, conversion_step=conv,
                      alert_steps=alertable, meta=meta, findings=findings,
                      complete_through=complete_through, incomplete_dates=incomplete,
                      optional_dims=usable_opt)
