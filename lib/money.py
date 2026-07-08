"""Render an amount_tiers row (min_usd/max_usd) as the bilingual band label
used throughout the workpaper views, e.g. "US$1k – < US$5k or
Rp.16,000,000 – < Rp.80,000,000", computed from each entity's own exchange
rate rather than stored as static text.
"""


def _compact_usd(n):
    n = float(n)
    if n != 0 and n % 1_000_000 == 0:
        return f"{int(n // 1_000_000)}mn"
    if n != 0 and n % 1_000 == 0:
        return f"{int(n // 1_000)}k"
    return f"{n:,.0f}"


def _rupiah(n):
    return f"Rp.{n:,.0f}"


def format_tier_label(min_usd, max_usd, exchange_rate_idr):
    min_usd = float(min_usd)
    rate = float(exchange_rate_idr)
    min_idr = min_usd * rate

    if min_usd == 0:
        usd = {"op": "<", "max": f"US${_compact_usd(max_usd)}"}
        idr = {"op": "<", "max": _rupiah(float(max_usd) * rate)}
    elif max_usd is None:
        usd = {"op": ">=", "min": f"US${_compact_usd(min_usd)}"}
        idr = {"op": ">=", "min": _rupiah(min_idr)}
    else:
        max_idr = float(max_usd) * rate
        usd = {"op": "range", "min": f"US${_compact_usd(min_usd)}", "max": f"US${_compact_usd(max_usd)}"}
        idr = {"op": "range", "min": _rupiah(min_idr), "max": _rupiah(max_idr)}

    return {"usd": usd, "idr": idr}
