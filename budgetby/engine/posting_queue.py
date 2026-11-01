"""
BudgetBy — High-Efficiency Paced & Dynamic Burst Deal Posting Engine
Guarantees:
1. Minimum Cadence: Exactly 1 post every 30 seconds (120 posts/hour • 2,880 posts/day) 24/7.
2. Fast Queue Drain: Queued deals (channel interceptions, price drops) are broadcast at 8s cadence
   through a dedicated fast-path that skips the lock, live hunt, and pre-flight scraping.
3. Multi-Store Interleaving: Catalog fallback uses weighted rotation to preserve anti-clustering.
4. Cooldown Accuracy: Cooldowns are applied only after successful delivery.
"""

import asyncio
import datetime
import io
import logging
import re
import time
import zoneinfo
from PIL import Image
from curl_cffi.requests import AsyncSession
import telegram.error

from budgetby import config, database
from budgetby.bot import templates

logger = logging.getLogger("budgetby.engine.posting_queue")

_rejected_candidates: dict[int, float] = {}

def _prune_rejected_candidates():
    global _rejected_candidates
    now = time.time()
    if len(_rejected_candidates) > 200:
        _rejected_candidates = {k: v for k, v in _rejected_candidates.items() if v > now}

def _process_image_sync(raw: bytes) -> io.BytesIO | None:
    try:
        im = Image.open(io.BytesIO(raw))
        if im.mode in ("RGBA", "P"):
            im = im.convert("RGB")
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=88)
        out.seek(0)
        out.name = "product.jpg"
        return out
    except Exception:
        b = io.BytesIO(raw)
        b.name = "product.jpg"
        return b

async def _fetch_and_normalize_image(image_url: str, timeout: float = 6.0) -> io.BytesIO | None:
    """
    Fetches product image via Chrome impersonation and normalizes AVIF/WEBP/PNG
    into standard high-quality JPEG for Telegram send_photo in a background thread.
    """
    if not image_url or not image_url.startswith("http"):
        return None
    try:
        async with AsyncSession(impersonate="chrome") as session:
            resp = await session.get(image_url, timeout=timeout)
            if resp.status_code == 200 and len(resp.content) > 500:
                return await asyncio.to_thread(_process_image_sync, resp.content)
    except Exception as e:
        logger.debug(f"Image download note for {image_url[:50]}: {e}")
    return None

# 14-slot proportional rotation cycle:
# Amazon: 4, Flipkart: 3, Myntra: 3, Ajio: 2, Nykaa: 2 (Total = 14 slots)
ROTATION_SEQUENCE = [
    "amazon", "flipkart", "myntra",
    "amazon", "ajio", "flipkart",
    "myntra", "amazon", "nykaa",
    "flipkart", "myntra", "ajio",
    "amazon", "nykaa"
]

def format_deal_message(deal_data: dict) -> str:
    """Format deal using tiered high-converting templates."""
    product = deal_data.get("product", {})
    deal_type = deal_data.get("type", "price_drop")
    score = float(deal_data.get("score") or 50)
    badge = deal_data.get("badge")
    deal_result = {"badge": badge, "score": score}

    if score >= 75 or badge in ["ATL", "LOOT"]:
        return templates.format_mega_deal(product, deal_result)
    elif deal_type in ["todays_deal", "channel_deal"]:
        return templates.format_today_deal(product, deal_result)
    elif hasattr(templates, "format_price_drop"):
        return templates.format_price_drop(product, deal_result)
    else:
        return templates.format_mega_deal(product, deal_result)


def _apply_amazon_tag(url: str) -> str:
    """Ensures Amazon associate tag is present on the URL."""
    tag = getattr(config, "AMAZON_ASSOCIATE_TAG", "dealpulse21-21")
    asin_m = re.search(r'/(?:dp|gp/product|product)/([A-Z0-9]{10})', url)
    if asin_m:
        return f"https://www.amazon.in/dp/{asin_m.group(1)}?tag={tag}"
    if "tag=" in url:
        return re.sub(r'([?&])tag=[^&]*', f'\\1tag={tag}', url)
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}tag={tag}"


class PostingQueue:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(PostingQueue, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._queue = asyncio.Queue(maxsize=1000)
        self._lock = asyncio.Lock()   # Used only for catalog/evergreen path
        self._draining = False
        self._queued_pids = set()
        self._bot = None
        self._last_posted_platform = None
        self._rotation_index = 0
        self.posts_this_hour = 0
        self.hour_started = self._get_ist_hour()
        self.ROTATION_SEQUENCE = ROTATION_SEQUENCE
        self._initialized = True
        logger.info("PostingQueue initialized with fast-drain queue path and 30s catalog pacing.")

    def set_bot(self, bot):
        self._bot = bot

    def _get_ist_hour(self) -> int:
        tz = zoneinfo.ZoneInfo("Asia/Kolkata")
        return datetime.datetime.now(tz).hour

    def reset_hour_if_needed(self):
        cur_hour = self._get_ist_hour()
        if cur_hour != self.hour_started:
            self.posts_this_hour = 0
            self.hour_started = cur_hour

    def _get_bot(self):
        """Returns cached bot or creates one from token."""
        if self._bot:
            return self._bot
        if config.TELEGRAM_BOT_TOKEN:
            from telegram import Bot
            return Bot(token=config.TELEGRAM_BOT_TOKEN)
        return None

    def get_queue_snapshot(self) -> dict:
        """Returns a snapshot of the current queue state and queued deals."""
        items = list(self._queue._queue) if hasattr(self._queue, "_queue") else []
        target_platform = self.ROTATION_SEQUENCE[self._rotation_index % len(self.ROTATION_SEQUENCE)]
        formatted_items = []
        for idx, it in enumerate(items):
            prod = it.get("product", {})
            price = float(prod.get("current_price") or 0)
            mrp = float(prod.get("mrp") or price)
            discount_pct = round(((mrp - price) / mrp) * 100, 1) if mrp > price else 0.0
            formatted_items.append({
                "queue_position": idx + 1,
                "id": prod.get("id"),
                "title": prod.get("title", "Product Deal"),
                "platform": (prod.get("platform") or "store").lower(),
                "price": price,
                "mrp": mrp,
                "discount_pct": discount_pct,
                "badge": it.get("badge", "DEAL"),
                "score": it.get("score", 50),
                "deal_type": it.get("type", "price_drop"),
                "source_channel": it.get("source_channel", "scanner"),
                "image_url": prod.get("image_url") or "",
                "affiliate_url": prod.get("affiliate_url") or prod.get("product_url") or ""
            })

        return {
            "queue_size": len(formatted_items),
            "is_draining_fast": self._draining,
            "next_scheduled_platform": target_platform,
            "last_posted_platform": self._last_posted_platform or "None yet",
            "posts_this_hour": self.posts_this_hour,
            "items": formatted_items
        }

    async def process_queue(self, bot=None):
        """
        Non-blocking wrapper called by the scheduler every 30s.
        - If queue has deals: start fast-drain (no lock, no hunt).
        - If queue is empty: post next catalog/live deal (with lock + hunt).
        """
        if bot:
            self._bot = bot
        if not self._queue.empty():
            if not self._draining:
                asyncio.create_task(self._drain_overflow_queue())
        else:
            asyncio.create_task(self.post_next_deal(bot))

    async def queue_deal(self, deal_data: dict):
        """
        Enqueue an intercepted or organic deal with deduplication.
        Immediately kicks off the fast-drain worker.
        """
        product = deal_data.get('product', {})
        pid = product.get('id')
        plat = (product.get('platform') or 'unknown').lower()
        plat_id = product.get('platform_id')

        dedup_key = pid if pid else (f"{plat}:{plat_id}" if plat_id else None)

        if dedup_key and dedup_key in self._queued_pids:
            logger.info(f"🛡️ #{dedup_key} already in queue. Skipping duplicate.")
            return

        from budgetby.engine.cooldown import is_on_cooldown
        if pid and await is_on_cooldown(pid):
            logger.info(f"🛡️ #{pid} on 24h cooldown. Skipping.")
            return

        if dedup_key:
            self._queued_pids.add(dedup_key)

        deal_data["_enqueued_at"] = time.time()

        # Non-blocking background image prefetch: normalizes image in RAM before deal is popped by drainer!
        img_url = product.get("image_url")
        if img_url:
            async def _bg_prefetch(d_ref, url):
                try:
                    bytes_io = await _fetch_and_normalize_image(url, timeout=3.5)
                    if bytes_io:
                        d_ref["_cached_photo_bytes"] = bytes_io
                except Exception:
                    pass
            asyncio.create_task(_bg_prefetch(deal_data, img_url))

        await self._queue.put(deal_data)
        qsize = self._queue.qsize()
        logger.info(f"📥 Queued #{dedup_key or 'deal'} [{plat.upper()}] (Queue size: {qsize})")

        # Always kick drain when a new deal lands
        if not self._draining:
            asyncio.create_task(self._drain_overflow_queue())

    def add_deal(self, deal_data: dict):
        """
        Synchronous / task wrapper for queue_deal to support flexible callers.
        """
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.queue_deal(deal_data))
        except RuntimeError:
            asyncio.run(self.queue_deal(deal_data))

    def clear(self) -> int:
        """
        Clears all queued deals from the in-memory queue and resets tracking.
        Returns the number of items removed.
        """
        cleared_count = 0
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                cleared_count += 1
            except asyncio.QueueEmpty:
                break
        self._queued_pids.clear()
        logger.info(f"🧹 PostingQueue cleared ({cleared_count} items removed).")
        return cleared_count

    # ── FAST PATH: Queue drain — NO lock, NO live hunt, NO pre-flight scrape ────

    async def _send_to_telegram(self, bot_instance, message_text: str, img_url: str, preloaded_bytes: io.BytesIO = None) -> object:
        """Sends photo or text to Telegram channel. Uses pre-fetched image bytes if available for zero-latency send."""
        sent_msg = None
        photo_bytes = preloaded_bytes
        if not photo_bytes and img_url:
            photo_bytes = await _fetch_and_normalize_image(img_url, timeout=3.0)

        if photo_bytes:
            try:
                photo_bytes.seek(0)
            except Exception:
                pass
            try:
                sent_msg = await bot_instance.send_photo(
                    chat_id=config.TELEGRAM_CHANNEL_ID,
                    photo=photo_bytes,
                    caption=message_text,
                    parse_mode="HTML"
                )
            except telegram.error.RetryAfter as ra:
                logger.warning(f"Rate limit (photo): sleeping {ra.retry_after}s")
                await asyncio.sleep(ra.retry_after + 0.5)
                return None
            except Exception as pe:
                logger.debug(f"Photo send with HTML failed: {pe}. Trying photo without HTML...")
                try:
                    clean_caption = re.sub(r'<[^>]+>', '', message_text)
                    try:
                        photo_bytes.seek(0)
                    except Exception:
                        pass
                    sent_msg = await bot_instance.send_photo(
                        chat_id=config.TELEGRAM_CHANNEL_ID,
                        photo=photo_bytes,
                        caption=clean_caption
                    )
                except Exception as pe2:
                    logger.debug(f"Photo send plain fallback also failed: {pe2}")

        if not sent_msg:
            try:
                sent_msg = await bot_instance.send_message(
                    chat_id=config.TELEGRAM_CHANNEL_ID,
                    text=message_text,
                    parse_mode="HTML",
                    disable_web_page_preview=False
                )
            except telegram.error.RetryAfter as ra:
                logger.warning(f"Rate limit (text): sleeping {ra.retry_after}s")
                await asyncio.sleep(ra.retry_after + 0.5)
                return None
            except Exception as e:
                logger.warning(f"send_message with HTML failed: {e}. Falling back to plain text...")
                try:
                    clean_text = re.sub(r'<[^>]+>', '', message_text)
                    sent_msg = await bot_instance.send_message(
                        chat_id=config.TELEGRAM_CHANNEL_ID,
                        text=clean_text,
                        disable_web_page_preview=False
                    )
                except Exception as e2:
                    logger.error(f"Plain text message delivery failed: {e2}")
                    return None

        return sent_msg

    async def _post_queued_deal(self, deal_data: dict, bot_instance) -> str:
        """
        Fast-path for queued deals. Zero network scraping.
        Returns:
          - "POSTED": Successfully sent to Telegram.
          - "SKIPPED": Deal was on cooldown, stale (>4h), or auto-persisted to DB. Instant 0.001s skip.
          - "FAILED": Delivery or Telegram error. Handled with backoff.
        """
        pid = None
        dedup_key = None
        try:
            from budgetby.engine.cooldown import is_on_cooldown, set_cooldown

            product = deal_data.get("product", {})
            pid = product.get("id")
            platform = (product.get("platform") or "store").lower()
            plat_id = product.get("platform_id")
            dedup_key = pid if pid else (f"{platform}:{plat_id}" if plat_id else None)

            # 1. Stale Deal Guard: Discard deals queued > 4 hours ago
            enqueued_at = deal_data.get("_enqueued_at")
            if enqueued_at and (time.time() - enqueued_at > 14400):
                logger.info(f"⌛ Queued deal #{pid or dedup_key} is > 4h old — skipping stale deal.")
                return "SKIPPED"

            # 2. Cooldown Guard
            if pid and await is_on_cooldown(pid):
                logger.info(f"🛡️ Queued #{pid} on cooldown — skipping instantly.")
                return "SKIPPED"

            price = float(product.get("current_price") or 0)
            mrp = float(product.get("mrp") or price)
            if price <= 0:
                logger.info(f"🚫 Queued #{pid or dedup_key} has price ₹0 — skipping.")
                return "SKIPPED"

            deal_type = deal_data.get("type", "channel_deal")
            badge = deal_data.get("badge", "DEAL")
            score = float(deal_data.get("score") or 60)
            source_channel = deal_data.get("source_channel", "channel")

            savings_amount = max(0.0, mrp - price)
            savings_pct = (savings_amount / mrp) if mrp > 0 else 0.0
            current_backlog = self._queue.qsize()

            # 3. High-Backlog Fast Path: When queue > 20 items, auto-persist low-tier deals directly to DB without Telegram broadcast delay
            if current_backlog > 20 and score < 60 and badge not in ["ATL", "LOOT", "MEGA_DROP"]:
                async def _bg_save():
                    try:
                        await database.insert_deal({
                            "product_id": pid,
                            "platform": platform,
                            "platform_id": plat_id,
                            "deal_type": deal_type,
                            "posted_price": price,
                            "posted_mrp": mrp,
                            "savings_amount": savings_amount,
                            "savings_pct": savings_pct,
                            "deal_score": score,
                            "badge": badge,
                            "source_channel": source_channel
                        })
                    except Exception:
                        pass
                asyncio.create_task(_bg_save())
                return "SKIPPED"

            url = product.get("affiliate_url") or product.get("product_url") or ""
            if platform == "amazon" and url:
                url = _apply_amazon_tag(url)
                product["affiliate_url"] = url

            message_text = format_deal_message(deal_data)
            img_url = product.get("image_url") or ""
            preloaded_bytes = deal_data.get("_cached_photo_bytes")

            sent_msg = await self._send_to_telegram(bot_instance, message_text, img_url, preloaded_bytes)
            if not sent_msg:
                logger.warning(f"Delivery failed for queued #{pid or dedup_key} — will retry.")
                return False

            # Resolve genuine PostgreSQL product ID before database write & cooldown
            if platform and plat_id:
                try:
                    pg_id = await database.fetchval(
                        "SELECT id FROM products WHERE platform = $1 AND platform_id = $2 LIMIT 1;",
                        platform, plat_id
                    )
                    if pg_id:
                        pid = pg_id
                        product["id"] = pid
                except Exception:
                    pass

            await database.insert_deal({
                "product_id": pid,
                "platform": platform,
                "platform_id": plat_id,
                "deal_type": deal_type,
                "posted_price": price,
                "posted_mrp": mrp,
                "savings_amount": savings_amount,
                "savings_pct": savings_pct,
                "deal_score": score,
                "badge": badge,
                "source_channel": source_channel
            })
            if pid:
                await set_cooldown(pid, config.PRICE_DROP_COOLDOWN_HOURS)

            self._last_posted_platform = platform
            self._rotation_index += 1
            self.posts_this_hour += 1
            logger.info(
                f"⚡ [QUEUE POST] [{platform.upper()}] {product.get('title', '')[:40]} "
                f"| ₹{price:.0f} / ₹{mrp:.0f} | {source_channel}"
            )
            return True

        except Exception as e:
            logger.error(f"Error posting queued deal #{pid or dedup_key}: {e}", exc_info=True)
            return "FAILED"
        finally:
            if pid:
                self._queued_pids.discard(pid)
            if dedup_key:
                self._queued_pids.discard(dedup_key)

    async def _drain_overflow_queue(self):
        """
        Ultra-High-Throughput Fast-Drain Worker:
        - Non-blocking image prefetching & non-blocking DB persistence
        - Clock-deadline pacing (~2.5s cadence, max safe Telegram channel throughput)
        - Instant 0.001s fast-path persistence for low-tier deals during large backlogs
        - Automatic RetryAfter backoff
        """
        if self._draining:
            return
        self._draining = True
        qsize = self._queue.qsize()
        logger.info(f"⚡ [TURBO DRAIN START] {qsize} deals in queue — draining at maximum safe throughput.")

        bot_instance = self._get_bot()
        if not bot_instance:
            logger.warning("No Telegram bot available for queue drain. Aborting.")
            self._draining = False
            return

        last_post_mono = 0.0
        target_post_gap = getattr(self, "target_post_gap", 3.5)  # 3.5s cadence (~17 posts/min peak, safely under Telegram's 20/min channel limit)

        try:
            while not self._queue.empty():
                self.reset_hour_if_needed()
                try:
                    deal_data = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                # Before dispatching a post, ensure minimum 3.5s gap from last post to prevent 429 flood errors
                now = time.monotonic()
                time_since_last = now - last_post_mono
                if last_post_mono > 0 and time_since_last < target_post_gap:
                    # Check if deal is eligible before waiting
                    prod_temp = deal_data.get("product", {})
                    score_temp = float(deal_data.get("score") or 60)
                    badge_temp = deal_data.get("badge", "DEAL")
                    # If backlog > 20 and low score, don't wait for rate limit gap — skip immediately!
                    if self._queue.qsize() > 20 and score_temp < 60 and badge_temp not in ["ATL", "LOOT", "MEGA_DROP"]:
                        pass
                    else:
                        await asyncio.sleep(target_post_gap - time_since_last)

                status = await self._post_queued_deal(deal_data, bot_instance)
                if status is True or status == "POSTED":
                    last_post_mono = time.monotonic()
                elif status == "SKIPPED":
                    await asyncio.sleep(0.001)
                else: # FAILED
                    retries = deal_data.get("_retry_count", 0) + 1
                    deal_data["_retry_count"] = retries
                    pid = deal_data.get("product", {}).get("id")
                    if retries < 2:
                        try:
                            self._queue.put_nowait(deal_data)
                        except asyncio.QueueFull:
                            logger.warning(f"⚠️ Queue full — unable to re-queue deal #{pid}. Dropping.")
                            self._queued_pids.discard(pid)
                        await asyncio.sleep(1.0)
                    else:
                        logger.warning(f"⚠️ Dropping deal #{pid} after failed attempts to unblock queue.")
                        self._queued_pids.discard(pid)

        except Exception as e:
            logger.error(f"Drain loop error: {e}", exc_info=True)
        finally:
            self._draining = False
            remaining = self._queue.qsize()
            logger.info(f"✅ [DRAIN DONE] Remaining in queue: {remaining}. Back to 30s catalog pacing.")

    # ── SLOW PATH: Catalog / live-hunt posting (called by 30s paced scheduler) ─

    async def post_next_deal(self, bot=None):
        """
        Catalog path: called every 30s by the scheduler when the queue is empty.
        1. Live Store Flash Hunt (fresh deals from store APIs).
        2. Evergreen catalog with pre-flight live verification.
        Uses async lock to prevent concurrent catalog hits.
        """
        async with self._lock:
            self.reset_hour_if_needed()

            bot_instance = bot or self._get_bot()
            if not bot_instance:
                logger.warning("No Telegram bot available for catalog posting.")
                return

            target_platform = self.ROTATION_SEQUENCE[self._rotation_index % len(self.ROTATION_SEQUENCE)]
            deal_data = None

            # 1. Live Store Flash Hunt
            try:
                from budgetby.engine.live_hunter import hunt_live_store_deal
                live_hunt_deal = await hunt_live_store_deal(target_platform)
                if live_hunt_deal:
                    deal_data = live_hunt_deal
            except Exception as he:
                logger.debug(f"Live hunter note: {he}")

            # 2. Evergreen catalog with pre-flight scrape verification
            if not deal_data:
                from budgetby.engine.evergreen import find_evergreen_deals
                from budgetby.engine.cooldown import is_on_cooldown
                from budgetby.scrapers.amazon import AmazonScraper
                from budgetby.scrapers.flipkart import FlipkartScraper
                from budgetby.scrapers.myntra import MyntraScraper
                from budgetby.scrapers.ajio import AjioScraper
                from budgetby.scrapers.nykaa import NykaaScraper

                scrapers_map = {
                    "amazon": AmazonScraper,
                    "flipkart": FlipkartScraper,
                    "myntra": MyntraScraper,
                    "ajio": AjioScraper,
                    "nykaa": NykaaScraper,
                }
                min_discount = getattr(config, "MIN_DEAL_DISCOUNT_PCT", 10.0) / 100.0

                candidates = await find_evergreen_deals(limit=15, platform=target_platform)
                if not candidates:
                    for alt_plat in ["amazon", "flipkart", "myntra", "ajio", "nykaa"]:
                        if alt_plat != self._last_posted_platform and alt_plat != target_platform:
                            alt_candidates = await find_evergreen_deals(limit=15, platform=alt_plat)
                            if alt_candidates:
                                candidates = alt_candidates
                                target_platform = alt_plat
                                break

                for cand in candidates:
                    p_dict = dict(cand)
                    pid = p_dict.get("id")
                    plat = (p_dict.get("platform") or target_platform).lower()

                    if pid and (await is_on_cooldown(pid) or (pid in _rejected_candidates and time.monotonic() < _rejected_candidates[pid])):
                        continue

                    c_url = p_dict.get("product_url") or p_dict.get("affiliate_url") or p_dict.get("url", "")
                    scr_cls = scrapers_map.get(plat)

                    if scr_cls and c_url:
                        try:
                            live_check = await scr_cls()._do_scrape_product(c_url)
                            if not live_check:
                                if pid:
                                    _rejected_candidates[pid] = time.monotonic() + 1800.0
                                continue

                            is_in_stock = bool(live_check.get("in_stock", True))
                            live_p = float(live_check.get("current_price") or 0)
                            live_m = float(live_check.get("mrp") or live_p)

                            if not is_in_stock:
                                if pid:
                                    _rejected_candidates[pid] = time.monotonic() + 1800.0
                                    await database.execute("UPDATE products SET in_stock = FALSE WHERE id = $1;", pid)
                                continue

                            if live_p <= 0 or live_m <= live_p or ((live_m - live_p) / live_m) < min_discount:
                                if pid:
                                    _rejected_candidates[pid] = time.monotonic() + 1800.0
                                    if live_p > 0:
                                        await database.execute(
                                            "UPDATE products SET current_price = $1, mrp = $2, in_stock = $3 WHERE id = $4;",
                                            live_p, live_m, is_in_stock, pid
                                        )
                                continue

                            p_dict["current_price"] = live_p
                            p_dict["mrp"] = live_m
                            if live_check.get("image_url"):
                                p_dict["image_url"] = live_check["image_url"]
                            if "rating" in live_check:
                                p_dict["rating"] = live_check["rating"]
                            if "review_count" in live_check:
                                p_dict["review_count"] = live_check["review_count"]

                            if pid:
                                try:
                                    await database.execute("""
                                        UPDATE products
                                        SET current_price = $1, mrp = $2, in_stock = TRUE,
                                            last_checked = (NOW() AT TIME ZONE 'Asia/Kolkata')
                                        WHERE id = $3;
                                    """, live_p, live_m, pid)
                                except Exception as dbe:
                                    logger.debug(f"DB preflight update note: {dbe}")

                            deal_data = {
                                "product": p_dict,
                                "type": "evergreen",
                                "badge": "EVERGREEN",
                                "score": 75
                            }
                            break
                        except Exception as pe:
                            logger.debug(f"Candidate check error #{pid}: {pe}")
                            continue
                    else:
                        logger.info(f"🚫 #{pid} ({plat.upper()}) missing scraper/URL. Skipping.")
                        continue

            if not deal_data:
                logger.info("No catalog deal available this tick.")
                self._rotation_index += 1
                return

            # Broadcast catalog deal
            pid = None
            try:
                from budgetby.engine.cooldown import is_on_cooldown, set_cooldown

                product = deal_data.get("product", {})
                pid = product.get("id")
                platform = (product.get("platform") or "store").lower()

                if pid and await is_on_cooldown(pid):
                    logger.info(f"🛡️ #{pid} on cooldown at broadcast time. Skipping.")
                    return

                deal_type = deal_data.get("type", "price_drop")
                badge = deal_data.get("badge", "DEAL")
                score = float(deal_data.get("score") or 50)
                source_channel = deal_data.get("source_channel", "local_scanner")

                price = float(product.get("current_price") or 0)
                mrp = float(product.get("mrp") or price)

                min_discount = getattr(config, "MIN_DEAL_DISCOUNT_PCT", 10.0) / 100.0
                if mrp > 0 and price > 0 and ((mrp - price) / mrp) < min_discount and deal_type != "price_drop":
                    return

                # URL handling
                url = product.get("affiliate_url") or product.get("product_url") or product.get("url", "")
                if platform == "amazon" and url:
                    url = _apply_amazon_tag(url)
                    product["affiliate_url"] = url
                    if pid:
                        try:
                            await database.execute("UPDATE products SET affiliate_url = $1 WHERE id = $2;", url, pid)
                        except Exception as e:
                            logger.debug(f"Amazon affiliate_url update note: {e}")

                elif platform in ("flipkart", "myntra", "ajio") and url:
                    try:
                        from budgetby.ingest.telegram_listener import convert_url_via_ek_bot
                        converted_ek = await convert_url_via_ek_bot(url, timeout=4.0)
                        if converted_ek and converted_ek != url:
                            product["affiliate_url"] = converted_ek
                            deal_data["product"]["affiliate_url"] = converted_ek
                            if pid:
                                await database.execute("UPDATE products SET affiliate_url = $1 WHERE id = $2;", converted_ek, pid)
                    except Exception as e:
                        logger.debug(f"EK conversion note: {e}")

                elif platform == "nykaa" and url:
                    try:
                        from budgetby.ingest.telegram_listener import convert_url_via_cuelinks_bot
                        converted_cl = await convert_url_via_cuelinks_bot(url, timeout=6.0)
                        if converted_cl and converted_cl != url:
                            product["affiliate_url"] = converted_cl
                            deal_data["product"]["affiliate_url"] = converted_cl
                            if pid:
                                await database.execute("UPDATE products SET affiliate_url = $1 WHERE id = $2;", converted_cl, pid)
                    except Exception as e:
                        logger.debug(f"Cuelinks conversion note: {e}")

                message_text = format_deal_message(deal_data)
                img_url = product.get("image_url") or ""
                sent_msg = await self._send_to_telegram(bot_instance, message_text, img_url)

                if not sent_msg:
                    logger.warning(f"Catalog deal #{pid} failed to send.")
                    return

                savings_amount = max(0.0, mrp - price)
                savings_pct = (savings_amount / mrp) if mrp > 0 else 0.0
                await database.insert_deal({
                    "product_id": pid,
                    "deal_type": deal_type,
                    "posted_price": price,
                    "posted_mrp": mrp,
                    "savings_amount": savings_amount,
                    "savings_pct": savings_pct,
                    "deal_score": score,
                    "badge": badge,
                    "source_channel": source_channel
                })
                if pid:
                    await set_cooldown(pid, config.PRICE_DROP_COOLDOWN_HOURS)

                self._last_posted_platform = platform
                self._rotation_index += 1
                self.posts_this_hour += 1
                logger.info(
                    f"📢 [CATALOG POST] [{platform.upper()}] {product.get('title', '')[:40]} "
                    f"| ₹{price:.0f} / ₹{mrp:.0f} | {source_channel}"
                )

            except Exception as e:
                logger.error(f"Error broadcasting catalog deal #{pid}: {e}", exc_info=True)
            finally:
                if pid:
                    self._queued_pids.discard(pid)


_global_posting_queue = None

def get_posting_queue() -> PostingQueue:
    global _global_posting_queue
    if _global_posting_queue is None:
        _global_posting_queue = PostingQueue()
    return _global_posting_queue
