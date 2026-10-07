"""The tools the harness can run, and the JSON that describes them to the model."""

import json
from statistics import mean, median

import requests

from wiki_client import get_monthly_pageviews, resolve_star

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
]

# What the harness runs: tool name -> Python function.
TOOL_MAP = {"get_weather": get_weather, "buzz_momentum": buzz_momentum}


def run_tool(name: str, args: dict) -> str:
    """Run one tool call. Models invent tool names and arguments; never let that crash the loop."""
    if name not in TOOL_MAP:
        return json.dumps({"error": f"Unknown tool '{name}'. Available: {list(TOOL_MAP)}"})
    try:
        return TOOL_MAP[name](**args)
    except TypeError as e:
        return json.dumps({"error": f"Bad arguments for {name}: {e}"})
