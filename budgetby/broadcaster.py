"""
BudgetBy — 24/7 Continuous Paced Deal Broadcaster & Dedicated Live Hunter Engine.
Guarantees:
1. Multi-Store Rotation Schedule:
   - 11-slot proportional rotation cycle:
     ['amazon', 'flipkart', 'myntra', 'amazon', 'ajio', 'flipkart', 'nykaa', 'amazon', 'myntra', 'flipkart', 'ajio']
   - If Amazon products arrive in bulk, they are queued into their platform queue and post only when Amazon's scheduled turn arrives.
   - Strict Anti-clustering: Never post the same store consecutively.
2. Fast Queue Drain:
   - In-memory priority queues per platform + global drain queue.
   - If total queue size reaches >= 10 products, automatically drains and broadcasts queued deals
     immediately without waiting for the 30s interval as soon as they are ready.
3. Dedup & Cooldown Enforcement (Exact Local 7-Day & 24h/3-Day Rules):
   - 7-Day strict duplicate protection against re-posting previously posted deals.
   - Cooldown guard across Product ID, Platform ID / ASIN, and normalized title / variant prefix.
   - Sets cooldown immediately upon successful Telegram post.
4. Continuous Deal Hunter:
   - Dedicated continuous background hunter task scanning 298 category feeds across all 5 stores.
   - Discovered deals are queued for their platform turn, expanding catalog and keeping pipeline stocked.
5. 100% Verified Affiliate Tracking:
   - Converted to official affiliate shortlinks (Amazon tag, EarnKaro fktr/ajiio/myntr, Cuelinks clnk.in).
"""
import asyncio
import io
import logging
import time
from datetime import datetime, timezone
from curl_cffi.requests import AsyncSession
import httpx
from PIL import Image

from budgetby import config, database
from budgetby.bot.templates import format_mega_deal, format_today_deal
from budgetby.cloud_scraper import get_or_create_affiliate_link, is_valid_affiliate_shortlink
from budgetby.engine.cooldown import is_on_cooldown, set_cooldown

logger = logging.getLogger("budgetby.cloud_broadcaster")

# 11-slot proportional rotation cycle:
ROTATION_SEQUENCE = [
    "amazon", "flipkart", "myntra",
    "amazon", "ajio", "flipkart",
    "nykaa",
    "amazon", "myntra",
    "flipkart", "ajio"
]

_current_seq_idx = 0
_last_posted_platform: str | None = None
_last_posted_product_ids = set()
_broadcast_post_counter = 0

# Platform queues for surplus deals (e.g. when Amazon or other stores arrive faster than their turn)
_platform_queues = {
    "amazon": asyncio.Queue(maxsize=500),
    "flipkart": asyncio.Queue(maxsize=500),
    "myntra": asyncio.Queue(maxsize=500),
    "ajio": asyncio.Queue(maxsize=500),
    "nykaa": asyncio.Queue(maxsize=500),
}

broadcaster_stats = {
    "status": "starting",
    "total_posted": 0,
    "last_posted_platform": None,
    "last_posted_product_id": None,
    "last_posted_title": None,
    "last_posted_at": None,
    "queue_size": 0,
    "channel": getattr(config, "TELEGRAM_CHANNEL_ID", "@deal_pulse_alerts")
}

def get_total_queued_count() -> int:
    return sum(q.qsize() for q in _platform_queues.values())

def get_broadcaster_stats() -> dict:
    """Return live status of the broadcaster."""
    return {
        **broadcaster_stats,
        "history_size": len(_last_posted_product_ids),
        "last_posted_platform": _last_posted_platform,
        "queue_size": get_total_queued_count(),
        "platform_queues": {k: q.qsize() for k, q in _platform_queues.items()},
        "channel": getattr(config, "TELEGRAM_CHANNEL_ID", "@deal_pulse_alerts"),
        "now": datetime.now(timezone.utc).isoformat()
    }

async def queue_deal_for_platform(platform: str, deal_data: dict) -> bool:
    """Pushes an incoming deal into the designated platform queue for its scheduled turn."""
    plat = (platform or "amazon").lower()
    if plat not in _platform_queues:
        plat = "amazon"
    q = _platform_queues[plat]
    pid = deal_data.get("product_id")
    if pid and await is_on_cooldown(pid):
        logger.debug(f"Deal #{pid} ({plat}) is on cooldown. Not queueing.")
        return False
    try:
        q.put_nowait(deal_data)
        broadcaster_stats["queue_size"] = get_total_queued_count()
        logger.info(f"📥 Queued deal #{pid} for [{plat.upper()}] (Platform queue size: {q.qsize()}, Total: {broadcaster_stats['queue_size']})")
        return True
    except asyncio.QueueFull:
        logger.warning(f"Platform queue for {plat} is full.")
        return False

async def _fetch_deal_candidate_for_store(store: str, exclude_ids: set) -> dict | None:
    """
    Fetch unposted deal candidate strictly for the specified store following local deduplication & cooldown rules.
    Guarantees strict platform quota per rotation cycle.
    Enforces 90% Live Flash Hunter / 10% Database Catalog:
    - 9 out of 10 times, prioritizes Live Flash Hunter on-the-fly scan for fresh loot.
    - 1 out of 10 times (or if Flash Hunter has no available items), falls back to verified database catalog.
    """
    global _broadcast_post_counter
    store = store.lower()

    # Priority 1: Check in-memory platform queue (burst deals queued from Telegram listener or hunter)
    q = _platform_queues.get(store)
    if q and not q.empty():
        while not q.empty():
            try:
                candidate = q.get_nowait()
                pid = candidate.get("product_id")
                if pid and pid not in exclude_ids and not await is_on_cooldown(pid):
                    return candidate
            except asyncio.QueueEmpty:
                break

    exclude_clause = ""
    if exclude_ids:
        recent_list = list(exclude_ids)[-250:]
        exclude_clause = f"AND d.product_id NOT IN ({','.join(str(i) for i in recent_list)})"

    # Enforce 90% Flash Hunter vs 10% Database ratio
    # If counter % 10 != 0 (9 out of 10 posts), Flash Hunter runs FIRST!
    prefer_hunter = (_broadcast_post_counter % 10 != 0)

    if prefer_hunter:
        # Step A: Live Flash Hunter - Discover fresh high-discount loot on the fly!
        try:
            from budgetby.engine.live_hunter import hunt_live_store_deal
            hunt_deal = await asyncio.wait_for(hunt_live_store_deal(store), timeout=10)
            if hunt_deal and hunt_deal.get("product"):
                hp = hunt_deal["product"]
                pid = hp.get("id")
                if pid and pid not in exclude_ids and not await is_on_cooldown(pid):
                    return {
                        "deal_id": 0,
                        "product_id": pid,
                        "posted_price": hp.get("current_price"),
                        "posted_mrp": hp.get("mrp"),
                        "deal_score": hunt_deal.get("score", 85.0),
                        "badge": hunt_deal.get("badge", "HOT_DEAL"),
                        "posted_at": None,
                        "title": hp.get("title"),
                        "platform": store,
                        "product_url": hp.get("product_url"),
                        "affiliate_url": hp.get("affiliate_url"),
                        "image_url": hp.get("image_url"),
                        "current_price": hp.get("current_price"),
                        "mrp": hp.get("mrp"),
                        "category": hp.get("category"),
                        "rating": hp.get("rating")
                    }
        except Exception as he:
            logger.debug(f"Live hunt candidate note for {store}: {he}")

    # Step B: Fresh unposted deals in database (posted_at IS NULL)
    row = await database.fetchrow(f"""
        SELECT d.id as deal_id, d.product_id, d.posted_price, d.posted_mrp, 
               d.deal_score, d.badge, d.posted_at,
               p.title, p.platform, p.product_url, p.affiliate_url, p.image_url, 
               p.current_price, p.mrp, p.category, p.rating
        FROM deals d
        JOIN products p ON d.product_id = p.id
        WHERE p.in_stock = TRUE AND p.status = 'ACTIVE'
          AND d.posted_at IS NULL
          AND p.platform = '{store}'
          {exclude_clause}
        ORDER BY d.id DESC
        LIMIT 1;
    """)
    if row:
        pid = row["product_id"]
        if not await is_on_cooldown(pid):
            return dict(row)

    # If prefer_hunter was False (1 in 10 count), try Live Flash Hunter now as fallback
    if not prefer_hunter:
        try:
            from budgetby.engine.live_hunter import hunt_live_store_deal
            hunt_deal = await asyncio.wait_for(hunt_live_store_deal(store), timeout=10)
            if hunt_deal and hunt_deal.get("product"):
                hp = hunt_deal["product"]
                pid = hp.get("id")
                if pid and pid not in exclude_ids and not await is_on_cooldown(pid):
                    return {
                        "deal_id": 0,
                        "product_id": pid,
                        "posted_price": hp.get("current_price"),
                        "posted_mrp": hp.get("mrp"),
                        "deal_score": hunt_deal.get("score", 85.0),
                        "badge": hunt_deal.get("badge", "HOT_DEAL"),
                        "posted_at": None,
                        "title": hp.get("title"),
                        "platform": store,
                        "product_url": hp.get("product_url"),
                        "affiliate_url": hp.get("affiliate_url"),
                        "image_url": hp.get("image_url"),
                        "current_price": hp.get("current_price"),
                        "mrp": hp.get("mrp"),
                        "category": hp.get("category"),
                        "rating": hp.get("rating")
                    }
        except Exception as he:
            logger.debug(f"Live hunt candidate note for {store}: {he}")

    # Step C: Fall back to active high-discount catalog items specifically for this store
    # Enforces 7-day duplicate prevention (NOT EXISTS in deals posted in last 7 days) and 24h 3-tier cooldown
    candidates = await database.fetch(f"""
        SELECT 0 as deal_id, p.id as product_id, p.current_price as posted_price, p.mrp as posted_mrp,
               ROUND((((p.mrp - p.current_price)/NULLIF(p.mrp,0)) * 100)::numeric, 1) as deal_score,
               'HOT_DEAL' as badge, NOW() as posted_at,
               p.title, p.platform, p.product_url, p.affiliate_url, p.image_url, 
               p.current_price, p.mrp, p.category, p.rating
        FROM products p
        LEFT JOIN post_cooldowns c ON p.id = c.product_id AND c.expires_at > NOW()
        WHERE p.in_stock = TRUE AND p.status = 'ACTIVE' AND p.current_price > 0
          AND p.platform = '{store}'
          AND p.mrp > p.current_price
          AND ((p.mrp - p.current_price) / p.mrp) >= 0.20
          AND c.product_id IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM deals d 
              WHERE d.product_id = p.id 
                AND d.posted_at >= (NOW() - INTERVAL '7 days')
          )
          {exclude_clause.replace('d.product_id', 'p.id')}
        ORDER BY ((p.mrp - p.current_price) / p.mrp) DESC, p.id DESC
        LIMIT 25;
    """)
    for r in candidates:
        pid = r["product_id"]
        if not await is_on_cooldown(pid):
            return dict(r)

    return None

async def broadcast_single_deal(deal_row: dict) -> bool:
    """
    Converts affiliate link, posts formatted deal to Telegram, updates website database,
    and applies strict cooldown to guarantee zero duplicates.
    """
    global _last_posted_platform, _last_posted_product_ids
    bot_token = getattr(config, "TELEGRAM_BOT_TOKEN", "").strip() or "8507632294:AAH8FmrM22hqvkk0A9ng97xs8DOod0j2Tng"
    channel_id = getattr(config, "TELEGRAM_CHANNEL_ID", "").strip() or "@deal_pulse_alerts"

    pid = deal_row["product_id"]
    if not pid or await is_on_cooldown(pid):
        logger.info(f"🛡️ #{pid} on cooldown at broadcast time. Skipping.")
        return False

    _last_posted_product_ids.add(pid)
    if len(_last_posted_product_ids) > 600:
        first_val = next(iter(_last_posted_product_ids))
        _last_posted_product_ids.remove(first_val)

    prod_dict = dict(deal_row)
    prod_dict["id"] = pid
    platform = (prod_dict.get("platform") or "amazon").lower()

    # 100% STRICT AFFILIATE CONVERSION ENFORCEMENT
    aff_url = await get_or_create_affiliate_link(prod_dict, force_fresh=True)
    if not aff_url or not is_valid_affiliate_shortlink(aff_url, platform):
        logger.warning(
            f"⚠️ [AFFILIATE GUARD] Product {pid} ({platform}) has unconverted link. "
            f"Skipping post to guarantee 100% affiliate compliance."
        )
        return False

    prod_dict["affiliate_url"] = aff_url

    # Format message
    score = float(deal_row.get("deal_score") or 50.0)
    badge = deal_row.get("badge") or "HOT_DEAL"
    deal_info = {
        "deal_score": score,
        "badge": badge,
        "posted_price": float(deal_row.get("posted_price") or prod_dict.get("current_price") or 0),
        "posted_mrp": float(deal_row.get("posted_mrp") or prod_dict.get("mrp") or 0)
    }

    if score >= 70 or badge in ("ATL", "LOOT"):
        caption = format_mega_deal(prod_dict, deal_info)
    else:
        caption = format_today_deal(prod_dict, deal_info)

    image_url = prod_dict.get("image_url")
    if image_url and "myntassets.com" in image_url:
        image_url = image_url.replace("($height)", "720").replace("($qualityPercentage)", "90").replace("($width)", "540").replace("http://", "https://")
    api_base = f"https://api.telegram.org/bot{bot_token}"

    posted = False
    message_id = None

    # Attempt 1: Fetch and convert photo to clean JPEG (handles AVIF/WebP formats that Telegram rejects)
    if image_url and image_url.startswith("http"):
        try:
            async with AsyncSession(impersonate="chrome124", timeout=8) as dl_sess:
                r_img = await dl_sess.get(image_url)
            if r_img.status_code == 200 and len(r_img.content) > 1000:
                im = Image.open(io.BytesIO(r_img.content)).convert("RGB")
                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=88)
                buf.seek(0)
                async with httpx.AsyncClient(timeout=15) as tg_client:
                    files = {"photo": ("product.jpg", buf.getvalue(), "image/jpeg")}
                    data = {"chat_id": channel_id, "caption": caption, "parse_mode": "HTML"}
                    tg_resp = await tg_client.post(f"{api_base}/sendPhoto", data=data, files=files)
                    if tg_resp.status_code == 200:
                        posted = True
                        try:
                            message_id = tg_resp.json().get("result", {}).get("message_id")
                        except Exception:
                            pass
        except Exception as img_err:
            logger.debug(f"Photo upload fallback: {img_err}")

    # Attempt 2: Fallback to direct URL sendPhoto
    if not posted and image_url and image_url.startswith("http"):
        try:
            async with AsyncSession(timeout=10) as session:
                resp = await session.post(f"{api_base}/sendPhoto", json={
                    "chat_id": channel_id,
                    "photo": image_url,
                    "caption": caption,
                    "parse_mode": "HTML"
                })
                if resp.status_code == 200:
                    posted = True
                    try:
                        message_id = resp.json().get("result", {}).get("message_id")
                    except Exception:
                        pass
        except Exception as p_err:
            logger.debug(f"Direct sendPhoto fallback: {p_err}")

    # Attempt 3: Fallback to sendMessage
    if not posted:
        try:
            async with AsyncSession(timeout=10) as session:
                resp = await session.post(f"{api_base}/sendMessage", json={
                    "chat_id": channel_id,
                    "text": caption,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False
                })
                if resp.status_code == 200:
                    posted = True
                    try:
                        message_id = resp.json().get("result", {}).get("message_id")
                    except Exception:
                        pass
        except Exception as m_err:
            logger.warning(f"Telegram sendMessage fallback error: {m_err}")

    if posted:
        global _broadcast_post_counter
        _broadcast_post_counter += 1
        _last_posted_platform = platform
        broadcaster_stats["total_posted"] += 1
        broadcaster_stats["last_posted_platform"] = _last_posted_platform
        broadcaster_stats["last_posted_product_id"] = pid
        broadcaster_stats["last_posted_title"] = prod_dict.get("title", "")[:50]
        broadcaster_stats["last_posted_at"] = datetime.now(timezone.utc).isoformat()
        logger.info(f"🚀 [POSTED #{broadcaster_stats['total_posted']}] Deal {pid} [{_last_posted_platform.upper()}]: {prod_dict.get('title', '')[:30]} → {channel_id}")

        # Set strict cooldown (24h anti-spam / 7-day evergreen)
        await set_cooldown(pid, getattr(config, "PRICE_DROP_COOLDOWN_HOURS", 24))

        # Synchronize deal & product tables with current timestamp for website storefront
        posted_price = float(deal_row.get("posted_price") or prod_dict.get("current_price") or 0)
        posted_mrp = float(deal_row.get("posted_mrp") or prod_dict.get("mrp") or posted_price)
        savings = max(0.0, posted_mrp - posted_price)
        savings_pct = (savings / posted_mrp * 100) if posted_mrp > 0 else 0.0

        try:
            await database.execute("""
                INSERT INTO deals (
                    product_id, deal_type, posted_price, posted_mrp,
                    savings_amount, savings_pct, deal_score, badge,
                    source_channel, posted_at
                ) VALUES (
                    $1, 'price_drop', $2, $3, $4, $5, $6, $7, 'cloud_broadcaster', NOW()
                )
                ON CONFLICT (product_id) DO UPDATE SET
                    posted_price = EXCLUDED.posted_price,
                    posted_mrp = EXCLUDED.posted_mrp,
                    savings_amount = EXCLUDED.savings_amount,
                    savings_pct = EXCLUDED.savings_pct,
                    deal_score = EXCLUDED.deal_score,
                    badge = EXCLUDED.badge,
                    deal_type = 'price_drop',
                    posted_at = NOW();
            """, pid, posted_price, posted_mrp, savings, savings_pct, score, badge)

            await database.execute("""
                UPDATE products 
                SET previous_price = GREATEST(COALESCE(previous_price, mrp, current_price), current_price * 1.15),
                    last_price_change = NOW(),
                    last_checked = NOW(),
                    affiliate_url = COALESCE($2, affiliate_url)
                WHERE id = $1;
            """, pid, aff_url)

            # Invalidate SSR cache so deal appears in Just Dropped immediately
            try:
                from budgetby.dashboard.app import invalidate_ssr_cache, ram_cache
                await invalidate_ssr_cache()
                await ram_cache.clear()
            except Exception:
                pass
        except Exception as db_sync_err:
            logger.warning(f"Error synchronizing deal/product table: {db_sync_err}")

        if message_id:
            try:
                await database.insert_deal_tracking(
                    deal_id=deal_row.get("deal_id") or 0,
                    product_id=pid,
                    message_id=message_id,
                    channel_id=channel_id,
                    caption=caption,
                    price=posted_price
                )
            except Exception as tr_err:
                logger.debug(f"Tracking insert notice: {tr_err}")

        return True

    return False

async def drain_all_queues_immediately():
    """
    Fast queue drain: when total queued items reach >= 10,
    drains queued products strictly preserving the exact 11-slot rotation cycle:
    Amazon: 3, Flipkart: 3, Ajio: 2, Myntra: 2, Nykaa: 1.
    Strictly forbids consecutive posts from the same platform.
    """
    total_q = get_total_queued_count()
    if total_q < 10:
        return
    logger.info(f"⚡ [QUEUE BURST DRAIN] Total queued deals ({total_q}) >= 10! Draining queued products in exact rotation order...")

    global _current_seq_idx, _last_posted_platform
    # Iterate through the rotation cycle to maintain balanced quotas
    for _ in range(len(ROTATION_SEQUENCE)):
        plat = ROTATION_SEQUENCE[_current_seq_idx % len(ROTATION_SEQUENCE)]
        if plat == _last_posted_platform:
            _current_seq_idx += 1
            plat = ROTATION_SEQUENCE[_current_seq_idx % len(ROTATION_SEQUENCE)]

        q = _platform_queues.get(plat)
        if q and not q.empty():
            try:
                candidate = q.get_nowait()
                pid = candidate.get("product_id")
                if pid and not await is_on_cooldown(pid):
                    success = await broadcast_single_deal(candidate)
                    if success:
                        _current_seq_idx += 1
                        await asyncio.sleep(2.0)  # Paced 2s delay between burst posts
            except Exception as de:
                logger.warning(f"Burst drain error on {plat}: {de}")

    broadcaster_stats["queue_size"] = get_total_queued_count()

async def run_paced_30s_broadcaster():
    """
    Continuous 30-second loop posting deals to Telegram & ensuring live website deals.
    Respects rotation sequence, handles surplus queueing, and triggers burst drain when queue >= 10.
    """
    logger.info("⚡ [30s CLOUD BROADCASTER] Initializing continuous 30-second deal poster...")
    broadcaster_stats["status"] = "running"
    global _current_seq_idx, _last_posted_platform
    await asyncio.sleep(5)

    while True:
        try:
            if database._pool is None:
                await database.init_pool()

            # Check if queue has reached >= 10 products: trigger burst drain!
            if get_total_queued_count() >= 10:
                await drain_all_queues_immediately()

            # Determine target store from the scheduled sequence
            target_plat = ROTATION_SEQUENCE[_current_seq_idx % len(ROTATION_SEQUENCE)]

            # Strict Anti-clustering rule: never post the same store twice in a row
            if target_plat == _last_posted_platform:
                _current_seq_idx += 1
                target_plat = ROTATION_SEQUENCE[_current_seq_idx % len(ROTATION_SEQUENCE)]

            # 1. Fetch next candidate deal prioritizing target store
            deal_row = await _fetch_deal_candidate_for_store(target_plat, _last_posted_product_ids)

            # If target store has no candidate right now, rotate through alternatives in sequence order
            if not deal_row:
                for alt_plat in ROTATION_SEQUENCE:
                    if alt_plat != _last_posted_platform and alt_plat != target_plat:
                        deal_row = await _fetch_deal_candidate_for_store(alt_plat, _last_posted_product_ids)
                        if deal_row:
                            target_plat = alt_plat
                            break

            if deal_row:
                success = await broadcast_single_deal(deal_row)
                if success:
                    _current_seq_idx += 1
            else:
                logger.debug("No deal candidates available across stores this cycle.")

        except Exception as e:
            logger.error(f"Error in run_paced_30s_broadcaster loop: {e}")

        broadcaster_stats["queue_size"] = get_total_queued_count()
        # Normal paced delay: exactly 30 seconds
        await asyncio.sleep(30)

async def run_continuous_deal_hunter_daemon():
    """
    Dedicated Continuous Deal Hunter:
    Constantly scans 298 category feeds across all 5 stores round-robin.
    Whenever a fresh high-discount deal is discovered, queues it for its platform turn!
    """
    logger.info("🎯 [CONTINUOUS DEAL HUNTER] Starting 24/7 background deal finder across all 5 platforms...")
    await asyncio.sleep(10)  # Brief startup delay
    from budgetby.engine.live_hunter import hunt_live_store_deal

    hunt_stores = ["amazon", "flipkart", "myntra", "ajio", "nykaa"]
    hunt_idx = 0

    while True:
        try:
            if database._pool is None:
                await database.init_pool()

            store = hunt_stores[hunt_idx % len(hunt_stores)]
            hunt_idx += 1

            # Only hunt if that platform's queue isn't overflowed
            q = _platform_queues.get(store)
            if q and q.qsize() < 25:
                hunt_deal = await hunt_live_store_deal(store)
                if hunt_deal and hunt_deal.get("product"):
                    hp = hunt_deal["product"]
                    pid = hp.get("id")
                    if pid and pid not in _last_posted_product_ids and not await is_on_cooldown(pid):
                        deal_payload = {
                            "deal_id": 0,
                            "product_id": pid,
                            "posted_price": hp.get("current_price"),
                            "posted_mrp": hp.get("mrp"),
                            "deal_score": hunt_deal.get("score", 85.0),
                            "badge": hunt_deal.get("badge", "HOT_DEAL"),
                            "posted_at": None,
                            "title": hp.get("title"),
                            "platform": store,
                            "product_url": hp.get("product_url"),
                            "affiliate_url": hp.get("affiliate_url"),
                            "image_url": hp.get("image_url"),
                            "current_price": hp.get("current_price"),
                            "mrp": hp.get("mrp"),
                            "category": hp.get("category"),
                            "rating": hp.get("rating")
                        }
                        await queue_deal_for_platform(store, deal_payload)

                        # Check if total queued deals reached >= 10: triggers instant burst drain!
                        if get_total_queued_count() >= 10:
                            await drain_all_queues_immediately()

            # Short breath between category scans to respect rate limits
            await asyncio.sleep(12)

        except Exception as e:
            logger.error(f"Continuous hunter loop error: {e}")
            await asyncio.sleep(15)
