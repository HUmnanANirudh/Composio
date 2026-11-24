# Entry requirements for 100 APIs

A research agent that profiles 100 SaaS apps for AI-agent toolkit buildability: how you authenticate, whether a developer can get credentials alone, what the API covers, whether an official MCP server exists, and what blocks a toolkit today. Every field cites a vendor page and a verbatim quote.

- **Case study:** https://humnananirudh.github.io/Composio/
- **Run it from GitHub:** Actions → *Run research agent* → *Run workflow* → type an app name
- **For agents:** [`llms.txt`](site/llms.txt), [`data.json`](site/data.json), [`results.csv`](site/results.csv)
- **Single-file copy:** [`submission.html`](submission.html) — the case study with its data embedded; opens offline, data-file links go to the live site. Rebuilt by `research build-site`.

## Results

- 75% of apps are self-serve; 29 are blocked by a paid plan; 87 vendors ship an official MCP server.
- 922 cited quotes, all re-found on their pages.
- On a 20-app hand-checked sample, 112 of 115 scored fields match (97%). Before misses were reviewed it was 109/115 (95%): three of the six misses turned out to be answer-key errors or ambiguous, and the key was corrected with sources.

**Caveat:** Claude (`claude-opus-5-5`) did the extraction and also wrote the answer key, so the sample is not an independent grader and 97% is an upper bound. The key was drawn with a fixed seed before any extraction, and every miss, verdict and correction is published in `data/audit.json`.

## How it works

```
apps.csv ─► 1 catalog ─► 2 discover ─► 3 extract ─► 4 verify ─► 5 score ─► 6 patterns / site
```

1. **Catalog** (`catalog.py`). Matches each app to Composio's public catalog of 1,841 toolkits: the existing toolkit, its tool count, its auth schemes, and any `*_MCP` proxy (evidence the vendor runs its own MCP server). No key needed.
2. **Discover** (`research.gather`, `web.py`). Crawls the official docs host (its `llms.txt`, then auth, pricing, MCP and API-reference links) and adds **Exa search through a Composio Platform session** (`EXA_SEARCH`). Pages are cached, so the verifier checks the same text the extractor read.
3. **Extract** (pass 1). A fixed rubric of enums; each field returns a value, evidence (source + verbatim quote) and a confidence. The extractor never sees the Composio catalog, so it stays an independent cross-check.
   - *In-session* (`manual.py`, how this dataset was made): `research pack` writes each app's sources to `data/packs/NNN.md`; Claude writes cited answers to `data/manual/`.
   - *Automated* (`research run`): the same rubric through the Gemini API, with a per-role model fallback chain (`config.py`).
4. **Verify** (`verify.py`).
   - **Grounding:** fuzzy-finds each quote on its cached page.
   - **Rules:** access and buildability must agree; auth and MCP are cross-checked against Composio's catalog.
   - **Re-research:** flagged fields get new targeted searches and are answered again, then re-checked. Here 14 apps were re-researched and 1 field changed (Datadog access: free → trial).
5. **Score** (`evaluate.py`). Pass 1 and final against `data/ground_truth.json`: 20 apps, 2 per category, each truth value with a URL and a quote.
6. **Patterns** (`patterns.py`). Plain counts over the final records, no LLM.

### Where a human was needed

- **Catalog matching.** 20 ambiguous app-to-toolkit matches pinned by hand (`data/composio_overrides.json`).
- **Search key.** Composio's built-in search toolkit is retired (`410`), so a human created an Exa key; `research connect-search` registers it on the agent's Composio user.
- **Answer key.** 20 apps checked by hand against official docs; contested fields are left unscored.
- **Reading step.** LLM free tiers could not finish an unattended run (Groq's daily token caps; on a free Gemini key the Pro models are capped at 0 and the Flash models were overloaded for hours), so the reading was done by Claude in-session from the same source packs.
- **Reviewing misses.** Every miss has a verdict in `data/audit.json`: 3 agent wrong (Gorgias access, Twilio access, Threads MCP), 2 key wrong (SendGrid auth, Harvest MCP), 1 ambiguous (SendGrid buildability). Key corrections are logged in the key's `revisions`.

## Run it

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env                    # COMPOSIO_API_KEY, EXA_API_KEY, GEMINI_API_KEY
uv run research connect-search          # one-time: connect Exa to the agent's Composio user
uv run research run --apps Attio,Stripe # automated: pass 1 + verification (resumable)
uv run research show --apps Attio --md  # cited record as a markdown table
uv run research evaluate                # score against the answer key
uv run research build-site              # site/data.json, results.csv, llms.txt
uv run pytest
```

In-session path: `research pack` → write answers to `data/manual/pass1-NN.json` → `research ingest` → answer flagged fields in `data/manual/fix-NN.json` → `research finalize`. `--apps` takes names (`Slack,Stripe`), ids (`3,7`) or ranges (`1-10`); `--force` ignores caches.

**GitHub Actions.** Set the `COMPOSIO_API_KEY` and `GEMINI_API_KEY` repo secrets. *Run research agent* researches any app on demand; the job summary prints the cited record and the JSON is uploaded as an artifact. Pushes to `main` redeploy the case study.

Records and cache files are written atomically, so a killed run loses only the app in progress; errors and rate-limited responses are never cached.

## Layout

```
data/apps.csv                 research set (100 apps, 10 categories)
data/ground_truth.json        hand-checked answer key (20 apps) + revision log
data/composio_overrides.json  human-reviewed catalog matches
data/results/{pass1,final}/   one JSON record per app
data/packs/                   source packs (NNN.md) and re-research packs (NNN.fix.md)
data/manual/                  in-session cited answers
data/audit.json               field-level audit of the 20-app sample
src/app_research_agent/       pipeline code
site/                         the case study (static, GitHub Pages)
```

## Known limits

- "Paygent Connect" could not be identified: its sources describe several unrelated products named Paygent.
- JavaScript-rendered docs (e.g. GoHighLevel's Stoplight portal) return almost no text to a plain fetch.
- Access is the hardest field: pricing pages rarely say whether the API is on the free plan.
- The answer key covers 20 of 100 apps; the other 80 rely on the verification loops alone.
