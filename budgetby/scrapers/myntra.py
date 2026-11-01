"""
BudgetBy — Myntra Scraper
"""
import json
from curl_cffi.requests import AsyncSession
try:
    from selectolax.lexbor import LexborHTMLParser as HTMLParser
except ImportError:
    from selectolax.parser import HTMLParser
from budgetby.scrapers.base import BaseScraper
from budgetby.scrapers.utils import get_random_ua, clean_title
from budgetby import config

class MyntraScraper(BaseScraper):
    async def _do_scrape_product(self, url: str) -> dict:
        async with AsyncSession(impersonate="chrome", headers={"User-Agent": get_random_ua()}) as s:
            r = await s.get(url, timeout=config.SCRAPER_TIMEOUT)
            
        tree = HTMLParser(r.text)
        pdp_data = None
        
        for script in tree.css("script"):
            txt = script.text() or ""
            if "pdpData" in txt:
                idx = txt.find("window.__myx")
                if idx != -1:
                    json_start = txt.find("{", idx)
                    if json_start != -1:
                        try:
                            decoder = json.JSONDecoder()
                            data, _ = decoder.raw_decode(txt[json_start:])
                            pdp_data = data.get("pdpData")
                            if pdp_data is not None:
                                break
                        except Exception:
                            pass
                
        title = ""
        price = 0.0
        mrp = 0.0
        rating = 0.0
        review_count = 0
        image_url = ""
        in_stock = False
        
        if pdp_data:
            try:
                title = pdp_data.get("name", "")
                price = float(pdp_data.get("price", {}).get("discounted", 0) or 0)
                mrp = float(pdp_data.get("price", {}).get("mrp", 0) or price)
                if mrp < price:
                    mrp = price
                elif price > 0 and (mrp > 15.0 * price or mrp > 500000):
                    mrp = price
                
                media = pdp_data.get("media", {}).get("albums", [])
                if media and media[0].get("images"):
                    image_url = media[0]["images"][0].get("src", "")
                    
                rating = float(pdp_data.get("ratings", {}).get("averageRating", 0) or 0)
                review_count = int(pdp_data.get("ratings", {}).get("totalReviewsCount", 0) or 0)
                
                # Check Out-of-Stock flags
                is_oos = pdp_data.get("flags", {}).get("outOfStock", False)
                in_stock = (not is_oos) and (price > 0)
            except Exception:
                in_stock = False
        else:
            in_stock = False
                
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
            "is_renewed": False
        }

    async def _do_scrape_listing(self, url: str) -> list[dict]:
        async with AsyncSession(impersonate="chrome", headers={"User-Agent": get_random_ua()}) as s:
            r = await s.get(url, timeout=config.SCRAPER_TIMEOUT)
            
        tree = HTMLParser(r.text)
        products = []
        
        for script in tree.css("script"):
            txt = script.text() or ""
            if "searchData" in txt:
                idx = txt.find("window.__myx")
                if idx != -1:
                    json_start = txt.find("{", idx)
                    if json_start != -1:
                        try:
                            decoder = json.JSONDecoder()
                            data, _ = decoder.raw_decode(txt[json_start:])
                            items = data.get("searchData", {}).get("results", {}).get("products", [])
                            for item in items:
                                pid = str(item.get("productId", ""))
                                p_url = f"https://www.myntra.com/{item.get('landingPageUrl', '')}"
                                title = clean_title(item.get("productName", ""))
                                if pid:
                                    products.append({
                                        "platform_id": pid,
                                        "product_url": p_url,
                                        "title": title
                                    })
                        except Exception:
                            pass
        return products
