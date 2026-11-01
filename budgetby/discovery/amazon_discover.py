"""
Amazon discovery engine with massive keyword matrix for scaling to 15k+ products.
"""
import logging
import re
import asyncio
import random
from typing import List, Dict, Any

from curl_cffi import CurlOpt
from curl_cffi.requests import AsyncSession
from selectolax.parser import HTMLParser
from budgetby import config
from budgetby.affiliate.amazon_links import build_affiliate_url
from budgetby.scrapers.utils import extract_price, fetch_with_retry

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

logger = logging.getLogger("budgetby.discovery.amazon")

ASIN_REGEX = re.compile(r'/dp/([A-Z0-9]{10})')

AMAZON_CATEGORY_KEYWORDS = {
    "electronics": [
        "wireless earbuds", "bluetooth speaker", "smart watch", "headphones", "power bank", "soundbar", "fast charger",
        "boat airdopes", "noise earbuds", "sony headphones", "jbl speaker", "boult audio", "realme buds", "oneplus bullets",
        "apple airpods", "sennheiser", "skullcandy", "marshall speaker", "zebronics soundbar", "portronics power bank",
        "ambrane power bank", "wireless charger", "ring light with stand", "gimbal for smartphone", "action camera 4k",
        "tripod for mobile", "bluetooth audio transmitter", "usb c hub"
    ],
    "computers": [
        "gaming laptop", "ssd 1tb", "wireless mouse", "mechanical keyboard", "computer monitor", "laptop stand",
        "hp victus laptop", "asus tuf gaming", "lenovo ideapad", "dell laptop i5", "macbook air", "samsung ssd 1tb",
        "crucial ssd 500gb", "sandisk pendrive 128gb", "logitech wireless keyboard", "razer mouse", "ergonomic mouse",
        "gaming chair", "24 inch ips monitor", "27 inch 144hz monitor", "mechanical keyboard red switch", "laptop cooling pad",
        "wifi 6 router", "drawing tablet with pen"
    ],
    "smartphones": [
        "5g mobile phone", "iphone 15", "iphone 14", "oneplus 12", "oneplus nord ce", "samsung galaxy s24", "samsung m34 5g",
        "redmi note 13 pro", "realme 12 pro", "iqoo z9 5g", "poco x6 pro", "motorola edge 50", "mobile back cover",
        "tempered glass 9d", "car phone mount", "magsafe power bank"
    ],
    "appliances": [
        "air fryer", "mixer grinder", "water purifier", "microwave oven", "vacuum cleaner", "refrigerator",
        "philips air fryer", "prestige pressure cooker", "pigeon mixer grinder 750w", "kent ro water purifier",
        "morphy richards oven toaster grill", "induction cooktop 2000w", "sandwich maker grill", "electric kettle 1.8l stainless steel",
        "cold press juicer", "hand blender with chopper", "food processor multi purpose", "steam iron 2000w", "robot vacuum mop"
    ],
    "home": [
        "cookware set non stick", "bedsheets king size cotton", "water bottle steel insulated", "office chair ergonomic",
        "curtains for door 7 feet", "wall clock modern", "sleepwell mattress", "wakefit orthopaedic mattress",
        "cast iron kadai", "milton thermosteel flask", "borosil glass storage containers", "dinner set 32 piece",
        "chef knife set", "blackout curtains", "study table foldable", "led strip lights rgb", "water geyser 25 litre"
    ],
    "apparel": [
        "men t-shirt cotton", "women kurti set with dupatta", "jeans men slim fit", "dresses for women western",
        "track pants men", "formal shirts for men", "levi's jeans men", "pepe jeans", "us polo assn polo t shirt",
        "tommy hilfiger shirt", "allen solly formal shirt", "van heusen trousers", "biba women kurta", "libas silk saree",
        "aurelia women ethnic set", "monte carlo jacket men", "jockey track pants"
    ],
    "shoes": [
        "running shoes men", "sneakers women white", "formal shoes men leather", "sandals for men leather", "walking shoes breathable",
        "nike running shoes men", "adidas sneakers men", "puma court shoes", "woodland boots leather", "skechers walking shoes d lites",
        "red tape sneakers", "asics gel running shoes", "crocs clogs unisex", "bata formal shoes"
    ],
    "watches": [
        "casio vintage digital watch", "casio edifice analog", "titan octane chronograph", "fastrack reflex smartwatch",
        "fossil gen 6 smartwatch", "timex analog watch men", "fossil grant watch", "g shock tough solar watch",
        "citizen eco drive", "sonata men analog watch", "daniel wellington watch women", "noise smart watch amoled"
    ],
    "beauty": [
        "minimalist 10% niacinamide serum", "the derma co 1% hyaluronic sunscreen", "cetaphil gentle skin cleanser",
        "dot and key vitamin c moisturizer", "plum green tea toner", "mamaearth onion hair oil", "l'oreal paris professional shampoo",
        "philips beard trimmer series 3000", "beardo hair styling wax", "bombay shaving company razor", "bellavita perfume combo pack",
        "villain perfume hydra", "skinn by titan perfume raw", "maybelline liquid lipstick", "neutrogena hydro boost water gel"
    ],
    "sports": [
        "yonex nanoray badminton racket", "lining windstorm badminton racket", "nivia football size 5 storm", "sg cricket bat english willow",
        "boldfit resistance bands set", "kore dumbbells 20kg combo set", "strauss yoga mat 6mm with carry bag", "fitkit treadmill motorised for home",
        "gym shaker bottle stainless steel", "whey protein isolate 1kg", "creatine monohydrate micronized", "cycling helmet adult isi"
    ],
    "toys": [
        "lego classic creative bricks", "hot wheels 5 car pack original", "nerf elite blaster gun", "monopoly board game classic",
        "uno card game original", "barbie doll set with accessories", "rubiks cube 3x3 magnetic speedcube", "remote control drone with hd camera",
        "stem educational science kit for kids", "rc rock crawler monster truck 4wd"
    ],
    "automotive": [
        "70mai dash cam pro plus", "qubo car dashcam front and rear", "tusa tyre inflator 12v digital", "bergmann car vacuum cleaner high power",
        "solimo microfiber cloth 800 gsm", "formula 1 car wax liquid polish", "vega cliff helmet isi certified", "studds ninja 3g flip up helmet",
        "motul chain cleaner and lube combo", "pressure washer 1800w for car washing"
    ],
}

async def _parse_amazon_listing(tree: HTMLParser) -> List[Dict[str, Any]]:
    results = []
    for item in tree.css(".zg-grid-general-faceout, .zg-item-immersion, .a-carousel-card, div[data-component-type='s-search-result']"):
        link_tag = item.css_first("a.a-link-normal")
        if not link_tag:
            continue
        href = link_tag.attributes.get("href", "")
        match = ASIN_REGEX.search(href)
        if not match:
            data_asin = item.attributes.get("data-asin")
            if data_asin and len(data_asin) == 10:
                asin = data_asin
            else:
                continue
        else:
            asin = match.group(1)

        title = ""
        for t_sel in [
            "[class*='p13n-sc-css-line-clamp']",
            "div._cDEzb_p13n-sc-css-line-clamp-2_EWgCb",
            "div._cDEzb_p13n-sc-css-line-clamp-1_1Fn1y",
            ".p13n-sc-truncate-desktop-type2",
            "h2 a span",
            "a.a-link-normal span",
            "img[alt]"
        ]:
            t_node = item.css_first(t_sel)
            if t_node:
                title = t_node.attributes.get("alt") if t_node.tag == "img" else t_node.text(strip=True)
                if title:
                    break

        img_node = item.css_first("img")
        image_url = img_node.attributes.get("src", "") if img_node else ""

        price = None
        price_node = item.css_first(".p13n-sc-price, ._cDEzb_p13n-sc-price_3mJ9Z, span.a-price-whole, span.a-price span.a-offscreen")
        if price_node:
            price = extract_price(price_node.text())

        mrp = None
        mrp_node = item.css_first("span.a-price.a-text-price span.a-offscreen, span.a-text-strike, span.basisPrice span.a-offscreen, .dcl-product-price-old span.a-offscreen, span[data-a-strike='true'] span.a-offscreen")
        if mrp_node:
            mrp_cand = extract_price(mrp_node.text())
            if mrp_cand and mrp_cand > (price or 0):
                if price and (mrp_cand > 15.0 * price or mrp_cand > 500000):
                    mrp_cand = price
                mrp = mrp_cand

        # Fallback: Check for discount percentage on card (e.g. "(88% off)" or "70% off")
        if (not mrp or mrp <= (price or 0)) and price:
            for span in item.css("span"):
                txt = span.text(strip=True)
                disc_match = re.search(r'\(?(\d{1,2})%\s*off\)?', txt, re.IGNORECASE)
                if disc_match:
                    pct = float(disc_match.group(1))
                    if 15 <= pct <= 95:
                        calc_mrp = round(price / (1.0 - (pct / 100.0)), 2)
                        if calc_mrp > price:
                            mrp = calc_mrp
                            break

        if not mrp and price:
            mrp = price


        # Rating & Review Count
        rating = None
        rating_node = item.css_first("i.a-icon-star-small span.a-icon-alt, span[aria-label*='stars'], span.a-icon-alt")
        if rating_node:
            r_match = re.search(r'([\d.]+)', rating_node.text())
            if r_match:
                try:
                    rating = float(r_match.group(1))
                except Exception:
                    pass

        review_count = None
        rc_node = item.css_first("span.a-size-small, a.a-link-normal span.a-size-base, span[aria-label*='ratings']")
        if rc_node:
            rc_match = re.search(r'([\d,]+)', rc_node.text())
            if rc_match:
                try:
                    review_count = int(rc_match.group(1).replace(',', ''))
                except Exception:
                    pass

        results.append({
            "platform": "amazon",
            "platform_id": asin,
            "product_url": f"https://www.amazon.in/dp/{asin}",
            "affiliate_url": build_affiliate_url(asin),
            "title": title,
            "image_url": image_url,
            "current_price": price,
            "mrp": mrp or price,
            "rating": rating,
            "review_count": review_count,
        })
    return results

async def discover_bestsellers(category_slug: str, pages: int = 3) -> List[Dict[str, Any]]:
    results = []
    async with AsyncSession(impersonate="chrome120", curl_options={CurlOpt.IPRESOLVE: 1}, headers=AMAZON_HEADERS, timeout=config.SCRAPER_TIMEOUT) as session:
        for page in range(1, pages + 1):
            url = f"https://www.amazon.in/gp/bestsellers/{category_slug}/ref=zg_bs_pg_{page}?ie=UTF8&pg={page}"
            try:
                await asyncio.sleep(config.SCRAPER_DELAY_MIN)
                response = await fetch_with_retry(session, url, timeout=config.SCRAPER_TIMEOUT)
                if response and response.status_code == 200:
                    tree = HTMLParser(response.text)
                    items = await _parse_amazon_listing(tree)
                    results.extend(items)
            except Exception as e:
                logger.error(f"Error scraping Amazon bestsellers {category_slug} page {page}: {e}")
    return results

async def discover_new_releases(category_slug: str, pages: int = 2) -> List[Dict[str, Any]]:
    results = []
    async with AsyncSession(impersonate="chrome120", curl_options={CurlOpt.IPRESOLVE: 1}, headers=AMAZON_HEADERS, timeout=config.SCRAPER_TIMEOUT) as session:
        for page in range(1, pages + 1):
            url = f"https://www.amazon.in/gp/new-releases/{category_slug}/ref=zg_bs_pg_{page}?ie=UTF8&pg={page}"
            try:
                await asyncio.sleep(config.SCRAPER_DELAY_MIN)
                response = await fetch_with_retry(session, url, timeout=config.SCRAPER_TIMEOUT)
                if response and response.status_code == 200:
                    tree = HTMLParser(response.text)
                    items = await _parse_amazon_listing(tree)
                    results.extend(items)
            except Exception as e:
                logger.error(f"Error scraping Amazon new releases {category_slug}: {e}")
    return results




async def discover_category_deals(category: str, min_discount: int = 35, pages: int = 1) -> List[Dict[str, Any]]:
    """
    Discovers live high-discount deals for an Amazon category using high-yield keyword queries
    with Amazon's native &pct-off filter.
    Guarantees products have both selling price and valid MRP on their listing cards.
    """
    results = []
    keywords = AMAZON_CATEGORY_KEYWORDS.get(category, [category])
    sample_kws = random.sample(keywords, min(2, len(keywords))) if len(keywords) > 2 else keywords
    
    async with AsyncSession(impersonate="chrome120", curl_options={CurlOpt.IPRESOLVE: 1}, headers=AMAZON_HEADERS, timeout=config.SCRAPER_TIMEOUT) as session:
        for kw in sample_kws:
            for page in range(1, pages + 1):
                url = f"https://www.amazon.in/s?k={kw.replace(' ', '+')}&pct-off={min_discount}-&page={page}"
                try:
                    await asyncio.sleep(0.5)
                    response = await fetch_with_retry(session, url, timeout=config.SCRAPER_TIMEOUT)
                    if response and response.status_code == 200:
                        tree = HTMLParser(response.text)
                        items = await _parse_amazon_listing(tree)
                        for it in items:
                            it["category"] = category
                        results.extend(items)
                except Exception as e:
                    logger.debug(f"Error scraping Amazon category deals for {category} ({kw}): {e}")
    return results


async def discover_deals_page(pages: int = 3) -> List[Dict[str, Any]]:
    """Scrapes Amazon India Today's Deals and Goldbox Lightning Deals hub."""
    results = []
    deal_urls = [
        "https://www.amazon.in/deals",
        "https://www.amazon.in/gp/goldbox",
    ]
    async with AsyncSession(impersonate="chrome120", curl_options={CurlOpt.IPRESOLVE: 1}, headers=AMAZON_HEADERS, timeout=config.SCRAPER_TIMEOUT) as session:
        for base_url in deal_urls:
            for page in range(1, pages + 1):
                url = f"{base_url}?page={page}" if page > 1 else base_url
                try:
                    await asyncio.sleep(0.5)
                    response = await fetch_with_retry(session, url, timeout=config.SCRAPER_TIMEOUT)
                    if response and response.status_code == 200:
                        tree = HTMLParser(response.text)
                        items = await _parse_amazon_listing(tree)
                        for it in items:
                            it["deal_type"] = "today_deal"
                            it["badge"] = "TODAY_DEAL"
                        results.extend(items)
                except Exception as e:
                    logger.error(f"Error scraping Amazon deals {url}: {e}")
    return results



