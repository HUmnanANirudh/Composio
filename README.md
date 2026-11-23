# Entry requirements for 100 APIs

A research agent that profiles 100 SaaS apps for **AI-agent-toolkit buildability**. For each app it records:

- how you authenticate,
- whether a developer can get credentials alone,
- what the API covers,
- whether an official MCP server exists,
- what blocks a toolkit today.

Every field cites a vendor page and a verbatim quote, and accuracy is measured against a hand-checked random sample.

- **Case study (live):** https://humnananirudh.github.io/Composio/
- **Run it from GitHub:** Actions → *Run research agent* → *Run workflow* → type an app name
- **For agents:** [`site/llms.txt`](site/llms.txt), [`site/data.json`](site/data.json), [`site/results.csv`](site/results.csv)

## How it works

```
apps.csv ──► 1 Composio catalog ──► 2 discover ──► 3 extract (pass 1) ──► 4 verify ×4 ──► final ──► score / patterns / site
             (public toolkit list)   crawl + Exa     gpt-oss-120b            grounding
                                    via Composio     cited JSON              rules + Composio x-check
                                                                             critic (qwen3.8-27b)
                                                                             targeted re-research
```

1. **Composio catalog** (`catalog.py`). Matches each app to Composio's public catalog of 1,841 toolkits at `docs.composio.dev/toolkits.md`; no key is needed. It records the existing toolkit, its tool count and its auth schemes, plus any `*_MCP` proxy, which is evidence the vendor runs its own MCP server. Twenty ambiguous name matches are pinned by hand in `data/composio_overrides.json`.
2. **Discover** (`research.gather`, `web.py`). A crawler starts at the official docs host. It reads `llms.txt` and follows links about auth, pricing, MCP and the API reference. It then supplements those pages with **Exa search run through a Composio Platform session** (`composio.create(user_id).execute("EXA_SEARCH")`). The search slug and its arguments come from the live tool schema. Pages are cached on disk, so the verifier re-checks exactly the text the extractor saw.
3. **Extract, pass 1** (`research.extract`). A fixed rubric of enums. Each field returns `{value, evidence: [{source, verbatim quote}], confidence}`. The extractor never sees the Composio catalog, so the catalog stays an independent cross-check.
4. **Verify** (`verify.py`):
   - **Grounding (deterministic).** Fuzzy-finds each quote on its cached page and flags fields with no grounded evidence.
   - **Rules (deterministic).** Checks that access and buildability are consistent with each other, and compares auth against Composio's catalog. It also flags "no official MCP" when Composio proxies the vendor's own MCP server.
   - **Critic.** A second model family (`qwen/qwen3.8-27b`) audits each field against the sources.
   - **Re-research.** Only the flagged fields get new targeted searches and are extracted again; then loops 1–3 run again, up to 2 rounds. Every flag and change is logged in `record.verification`.
5. **Score** (`evaluate.py`). Compares pass 1 and final against `data/ground_truth.json`: 20 apps, 2 per category, drawn with a fixed seed **before any agent run**, each truth value with a URL and a quote.
6. **Patterns** (`patterns.py`). Plain counts over the final records, with no LLM involved, so every headline is traceable.

### Where a human was needed

- **Catalog matching.** I reviewed 20 ambiguous app-to-toolkit matches by hand (`data/composio_overrides.json`).
- **Search access.** Composio's built-in `COMPOSIO_SEARCH` toolkit is retired: it returns `410 ToolRouterV2_ToolkitRetired`. A human created an Exa key, and `research connect-search` registers it on the agent's Composio user. Without it, the agent prints a Composio Connect Link and falls back to crawl-only.
- **The answer key.** Twenty apps were checked by hand against the official docs. Contested fields are left unscored instead of guessed.
- **Reviewing misses.** Each miss was read against its source. In one case the key was wrong: the agent's crawler found Clay's Public API docs at `developers.clay.com`, which the hand check had missed. The correction is logged in the key's `revisions`.

## Run it

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env            # GROQ_API_KEY, COMPOSIO_API_KEY, EXA_API_KEY
uv run research connect-search  # one-time: connect Exa to the agent's Composio user
uv run research list            # the 100-app research set
uv run research catalog --apps 1-10
uv run research run --apps Attio,Stripe      # pass 1 + verification, resumable
uv run research run                          # all 100 (pauses cleanly at Groq daily caps; re-run to resume)
uv run research show --apps Attio --md       # cited record as a markdown table
uv run research evaluate                     # pass 1 vs final vs answer key
uv run research patterns
uv run research build-site                   # site/data.json, results.csv, llms.txt
uv run pytest
```

`--apps` accepts names (`Slack,Stripe`), ids (`3,7`) or ranges (`1-10`). `--force` ignores caches; `--no-critic` skips the critic loop.

**Free-tier friendly.** Groq's free tier allows 8K tokens per minute per model. `llm.py` keeps a sliding-window token budget per model, synced from Groq's `x-ratelimit-*` headers. It also runs gpt-oss with low reasoning effort and sizes prompts to fit. When a daily quota runs out, the run pauses and the next invocation resumes from where it stopped. Token use per call is logged to `data/results/usage.jsonl`.

**GitHub Actions.** Set the `GROQ_API_KEY` and `COMPOSIO_API_KEY` repo secrets (and connect Exa once with `connect-search`). Then run *Run research agent* with any app. The job summary prints the cited record, and the JSON is uploaded as an artifact.

## Layout

```
data/apps.csv                 research set (100 apps, 10 categories)
data/ground_truth.json        hand-checked answer key (20 apps) + revision log
data/composio_overrides.json  human-reviewed catalog matches
data/results/{pass1,final}/   one JSON record per app
src/app_research_agent/       catalog, web, research, verify, llm, evaluate, patterns, site_build, cli
site/                         the case study (static; GitHub Pages)
.agents/skills/composio       Composio agent skill (npx skills add ComposioHQ/composio)
```

## Known limits

- **Throughput.** Groq's free tier makes a full run take hours, spread across daily caps.
- **JavaScript-rendered docs.** Portals like GoHighLevel's Stoplight docs return almost no text to a plain fetch.
- **Access is the hardest field.** Pricing pages rarely say whether the API is included on the free plan.
- **Coverage of the key.** The answer key covers 20 of the 100 apps. The other 80 rely on the verification loops and calibrated confidence alone.
