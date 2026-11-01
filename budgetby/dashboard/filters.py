"""
BudgetBy — Jinja2 Custom Storefront Filters for Server-Side Rendering (SSR)
"""
import datetime
from budgetby.utils import format_inr

def jinja_format_inr(val):
    return format_inr(val, prefix="₹")

def jinja_time_ago(val):
    if not val:
        return "Recently"
    try:
        if isinstance(val, str):
            val = datetime.datetime.fromisoformat(val.replace("Z", "+00:00"))
        now = datetime.datetime.now(datetime.timezone.utc)
        if hasattr(val, "tzinfo") and val.tzinfo is None:
            val = val.replace(tzinfo=datetime.timezone.utc)
        diff = int((now - val).total_seconds())
        if diff < 60:
            return "Just now"
        if diff < 3600:
            return f"{diff // 60}m ago"
        if diff < 86400:
            return f"{diff // 3600}h ago"
        return f"{diff // 86400}d ago"
    except Exception:
        return "Recently"

def jinja_store_badge(platform):
    plat = (platform or "").lower()
    badges = {
        "amazon": "bg-amber-50 text-amber-900 border border-amber-300 font-bold",
        "flipkart": "bg-blue-50 text-blue-700 border border-blue-300 font-bold",
        "myntra": "bg-pink-50 text-pink-700 border border-pink-300 font-bold",
        "ajio": "bg-yellow-50 text-yellow-800 border border-yellow-300 font-bold",
        "nykaa": "bg-rose-50 text-rose-700 border border-rose-300 font-bold",
    }
    return badges.get(plat, "bg-slate-100 text-slate-700 border border-slate-200 font-bold")

def jinja_store_name(platform):
    plat = (platform or "").lower()
    names = {
        "amazon": "Amazon",
        "flipkart": "Flipkart",
        "myntra": "Myntra",
        "ajio": "Ajio",
        "nykaa": "Nykaa",
    }
    return names.get(plat, plat.capitalize())

def jinja_round_int(val):
    try:
        return int(round(float(val)))
    except Exception:
        return 0

def jinja_format_posted_time(val):
    if not val:
        return "Recently posted"
    try:
        if isinstance(val, str):
            val = datetime.datetime.fromisoformat(val.replace("Z", "+00:00"))
        ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        if hasattr(val, "astimezone"):
            val_ist = val.astimezone(ist)
        else:
            val_ist = val
        return val_ist.strftime("%d %b, %I:%M %p")
    except Exception:
        return "Recently posted"

def register_jinja_filters(templates):
    """Registers all custom SSR filters on the Jinja2Templates environment."""
    templates.env.filters["format_inr"] = jinja_format_inr
    templates.env.filters["time_ago"] = jinja_time_ago
    templates.env.filters["store_badge"] = jinja_store_badge
    templates.env.filters["store_name"] = jinja_store_name
    templates.env.filters["round_int"] = jinja_round_int
    templates.env.filters["format_posted_time"] = jinja_format_posted_time
