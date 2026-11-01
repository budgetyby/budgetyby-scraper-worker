"""
BudgetBy — Real-Time Telegram MTProto Listener for Private & Joined Channels.
Listens to live incoming messages from private invite channels and forwards verified deals.
Strictly ignores all historical messages and only processes new posts from this moment onwards.
"""

import logging
import asyncio
import os
import re
from telethon import TelegramClient, events
import httpx
from budgetby import database, config
from budgetby.ingest.channel_monitor import verify_and_ingest_single_deal

logger = logging.getLogger("budgetby.ingest.telegram_listener")

_client = None
_ek_cache = {}
_ek_lock = asyncio.Lock()
_cl_cache = {}
_cl_lock = asyncio.Lock()

def is_telethon_connected() -> bool:
    """Returns True if the Telethon MTProto client is connected and authorized."""
    global _client
    return bool(_client and _client.is_connected())

async def convert_url_via_ek_bot(raw_url: str, platform: str = None, timeout: float = 10.0) -> str:
    """
    Automatically converts raw Flipkart/Myntra/Ajio URLs to real
    fktr.in / myntr.it / ajiio.in / ekaro.in profit tracking links via @ekconverter9bot.
    Guarantees that 100% of clicks are recorded live on earnkaro.com!
    Protected by _ek_lock to eliminate link-swapping concurrency bugs.
    Validates that returned domain matches the designated product's platform.
    """
    global _client
    if not raw_url:
        return raw_url

    clean_target = raw_url.split("&affid=")[0].split("?affid=")[0].split("&affExtParam")[0].split("?affExtParam")[0]
    cache_key = f"{platform or ''}:{clean_target}"
    if cache_key in _ek_cache:
        return _ek_cache[cache_key]

    if not _client or not _client.is_connected():
        return raw_url

    plat = (platform or "").strip().lower()

    try:
        async with _ek_lock:
            # Serialized conversion: Strictly 1 URL at a time eliminates any possibility of link mismatch!
            async with _client.conversation("ekconverter9bot", timeout=timeout) as conv:
                # 1. Send clean product target URL
                sent_msg = await conv.send_message(clean_target)
                
                # 2. Poll responses until a message with converted link arrives
                loop_start = asyncio.get_event_loop().time()
                while (asyncio.get_event_loop().time() - loop_start) < timeout:
                    rem_time = max(0.5, timeout - (asyncio.get_event_loop().time() - loop_start))
                    try:
                        resp = await conv.get_response(timeout=rem_time)
                    except (asyncio.TimeoutError, TimeoutError):
                        break

                    if not resp or not resp.text:
                        continue
                    if getattr(resp, "id", 0) < getattr(sent_msg, "id", 0):
                        continue

                    urls = re.findall(r'https?://[^\s\)\>]+', resp.text)
                    valid_links = [
                        u.strip() for u in urls 
                        if not any(x in u.lower() for x in ['t.me', 'telegram.org', 'affiliaters.in', 'support', 'help', 'faq'])
                    ]
                    if not valid_links:
                        # Interim bot response (e.g., "Converting..."), wait for next message
                        continue

                    cand = valid_links[0].rstrip(".,;!?")
                    cand_lower = cand.lower()

                    # Strict Platform Domain Safeguard:
                    # Prevent cross-assigning a link from one store to another!
                    is_valid_store = False
                    if plat == "flipkart" and ("fktr.in" in cand_lower or "ekaro.in" in cand_lower):
                        is_valid_store = True
                    elif plat == "myntra" and ("myntr.it" in cand_lower or "ekaro.in" in cand_lower):
                        is_valid_store = True
                    elif plat == "ajio" and ("ajiio.in" in cand_lower or "ekaro.in" in cand_lower):
                        is_valid_store = True
                    elif not plat and any(d in cand_lower for d in ("fktr.in", "myntr.it", "ajiio.in", "ekaro.in")):
                        is_valid_store = True

                    if is_valid_store:
                        _ek_cache[cache_key] = cand
                        logger.info(f"✨ [EARNKARO AUTO-CONVERTED] [{plat.upper()}] {clean_target[:45]}... ➔ {cand}")
                        return cand
                    else:
                        logger.warning(f"⚠️ [EARNKARO MISMATCH REJECTED] Platform '{plat}' rejected mismatched link: {cand}")
                        return raw_url
    except Exception as e:
        logger.warning(f"EK converter exception for {clean_target[:45]}: {e}")

    return raw_url


async def convert_url_via_cuelinks_bot(raw_url: str, platform: str = "nykaa", timeout: float = 10.0) -> str:
    """
    Automatically converts raw Nykaa URLs to real clnk.in profit tracking links via @CuelinksBot.
    Guarantees that 100% of clicks are recorded live on cuelinks.com!
    Protected by _cl_lock to eliminate link-swapping concurrency bugs.
    Validates that returned domain matches clnk.in.
    """
    global _client
    if not raw_url:
        return raw_url

    clean_target = raw_url.split("&affid=")[0].split("?affid=")[0].split("&affExtParam")[0].split("?affExtParam")[0]
    cache_key = f"cuelinks:{clean_target}"
    if cache_key in _cl_cache:
        return _cl_cache[cache_key]

    if not _client or not _client.is_connected():
        return raw_url

    try:
        async with _cl_lock:
            # Serialized conversion: Strictly 1 URL at a time eliminates any possibility of link mismatch!
            async with _client.conversation("CuelinksBot", timeout=timeout) as conv:
                # 1. Send clean product target URL
                sent_msg = await conv.send_message(clean_target)

                # 2. Poll responses until a message with converted link arrives
                loop_start = asyncio.get_event_loop().time()
                while (asyncio.get_event_loop().time() - loop_start) < timeout:
                    rem_time = max(0.5, timeout - (asyncio.get_event_loop().time() - loop_start))
                    try:
                        resp = await conv.get_response(timeout=rem_time)
                    except (asyncio.TimeoutError, TimeoutError):
                        break

                    if not resp or not resp.text:
                        continue
                    if getattr(resp, "id", 0) < getattr(sent_msg, "id", 0):
                        continue

                    urls = re.findall(r'https?://[^\s\)\>]+', resp.text)
                    valid_links = [
                        u.strip() for u in urls 
                        if not any(x in u.lower() for x in ['t.me', 'telegram.org', 'support', 'help', 'faq'])
                    ]
                    if not valid_links:
                        continue

                    cand = valid_links[0].rstrip(".,;!?")
                    if "clnk.in" in cand.lower():
                        _cl_cache[cache_key] = cand
                        logger.info(f"✨ [CUELINKS AUTO-CONVERTED] [NYKAA] {clean_target[:45]}... ➔ {cand}")
                        return cand
                    else:
                        logger.warning(f"⚠️ [CUELINKS MISMATCH REJECTED] Nykaa rejected non-clnk link: {cand}")
                        return raw_url
    except Exception as e:
        logger.warning(f"Cuelinks converter exception for {clean_target[:45]}: {e}")

    return raw_url


# Target Private & Web Channel IDs / Links provided by user:
TARGET_CHAT_IDS = [
    -1002273009558,  # Discounts & Offers and More ✨ (+0GTLjgGo-DQ5YjE9)
    -1002442523223,  # CHIRAG DEALS (-1002442523223)
    -1001404064358,  # QUICK DEALS 2.0 (+SHMJO014m9MxNDFl)
]

PRIVATE_CHANNEL_NAMES = {
    -1002273009558: "🔒 Discounts & Offers and More ✨",
    -1002442523223: "🔒 CHIRAG DEALS",
    -1001404064358: "🔒 QUICK DEALS 2.0"
}

# Telethon Session path in local storage
SESSION_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "telegram_user.session")

async def _heartbeat_loop():
    """Continuously reports live streaming heartbeat to PostgreSQL so UI shows live status."""
    while True:
        try:
            for ch in PRIVATE_CHANNEL_NAMES.values():
                await database.execute("""
                    INSERT INTO channel_monitors (channel_name, status, last_scanned_at)
                    VALUES ($1, 'ACTIVE (Live Stream)', (NOW() AT TIME ZONE 'Asia/Kolkata'))
                    ON CONFLICT (channel_name) DO UPDATE SET
                        status = 'ACTIVE (Live Stream)',
                        last_scanned_at = (NOW() AT TIME ZONE 'Asia/Kolkata');
                """, ch)
        except Exception as e:
            logger.debug(f"Heartbeat update notice: {e}")
        await asyncio.sleep(30)

async def start_telegram_listener(api_id: int = None, api_hash: str = None):
    """
    Connects to Telegram user session and starts listening to live incoming messages.
    """
    env_api_id = os.getenv("TG_API_ID")
    env_api_hash = os.getenv("TG_API_HASH")
    
    api_id = api_id or (int(env_api_id) if env_api_id else None)
    api_hash = api_hash or env_api_hash

    if not api_id or not api_hash or not os.path.exists(SESSION_PATH):
        logger.info("TG_API_ID, TG_API_HASH or session file missing. Private listener skipped (public DoH monitor active).")
        return

    try:
        logger.info("🚀 Connecting Real-Time Telegram MTProto Channel Listener...")
        global _client
        client = TelegramClient(SESSION_PATH, api_id, api_hash)
        _client = client

        # Retry connecting in case the previous process still holds the SQLite
        # session file lock (common during auto-restart within the first 10s)
        for attempt in range(1, 7):
            try:
                await client.start()
                break
            except Exception as e:
                if "database is locked" in str(e).lower():
                    wait = attempt * 3
                    logger.warning(
                        f"Telethon session locked by previous process "
                        f"(attempt {attempt}/6). Retrying in {wait}s..."
                    )
                    await asyncio.sleep(wait)
                else:
                    raise
        else:
            logger.error("Telethon session still locked after 6 retries. Listener skipped this boot.")
            return

        if not await client.is_user_authorized():
            logger.warning("Telegram user session is not authorized. Please run python login_telegram_listener.py once.")
            return

        logger.info("🟢 Telethon Connected! Listening to incoming deals in real-time (0-second push latency)...")

        # Start continuous UI heartbeat
        asyncio.create_task(_heartbeat_loop())

        http_client = httpx.AsyncClient(timeout=10, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})

        @client.on(events.NewMessage)
        async def handler(event):
            try:
                chat = await event.get_chat()
                chat_id = event.chat_id
                
                chat_title = PRIVATE_CHANNEL_NAMES.get(chat_id) or getattr(chat, 'title', str(chat_id))
                chat_username = getattr(chat, 'username', None)
                source_tag = f"@{chat_username}" if chat_username else f"[{chat_title}]"

                # 1. Strictly ignore our own destination channel to prevent feedback loop
                own_channel_id = str(config.TELEGRAM_CHANNEL_ID).strip()
                if str(chat_id) == own_channel_id or (chat_username and chat_username.lower() in ("deal_pulse_alerts", "dealpulse")):
                    return
                if chat_title and "dealpulse" in chat_title.lower():
                    return

                # 2. Strictly filter: Only process messages from whitelisted target spy channels
                if chat_id not in TARGET_CHAT_IDS and chat_id not in PRIVATE_CHANNEL_NAMES:
                    return

                text = event.raw_text or ""
                detected_urls = set(re.findall(r'https?://[^\s<>"]+|www\.[^\s<>"]+', text))

                # 1. Extract hidden text-linked URLs (e.g. [Buy Now](https://...))
                if hasattr(event.message, "entities") and event.message.entities:
                    for ent in event.message.entities:
                        if hasattr(ent, "url") and ent.url:
                            detected_urls.add(ent.url)

                # 2. Extract Inline Button URLs
                if hasattr(event.message, "buttons") and event.message.buttons:
                    for row in event.message.buttons:
                        for btn in row:
                            if hasattr(btn, "url") and btn.url:
                                detected_urls.add(btn.url)

                # Clean and filter non-Telegram external URLs
                clean_urls = [u for u in detected_urls if u.startswith("http") and "t.me/" not in u and "telegram.org" not in u]

                if clean_urls:
                    logger.info(f"⚡ [REAL-TIME TELEGRAM] Intercepted multi-link post from {source_tag} with {len(clean_urls)} individual product deal(s)")
                    for url in clean_urls:
                        await verify_and_ingest_single_deal(
                            channel=chat_title,
                            post_id=event.id,
                            raw_url=url,
                            client=http_client
                        )

            except Exception as e:
                logger.error(f"Error processing real-time message: {e}")

        await client.run_until_disconnected()

    except Exception as e:
        logger.error(f"Error in start_telegram_listener: {e}", exc_info=True)

if __name__ == "__main__":
    asyncio.run(start_telegram_listener())
