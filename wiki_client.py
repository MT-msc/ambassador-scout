"""Shared helpers for Wikidata and Wikipedia.

These return plain Python data. tools.py turns it into JSON for the model.
"""

import calendar
import re
from datetime import date
from urllib.parse import quote

import requests

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
PAGEVIEWS_API = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"

USER_AGENT = "AmbassadorScout/0.1 (https://github.com/MT-msc/ambassador-scout)"
TIMEOUT = 10

# A search result is probably a star if its description mentions one of these.
STAR_KEYWORDS = ("singer", "rapper", "actor", "actress", "band", "group", "musician",
                 "idol", "songwriter", "entertainer", "model", "dancer", "duo", "personality")

# Sitelink keys that end in "wiki" but are not a language edition of Wikipedia.
NON_LANGUAGE_WIKIS = {"commonswiki", "specieswiki", "metawiki", "wikidatawiki",
                      "mediawikiwiki", "sourceswiki", "simplewiki", "incubatorwiki"}


def _wikidata_get(params: dict) -> dict:
    """Call the Wikidata API and return parsed JSON. Raises on HTTP errors (e.g. 429)."""
    response = requests.get(
        WIKIDATA_API,
        params={**params, "format": "json"},
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def _language_titles(sitelinks: dict) -> dict[str, str]:
    """{"enwiki": {"title": "Stray Kids", ...}, ...} -> {"en": "Stray Kids", ...}"""
    titles = {}
    for key, link in sitelinks.items():
        if not key.endswith("wiki") or key in NON_LANGUAGE_WIKIS:
            continue
        lang = key[:-4].replace("_", "-")
        titles[lang] = link["title"]
    return titles


def resolve_star(name: str) -> dict:
    """Find a celebrity or group on Wikidata.

    `name` may be a stage name ("Stray Kids") or a Wikidata ID ("Q46134670").
    Returns {"status": "found" | "ambiguous" | "not_found", ...}.
    """
    name = name.strip()

    # Step 1: name -> Wikidata ID
    if re.fullmatch(r"Q\d+", name):
        star_id = name
    else:
        results = _wikidata_get({
            "action": "wbsearchentities",
            "search": name,
            "language": "en",
            "type": "item",
            "limit": 10,
        })["search"]

        stars = [
            result for result in results
            if any(keyword in result.get("description", "").lower()
                   for keyword in STAR_KEYWORDS)
        ]

        if not stars:
            return {"status": "not_found", "query": name}

        matching_stars = [
            star for star in stars
            if star.get("label", "").casefold() == name.casefold()
        ]
        if len(matching_stars) == 1:
            star_id = matching_stars[0]["id"]
        elif len(stars) == 1:
            star_id = stars[0]["id"]
        else:
            # Only the fields the model needs to tell candidates apart.
            candidates = [
                {"id": star["id"], "label": star.get("label", ""),
                 "description": star.get("description", "")}
                for star in stars[:5]
            ]
            return {"status": "ambiguous", "query": name, "candidates": candidates}

    # Step 2: ID -> label, description and sitelinks
    data = _wikidata_get({"action": "wbgetentities", "ids": star_id,
                          "props": "labels|descriptions|sitelinks", "languages": "en|mul"})

    if "error" in data:
        return {"status": "not_found", "query": name}

    entity = data["entities"][star_id]

    labels = entity.get("labels", {})
    return {
        "status": "found",
        "star_id": star_id,
        "label": labels.get("en", labels.get("mul", {})).get("value", name),
        "description": entity.get("descriptions", {}).get("en", {}).get("value", ""),
        "language_titles": _language_titles(entity.get("sitelinks", {})),
    }


def _last_complete_months(count: int) -> list[str]:
    """The last `count` finished calendar months, oldest first: ["2025-10", ..., "2026-09"]."""
    today = date.today()
    year, month = today.year, today.month
    months = []
    for _ in range(count):
        month -= 1
        if month == 0:
            year, month = year - 1, 12
        months.append(f"{year}-{month:02d}")
    return months[::-1]


def get_monthly_pageviews(lang: str, title: str, months: int = 12) -> list[dict]:
    """Human pageviews of one Wikipedia article over the last `months` finished months.

    `title` should come from resolve_star's language_titles, never from user text.
    Returns [{"month": "2025-10", "views": 90303}, ...], oldest first, one entry per
    month. Months with no data count as 0 views (e.g. before the article existed).
    Raises on HTTP errors other than 404 (e.g. 429, 500).
    """
    month_keys = _last_complete_months(months)
    # The API sums only the days inside the range, so it must run from the 1st of
    # the first month to the last day of the last month, or edge months come back partial.
    last_year, last_month = map(int, month_keys[-1].split("-"))
    last_day = calendar.monthrange(last_year, last_month)[1]
    start = month_keys[0].replace("-", "") + "01"
    end = month_keys[-1].replace("-", "") + f"{last_day:02d}"
    # Spaces become underscores, then encode the rest ("/", "?", non-Latin letters).
    article = quote(title.replace(" ", "_"), safe="")
    url = f"{PAGEVIEWS_API}/{lang}.wikipedia/all-access/user/{article}/monthly/{start}/{end}"

    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    if response.status_code == 404:
        # The API's 404 means "no data in this range", not necessarily a bad title.
        items = []
    else:
        response.raise_for_status()
        items = response.json()["items"]

    # "2025100100" -> "2025-10". Keep only the months we asked for.
    views = {f"{item['timestamp'][:4]}-{item['timestamp'][4:6]}": item["views"] for item in items}
    return [{"month": key, "views": views.get(key, 0)} for key in month_keys]
