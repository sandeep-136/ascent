# Ascent — a personal AI career agent

Ascent is a career agent you run for yourself. Every morning it builds a
concrete plan from what is actually on your plate — upcoming interviews,
application deadlines, follow-ups that are due, and the gaps between your
profile and the jobs you want. It tracks applications, scores job
descriptions against your profile transparently, and runs mock interviews
with questions drawn partly from your own projects.

Everything the agent does is visible: each plan item, fit analysis and LLM
call records an inspectable trace of how it was decided.

A project by Sandeep Sagar

## What it does

- **Daily plan** — a deterministic planner turns interviews, deadlines,
  overdue follow-ups, JD-fit prep and practice into an ordered task list,
  each with the reason it was scheduled. Re-running the plan never
  duplicates tasks you already have.
- **Application tracker** — companies, roles, statuses, deadlines,
  follow-up dates and the pasted job description, all in one place.
- **JD fit analysis** — extracts the skills a job description asks for with
  a transparent phrase lexicon (every extracted skill can be pointed to in
  the text), matches them against your profile, lists the gaps, and routes
  each gap to interview-question categories to practice. An embedding
  cosine between the JD and your profile summary is shown as a labeled
  signal, not a verdict.
- **Mock interviews** — a curated question bank (Python, SQL, DSA, web, ML,
  CS fundamentals, behavioral) plus questions generated from the projects
  in your profile. Answers are recorded with a self-rating so you can see
  practice volume and coverage over time.
- **Optional LLM features** — an AI prep brief for a fit analysis and AI
  feedback on a practice answer. These are layered on top of the
  deterministic core and are strictly opt-in per call (see Privacy).

## Why the architecture looks like this

The agent's spine is deterministic and testable. Rules, a curated lexicon
and small ONNX embeddings decide what goes on your plan and how a JD maps
to your profile, because those decisions must be explainable in an
interview and reproducible in tests. The LLM is an optional narrator and
coach on top — never the part that decides.

## Access and deployment model

Ascent is single-owner software with two deployment modes:

- **Private instance (default)** — first run asks you to create an owner
  passphrase (salted PBKDF2 hash, standard library only). Every `/api`
  route requires the signed, expiring HttpOnly session cookie it issues;
  there is no unauthenticated read or write. Run it on your own machine
  (`python run.py` or Docker) and your data never leaves your hands.
- **Public demo (`ASCENT_DEMO=1`)** — for showcasing. Reads are open, but
  every mutation is rejected (`demo_read_only`), the LLM endpoints are
  refused, and the LLM client is force-disabled so a configured key cannot
  be spent by public traffic. Seed it with `samples/seed_demo.py`
  (fictional persona) — there is no way to enter real personal data.

Free-hosting note: free web-service tiers (e.g. Render) have ephemeral
filesystems, so SQLite there is not durable — fine for the stateless-ish
demo, not for your private data. Your private instance belongs on your own
machine; durable free hosting for it is a separate decision.

Set `ASCENT_SECURE_COOKIE=1` when serving over HTTPS.

## Privacy: preview before send

Free LLM tiers may review submitted content, so Ascent treats every
outbound call as a privacy decision:

1. **Minimal construction** — payloads are built server-side from only the
   fields a feature needs (e.g. a skill list, or one question plus the
   answer just typed). Stored profile data is never attached silently.
2. **Preview before send** — the UI shows the exact system instruction and
   message that would be sent. The send endpoint re-reads the stored
   payload by id (the client cannot alter it after preview) and verifies
   its hash before calling a provider.
3. **Policy guard** — `app/llm/policy.py` inspects every payload for
   configured personal terms and generic patterns (emails, phone-like
   numbers, long identifiers). Modes: `block` (default — refuse),
   `redact` (mask, then send), `allow` (fictional test data only). The
   guard is a backstop; the construction rules and the preview are the
   real boundary.
4. **Budget caps** — per-IP and global daily call limits; a free-tier
   exhaustion stops the feature, it never falls over into a paid plan.
5. **Trace** — every send is recorded in `agent_run` with the guard's
   decision.

No LLM provider key is required to run the app; without one the
deterministic features work fully and the AI buttons report that LLM is
not configured.

## Tech stack

Python · FastAPI · SQLite · scikit-learn · ONNX Runtime (all-MiniLM-L6-v2,
384-dim, quantized, baked into the image) · vanilla HTML/CSS/JS (mobile-first)

LLM provider is pluggable: Gnani Vachana (OpenAI-compatible) or any
OpenAI-compatible endpoint, via environment variables.

## Running locally

```bash
pip install -r requirements.txt
python run.py          # http://localhost:8000
```

Demo persona (fictional — for screenshots and demos only):

```bash
ASCENT_DEMO=1 python samples/seed_demo.py
```

Configuration (all optional):

| Env var | Purpose |
| --- | --- |
| `ASCENT_DB` | SQLite path (default `./ascent.db`) |
| `LLM_PROVIDER` | `gnani` or `openai`-compatible |
| `LLM_API_KEY` | Provider key (server-side only) |
| `LLM_BASE_URL` / `LLM_MODEL` | Endpoint and model |
| `LLM_PRIVACY_MODE` | `block` (default) / `redact` / `allow` |
| `LLM_PII_TERMS` | JSON list of personal terms the guard must catch |
| `LLM_PER_IP_DAILY_LIMIT` / `LLM_GLOBAL_DAILY_LIMIT` | Call budgets |

## Docker / deploy

```bash
docker build -t ascent .
docker run -p 8000:8000 -v ascent-data:/data ascent
```

The image is self-contained (embedding model included); no network calls
are needed at runtime unless an LLM provider is configured. Deploys as-is
on a free web-service tier such as Render.

## Tests and evaluations

```bash
python -m pytest tests/ -q     # 26 tests: planner, fit, interview, privacy guard, API, LLM flow
python evals/run_evals.py      # extraction / planner-ordering / fit fixtures
```

The eval harness measures JD skill extraction (precision/recall against
hand-labeled job descriptions), planner ordering, and fit matched/missing
separation. Fixture limits are stated in the harness itself: 5
hand-labeled cases written by the same author as the matcher, with
annotations completed during development — they are exploratory regression
guards, not an independent validation set, and support no performance
claim beyond those cases. LLM output quality is not measured here and
would need a configured provider plus its own labeled set.

## Project layout

```
app/
  main.py            FastAPI routes
  db.py              SQLite schema + helpers
  agent/
    planner.py       daily-plan rules
    fit.py           JD fit engine
    interview.py     question bank, sessions, stats
    data/            curated question bank
  nlp/
    skills.py        transparent skill lexicon + phrase matcher
    extract.py       JD structure extraction
    embed.py         ONNX MiniLM embeddings
  llm/
    client.py        provider clients + budget caps
    policy.py        privacy guard (block/redact/allow)
    features.py      preview-before-send payload flow
  static/index.html  the whole UI
evals/               fixture-based evaluation harness
samples/seed_demo.py fictional demo persona
tests/               26 tests
```
