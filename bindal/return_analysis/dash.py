"""Dashboard numbers of a return analysis: per portal (and Myntra combined / all portals) the sale
and return quantities, the return % by month, and the top return reasons, brands, categories
and states. Built from the same rows the reports are built from, so they always agree."""
from __future__ import annotations

import pandas as pd

TOP = 8


def _rate(sold, returned) -> float:
    return round(float(returned) / float(sold) * 100, 2) if sold else 0.0


def _labels(values: pd.Series, aliases: dict | None = None) -> pd.Series:
    """One spelling per value: the same name in other cases / aliases (FashFun, FASHFUN CLOTHING)
    shows as its most common spelling, as the combined reports do."""
    values = values.fillna("-").astype(str).str.strip()
    key = values.str.lower().replace(aliases or {})
    return key.map(values.groupby(key).agg(lambda s: s.mode()[0]))


def _split(df: pd.DataFrame, column: str, top: int = TOP, min_sold: int = 20) -> list[dict]:
    """Sale qty, return qty and % per value of `column`, biggest sellers first."""
    if column not in df or df.empty:
        return []
    from .engine import BRAND_ALIASES
    labels = _labels(df[column], BRAND_ALIASES if column == "BRAND" else None)
    g = df.assign(_ret=df["QTY"].where(df["ORDER STATUS"] == "Return", 0)) \
          .groupby(labels)[["QTY", "_ret"]].sum()
    g = g[g["QTY"] >= min_sold].sort_values("QTY", ascending=False).head(top)
    return [{"name": k, "sold": int(r.QTY), "returned": int(r._ret), "rate": _rate(r.QTY, r._ret)}
            for k, r in g.iterrows()]


def _reason(text) -> str:
    """AJIO / Flipkart reason codes read like Myntra's: Does_Not_Look_Good_On_Me -> Does not look
    good on me, FIT_NOT_AS_EXPECTED -> Fit not as expected."""
    t = str(text).strip() if pd.notna(text) else ""
    if not t or t.lower() in ("nan", "none", "-"):
        return "Not given"
    if "_" in t or t.isupper():
        t = t.replace("_", " ").strip().lower()
        t = t[:1].upper() + t[1:]
    return t


def _reasons(df: pd.DataFrame, top: int = TOP) -> list[dict]:
    ret = df[df["ORDER STATUS"] == "Return"]
    if ret.empty or "RETURN/EXCHANGE REASON" not in ret:
        return []
    g = ret.groupby(ret["RETURN/EXCHANGE REASON"].map(_reason))["QTY"].sum()
    total = int(g.sum())
    return [{"name": k[:60], "qty": int(v), "share": _rate(total, v)}
            for k, v in g.sort_values(ascending=False).head(top).items()]


def _by_month(df: pd.DataFrame) -> list[dict]:
    if "MONTH" not in df or df.empty:
        return []
    out = []
    for m, part in df.groupby("MONTH"):
        sold = int(part["QTY"].sum())
        returned = int(part.loc[part["ORDER STATUS"] == "Return", "QTY"].sum())
        try:
            key = pd.to_datetime(m, format="%m-%Y").strftime("%Y-%m")
        except (ValueError, TypeError):
            continue
        out.append({"month": key, "sold": sold, "returned": returned, "rate": _rate(sold, returned)})
    return sorted(out, key=lambda r: r["month"])


def portal_block(df: pd.DataFrame) -> dict:
    sold = int(df["QTY"].sum())
    returned = int(df.loc[df["ORDER STATUS"] == "Return", "QTY"].sum())
    return {"sold": sold, "returned": returned, "rate": _rate(sold, returned),
            "months": _by_month(df), "reasons": _reasons(df), "brands": _split(df, "BRAND"),
            "categories": _split(df, "CATEGORY"), "states": _split(df, "STATE")}


def details(frames: dict[str, pd.DataFrame]) -> dict[str, dict]:
    """Portal name -> block, plus MYNTRA COMBINED and ALL PORTAL when there is more than one."""
    frames = {k: v for k, v in frames.items() if v is not None and not v.empty}
    out = {}
    for name, df in frames.items():
        try:
            out[name] = portal_block(df)
        except Exception:  # noqa: BLE001 - one odd frame must not cost the others
            continue
    myntra = [df for k, df in frames.items() if k.startswith("MYNTRA")]
    if len(myntra) > 1:
        out["MYNTRA COMBINED"] = portal_block(pd.concat(myntra, ignore_index=True))
    if len(frames) > 1:
        out["ALL PORTAL"] = portal_block(pd.concat(frames.values(), ignore_index=True))
    return out
