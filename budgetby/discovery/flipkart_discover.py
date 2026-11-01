"""
Flipkart discovery engine.
"""
import logging
import re
import asyncio
from typing import List, Dict, Any
from curl_cffi import CurlOpt
from curl_cffi.requests import AsyncSession
from selectolax.parser import HTMLParser
from budgetby import config
from budgetby.affiliate.earnkaro_links import build_earnkaro_url_sync
from budgetby.scrapers.utils import extract_price, clean_title, fetch_with_retry

logger = logging.getLogger("budgetby.discovery.flipkart")

PID_REGEX = re.compile(r'pid=([A-Z0-9]+)')

async def discover_category(name: str, sid: str, pages: int = 5, sort: str = "popularity") -> List[Dict[str, Any]]:
    results = []
    async with AsyncSession(impersonate="chrome124", curl_options={CurlOpt.IPRESOLVE: 1}, timeout=config.SCRAPER_TIMEOUT) as session:
        for page in range(1, pages + 1):
            url = f"https://www.flipkart.com/{name}/pr?sid={sid}&sort={sort}&page={page}"
            try:
                await asyncio.sleep(0.3)  # Fast discovery: 0.3s per page
                response = await fetch_with_retry(session, url, timeout=config.SCRAPER_TIMEOUT)
                if not response or response.status_code != 200:
                    continue

                tree = HTMLParser(response.text)
                items = tree.css("div[data-id], a[href*='pid=']")

                # If primary URL returns 0 items on page 1, fallback to search URL
                if not items and page == 1:
                    query = name.replace('-', '+')
                    search_url = f"https://www.flipkart.com/search?q={query}&sort={sort}&page={page}"
                    s_resp = await fetch_with_retry(session, search_url, timeout=config.SCRAPER_TIMEOUT)
                    if s_resp and s_resp.status_code == 200:
                        tree = HTMLParser(s_resp.text)
                        items = tree.css("div[data-id], a[href*='pid=']")

                if not items:
                    # No more products in this category, stop paging early
                    break

                seen_pids = set()
                for item in items:
                    try:
                        pid = item.attributes.get("data-id")
                        href = item.attributes.get("href", "")
                        
                        if not pid and href:
                            match = PID_REGEX.search(href)
                            if match:
                                pid = match.group(1)
                        
                        if not pid or pid in seen_pids:
                            continue
                        seen_pids.add(pid)

                        if href and not href.startswith("http"):
                            product_url = "https://www.flipkart.com" + href
                        elif not href:
                            product_url = f"https://www.flipkart.com/product/p/itm?pid={pid}"
                        else:
                            product_url = href

                        # Extract title with proper brand + description separation
                        brand_node = item.css_first("div._2WkVRV, div.syl9yP")
                        brand = brand_node.text(strip=True) if brand_node else ""
                        
                        desc_node = item.css_first("a.WKTcLC, a.IRpwTa, div.KzDlHZ, div._4rR01T, a.s1Q9rs, a.wByIpH, a[title]")
                        desc = ""
                        if desc_node:
                            desc = desc_node.attributes.get("title") or desc_node.text(strip=True)
                            
                        if brand and desc and not desc.lower().startswith(brand.lower()):
                            title = f"{brand} {desc}"
                        elif desc:
                            title = desc
                        elif brand:
                            title = brand
                        else:
                            # Try img alt
                            img_node = item.css_first("img[alt]")
                            if img_node:
                                title = img_node.attributes.get("alt", "")
                            elif href:
                                slug = re.sub(r'https?://[^/]+/', '', product_url).split('/p/')[0].split('?')[0].replace('-', ' ').title()
                                if slug and len(slug) >= 5:
                                    title = slug

                        title = clean_title(title)

                        # Image
                        img_node = item.css_first("img")
                        image_url = img_node.attributes.get("src", "") if img_node else ""

                        # Price extraction (multi-selector + regex fallback)
                        price = None
                        price_node = item.css_first("div.Nx9bqj, div._30jeq3, div[class*='Nx9bqj'], div[class*='_30jeq3'], div.hl05eU")
                        if price_node:
                            price = extract_price(price_node.text())
                        
                        if not price:
                            raw_text = item.text()
                            price_matches = re.findall(r'[\u20b9₹]\s*([\d]{1,3}(?:,\d{2,3})*|\d+)', raw_text)
                            if price_matches:
                                try:
                                    price = float(price_matches[0].replace(',', ''))
                                except Exception:
                                    pass

                        # MRP extraction
                        mrp = price
                        mrp_node = item.css_first("div.yRaY8j, div._3I9_wc, div[class*='yRaY8j'], div[class*='_3I9_wc']")
                        if mrp_node:
                            mrp = extract_price(mrp_node.text())
                        elif price and price_matches and len(price_matches) > 1:
                            try:
                                mrp_cand = float(price_matches[1].replace(',', ''))
                                if mrp_cand >= price:
                                    mrp = mrp_cand
                            except Exception:
                                pass

                        if not mrp or mrp <= (price or 0):
                            mrp = price

                        # Rating extraction (e.g. 4.2 in div.XQDdHH or div._3LWZlK)
                        rating = None
                        rating_node = item.css_first("div.XQDdHH, div._3LWZlK, span._1lRcqv, div.hGSR34")
                        if rating_node:
                            r_match = re.search(r'([\d.]+)', rating_node.text())
                            if r_match:
                                try:
                                    rating = float(r_match.group(1))
                                except Exception:
                                    pass

                        # Review count extraction (e.g. (1,234) in span.WJhFly or span._2_R_DZ)
                        review_count = None
                        rc_node = item.css_first("span.WJhFly, span._2_R_DZ, span[class*='WJhFly']")
                        if rc_node:
                            rc_match = re.search(r'([\d,]+)', rc_node.text())
                            if rc_match:
                                try:
                                    review_count = int(rc_match.group(1).replace(',', ''))
                                except Exception:
                                    pass

                        if not title or len(title) < 5 or title.lower() in ('product', 'flipkart product'):
                            continue

                        aff_url = build_earnkaro_url_sync(product_url)

                        results.append({
                            "platform": "flipkart",
                            "platform_id": pid,
                            "product_url": product_url,
                            "affiliate_url": aff_url,
                            "title": title,
                            "image_url": image_url,
                            "current_price": price,
                            "mrp": mrp or price,
                            "rating": rating,
                            "review_count": review_count,
                        })
                    except Exception as e:
                        logger.error(f"Error parsing Flipkart product item: {e}")
            except Exception as e:
                logger.error(f"Error scraping Flipkart category {name} page {page}: {e}")
    return results

async def discover_offers_store(pages: int = 3) -> List[Dict[str, Any]]:
    """Crawls Flipkart Top Offers Store and Deals of the Day."""
    results = []
    hubs = [
        ("offers-store", "all"),
        ("search?q=deals+of+the+day", "all"),
        ("search?q=top+deals", "all"),
    ]
    for slug, cat in hubs:
        try:
            items = await discover_category(slug, cat, pages=pages)
            for it in items:
                it["deal_type"] = "today_deal"
                it["badge"] = "TODAY_DEAL"
            results.extend(items)
        except Exception as e:
            logger.error(f"Error scraping Flipkart offers hub {slug}: {e}")
    return results

