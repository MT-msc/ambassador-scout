"""Shared helpers for Wikidata and Wikipedia.

These return plain Python data. tools.py turns it into JSON for the model.
"""

import calendar
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from urllib.parse import quote

import requests

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
PAGEVIEWS_API = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
SPARQL_API = "https://query.wikidata.org/sparql"
SPARQL_TIMEOUT = 20
# Parallel requests for bulk pageviews. Wikimedia allows far more; this stays polite.
MAX_WORKERS = 8

USER_AGENT = "AmbassadorScout/0.1 (https://github.com/MT-msc/ambassador-scout)"
TIMEOUT = 10

# A search result is probably a star if its description mentions one of these.
STAR_KEYWORDS = ("singer", "rapper", "actor", "actress", "band", "group", "musician",
                 "idol", "songwriter", "entertainer", "model", "dancer", "duo", "personality")
# ...unless it describes a work by a star ("1994 single by Canadian country band"), or an
# adult performer, who is out of scope for brand deals. Whole words only, so
# "singer-songwriter" doesn't count as "song".
NOT_A_STAR = re.compile(r"\b(song|single|album|mixtape|soundtrack|EP|pornographic)\b",
                        re.IGNORECASE)

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
            and not NOT_A_STAR.search(result.get("description", ""))
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


def get_pageviews_bulk(articles: list[tuple[str, str]], months: int) -> dict:
    """get_monthly_pageviews for many (lang, title) pairs at once, in parallel.

    Returns {(lang, title): [...monthly rows...] or None if that request failed}.
    One failure (e.g. a 429) doesn't sink the rest.
    """
    def fetch(article):
        try:
            return get_monthly_pageviews(article[0], article[1], months)
        except requests.RequestException:
            return None

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        return dict(zip(articles, pool.map(fetch, articles)))


# --- Candidate stars for rising_star_finder ---

# Wikidata SPARQL per category. {year} is the earliest debut year to include.
# Kept simple on purpose: broader queries (unions, subclass paths) time out.
CATEGORY_QUERIES = {
    # Non-human items with genre K-pop (Q213665) and an inception date (P571).
    "kpop_group": """
        SELECT ?item ?start ?title WHERE {{
          ?item wdt:P136 wd:Q213665 ; wdt:P571 ?start .
          FILTER NOT EXISTS {{ ?item wdt:P31 wd:Q5 }}
          FILTER(YEAR(?start) >= {year})
          ?article schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> ;
                   schema:name ?title .
        }}""",
    # Humans (Q5) with genre K-pop and a "work period start" (P2031), incl. group members.
    "kpop_idol": """
        SELECT ?item ?start ?title WHERE {{
          ?item wdt:P31 wd:Q5 ; wdt:P136 wd:Q213665 ; wdt:P2031 ?start .
          FILTER(YEAR(?start) >= {year})
          ?article schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> ;
                   schema:name ?title .
        }}""",
}

# Hand-checked additions: well-known stars Wikidata's data misses (no genre or debut date
# recorded), and a fallback when the SPARQL service is down. (star_id, en title, debut year)
SEED_CANDIDATES = {
    "kpop_group": [
        ("Q115967938", "Babymonster", 2023), ("Q123480242", "Katseye", 2024),
        ("Q118178306", "BoyNextDoor", 2023), ("Q117808633", "Zerobaseone", 2023),
        ("Q130084175", "Meovv", 2024), ("Q134986635", "AllDay Project", 2025),
        ("Q124412677", "QWER", 2023), ("Q132526762", "KiiiKiii", 2025),
        ("Q135466598", "Idid (group)", 2025),
    ],
    "kpop_idol": [],
}


def find_candidates(category: str, debuted_after: int) -> tuple[list[dict], bool]:
    """Stars in a category who debuted in or after `debuted_after`.

    Returns ([{"star_id", "title", "debut_year"}, ...], live), where live is False
    when the Wikidata query failed and only the hand-checked seeds were used.
    """
    candidates = {}
    live = True
    try:
        response = requests.get(
            SPARQL_API,
            params={"query": CATEGORY_QUERIES[category].format(year=debuted_after), "format": "json"},
            headers={"User-Agent": USER_AGENT},
            timeout=SPARQL_TIMEOUT,
        )
        response.raise_for_status()
        for row in response.json()["results"]["bindings"]:
            title = row["title"]["value"]
            year = int(row["start"]["value"][:4])
            # An item can have several dates; keep the earliest.
            if title not in candidates or year < candidates[title]["debut_year"]:
                candidates[title] = {"star_id": row["item"]["value"].rsplit("/", 1)[1],
                                     "title": title, "debut_year": year}
    except (requests.RequestException, ValueError, KeyError):
        live = False

    for star_id, title, year in SEED_CANDIDATES[category]:
        if year >= debuted_after and title not in candidates:
            candidates[title] = {"star_id": star_id, "title": title, "debut_year": year}
    return list(candidates.values()), live
