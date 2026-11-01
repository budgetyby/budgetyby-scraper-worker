import asyncio
import logging
import os
import random
import re
from curl_cffi.requests import AsyncSession
from budgetby import config

logger = logging.getLogger("budgetby.scrapers.utils")

def get_proxy_config() -> dict | None:
    """Returns proxy dictionary from PROXY_URL or HTTP_PROXY environment variables if configured."""
    proxy = getattr(config, "PROXY_URL", "") or os.getenv("PROXY_URL", "") or os.getenv("HTTP_PROXY", "")
    if proxy:
        return {"http": proxy, "https": proxy}
    return None

async def fetch_with_retry(
    session: AsyncSession,
    url: str,
    headers: dict = None,
    max_attempts: int = 3,
    backoff: float = 2.0,
    timeout: float = 15.0
):
    """Executes an async HTTP GET request with exponential retry and backoff."""
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = await session.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                return resp
            elif resp.status_code in (429, 502, 503, 504):
                sleep_dur = backoff * (2 ** (attempt - 1))
                logger.debug(f"HTTP {resp.status_code} for {url[:50]}, retrying in {sleep_dur:.1f}s (attempt {attempt}/{max_attempts})")
                await asyncio.sleep(sleep_dur)
            else:
                return resp
        except Exception as e:
            last_err = e
            sleep_dur = backoff * (2 ** (attempt - 1))
            logger.debug(f"Network error {e} for {url[:50]}, retrying in {sleep_dur:.1f}s (attempt {attempt}/{max_attempts})")
            await asyncio.sleep(sleep_dur)
    if last_err:
        logger.debug(f"Fetch failed after {max_attempts} attempts for {url[:50]}: {last_err}")
    return None

def get_random_ua() -> str:
    """Picks a random User-Agent from config."""
    return random.choice(config.USER_AGENTS)

def extract_price(text: str) -> float:
    """Parses Indian price strings like '₹1,999.00' or '1,999' to float, avoiding concatenated digits."""
    if not text:
        return 0.0
    cleaned = text.replace('\xa0', ' ').replace(',', '').strip()
    match = re.search(r'(\d+(?:\.\d{1,2})?)', cleaned)
    if not match:
        return 0.0
    try:
        val = float(match.group(1))
        # Reject numbers > 20,00,000 (20 Lakhs) as they are tracking IDs/phone numbers/metadata
        if val > 2000000.0:
            return 0.0
        return val
    except ValueError:
        return 0.0

UTILITY_BLACKLIST_PATTERNS = [
    r'\blpg\b.*\b(cylinder|booking|gas)\b',
    r'\bcylinder\s+booking\b',
    r'\bgas\s+booking\b',
    r'\belectricity\s+bill\b',
    r'\bbill\s+payment\b',
    r'\b(mobile|dth|fastag|metro)\s+recharge\b',
    r'\bgoogle\s+play\s+recharge\b',
    r'\b(amazon\s+pay|flipkart)\s+(e-?gift|gift\s+card)\b',
    r'\be-?gift\s+(card|voucher)\b',
    r'\bsubscription\s+(plan|pack)\b',
    r'\bprepaid\s+recharge\b'
]

def is_blacklisted_utility_item(title: str, url: str = "") -> bool:
    """Returns True if the item is a utility bill payment, recharge, gift card, or booking service rather than a physical retail product."""
    if not title:
        return False
    t_lower = title.lower()
    for pat in UTILITY_BLACKLIST_PATTERNS:
        if re.search(pat, t_lower, re.IGNORECASE):
            return True
    if url and any(sub in url.lower() for sub in ["/billpay", "/recharge", "/gift-cards", "/lpg-booking"]):
        return True
    return False

def clean_title(text: str) -> str:
    """Sanitizes product titles, removing leaked prices, discounts, leading numbers, and trailing ratings/card junk."""
    if not text:
        return ""
    # Remove currency symbol and any attached price/discount noise (e.g. "Top₹292₹2,99990% off...")
    cleaned = re.split(r'[\u20b9₹]', text)[0]
    
    # Strip leading list numbers/bullets (e.g. "3. CANTABIL...", "1) Nike...", "10 - Puma...", "(1) Item...")
    cleaned = re.sub(r'^(?:\d+[\.\)\-:\s]+|\(\d+\)\s*)', '', cleaned)
    # Strip leading bracketed SKU/ASIN codes (e.g. "[B09XKZV7S8] Men's...", "[1202621] ...")
    cleaned = re.sub(r'^\[[A-Za-z0-9_\-]+\]\s*', '', cleaned)
    
    # Strip trailing rating and review text (e.g. "...4.4 3,338 Ratings & 224 Reviews", "...4.2 (1,200 Ratings)", "... 4.5 2,100 Reviews", "Black 4.6 (15,000)")
    cleaned = re.sub(r'(?:\.{2,}|…|\s+)\d(?:\.\d+)?\s*(?:[★*]|\([\d,\.kKMB\s\w]+\)).*$', '', cleaned)
    cleaned = re.sub(r'(?:\.{2,}|…|\s+)\d(?:\.\d+)?(?:\s*[\d,]+)?\s*(?:Ratings?\s*(?:&|and)?\s*[\d,]*\s*Reviews?|Ratings?|Reviews?).*$', '', cleaned, flags=re.IGNORECASE)
    
    # Remove discount percentages and attached stock/delivery text
    cleaned = re.sub(r'\b\d{1,2}%\s*off\b.*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\b(only\s+\w+\s+left|in\s+stock|out\s+of\s+stock|free\s+delivery|bank\s+offer|special\s+price)\b.*', '', cleaned, flags=re.IGNORECASE)
    
    # Add space between concatenated brand and title words (e.g. "COLLECTIONCasual" -> "COLLECTION Casual", "CollectionsCasual" -> "Collections Casual", preserving "iPhone")
    cleaned = re.sub(r'([a-z]{2,})([A-Z])', r'\1 \2', cleaned)
    cleaned = re.sub(r'([A-Z]{2,})([A-Z][a-z])', r'\1 \2', cleaned)

    # Clean up non-breaking spaces, trailing ellipsis, and collapse whitespace
    cleaned = cleaned.replace('\xa0', ' ')
    cleaned = re.sub(r'\.{2,}$', '', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return cleaned.strip(' -–—,:|.')

