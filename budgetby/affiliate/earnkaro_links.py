"""
EarnKaro affiliate link generator for Flipkart, Myntra, Ajio, and Nykaa.

How EarnKaro tracking works (reverse-engineered from real generated links):
  - EarnKaro's fktr.in/xxxxx short links simply redirect to the product URL
    with 3 appended parameters:
      1. affid=affgrowth                            (EarnKaro's master affiliate ID)
      2. affExtParam1=ENKR{YYYYMMDD}A{UNIQUE_ID}   (unique per-click tracking token)
      3. affExtParam2={YOUR_EARNKARO_USER_ID}       (your EarnKaro account ID = 5549565)

  - Confirmed real expanded link:
    https://www.flipkart.com/...?pid=XYZ
    &affid=affgrowth
    &affExtParam1=ENKR20260826A2145271280
    &affExtParam2=5549565

  - We replicate this exactly — a fresh unique ENKR token per call so every
    click is individually tracked by EarnKaro, same as their website/app does.
"""
import logging
import random
from datetime import datetime
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

logger = logging.getLogger("budgetby.affiliate.earnkaro")

EARNKARO_AFF_ID = "affgrowth"

# Params to strip before re-applying ours (avoids double-param duplication)
_STRIP_PARAMS = {"affid", "affExtParam1", "affExtParam2", "affTrackingKey",
                 "utm_source", "utm_medium", "utm_campaign"}


def _generate_enkr_token() -> str:
    """
    Generate a unique EarnKaro per-click tracking token.
    Format from real links: ENKR{YYYYMMDD}A{HHMMSS}{4-random-digits}
    Example: ENKR20260826A2145271280
    """
    now = datetime.now()
    date_str = now.strftime("%Y%m%d")
    time_str = now.strftime("%H%M%S")
    rand_suffix = str(random.randint(1000, 9999))
    return f"ENKR{date_str}A{time_str}{rand_suffix}"


def _strip_existing_affiliate_params(url: str) -> str:
    """Remove any pre-existing affiliate/tracking params before adding ours."""
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        for p in _STRIP_PARAMS:
            qs.pop(p, None)
        clean_query = urlencode({k: v[0] for k, v in qs.items()})
        return urlunparse(parsed._replace(query=clean_query))
    except Exception:
        return url


def strip_affiliate_params(url: str) -> str:
    """
    Returns clean merchant URL stripped of any third-party tracking or foreign affiliate parameters.
    Ensures URL is ready for live Telegram converter or direct catalog storage.
    """
    if not url:
        return ""
    try:
        return _strip_existing_affiliate_params(url)
    except Exception as e:
        logger.warning(f"URL clean failed for {url[:60]}: {e}")
        return url

def build_earnkaro_url_sync(url: str, user_id: str = None) -> str:
    """
    Constructs an EarnKaro tracked URL with official affgrowth and user tracking token.
    Works 100% cloud-native without requiring Telegram MTProto bots.
    """
    if not url:
        return ""
    clean = _strip_existing_affiliate_params(url)
    from budgetby import config
    uid = user_id or getattr(config, "EARNKARO_API_KEY", "") or "5549565"
    token = _generate_enkr_token()
    sep = "&" if "?" in clean else "?"
    return f"{clean}{sep}affid={EARNKARO_AFF_ID}&affExtParam1={token}&affExtParam2={uid}"

async def build_earnkaro_url(url: str, user_id: str = None) -> str:
    """Async wrapper for build_earnkaro_url_sync."""
    return build_earnkaro_url_sync(url, user_id=user_id)
