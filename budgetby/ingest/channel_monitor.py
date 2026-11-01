"""
BudgetBy — Real-Time Telegram Channel Deal Monitor & Ingestion Engine
Monitors public deal channels in parallel (bypassing ISP blocks with DoH),
sanitizes affiliate tags, verifies live product pricing locally,
stores in local PostgreSQL database, and immediately broadcasts verified deals.
"""

import logging
import re
import asyncio
from typing import Optional, List, Dict, Any
from selectolax.parser import HTMLParser
import httpx
from budgetby import database, config
from budgetby.engine.classifier import classify_product
from budgetby.utils import is_monetized_affiliate_url
from budgetby.scrapers.utils import get_random_ua

logger = logging.getLogger("budgetby.ingest.channel_monitor")

# Configurable list of active high-velocity public deal channels to monitor in parallel
DEFAULT_MONITORED_CHANNELS = [
    "flipkart_deals",       # 126K Subscribers (Dealshub)
    "desidime",             # 85K Subscribers (DesiDime Official)
    "dealbeeofficial"       # 38.2K Subscribers (DealBee Deals)
]

# Cloudflare DoH IP for Telegram Web
TELEGRAM_WEB_IP = "149.154.167.99"

# In-memory baseline tracker: {channel_name: max_seen_post_id}
_channel_baseline_post_ids = {}

async def unshorten_url(url: str, client: httpx.AsyncClient) -> str:
    """Follow HTTP redirects to unshorten shortened links."""
    if not url or not url.startswith("http"):
        return url or ""
    try:
        if any(store in url for store in ["amazon.in/dp/", "amazon.in/gp/product/"]):
            return url

        resp = await client.get(url, follow_redirects=True, timeout=8)
        final_url = str(resp.url)

        # If landed on linkredirect.in (EarnKaro gateway), extract destination from dl parameter
        if "linkredirect.in" in final_url and "dl=" in final_url:
            import urllib.parse
            parsed = urllib.parse.urlparse(final_url)
            qs = urllib.parse.parse_qs(parsed.query)
            if "dl" in qs and qs["dl"]:
                return qs["dl"][0]

        return final_url
    except Exception:
        return url

def clean_and_tag_url(resolved_url: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Identifies store platform, cleans the product URL for local scraping,
    and builds affiliate URL for Amazon (with official dealpulse tag).
    For non-Amazon platforms, returns clean merchant URL (affiliate link is kept from Telegram raw_url).
    Returns: (platform, clean_product_url, affiliate_url)
    """
    if not resolved_url:
        return None, None, None

    # 1. Amazon
    if "amazon.in" in resolved_url or "amzn." in resolved_url or "link.amazon" in resolved_url:
        asin_match = re.search(r'/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})', resolved_url)
        if not asin_match:
            asin_match = re.search(r'hidden-keywords=([A-Z0-9]{10})', resolved_url)
        if not asin_match:
            asin_match = re.search(r'/([A-Z0-9]{10})(?:[/?]|$)', resolved_url)
        if asin_match:
            asin = asin_match.group(1)
            clean_url = f"https://www.amazon.in/dp/{asin}"
            aff_url = f"{clean_url}?tag={config.AMAZON_ASSOCIATE_TAG}"
            return "amazon", clean_url, aff_url

    # 2. Flipkart
    elif "flipkart.com" in resolved_url or "fkrt." in resolved_url:
        resolved_url = resolved_url.replace("dl.flipkart.com/dl/", "www.flipkart.com/").replace("dl.flipkart.com/", "www.flipkart.com/")
        pid_match = re.search(r'[?&]pid=([A-Za-z0-9]+)', resolved_url)
        base_path = resolved_url.split('?')[0]
        clean_url = f"{base_path}?pid={pid_match.group(1)}" if pid_match else base_path
        if "/p/itm" in clean_url or "/p/" in clean_url or pid_match:
            return "flipkart", clean_url, clean_url

    # 3. Myntra
    elif "myntra.com" in resolved_url or "myntr." in resolved_url:
        clean_url = resolved_url.split('?')[0]
        if "/buy" in clean_url or re.search(r'/\d+$', clean_url):
            return "myntra", clean_url, clean_url

    # 4. Ajio
    elif "ajio.com" in resolved_url:
        clean_url = resolved_url.split('?')[0]
        if "/p/" in clean_url:
            return "ajio", clean_url, clean_url

    # 5. Nykaa
    elif "nykaa.com" in resolved_url:
        clean_url = resolved_url.split('?')[0]
        if "/p/" in clean_url or "/product/" in clean_url:
            return "nykaa", clean_url, clean_url

    return None, None, None

async def scrape_channel_posts(channel: str, limit: int = 15) -> List[Dict[str, Any]]:
    """Scrape latest messages from Telegram channel web preview via DoH."""
    channel_name = channel.lstrip("@").strip()
    extracted_posts = []

    try:
        transport = httpx.AsyncHTTPTransport(verify=False)
        async with httpx.AsyncClient(transport=transport, follow_redirects=False, timeout=10, headers={"User-Agent": get_random_ua()}) as client:
            resp = await client.get(f"https://{TELEGRAM_WEB_IP}/s/{channel_name}", headers={"Host": "t.me"})
            
            if resp.status_code != 200:
                return []

            tree = HTMLParser(resp.text)
            message_nodes = tree.css(".tgme_widget_message_wrap")

            for node in message_nodes[-limit:]:
                msg_node = node.css_first(".tgme_widget_message")
                if not msg_node:
                    continue

                data_post = msg_node.attributes.get("data-post", "")
                post_id = int(data_post.split("/")[-1]) if "/" in data_post else 0

                text_node = node.css_first(".tgme_widget_message_text")
                post_text = text_node.text(strip=True) if text_node else ""

                link_nodes = node.css("a[href]")
                raw_urls = []
                for ln in link_nodes:
                    href = ln.attributes.get("href", "")
                    if href and href.startswith("http") and "t.me/" not in href and "telegram.org" not in href:
                        raw_urls.append(href)

                if raw_urls and post_id > 0:
                    extracted_posts.append({
                        "channel": f"@{channel_name}",
                        "post_id": post_id,
                        "text": post_text,
                        "raw_urls": list(set(raw_urls))
                    })

    except Exception as e:
        logger.error(f"Error scraping channel @{channel_name}: {e}")

    return extracted_posts

async def verify_and_ingest_single_deal(channel: str, post_id: int, raw_url: str, client: httpx.AsyncClient) -> Optional[int]:
    """
    Unshortens URL, verifies product data live with local scrapers, saves in DB, and queues deal if valid.
    """
    try:
        # 1. Unshorten & clean
        resolved = await unshorten_url(raw_url, client)
        platform, clean_url, aff_url = clean_and_tag_url(resolved)

        if not platform or not clean_url:
            return None

        # 2. Extract Platform ID
        platform_id = None
        if platform == "amazon":
            asin_match = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', clean_url)
            platform_id = asin_match.group(1) if asin_match else None
        elif platform == "flipkart":
            pid_m = re.search(r'[?&]pid=([A-Za-z0-9]+)', clean_url) or re.search(r'[?&]pid=([A-Za-z0-9]+)', resolved) or re.search(r'[?&]pid=([A-Za-z0-9]+)', raw_url)
            if pid_m:
                platform_id = pid_m.group(1)
            else:
                itm_match = re.search(r'/p/([^/?]+)', clean_url)
                platform_id = itm_match.group(1) if itm_match else clean_url.split('/')[-1]
        elif platform == "myntra":
            mid_m = re.search(r'/(\d{5,10})', clean_url) or re.search(r'/(\d{5,10})', resolved) or re.search(r'/(\d{5,10})', raw_url)
            platform_id = mid_m.group(1) if mid_m else clean_url.rstrip('/').split('/')[-1].replace('.html', '')
        elif platform in ["ajio", "nykaa"]:
            platform_id = clean_url.rstrip('/').split('/')[-1].replace('.html', '')

        if not platform_id:
            return None

        # 3. Scrape Live Store Product Details
        live_data = None
        if platform == "amazon":
            from budgetby.scrapers.amazon import AmazonScraper
            live_data = await AmazonScraper()._do_scrape_product(clean_url)
        elif platform == "flipkart":
            from budgetby.scrapers.flipkart import FlipkartScraper
            live_data = await FlipkartScraper()._do_scrape_product(clean_url)
        elif platform == "myntra":
            from budgetby.scrapers.myntra import MyntraScraper
            live_data = await MyntraScraper()._do_scrape_product(clean_url)
        elif platform == "ajio":
            from budgetby.scrapers.ajio import AjioScraper
            live_data = await AjioScraper()._do_scrape_product(clean_url)
        elif platform == "nykaa":
            from budgetby.scrapers.nykaa import NykaaScraper
            live_data = await NykaaScraper()._do_scrape_product(clean_url)

        if not live_data or not live_data.get("current_price") or live_data.get("current_price") <= 0:
            await database.execute("""
                INSERT INTO ingested_channel_deals (source_channel, message_id, raw_url, resolved_url, platform, status)
                VALUES ($1, $2, $3, $4, $5, 'FAILED_SCRAPE');
            """, channel, post_id, raw_url, clean_url, platform)
            return None

        price = float(live_data.get("current_price"))
        mrp = float(live_data.get("mrp") or price)
        from budgetby.scrapers.utils import clean_title, is_blacklisted_utility_item
        title = clean_title(live_data.get("title") or f"{platform.capitalize()} Product")
        rating = float(live_data.get("rating") or 4.2)
        review_count = int(live_data.get("review_count") or 100)
        image_url = live_data.get("image_url") or ""
        in_stock = bool(live_data.get("in_stock", True))

        if is_blacklisted_utility_item(title, clean_url):
            logger.info(f"Skipping blacklisted utility/service item: {title} ({clean_url})")
            await database.execute("""
                INSERT INTO ingested_channel_deals (source_channel, message_id, raw_url, resolved_url, platform, status, title, price, mrp)
                VALUES ($1, $2, $3, $4, $5, 'BLACKLISTED_UTILITY', $6, $7, $8);
            """, channel, post_id, raw_url, clean_url, platform, title, price, mrp)
            return None

        if not in_stock:
            await database.execute("""
                INSERT INTO ingested_channel_deals (source_channel, message_id, raw_url, resolved_url, platform, status, title, price, mrp)
                VALUES ($1, $2, $3, $4, $5, 'OUT_OF_STOCK', $6, $7, $8);
            """, channel, post_id, raw_url, clean_url, platform, title, price, mrp)
            return None

        # 4. Upsert product into local database
        # For Amazon, use official dealpulse associate tag.
        # For non-Amazon, convert through our own EarnKaro/Cuelinks bot to ensure clicks attribute to our account.
        final_deal_url = None
        if platform == "amazon":
            final_deal_url = f"{clean_url}?tag={config.AMAZON_ASSOCIATE_TAG}"
        elif is_monetized_affiliate_url(raw_url):
            final_deal_url = raw_url
        else:
            try:
                from budgetby.ingest.telegram_listener import convert_url_via_ek_bot, convert_url_via_cuelinks_bot
                if platform in ("flipkart", "myntra", "ajio"):
                    converted = await convert_url_via_ek_bot(clean_url, platform=platform, timeout=6.0)
                    if converted and converted != clean_url:
                        final_deal_url = converted
                elif platform == "nykaa":
                    converted = await convert_url_via_cuelinks_bot(clean_url, platform="nykaa", timeout=8.0)
                    if converted and converted != clean_url:
                        final_deal_url = converted
            except Exception as e:
                logger.debug(f"Immediate channel convert notice: {e}")
            if not final_deal_url:
                final_deal_url = clean_url

        prod_dict = {
            "platform": platform,
            "platform_id": platform_id,
            "title": title,
            "category": classify_product(title),
            "product_url": clean_url,
            "affiliate_url": final_deal_url,
            "image_url": image_url,
            "current_price": price,
            "mrp": mrp,
            "rating": rating,
            "review_count": review_count,
            "in_stock": in_stock
        }

        pid = await database.upsert_product(prod_dict)
        if not pid:
            return None

        # Record daily price snapshot
        await database.upsert_daily_price(pid, price)

        # 5. Verify Deal Legitimacy
        discount_pct = round(((mrp - price) / mrp) * 100, 1) if mrp > price else 0.0
        min_disc = getattr(config, "MIN_DEAL_DISCOUNT_PCT", 10.0)
        min_sav = getattr(config, "MIN_DEAL_SAVINGS_INR", 30.0)
        is_valid_deal = (mrp > price) and (discount_pct >= min_disc) and ((mrp - price) >= min_sav)

        status = "VERIFIED_DEAL" if is_valid_deal else "SAVED_TO_CATALOG"
        await database.execute("""
            INSERT INTO ingested_channel_deals (source_channel, message_id, raw_url, resolved_url, platform, product_id, status, title, price, mrp, discount_pct)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11);
        """, channel, post_id, raw_url, clean_url, platform, pid, status, title, price, mrp, discount_pct)

        # 6. If verified deal, queue deal and trigger immediate posting!
        from budgetby.engine.cooldown import is_on_cooldown
        if is_valid_deal and not await is_on_cooldown(pid):
            from budgetby.engine.posting_queue import get_posting_queue
            pq = get_posting_queue()
            
            badge = "LOOT" if discount_pct >= 70 else ("MEGA_DROP" if discount_pct >= 50 else ("HOT_DEAL" if discount_pct >= 25 else "DEAL"))
            deal_data = {
                "product": {
                    "id": pid,
                    "title": title,
                    "platform": platform,
                    "category": classify_product(title),
                    "product_url": clean_url,
                    "affiliate_url": final_deal_url,
                    "image_url": image_url,
                    "current_price": price,
                    "mrp": mrp,
                    "rating": rating,
                    "review_count": review_count
                },
                "type": "channel_deal",
                "badge": badge,
                "score": round(min(95, 50 + discount_pct)),
                "source_channel": channel
            }
            await pq.queue_deal(deal_data)
            logger.info(f"🎯 Intercepted & Verified DEAL from {channel} [{platform.upper()}]: {title[:40]} ({discount_pct}% OFF)")

        return pid

    except Exception as e:
        logger.error(f"Error ingesting deal from {channel}: {e}")
        return None

async def _process_single_channel(channel: str, client: httpx.AsyncClient):
    """Processes a single channel, records live heartbeat in database, and processes incoming deals."""
    global _channel_baseline_post_ids
    ch_key = channel.lstrip("@").lower()
    ch_tag = f"@{ch_key}"

    try:
        # Record live scan heartbeat in database
        await database.execute("""
            INSERT INTO channel_monitors (channel_name, status, last_scanned_at)
            VALUES ($1, 'ACTIVE', (NOW() AT TIME ZONE 'Asia/Kolkata'))
            ON CONFLICT (channel_name) DO UPDATE SET
                status = 'ACTIVE',
                last_scanned_at = (NOW() AT TIME ZONE 'Asia/Kolkata');
        """, ch_tag)

        posts = await scrape_channel_posts(channel, limit=15)
        if not posts:
            return

        max_id_on_page = max(p["post_id"] for p in posts)

        # Check baseline post ID from memory or database
        if ch_key not in _channel_baseline_post_ids:
            row = await database.fetchrow("SELECT last_post_id FROM channel_monitors WHERE channel_name = $1;", ch_tag)
            db_last_id = row["last_post_id"] if row and row.get("last_post_id") else None
            if db_last_id and db_last_id > 0:
                # Use stored DB post ID as baseline so posts while offline are ingested
                _channel_baseline_post_ids[ch_key] = db_last_id
                logger.info(f"📍 Channel @{ch_key}: Loaded persistent baseline #{db_last_id} from database.")
            else:
                _channel_baseline_post_ids[ch_key] = max(0, max_id_on_page - 5)
                logger.info(f"📍 Channel @{ch_key}: Initialized fresh baseline at Post #{_channel_baseline_post_ids[ch_key]}.")

        baseline_id = _channel_baseline_post_ids[ch_key]
        new_posts = [p for p in posts if p["post_id"] > baseline_id]

        if new_posts:
            logger.info(f"⚡ Channel @{ch_key}: Found {len(new_posts)} NEW incoming deal posts (since #{baseline_id})")
            for p in new_posts:
                for raw_url in p["raw_urls"]:
                    await verify_and_ingest_single_deal(
                        channel=p["channel"],
                        post_id=p["post_id"],
                        raw_url=raw_url,
                        client=client
                    )
                    await asyncio.sleep(0.3)

        _channel_baseline_post_ids[ch_key] = max_id_on_page
        # Update last post id in database after processing
        await database.execute("UPDATE channel_monitors SET last_post_id = $2 WHERE channel_name = $1;", ch_tag, max_id_on_page)

    except Exception as e:
        logger.error(f"Error processing channel @{channel}: {e}")

async def run_channel_monitor(channels: List[str] = None):
    """
    Main background job: Scrapes all monitored channels concurrently in parallel.
    """
    target_channels = channels or DEFAULT_MONITORED_CHANNELS
    async with httpx.AsyncClient(timeout=12, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}) as client:
        await asyncio.gather(*(_process_single_channel(ch, client) for ch in target_channels))
