import json
import logging
import asyncio
from typing import List, Dict, Any
from curl_cffi.requests import AsyncSession
from budgetby.affiliate.earnkaro_links import build_earnkaro_url_sync

logger = logging.getLogger("budgetby.discovery.ajio")

async def discover_category(category_code: str, pages: int = 2) -> List[Dict[str, Any]]:
    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    async with AsyncSession(impersonate="chrome", headers=headers, timeout=25) as session:
        for page in range(0, pages):
            url = f"https://www.ajio.com/c/{category_code}?curpage={page}"
            try:
                await asyncio.sleep(0.5)
                response = await session.get(url)
                if response.status_code != 200:
                    continue

                text = response.text
                products = []

                # Strategy 1: Parse from window.__PRELOADED_STATE__
                idx = text.find("window.__PRELOADED_STATE__ = ")
                if idx != -1:
                    end = text.find("</script>", idx)
                    raw = text[idx + len("window.__PRELOADED_STATE__ = "):end].strip()
                    if raw.endswith(";"):
                        raw = raw[:-1]
                    try:
                        data = json.loads(raw)
                        entities = data.get("grid", {}).get("entities", {})
                        for code, p in entities.items():
                            name = p.get("name", "")
                            brand = p.get("brandName", "")
                            title = f"{brand} {name}".strip() if brand and not name.startswith(brand) else name
                            price = float(p.get("price", {}).get("value", 0)) if p.get("price") else None
                            mrp = float(p.get("wasPriceData", {}).get("value", price)) if p.get("wasPriceData") else price
                            
                            product_url = f"https://www.ajio.com/p/{code}"
                            aff_url = build_earnkaro_url_sync(product_url)
                            images = p.get("images", [])
                            image_url = images[0].get("url", "") if images else ""

                            if not title or not price or price <= 0:
                                continue

                            r_val = float(p.get("averageRating") or p.get("rating") or 0.0)
                            rc_val = int(p.get("ratingsCount") or p.get("reviewCount") or 0)

                            products.append({
                                "platform": "ajio",
                                "platform_id": str(code),
                                "product_url": product_url,
                                "affiliate_url": aff_url,
                                "title": title,
                                "brand": brand,
                                "image_url": image_url,
                                "current_price": price,
                                "mrp": mrp or price,
                                "rating": r_val,
                                "review_count": rc_val,
                            })
                    except Exception as e:
                        logger.error(f"Error parsing Ajio preloaded state: {e}")

                # Strategy 2: If JSON response was returned directly
                if not products and text.startswith("{"):
                    try:
                        data = response.json()
                        raw_prods = data.get("products", []) or data.get("data", {}).get("products", [])
                        for p in raw_prods:
                            code = str(p.get("code", ""))
                            if not code:
                                continue
                            name = p.get("name", "")
                            brand = p.get("fnlColorVariantData", {}).get("brandName", "")
                            title = f"{brand} {name}".strip() if brand and not name.startswith(brand) else name
                            price = float(p.get("price", {}).get("value", 0)) if p.get("price") else None
                            mrp = float(p.get("wasPriceData", {}).get("value", price)) if p.get("wasPriceData") else price
                            
                            product_url = f"https://www.ajio.com/p/{code}"
                            aff_url = build_earnkaro_url_sync(product_url)
                            images = p.get("images", [])
                            image_url = images[0].get("url", "") if images else ""

                            r_val = float(p.get("averageRating") or p.get("rating") or 0.0)
                            rc_val = int(p.get("ratingsCount") or p.get("reviewCount") or 0)

                            products.append({
                                "platform": "ajio",
                                "platform_id": code,
                                "product_url": product_url,
                                "affiliate_url": aff_url,
                                "title": title,
                                "brand": brand,
                                "image_url": image_url,
                                "current_price": price,
                                "mrp": mrp or price,
                                "rating": r_val,
                                "review_count": rc_val,
                            })
                    except Exception as e:
                        logger.error(f"Error parsing Ajio JSON response: {e}")

                results.extend(products)
            except Exception as e:
                logger.error(f"Error scraping Ajio category {category_code} page {page}: {e}")
    return results

async def discover_deals_page(pages: int = 2) -> List[Dict[str, Any]]:
    """Crawls Ajio High-Discount Category Clearance Hubs."""
    results = []
    # 830216014=Men T-shirts, 830207=Footwear, 830303002=Kurtas, 830302=Westernwear, 830201=Men Clothing
    deal_codes = ["830216014", "830207", "830303002", "830302", "830201"]
    for code in deal_codes:
        try:
            items = await discover_category(code, pages=pages)
            for it in items:
                it["deal_type"] = "today_deal"
                it["badge"] = "TODAY_DEAL"
            results.extend(items)
        except Exception as e:
            logger.error(f"Error scraping Ajio deals {code}: {e}")
    return results


