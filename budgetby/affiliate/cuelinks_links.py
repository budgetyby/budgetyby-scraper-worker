"""
Cuelinks affiliate link generator for Nykaa and other Cuelinks-supported platforms.
Ensures zero EarnKaro contamination and supports direct Cuelinks API conversion.
"""
import logging
import urllib.parse
from curl_cffi.requests import AsyncSession
from budgetby import config

logger = logging.getLogger("budgetby.affiliate.cuelinks")

# Parameters to strip to ensure a completely clean URL before conversion
_STRIP_PARAMS = {
    "affid", "affExtParam1", "affExtParam2", "affTrackingKey",
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid"
}

def clean_product_url(url: str) -> str:
    """Strip any unwanted third-party affiliate/tracking query parameters."""
    if not url:
        return ""
    try:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        for p in _STRIP_PARAMS:
            qs.pop(p, None)
        clean_query = urllib.parse.urlencode({k: v[0] for k, v in qs.items()})
        return urllib.parse.urlunparse(parsed._replace(query=clean_query))
    except Exception:
        return url

def build_cuelinks_url_sync(url: str) -> str:
    """
    Synchronous builder: returns the clean URL stripped of EarnKaro/tracking params.
    """
    clean_url = clean_product_url(url)
    return clean_url or url

async def build_cuelinks_url(url: str) -> str:
    """
    Asynchronously converts a raw product URL into a Cuelinks affiliate link.
    If CUELINKS_API_KEY is configured, it calls the Cuelinks API to fetch the short clnk.in link.
    Otherwise, returns the clean URL for the channel bot to convert.
    """
    clean_url = clean_product_url(url)
    if not clean_url:
        return ""

    api_key = getattr(config, "CUELINKS_API_KEY", "").strip()
    channel_id = getattr(config, "CUELINKS_CHANNEL_ID", "314807")

    if api_key:
        try:
            headers = {
                "Authorization": f"Token token={api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "url": clean_url,
                "channel_id": channel_id
            }
            async with AsyncSession(timeout=5) as session:
                resp = await session.post("https://www.cuelinks.com/api/v2/links.json", json=payload, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    aff_url = data.get("affiliate_url") or data.get("shortened_url")
                    if aff_url:
                        logger.info(f"Generated direct Cuelinks API link: {aff_url}")
                        return aff_url
        except Exception as e:
            logger.warning(f"Cuelinks API link generation failed: {e}")

    return clean_url
