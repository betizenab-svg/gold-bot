"""Words subscribers see, in English and Amharic.

MESSAGE_LANGUAGE (GitHub variable): "en" (default), "am" (Amharic only) or
"both" (English with Amharic underneath). The Amharic below was written by
the developer and must be checked by a native speaker before it is switched
on.
"""

from __future__ import annotations

import os

# key: (English, Amharic). {placeholders} are filled in at send time.
STRINGS: dict[str, tuple[str, str]] = {
    "buy": ("BUY", "ግዛ"),
    "sell": ("SELL", "ሽጥ"),
    "entry": ("Entry", "መግቢያ"),
    "stop": ("Stop", "ማቆሚያ"),
    "target1": ("Target 1", "ዒላማ 1"),
    "target2": ("Target 2", "ዒላማ 2"),
    "pips": ("pips", "ፒፕ"),
    "per_lot": ("per 0.01 lot", "በ0.01 ሎት"),
    "bank_half": ("close half, move stop to entry", "ግማሹን ዝጉ፣ ማቆሚያውን ወደ መግቢያ ያዛውሩ"),
    "reward_risk": ("Reward to risk", "ትርፍ ከስጋት አንጻር"),
    "why": ("Why", "ለምን"),
    "reason": ("Reason", "ምክንያት"),
    "lots": ("Lots for {risk}% risk", "ለ{risk}% ስጋት የሎት መጠን"),
    "lots_warning": ("too big for this balance", "ለዚህ ሂሳብ በጣም ትልቅ ነው"),
    "sent": ("Sent", "የተላከበት"),
    "time": ("Time", "ሰዓት"),
    "order_limit_buy": (
        "Buy limit: place it now; it opens only if the price comes back to the entry.",
        "የግዢ ሊሚት፦ አሁን ያስቀምጡት፤ ዋጋው ወደ መግቢያው ከተመለሰ ብቻ ይከፈታል።",
    ),
    "order_limit_sell": (
        "Sell limit: place it now; it opens only if the price comes back to the entry.",
        "የሽያጭ ሊሚት፦ አሁን ያስቀምጡት፤ ዋጋው ወደ መግቢያው ከተመለሰ ብቻ ይከፈታል።",
    ),
    "order_stop_buy": (
        "Buy stop: it opens only if the price rises to the entry.",
        "የግዢ ስቶፕ፦ ዋጋው ወደ መግቢያው ከፍ ካለ ብቻ ይከፈታል።",
    ),
    "order_stop_sell": (
        "Sell stop: it opens only if the price falls to the entry.",
        "የሽያጭ ስቶፕ፦ ዋጋው ወደ መግቢያው ዝቅ ካለ ብቻ ይከፈታል።",
    ),
    "order_market_buy": ("Buy now at the market price.", "አሁኑኑ በገበያ ዋጋ ይግዙ።"),
    "order_market_sell": ("Sell now at the market price.", "አሁኑኑ በገበያ ዋጋ ይሽጡ።"),
    "valid_until": (
        "Cancel it if it has not opened by {time}.",
        "እስከ {time} ካልተከፈተ ይሰርዙት።",
    ),
    "good_morning": ("Good morning", "እንደምን አደራችሁ"),
    "key_levels": ("Gold key levels", "የወርቅ ቁልፍ የዋጋ ደረጃዎች"),
    "todays_news": ("Today's big news", "የዛሬ ዋና ዋና ዜናዎች"),
    "no_news": ("No big news today.", "ዛሬ ትልቅ ዜና የለም።"),
    "leaning": ("Leaning", "አዝማሚያ"),
    "no_new_trades": ("No new trades", "አዲስ ንግድ የለም"),
    "weekly_results": ("Weekly results", "የሳምንቱ ውጤት"),
    "losses_included": ("Every trade counted, losses included.", "ሁሉም ንግዶች ተቆጥረዋል፤ ኪሳራዎችም ጭምር።"),
    "lesson": ("Lesson of the week", "የሳምንቱ ትምህርት"),
    "trial": ("TRIAL", "ሙከራ"),
    # One plain line on why each kind of trade was sent.
    "why_PIN_BAR_REJECTION_LONG": (
        "A long-tailed candle shows buyers pushed the price back up from a support area.",
        "ረጅም ጭራ ያለው ሻማ ገዢዎች ዋጋውን ከድጋፍ ቦታ መልሰው እንደገፉት ያሳያል።",
    ),
    "why_PIN_BAR_REJECTION_SHORT": (
        "A long-tailed candle shows sellers pushed the price back down from a resistance area.",
        "ረጅም ጭራ ያለው ሻማ ሻጮች ዋጋውን ከተቃውሞ ቦታ መልሰው እንደገፉት ያሳያል።",
    ),
    "why_ENGULFING_ZONE_LONG": (
        "A strong up candle swallowed the one before it at a tested buy zone.",
        "ጠንካራ ወደ ላይ የወጣ ሻማ በተፈተሸ የግዢ ቀጠና ላይ ቀዳሚውን ሻማ ዋጠው።",
    ),
    "why_ENGULFING_ZONE_SHORT": (
        "A strong down candle swallowed the one before it at a tested sell zone.",
        "ጠንካራ ወደ ታች የወረደ ሻማ በተፈተሸ የሽያጭ ቀጠና ላይ ቀዳሚውን ሻማ ዋጠው።",
    ),
    "why_H2_PULLBACK_LONG": (
        "The uptrend paused twice and is now moving up again.",
        "ወደ ላይ ያለው አዝማሚያ ሁለት ጊዜ ቆም ብሎ አሁን እንደገና ወደ ላይ እየሄደ ነው።",
    ),
    "why_L2_PULLBACK_SHORT": (
        "The downtrend paused twice and is now moving down again.",
        "ወደ ታች ያለው አዝማሚያ ሁለት ጊዜ ቆም ብሎ አሁን እንደገና ወደ ታች እየሄደ ነው።",
    ),
    "why_INSIDE_BAR_TRAP_LONG": (
        "A fake break below a small range trapped sellers, and the price snapped back up.",
        "ከትንሽ ክልል በታች የተደረገ የውሸት መውጣት ሻጮችን አጥምዷል፤ ዋጋው ወደ ላይ ተመልሷል።",
    ),
    "why_INSIDE_BAR_TRAP_SHORT": (
        "A fake break above a small range trapped buyers, and the price snapped back down.",
        "ከትንሽ ክልል በላይ የተደረገ የውሸት መውጣት ገዢዎችን አጥምዷል፤ ዋጋው ወደ ታች ተመልሷል።",
    ),
    "why_ZONE_BOUNCE_LONG": (
        "The price is back at a zone where big buyers stepped in before.",
        "ዋጋው ቀደም ሲል ትላልቅ ገዢዎች ወደገቡበት ቀጠና ተመልሷል።",
    ),
    "why_ZONE_BOUNCE_SHORT": (
        "The price is back at a zone where big sellers stepped in before.",
        "ዋጋው ቀደም ሲል ትላልቅ ሻጮች ወደገቡበት ቀጠና ተመልሷል።",
    ),
    "why_QUASIMODO_LONG": (
        "The price dipped below the last low, then broke higher: sellers are trapped.",
        "ዋጋው ከመጨረሻው ዝቅተኛ ነጥብ በታች ወርዶ ከዚያ ወደ ላይ ሰብሯል፤ ሻጮች ተጠምደዋል።",
    ),
    "why_QUASIMODO_SHORT": (
        "The price poked above the last high, then broke lower: buyers are trapped.",
        "ዋጋው ከመጨረሻው ከፍተኛ ነጥብ በላይ ወጥቶ ከዚያ ወደ ታች ሰብሯል፤ ገዢዎች ተጠምደዋል።",
    ),
    "why_OPENING_RANGE_BREAKOUT_LONG": (
        "The price broke above the range of the first 30 minutes after the session opened.",
        "ዋጋው ገበያው ከተከፈተ በኋላ የመጀመሪያዎቹን 30 ደቂቃዎች ክልል ወደ ላይ ሰብሯል።",
    ),
    "why_OPENING_RANGE_BREAKOUT_SHORT": (
        "The price broke below the range of the first 30 minutes after the session opened.",
        "ዋጋው ገበያው ከተከፈተ በኋላ የመጀመሪያዎቹን 30 ደቂቃዎች ክልል ወደ ታች ሰብሯል።",
    ),
    "why_ASIAN_RANGE_BREAKOUT_LONG": (
        "Gold broke above its quiet Asian-session range.",
        "ወርቅ ጸጥ ያለውን የእስያ ሰዓት ክልል ወደ ላይ ሰብሯል።",
    ),
    "why_ASIAN_RANGE_BREAKOUT_SHORT": (
        "Gold broke below its quiet Asian-session range.",
        "ወርቅ ጸጥ ያለውን የእስያ ሰዓት ክልል ወደ ታች ሰብሯል።",
    ),
    "why_default": (
        "The trend and a key price area line up for this trade.",
        "አዝማሚያውና ቁልፍ የዋጋ ቦታው ለዚህ ንግድ ተስማምተዋል።",
    ),
}


def language() -> str:
    value = (os.getenv("MESSAGE_LANGUAGE") or "en").strip().lower()
    return value if value in {"en", "am", "both"} else "en"


def t(key: str, **values: object) -> str:
    english, amharic = STRINGS[key]
    english, amharic = english.format(**values), amharic.format(**values)
    mode = language()
    if mode == "am":
        return amharic
    if mode == "both":
        joiner = " / " if len(english) <= 30 else "\n"
        return f"{english}{joiner}{amharic}"
    return english


def why_this_trade(strategy: object, direction: object) -> str:
    key = f"why_{str(strategy or '').upper()}_{str(direction or '').upper()}"
    return t(key if key in STRINGS else "why_default")
