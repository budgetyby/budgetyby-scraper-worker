"""
BudgetBy — Ajio Scraper
"""
import re
import logging
from curl_cffi.requests import AsyncSession
from budgetby.scrapers.base import BaseScraper
from budgetby.scrapers.utils import clean_title
from budgetby.affiliate.earnkaro_links import build_earnkaro_url_sync
from budgetby import config

logger = logging.getLogger("budgetby.scrapers.ajio")

class AjioScraper(BaseScraper):
    async def _do_scrape_product(self, url: str) -> dict:
        code_match = re.search(r'/p/([A-Za-z0-9_]+)', url)
        code = code_match.group(1) if code_match else ""

        title = ""
        brand = ""
        price = 0.0
        mrp = 0.0
        image_url = ""
        in_stock = True
        rating = 0.0
        review_count = 0

        try:
            try:
                from selectolax.lexbor import LexborHTMLParser as HTMLParser
            except ImportError:
                from selectolax.parser import HTMLParser
            import json

            async with AsyncSession(impersonate="chrome124", timeout=config.SCRAPER_TIMEOUT) as s:
                r = await s.get(url)

            if r.status_code == 200:
                tree = HTMLParser(r.text)

                # 1. Primary: Extract window.__PRELOADED_STATE__
                for script in tree.css("script"):
                    txt = script.text() or ""
                    if "window.__PRELOADED_STATE__" in txt:
                        idx = txt.find("window.__PRELOADED_STATE__")
                        json_start = txt.find("{", idx)
                        if json_start != -1:
                            try:
                                decoder = json.JSONDecoder()
                                data, _ = decoder.raw_decode(txt[json_start:])
                                pdp = data.get("product", {}).get("productDetails", {})
                                if pdp:
                                    title = clean_title(pdp.get("name", "") or pdp.get("baseOptions", [{}])[0].get("title", ""))
                                    brand = pdp.get("brandName", "") or pdp.get("brickSubject", "")
                                    
                                    price_val = pdp.get("price", {}).get("value") or pdp.get("offerPrice", {}).get("value")
                                    if price_val:
                                        price = float(price_val)
                                        
                                    mrp_val = pdp.get("wasPriceData", {}).get("value") or pdp.get("mrp", {}).get("value")
                                    if mrp_val:
                                        mrp = float(mrp_val)
                                        
                                    in_stock = pdp.get("stock", {}).get("stockLevelStatus", "inStock") == "inStock"
                                    images = pdp.get("images", [])
                                    if images:
                                        image_url = images[0].get("url", "")
                                    
                                    # Extract Genuine Live Customer Star Rating
                                    ratings_resp = pdp.get("ratingsResponse", {})
                                    agg_rating = ratings_resp.get("aggregateRating", {})
                                    if agg_rating.get("averageRating"):
                                        try:
                                            rating = float(agg_rating["averageRating"])
                                        except Exception:
                                            pass
                                    else:
                                        rating = 0.0

                                    if agg_rating.get("numUserRatings"):
                                        try:
                                            review_count = int(agg_rating["numUserRatings"])
                                        except Exception:
                                            pass
                                    else:
                                        review_count = 0
                                    break
                            except Exception:
                                pass

                # 2. Fallback: application/ld+json
                if not title or price <= 0:
                    for script in tree.css("script[type='application/ld+json']"):
                        txt = script.text() or ""
                        try:
                            data = json.loads(txt)
                            if isinstance(data, list):
                                data = data[0]
                            if data.get("@type") == "Product" or "offers" in data:
                                if not title:
                                    title = clean_title(data.get("name", ""))
                                offers = data.get("offers", {})
                                if isinstance(offers, dict) and price <= 0:
                                    p_val = offers.get("price")
                                    if p_val:
                                        price = float(p_val)
                                    avail = str(offers.get("availability", ""))
                                    if "OutOfStock" in avail:
                                        in_stock = False
                                if not image_url and data.get("image"):
                                    image_url = str(data.get("image"))
                                break
                        except Exception:
                            pass
        except Exception as e:
            logger.warning(f"Ajio scraping parsing warning for {url}: {e}")

        if not title or price <= 0:
            return None

        if not mrp or mrp < price:
            mrp = price
        elif price > 0 and (mrp > 15.0 * price or mrp > 500000):
            mrp = price

        affiliate_url = build_earnkaro_url_sync(url)

        return {
            "platform": "ajio",
            "platform_id": code or url,
            "title": f"{brand} {title}".strip() if brand and not title.startswith(brand) else title,
            "brand": brand,
            "current_price": price,
            "mrp": mrp,
            "product_url": url,
            "affiliate_url": affiliate_url,
            "image_url": image_url,
            "in_stock": in_stock,
            "rating": rating,
            "review_count": review_count,
        }

    async def _do_scrape_listing(self, url: str) -> list[dict]:
        from budgetby.discovery.ajio_discover import discover_category
        code_match = re.search(r'/c/(\d+)', url)
        code = code_match.group(1) if code_match else "830216014"
        return await discover_category(code, pages=1)
