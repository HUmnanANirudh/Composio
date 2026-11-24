# Entry requirements for 100 APIs

A research agent that profiles 100 SaaS apps for **AI-agent-toolkit buildability**. For each app it records:

- how you authenticate,
- whether a developer can get credentials alone,
- what the API covers,
- whether an official MCP server exists,
- what blocks a toolkit today.

Every field cites a vendor page and a verbatim quote, and accuracy is measured against a hand-checked random sample.

**Results (all 100 apps):** 922 cited quotes, all re-found on their pages. On the 20-app hand-checked sample, 112 of 115 scored fields match (97%) after miss review; before review it was 109/115 (95%). Three of the six original misses were answer-key errors or ambiguous and were corrected with sources (see `data/audit.json` and the key's `revisions`). 75% of apps are self-serve, 29 are blocked by a paid plan, 87 vendors ship an official MCP server.

> **How this dataset was extracted.** The pipeline gathered every source (crawler + Exa through Composio) and ran every verification check. The reading step, turning a source pack into cited fields, was done by Claude (`claude-opus-5-5`) in a Claude Code session, because LLM API free tiers (Groq's daily token caps, Gemini's zero-quota Pro models and overloaded Flash models) could not finish an unattended run. The automated path (`research run`, Gemini) is still built, tested, and wired to GitHub Actions. **Caveat:** the same model wrote the answer key, so the sample is not an independent grader; 97% is an upper bound. The key was drawn with a fixed seed before any extraction, and every miss, verdict and key correction is published.

- **Case study (live):** https://humnananirudh.github.io/Composio/
- **Run it from GitHub:** Actions → *Run research agent* → *Run workflow* → type an app name
- **For agents:** [`site/llms.txt`](site/llms.txt), [`site/data.json`](site/data.json), [`site/results.csv`](site/results.csv)

## How it works

```
apps.csv ──► 1 Composio catalog ──► 2 discover ──► 3 extract (pass 1) ──► 4 verify ──► final ──► score / patterns / site
             (public toolkit list)   crawl + Exa     Claude in-session,      grounding
                                    via Composio     or Gemini (automated)   rules + Composio x-check
                                                     cited JSON              targeted re-research
```

1. **Composio catalog** (`catalog.py`). Matches each app to Composio's public catalog of 1,841 toolkits at `docs.composio.dev/toolkits.md`; no key is needed. It records the existing toolkit, its tool count and its auth schemes, plus any `*_MCP` proxy, which is evidence the vendor runs its own MCP server. Twenty ambiguous name matches are pinned by hand in `data/composio_overrides.json`.
2. **Discover** (`research.gather`, `web.py`). A crawler starts at the official docs host. It reads `llms.txt` and follows links about auth, pricing, MCP and the API reference. It then supplements those pages with **Exa search run through a Composio Platform session** (`composio.create(user_id).execute("EXA_SEARCH")`). The search slug and its arguments come from the live tool schema. Pages are cached on disk, so the verifier re-checks exactly the text the extractor saw.
3. **Extract, pass 1.** In-session (`manual.py`): `research pack` writes each app's sources to `data/packs/NNN.md` (8 pages, ~2,400 chars each, official pages first); the extractor writes compact cited answers to `data/manual/pass1-*.json`. Automated (`research.extract`): the same rubric through Gemini. A fixed rubric of enums. Each field returns `{value, evidence: [{source, verbatim quote}], confidence}`. The extractor never sees the Composio catalog, so the catalog stays an independent cross-check.
4. **Verify** (`verify.py`):
   - **Grounding (deterministic).** Fuzzy-finds each quote on its cached page and flags fields with no grounded evidence.
   - **Rules (deterministic).** Checks that access and buildability are consistent with each other, and compares auth against Composio's catalog. It also flags "no official MCP" when Composio proxies the vendor's own MCP server.
   - **Re-research.** Only flagged scored fields get new targeted searches (`data/packs/NNN.fix.md`, sources R1…) and are answered again (`data/manual/fix-*.json`); then grounding and rules run again. In this dataset 14 apps were re-researched and 1 field changed (Datadog access: free → trial). Every flag and change is logged in `record.verification`.
5. **Score** (`evaluate.py`). Compares pass 1 and final against `data/ground_truth.json`: 20 apps, 2 per category, drawn with a fixed seed **before any agent run**, each truth value with a URL and a quote.
6. **Patterns** (`patterns.py`). Plain counts over the final records, with no LLM involved, so every headline is traceable.

### Where a human was needed

- **Catalog matching.** I reviewed 20 ambiguous app-to-toolkit matches by hand (`data/composio_overrides.json`).
- **Search access.** Composio's built-in `COMPOSIO_SEARCH` toolkit is retired: it returns `410 ToolRouterV2_ToolkitRetired`. A human created an Exa key, and `research connect-search` registers it on the agent's Composio user. Without it, the agent prints a Composio Connect Link and falls back to crawl-only.
- **The answer key.** Twenty apps were checked by hand against the official docs. Contested fields are left unscored instead of guessed.
- **The reading step.** After Groq and Gemini free tiers stalled the unattended run, the user chose in-session extraction by Claude from the same source packs.
- **Reviewing misses.** Every miss has a verdict in `data/audit.json` (120 cells: 20 apps × 6 fields, each with truth, pass 1, final, sources and review). 3 agent wrong (Gorgias access, Twilio access, Threads MCP), 2 key wrong (SendGrid auth, Harvest MCP), 1 ambiguous (SendGrid buildability). Earlier, Clay's key was also corrected after the crawler found `developers.clay.com`. Corrections are logged in the key's `revisions`.

## Run it

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env            # GEMINI_API_KEY, COMPOSIO_API_KEY, EXA_API_KEY
uv run research connect-search  # one-time: connect Exa to the agent's Composio user
uv run research list            # the 100-app research set
uv run research catalog --apps 1-10
# in-session path (how this dataset was made)
uv run research pack --apps Attio            # sources -> data/packs/004.md
#   write cited answers to data/manual/pass1-NN.json
uv run research ingest --apps Attio          # pass 1 + grounding/rules; fix packs for flags
#   answer flagged fields in data/manual/fix-NN.json
uv run research finalize                     # apply fixes, re-check, write final records
uv run research audit                        # data/audit.json, field-level
uv run research review 19 access agent_wrong "note" --by "name"
# automated path (Gemini)
uv run research run --apps Attio,Stripe      # pass 1 + verification, resumable
uv run research run                          # all 100 (resumable; re-run to continue)
uv run research show --apps Attio --md       # cited record as a markdown table
uv run research evaluate                     # pass 1 vs final vs answer key
uv run research patterns
uv run research build-site                   # site/data.json, results.csv, llms.txt
uv run pytest
```

`--apps` accepts names (`Slack,Stripe`), ids (`3,7`) or ranges (`1-10`). `--force` ignores caches.

**Automated path.** `research run` sends the same rubric to the Gemini API (JSON mode). Each role has a newest-first fallback chain of models (`config.py`, overridable in `.env`). When one model runs out of quota or is overloaded, the next takes over, and a run that runs out entirely resumes where it stopped. Token use per call is logged to `data/results/usage.jsonl`.

**GitHub Actions.** Set the `GEMINI_API_KEY` and `COMPOSIO_API_KEY` repo secrets (and connect Exa once with `connect-search`). *Run research agent* (manual) researches any app on demand: the job summary prints the cited record, and the JSON is uploaded as an artifact. Pushes to `main` redeploy the case study to Pages.

**Checkpoints.** Every record and cache file is written to a temp file and then renamed into place, so a run killed mid-app loses only that app's work. Network errors, 429s and 5xx responses are not cached, so the next run fetches those pages again.

## Layout

```
data/apps.csv                 research set (100 apps, 10 categories)
data/ground_truth.json        hand-checked answer key (20 apps) + revision log
data/composio_overrides.json  human-reviewed catalog matches
data/results/{pass1,final}/   one JSON record per app
data/packs/                   source packs (NNN.md) and re-research packs (NNN.fix.md)
data/manual/                  in-session cited answers (pass1-*.json, fix-*.json)
data/audit.json               field-level audit of the 20-app sample, with review verdicts
src/app_research_agent/       catalog, web, research, verify, llm, evaluate, patterns, site_build, cli
site/                         the case study (static; GitHub Pages)
.agents/skills/composio       Composio agent skill (npx skills add ComposioHQ/composio)
```

## Known limits

- **Not an independent grader.** Claude wrote the answer key and did the extraction. Read 97% as an upper bound; the audit file makes every judgement re-checkable.
- **Model availability.** On a free-tier Gemini key, the Pro-class models (`gemini-4-carbon`, `gemini-4-neon`) are capped at 0 requests, and the Flash models were overloaded for hours. That is why this dataset was extracted in-session.
- **One unidentified app.** Sources for "Paygent Connect" describe several unrelated products named Paygent, none NMI-powered; its record is low-confidence and flagged.
- **JavaScript-rendered docs.** Portals like GoHighLevel's Stoplight docs return almost no text to a plain fetch.
- **Access is the hardest field.** Pricing pages rarely say whether the API is included on the free plan.
- **Coverage of the key.** The answer key covers 20 of the 100 apps. The other 80 rely on the verification loops and calibrated confidence alone.
