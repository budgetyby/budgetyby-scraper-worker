"""
Amazon Affiliate link builder and ASIN extractor.
"""
import re
from budgetby.config import AMAZON_ASSOCIATE_TAG

ASIN_REGEX = re.compile(r'/dp/([A-Z0-9]{10})')

def extract_asin(url: str) -> str | None:
    match = ASIN_REGEX.search(url)
    if match:
        return match.group(1)
    # also try /product/
    match2 = re.search(r'/product/([A-Z0-9]{10})', url)
    if match2:
        return match2.group(1)
    match3 = re.search(r'/gp/product/([A-Z0-9]{10})', url)
    if match3:
        return match3.group(1)
    return None

def build_affiliate_url(asin: str) -> str:
    return f"https://www.amazon.in/dp/{asin}?tag={AMAZON_ASSOCIATE_TAG}"
