"""
BudgetBy — Shared Core Utilities
Canonical implementations for Indian currency formatting, affiliate URL detection,
and cross-module data hygiene.
"""
from typing import Optional

def format_inr(val: float | int | str | None, prefix: str = "₹") -> str:
    """
    Format amount according to the Indian numbering system: ₹1,23,456
    Handles integers, floats, None, zero, and negative values cleanly.
    """
    if val is None or val == "":
        return f"{prefix}0"
    try:
        val_int = int(round(float(val)))
        s = str(abs(val_int))
        if len(s) <= 3:
            res = s
        else:
            last3 = s[-3:]
            rest = s[:-3]
            chunks = []
            while len(rest) > 2:
                chunks.insert(0, rest[-2:])
                rest = rest[:-2]
            if rest:
                chunks.insert(0, rest)
            res = ",".join(chunks) + "," + last3
        sign = "-" if val_int < 0 else ""
        return f"{sign}{prefix}{res}"
    except (ValueError, TypeError):
        return f"{prefix}{val}"


AFFILIATE_MONETIZED_DOMAINS = (
    "fktr.in",
    "myntr.it",
    "ajiio.in",
    "ekaro.in",
    "clnk.in"
)

def is_monetized_affiliate_url(url: Optional[str]) -> bool:
    """
    Returns True if the given URL is already monetized with an active
    affiliate tracking parameter or shortlink.
    """
    if not url:
        return False
    u_lower = url.lower()
    if any(dom in u_lower for dom in AFFILIATE_MONETIZED_DOMAINS):
        return True
    if "amazon.in" in u_lower and "tag=" in u_lower:
        return True
    return False

SQL_AFFILIATE_PRIORITY_ORDER = (
    "(CASE WHEN p.affiliate_url ILIKE '%fktr.in%' "
    "OR p.affiliate_url ILIKE '%myntr.it%' "
    "OR p.affiliate_url ILIKE '%ajiio.in%' "
    "OR p.affiliate_url ILIKE '%clnk.in%' "
    "OR LOWER(p.platform) = 'amazon' THEN 1 ELSE 0 END) DESC"
)
