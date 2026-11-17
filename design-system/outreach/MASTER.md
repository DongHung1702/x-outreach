# Outreach design system (source of truth)

**Direction (one sentence):** looks like a working X client for one account: white canvas, near-black ink, one blue accent, a left nav rail + one feed column + a slim right rail, rows split by hairlines, pill buttons. Cards only where a human must decide something.

Decided in stage 2 (redesign). The CSV suggestions (rose primary, Space Grotesk/Inter, "modern dark cinema") were discarded.

## Tokens

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#ffffff` | `#000000` → use `#0b0d0e` | page |
| `--ink` | `#0f1419` | `#e7e9ea` | text |
| `--mute` | `#536471` | `#8b98a5` | meta, handles, timestamps |
| `--line` | `#eff3f4` | `#2f3336` | hairline dividers |
| `--hover` | `#f7f9f9` | `#16181c` | row hover |
| `--acc` | `#1d9bf0` | `#1d9bf0` | links, primary actions, focus ring |
| `--good` | `#00ba7c` | `#00ba7c` | sent, replied, verified quote |
| `--bad` | `#f4212e` | `#f4212e` | blocking issues, failures |
| `--warn` | `#b07800` | `#ffd400` | soft warnings |

There is one accent. Status uses colored text and a small dot, not filled pills.

## Type

- System stack: `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif`. This is deliberate (it matches the X client feel and loads no webfont). Don't ship X's proprietary font.
- Sizes: 13 (meta), 15 (body, the default as on X), 17 (draft text, "post" size), 20 (page title, weight 800), 31 (single stat in the right rail, weight 800, tabular-nums).
- Weights: 400 body, 700 names, 800 titles. Sentence case everywhere. No uppercase table headers.
- Numbers: `font-variant-numeric: tabular-nums`.

## Layout

- Desktop ≥ 1100: `[rail 240] [main 600–680] [aside 320]`, centered as a group, max 1260. Columns are separated by vertical hairlines.
- 700–1099: the rail collapses to icons-only (72px). The aside moves below the main column.
- < 700: the rail becomes a fixed bottom tab bar (5 items, 52px high, icon + short label). Main is full width with a 16px gutter. The sticky page header shows the title and the mode badge.
- Lists are rows (padding 12px 16px, border-bottom hairline, hover `--hover`), not cards.

## Components

- **Buttons:** pill (`border-radius: 9999px`), height 36 (34 small), weight 700.
  - Primary: `--ink` background with `--bg` text (X's black pill). Accent blue is reserved for the single "do it" action on a page (Approve, Find people).
  - Secondary: transparent background with a 1px `--line` border.
  - Danger: `--bad` text.
  - Hover darkens 8%, active `scale(.98)`, transitions 150ms. Focus: 2px `--acc` outline with a 2px offset.
- **Avatar:** 40px circle with the person's initial on a tinted background derived from the handle. There are no images, so the tool makes no extra API calls.
- **Draft row = post:**
  - Avatar, then name (700) + @handle · fit 82 · SEA (meta).
  - Draft text at 17px in an editable textarea styled as post text.
  - Evidence shown as a "quote post" box: 1px `--line` border, radius 16, inner padding 12. The cited snippet gets a `--good` left bar.
  - Validator issues as plain red/amber lines.
  - Action row of pills.
- **Status:** `● sent` (green dot + text), `● check on X` (red), `● queued` (mute). No filled badges.
- **Mode badge:** text-only in the rail footer: "Demo data · nothing is sent" or "Live · @handle".
- **Inputs:** radius 4, 1px `--line` border. Focus border `--acc` + ring. Label inside the box above the value (X compose style) or above it.
- **Thread view:** our messages in right-aligned bubbles with `--acc` background and white text; theirs left-aligned with `--hover` background; radius 20 (the corner nearest the speaker is 4).

## Motion

150ms color/background transitions only. Honour `prefers-reduced-motion`. No entrance animations.

## Hard constraints (stage 1)

- Text contrast ≥ 4.5:1 (`--mute` on white = 5.9:1 ✓).
- Touch targets ≥ 40px on mobile (tab bar items 52px).
- Visible focus on every control.
- Tables or wide rows scroll horizontally inside their own wrapper, never the page.
- No page-level horizontal scroll at 375px.

## Anti-patterns banned here

- Equal stat-tile grids.
- Bordered white cards around every block.
- Uppercase micro-labels.
- Emoji or unicode symbols as icons (use inline SVG, 1.75 stroke).
- Gradients.
- Shadows, except the mobile tab bar top hairline.
- Any company branding inside the app.
