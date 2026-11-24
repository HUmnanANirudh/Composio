# Design

The case study is set as an **entry-requirements reference manual**. API access is treated like border entry: each app has an *entry regime*, and the page reads like a manual of entry requirements. The metaphor stays in the frame (masthead, legend, marks); every label is plain language.

## Ground and ink

| Token | Light | Dark | Role |
|---|---|---|---|
| `--paper` | `#f3f4f1` | `#0f1620` | page ground (cool paper, not cream) |
| `--sheet` | `#fbfbf9` | `#141d29` | raised sheets: record card, tables, callouts |
| `--ink` | `#132030` | `#e8ecf0` | text, rules at strength |
| `--ink-2` / `--ink-3` | `#3d4a59` / `#5b6775` | `#b9c3cd` / `#97a2ae` | secondary / tertiary text |
| `--band` | `#132030` | `#070b11` | masthead and closing band; the overscroll ground |
| `--act` | `#1d4ed8` | `#8db0ff` | **the one reserved accent**: actions and links only |

## Regime legend (fixed; used for entry regime and nothing else)

| Regime | Code | Fill (light) | Ink on fill |
|---|---|---|---|
| Self-serve, free | `F` | `#8fd0a4` | `#0f2a19` |
| Self-serve, trial only | `T` | `#c9e39d` | `#23300c` |
| Paid plan required | `P` | `#f0cf6e` | `#352806` |
| Application / approval | `A` | `#f3a774` | `#3a1906` |
| Partnership or sales | `S` | `#c6a3e0` | `#26103a` |
| No hosted API | `X` | `#4b515a` | `#f2f3f4` |

Regime is never carried by color alone. Every cell and chip also prints its letter code.

## Marks (states are marks, not just hues)

- **Stamp** (`.stampmark`): a small outlined label in sentence case. "On page" means the verifier re-found the quote on the cited page; "fixed" means a verification loop corrected a field.
- **Struck value** (`.struck`): a wrong answer, struck through in `--miss`.
- **Ring** on a matrix cell: the regime is held with low confidence (< 0.5).
- **Square station** in the route: a human checkpoint. Round stations are automated. The same square marks each item under "Where a human was needed".

## Type

- **Display**: Archivo (variable `wdth` 62–125, `wght` 400–900), set condensed (`wdth` 68–80, weight 750–800) for the masthead, section heads and figures.
- **Reading/UI**: Atkinson Hyperlegible Next (400/500/700), body 16–17px.
- **Data**: Red Hat Mono, used only for codes, slugs, enums and figures in tables. Tabular numerals in all numeric columns.

## Composition

- One content width (1240px max) with a gutter of `clamp(16px, 4vw, 40px)` on both sides.
- First viewport: compact two-column masthead, then findings (figure + sentence rows on one left edge) beside the 100-cell regime matrix and its record card.
- Dense passages (register, miss tables) alternate with reading passages (findings, route).
- The register is one viewport tall (`max-height: min(78vh, 860px)`) and scrolls on its own, with a sticky header row and app column. Rows sit under category subheads (display face, 2px ink rule), so the category isn't repeated on every row.
- Every section head after the first viewport sits under a 2px ink chapter rule, the manual's chapter break. No kicker labels or section numbers.
- Spacing is in three steps: between sections (88–144px), between blocks inside a section (40–76px), and inside a block (12–24px). Space above a heading is always larger than the space below it.
- Rows breathe: findings rows have 17px vertical padding, table cells 11–14px, list rows 11px.
- The page closes on the dark band that matches the masthead: "Run it yourself" and "Where it fell short".
- Below 980px everything stacks, and the route turns vertical. Below 640px the matrix drops app names and keeps codes, with category labels spanning each row.
