# App buildability research agent

An agent that researches 100 SaaS apps and records, for each one, how to authenticate, whether a developer can get credentials self-serve, what the API covers, whether an MCP server exists, and whether an agent toolkit could be built today. Every answer comes with evidence.

> Work in progress. See [ROADMAP](#roadmap).

## Quick start

```bash
uv sync
cp .env.example .env        # add COMPOSIO_API_KEY and GROQ_API_KEY
uv run research list        # show the 100-app research set
```

## Roadmap

- [x] Project scaffold, data model, app loader
- [ ] Composio catalog lookup (existing toolkit + auth schemes)
- [ ] Web layer: search + fetch with on-disk cache
- [ ] Groq extractor (pass 1) with per-field evidence
- [ ] Verification loops: grounding check, rule checks, critic model, targeted re-research
- [ ] Evaluation against a hand-checked sample (pass 1 vs final)
- [ ] Pattern analysis
- [ ] Case-study page on GitHub Pages
- [ ] GitHub Actions trigger to run the agent on any app
