"""
Myntra discovery engine.
"""
import logging
import json
import asyncio
import re
from typing import List, Dict, Any
from curl_cffi.requests import AsyncSession
from budgetby import config
from budgetby.affiliate.earnkaro_links import build_earnkaro_url_sync

logger = logging.getLogger("budgetby.discovery.myntra")

MYNTRA_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.myntra.com/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
}

async def discover_category(slug: str, pages: int = 5, sort: str = "popularity") -> List[Dict[str, Any]]:
    results = []
    async with AsyncSession(impersonate="chrome", headers=MYNTRA_HEADERS, timeout=config.SCRAPER_TIMEOUT) as session:
        for page in range(1, pages + 1):
            url = f"https://www.myntra.com/{slug}?p={page}&sort={sort}"
            try:
                await asyncio.sleep(1.0)
                response = await session.get(url)
                if response.status_code != 200:
                    logger.warning(f"Myntra {slug} page {page} returned status {response.status_code}")
                    continue

                text = response.text
                products = []

                # Strategy 1: Find window.__myx
                idx = text.find("window.__myx =")
                if idx == -1:
                    idx = text.find("window.__myx=")
                if idx == -1:
                    idx = text.find("window.__myx_data__ =")
                if idx == -1:
                    idx = text.find("window.__myx_data__=")

                if idx != -1:
                    try:
                        json_start = text.find("{", idx)
                        decoder = json.JSONDecoder()
                        data, _ = decoder.raw_decode(text[json_start:])
                        products = data.get("searchData", {}).get("results", {}).get("products", [])
                    except Exception as e:
                        logger.warning(f"Failed to raw_decode Myntra JSON on page {page}: {e}")

                # Strategy 2: Regex fallback
                if not products:
                    match = re.search(r'window\.__myx\s*=\s*({.*?});</script>', text, re.DOTALL)
                    if match:
                        try:
                            data = json.loads(match.group(1))
                            products = data.get("searchData", {}).get("results", {}).get("products", [])
                        except Exception:
                            pass

                if not products:
                    # No more products in this category, stop paging early
                    break

                for p in products:
                    style_id = str(p.get("productId", p.get("styleId", "")))
                    if not style_id:
                        continue
                    title = p.get("productName", "") or p.get("brand", "")
                    p_url = f"https://www.myntra.com/{p.get('landingPageUrl', style_id)}"
                    img_url = p.get("searchImage", "")
                    price = float(p.get("price", 0)) if p.get("price") else None
                    mrp = float(p.get("mrp", 0)) if p.get("mrp") else price
                    brand = p.get("brand", "")
                    rating = float(p.get("rating", 0)) if p.get("rating") else None
                    rc = int(p.get("ratingCount", 0)) if p.get("ratingCount") else 0

                    aff_url = build_earnkaro_url_sync(p_url)

                    results.append({
                        "platform": "myntra",
                        "platform_id": style_id,
                        "product_url": p_url,
                        "affiliate_url": aff_url,
                        "title": title,
                        "brand": brand,
                        "image_url": img_url,
                        "current_price": price,
                        "mrp": mrp,
                        "rating": rating,
                        "review_count": rc,
                    })
            except Exception as e:
                logger.error(f"Error scraping Myntra category {slug} page {page}: {e}")
    return results

async def discover_deals_page(pages: int = 2) -> List[Dict[str, Any]]:
    """Crawls Myntra Top Discount Categories sorted by highest discount."""
    results = []
    deal_categories = [
        "men-tshirts",
        "men-casual-shoes",
        "women-kurtas-kurtis-suits",
        "women-dresses",
        "smart-watches",
        "headphones",
    ]
    for slug in deal_categories:
        try:
            items = await discover_category(slug, pages=pages, sort="discount")
            for it in items:
                it["deal_type"] = "today_deal"
                it["badge"] = "TODAY_DEAL"
            results.extend(items)
        except Exception as e:
            logger.error(f"Error scraping Myntra deals {slug}: {e}")
    return results

