"""
BudgetBy — Amazon Scraper
"""
import re
from curl_cffi import CurlOpt
from curl_cffi.requests import AsyncSession
try:
    from selectolax.lexbor import LexborHTMLParser as HTMLParser
except ImportError:
    from selectolax.parser import HTMLParser
from budgetby.scrapers.base import BaseScraper
from budgetby.scrapers.utils import get_random_ua, extract_price, clean_title
from budgetby import config

AMAZON_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

class AmazonScraper(BaseScraper):
    async def _do_scrape_product(self, url: str) -> dict:
        asin_match = re.search(r'/dp/([A-Z0-9]{10})', url)
        if not asin_match:
            asin_match = re.search(r'/product/([A-Z0-9]{10})', url)
            
        affiliate_url = f"{url}?tag={config.AMAZON_ASSOCIATE_TAG}"
        
        async with AsyncSession(impersonate="chrome120", curl_options={CurlOpt.IPRESOLVE: 1}, headers=AMAZON_HEADERS, timeout=config.SCRAPER_TIMEOUT) as s:
            r = await s.get(url)
            
        tree = HTMLParser(r.text)
        
        title_node = tree.css_first("#productTitle")
        title = clean_title(title_node.text()) if title_node else ""
        
        # Price
        price = 0.0
        for sel in ["#priceblock_dealprice", "#priceblock_ourprice", ".a-price .a-offscreen", "span.a-price-whole"]:
            node = tree.css_first(sel)
            if node:
                price = extract_price(node.text())
                if price > 0:
                    break
                    
        # MRP (Strike-through list price)
        mrp = 0.0
        for sel in [
            "span.basisPrice span.a-offscreen",
            "div#corePriceDisplay_desktop_feature_div span.a-text-price span.a-offscreen",
            "span.a-price.a-text-price span.a-offscreen",
            "span[data-a-strike='true']",
            "span.a-text-strike",
            "td.a-span12.a-color-secondary.a-size-base span.a-price.a-text-price span.a-offscreen",
            ".priceBlockStrikePriceString",
            "#priceblock_saleprice_lbl + span"
        ]:
            node = tree.css_first(sel)
            if node:
                mrp_cand = extract_price(node.text())
                if mrp_cand > (price or 0):
                    if price and (mrp_cand > 15.0 * price or mrp_cand > 500000):
                        mrp_cand = price
                    mrp = mrp_cand
                    break

        if not mrp or mrp < (price or 0):
            mrp = price
        elif price and (mrp > 15.0 * price or mrp > 500000):
            mrp = price
            
        # Rating & Review Count (Strictly inside true product review container)
        rating = 0.0
        review_count = 0
        
        acr = tree.css_first("#averageCustomerReviews, #acrPopover, #acrCustomerReviewLink")
        if acr:
            r_node = acr.css_first("span.a-icon-alt, i.a-icon-star span.a-icon-alt, span.a-size-base.a-color-base")
            if r_node:
                r_match = re.search(r'([\d.]+)', r_node.text(strip=True))
                if r_match:
                    try:
                        rating = float(r_match.group(1))
                    except Exception:
                        pass
            rc_node = acr.css_first("#acrCustomerReviewText, span[data-hook='total-review-count']")
            if rc_node:
                rc_match = re.search(r'([\d,]+)', rc_node.text(strip=True))
                if rc_match:
                    try:
                        review_count = int(rc_match.group(1).replace(',', ''))
                    except Exception:
                        pass
        else:
            # Check bottom review summary
            r_bottom = tree.css_first("span[data-hook='rating-out-of-text']")
            if r_bottom:
                r_match = re.search(r'([\d.]+)', r_bottom.text(strip=True))
                if r_match:
                    try:
                        rating = float(r_match.group(1))
                    except Exception:
                        pass
            
        # Image
        img_node = tree.css_first("#landingImage") or tree.css_first("#imgBlkFront")
        image_url = img_node.attributes.get("src") if img_node else ""
        
        # In stock: Check availability block, outOfStock block, and price validity
        in_stock = True
        avail_node = tree.css_first("#availability, #outOfStock, #buybox, #deliveryBlockMessage")
        avail_text = avail_node.text().lower() if avail_node else ""
        if any(msg in avail_text for msg in [
            "currently unavailable",
            "we don't know when or if this item will be back in stock",
            "temporarily out of stock",
            "out of stock",
            "item is unavailable"
        ]):
            in_stock = False
        elif tree.css_first("#outOfStock"):
            in_stock = False
        elif price <= 0:
            in_stock = False
        
        # Renewed
        is_renewed = "Renewed" in title or "Refurbished" in title or "renewed" in r.text.lower()
        
        return {
            "title": title,
            "current_price": price,
            "mrp": mrp,
            "rating": rating,
            "review_count": review_count,
            "in_stock": in_stock,
            "image_url": image_url,
            "brand": "",
            "category": "",
            "is_renewed": is_renewed,
            "affiliate_url": affiliate_url
        }

    async def _do_scrape_listing(self, url: str) -> list[dict]:
        async with AsyncSession(impersonate="chrome", headers={"User-Agent": get_random_ua()}) as s:
            r = await s.get(url, timeout=config.SCRAPER_TIMEOUT)
            
        tree = HTMLParser(r.text)
        products = []
        seen = set()
        
        for a in tree.css("a"):
            href = a.attributes.get("href", "")
            if not href:
                continue
            match = re.search(r'/dp/([A-Z0-9]{10})', href)
            if match:
                asin = match.group(1)
                if asin not in seen:
                    seen.add(asin)
                    p_url = f"https://www.amazon.in/dp/{asin}"
                    title = clean_title(a.text())
                    products.append({
                        "platform_id": asin,
                        "product_url": p_url,
                        "title": title
                    })
        return products
