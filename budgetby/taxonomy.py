"""
BudgetBy — Universal E-Commerce Category & Subcategory Taxonomy
Defines the official department catalog structure, badges, icons, color gradients,
aliases, and keyword matching lists.
"""

UNIVERSAL_CATEGORIES = {
    "fashion": {
        "name": "Fashion & Apparel",
        "icon": "👗",
        "badge": "CLOTHING & WEAR",
        "color": "purple",
        "bg_gradient": "from-purple-500/10 via-pink-500/10 to-rose-500/10",
        "border_color": "border-purple-200 hover:border-purple-500",
        "tag_color": "bg-purple-100 text-purple-800",
        "image": "https://images.unsplash.com/photo-1490481651871-ab68de25d43d?w=360&auto=format&fit=crop&q=70",
        "deal_count": "29,000+",
        "aliases": ["fashion", "clothing", "apparel", "wear"],
        "keywords": ["t-shirt", "casual shirt", "formal shirt", "kurta", "kurti", "saree", "jeans", "trouser", "trousers", "jacket", "hoodie", "boxer", "lehenga", "ethnic wear", "sweatshirt"],
        "subcategories": {
            "tshirts": {"name": "T-Shirts & Polos", "icon": "👕", "keywords": ["t-shirt", "tshirt", "polo t-shirt", "printed t-shirt", "oversized t-shirt", "round neck t-shirt"], "image": "https://images.unsplash.com/photo-1521572267360-ee0c2909d518?w=300&auto=format&fit=crop&q=70"},
            "shirts": {"name": "Casual & Formal Shirts", "icon": "👔", "keywords": ["casual shirt", "formal shirt", "spread collar shirt", "cotton shirt", "denim shirt", "slim shirt"], "image": "https://images.unsplash.com/photo-1602810318383-e386cc2a3ccf?w=300&auto=format&fit=crop&q=70"},
            "kurta": {"name": "Kurtas & Ethnic Wear", "icon": "✨", "keywords": ["kurta", "kurti", "saree", "anarkali", "ethnic", "nehru jacket", "lehenga", "sherwani"], "image": "https://images.unsplash.com/photo-1610030469983-98e550d6193c?w=300&auto=format&fit=crop&q=70"},
            "jeans": {"name": "Jeans & Denim", "icon": "👖", "keywords": ["jeans", "denim jeans", "skinny fit jeans", "slim fit jeans", "straight fit jeans", "baggy jeans"], "image": "https://images.unsplash.com/photo-1541099649105-f69ad21f3246?w=300&auto=format&fit=crop&q=70"},
            "trousers": {"name": "Trousers & Joggers", "icon": "👖", "keywords": ["trouser", "trousers", "chino", "cargos", "cargo pant", "track pant", "joggers"], "image": "https://images.unsplash.com/photo-1624378439575-d8705ad7ae80?w=300&auto=format&fit=crop&q=70"},
            "dresses": {"name": "Dresses & Tops", "icon": "👗", "keywords": ["maxi dress", "crop top", "jumpsuit", "tunic", "women dress", "party dress"], "image": "https://images.unsplash.com/photo-1595777457583-95e059d581b8?w=300&auto=format&fit=crop&q=70"},
            "jackets": {"name": "Jackets & Hoodies", "icon": "🧥", "keywords": ["jacket", "jackets", "hoodie", "hoodies", "sweatshirt", "blazer", "bomber jacket"], "image": "https://images.unsplash.com/photo-1551028719-00167b16eac5?w=300&auto=format&fit=crop&q=70"},
            "innerwear": {"name": "Innerwear & Loungewear", "icon": "🩲", "keywords": ["boxer", "brief", "innerwear", "trunks", "nightsuit", "pyjama", "lounge wear", "brassiere"], "image": "https://images.unsplash.com/photo-1583496661160-fb5886a0aaaa?w=300&auto=format&fit=crop&q=70"},
            "activewear": {"name": "Activewear & Tracksuits", "icon": "🏃", "keywords": ["tracksuit", "track suit", "gym wear", "sportswear", "active wear", "yoga pants", "yoga tights", "gym tights", "compression leggings", "athletic wear"], "image": "https://images.unsplash.com/photo-1518310383802-640c2de311b2?w=300&auto=format&fit=crop&q=70"},
            "winterwear": {"name": "Winterwear & Woollens", "icon": "🧣", "keywords": ["sweater", "pullover sweater", "cardigan", "woollen sweater", "muffler", "scarf", "winter cap", "thermal wear", "turtle neck"], "image": "https://images.unsplash.com/photo-1608063615781-e2ef8c73d114?w=300&auto=format&fit=crop&q=70"}
        }
    },
    "beauty": {
        "name": "Beauty & Skincare",
        "icon": "💄",
        "badge": "BEAUTY & SKINCARE",
        "color": "rose",
        "bg_gradient": "from-rose-500/10 via-pink-500/10 to-red-500/10",
        "border_color": "border-rose-200 hover:border-rose-500",
        "tag_color": "bg-rose-100 text-rose-800",
        "image": "https://images.unsplash.com/photo-1522335789203-aabd1fc54bc9?w=360&auto=format&fit=crop&q=70",
        "deal_count": "14,500+",
        "aliases": ["beauty", "skincare", "cosmetics", "grooming", "personal_care"],
        "keywords": ["face wash", "sunscreen", "face serum", "moisturizer", "perfume", "shampoo", "lipstick", "body wash", "body lotion", "deodorant", "hair oil", "kajal", "foundation"],
        "subcategories": {
            "sunscreen": {"name": "Sunscreen & SPF 50", "icon": "☀️", "keywords": ["sunscreen", "sun block", "spf 50", "spf 30", "sun screen"], "image": "https://images.unsplash.com/photo-1556228720-195a672e8a03?w=300&auto=format&fit=crop&q=70"},
            "facewash": {"name": "Face Wash & Cleansers", "icon": "🧼", "keywords": ["face wash", "facewash", "cleanser", "face scrub", "foaming face wash"], "image": "https://images.unsplash.com/photo-1556228720-195a672e8a03?w=300&auto=format&fit=crop&q=70"},
            "serum": {"name": "Serums & Face Oils", "icon": "💧", "keywords": ["face serum", "niacinamide serum", "salicylic acid", "vitamin c serum", "retinol serum"], "image": "https://images.unsplash.com/photo-1620916566398-39f1143ab7be?w=300&auto=format&fit=crop&q=70"},
            "moisturizer": {"name": "Moisturizers & Creams", "icon": "🧴", "keywords": ["moisturizer", "moisturising cream", "body lotion", "night cream", "day cream"], "image": "https://images.unsplash.com/photo-1570172619644-dfd03ed5d881?w=300&auto=format&fit=crop&q=70"},
            "perfume": {"name": "Perfumes & Deos", "icon": "🌸", "keywords": ["perfume", "eau de parfum", "deodorant", "body spray", "body mist", "attar"], "image": "https://images.unsplash.com/photo-1592945403244-b3fbafd7f539?w=300&auto=format&fit=crop&q=70"},
            "haircare": {"name": "Shampoos & Haircare", "icon": "💆", "keywords": ["shampoo", "hair conditioner", "hair oil", "hair mask", "hair serum"], "image": "https://images.unsplash.com/photo-1535585209827-a15fcdbc4c2d?w=300&auto=format&fit=crop&q=70"},
            "lipstick": {"name": "Lip Makeup", "icon": "💋", "keywords": ["lipstick", "lip gloss", "lip liner", "lip tint", "matte lipstick", "liquid lipstick", "lip balm tinted"], "image": "https://images.unsplash.com/photo-1586495777744-4413f21062fa?w=300&auto=format&fit=crop&q=70"},
            "eyemakeup": {"name": "Eye Makeup", "icon": "👁️", "keywords": ["kajal", "eyeliner", "mascara", "eye shadow", "eyebrow pencil", "kohl", "eye primer", "eye liner"], "image": "https://images.unsplash.com/photo-1512496015851-a90fb38ba796?w=300&auto=format&fit=crop&q=70"},
            "facemakeup": {"name": "Face Makeup", "icon": "✨", "keywords": ["foundation", "compact powder", "blush powder", "blush palette", "highlighter makeup", "setting powder", "bb cream", "cc cream", "concealer", "contour", "primer makeup"], "image": "https://images.unsplash.com/photo-1596462502278-27bfdc403348?w=300&auto=format&fit=crop&q=70"},
            "bodywash": {"name": "Body Wash & Soaps", "icon": "🚿", "keywords": ["body wash", "shower gel", "bath soap", "bathing bar", "glycerin soap", "handwash", "hand wash liquid", "antibacterial soap"], "image": "https://images.unsplash.com/photo-1584305574647-0cc949a2bb9f?w=300&auto=format&fit=crop&q=70"},
            "nailcare": {"name": "Nail Care", "icon": "💅", "keywords": ["nail polish", "nail paint", "nail remover", "nail art", "nail cutter", "cuticle oil", "gel nail"], "image": "https://images.unsplash.com/photo-1604654894610-df63bc536371?w=300&auto=format&fit=crop&q=70"},
            "grooming": {"name": "Men's Grooming", "icon": "🪒", "keywords": ["beard trimmer", "beard oil", "shaving foam", "razor blade", "after shave", "beard wash"], "image": "https://images.unsplash.com/photo-1621607512214-68297480165e?w=300&auto=format&fit=crop&q=70"}
        }
    },
    "electronics": {
        "name": "Electronics & Gadgets",
        "icon": "📱",
        "badge": "GADGETS & AUDIO",
        "color": "blue",
        "bg_gradient": "from-blue-500/10 via-cyan-500/10 to-indigo-500/10",
        "border_color": "border-blue-200 hover:border-blue-500",
        "tag_color": "bg-blue-100 text-blue-800",
        "image": "https://images.unsplash.com/photo-1505740420928-5e560c06d30e?w=360&auto=format&fit=crop&q=70",
        "deal_count": "13,000+",
        "aliases": ["electronics", "smartphones", "laptops", "appliances", "gadgets"],
        "keywords": ["earphones", "headphone", "earbuds", "tws", "bluetooth speaker", "power bank", "fast charger", "smartphone", "laptop", "tablet", "camera", "smart bulb", "pen drive", "memory card", "ssd"],
        "subcategories": {
            "earbuds": {"name": "TWS Earbuds", "icon": "🎧", "keywords": ["earbuds", "tws", "airbuds", "airdopes", "wireless earbuds", "true wireless"], "image": "https://images.unsplash.com/photo-1590658268037-6bf12165a8df?w=300&auto=format&fit=crop&q=70"},
            "headphones": {"name": "Headphones & Neckbands", "icon": "🎧", "keywords": ["headphone", "headphones", "neckband", "earphones", "wired earphones", "over ear"], "image": "https://images.unsplash.com/photo-1546435770-a3e426bf472b?w=300&auto=format&fit=crop&q=70"},
            "speakers": {"name": "Bluetooth Speakers", "icon": "🔊", "keywords": ["bluetooth speaker", "soundbar", "party speaker", "portable speaker", "audio speaker"], "image": "https://images.unsplash.com/photo-1608043152269-423dbba4e7e1?w=300&auto=format&fit=crop&q=70"},
            "powerbanks": {"name": "Power Banks & Fast Chargers", "icon": "🔋", "keywords": ["power bank", "powerbank", "fast charger", "type-c charger", "charging cable", "usb adapter"], "image": "https://images.unsplash.com/photo-1583863788434-e58a36330cf0?w=300&auto=format&fit=crop&q=70"},
            "smartphones": {"name": "Smartphones", "icon": "📱", "keywords": ["smartphone", "mobile phone", "android phone", "iphone", "5g phone"], "image": "https://images.unsplash.com/photo-1511707171634-5f897ff02aa9?w=300&auto=format&fit=crop&q=70"},
            "laptops": {"name": "Laptops & Computing", "icon": "💻", "keywords": ["laptop", "notebook pc", "wireless mouse", "mechanical keyboard", "tablet pc", "monitor screen"], "image": "https://images.unsplash.com/photo-1496181133206-80ce9b88a853?w=300&auto=format&fit=crop&q=70"},
            "cameras": {"name": "Cameras & Photography", "icon": "📷", "keywords": ["dslr camera", "mirrorless camera", "action camera", "webcam", "camera lens", "tripod stand", "ring light", "camera bag"], "image": "https://images.unsplash.com/photo-1516035069371-29a1b244cc32?w=300&auto=format&fit=crop&q=70"},
            "smarthome": {"name": "Smart Home", "icon": "🏠", "keywords": ["smart bulb", "smart plug", "smart speaker", "alexa echo", "google home", "smart switch", "wifi smart", "voice assistant", "smart light strip"], "image": "https://images.unsplash.com/photo-1558002038-1055907df827?w=300&auto=format&fit=crop&q=70"},
            "storage": {"name": "Storage & Drives", "icon": "💾", "keywords": ["pen drive", "usb drive", "memory card", "sd card", "external hard disk", "portable ssd", "hard drive external", "flash drive"], "image": "https://images.unsplash.com/photo-1597848212624-a19eb35e2651?w=300&auto=format&fit=crop&q=70"}
        }
    },
    "home": {
        "name": "Home & Kitchen",
        "icon": "🏠",
        "badge": "HOME & ESSENTIALS",
        "color": "amber",
        "bg_gradient": "from-amber-500/10 via-orange-500/10 to-yellow-500/10",
        "border_color": "border-amber-200 hover:border-amber-500",
        "tag_color": "bg-amber-100 text-amber-800",
        "image": "https://images.unsplash.com/photo-1556911220-e15b29be8c8f?w=360&auto=format&fit=crop&q=70",
        "deal_count": "12,500+",
        "aliases": ["home", "kitchen", "household", "appliances_home"],
        "keywords": ["water bottle", "lunch box", "frying pan", "cookware", "storage container", "dustbin", "bedsheet", "curtain", "towel", "electric kettle", "home decor", "candle", "photo frame"],
        "subcategories": {
            "bottles": {"name": "Bottles & Lunch Boxes", "icon": "🍶", "keywords": ["water bottle", "flask bottle", "insulated bottle", "lunch box", "sipper bottle", "stainless steel bottle"], "image": "https://images.unsplash.com/photo-1602143407151-7111542de6e8?w=360&auto=format&fit=crop&q=70"},
            "cookware": {"name": "Cookware & Pans", "icon": "🍳", "keywords": ["cookware set", "frying pan", "kadhai", "tawa pan", "pressure cooker", "non stick pan", "triply"], "image": "https://images.unsplash.com/photo-1590794056226-79ef3a8147e1?w=300&auto=format&fit=crop&q=70"},
            "storage": {"name": "Storage Containers", "icon": "📦", "keywords": ["storage container", "airtight jar", "spice rack", "kitchen organizer", "storage box", "food container"], "image": "https://images.unsplash.com/photo-1584269600464-37b1b58a9fe7?w=360&auto=format&fit=crop&q=70"},
            "cleaning": {"name": "Cleaning & Dustbins", "icon": "🧹", "keywords": ["dustbin", "garbage bin", "spin mop", "cleaning broom", "cleaning wiper", "trash can"], "image": "https://images.unsplash.com/photo-1581578731548-c64695cc6952?w=300&auto=format&fit=crop&q=70"},
            "bedding": {"name": "Bedsheets & Curtains", "icon": "🛏️", "keywords": ["bedsheet", "bed cover", "window curtain", "door curtain", "pillow cover", "cotton blanket", "quilt", "comforter"], "image": "https://images.unsplash.com/photo-1522771739844-6a9f6d5f14af?w=300&auto=format&fit=crop&q=70"},
            "towels": {"name": "Towels & Bath Linen", "icon": "🛁", "keywords": ["bath towel", "hand towel", "face towel", "towel set", "cotton towel", "microfiber towel", "beach towel"], "image": "https://images.unsplash.com/photo-1583947581924-860bda6a26df?w=300&auto=format&fit=crop&q=70"},
            "decor": {"name": "Home Decor", "icon": "🕯️", "keywords": ["photo frame", "wall art", "scented candle", "flower vase", "wall clock", "table lamp", "fairy lights", "decorative", "showpiece", "figurine"], "image": "https://images.unsplash.com/photo-1513694203232-719a280e022f?w=300&auto=format&fit=crop&q=70"},
            "bathroom": {"name": "Bathroom Accessories", "icon": "🪥", "keywords": ["soap dispenser", "toothbrush holder", "bath mat", "shower curtain", "bathroom organizer", "toilet brush", "bathroom shelf"], "image": "https://images.unsplash.com/photo-1552321554-5fefe8c9ef14?w=300&auto=format&fit=crop&q=70"},
            "appliances": {"name": "Kitchen Appliances", "icon": "⚡", "keywords": ["electric kettle", "air fryer", "mixer grinder", "vegetable chopper", "sandwich maker", "induction cooktop"], "image": "https://images.unsplash.com/photo-1585515320310-259814833e62?w=300&auto=format&fit=crop&q=70"}
        }
    },
    "sports": {
        "name": "Sports & Fitness",
        "icon": "🏋️",
        "badge": "FITNESS & SPORTS",
        "color": "green",
        "bg_gradient": "from-emerald-500/10 via-green-500/10 to-teal-500/10",
        "border_color": "border-green-200 hover:border-green-500",
        "tag_color": "bg-green-100 text-green-800",
        "image": "https://images.unsplash.com/photo-1517838277536-f5f99be501cd?w=360&auto=format&fit=crop&q=70",
        "deal_count": "8,000+",
        "aliases": ["sports", "fitness", "gym", "workout"],
        "keywords": ["gym dumbbell", "yoga mat", "badminton racquet", "cricket bat", "bicycle", "football", "fitness equipment", "running shoe", "swimwear"],
        "subcategories": {
            "gym": {"name": "Gym & Weights", "icon": "💪", "keywords": ["dumbbell", "dumbbells", "resistance band", "gym shaker", "gym gloves", "weight plates", "barbell"], "image": "https://images.unsplash.com/photo-1583454110551-21f2fa2afe61?w=300&auto=format&fit=crop&q=70"},
            "yoga": {"name": "Yoga & Exercise", "icon": "🧘", "keywords": ["yoga mat", "exercise mat", "foam roller", "skipping rope", "ab roller"], "image": "https://images.unsplash.com/photo-1544367567-0f2fcb009e0b?w=300&auto=format&fit=crop&q=70"},
            "badminton": {"name": "Badminton & Racket", "icon": "🏸", "keywords": ["badminton", "shuttlecock", "badminton racquet", "squash racquet", "table tennis"], "image": "https://images.unsplash.com/photo-1626224583764-f87db24ac4ea?w=300&auto=format&fit=crop&q=70"},
            "cricket": {"name": "Cricket Gear", "icon": "🏏", "keywords": ["cricket bat", "cricket ball", "batting gloves", "cricket kit", "cricket helmet"], "image": "https://images.unsplash.com/photo-1531415074968-036ba1b575da?w=300&auto=format&fit=crop&q=70"},
            "football": {"name": "Football & Team Sports", "icon": "⚽", "keywords": ["football", "soccer ball", "basketball", "volleyball", "shin guards", "goalkeeper gloves", "sports jersey"], "image": "https://images.unsplash.com/photo-1579952363873-27f3bade9f55?w=300&auto=format&fit=crop&q=70"},
            "cycling": {"name": "Cycling & Outdoors", "icon": "🚴", "keywords": ["bicycle", "cycling gloves", "cycle lock", "camping tent", "trekking pole"], "image": "https://images.unsplash.com/photo-1485965120184-e220f721d03e?w=300&auto=format&fit=crop&q=70"},
            "running": {"name": "Athletics & Training", "icon": "🏃", "keywords": ["running hydration", "sports bottle", "sweatband", "athletics gear", "running belt", "stopwatch"], "image": "https://images.unsplash.com/photo-1476480862126-209bfaa8edc8?w=300&auto=format&fit=crop&q=70"},
            "swimming": {"name": "Swimming & Water Sports", "icon": "🏊", "keywords": ["swimming costume", "swimsuit", "swim cap", "swimming goggles", "swim wear"], "image": "https://images.unsplash.com/photo-1530549387789-4c1017266635?w=300&auto=format&fit=crop&q=70"}
        }
    },
    "footwear": {
        "name": "Footwear & Shoes",
        "icon": "👟",
        "badge": "SHOES & FOOTWEAR",
        "color": "indigo",
        "bg_gradient": "from-indigo-500/10 via-blue-500/10 to-violet-500/10",
        "border_color": "border-indigo-200 hover:border-indigo-500",
        "tag_color": "bg-indigo-100 text-indigo-800",
        "image": "https://images.unsplash.com/photo-1542291026-7eec264c27ff?w=360&auto=format&fit=crop&q=70",
        "deal_count": "9,300+",
        "aliases": ["footwear", "shoes", "shoe"],
        "keywords": ["sneakers", "running shoe", "sports shoes", "slippers", "slides footwear", "flip flop", "sandals", "formal shoes", "loafer", "floaters", "clogs footwear", "crocs"],
        "subcategories": {
            "sneakers": {"name": "Sneakers & Casual", "icon": "👟", "keywords": ["sneaker", "sneakers", "casual sneakers", "lifestyle sneakers", "canvas shoes", "white sneakers", "chunky sneakers", "plimsoll"], "image": "https://images.unsplash.com/photo-1595950653106-6c9ebd614d3a?w=300&auto=format&fit=crop&q=70"},
            "runningshoes": {"name": "Running & Training Shoes", "icon": "🏃", "keywords": ["running shoes", "running shoe", "sports shoes", "training shoes", "athletic shoes", "gym shoes", "performance shoes"], "image": "https://images.unsplash.com/photo-1542291026-7eec264c27ff?w=300&auto=format&fit=crop&q=70"},
            "slippers": {"name": "Slippers & Slides", "icon": "🩴", "keywords": ["slipper", "slippers", "flip flop", "flip-flop", "slide sandal", "chappal"], "image": "https://images.unsplash.com/photo-1603487742131-4160ec999306?w=300&auto=format&fit=crop&q=70"},
            "formal": {"name": "Formal & Loafers", "icon": "👞", "keywords": ["formal shoes", "loafer shoes", "oxford shoes", "derby shoes", "monk strap", "office shoes"], "image": "https://images.unsplash.com/photo-1614252235316-8c857d38b5f4?w=300&auto=format&fit=crop&q=70"},
            "heels": {"name": "Heels & Wedges", "icon": "👠", "keywords": ["high heels", "block heel", "wedge sandals", "stilettos", "pump shoes"], "image": "https://images.unsplash.com/photo-1543163521-1bf539c55dd2?w=300&auto=format&fit=crop&q=70"},
            "boots": {"name": "Boots & High-Tops", "icon": "👢", "keywords": ["leather boots", "ankle boots", "high top shoes", "chelsea boots", "riding boots"], "image": "https://images.unsplash.com/photo-1520639888713-7851133b1ed0?w=300&auto=format&fit=crop&q=70"},
            "sandals": {"name": "Sandals & Floaters", "icon": "👡", "keywords": ["sandals", "floater sandals", "strap sandals", "flat sandals", "gladiator sandals"], "image": "https://images.unsplash.com/photo-1562273138-f46be4ebdf33?w=300&auto=format&fit=crop&q=70"},
            "clogs": {"name": "Clogs & Crocs", "icon": "🦶", "keywords": ["clogs", "crocs", "foam clogs", "mule clogs", "slip on clogs"], "image": "https://images.unsplash.com/photo-1607522370275-f14206abe5d3?w=360&auto=format&fit=crop&q=70"}
        }
    },
    "watches": {
        "name": "Watches",
        "icon": "⌚",
        "badge": "WATCHES & WEARABLES",
        "color": "slate",
        "bg_gradient": "from-slate-500/10 via-zinc-500/10 to-neutral-500/10",
        "border_color": "border-slate-200 hover:border-slate-500",
        "tag_color": "bg-slate-100 text-slate-800",
        "image": "https://images.unsplash.com/photo-1523275335684-37898b6baf30?w=360&auto=format&fit=crop&q=70",
        "deal_count": "2,500+",
        "aliases": ["watches", "watch", "wristwatch"],
        "keywords": ["wrist watch", "analog watch", "digital watch", "chronograph watch", "smartwatch", "smart watch band", "leather watch"],
        "subcategories": {
            "menwatches": {"name": "Men's Watches", "icon": "⌚", "keywords": ["men watch", "analog watch men", "chronograph watch men", "leather strap watch", "sports watch men"], "image": "https://images.unsplash.com/photo-1524805444758-089113d48a6d?w=300&auto=format&fit=crop&q=70"},
            "womenwatches": {"name": "Women's Watches", "icon": "⌚", "keywords": ["women watch", "analog watch women", "rose gold watch", "dial watch women", "ladies watch"], "image": "https://images.unsplash.com/photo-1508685096489-7aacd43bd3b1?w=300&auto=format&fit=crop&q=70"},
            "smartwatches": {"name": "Smartwatches & Bands", "icon": "💡", "keywords": ["smartwatch", "smart watch", "fitness band", "smart band", "activity tracker"], "image": "https://images.unsplash.com/photo-1579586337278-3befd40fd17a?w=300&auto=format&fit=crop&q=70"},
            "digital": {"name": "Digital & Sports", "icon": "🔢", "keywords": ["digital watch", "sport watch", "military watch", "casio watch", "g-shock watch", "led watch"], "image": "https://images.unsplash.com/photo-1508057198894-247b23fe5ade?w=360&auto=format&fit=crop&q=70"},
            "couple": {"name": "Couple Watches", "icon": "💑", "keywords": ["couple watch", "pair watch", "his and her watch", "combo watch set"], "image": "https://images.unsplash.com/photo-1513094735237-8f2714d57c13?w=360&auto=format&fit=crop&q=70"}
        }
    },
    "bags": {
        "name": "Bags & Luggage",
        "icon": "👜",
        "badge": "BAGS & LUGGAGE",
        "color": "teal",
        "bg_gradient": "from-teal-500/10 via-emerald-500/10 to-cyan-500/10",
        "border_color": "border-teal-200 hover:border-teal-500",
        "tag_color": "bg-teal-100 text-teal-800",
        "image": "https://images.unsplash.com/photo-1584917865442-de89df76afd3?w=360&auto=format&fit=crop&q=70",
        "deal_count": "2,600+",
        "aliases": ["bags", "bag", "luggage", "wallets"],
        "keywords": ["handbag", "tote bag", "backpack", "leather wallet", "leather belt", "sling bag", "trolley bag", "travel suitcase", "duffel bag"],
        "subcategories": {
            "handbags": {"name": "Handbags & Totes", "icon": "👜", "keywords": ["handbag", "tote bag", "shoulder bag", "satchel bag", "structured bag"], "image": "https://images.unsplash.com/photo-1590874103328-eac38a683ce7?w=300&auto=format&fit=crop&q=70"},
            "slingbags": {"name": "Sling & Crossbody", "icon": "👝", "keywords": ["sling bag", "crossbody bag", "side bag", "mini messenger bag"], "image": "https://images.unsplash.com/photo-1548036328-c9fa89d128fa?w=300&auto=format&fit=crop&q=70"},
            "backpacks": {"name": "Backpacks & Duffels", "icon": "🎒", "keywords": ["backpack", "laptop backpack", "school bag", "duffel bag", "travel backpack"], "image": "https://images.unsplash.com/photo-1553062407-98eeb64c6a62?w=300&auto=format&fit=crop&q=70"},
            "wallets": {"name": "Wallets & Cardholders", "icon": "👛", "keywords": ["leather wallet", "card holder", "ladies purse", "money clip wallet", "bi-fold wallet"], "image": "https://images.unsplash.com/photo-1627123424574-724758594e93?w=300&auto=format&fit=crop&q=70"},
            "belts": {"name": "Belts", "icon": "🪙", "keywords": ["leather belt", "formal belt", "casual belt", "reversible belt", "buckle belt"], "image": "https://images.unsplash.com/photo-1624222247344-550fb60583dc?w=300&auto=format&fit=crop&q=70"},
            "luggage": {"name": "Trolley & Travel Bags", "icon": "🧳", "keywords": ["trolley bag", "travel suitcase", "cabin luggage", "travel bag organizer"], "image": "https://images.unsplash.com/photo-1565026057447-bc90a3dceb87?w=300&auto=format&fit=crop&q=70"}
        }
    },
    "health": {
        "name": "Health & Nutrition",
        "icon": "💊",
        "badge": "HEALTH & NUTRITION",
        "color": "emerald",
        "bg_gradient": "from-emerald-500/10 via-teal-500/10 to-green-500/10",
        "border_color": "border-emerald-200 hover:border-emerald-500",
        "tag_color": "bg-emerald-100 text-emerald-800",
        "image": "https://images.unsplash.com/photo-1584308666744-24d5c474f2ae?w=360&auto=format&fit=crop&q=70",
        "deal_count": "2,400+",
        "aliases": ["health", "nutrition", "supplements", "vitamins"],
        "keywords": ["whey protein", "protein powder", "creatine supplement", "multivitamin tablets", "omega 3 capsules", "peanut butter", "protein bar", "bcaa"],
        "subcategories": {
            "protein": {"name": "Whey Protein & Isolate", "icon": "💪", "keywords": ["whey protein", "protein powder", "plant protein", "isolate protein", "gold standard whey"], "image": "https://images.unsplash.com/photo-1579722821273-0f6c7d44362f?w=300&auto=format&fit=crop&q=70"},
            "creatine": {"name": "Creatine & Pre-Workout", "icon": "⚡", "keywords": ["creatine monohydrate", "pre workout powder", "bcaa amino", "glutamine powder"], "image": "https://images.unsplash.com/photo-1546483875-ad9014c88eba?w=300&auto=format&fit=crop&q=70"},
            "vitamins": {"name": "Multivitamins & Omega-3", "icon": "💊", "keywords": ["multivitamin capsules", "vitamin c tablets", "vitamin d3", "omega 3 fish oil", "biotin capsules"], "image": "https://images.unsplash.com/photo-1471864190281-a93a3070b6de?w=300&auto=format&fit=crop&q=70"},
            "healthfoods": {"name": "Peanut Butter & Healthy Snacks", "icon": "🥜", "keywords": ["peanut butter", "energy bar", "protein bar", "muesli cereal", "granola", "chia seeds"], "image": "https://images.unsplash.com/photo-1590080875515-8a3a8dc5735e?w=300&auto=format&fit=crop&q=70"}
        }
    },
    "jewellery": {
        "name": "Jewellery & Accessories",
        "icon": "💍",
        "badge": "JEWELLERY & FASHION",
        "color": "pink",
        "bg_gradient": "from-pink-500/10 via-rose-500/10 to-amber-500/10",
        "border_color": "border-pink-200 hover:border-pink-500",
        "tag_color": "bg-pink-100 text-pink-800",
        "image": "https://images.unsplash.com/photo-1599643478518-a784e5dc4c8f?w=360&auto=format&fit=crop&q=70",
        "deal_count": "3,100+",
        "aliases": ["jewellery", "jewelry", "accessories_fashion"],
        "keywords": ["earrings jewellery", "necklace jewellery", "finger ring", "pendant necklace", "bangles jewellery", "bracelet jewellery", "sunglasses eyewear"],
        "subcategories": {
            "earrings": {"name": "Earrings & Jhumkas", "icon": "✨", "keywords": ["jhumka earrings", "stud earrings", "hoop earrings", "drop earrings", "chandbali earrings"], "image": "https://images.unsplash.com/photo-1630019852942-f89202989a59?w=300&auto=format&fit=crop&q=70"},
            "necklaces": {"name": "Necklaces & Pendants", "icon": "📿", "keywords": ["necklace set", "pendant chain", "choker necklace", "silver chain necklace", "mangalsutra"], "image": "https://images.unsplash.com/photo-1599643477877-530eb83abc8e?w=300&auto=format&fit=crop&q=70"},
            "rings": {"name": "Rings & Bracelets", "icon": "💍", "keywords": ["finger ring", "solitaire ring", "bracelet jewellery", "bangle set", "kada bracelet"], "image": "https://images.unsplash.com/photo-1605100804763-247f67b3557e?w=300&auto=format&fit=crop&q=70"},
            "sunglasses": {"name": "Sunglasses & Eyewear", "icon": "🕶️", "keywords": ["sunglasses", "shades sunglasses", "aviator sunglasses", "wayfarer sunglasses", "polarized sunglasses"], "image": "https://images.unsplash.com/photo-1511499767150-a48a237f0083?w=300&auto=format&fit=crop&q=70"}
        }
    },
    "gaming": {
        "name": "Gaming & PC",
        "icon": "🎮",
        "badge": "GAMING & PC",
        "color": "violet",
        "bg_gradient": "from-violet-500/10 via-purple-500/10 to-indigo-500/10",
        "border_color": "border-violet-200 hover:border-violet-500",
        "tag_color": "bg-violet-100 text-violet-800",
        "image": "https://images.unsplash.com/photo-1542751371-adc38448a05e?w=360&auto=format&fit=crop&q=70",
        "deal_count": "2,800+",
        "aliases": ["gaming", "pc_gaming", "consoles"],
        "keywords": ["gaming controller", "mechanical keyboard", "gaming mouse", "gaming headset", "gamepad", "ps5 console", "xbox controller"],
        "subcategories": {
            "keyboards": {"name": "Mechanical Keyboards", "icon": "⌨️", "keywords": ["mechanical keyboard", "rgb keyboard", "gaming keyboard", "wireless mechanical keyboard"], "image": "https://images.unsplash.com/photo-1587829741301-dc798b83add3?w=300&auto=format&fit=crop&q=70"},
            "mice": {"name": "Gaming Mice & Pads", "icon": "🖱️", "keywords": ["gaming mouse", "mouse pad gaming", "rgb gaming mouse", "wireless gaming mouse"], "image": "https://images.unsplash.com/photo-1615663245857-ac93bb7c39e7?w=300&auto=format&fit=crop&q=70"},
            "headsets": {"name": "Gaming Headsets", "icon": "🎧", "keywords": ["gaming headset", "7.1 gaming headphones", "mic headset gaming"], "image": "https://images.unsplash.com/photo-1599669454699-248893623440?w=300&auto=format&fit=crop&q=70"},
            "controllers": {"name": "Controllers & Consoles", "icon": "🎮", "keywords": ["game controller", "gamepad", "ps5 controller", "xbox controller", "joystick"], "image": "https://images.unsplash.com/photo-1600080972464-8e5f35f63d08?w=300&auto=format&fit=crop&q=70"}
        }
    },
    "toys_kids": {
        "name": "Toys & Baby",
        "icon": "🧸",
        "badge": "TOYS & BABY",
        "color": "yellow",
        "bg_gradient": "from-yellow-500/10 via-amber-500/10 to-orange-500/10",
        "border_color": "border-yellow-200 hover:border-yellow-500",
        "tag_color": "bg-yellow-100 text-yellow-800",
        "image": "https://images.unsplash.com/photo-1566576912321-d58ddd7a6088?w=360&auto=format&fit=crop&q=70",
        "deal_count": "5,000+",
        "aliases": ["toys", "baby", "kids", "games"],
        "keywords": ["kids toy", "board game", "baby diapers", "building blocks lego", "remote control car", "baby wipes"],
        "subcategories": {
            "boardgames": {"name": "Board Games & Puzzles", "icon": "🎲", "keywords": ["board game", "jigsaw puzzle", "monopoly game", "uno cards game", "chess board", "rubik cube"], "image": "https://images.unsplash.com/photo-1610890716171-6b1bb98ffd09?w=300&auto=format&fit=crop&q=70"},
            "toys": {"name": "Action Toys & LEGO", "icon": "🧸", "keywords": ["building blocks", "lego toy", "action figure toy", "remote control car", "baby doll toy"], "image": "https://images.unsplash.com/photo-1558877385-81a1c7e67d72?w=300&auto=format&fit=crop&q=70"},
            "baby": {"name": "Baby & Diapers", "icon": "👶", "keywords": ["baby diapers", "baby wipes", "baby lotion", "baby shampoo", "baby massage oil"], "image": "https://images.unsplash.com/photo-1515488042361-ee00e0ddd4e4?w=300&auto=format&fit=crop&q=70"}
        }
    },
    "books": {
        "name": "Books & Stationery",
        "icon": "📚",
        "badge": "BOOKS & STATIONERY",
        "color": "cyan",
        "bg_gradient": "from-cyan-500/10 via-blue-500/10 to-teal-500/10",
        "border_color": "border-cyan-200 hover:border-cyan-500",
        "tag_color": "bg-cyan-100 text-cyan-800",
        "image": "https://images.unsplash.com/photo-1497633762265-9d179a990aa6?w=360&auto=format&fit=crop&q=70",
        "deal_count": "2,200+",
        "aliases": ["books", "stationery", "novels", "reading"],
        "keywords": ["paperback book", "fiction novel", "self help book", "fountain pen", "hardcover notebook", "diary planner"],
        "subcategories": {
            "novels": {"name": "Fiction & Bestsellers", "icon": "📖", "keywords": ["fiction novel", "bestseller book", "thriller novel", "paperback book", "hardcover novel"], "image": "https://images.unsplash.com/photo-1544716278-ca5e3f4abd8c?w=300&auto=format&fit=crop&q=70"},
            "selfhelp": {"name": "Self-Help & Business", "icon": "💡", "keywords": ["self help book", "business book", "finance book", "psychology book", "biography book"], "image": "https://images.unsplash.com/photo-1589829085413-56de8ae18c73?w=300&auto=format&fit=crop&q=70"},
            "stationery": {"name": "Pens & Notebooks", "icon": "✏️", "keywords": ["fountain pen", "ruled notebook", "journal diary", "sketchbook", "gel pen set"], "image": "https://images.unsplash.com/photo-1583485088034-697b5bc54ccd?w=300&auto=format&fit=crop&q=70"}
        }
    },
    "grocery": {
        "name": "Grocery & Gourmet",
        "icon": "🍵",
        "badge": "GROCERY & GOURMET",
        "color": "lime",
        "bg_gradient": "from-lime-500/10 via-emerald-500/10 to-green-500/10",
        "border_color": "border-lime-200 hover:border-lime-500",
        "tag_color": "bg-lime-100 text-lime-800",
        "image": "https://images.unsplash.com/photo-1542838132-92c53300491e?w=360&auto=format&fit=crop&q=70",
        "deal_count": "1,800+",
        "aliases": ["grocery", "gourmet", "food", "beverages"],
        "keywords": ["green tea", "instant coffee", "dry fruits nuts", "dark chocolate", "olive oil bottle", "raw honey", "almonds dry fruit"],
        "subcategories": {
            "tea_coffee": {"name": "Tea & Coffee Blends", "icon": "☕", "keywords": ["green tea bags", "instant coffee", "filter coffee powder", "masala tea blend"], "image": "https://images.unsplash.com/photo-1514432324607-a09d9b4aefdd?w=300&auto=format&fit=crop&q=70"},
            "dryfruits": {"name": "Dry Fruits & Nuts", "icon": "🥜", "keywords": ["raw almonds", "cashew nuts", "walnuts kernel", "raisins kismis", "pistachio nuts"], "image": "https://images.unsplash.com/photo-1596040033229-a9821ebd058d?w=300&auto=format&fit=crop&q=70"},
            "chocolates": {"name": "Chocolates & Sweets", "icon": "🍫", "keywords": ["dark chocolate bar", "chocolate cookies", "wafer biscuits", "assorted sweets box"], "image": "https://images.unsplash.com/photo-1511381939415-e44015466834?w=300&auto=format&fit=crop&q=70"}
        }
    },
    "automotive": {
        "name": "Auto & Bikes",
        "icon": "🚗",
        "badge": "BIKE & CAR",
        "color": "orange",
        "bg_gradient": "from-orange-500/10 via-red-500/10 to-amber-500/10",
        "border_color": "border-orange-200 hover:border-orange-500",
        "tag_color": "bg-orange-100 text-orange-800",
        "image": "https://images.unsplash.com/photo-1503376780353-7e6692767b70?w=360&auto=format&fit=crop&q=70",
        "deal_count": "3,000+",
        "aliases": ["automotive", "car", "bike", "auto"],
        "keywords": ["bike helmet", "riding gloves", "car mobile holder", "car charger", "car vacuum cleaner", "car body cover", "bike cover"],
        "subcategories": {
            "helmets": {"name": "Helmets & Riding Gear", "icon": "🪖", "keywords": ["motorcycle helmet", "full face helmet", "riding gloves", "bike face mask", "riding jacket"], "image": "https://images.unsplash.com/photo-1558981806-ec527fa84c39?w=300&auto=format&fit=crop&q=70"},
            "caraccessories": {"name": "Car Accessories", "icon": "🚗", "keywords": ["car mobile holder", "car vacuum cleaner", "fast car charger", "tire inflator pump", "car air freshener perfume"], "image": "https://images.unsplash.com/photo-1563720223185-11003d516935?w=300&auto=format&fit=crop&q=70"}
        }
    },
    "pets": {
        "name": "Pet Supplies",
        "icon": "🐾",
        "badge": "PET SUPPLIES",
        "color": "sky",
        "bg_gradient": "from-sky-500/10 via-cyan-500/10 to-blue-500/10",
        "border_color": "border-sky-200 hover:border-sky-500",
        "tag_color": "bg-sky-100 text-sky-800",
        "image": "https://images.unsplash.com/photo-1450778869180-41d0601e046e?w=360&auto=format&fit=crop&q=70",
        "deal_count": "1,500+",
        "aliases": ["pets", "pet", "pet_supplies"],
        "keywords": ["dog food", "cat food", "puppy food", "pedigree dog", "whiskas cat", "drools pet", "cat litter", "dog leash collar", "pet grooming shampoo", "pet chew toy"],
        "subcategories": {
            "dogfood": {"name": "Dog Food & Treats", "icon": "🐕", "keywords": ["dog food", "puppy food", "pedigree dog", "drools dog", "dog biscuits treats", "chew bones dog"], "image": "https://images.unsplash.com/photo-1589924691995-400dc9ecc119?w=300&auto=format&fit=crop&q=70"},
            "catfood": {"name": "Cat Food & Litter", "icon": "🐈", "keywords": ["cat food", "kitten food", "whiskas cat", "cat litter sand", "purepet cat food", "me-o cat"], "image": "https://images.unsplash.com/photo-1514888286974-6c03e2ca1dba?w=300&auto=format&fit=crop&q=70"},
            "accessories": {"name": "Pet Grooming & Toys", "icon": "🎾", "keywords": ["pet shampoo dog", "dog leash collar", "pet harness", "dog bed mattress", "pet chew toy", "pet grooming brush"], "image": "https://images.unsplash.com/photo-1576201836106-db1758fd1c97?w=300&auto=format&fit=crop&q=70"}
        }
    },
    "miscellaneous": {
        "name": "Miscellaneous & More",
        "icon": "📦",
        "badge": "EVERYTHING ELSE",
        "color": "amber",
        "bg_gradient": "from-amber-500/10 via-orange-500/10 to-stone-500/10",
        "border_color": "border-amber-200 hover:border-amber-500",
        "tag_color": "bg-amber-100 text-amber-800",
        "image": "https://images.unsplash.com/photo-1586528116311-ad8dd3c8310d?w=360&auto=format&fit=crop&q=70",
        "deal_count": "1,200+",
        "aliases": ["miscellaneous", "other", "general", "misc", "more"],
        "keywords": ["stationery", "organizer", "utility", "gadget", "gift items", "daily essentials", "umbrella", "torch flashlight", "candle pack", "calculator"],
        "subcategories": {
            "daily_deals": {"name": "Daily Loot & Steals", "icon": "⚡", "keywords": ["daily loot", "flash deal", "super saver", "combo pack offer"], "image": "https://images.unsplash.com/photo-1607082348824-0a96f2a4b9da?w=360&auto=format&fit=crop&q=70"},
            "stationery_craft": {"name": "Crafts & Utilities", "icon": "✂️", "keywords": ["craft supplies", "utility scissors", "adhesive tape", "desk calculator", "office stapler", "document folder"], "image": "https://images.unsplash.com/photo-1583485088034-697b5bc54ccd?w=300&auto=format&fit=crop&q=70"},
            "lifestyle": {"name": "Lifestyle & Essentials", "icon": "🌟", "keywords": ["rain umbrella", "raincoat", "keychain", "torch light", "air freshener", "travel neck pillow"], "image": "https://images.unsplash.com/photo-1513542789411-b6a5d4f31634?w=360&auto=format&fit=crop&q=70"}
        }
    }
}

def get_category_info(category_key: str) -> dict | None:
    """Returns metadata for a given category key or alias, or None if unknown."""
    if not category_key:
        return None
    key_clean = category_key.strip().lower()
    if key_clean in UNIVERSAL_CATEGORIES:
        return UNIVERSAL_CATEGORIES[key_clean]
    for cat, info in UNIVERSAL_CATEGORIES.items():
        if key_clean in info.get("aliases", []):
            return info
    return None
