"""Shared numeric helpers for ETF PCF look-through scripts."""

from __future__ import annotations

import math
import shutil
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


TRADING_DAYS_PER_YEAR = 252
CALENDAR_DAYS_PER_YEAR = 365.25


def _replace_file(source: Path, target: Path) -> None:
    source.replace(target)


def publish_staged_files(
    staged_files: list[tuple[Path, Path]],
    *,
    stale_targets: Iterable[Path] = (),
    commit_file: tuple[Path, Path] | None = None,
) -> None:
    """Publish a validated file set and restore the previous set on failure."""
    stale = list(stale_targets)
    actions = list(staged_files)
    if commit_file is not None:
        actions.append(commit_file)
    if not actions and not stale:
        return

    missing = [str(source) for source, _ in actions if not source.is_file()]
    if missing:
        raise RuntimeError(f"staged files missing: {', '.join(missing)}")

    targets = [target for _, target in actions] + stale
    seen: set[str] = set()
    for target in targets:
        key = str(target.absolute()).casefold()
        if key in seen:
            raise RuntimeError(f"duplicate publication target: {target}")
        seen.add(key)

    backup_parent = (actions[0][1] if actions else stale[0]).parent
    backup_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".pcf-backup-", dir=backup_parent) as tmpdir:
        backup_dir = Path(tmpdir)
        backups: dict[Path, Path] = {}
        for index, target in enumerate(targets):
            if target.is_file():
                backup = backup_dir / f"{index:04d}.bak"
                shutil.copy2(target, backup)
                backups[target] = backup

        mutated: list[Path] = []
        try:
            for source, target in staged_files:
                target.parent.mkdir(parents=True, exist_ok=True)
                _replace_file(source, target)
                mutated.append(target)
            for target in stale:
                if target.is_file():
                    target.unlink()
                    mutated.append(target)
            if commit_file is not None:
                source, target = commit_file
                target.parent.mkdir(parents=True, exist_ok=True)
                _replace_file(source, target)
                mutated.append(target)
        except Exception as exc:
            rollback_errors: list[str] = []
            for target in reversed(mutated):
                try:
                    backup = backups.get(target)
                    if backup is not None and backup.is_file():
                        _replace_file(backup, target)
                    elif target.is_file():
                        target.unlink()
                except Exception as rollback_exc:  # noqa: BLE001
                    rollback_errors.append(f"{target}: {rollback_exc}")
            if rollback_errors:
                raise RuntimeError(
                    f"output publication failed ({exc}); rollback also failed: {'; '.join(rollback_errors)}"
                ) from exc
            raise


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except TypeError:
        return False


def to_float(value: Any) -> float | None:
    if is_missing(value):
        return None
    text = str(value).strip().replace(",", "")
    if text.endswith("%"):
        text = text[:-1]
    if text in {"", "-", "--", "None", "nan", "NaN", "NaT"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def weighted_average(
    rows: list[dict[str, Any]],
    key: str,
    *,
    weight_key: str = "weight_pct",
    positive_only: bool = False,
) -> tuple[float | None, float]:
    total_weight = 0.0
    total = 0.0
    for row in rows:
        value = to_float(row.get(key))
        weight = to_float(row.get(weight_key)) or 0.0
        if value is None:
            continue
        if positive_only and value <= 0:
            continue
        total += weight * value
        total_weight += weight
    if total_weight <= 0:
        return None, 0.0
    return total / total_weight, total_weight


def earnings_yield_pe(
    rows: list[dict[str, Any]],
    *,
    pe_key: str = "pe",
    weight_key: str = "weight_pct",
) -> tuple[float | None, float, float]:
    total_weight = 0.0
    earnings_yield = 0.0
    negative_weight = 0.0
    for row in rows:
        pe = to_float(row.get(pe_key))
        weight = to_float(row.get(weight_key)) or 0.0
        if pe is None:
            continue
        if pe <= 0:
            negative_weight += weight
            continue
        earnings_yield += weight / pe
        total_weight += weight
    if total_weight <= 0 or earnings_yield <= 0:
        return None, total_weight, negative_weight
    return total_weight / earnings_yield, total_weight, negative_weight


def annualized_return(first_value: float, last_value: float, first_date: date, last_date: date) -> float | None:
    days = (last_date - first_date).days
    if first_value <= 0 or last_value <= 0 or days <= 0:
        return None
    return (last_value / first_value) ** (CALENDAR_DAYS_PER_YEAR / days) - 1.0


def normalize_price_frame(df: pd.DataFrame, date_col: str, value_col: str) -> pd.DataFrame:
    temp = df[[date_col, value_col]].copy()
    temp.columns = ["date", "value"]
    temp["date"] = pd.to_datetime(temp["date"], errors="coerce")
    temp["value"] = pd.to_numeric(temp["value"], errors="coerce")
    temp = temp.dropna()
    if getattr(temp["date"].dt, "tz", None) is not None:
        temp["date"] = temp["date"].dt.tz_localize(None)
    temp["date"] = temp["date"].dt.normalize()
    temp = temp[temp["value"] > 0]
    return temp.sort_values("date").drop_duplicates("date", keep="last")


def normalize_price_series(series: pd.Series) -> pd.Series:
    temp = pd.Series(series, copy=True)
    temp.index = pd.to_datetime(temp.index, errors="coerce")
    if isinstance(temp.index, pd.DatetimeIndex):
        if temp.index.tz is not None:
            temp.index = temp.index.tz_localize(None)
        temp.index = temp.index.normalize()
    temp = pd.to_numeric(temp, errors="coerce")
    temp = temp[temp.index.notna() & temp.notna()].sort_index()
    temp = temp[~temp.index.duplicated(keep="last")].astype(float)
    return temp[temp > 0]


def trailing_return_pct(series: pd.Series, days_back: int, min_days: int) -> float | None:
    series = normalize_price_series(series)
    if len(series) < 2:
        return None
    last_date = series.index[-1]
    target = last_date - pd.Timedelta(days=days_back)
    eligible = series[series.index <= target]
    first_date = eligible.index[-1] if not eligible.empty else series.index[0]
    if (last_date - first_date).days < min_days:
        return None
    first = float(eligible.iloc[-1] if not eligible.empty else series.iloc[0])
    last = float(series.iloc[-1])
    return None if first <= 0 or last <= 0 else (last / first - 1.0) * 100


def price_series_metrics(series: pd.Series) -> dict[str, Any]:
    series = normalize_price_series(series)
    if len(series) < 30:
        return {"error": f"price series too short: {len(series)}"}

    last_date = series.index[-1]
    risk_prices = series[series.index >= last_date - pd.Timedelta(days=365)]
    returns = risk_prices.pct_change().dropna()
    result: dict[str, Any] = {
        "annualized_return_pct": None,
        "sortino_ratio": None,
        "volatility_pct": None,
        "half_year_return_pct": trailing_return_pct(series, 183, 150),
        "one_year_return_pct": trailing_return_pct(series, 365, 330),
        "three_year_return_pct": trailing_return_pct(series, 365 * 3, 900),
        "max_drawdown_pct": None,
        "sharpe_ratio": None,
        "calmar_ratio": None,
        "var95_pct": None,
        "cvar95_pct": None,
        "downside_volatility_pct": None,
        "return_window": "",
        "risk_window": "",
    }

    one_year_target = last_date - pd.Timedelta(days=365)
    eligible = series[series.index <= one_year_target]
    first_date = eligible.index[-1] if not eligible.empty else series.index[0]
    if (last_date - first_date).days >= 330:
        first = float(eligible.iloc[-1] if not eligible.empty else series.iloc[0])
        last = float(series.iloc[-1])
        annualized = annualized_return(first, last, first_date.date(), last_date.date())
        result["annualized_return_pct"] = None if annualized is None else annualized * 100
        result["return_window"] = f"{first_date.date()} to {last_date.date()}"

    if len(returns) < 60:
        return result

    mean_daily = float(returns.mean())
    volatility = float(returns.std(ddof=1)) * math.sqrt(TRADING_DAYS_PER_YEAR)
    downside = returns.clip(upper=0)
    downside_deviation = float((downside.pow(2).mean()) ** 0.5) * math.sqrt(TRADING_DAYS_PER_YEAR)
    annualized_mean = mean_daily * TRADING_DAYS_PER_YEAR
    drawdown = risk_prices.div(risk_prices.cummax()).sub(1.0)
    max_drawdown = float(drawdown.min())
    var95 = float(returns.quantile(0.05))
    tail = returns[returns <= var95]

    result.update(
        {
            "sortino_ratio": None if downside_deviation <= 0 else annualized_mean / downside_deviation,
            "volatility_pct": volatility * 100,
            "max_drawdown_pct": max_drawdown * 100,
            "sharpe_ratio": None if volatility <= 0 else annualized_mean / volatility,
            "calmar_ratio": None
            if result["annualized_return_pct"] is None or max_drawdown >= 0
            else (result["annualized_return_pct"] / 100) / abs(max_drawdown),
            "var95_pct": var95 * 100,
            "cvar95_pct": None if tail.empty else float(tail.mean()) * 100,
            "downside_volatility_pct": downside_deviation * 100,
            "risk_window": f"{risk_prices.index[0].date()} to {risk_prices.index[-1].date()}",
        }
    )
    return result


def combine_price_series(series_map: dict[str, pd.Series], weights: dict[str, float]) -> pd.Series:
    requested = [code for code, weight in weights.items() if weight > 0]
    if not requested or any(code not in series_map for code in requested):
        return pd.Series(dtype=float)
    cleaned = {code: normalize_price_series(series_map[code]) for code in requested}
    if any(series.empty for series in cleaned.values()):
        return pd.Series(dtype=float)
    common_end = min(series.index[-1] for series in cleaned.values())
    prices = pd.concat(
        cleaned,
        axis=1,
        sort=True,
    ).sort_index().loc[:common_end].ffill().dropna()
    if prices.empty:
        return pd.Series(dtype=float)
    total = sum(weights[code] for code in requested)
    if total <= 0:
        return pd.Series(dtype=float)
    normalized = prices[requested].div(prices[requested].iloc[0])
    return sum(normalized[code] * weights[code] / total for code in requested)
