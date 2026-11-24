# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Static HTML/CSS/JS, no build step, served by GitHub Pages from this repo (user's choice). The page reads `site/data.json`, produced by `uv run research build-site`.

## Users

Primary: a reviewer on Composio's integrations team evaluating a take-home case study. They spend about two minutes, with no narrator, and must come away knowing the findings, the patterns, how the agent works, where a human stepped in, and how accurate it is. Secondary: other agents, which consume the same results as JSON and llms.txt.

## Product Purpose

A research agent that profiles 100 SaaS apps for agent-toolkit buildability (auth, self-serve vs gated access, API surface, MCP, blockers), plus the case-study page that presents its findings and proves their accuracy.

## Positioning

Every field is backed by a cited, verbatim quote that a deterministic check re-finds on the source page. Accuracy is measured against a hand-checked random sample, pass 1 vs final, and misses are shown, not hidden.

## Operating Context

The reviewer opens the live link from a submission, likely on a laptop, possibly a phone. Agents fetch `data.json` and `llms.txt`.

## Capabilities and Constraints

- Data is real agent output. Nothing on the page may be invented; unscored or uncertain fields stay marked as such.
- Must show: headline patterns first, a skimmable 100-app matrix, the agent workflow with human-in-the-loop points, the runnable trigger (GitHub Actions) and repo link, and verification with hits and misses.
- Composio appears as the subject. No Composio logo or brand styling.

## Evidence on Hand

`data/results/` (pass1 and final records), `data/ground_truth.json` (20-app answer key with URLs and quotes, plus a revision log), `data/results/eval.json`, `data/results/patterns.json`, `data/results/usage.jsonl` (token accounting). No testimonials or external benchmarks exist; none may be fabricated.

## Product Principles

1. Insight before inventory: patterns lead, rows support.
2. Every claim is traceable to a source URL and quote.
3. Show the misses: honesty about errors is the proof of trustworthiness.
4. Machine-readable by default: the same data serves humans and agents.

## Accessibility & Inclusion

WCAG 2.2 AA text contrast (the page is light only); keyboard-operable filters; tables usable at 390px width.
