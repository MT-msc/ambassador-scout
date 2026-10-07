"""The tools the harness can run, and the JSON that describes them to the model."""

import json
import re
import time
from datetime import date
from statistics import mean, median

import requests

from wiki_client import (CATEGORY_QUERIES, find_candidates, get_monthly_pageviews,
                         get_pageviews_bulk, resolve_star)

# Open-Meteo is free and needs no API key.
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


def get_weather(location: str) -> str:
    """Get the current weather for a location."""
    try:
        places = requests.get(GEOCODE_URL, params={"name": location, "count": 1}, timeout=10).json()
        if not places.get("results"):
            return json.dumps({"error": f"City '{location}' was not found."})
        place = places["results"][0]

        current = requests.get(
            FORECAST_URL,
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,relative_humidity_2m,wind_speed_10m",
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
            },
            timeout=10,
        ).json()["current"]
    except requests.RequestException as e:
        # The model cannot see an exception. Return something it can reason about.
        return json.dumps({"error": f"Weather service failed: {e}"})

    return json.dumps({
        "location": place["name"],
        "temp_f": current["temperature_2m"],
        "humidity": current["relative_humidity_2m"],
        "wind_mph": current["wind_speed_10m"],
    })


# --- Shared helpers for the Ambassador Scout tools ---

DATA_NOTE = (
    "Wikipedia pageviews measure public attention (human readers), "
    "not sales, streams or official fan counts."
)


def _error(message: str) -> str:
    return json.dumps({"error": message})


def _resolve_or_error(star_name: str) -> tuple[dict | None, str | None]:
    """Resolve a star, or return a JSON message telling the model what to do instead."""
    star = resolve_star(star_name)
    if star["status"] == "not_found":
        return None, _error(
            f"No celebrity or group found for '{star_name}'. Ask the user for the full "
            "stage name or group name (e.g. 'Kim Seok-jin' instead of 'Jin')."
        )
    if star["status"] == "ambiguous":
        return None, json.dumps({
            "error": f"'{star_name}' matches several people. Ask the user which one they "
                     "mean, then call this tool again with that candidate's id.",
            "candidates": star["candidates"],
        })
    return star, None


# --- buzz_momentum ---

# A change of at least this much over 3 months counts as a real move.
SHORT_TERM_THRESHOLD_PCT = 20
# Against the start of the window, attention must move this much to count.
LONG_TERM_THRESHOLD_PCT = 25
# Within this share of the window's peak counts as "at the peak".
NEAR_PEAK_PCT = 85
# Below this many monthly views, percentages are mostly noise.
LOW_VISIBILITY_VIEWS = 300
# A month with at least this many times the window's median views is a spike.
SPIKE_MULTIPLIER = 2


def _pct_change(new: float, old: float) -> float | None:
    return round((new - old) / old * 100, 1) if old else None


def _momentum_stats(views: list[int]) -> dict:
    """Trend numbers and a label from monthly views (oldest first, at least 6 months)."""
    recent = mean(views[-3:])
    previous = mean(views[-6:-3])
    window_start = mean(views[:3])
    peak_index = max(range(len(views)), key=lambda i: views[i])

    short_term = _pct_change(recent, previous)
    long_term = _pct_change(recent, window_start)
    recent_vs_peak = round(recent / views[peak_index] * 100, 1) if views[peak_index] else 0
    typical = median(views)
    spike_indexes = [i for i, v in enumerate(views) if typical and v >= SPIKE_MULTIPLIER * typical]

    if recent < LOW_VISIBILITY_VIEWS:
        label = "Low visibility"
    elif short_term is None or long_term is None:
        label = "New"  # had no views at the start: the article is newer than the window
    elif short_term >= SHORT_TERM_THRESHOLD_PCT or (
            long_term >= LONG_TERM_THRESHOLD_PCT and short_term > -SHORT_TERM_THRESHOLD_PCT / 2):
        label = "Rising"
    elif short_term <= -SHORT_TERM_THRESHOLD_PCT or (
            long_term <= -LONG_TERM_THRESHOLD_PCT and short_term < SHORT_TERM_THRESHOLD_PCT / 2):
        label = "Fading"
    elif recent_vs_peak >= NEAR_PEAK_PCT:
        label = "Peaking"
    else:
        label = "Steady"

    return {
        "label": label,
        "recent_3mo_avg_views": round(recent),
        "short_term_change_pct": short_term,
        "long_term_change_pct": long_term,
        "peak_index": peak_index,
        "recent_vs_peak_pct": recent_vs_peak,
        "spike_indexes": spike_indexes,
    }


def buzz_momentum(star_name: str, months: int = 12) -> str:
    """Is a celebrity's public attention rising, peaking, steady or fading?"""
    try:
        months = int(months)
    except (TypeError, ValueError):
        return _error(f"months must be a whole number, got {months!r}. Use 12 by default.")
    if not 6 <= months <= 36:
        return _error("months must be between 6 and 36. Use 12 unless the user asks otherwise.")

    try:
        star, error = _resolve_or_error(star_name)
        if error:
            return error
        title = star["language_titles"].get("en")
        if not title:
            return _error(
                f"{star['label']} has no English Wikipedia article, so momentum can't be "
                "measured. Try fan_geography to see where they are popular instead."
            )
        series = get_monthly_pageviews("en", title, months)
    except requests.RequestException as e:
        return _error(f"Wikipedia/Wikidata is not responding ({e}). Try again in a minute.")

    views = [row["views"] for row in series]
    stats = _momentum_stats(views)
    peak = series[stats["peak_index"]]

    return json.dumps({
        "star": star["label"],
        "star_id": star["star_id"],
        "description": star["description"],
        "label": stats["label"],
        "recent_3mo_avg_views": stats["recent_3mo_avg_views"],
        "short_term_change_pct": stats["short_term_change_pct"],
        "long_term_change_pct": stats["long_term_change_pct"],
        "peak_month": peak["month"],
        "peak_views": peak["views"],
        "recent_vs_peak_pct": stats["recent_vs_peak_pct"],
        # One-off events (a viral moment, an award show) rather than steady growth.
        "spike_months": [series[i]["month"] for i in stats["spike_indexes"]],
        "monthly_views": series,
        "source": f"English Wikipedia pageviews, last {months} finished months",
        "note": DATA_NOTE,
    })


# --- fan_geography ---

# Language editions we measure, and the markets their readers mostly come from.
# Capping the list keeps a star with 150+ articles to ~25 requests.
LANGUAGE_MARKETS = {
    "en": ("English", "Global English-speaking (US, UK, India, Philippines, Australia...)"),
    "ko": ("Korean", "South Korea"),
    "ja": ("Japanese", "Japan"),
    "zh": ("Chinese", "Taiwan, Hong Kong, Singapore, Chinese diaspora"),
    "es": ("Spanish", "Spain and Latin America"),
    "pt": ("Portuguese", "Brazil and Portugal"),
    "fr": ("French", "France and francophone countries"),
    "de": ("German", "Germany, Austria, Switzerland"),
    "it": ("Italian", "Italy"),
    "ru": ("Russian", "Russia and Central Asia"),
    "uk": ("Ukrainian", "Ukraine"),
    "pl": ("Polish", "Poland"),
    "nl": ("Dutch", "Netherlands and Belgium"),
    "sv": ("Swedish", "Sweden"),
    "tr": ("Turkish", "Turkey"),
    "ar": ("Arabic", "Middle East and North Africa"),
    "fa": ("Persian", "Iran"),
    "he": ("Hebrew", "Israel"),
    "hi": ("Hindi", "India"),
    "id": ("Indonesian", "Indonesia"),
    "ms": ("Malay", "Malaysia"),
    "th": ("Thai", "Thailand"),
    "vi": ("Vietnamese", "Vietnam"),
    "tl": ("Tagalog", "Philippines"),
}

GEOGRAPHY_CAVEATS = [
    "Language is not country: English is read worldwide, so a high English share means "
    "broad global reach, not specifically the US.",
    "Korean interest is under-counted: most Koreans use Namuwiki and Naver, not Wikipedia.",
    "Wikipedia is blocked in mainland China, so Chinese views come mostly from Taiwan, "
    "Hong Kong, Singapore and the diaspora.",
    "Only each language's main article is counted (not redirects), and only the "
    f"{len(LANGUAGE_MARKETS)} languages listed in LANGUAGE_MARKETS.",
]


def fan_geography(star_name: str, months: int = 3) -> str:
    """Where in the world is the attention on a star coming from?"""
    try:
        months = int(months)
    except (TypeError, ValueError):
        return _error(f"months must be a whole number, got {months!r}. Use 3 by default.")
    if not 1 <= months <= 12:
        return _error("months must be between 1 and 12. Use 3 unless the user asks otherwise.")

    try:
        star, error = _resolve_or_error(star_name)
        if error:
            return error
    except requests.RequestException as e:
        return _error(f"Wikidata is not responding ({e}). Try again in a minute.")

    articles = [(lang, title) for lang, title in star["language_titles"].items()
                if lang in LANGUAGE_MARKETS]
    if not articles:
        return _error(f"{star['label']} has no Wikipedia article in any tracked language.")

    results = get_pageviews_bulk(articles, months)
    totals = {lang: sum(row["views"] for row in rows)
              for (lang, _), rows in results.items() if rows is not None}
    failed = [lang for (lang, _), rows in results.items() if rows is None]
    grand_total = sum(totals.values())
    if not totals:
        return _error("Wikipedia pageviews are not responding. Try again in a minute.")
    if grand_total == 0:
        return _error(f"{star['label']} had no measurable Wikipedia views in the last "
                      f"{months} months, so fan geography can't be estimated.")

    ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)
    markets = [{
        "language": LANGUAGE_MARKETS[lang][0],
        "code": lang,
        "market": LANGUAGE_MARKETS[lang][1],
        "views": views,
        "share_pct": round(views / grand_total * 100, 1),
    } for lang, views in ranked if views > 0]  # all of them, so any market asked about is answerable

    return json.dumps({
        "star": star["label"],
        "star_id": star["star_id"],
        "description": star["description"],
        "markets": markets,
        # Tracked languages with no article for this star, i.e. likely weak markets.
        "no_article_in": [LANGUAGE_MARKETS[lang][0] for lang in LANGUAGE_MARKETS
                          if lang not in star["language_titles"]],
        "total_views_measured": grand_total,
        # Articles in many languages is itself a sign of international reach.
        "wikipedia_languages_total": len(star["language_titles"]),
        "languages_failed": failed,
        "source": f"Wikipedia pageviews per language edition, last {months} finished months",
        "caveats": GEOGRAPHY_CAVEATS,
        "note": DATA_NOTE,
    })


# --- rising_star_finder ---

# Below this, a candidate has too little attention to judge.
MIN_SIGNAL_VIEWS = 1000
# Months of history fetched for every candidate.
FINDER_MONTHS = 12
# Candidate lists and their pageviews barely change within a day, so reuse them.
CACHE_SECONDS = 6 * 60 * 60
_finder_cache: dict[tuple, tuple[float, list, dict, bool]] = {}


def _display_name(title: str) -> str:
    """'Kiss of Life (group)' -> 'Kiss of Life'"""
    return re.sub(r" \([^)]*\)$", "", title)


def _candidates_with_views(category: str, debuted_after: int) -> tuple[list, dict, bool]:
    key = (category, debuted_after)
    cached = _finder_cache.get(key)
    if cached and time.time() - cached[0] < CACHE_SECONDS:
        return cached[1], cached[2], cached[3]

    candidates, live = find_candidates(category, debuted_after)
    views = get_pageviews_bulk([("en", c["title"]) for c in candidates], FINDER_MONTHS)
    # Only cache complete answers, so a temporary outage isn't remembered for 6 hours.
    if live and all(rows is not None for rows in views.values()):
        _finder_cache[key] = (time.time(), candidates, views, live)
    return candidates, views, live


def rising_star_finder(category: str, debuted_after: int = 2022,
                       max_monthly_views: int = 100000, limit: int = 5) -> str:
    """Find stars whose attention is growing fast but who aren't mainstream yet."""
    if category not in CATEGORY_QUERIES:
        return _error(f"Unknown category '{category}'. Use one of: {list(CATEGORY_QUERIES)}.")
    try:
        debuted_after, max_monthly_views, limit = (
            int(debuted_after), int(max_monthly_views), int(limit))
    except (TypeError, ValueError):
        return _error("debuted_after, max_monthly_views and limit must be whole numbers.")
    if not 2010 <= debuted_after <= date.today().year:
        return _error(f"debuted_after must be between 2010 and {date.today().year}.")
    if max_monthly_views < MIN_SIGNAL_VIEWS:
        return _error(f"max_monthly_views must be at least {MIN_SIGNAL_VIEWS}.")
    limit = max(1, min(limit, 10))

    candidates, views, live = _candidates_with_views(category, debuted_after)
    if not candidates:
        return _error("The Wikidata candidate search is not responding and there is no "
                      f"backup list for '{category}'. Try again in a minute, or try 'kpop_group'.")

    prospects, already_big, too_new = [], [], []
    for candidate in candidates:
        rows = views.get(("en", candidate["title"]))
        if rows is None:
            continue
        series = [row["views"] for row in rows]
        if mean(series[-3:]) < MIN_SIGNAL_VIEWS:
            continue
        # Many stars' English titles were redirects (~100 views/month) until a real article
        # was written, which looks like 10-100x "growth". Keep only the months after the
        # last near-empty month before the recent 3, so we measure attention, not article age.
        floor = max(100, 0.1 * mean(series[-3:]))
        near_empty = [i for i in range(len(series) - 3) if series[i] < floor]
        if near_empty:
            series = series[near_empty[-1] + 1:]
        name = _display_name(candidate["title"])
        if len(series) < 6:
            too_new.append(name)
            continue
        stats = _momentum_stats(series)
        recent = stats["recent_3mo_avg_views"]
        if recent > max_monthly_views:
            already_big.append((name, recent))
            continue
        if stats["label"] == "Fading":
            continue  # grew over the year but dropping now: not a rising star
        prospects.append({
            "name": name,
            "star_id": candidate["star_id"],
            "debut_year": candidate["debut_year"],
            "label": stats["label"],
            "recent_3mo_avg_views": recent,
            "growth_pct": stats["long_term_change_pct"],
            "short_term_change_pct": stats["short_term_change_pct"],
            # A spike in the last 3 months may be one viral moment, not lasting growth.
            "recent_spike": any(i >= len(series) - 3 for i in stats["spike_indexes"]),
        })

    prospects.sort(key=lambda p: (p["growth_pct"] or 0, p["short_term_change_pct"] or 0),
                   reverse=True)
    already_big.sort(key=lambda item: item[1], reverse=True)

    return json.dumps({
        "category": category,
        "debuted_after": debuted_after,
        "max_monthly_views": max_monthly_views,
        "prospects": prospects[:limit],
        "excluded_already_mainstream": [name for name, _ in already_big[:5]],
        # Under 6 months of meaningful data: just debuted, or their English article is new.
        "too_new_to_judge": too_new[:5],
        "candidates_checked": len(candidates),
        "candidate_source": ("Wikidata + hand-checked list" if live
                             else "hand-checked list only (Wikidata search unavailable)"),
        "method": (
            "Growth compares the last 3 finished months with the first 3 months of the "
            f"past {FINDER_MONTHS} (or since the article appeared) on English Wikipedia. "
            f"Stars above {max_monthly_views:,} monthly views are excluded as already mainstream."
        ),
        "note": DATA_NOTE,
    })


# What the model sees: the "set notes" in the screenplay.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather (temperature, humidity, wind) for a city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City name, e.g. 'New York'"},
                },
                "required": ["location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buzz_momentum",
            "description": (
                "Measure whether a celebrity's or group's public attention is Rising, Peaking, "
                "Steady, Fading, New or Low visibility, using monthly English Wikipedia "
                "pageviews. Use it when the user asks if a star is trending, hot, growing, "
                "declining or still relevant, or when judging an ambassador's timing. "
                "Call it once per star when comparing several. Returns the label, recent "
                "average views, 3-month and full-window % change, the peak month, and the "
                "monthly series."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "star_name": {
                        "type": "string",
                        "description": (
                            "Stage name or group name as commonly written in English, e.g. "
                            "'Stray Kids', 'Olivia Rodrigo', 'Jennie'. If an earlier call "
                            "returned candidates, pass the chosen candidate's id instead, "
                            "e.g. 'Q178348'."
                        ),
                    },
                    "months": {
                        "type": "integer",
                        "description": (
                            "How many finished months to analyze, 6 to 36. Default 12. "
                            "Use 24 or 36 only if the user asks about a longer period."
                        ),
                    },
                },
                "required": ["star_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fan_geography",
            "description": (
                "Estimate where in the world a celebrity's or group's audience is, from how "
                "often their Wikipedia article is read in each language (Korean, Japanese, "
                "Spanish, Portuguese, Indonesian, Thai, etc.), mapped to likely markets. Use it "
                "when the user asks where a star is popular, who their international fans are, "
                "or whether a star fits a brand's target countries. Returns every tracked "
                "market ranked by share of views, plus caveats that must be passed on (e.g. "
                "Korean interest is under-counted)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "star_name": {
                        "type": "string",
                        "description": (
                            "Stage name or group name as commonly written in English, e.g. "
                            "'BLACKPINK', 'Bad Bunny'. If an earlier call returned "
                            "candidates, pass the chosen candidate's id instead, e.g. 'Q178348'."
                        ),
                    },
                    "months": {
                        "type": "integer",
                        "description": "How many recent finished months to total, 1 to 12. Default 3.",
                    },
                },
                "required": ["star_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rising_star_finder",
            "description": (
                "Scout for up-and-coming stars: finds recently debuted K-pop groups or idols "
                "whose English Wikipedia attention is growing fastest but who are not yet "
                "mainstream (so likely cheaper to sign). Use it when the user asks for rising, "
                "emerging, underrated or affordable talent, or who to sign before they blow "
                "up. Returns ranked prospects with growth %, plus which stars were left out "
                "for being already mainstream or too new to judge. Takes several seconds."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["kpop_group", "kpop_idol"],
                        "description": (
                            "'kpop_group' for groups and bands; 'kpop_idol' for individual "
                            "artists, including members of groups."
                        ),
                    },
                    "debuted_after": {
                        "type": "integer",
                        "description": (
                            "Only include stars who debuted in or after this year. "
                            "Default 2022. Use a recent year for 'new' or 'rookie' talent."
                        ),
                    },
                    "max_monthly_views": {
                        "type": "integer",
                        "description": (
                            "Stars above this many monthly English Wikipedia views count as "
                            "already mainstream and are excluded. Default 100000. Lower it "
                            "(e.g. 20000) for truly under-the-radar picks or a small budget."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "How many prospects to return, 1 to 10. Default 5.",
                    },
                },
                "required": ["category"],
            },
        },
    },
]

# What the harness runs: tool name -> Python function.
TOOL_MAP = {
    "get_weather": get_weather,
    "buzz_momentum": buzz_momentum,
    "fan_geography": fan_geography,
    "rising_star_finder": rising_star_finder,
}


def run_tool(name: str, args: dict) -> str:
    """Run one tool call. Models invent tool names and arguments; never let that crash the loop."""
    if name not in TOOL_MAP:
        return json.dumps({"error": f"Unknown tool '{name}'. Available: {list(TOOL_MAP)}"})
    try:
        return TOOL_MAP[name](**args)
    except TypeError as e:
        return json.dumps({"error": f"Bad arguments for {name}: {e}"})
