"""Enhanced post-look-through analytics for selected ETF PCF outputs."""

from __future__ import annotations

import json
import hashlib
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


C_ETF_CODE = "ETF代码"
C_ETF_NAME = "ETF名称"
C_ETF_WEIGHT = "ETF权重%"
C_MODE = "穿透模式"
C_MARKET = "底层市场"
C_STOCK_CODE = "股票代码"
C_STOCK_NAME = "股票名称"
C_ETF_INNER_WEIGHT = "ETF内权重%"
C_PORTFOLIO_WEIGHT = "组合穿透权重%"
C_STOCK_PE = "股票PE"
C_STOCK_PB = "股票PB"
C_STOCK_DY = "股票股息率%"
C_PERIOD = "持仓期"
C_SOURCE = "持仓来源"
C_VALUATION_SOURCE = "估值来源"
C_VALUATION_ERROR = "估值错误"
C_DIVIDEND_SOURCE = "股息率来源"


@dataclass(frozen=True)
class ConstraintConfig:
    max_stock_weight: float | None = None
    max_industry_weight: float | None = None
    min_dividend_yield: float | None = None
    max_pe: float | None = None
    max_pb: float | None = None
    max_drawdown: float | None = None
    target_a_weight: float | None = None
    target_hk_weight: float | None = None
    target_us_weight: float | None = None
    min_metric_coverage: float = 80.0


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
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


def safe_divide(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def normalize_detail(detail: pd.DataFrame) -> pd.DataFrame:
    df = detail.copy()
    for column in [C_PORTFOLIO_WEIGHT, C_ETF_INNER_WEIGHT, C_STOCK_PE, C_STOCK_PB, C_STOCK_DY, C_ETF_WEIGHT]:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    if C_STOCK_CODE in df.columns:
        df[C_STOCK_CODE] = df[C_STOCK_CODE].astype(str).str.replace(r"\.0$", "", regex=True)
    return df


def numeric_column(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series([math.nan] * len(df), index=df.index, dtype="float64")
    return pd.to_numeric(df[column], errors="coerce")


def classify_board(market: str, code: str) -> str:
    if market == "US":
        return "美股"
    code = re.sub(r"\D", "", str(code))
    if market == "HK":
        return "港股"
    if code.startswith(("688", "689")):
        return "科创板"
    if code.startswith(("300", "301")):
        return "创业板"
    if code.startswith(("8", "4")):
        return "北交所"
    if code.startswith(("000", "001", "002", "003", "6")):
        return "主板"
    return "其他"


THEME_RULES: list[tuple[str, str]] = [
    ("半导体", r"半导体|芯|微|晶|硅|存储|海光|寒武纪|中芯|澜起|中微|拓荆|佰维|江波龙|芯原|沪硅|韦尔|兆易"),
    ("AI/算力", r"AI|人工智能|算力|光模块|通信|新易盛|中际旭创|天孚通信|寒武纪|海光|工业富联|浪潮|服务器"),
    ("新能源", r"新能源|锂|电池|宁德|阳光|亿纬|隆基|通威|天合|光伏|储能|赣锋|天齐"),
    ("银行金融", r"银行|保险|证券|券商|信托|金融|招行|工行|建行|农行|中行|平安"),
    ("资源能源", r"煤|石油|油|矿|黄金|有色|铜|铝|能源|神华|中海油|紫金"),
    ("医药医疗", r"医药|医疗|生物|药|迈瑞|恒瑞|爱尔|片仔癀|药明"),
    ("消费", r"消费|食品|饮料|白酒|家电|美的|格力|贵州茅台|五粮液|伊利"),
    ("高端制造", r"制造|工业|机器人|自动化|汇川|三环|设备|材料|电气"),
]


def classify_theme(name: str) -> str:
    hits = [theme for theme, pattern in THEME_RULES if re.search(pattern, str(name), flags=re.I)]
    return ";".join(hits) if hits else "其他"


def rule_industry_from_theme(theme: str) -> str:
    if "半导体" in theme:
        return "电子"
    if "AI/算力" in theme:
        return "通信/计算机"
    if "新能源" in theme:
        return "电力设备"
    if "银行金融" in theme:
        return "银行/非银金融"
    if "资源能源" in theme:
        return "煤炭/石油石化/有色"
    if "医药医疗" in theme:
        return "医药生物"
    if "消费" in theme:
        return "食品饮料/家电"
    if "高端制造" in theme:
        return "机械设备/电力设备"
    return "未分类"


def add_classifications(detail: pd.DataFrame) -> pd.DataFrame:
    df = normalize_detail(detail)
    if df.empty:
        return df
    df["板块"] = [classify_board(str(market), str(code)) for market, code in zip(df[C_MARKET], df[C_STOCK_CODE])]
    df["主题"] = df[C_STOCK_NAME].astype(str).map(classify_theme)
    df["规则行业"] = df["主题"].map(rule_industry_from_theme)
    # These are explicit placeholders: official Shenwan/CITIC mappings are not
    # bundled. The rule industry is provided separately and marked as estimated.
    df["申万一级行业"] = df["规则行业"]
    df["中信一级行业"] = df["规则行业"]
    df["行业数据源"] = "规则估计(非官方申万/中信)"
    return df


def weighted_average(df: pd.DataFrame, value_col: str, weight_col: str = C_PORTFOLIO_WEIGHT, positive_only: bool = False) -> float | None:
    if df.empty or value_col not in df.columns:
        return None
    values = pd.to_numeric(df[value_col], errors="coerce")
    weights = pd.to_numeric(df[weight_col], errors="coerce").fillna(0)
    mask = values.notna()
    if positive_only:
        mask &= values > 0
    total_weight = weights[mask].sum()
    if total_weight <= 0:
        return None
    return float((values[mask] * weights[mask]).sum() / total_weight)


def weight_sum(df: pd.DataFrame, mask: pd.Series | None = None) -> float:
    if df.empty or C_PORTFOLIO_WEIGHT not in df.columns:
        return 0.0
    weights = pd.to_numeric(df[C_PORTFOLIO_WEIGHT], errors="coerce").fillna(0)
    return float(weights[mask].sum() if mask is not None else weights.sum())


def build_structure_analysis(detail: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = add_classifications(detail)
    if df.empty:
        stock_group = df
    else:
        stock_group = (
            df.groupby([C_MARKET, C_STOCK_CODE], as_index=False)
            .agg(
                **{
                    C_STOCK_NAME: (C_STOCK_NAME, "first"),
                    C_PORTFOLIO_WEIGHT: (C_PORTFOLIO_WEIGHT, "sum"),
                    "板块": ("板块", "first"),
                }
            )
            .sort_values(C_PORTFOLIO_WEIGHT, ascending=False)
        )
    total = weight_sum(df)
    stock_count = int(len(stock_group))
    effective_count = int((stock_group[C_PORTFOLIO_WEIGHT] > 0).sum()) if C_PORTFOLIO_WEIGHT in stock_group.columns else stock_count
    rows = [
        {"指标": "底层持仓数量", "数值": stock_count, "单位": "只", "说明": "穿透后股票行数量"},
        {"指标": "有效持仓数量", "数值": effective_count, "单位": "只", "说明": "权重大于 0 的股票数量"},
        {"指标": "股票权重合计", "数值": total, "单位": "%", "说明": "PCF 股票行权重合计，可能因现金/替代/四舍五入不等于 100"},
        {"指标": "现金及其他估算", "数值": 100 - total, "单位": "%", "说明": "100 - 股票权重合计；负数通常表示 PCF 替代现金/权重口径导致股票权重超过 100"},
        {"指标": "前5持仓集中度", "数值": float(stock_group.nlargest(5, C_PORTFOLIO_WEIGHT)[C_PORTFOLIO_WEIGHT].sum()) if not stock_group.empty else 0, "单位": "%", "说明": "先按股票代码合并，再按组合穿透权重排序"},
        {"指标": "前10持仓集中度", "数值": float(stock_group.nlargest(10, C_PORTFOLIO_WEIGHT)[C_PORTFOLIO_WEIGHT].sum()) if not stock_group.empty else 0, "单位": "%", "说明": "先按股票代码合并，再按组合穿透权重排序"},
        {"指标": "前20持仓集中度", "数值": float(stock_group.nlargest(20, C_PORTFOLIO_WEIGHT)[C_PORTFOLIO_WEIGHT].sum()) if not stock_group.empty else 0, "单位": "%", "说明": "先按股票代码合并，再按组合穿透权重排序"},
        {"指标": "单一股票最大权重", "数值": float(stock_group[C_PORTFOLIO_WEIGHT].max()) if not stock_group.empty else 0, "单位": "%", "说明": "组合穿透后按股票合并的最大个股权重"},
    ]
    exposure_rows: list[dict[str, Any]] = []
    for kind, column in [("市场", C_MARKET), ("板块", "板块")]:
        if column not in df.columns:
            continue
        for key, group in df.groupby(column, dropna=False):
            exposure_rows.append({"分类类型": kind, "分类": key, "权重%": weight_sum(group), "股票数": int(len(group))})
    if total < 100:
        exposure_rows.append({"分类类型": "市场", "分类": "现金及其他估算", "权重%": 100 - total, "股票数": 0})
    return pd.DataFrame(rows), pd.DataFrame(exposure_rows)


def build_industry_theme_exposure(detail: pd.DataFrame) -> pd.DataFrame:
    df = add_classifications(detail)
    rows: list[dict[str, Any]] = []
    for source_name, column in [
        ("申万一级行业", "申万一级行业"),
        ("中信一级行业", "中信一级行业"),
        ("规则行业", "规则行业"),
        ("主题", "主题"),
    ]:
        if column not in df.columns:
            continue
        exploded = df.assign(**{column: df[column].astype(str).str.split(";")}).explode(column)
        for key, group in exploded.groupby(column, dropna=False):
            rows.append(
                {
                    "分类体系": source_name,
                    "分类": key,
                    "权重%": weight_sum(group),
                    "股票数": int(group[C_STOCK_CODE].nunique()),
                    "PE": weighted_average(group, C_STOCK_PE, positive_only=True),
                    "PB": weighted_average(group, C_STOCK_PB, positive_only=True),
                    "股息率%": weighted_average(group, C_STOCK_DY),
                    "数据源": "规则估计，非官方申万/中信" if source_name in {"申万一级行业", "中信一级行业", "规则行业"} else "名称关键词规则",
                }
            )
    return pd.DataFrame(rows).sort_values(["分类体系", "权重%"], ascending=[True, False]).reset_index(drop=True)


def bucket_label(value: float | None, buckets: list[tuple[str, float | None, float | None]]) -> str:
    if value is None:
        return "缺失"
    for label, low, high in buckets:
        if low is not None and value < low:
            continue
        if high is not None and value >= high:
            continue
        return label
    return "其他"


def build_valuation_buckets(detail: pd.DataFrame) -> pd.DataFrame:
    df = normalize_detail(detail)
    specs = [
        ("PE", C_STOCK_PE, [("亏损/负PE", None, 0), ("0-20", 0, 20), ("20-50", 20, 50), ("50-100", 50, 100), ("100+", 100, None), ("缺失", None, None)]),
        ("PB", C_STOCK_PB, [("破净/负PB", None, 1), ("1-3", 1, 3), ("3-10", 3, 10), ("10+", 10, None), ("缺失", None, None)]),
        ("股息率", C_STOCK_DY, [("0", None, 0.000001), ("0-2%", 0.000001, 2), ("2-5%", 2, 5), ("5%+", 5, None), ("缺失", None, None)]),
    ]
    rows: list[dict[str, Any]] = []
    for metric, column, buckets in specs:
        if column not in df.columns:
            continue
        labels = [bucket_label(to_float(value), buckets) for value in df[column]]
        temp = df.assign(_bucket=labels)
        for bucket, group in temp.groupby("_bucket", dropna=False):
            rows.append({"指标": metric, "分层": bucket, "权重%": weight_sum(group), "股票数": int(len(group))})
    pe_values = numeric_column(df, C_STOCK_PE)
    high_pe = weight_sum(df, pe_values > 100) if C_STOCK_PE in df.columns else 0
    negative_pe = weight_sum(df, pe_values <= 0) if C_STOCK_PE in df.columns else 0
    rows.extend(
        [
            {"指标": "高估值股票权重", "分层": "PE>100", "权重%": high_pe, "股票数": ""},
            {"指标": "负PE/亏损权重", "分层": "PE<=0", "权重%": negative_pe, "股票数": ""},
        ]
    )
    return pd.DataFrame(rows)


def build_profit_quality(detail: pd.DataFrame) -> pd.DataFrame:
    df = normalize_detail(detail)
    pe = numeric_column(df, C_STOCK_PE)
    pb = numeric_column(df, C_STOCK_PB)
    implied_roe = pb / pe
    temp = df.copy()
    temp["隐含ROE%"] = implied_roe * 100
    return pd.DataFrame(
        [
            {"指标": "市值加权隐含ROE", "数值": weighted_average(temp, "隐含ROE%", positive_only=True), "单位": "%", "覆盖权重%": weight_sum(temp, implied_roe.notna() & (implied_roe > 0)), "数据源": "PB/PE 推导，非财报直接 ROE"},
        ]
    )


def build_dividend_quality(detail: pd.DataFrame) -> pd.DataFrame:
    df = normalize_detail(detail)
    dy = numeric_column(df, C_STOCK_DY)
    rows = [
        {"指标": "当前加权股息率", "数值": weighted_average(df, C_STOCK_DY), "单位": "%", "权重%": weight_sum(df, dy.notna()), "说明": "近 12 个月或源字段股息率"},
        {"指标": "不分红股票权重", "数值": weight_sum(df, dy.fillna(-1).eq(0)), "单位": "%", "权重%": weight_sum(df, dy.fillna(-1).eq(0)), "说明": "股息率等于 0"},
        {"指标": "高股息股票权重>3%", "数值": weight_sum(df, dy > 3), "单位": "%", "权重%": weight_sum(df, dy > 3), "说明": "股息率大于 3%"},
        {"指标": "高股息股票权重>5%", "数值": weight_sum(df, dy > 5), "单位": "%", "权重%": weight_sum(df, dy > 5), "说明": "股息率大于 5%"},
    ]
    return pd.DataFrame(rows)


def build_risk_analysis(metrics_summary: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if metrics_summary.empty:
        return pd.DataFrame(rows)
    for _, row in metrics_summary.iterrows():
        rows.append(
            {
                "对象": row.get(C_ETF_CODE, row.get("ETF代码", "")),
                "年化收益%": row.get("年化收益%"),
                "波动率%": row.get("波动率%"),
                "索提诺比率": row.get("索提诺比率"),
                "最大回撤%": row.get("最大回撤%"),
                "Sharpe Ratio": row.get("Sharpe Ratio"),
                "Calmar Ratio": row.get("Calmar Ratio"),
                "Beta": row.get("Beta"),
                "VaR95%": row.get("VaR95%"),
                "CVaR95%": row.get("CVaR95%"),
                "下行波动率%": row.get("下行波动率%"),
                "个股风险贡献": "未计算；需要底层个股历史收益协方差",
                "说明": row.get("收益错误", "") if "收益错误" in metrics_summary.columns else "",
            }
        )
    return pd.DataFrame(rows)


def build_overlap_analysis(detail: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = normalize_detail(detail)
    codes = sorted(df[C_ETF_CODE].astype(str).unique()) if C_ETF_CODE in df.columns and not df.empty else []
    pair_rows: list[dict[str, Any]] = []
    common_rows: list[dict[str, Any]] = []
    if len(codes) < 2:
        return (
            pd.DataFrame([{"ETF_A": codes[0] if codes else "", "ETF_B": "", "Jaccard相似度": None, "按权重重合度%": None, "说明": "少于两只 ETF，无法计算 ETF 间重合度"}]),
            pd.DataFrame(columns=["股票代码", "股票名称", "覆盖ETF数", "组合穿透权重%"]),
        )
    df["_identity"] = df[C_MARKET].astype(str) + "|" + df[C_STOCK_CODE].astype(str)
    by_etf = {}
    for code in codes:
        part = df[df[C_ETF_CODE].astype(str).eq(code)]
        by_etf[code] = part.groupby("_identity")[C_ETF_INNER_WEIGHT].sum().to_dict()
    for i, left in enumerate(codes):
        for right in codes[i + 1 :]:
            left_set, right_set = set(by_etf[left]), set(by_etf[right])
            union = left_set | right_set
            inter = left_set & right_set
            weighted = sum(min(to_float(by_etf[left].get(stock)) or 0, to_float(by_etf[right].get(stock)) or 0) for stock in inter)
            pair_rows.append({"ETF_A": left, "ETF_B": right, "Jaccard相似度": None if not union else len(inter) / len(union), "按权重重合度%": weighted, "共同持仓数": len(inter)})
    grouped = df.groupby([C_MARKET, C_STOCK_CODE], as_index=False).agg(
        **{
            C_STOCK_NAME: (C_STOCK_NAME, "first"),
            "覆盖ETF数": (C_ETF_CODE, "nunique"),
            C_PORTFOLIO_WEIGHT: (C_PORTFOLIO_WEIGHT, "sum"),
        }
    )
    common = grouped[grouped["覆盖ETF数"] > 1].sort_values(C_PORTFOLIO_WEIGHT, ascending=False)
    return pd.DataFrame(pair_rows), common.reset_index(drop=True)


def build_pcf_quality(detail: pd.DataFrame, etf_summary: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if etf_summary.empty:
        return pd.DataFrame(rows)
    for _, row in etf_summary.iterrows():
        etf = row.get(C_ETF_CODE, "")
        stock_weight = to_float(row.get("ETF内股票权重合计%")) or to_float(row.get("股票权重合计%"))
        residual = None if stock_weight is None else 100 - stock_weight
        rows.append(
            {
                "ETF代码": etf,
                "PCF日期/持仓期": row.get(C_PERIOD, ""),
                "PCF来源": row.get(C_SOURCE, ""),
                "股票权重合计%": stock_weight,
                "现金及其他估算%": residual,
                "PCF原始股票行数": row.get("PCF原始股票行数"),
                "有效权重行数": row.get("有效权重行数", row.get("股票数")),
                "未定价/缺失权重行数": row.get("未定价/缺失权重行数", 0),
                "异常权重提示": "股票权重偏离100超过5%" if residual is not None and abs(residual) > 5 else "",
                "异常成分行数": int(to_float(row.get("未定价/缺失权重行数")) or 0),
                "多XML候选文件对比": "SZSE parser 已按相关成分数量选择候选；候选明细未落表",
                "PCF与季报持仓对比": "未计算；需要另取基金季报持仓",
            }
        )
    return pd.DataFrame(rows)


def build_cross_validation(metrics_summary: pd.DataFrame, detail: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if metrics_summary.empty:
        return pd.DataFrame(rows)
    for _, row in metrics_summary.iterrows():
        pe_cov = to_float(row.get("PE覆盖权重%"))
        dy_cov = to_float(row.get("股息率覆盖权重%"))
        pb_cov = to_float(row.get("PB覆盖权重%"))
        score_values = [min(max(value, 0), 100) for value in [pe_cov, dy_cov, pb_cov] if value is not None]
        score = sum(score_values) / len(score_values) if score_values else None
        rows.append(
            {
                "对象": row.get(C_ETF_CODE, ""),
                "PE覆盖权重%": pe_cov,
                "PB覆盖权重%": pb_cov,
                "股息率覆盖权重%": dy_cov,
                "数据覆盖率评分": score,
                "PE/PB/股息率多源比对": "未启用；当前记录主数据源及错误字段",
                "ETF净值vs场内价格收益比对": "若收益来源错误或单源成功，则无法比对",
                "PCF持仓vs季报持仓比对": "未计算；需要季报持仓源",
                "异常差异报警": "覆盖率低于80%" if score is not None and score < 80 else "",
            }
        )
    return pd.DataFrame(rows)


def build_constraint_checks(
    detail: pd.DataFrame,
    metrics_summary: pd.DataFrame,
    industry_theme: pd.DataFrame,
    config: ConstraintConfig,
) -> pd.DataFrame:
    df = add_classifications(detail)
    stock_group = (
        df.groupby([C_MARKET, C_STOCK_CODE], as_index=False).agg(
            **{C_STOCK_NAME: (C_STOCK_NAME, "first"), C_PORTFOLIO_WEIGHT: (C_PORTFOLIO_WEIGHT, "sum")}
        )
        if not df.empty
        else pd.DataFrame(columns=[C_STOCK_CODE, C_STOCK_NAME, C_PORTFOLIO_WEIGHT])
    )
    portfolio_row = metrics_summary[metrics_summary.get(C_ETF_CODE, pd.Series(dtype=str)).astype(str).eq("PORTFOLIO")]
    if portfolio_row.empty and not metrics_summary.empty:
        portfolio_row = metrics_summary.head(1)
    row = portfolio_row.iloc[0] if not portfolio_row.empty else pd.Series(dtype=object)
    checks = []

    def add(
        name: str,
        actual: float | None,
        operator: str,
        threshold: float | None,
        unit: str = "%",
        coverage: float | None = None,
    ) -> None:
        if threshold is None:
            status = "未设置阈值"
        elif coverage is not None and coverage < config.min_metric_coverage:
            status = "数据不足"
        elif actual is None:
            status = "缺少数据"
        elif operator == "<=":
            status = "通过" if actual <= threshold else "不通过"
        elif operator == ">=":
            status = "通过" if actual >= threshold else "不通过"
        else:
            status = "未检查"
        checks.append({"约束": name, "实际值": actual, "条件": "" if threshold is None else f"{operator} {threshold}", "单位": unit, "覆盖权重%": coverage, "结果": status})

    add("单一股票权重上限", float(stock_group[C_PORTFOLIO_WEIGHT].max()) if not stock_group.empty else None, "<=", config.max_stock_weight)
    max_industry = None
    if not industry_theme.empty:
        industry_rows = industry_theme[industry_theme["分类体系"].eq("规则行业")]
        if not industry_rows.empty:
            max_industry = float(industry_rows["权重%"].max())
    add("单一行业权重上限", max_industry, "<=", config.max_industry_weight)
    add("最低股息率", to_float(row.get("股息率%")), ">=", config.min_dividend_yield, coverage=to_float(row.get("股息率覆盖权重%")))
    add("最高PE", to_float(row.get("PE")), "<=", config.max_pe, "倍", to_float(row.get("PE覆盖权重%")))
    add("最高PB", to_float(row.get("PB")), "<=", config.max_pb, "倍", to_float(row.get("PB覆盖权重%")))
    add("最大回撤限制", to_float(row.get("最大回撤%")), ">=", config.max_drawdown)
    market_weights = df.groupby(C_MARKET)[C_PORTFOLIO_WEIGHT].sum().to_dict() if not df.empty else {}
    add("A股比例约束", to_float(market_weights.get("A")), ">=", config.target_a_weight)
    add("港股比例约束", to_float(market_weights.get("HK")), ">=", config.target_hk_weight)
    add("美股比例约束", to_float(market_weights.get("US")), ">=", config.target_us_weight)
    checks.append({"约束": "自动筛选满足条件ETF组合", "实际值": None, "条件": "", "单位": "", "结果": "当前脚本对已选ETF做约束检查；自动筛选需要候选ETF池"})
    checks.append({"约束": "ETF组合再平衡建议", "实际值": None, "条件": "", "单位": "", "结果": "输出当前超限项；权重优化需启用候选池与目标函数"})
    return pd.DataFrame(checks)


def build_historical_tracking(detail: pd.DataFrame, cache_dir: Path, run_id: str, use_cache: bool) -> pd.DataFrame:
    portfolio_rows = (
        detail[[C_ETF_CODE, C_ETF_WEIGHT]].drop_duplicates().sort_values(C_ETF_CODE).astype(str).values.tolist()
        if not detail.empty and {C_ETF_CODE, C_ETF_WEIGHT}.issubset(detail.columns)
        else []
    )
    signature = hashlib.sha256(json.dumps(portfolio_rows, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
    snapshot_path = cache_dir / f"holdings_{signature}_{run_id}.csv"
    if use_cache:
        cache_dir.mkdir(parents=True, exist_ok=True)
        detail.to_csv(snapshot_path, index=False, encoding="utf-8-sig")
    rows = []
    files = sorted(cache_dir.glob(f"holdings_{signature}_*.csv")) if cache_dir.exists() else []
    rows.append({"指标": "缓存快照数量", "数值": len(files), "说明": "用于后续历史穿透/持仓变化/换手率估算"})
    rows.append({"指标": "按每日PCF生成历史底层持仓", "数值": "已缓存当前快照" if use_cache else "未启用缓存", "说明": str(snapshot_path) if use_cache else ""})
    if len(files) >= 2:
        prev = pd.read_csv(files[-2], encoding="utf-8-sig")
        curr = detail
        prev_map = prev.groupby([C_MARKET, C_STOCK_CODE])[C_PORTFOLIO_WEIGHT].sum()
        curr_map = curr.groupby([C_MARKET, C_STOCK_CODE])[C_PORTFOLIO_WEIGHT].sum()
        all_codes = sorted(set(prev_map.index) | set(curr_map.index))
        turnover = sum(abs((curr_map.get(code, 0) or 0) - (prev_map.get(code, 0) or 0)) for code in all_codes) / 2
        rows.append({"指标": "持仓变化/换手率估算", "数值": turnover, "说明": f"对比 {files[-2].name} 与 {files[-1].name}"})
    else:
        rows.append({"指标": "持仓变化/换手率估算", "数值": None, "说明": "需要至少两个缓存快照"})
    rows.append({"指标": "行业权重变化", "数值": None, "说明": "需要至少两个带行业分类的缓存快照"})
    rows.append({"指标": "估值变化趋势", "数值": None, "说明": "需要至少两个缓存快照"})
    return pd.DataFrame(rows)


def build_run_manifest(args: dict[str, Any], outputs: dict[str, pd.DataFrame], run_id: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "args": args,
        "outputs": {name: {"rows": int(len(df)), "columns": list(df.columns)} for name, df in outputs.items()},
        "notes": [
            "Industry fields are rule estimates unless an external official mapping is later supplied.",
            "Historical tracking is based on cached snapshots produced by this script.",
            "Some financial quality fields require future financial statement data-source integration.",
        ],
    }


def build_markdown_report(outputs: dict[str, pd.DataFrame]) -> str:
    lines = ["# ETF PCF Enhanced Analytics Report", ""]
    for name in ["structure_analysis", "metrics_summary", "industry_theme_exposure", "valuation_buckets", "constraint_checks"]:
        df = outputs.get(name)
        if df is None or df.empty:
            continue
        lines.extend([f"## {name}", "", df.head(20).to_markdown(index=False), ""])
    return "\n".join(lines)


def build_html_report(outputs: dict[str, pd.DataFrame]) -> str:
    sections = ["<html><head><meta charset='utf-8'><title>ETF PCF Enhanced Analytics</title></head><body>"]
    sections.append("<h1>ETF PCF Enhanced Analytics</h1>")
    for name, df in outputs.items():
        sections.append(f"<h2>{name}</h2>")
        sections.append(df.head(200).to_html(index=False) if not df.empty else "<p>No rows</p>")
    sections.append("</body></html>")
    return "\n".join(sections)


def build_enhanced_outputs(
    summary: pd.DataFrame,
    detail: pd.DataFrame,
    etf_summary: pd.DataFrame,
    metrics_summary: pd.DataFrame,
    *,
    args: dict[str, Any],
    out_dir: Path,
    constraints: ConstraintConfig,
    use_cache: bool = True,
) -> dict[str, pd.DataFrame]:
    classified_detail = add_classifications(detail)
    structure, market_board = build_structure_analysis(classified_detail)
    industry_theme = build_industry_theme_exposure(classified_detail)
    valuation_buckets = build_valuation_buckets(classified_detail)
    profit_quality = build_profit_quality(classified_detail)
    dividend_quality = build_dividend_quality(classified_detail)
    risk_analysis = build_risk_analysis(metrics_summary)
    overlap_pairs, common_holdings = build_overlap_analysis(classified_detail)
    pcf_quality = build_pcf_quality(classified_detail, etf_summary)
    cross_validation = build_cross_validation(metrics_summary, classified_detail)
    constraint_checks = build_constraint_checks(classified_detail, metrics_summary, industry_theme, constraints)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    historical_tracking = build_historical_tracking(classified_detail, out_dir / "cache", run_id, use_cache)
    outputs = {
        "classified_detail": classified_detail,
        "structure_analysis": structure,
        "market_board_exposure": market_board,
        "industry_theme_exposure": industry_theme,
        "valuation_buckets": valuation_buckets,
        "profit_quality": profit_quality,
        "dividend_quality": dividend_quality,
        "risk_analysis": risk_analysis,
        "overlap_pairs": overlap_pairs,
        "common_holdings": common_holdings,
        "pcf_quality": pcf_quality,
        "cross_validation": cross_validation,
        "constraint_checks": constraint_checks,
        "historical_tracking": historical_tracking,
    }
    manifest_outputs = {
        "summary": summary,
        "detail": detail,
        "etf_summary": etf_summary,
        "metrics_summary": metrics_summary,
        **outputs,
    }
    manifest = build_run_manifest(args, manifest_outputs, run_id)
    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return outputs
