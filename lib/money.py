"""Render an amount_tiers row (min_usd/max_usd) as the bilingual band label
used throughout the kertas kerja views, e.g. "US$1k – < US$5k or
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
        usd = f"< US${_compact_usd(max_usd)}"
        idr = f"< {_rupiah(float(max_usd) * rate)}"
    elif max_usd is None:
        usd = f"≥ US${_compact_usd(min_usd)}"
        idr = f"≥ {_rupiah(min_idr)}"
    else:
        max_idr = float(max_usd) * rate
        usd = f"US${_compact_usd(min_usd)} – < US${_compact_usd(max_usd)}"
        idr = f"{_rupiah(min_idr)} – < {_rupiah(max_idr)}"

    return f"{usd} or {idr}"
