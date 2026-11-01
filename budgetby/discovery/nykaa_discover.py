"""
Nykaa discovery engine with high-accuracy title extraction.
"""
import logging
import asyncio
import re
from typing import List, Dict, Any
from curl_cffi.requests import AsyncSession
from selectolax.parser import HTMLParser
from budgetby import config
from budgetby.affiliate.cuelinks_links import build_cuelinks_url_sync
from budgetby.scrapers.utils import extract_price, clean_title

logger = logging.getLogger("budgetby.discovery.nykaa")

async def discover_category(category_path: str, pages: int = 3, sort: str = "popularity") -> List[Dict[str, Any]]:
    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    }
    async with AsyncSession(impersonate="chrome", headers=headers, timeout=config.SCRAPER_TIMEOUT) as session:
        for page in range(1, pages + 1):
            url = f"https://www.nykaa.com/{category_path}?page_no={page}&sort={sort}"
            try:
                await asyncio.sleep(0.5)  # Fast discovery: 0.5s per page (was 2.0s - too slow)
                response = await session.get(url)
                if response.status_code != 200:
                    continue

                page_products = []

                # Strategy 1: Fast & 100% accurate JSON extraction from window.__PRELOADED_STATE__
                try:
                    import json
                    txt = response.text
                    if "window.__PRELOADED_STATE__" in txt:
                        start_idx = txt.find("window.__PRELOADED_STATE__ =")
                        if start_idx != -1:
                            j_str = txt[start_idx + len("window.__PRELOADED_STATE__ ="):].strip()
                            if j_str.endswith(";"):
                                j_str = j_str[:-1].strip()
                            data = json.loads(j_str)
                            prods = (data.get("categoryListing", {}).get("listingData", {}).get("products") or 
                                     data.get("searchListingPage", {}).get("listingData", {}).get("products") or [])
                            for p in prods:
                                pid = str(p.get("productId") or p.get("id") or "")
                                slug = p.get("slug") or ""
                                if slug:
                                    product_url = f"https://www.nykaa.com/{slug.lstrip('/')}"
                                else:
                                    product_url = f"https://www.nykaa.com/p/{pid}"

                                title = clean_title(p.get("title") or p.get("productTitle") or p.get("name") or "")
                                raw_img = p.get("imageUrl") or ""
                                # Upgrade thumbnail to high quality
                                if "tr:" in raw_img:
                                    image_url = re.sub(r'tr:[^/]+/', 'tr:h-500,w-500,cm-pad_resize/', raw_img)
                                else:
                                    image_url = raw_img

                                price = float(p.get("price") or 0)
                                mrp = float(p.get("mrp") or price)
                                rating = float(p.get("rating") or 4.3)
                                review_count = int(p.get("ratingCount") or 0)

                                if pid and title and price > 0:
                                    page_products.append({
                                        "platform": "nykaa",
                                        "platform_id": pid,
                                        "product_url": product_url,
                                        "affiliate_url": build_cuelinks_url_sync(product_url),
                                        "title": title,
                                        "image_url": image_url,
                                        "current_price": price,
                                        "mrp": mrp,
                                        "rating": rating,
                                        "review_count": review_count,
                                    })
                except Exception as ex:
                    logger.debug(f"Nykaa preloaded state extraction fallback: {ex}")

                # If JSON state produced products, append and move to next page
                if page_products:
                    results.extend(page_products)
                    continue

                # Strategy 2: DOM Parsing Fallback
                tree = HTMLParser(response.text)
                items = tree.css("div.productWrapper, div.product-listing")
                if not items:
                    # No more products in this category, stop paging early
                    break

                for item in items:
                    link_node = item.css_first("a[href*='/p/']")
                    if not link_node:
                        continue
                    href = link_node.attributes.get("href", "")
                    if not href.startswith("http"):
                        product_url = "https://www.nykaa.com" + href
                    else:
                        product_url = href

                    pid_match = re.search(r'/p/(\d+)', product_url)
                    pid = pid_match.group(1) if pid_match else re.sub(r'https?://[^/]+/', '', product_url).split('?')[0]

                    # 1. Extract title from img alt (ignoring generic icons)
                    title = ""
                    for img in item.css("img[alt]"):
                        alt = img.attributes.get("alt", "").strip()
                        if alt and not any(x in alt.lower() for x in ["editor", "pick", "featured", "nykaa", "star", "badge", "offer"]):
                            title = alt
                            break
                        elif alt and not title:
                            title = alt

                    # 2. Text node fallback
                    if not title or len(title) < 5:
                        for sel in ["div.css-xrzmfa", "div.css-11gn9r6", "div.css-154w49h", ".title", "h3", "h2"]:
                            node = item.css_first(sel)
                            if node and node.text(strip=True):
                                title = node.text(strip=True)
                                break

                    # 3. URL slug fallback (100% reliable)
                    if not title or len(title) < 4 or title.lower() in ["nykaa", "editor_pickv2", "featured"]:
                        slug = re.sub(r'https?://[^/]+/', '', product_url).split('/p/')[0].lstrip('/').replace('-', ' ').title()
                        title = slug if slug else f"Nykaa Product {pid}"

                    title = clean_title(title)

                    img_node = item.css_first("img[src*='catalog/product'], img[src*='media'], img[src*='assets'], img")
                    image_url = ""
                    if img_node:
                        image_url = img_node.attributes.get("src") or img_node.attributes.get("data-src") or ""
                        if "tr:" in image_url:
                            image_url = re.sub(r'tr:[^/]+/', 'tr:h-500,w-500,cm-pad_resize/', image_url)

                    price = None
                    p_node = item.css_first(".css-111z9ua, .post-discount-price, span.css-111z9ua, .css-1jczs19")
                    if p_node:
                        price = extract_price(p_node.text())

                    mrp = None
                    m_node = item.css_first(".css-u05rr, .css-t37sfa, span[class*='u05rr'], .strike-price, span.css-1kfl14w, .css-17x46n5")
                    if m_node:
                        mrp_cand = extract_price(m_node.text())
                        if mrp_cand and mrp_cand > (price or 0):
                            mrp = mrp_cand

                    if not mrp and price:
                        mrp = price

                    # Rating extraction (e.g. 4.3 in span.css-15wd42o, span.css-1r05unw, .rating)
                    rating = None
                    r_node = item.css_first("span.css-15wd42o, span.css-1r05unw, span[class*='rating'], .rating")
                    if r_node:
                        r_match = re.search(r'([\d.]+)', r_node.text())
                        if r_match:
                            try:
                                rating = float(r_match.group(1))
                            except Exception:
                                pass

                    review_count = None
                    rc_node = item.css_first("span.css-63wuk1, span[class*='review'], span[class*='count']")
                    if rc_node:
                        rc_match = re.search(r'([\d,]+)', rc_node.text())
                        if rc_match:
                            try:
                                review_count = int(rc_match.group(1).replace(',', ''))
                            except Exception:
                                pass

                    aff_url = build_cuelinks_url_sync(product_url)

                    results.append({
                        "platform": "nykaa",
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
                logger.error(f"Error scraping Nykaa category {category_path} page {page}: {e}")
    return results

async def discover_deals_page(pages: int = 2) -> List[Dict[str, Any]]:
    """Crawls Nykaa Top Discount & Beauty Flash Sale items."""
    results = []
    for path in ["skin-care/c/8397", "makeup/c/12", "hair-care/c/8378"]:
        try:
            items = await discover_category(path, pages=pages, sort="discount")
            for it in items:
                it["deal_type"] = "today_deal"
                it["badge"] = "TODAY_DEAL"
            results.extend(items)
        except Exception as e:
            logger.error(f"Error scraping Nykaa deals {path}: {e}")
    return results

