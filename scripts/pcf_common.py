"""Shared numeric helpers for ETF PCF look-through scripts."""

from __future__ import annotations

import math
from datetime import date
from typing import Any

import pandas as pd


TRADING_DAYS_PER_YEAR = 252
CALENDAR_DAYS_PER_YEAR = 365.25


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
    return temp.dropna().sort_values("date")

