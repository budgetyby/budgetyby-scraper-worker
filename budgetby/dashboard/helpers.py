"""
BudgetBy — Dashboard Search & URL Resolution Helpers
"""
import re
from urllib.parse import urlparse
from budgetby import config
from budgetby.utils import is_monetized_affiliate_url

KNOWN_PLATFORMS = {'amazon', 'flipkart', 'myntra', 'ajio', 'nykaa'}
ACCESSORY_WORDS = {
    'case', 'cover', 'glass', 'strap', 'cable', 'charger', 'adapter',
    'sleeve', 'bag', 'backpack', 'pouch', 'guard', 'protector', 'skin',
    'stand', 'mount', 'holder', 'cleaner', 'cooling pad', 'mouse pad'
}

def parse_search_query(raw_query: str) -> dict:
    """
    Parses natural language search queries:
    - Extracts platform names (e.g. 'puma shoes amazon' -> platform='amazon', keywords=['puma', 'shoes'])
    - Extracts price constraints (e.g. 'under 500', 'below 1000', '500 to 1000', 'above 1500')
    - Cleans noise words and extracts search tokens
    - Detects if user specifically intends to search for accessories
    """
    query = raw_query.strip().lower()
    target_platform = None
    max_price = None
    min_price = None

    # Normalize t-shirt variants
    query = re.sub(r'\bt[\s\-]+shirt\b', 'tshirt', query)

    # 1. Platform extraction
    for plat in KNOWN_PLATFORMS:
        pattern = rf'\b(?:on|from|in|at)?\s*{plat}\b'
        if re.search(pattern, query):
            target_platform = plat
            query = re.sub(pattern, ' ', query)
            break

    # 2. Price range: "500 to 1000", "between 500 and 1000"
    range_match = re.search(r'\b(?:between\s+)?(\d+)\s*(?:to|-|and)\s*(\d+)\b', query)
    if range_match:
        val1 = float(range_match.group(1))
        val2 = float(range_match.group(2))
        min_price = min(val1, val2)
        max_price = max(val1, val2)
        query = re.sub(r'\b(?:between\s+)?(\d+)\s*(?:to|-|and)\s*(\d+)\b', ' ', query)
    else:
        under_match = re.search(r'\b(?:under|below|less than|within|<=|<)\s*(?:rs\.?|inr|₹)?\s*(\d+)\b', query)
        if under_match:
            max_price = float(under_match.group(1))
            query = re.sub(r'\b(?:under|below|less than|within|<=|<)\s*(?:rs\.?|inr|₹)?\s*(\d+)\b', ' ', query)

        above_match = re.search(r'\b(?:above|over|more than|>=|>)\s*(?:rs\.?|inr|₹)?\s*(\d+)\b', query)
        if above_match:
            min_price = float(above_match.group(1))
            query = re.sub(r'\b(?:above|over|more than|>=|>)\s*(?:rs\.?|inr|₹)?\s*(\d+)\b', ' ', query)

    # 3. Clean tokens
    words = re.findall(r'\b[a-z0-9]{2,}\b', query)
    stop_words = {'for', 'with', 'and', 'the', 'best', 'good', 'cheap', 'buy', 'online', 'in', 'on', 'from', 'at', 'to', 'of', 'a', 'an', 'deal', 'deals', 'offer', 'offers', 'all', 'latest'}
    meaningful = [w for w in words if w not in stop_words]
    tokens = meaningful if meaningful else words

    is_accessory_query = any(w in raw_query.lower() for w in ACCESSORY_WORDS)

    return {
        'platform': target_platform,
        'max_price': max_price,
        'min_price': min_price,
        'tokens': tokens,
        'clean_query': " ".join(tokens),
        'is_accessory_query': is_accessory_query
    }

def build_token_regex_pattern(token: str, for_postgres: bool = True) -> str:
    """
    Builds a POSIX regular expression pattern with word boundaries and plural/possessive support.
    e.g.:
      'hat' -> r'\yhat(s|es|\'?s)?\y'
      'led' -> r'\yled(s|es|\'?s)?\y'
      'tshirt' -> r'\y(t[\s\-]?shirts?|tees?)\y'
      'lower' -> r'\ylower(s|es|\'?s)?\y'
    """
    t = token.strip().lower()
    if not t:
        return ""
    b = r"\y" if for_postgres else r"\b"

    # Common e-commerce apparel & gadget synonyms
    synonyms = {
        "tshirt": f"{b}(t[\\s\\-]?shirts?|tees?){b}",
        "tshirts": f"{b}(t[\\s\\-]?shirts?|tees?){b}",
        "t-shirt": f"{b}(t[\\s\\-]?shirts?|tees?){b}",
        "tee": f"{b}(t[\\s\\-]?shirts?|tees?){b}",
        "earbud": f"{b}(earbuds?|tws|airbuds?|airdopes?){b}",
        "earbuds": f"{b}(earbuds?|tws|airbuds?|airdopes?){b}",
        "tws": f"{b}(earbuds?|tws|airbuds?|airdopes?){b}",
        "powerbank": f"{b}(power[\\s\\-]?banks?){b}",
        "smartwatch": f"{b}(smart[\\s\\-]?watch(es)?){b}",
        "men": f"{b}(men|man)(\'?s)?{b}",
        "mens": f"{b}(men|man)(\'?s)?{b}",
        "women": f"{b}(women|woman)(\'?s)?{b}",
        "womens": f"{b}(women|woman)(\'?s)?{b}",
    }
    if t in synonyms:
        return synonyms[t]

    # Handle plural roots (e.g. 'hats' -> 'hat', 'lowers' -> 'lower', 'boxes' -> 'box')
    if len(t) > 3 and t.endswith("ies"):
        base = t[:-3] + "y"
        esc = re.escape(base)
        return f"{b}({esc}|{re.escape(t)}){b}"
    elif len(t) > 4 and t.endswith("es") and not t.endswith("sses"):
        base = t[:-2]
        esc = re.escape(base)
        return f"{b}{esc}(es|s|'?s)?{b}"
    elif len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        base = t[:-1]
        esc = re.escape(base)
        return f"{b}{esc}(s|es|'?s)?{b}"
    else:
        esc = re.escape(t)
        return f"{b}{esc}(s|es|'?s)?{b}"

ALLOWED_REDIRECT_DOMAINS = (
    "amazon.in", "amazon.com", "amzn.to", "amzn.in",
    "flipkart.com", "dl.flipkart.com", "fktr.in",
    "myntra.com", "myntr.it",
    "ajio.com", "ajiio.in",
    "nykaa.com", "nykaa.ly", "clnk.in", "ekaro.in"
)

def is_safe_redirect_url(url: str) -> bool:
    """Validates that destination URL belongs strictly to recognized merchant or affiliate domains."""
    if not url or not isinstance(url, str) or not url.startswith("http"):
        return False
    try:
        host = urlparse(url).netloc.lower().split(":")[0]
        if not host:
            return False
        return any(host == d or host.endswith("." + d) for d in ALLOWED_REDIRECT_DOMAINS)
    except Exception:
        return False

def resolve_deal_button_url(
    platform: str, 
    tg_raw_url: str | None, 
    affiliate_url: str | None, 
    product_url: str | None, 
    product_id: int | None = None
) -> str:
    """
    Guarantees all consumer storefront deals route shoppers through verified affiliate links:
    1. Amazon: Always attach the official associate tag.
    2. Non-Amazon (Flipkart, Myntra, Ajio, Nykaa):
       - If already converted to EarnKaro or Cuelinks, return that link.
       - If not yet converted and product_id is available, route through /api/deal/redirect/{product_id}.
       - Fallback: clean direct merchant URL.
    """
    plat = (platform or "").strip().lower()

    # 1. Amazon: Always use official associate tag
    if plat == "amazon":
        tag = getattr(config, "AMAZON_ASSOCIATE_TAG", "dealpulse21-21")
        target = product_url or affiliate_url or tg_raw_url or ""
        m = re.search(r'/(?:dp|gp/product|product)/([A-Z0-9]{10})', target)
        if m:
            return f"https://www.amazon.in/dp/{m.group(1)}?tag={tag}"
        if target:
            clean = re.sub(r'([?&])tag=[^&]*', '', target)
            sep = "&" if "?" in clean else "?"
            return f"{clean}{sep}tag={tag}"
        return target

    # 2. Non-Amazon: Check if already an EarnKaro or Cuelinks tracking shortlink
    for candidate in (affiliate_url, tg_raw_url):
        if candidate and candidate.startswith("http") and not any(bad in candidate for bad in ("affgrowth", "affExtParam")):
            if is_monetized_affiliate_url(candidate):
                return candidate

    # 3. If product_id is known, route through redirect endpoint which converts live
    if product_id:
        return f"/api/deal/redirect/{product_id}"

    # 4. Fallback: Clean direct merchant product_url without synthetic broken params
    target = product_url or affiliate_url or ""
    clean = target.split("&affid=")[0].split("?affid=")[0].split("&affExtParam")[0].split("?affExtParam")[0]
    return clean
