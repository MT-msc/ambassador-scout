# ✦ Ambassador Scout

Every brand wants the next big star *before* they sell out stadiums. Ambassador Scout
is your AI talent scout: it reads live Wikipedia attention data to tell you who's on
the rise, where their fans are, and which up-and-coming K-pop groups are worth
signing now.

I built this because I'm into K-pop and kept wondering why some idols land huge brand
deals right after debut while others don't.

**Live app:** <https://ambassador-scout-git-638434810531.europe-west1.run.app/>
(sign in with a Columbia Google account)

**Who it's for:** marketing teams and small brands choosing a celebrity ambassador.
Tell it about your brand (product, audience, target countries) and it remembers that
context for the rest of the session, then ends each evaluation with a
**SIGN ✅ / WATCH 👀 / PASS ❌** verdict.

## Try these first 🎤

1. **Is Stray Kids rising or fading right now?**
   Calls `buzz_momentum`. You will see a momentum card (label, recent views, 3- and 12-month
   change, sparkline) and a short verdict.
2. **We're launching a skincare line in Brazil and Indonesia. Where are BLACKPINK's fans, and are they a good fit?**
   Calls `fan_geography` (usually with `buzz_momentum`). You will see a bar chart of language
   markets, the Portuguese and Indonesian shares called out, caveats, and a verdict.
3. **Find K-pop groups that debuted since 2023 and are still under the radar.**
   Calls `rising_star_finder`. You will see a ranked leaderboard plus who was left out as
   already mainstream. Then follow up in the same chat with
   *"Which of those has the strongest audience in Latin America?"*: the agent
   remembers the prospects and calls `fan_geography` for each one.

The example buttons on the landing page send these queries with one click. If you open a second private window, the sessions are kept separate.

## The look

Ambassador Scout is styled like a concert night: dark stage, lightstick-neon colors,
and an equalizer that bounces while the scout works. Every star gets a **reach tier**
based on monthly views (🌱 Rookie · 🎤 Main stage · 🏟️ Stadium · 👑 Icon), and the
final verdict lands as a stamp: **SIGN ✅**, **WATCH 👀** or **PASS ❌**. When you
compare stars, their cards line up side by side for a head-to-head.

## Meet the scouting crew

All three tools are original to this project. They use free, keyless public data
(Wikidata and Wikimedia Pageviews) and add their own analysis on top.

| Tool | What it answers | How it works |
|---|---|---|
| `buzz_momentum(star_name, months=12)` | Is this star **Rising, Peaking, Steady, Fading, New** or **Low visibility**? | Monthly English Wikipedia pageviews for the last N finished months. Compares the last 3 months with the 3 before (short term) and with the start of the window (long term), finds the peak, and flags spike months (≥ 2× the median) so one viral moment isn't mistaken for lasting growth. |
| `fan_geography(star_name, months=3)` | **Where in the world** is the attention coming from? | Reads the star's article title in every language from Wikidata, fetches pageviews for 24 language editions in parallel, and maps each language to its likely markets (e.g. Portuguese → Brazil and Portugal). Returns share of views per market plus caveats. |
| `rising_star_finder(category, debuted_after=2022, max_monthly_views=100000, limit=5)` | Which **K-pop groups or idols** are growing fastest but **not mainstream yet**? | Finds candidates with a Wikidata SPARQL query plus a hand-checked list (Wikidata misses some groups), fetches 12 months of pageviews for each, drops the months before a real article existed, and ranks by growth. Excludes stars above the views cap or currently fading, and reports who was left out and why. |

Names are resolved through Wikidata (`resolve_star`), which also accepts a Wikidata ID.
That powers the disambiguation flow: if a name matches several people, the tool
returns the candidates, the agent asks which one you mean, and the next call uses
the chosen ID.

### Error handling

Tools never crash the conversation. Every failure comes back as JSON that tells the
model what to do next, for example:

- not found → *"Ask the user for the full stage name (e.g. 'Kim Seok-jin' instead of 'Jin')."*
- ambiguous → the candidates, with *"Ask the user which one they mean, then call again with that candidate's id."*
- bad arguments → the valid range or values (e.g. the allowed `category` enum)
- Wikipedia or Wikidata down or rate-limited → *"Try again in a minute."*

Gemini rate limits (HTTP 429) are retried automatically, and the user sees a plain
message if the service is still busy.

## How it works

```text
index.html ── POST /chat {message, session_id} ──► app.py
                                                    │ session history (in memory)
                                                    ▼
                                         run_agent(): Gemini on Vertex AI
                                         ⇄ tools.py (the 3 tools + their JSON schemas)
                                                    ⇣
                                         wiki_client.py: Wikidata + Pageviews
```

- `app.py`: FastAPI server, system prompt, session store and the tool-calling loop.
  `/chat` returns `response`, `session_id` and `tool_calls` (each with `name`,
  `args` and `result`).
- `tools.py`: the three tools, their analysis and the descriptions Gemini sees.
- `wiki_client.py`: name resolution, monthly pageviews (single and bulk) and the
  candidate search.
- `index.html`: the frontend. Each tool call is drawn as a card (stat tile and
  sparkline, market bar chart, prospect leaderboard) with the raw
  `name/args/result` one click away; answers are rendered Markdown with verdict
  stamps.

The system prompt makes the agent ground every number in a tool result, mark outside
knowledge as "General context", pass on data caveats, remember the brand's context,
and stay on topic.

## What the numbers can (and can't) tell you

Wikipedia pageviews show **how many people are curious** about a star, not how many
streams, sales or followers they have, or whether the attention is positive. Think of
it as the buzz, not the box office. The tools say so in every result. Known blind spots:

- English is read worldwide, so a high English share means global reach, not specifically the US.
- Korean interest is under-counted: most Koreans use Namuwiki and Naver.
- Wikipedia is blocked in mainland China.
- Only each article's main title is counted (not redirects), and last month's data
  appears a day or two into the new month.

## Run locally

Requires [uv](https://docs.astral.sh/uv/) and a GCP project with billing and the
Vertex AI API enabled.

```bash
gcloud auth application-default login
uv run app.py        # then open http://localhost:8000
```

## Deployment

Cloud Run with continuous deployment from GitHub (`main`) via Developer Connect,
built with Google Cloud buildpacks and started with
`uvicorn app:app --host 0.0.0.0 --port $PORT`. Access is restricted to `columbia.edu`
accounts with Identity-Aware Proxy. Sessions live in memory, so the service runs with
a maximum of one instance.

## Encore 🎶 (future work)

- **YouTube Data API** (channel IDs are already in Wikidata): subscribers and
  engagement rate, a fan-activity signal Wikipedia can't give.
- **Shared session storage** (e.g. Firestore) so the service can scale past one instance.
- More `rising_star_finder` categories beyond K-pop.
