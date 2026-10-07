import json
import os
import uuid
from datetime import date
from pathlib import Path

import litellm
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from tools import TOOLS, run_tool

# --- Config ---

SYSTEM_PROMPT = """\
You are Ambassador Scout, an AI talent scout that helps brands choose celebrity and \
K-pop ambassadors. Today is {today}.

## Tools
- buzz_momentum: is a star's attention Rising, Peaking, Steady or Fading?
- fan_geography: which language markets their attention comes from.
- rising_star_finder: up-and-coming K-pop groups or idols before they go mainstream.
All three measure Wikipedia pageviews: public attention, not sales, streams or fan counts.

## Rules
1. Ground every number and label in a tool result from this conversation. Never invent \
or estimate figures. If you add general knowledge (e.g. a star's agency), keep it short \
and mark it as "General context:" so it is clearly not from the data.
2. When comparing stars, call the tools for each one. Comparing on the same measures is \
the point.
3. If a tool returns an error, follow its instructions. If it returns candidates, ask the \
user which one they mean, then call the tool again with that candidate's id. Never guess.
4. Pass on caveats that matter for the question. In particular: Korean interest is \
under-counted, English means global reach rather than the US, and a recent spike_months \
or recent_spike entry means the rise may be one event rather than lasting growth.
5. Remember the brand's context from earlier in the conversation (product, audience, \
target markets, budget, stars already discussed) and apply it without asking again.
6. Report what the data shows. Do not speculate about scandals, relationships or \
private lives.
7. If asked something unrelated to celebrities and brand partnerships, say briefly what \
you can help with instead.

## Answer style
- Lead with the answer, then the supporting numbers. Keep it scannable: short bullets, \
or a table when comparing several stars. Round large numbers (68k, 1.2M).
- When the user is weighing a star for a brand deal, end with a verdict per star: \
SIGN ✅, WATCH 👀 or PASS ❌, with a one-line reason.
"""
# Comparing several stars can take a few rounds: find, then check each one.
MAX_TOOL_ROUNDS = 8

# --- The Harness ---


def run_agent(messages: list[dict]) -> tuple[str, list[dict]]:
    """Complete until the model answers without asking for a tool.

    Returns the final text and a record of every tool call made along the way.
    """
    tool_calls = []

    for _ in range(MAX_TOOL_ROUNDS):
        reply = litellm.completion(
            model="vertex_ai/gemini-3.5-flash-lite",
            vertex_location="global",
            messages=messages,
            tools=TOOLS,
            # Vertex AI briefly returns 429 under bursts; LiteLLM retries with backoff.
            num_retries=3,
        ).choices[0].message

        # Append assistant's reply (text, tool calls, or both) to the context.
        # model_dump() keeps it a plain dict: the raw object carries provider-specific
        # fields that trip Pydantic when LiteLLM re-serializes it next round.
        messages += [reply.model_dump()]

        if not reply.tool_calls:
            return reply.content, tool_calls

        # The harness, not the model, runs each tool and appends the result
        for call in reply.tool_calls:
            args = json.loads(call.function.arguments)
            result = run_tool(call.function.name, args)
            tool_calls += [{"name": call.function.name, "args": args, "result": result}]

            messages += [{"role": "tool", "tool_call_id": call.id, "content": result}]

    return "Sorry, I hit my tool-call limit before finishing.", tool_calls


# --- Session Store ---

# session_id -> list of messages. In-memory, single process.
sessions: dict[str, list] = {}

# --- FastAPI App ---

app = FastAPI()


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    response: str
    session_id: str
    tool_calls: list[dict]


@app.get("/")
def index():
    # no-cache: browsers re-check for a new version, so each deploy shows up right away.
    return FileResponse(Path(__file__).parent / "index.html", headers={"Cache-Control": "no-cache"})


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    # Get or create the session
    session_id = request.session_id or str(uuid.uuid4())
    if session_id not in sessions:
        # Date filled in per session, so a server running for days keeps it current.
        prompt = SYSTEM_PROMPT.format(today=date.today().strftime("%B %d, %Y"))
        sessions[session_id] = [{"role": "system", "content": prompt}]

    # Append user's message to the context
    sessions[session_id] += [{"role": "user", "content": request.message}]

    try:
        response, tool_calls = run_agent(sessions[session_id])
    except litellm.RateLimitError:
        # Still busy after the retries: tell the user plainly instead of dumping JSON.
        response, tool_calls = ("The AI service is busy right now. Please wait a few "
                                "seconds and send your message again."), []
    except Exception as e:
        # Auth, billing, a model that is not running: show it in the chat, not as a 500.
        response, tool_calls = f"Model call failed: {type(e).__name__}: {str(e)[:300]}", []

    return ChatResponse(response=response, session_id=session_id, tool_calls=tool_calls)


@app.post("/clear")
def clear(session_id: str | None = None):
    sessions.pop(session_id, None)
    return {"status": "ok"}


if __name__ == "__main__":
    # Cloud Run sets PORT and needs the server reachable from outside the container.
    # Locally there is no PORT, so stay on 127.0.0.1:8000 (not exposed to the network).
    if "PORT" in os.environ:
        uvicorn.run(app, host="0.0.0.0", port=int(os.environ["PORT"]))
    else:
        uvicorn.run(app, host="127.0.0.1", port=8000)
