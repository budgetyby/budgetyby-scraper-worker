"""
Telegram command handlers.
"""

import logging
import re
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from budgetby import database

logger = logging.getLogger("budgetby.bot.handlers")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    text = (
        "Welcome to BudgetBy! 🚀\n\n"
        "We track prices on Amazon, Flipkart, and Myntra to bring you the best deals.\n"
        "Join our channel to get instant updates with affiliate links.\n"
        "Type /help to see what I can do."
    )
    await update.message.reply_text(text)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    text = (
        "Available commands:\n"
        "/start - Welcome message\n"
        "/help - List of commands\n"
        "/deals - Show latest 5 deals\n"
        "/search <query> - Search tracked products\n"
        "/track <url> - Submit a product for tracking"
    )
    await update.message.reply_text(text)

async def deals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /deals command."""
    records = await database.fetch(
        "SELECT title, current_price, affiliate_url, product_url FROM products "
        "WHERE status = 'ACTIVE' "
        "ORDER BY last_price_change DESC NULLS LAST LIMIT 5"
    )
    if not records:
        await update.message.reply_text("No recent deals found.")
        return
        
    from budgetby.bot.templates import format_price
    text = "<b>Latest 5 Deals:</b>\n\n"
    for r in records:
        url = r['affiliate_url'] or r['product_url']
        text += f"• <a href='{url}'>{r['title']}</a> - {format_price(r['current_price'])}\n"
    
    await update.message.reply_text(text, parse_mode='HTML', disable_web_page_preview=True)

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /search command."""
    if not context.args:
        await update.message.reply_text("Usage: /search <query>")
        return
        
    query = " ".join(context.args)
    records = await database.fetch(
        "SELECT title, current_price, affiliate_url, product_url FROM products "
        "WHERE title ILIKE $1 LIMIT 10", 
        f"%{query}%"
    )
    
    if not records:
        await update.message.reply_text("No products found matching your search.")
        return
        
    from budgetby.bot.templates import format_price
    text = f"<b>Search results for '{query}':</b>\n\n"
    for r in records:
        url = r['affiliate_url'] or r['product_url']
        text += f"• <a href='{url}'>{r['title']}</a> - {format_price(r['current_price'])}\n"
        
    await update.message.reply_text(text, parse_mode='HTML', disable_web_page_preview=True)

async def track_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /track command."""
    if not context.args:
        await update.message.reply_text("Usage: /track <url>")
        return
    url = context.args[0]
    await update.message.reply_text(f"Thank you! Added to the tracking queue.\n{url}")


async def channel_post_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles live incoming channel posts whenever @Deal_pulse_alert_bot is added to private channels.
    Extracts deals, verifies live prices, and broadcasts to target channel.
    """
    try:
        msg = update.channel_post or update.message
        if not msg:
            return

        chat = msg.chat
        chat_title = chat.title or str(chat.id)
        chat_username = chat.username
        source_tag = f"@{chat_username}" if chat_username else f"[{chat_title}]"

        text = msg.text or msg.caption or ""
        raw_urls = re.findall(r'https?://[^\s<>"]+|www\.[^\s<>"]+', text)

        if raw_urls:
            import httpx
            from budgetby.ingest.channel_monitor import verify_and_ingest_single_deal
            logger.info(f"⚡ [BOT CHANNEL LISTENER] Intercepted new post from {source_tag} with {len(raw_urls)} links")

            async with httpx.AsyncClient(timeout=10, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
                for url in raw_urls:
                    await verify_and_ingest_single_deal(
                        channel=source_tag,
                        post_id=msg.message_id,
                        raw_url=url,
                        client=client
                    )
    except Exception as e:
        logger.error(f"Error in channel_post_handler: {e}")

def register_handlers(app: Application):
    """Register all public command handlers."""
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("deals", deals_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("track", track_command))

    from telegram.ext import MessageHandler, filters
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, channel_post_handler))