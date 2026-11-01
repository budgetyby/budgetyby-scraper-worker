import html
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from budgetby import config
from budgetby.scrapers.utils import clean_title
from budgetby.utils import format_inr

def format_rating(rating: float | int | str | None) -> str:
    """Format star rating cleanly to 1 decimal place (e.g. 3.8 ★, 4.2 ★)."""
    if rating is None or rating == "":
        return ""
    try:
        val = float(rating)
        if val <= 0:
            return ""
        return f"{val:.1f}"
    except (ValueError, TypeError):
        return str(rating).strip()

def format_price(amount: float | int | None) -> str:
    """Format amount as ₹1,23,456"""
    if amount is None:
        return "N/A"
    return format_inr(amount, prefix="₹")

def build_buy_button(url: str, text: str = "🛒 Buy Now") -> InlineKeyboardMarkup:
    """Create an InlineKeyboardMarkup with a single URL button."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, url=url)]])

def get_tiered_badge(badge: str = None, pct: int = 0) -> str:
    """Determine high-impact visual badge based on drop magnitude and historical baseline."""
    if pct >= 70:
        return "🚨 LOOT DEAL (70%+ OFF) 🚨"
    elif pct >= 50:
        return "⚡ MEGA PRICE DROP (50%+ OFF) ⚡"
    elif badge == "ATL":
        return "📉 ALL-TIME LOW PRICE 📉"
    elif badge == "near_ATL":
        return "🔥 NEAR RECORD LOW 🔥"
    elif badge == "90d_low":
        return "🏷️ 90-DAY LOWEST PRICE"
    elif badge == "60d_low":
        return "🏷️ 60-DAY LOWEST PRICE"
    elif badge == "30d_low":
        return "🏷️ 30-DAY LOWEST PRICE"
    return "🔥 PRICE DROP ALERT"

def get_category_hashtags(platform: str = "", category: str = "", pct: int = 0, badge: str = "") -> str:
    """Generate relevant, searchable category and platform hashtags."""
    tags = ["#DealPulse"]
    
    # Platform tag
    p = (platform or "").lower()
    if "amazon" in p:
        tags.append("#Amazon")
    elif "flipkart" in p:
        tags.append("#Flipkart")
    elif "myntra" in p:
        tags.append("#Myntra")
    elif "ajio" in p:
        tags.append("#Ajio")
    elif "nykaa" in p:
        tags.append("#Nykaa")
        
    # Category tag mappings
    c = (category or "").lower()
    if any(k in c for k in ["phone", "mobile", "electronics", "laptop", "audio", "headphone", "watch", "tv", "camera"]):
        tags.append("#Electronics")
        if "phone" in c or "mobile" in c:
            tags.append("#Smartphones")
        elif "audio" in c or "headphone" in c:
            tags.append("#Audio")
    elif any(k in c for k in ["shirt", "jean", "trouser", "dress", "saree", "kurta", "jacket", "top", "clothing", "wear"]):
        tags.append("#Fashion")
        tags.append("#Clothing")
    elif any(k in c for k in ["shoe", "sneaker", "sandal", "footwear", "boot"]):
        tags.append("#Footwear")
        tags.append("#Sneakers")
    elif any(k in c for k in ["beauty", "makeup", "skincare", "haircare", "perfume", "fragrance", "bath"]):
        tags.append("#Beauty")
    elif any(k in c for k in ["home", "kitchen", "furniture", "decor", "dining", "cookware"]):
        tags.append("#HomeKitchen")
    elif any(k in c for k in ["baby", "kids", "toy"]):
        tags.append("#KidsBaby")
        
    # Deal severity tags
    if pct >= 70 or badge == "ATL":
        tags.append("#LootDeal")
    elif pct >= 50:
        tags.append("#MegaDrop")
    else:
        tags.append("#Deals")
        
    return " ".join(tags)

def format_mega_deal(product: dict, deal_result: dict = None) -> str:
    """Format a 80+ score MEGA DEAL."""
    title = html.escape(clean_title(product.get("title", "")))
    current_price = product.get("current_price", 0)
    mrp = product.get("mrp", 0)
    platform = (product.get("platform") or "Store").capitalize()
    savings = mrp - current_price if mrp and mrp > current_price else 0
    pct = round((savings / mrp) * 100) if mrp and mrp > 0 else 0
    badge = (deal_result or {}).get("badge") or product.get("badge")
    rating = product.get("rating", "")
    url = product.get("affiliate_url") or product.get("product_url") or product.get("url", "")
    
    badge_header = get_tiered_badge(badge, pct)
    hashtags = get_category_hashtags(platform, product.get("category", ""), pct, badge)
    
    text = f"<b>{badge_header} ({platform})</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    text += f"🛍️ <b>{title}</b>\n\n"
    if savings > 0:
        text += f"💸 <b>Steal Price:</b> <b>{format_price(current_price)}</b> (<s>{format_price(mrp)}</s>)\n"
        text += f"🏷️ <b>Massive Savings:</b> <b>{pct}% OFF</b> (Save {format_price(savings)})\n"
    else:
        text += f"💸 <b>Deal Price:</b> <b>{format_price(current_price)}</b>\n"
def get_bot_footer() -> str:
    bot_handle = getattr(config, "TELEGRAM_BOT_USERNAME", "") or "@BudgetByBot"
    return f"🔍 <i>Missed a price drop? Send product link to {bot_handle} for review!</i>\n"

def format_mega_deal(product: dict, deal_result: dict = None) -> str:
    """Format a 80+ score MEGA DEAL or ATL price drop."""
    title = html.escape(clean_title(product.get("title", "")))
    current_price = product.get("current_price", 0)
    mrp = product.get("mrp", 0)
    platform = (product.get("platform") or "Store").capitalize()
    savings = mrp - current_price if mrp and mrp > current_price else 0
    pct = round((savings / mrp) * 100) if mrp and mrp > 0 else 0
    badge = (deal_result or {}).get("badge") or product.get("badge")
    rating = product.get("rating", "")
    url = product.get("affiliate_url") or product.get("product_url") or product.get("url", "")
    
    badge_header = get_tiered_badge(badge, pct)
    hashtags = get_category_hashtags(platform, product.get("category", ""), pct, badge)
    
    text = f"<b>{badge_header} ({platform})</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    text += f"🛍️ <b>{title}</b>\n\n"
    if savings > 0:
        text += f"💸 <b>Offer Price:</b> <b>{format_price(current_price)}</b> (<s>{format_price(mrp)}</s>)\n"
        text += f"🏷️ <b>Discount:</b> <b>{pct}% OFF</b> (Save {format_price(savings)})\n"
    else:
        text += f"💸 <b>Offer Price:</b> <b>{format_price(current_price)}</b>\n"
        
    rating_str = format_rating(rating)
    if rating_str:
        text += f"⭐ <b>Rating:</b> {rating_str} ★\n"
        
    if url:
        text += f"\n🛒 <b>Buy Directly on {platform}:</b>\n👉 {url}\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    text += get_bot_footer()
    text += f"{hashtags}"
        
    return text

def format_today_deal(product: dict, deal_result: dict = None) -> str:
    """Format a Today's Deal / Flash Sale alert."""
    title = html.escape(clean_title(product.get("title", "")))
    current_price = product.get("current_price", 0)
    mrp = product.get("mrp", 0)
    platform = (product.get("platform") or "Store").capitalize()
    savings = mrp - current_price if mrp and mrp > current_price else 0
    pct = round((savings / mrp) * 100) if mrp and mrp > 0 else 0
    badge = (deal_result or {}).get("badge") or product.get("badge")
    rating = product.get("rating", "")
    url = product.get("affiliate_url") or product.get("product_url") or product.get("url", "")
    
    badge_header = get_tiered_badge(badge, pct)
    hashtags = get_category_hashtags(platform, product.get("category", ""), pct, badge)

    text = f"<b>{badge_header} ({platform})</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    text += f"🛍️ <b>{title}</b>\n\n"
    if savings > 0:
        text += f"💸 <b>Deal Price:</b> <b>{format_price(current_price)}</b> (<s>{format_price(mrp)}</s>)\n"
        text += f"🏷️ <b>Discount:</b> <b>{pct}% OFF</b> (Save {format_price(savings)})\n"
    else:
        text += f"💸 <b>Deal Price:</b> <b>{format_price(current_price)}</b>\n"

    rating_str = format_rating(rating)
    if rating_str:
        text += f"⭐ <b>Rating:</b> {rating_str} ★\n"

    if url:
        text += f"\n🛒 <b>Buy Directly on {platform}:</b>\n👉 {url}\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    text += get_bot_footer()
    text += f"{hashtags}"

    return text

def format_hot_deal(product: dict, deal_result: dict = None) -> str:
    """Format a 60-79 score HOT DEAL."""
    title = html.escape(clean_title(product.get("title", "")))
    current_price = product.get("current_price", 0)
    mrp = product.get("mrp", 0)
    platform = (product.get("platform") or "Store").capitalize()
    savings = mrp - current_price if mrp and mrp > current_price else 0
    pct = round((savings / mrp) * 100) if mrp and mrp > 0 else 0
    badge = (deal_result or {}).get("badge") or product.get("badge")
    rating = product.get("rating", "")
    url = product.get("affiliate_url") or product.get("product_url") or product.get("url", "")
    
    badge_header = get_tiered_badge(badge, pct)
    hashtags = get_category_hashtags(platform, product.get("category", ""), pct, badge)
    
    text = f"<b>{badge_header} ({platform})</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    text += f"🛍️ <b>{title}</b>\n\n"
    if savings > 0:
        text += f"💸 <b>Offer Price:</b> <b>{format_price(current_price)}</b> (<s>{format_price(mrp)}</s>)\n"
        text += f"🏷️ <b>Discount:</b> <b>{pct}% OFF</b> (Save {format_price(savings)})\n"
    else:
        text += f"💸 <b>Offer Price:</b> <b>{format_price(current_price)}</b>\n"
        
    rating_str = format_rating(rating)
    if rating_str:
        text += f"⭐ <b>Rating:</b> {rating_str} ★\n"
        
    if url:
        text += f"\n🛒 <b>Buy Directly on {platform}:</b>\n👉 {url}\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    text += get_bot_footer()
    text += f"{hashtags}"
        
    return text

def format_good_deal(product: dict, deal_result: dict = None) -> str:
    """Format a 35-59 score Good Deal."""
    title = html.escape(clean_title(product.get("title", "")))
    current_price = product.get("current_price", 0)
    mrp = product.get("mrp", 0)
    platform = (product.get("platform") or "Store").capitalize()
    savings = mrp - current_price if mrp and mrp > current_price else 0
    pct = round((savings / mrp) * 100) if mrp and mrp > 0 else 0
    badge = (deal_result or {}).get("badge") or product.get("badge")
    url = product.get("affiliate_url") or product.get("product_url") or product.get("url", "")
    
    badge_header = get_tiered_badge(badge, pct)
    hashtags = get_category_hashtags(platform, product.get("category", ""), pct, badge)
    
    text = f"<b>{badge_header} ({platform})</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    text += f"🛍️ <b>{title}</b>\n\n"
    if savings > 0:
        text += f"💸 <b>Deal Price:</b> <b>{format_price(current_price)}</b> (<s>{format_price(mrp)}</s>)\n"
        text += f"🏷️ <b>Discount:</b> <b>{pct}% OFF</b>\n"
    else:
        text += f"💸 <b>Deal Price:</b> <b>{format_price(current_price)}</b>\n"
        
    if url:
        text += f"\n🛒 <b>Buy Directly on {platform}:</b>\n👉 {url}\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    text += get_bot_footer()
    text += f"{hashtags}"
    return text

def format_evergreen_deal(product: dict, post_count: int = 1) -> str:
    """Format an evergreen deal repost."""
    title = html.escape(clean_title(product.get("title", "")))
    current_price = product.get("current_price", 0)
    mrp = product.get("mrp", 0)
    platform = (product.get("platform") or "Store").capitalize()
    savings = mrp - current_price if mrp and mrp > current_price else 0
    pct = round((savings / mrp) * 100) if mrp and mrp > 0 else 0
    rating = product.get("rating", "")
    url = product.get("affiliate_url") or product.get("product_url") or product.get("url", "")
    hashtags = get_category_hashtags(platform, product.get("category", ""), pct, "evergreen")
    
    header = "🌟 <b>HANDPICKED BESTSELLER DEAL!</b>" if post_count == 1 else "🔔 <b>DEAL REMINDER — DON'T MISS OUT!</b>"
    text = f"{header} ({platform})\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    text += f"🛍️ <b>{title}</b>\n\n"
    if savings > 0:
        text += f"💸 <b>Offer Price:</b> <b>{format_price(current_price)}</b> (<s>{format_price(mrp)}</s>)\n"
        text += f"🏷️ <b>Discount:</b> <b>{pct}% OFF</b> (Save {format_price(savings)})\n"
    else:
        text += f"💸 <b>Offer Price:</b> <b>{format_price(current_price)}</b>\n"
        
    rating_str = format_rating(rating)
    if rating_str:
        text += f"⭐ <b>Rating:</b> {rating_str} ★\n"
        
    if url:
        text += f"\n🛒 <b>Buy Directly on {platform}:</b>\n👉 {url}\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    text += get_bot_footer()
    text += f"{hashtags}"
    return text

def format_daily_digest(top_deals: list[dict], digest_type: str = "Morning") -> str:
    """Format a clean Top 5 Deals Daily Digest for morning or evening roundups."""
    icon = "🌅" if digest_type.lower() == "morning" else "🌙"
    num_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    
    text = f"{icon} <b>{digest_type.upper()} DEALS DIGEST — Top 5 Best Drops!</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    text += "Here are the top-rated, biggest price drops curated for you right now:\n\n"
    
    for i, d in enumerate(top_deals[:5]):
        num = num_emojis[i] if i < len(num_emojis) else f"#{i+1}"
        title = html.escape(clean_title(d.get("title", "")))
        price = d.get("current_price") or d.get("posted_price", 0)
        mrp = d.get("mrp") or d.get("posted_mrp", 0)
        platform = (d.get("platform") or "Store").capitalize()
        url = d.get("affiliate_url") or d.get("product_url") or d.get("url", "")
        
        pct = 0
        if mrp and mrp > price:
            pct = round(((mrp - price) / mrp) * 100)
            
        pct_str = f" (<b>{pct}% OFF</b>)" if pct > 0 else ""
        r_val = format_rating(d.get('rating'))
        rating_str = f" ⭐ {r_val}★" if r_val else ""
        
        text += f"{num} <b>{title}</b>\n"
        text += f"   💰 <b>{format_price(price)}</b>{pct_str}{rating_str}\n"
        if url:
            text += f"   👉 <a href='{url}'>Grab Deal on {platform}</a>\n\n"
        else:
            text += "\n"
            
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    text += "#DailyDigest #TopPicks #BestDeals #DealPulse"
    return text

def format_refurbished_deal(product: dict, new_product: dict = None) -> str:
    """Format a refurbished deal with optional new product comparison."""
    title = html.escape(clean_title(product.get("title", "")))
    current_price = product.get("current_price", 0)
    platform = (product.get("platform") or "Store").capitalize()
    url = product.get("affiliate_url") or product.get("product_url") or product.get("url", "")
    hashtags = get_category_hashtags(platform, product.get("category", ""), 0, "refurbished")
    
    text = f"♻️ <b>VERIFIED REFURBISHED DEAL! ({platform})</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    text += f"🛍️ <b>{title}</b>\n\n"
    text += f"💸 <b>Refurbished Price:</b> <b>{format_price(current_price)}</b>\n"
    if new_product:
        new_price = new_product.get("current_price", 0)
        text += f"🏷️ <b>Brand New Price:</b> <s>{format_price(new_price)}</s>\n"
    if url:
        text += f"\n🛒 <b>Buy Directly on {platform}:</b>\n👉 {url}\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    text += "🔍 <i>Missed a price drop? Send product link to @Deal_pulse_alert_bot for review!</i>\n"
    text += f"{hashtags}"
    return text

def format_bundle_deal(bundle_data: dict) -> str:
    """Format a bundle deal containing multiple items."""
    text = "📦 <b>COMBO BUNDLE DEAL!</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    items = bundle_data.get("items", [])
    total_separate = sum(i.get("price", 0) for i in items)
    bundle_price = bundle_data.get("bundle_price", 0)
    savings = total_separate - bundle_price
    
    for item in items:
        text += f"• <b>{item.get('name')}</b>: {format_price(item.get('price', 0))}\n"
        
    text += f"\n💸 <b>Bundle Price:</b> <b>{format_price(bundle_price)}</b> (<s>{format_price(total_separate)}</s>)\n"
    if savings > 0:
        pct = round((savings / total_separate) * 100) if total_separate > 0 else 0
        text += f"🏷️ <b>Combo Savings:</b> <b>{pct}% OFF</b> (Save {format_price(savings)})\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    text += "#BundleDeal #ComboOffer #DealPulse"
    return text

def format_back_in_stock(product: dict, days_oos: int = 0) -> str:
    """Format a back in stock notification."""
    title = html.escape(clean_title(product.get("title", "")))
    current_price = product.get("current_price", 0)
    mrp = product.get("mrp", 0)
    platform = (product.get("platform") or "Store").capitalize()
    savings = mrp - current_price if mrp and mrp > current_price else 0
    pct = round((savings / mrp) * 100) if mrp and mrp > 0 else 0
    rating = product.get("rating")
    url = product.get("affiliate_url") or product.get("product_url") or product.get("url", "")
    hashtags = get_category_hashtags(platform, product.get("category", ""), pct, "back_in_stock")
    
    text = f"🔔 <b>BACK IN STOCK ALERT! ({platform})</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    text += f"🛍️ <b>{title}</b>\n\n"
    if savings > 0:
        text += f"💸 <b>Current Price:</b> <b>{format_price(current_price)}</b> (<s>{format_price(mrp)}</s>)\n"
        text += f"🏷️ <b>Discount:</b> <b>{pct}% OFF</b>\n"
    else:
        text += f"💸 <b>Current Price:</b> <b>{format_price(current_price)}</b>\n"
    rating_str = format_rating(rating)
    if rating_str:
        text += f"⭐ <b>Rating:</b> {rating_str} ★\n"
    if url:
        text += f"\n🛒 <b>Buy Directly on {platform}:</b>\n👉 {url}\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    text += "🔍 <i>Missed a price drop? Send product link to @Deal_pulse_alert_bot for review!</i>\n"
    text += f"{hashtags}"
    return text

def format_variant_deal(product: dict, variants: list) -> str:
    """Format a deal with multiple variants."""
    title = html.escape(clean_title(product.get("title", "")))
    platform = (product.get("platform") or "Store").capitalize()
    url = product.get("affiliate_url") or product.get("product_url") or product.get("url", "")
    text = f"🎨 <b>MULTIPLE VARIANTS ON SALE! ({platform})</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    text += f"🛍️ <b>{title}</b>\n\n"
    variants_sorted = sorted(variants, key=lambda x: x.get("price", 0))
    for v in variants_sorted:
        text += f"• {v.get('name')}: <b>{format_price(v.get('price', 0))}</b>\n"
    if url:
        text += f"\n🛒 <b>Buy Directly on {platform}:</b>\n👉 {url}\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    text += "#VariantDeals #DealPulse"
    return text

def format_budget_segment(segment_name: str, deals: list) -> str:
    """Format a roundup of best deals in a budget segment."""
    text = f"📋 <b>Top {segment_name} Deals Right Now!</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    for d in deals:
        url = d.get('affiliate_url') or d.get('product_url') or d.get('url', '')
        text += f"• <b>{d.get('title')}</b> — <b>{format_price(d.get('current_price'))}</b>\n  👉 {url}\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    text += "#BudgetDeals #DealPulse"
    return text

def format_trending_roundup(category: str, deals: list) -> str:
    """Format a trending roundup for a category."""
    text = f"🔥 <b>Trending Deals in {category.title()}</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    for d in deals:
        url = d.get('affiliate_url') or d.get('product_url') or d.get('url', '')
        text += f"• <b>{d.get('title')}</b> — <b>{format_price(d.get('current_price'))}</b>\n  👉 {url}\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    text += "#TrendingDeals #DealPulse"
    return text

def format_deal_expiry_edit(original_caption: str, new_price: float = None, is_oos: bool = False, dropped_more: bool = False) -> str:
    """Append expiry or update notice to an existing caption."""
    if is_oos:
        return original_caption + "\n\n❌ <i>UPDATE: Out of stock!</i>"
    if new_price is not None:
        if dropped_more:
            return original_caption + f"\n\n📉 <i>UPDATE: Price dropped further to {format_price(new_price)}!</i>"
        else:
            return original_caption + f"\n\n⚠️ <i>UPDATE: Price changed to {format_price(new_price)}. Deal may be over.</i>"
    return original_caption + "\n\n⚠️ <i>UPDATE: Deal has expired.</i>"
