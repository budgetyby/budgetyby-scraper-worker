"""
BudgetBy — Nykaa Scraper
"""
import re
import logging
from curl_cffi.requests import AsyncSession
try:
    from selectolax.lexbor import LexborHTMLParser as HTMLParser
except ImportError:
    from selectolax.parser import HTMLParser
from budgetby.scrapers.base import BaseScraper
from budgetby.scrapers.utils import extract_price, clean_title
from budgetby.affiliate.cuelinks_links import build_cuelinks_url_sync
from budgetby import config

logger = logging.getLogger("budgetby.scrapers.nykaa")

class NykaaScraper(BaseScraper):
    async def _do_scrape_product(self, url: str) -> dict:
        pid_match = re.search(r'/p/(\d+)', url)
        pid = pid_match.group(1) if pid_match else re.sub(r'https?://[^/]+/', '', url).split('?')[0]

        title = ""
        price = 0.0
        mrp = 0.0
        rating = 0.0
        review_count = 0
        image_url = ""
        in_stock = True

        try:
            import json
            async with AsyncSession(impersonate="chrome124", timeout=config.SCRAPER_TIMEOUT) as s:
                r = await s.get(url)

            tree = HTMLParser(r.text)

            # 1. Primary Strategy: Direct DOM Active Variant Price & MRP
            p_node = tree.css_first(".css-1jczs19, .post-discount-price, .css-111z9ua")
            if p_node and p_node.text():
                cand_p = extract_price(p_node.text())
                if cand_p and cand_p > 0:
                    price = cand_p

            m_node = tree.css_first(".css-u05rr, .css-17x46n5, .css-t37sfa, span[class*='mrp']")
            if m_node and m_node.text():
                cand_m = extract_price(m_node.text())
                if cand_m and cand_m >= price:
                    mrp = cand_m

            # 2. Extract Title from H1
            t_node = tree.css_first("h1.css-1gc4x7i, h1.title, h1")
            if t_node and t_node.text():
                raw_title = t_node.text()
                raw_title = re.sub(r'@media[^{]+\{[^}]+\}', '', raw_title)
                raw_title = re.sub(r'\.css-[^{]+\{[^}]+\}', '', raw_title)
                title = clean_title(raw_title)

            # 3. Fallback to application/ld+json for missing title/price/image/rating
            for script in tree.css("script[type='application/ld+json']"):
                txt = script.text() or ""
                try:
                    data = json.loads(txt)
                    if isinstance(data, list):
                        data = data[0]
                    if data.get("@type") == "Product" or "offers" in data:
                        if not title:
                            raw_t = data.get("name", "")
                            if raw_t:
                                raw_t = re.sub(r'@media[^{]+\{[^}]+\}', '', raw_t)
                                raw_t = re.sub(r'\.css-[^{]+\{[^}]+\}', '', raw_t)
                                title = clean_title(raw_t)

                        offers = data.get("offers", {})
                        if isinstance(offers, dict):
                            if not price:
                                p_val = offers.get("price")
                                if p_val:
                                    price = float(p_val)
                            avail = str(offers.get("availability", ""))
                            if "OutOfStock" in avail:
                                in_stock = False

                        imgs = data.get("image")
                        if isinstance(imgs, list) and imgs:
                            image_url = imgs[0]
                        elif isinstance(imgs, str):
                            image_url = imgs

                        agg = data.get("aggregateRating", {})
                        if isinstance(agg, dict):
                            r_val = agg.get("ratingValue")
                            if r_val:
                                rating = float(r_val)
                        break
                except Exception:
                    pass

            if not mrp or mrp < price:
                mrp = price

            # 4. State Extraction Fallback (__PRELOADED_STATE__)
            for script in tree.css("script"):
                stxt = script.text() or ""
                if "window.__PRELOADED_STATE__" in stxt:
                    try:
                        s_idx = stxt.find("window.__PRELOADED_STATE__ =")
                        if s_idx != -1:
                            j_str = stxt[s_idx + len("window.__PRELOADED_STATE__ ="):].strip()
                            if j_str.endswith(";"):
                                j_str = j_str[:-1].strip()
                            s_data = json.loads(j_str)
                            prod_obj = s_data.get("productPage", {}).get("product", {})
                            if prod_obj:
                                if not image_url and prod_obj.get("imageUrl"):
                                    image_url = prod_obj.get("imageUrl")
                                if not title and prod_obj.get("title"):
                                    title = clean_title(prod_obj.get("title"))
                                if (not price or price <= 0) and prod_obj.get("price"):
                                    price = float(prod_obj.get("price"))
                                if (not mrp or mrp <= 0) and prod_obj.get("mrp"):
                                    mrp = float(prod_obj.get("mrp"))
                                if prod_obj.get("rating"):
                                    rating = float(prod_obj.get("rating"))
                                if prod_obj.get("ratingCount"):
                                    review_count = int(prod_obj.get("ratingCount"))
                    except Exception:
                        pass
                    break

            # 5. Meta Tag & DOM Image Fallback
            if not image_url:
                og_node = tree.css_first("meta[property='og:image'], meta[name='og:image']")
                if og_node and og_node.attributes.get("content"):
                    image_url = og_node.attributes.get("content").strip()

            if not image_url:
                tw_node = tree.css_first("meta[property='twitter:image'], meta[name='twitter:image']")
                if tw_node and tw_node.attributes.get("content"):
                    image_url = tw_node.attributes.get("content").strip()

            if not image_url:
                img_node = tree.css_first("img[src*='catalog/product'], img.css-11q6006, img[alt]")
                if img_node:
                    image_url = img_node.attributes.get("src", "").strip()

            # Upgrade Nykaa image thumbnail to crisp 800x800 resolution
            if image_url and "tr:" in image_url:
                image_url = re.sub(r'tr:[^/]+/', 'tr:h-800,w-800,cm-pad_resize/', image_url)
        except Exception as e:
            logger.warning(f"Nykaa scraping parsing warning for {url}: {e}")

        if not title or price <= 0:
            return None

        if not mrp or mrp < price:
            mrp = price
        elif mrp > 15.0 * price or mrp > 500000:
            mrp = price

        affiliate_url = build_cuelinks_url_sync(url)

        return {
            "platform": "nykaa",
            "platform_id": pid,
            "title": title,
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
        from budgetby.discovery.nykaa_discover import discover_category
        path = re.sub(r'https?://[^/]+/', '', url).split('?')[0]
        return await discover_category(path, pages=1)
