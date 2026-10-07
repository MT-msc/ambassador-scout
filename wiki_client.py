"""Shared helpers for Wikidata and Wikipedia.

These return plain Python data. tools.py turns it into JSON for the model.
"""

import re

import requests

WIKIDATA_API = "https://www.wikidata.org/w/api.php"

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
        lang = key[:-4].replace("_", "-")  # "enwiki" -> "en", "zh_yuewiki" -> "zh-yue"
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
