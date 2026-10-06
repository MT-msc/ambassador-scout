"""Shared helpers for Wikidata and Wikipedia.

These return plain Python data. tools.py turns it into JSON for the model.
"""

import re

import requests

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
# Wikimedia requires clients to identify themselves with a way to reach the owner.
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
        # TODO 1: skip keys that don't end in "wiki" (e.g. "enwikiquote")
        #         or that are in NON_LANGUAGE_WIKIS

        # TODO 2: turn the key into a language code and store the title:
        #         "enwiki" -> "en",  "zh_yuewiki" -> "zh-yue"
        pass
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
        # TODO 3: search Wikidata with action "wbsearchentities"
        #         (search=name, language="en", type="item", limit=10)
        #         and keep the list under the "search" key
        results = ...

        # TODO 4: keep only results whose description contains a STAR_KEYWORD
        stars = ...

        # TODO 5: decide which star it is
        #   - no stars                                      -> return not_found
        #   - exactly one star whose label == name (any case) -> use its "id"
        #   - only one star in total                        -> use its "id"
        #   - otherwise                                     -> return ambiguous (max 5 candidates)
        ...

    # Step 2: ID -> label, description and sitelinks
    data = _wikidata_get({"action": "wbgetentities", "ids": star_id,
                          "props": "labels|descriptions|sitelinks", "languages": "en|mul"})

    # TODO 6: a bad ID comes back as {"error": ...} -> return not_found

    entity = data["entities"][star_id]

    # TODO 7: build and return the "found" dict.
    #         label: use labels["en"]["value"], or labels["mul"]["value"] if there's no "en"
    ...
