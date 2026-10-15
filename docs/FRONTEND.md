# Frontend, design system and UX principles

> The whole UI is one file: `app/static_app/index.html` (~4,100 lines of HTML, CSS and vanilla JS),
> plus `sw.js` and `manifest.webmanifest`. There is no build step, framework or bundler. The target
> device is an installed PWA on a **Pixel 9**. Written 2026-10-07 at PR 104d.1.
>
> Editing tip: the file is large. On Eran's Windows mounts, use the `safe-windows-edits` skill
> (the Edit tool has truncated long files there before).

---

## 1. How it is served

- Mounted at **`/app`** (`app/main.py`) via `_NoCacheShell`, which sends `Cache-Control: no-cache, must-revalidate` on `*.html`
  and `sw.js`. Before this, installed clients stayed on old JS across several deploys.
- `/dashboard` is a **redirect to `/app`**. The old "Advanced" dashboard was consolidated in Phase G and no longer exists.
- `/` returns service JSON. `/login` is a small server-rendered page (`app/api/routes/google_auth.py`).
- There is no `frontend/` directory: a stale `package-lock.json` from an abandoned React/Vite scaffold was removed (W0-5, 2026-10-12).

## 2. Auth from the browser

- **No token handling in JS.** Google OAuth sets an httpOnly `iw_session` cookie, and same-origin `fetch` sends it.
- A global fetch wrapper sends any **401 → `/login`**. `boot()` probes `/api/v1/auth/me` first.
- The shell is public; the data is not.

## 3. Navigation

Six destinations: top tabs on desktop, a fixed **bottom nav on phones ≤ 720 px**. All go through `show(v)`, which
**reloads that view's data every time it is shown**. There is no router and no URL hash.

| Tab | Contents |
|---|---|
| 🏠 **Today** | Triggered-rules banner · health hero (On plan + On track) · goal line · account-history chart (real NAV, TWR vs SPY) · AI summary · "Where this could end up" · **What to do now** (cards, then "N more", **Heads-up** list, **Holding review**) · biggest risk · Ask InvestWise / digest |
| 💼 **Holdings** | Segmented **Positions** (NAV summary "you put in X, it's worth Y", Cash row first, holdings with chart / AI research / edit / remove, commodities picker, 💵 Liquid cash modal with cash rules + standing orders) / **Rules** (filter, add rule — 11 types, suggested rules, per-ticker groups with distance bars in the rule's own unit, rule history) |
| 🎯 **Plan** | Sub-tabs **Goal · My mix · Explore** (PR 104a). See §4 |
| 📊 **Markets** | Regime hero (the strategies' regime; futures as a labelled cross-check) + futures grid, AI summary |
| ⚔️ **Agents** | The war-room transcript per ticker ("How the decisions are made") |
| 🔭 **Explore** | Projection fan, mix donut, backfilled "if you had held" chart (labelled reconstruction), portfolio risk, what-if scenarios + sliders, "What's moving", glossary |

A hidden **Setup** view appears when the portfolio is empty: a sample portfolio, manual entry, or CSV upload.

> **Naming collision:** top-level **Explore** vs **Plan › Explore** (the strategy catalog). There is no Settings tab.
> Settings live as rows plus bottom sheets on **Plan › Goal**, and in the **Cash modal** on Holdings.

## 4. The Plan tab (PR 104, "A's sub-tabs built the C way")

The design exploration is `docs/design/plan-tab-options-2026-10-05.html` (12 phone boards). The options were:
- the old single page (risk was set in five places)
- **A**: three sub-tabs
- **B**: one page with numbered sections
- **C**: a summary of rows with Edit sheets

Shipped: **A + C**.

- **Goal:**
  - Goal summary ("₪X by Mon YYYY", progress, **N% chance with your protective rules as they are**, without-rules and halving chips, needs vs expected return).
  - Four rows, each with **Edit**: **Target** · **Monthly deposit** → Edit-goal sheet; **Risk dials** → Risk-dials sheet; **Card thresholds** → thresholds sheet.
  - "Ways to close the gap" (goal cards).
- **My mix:**
  - "What you hold vs your plan": per sleeve, Core, Commodities and Cash, held% / target% with a bar and target tick. Sleeves have Remove (sells nothing). A ticker two sleeves share is split between them (PR 131). The chip counts both sides: "N pts to fund · M pts over", or "on plan".
  - The goal card quotes `goal_chance`; an out-of-date one shows "As of … — <reason>" and is re-solved once per load (PR 131).
  - **Fund all sleeves** (dry-run, then confirm) and **Change core**.
  - **Your protective rules**, with the odds they cost, the retune card, and **Edit rules**.
- **Explore:**
  - The strategy catalog (goal pills, profile chips marked *measured* vs *est.*, sleeve slider, What changes? / Add as a sleeve / Make this my core / Fund).
  - Folded at the foot: **"What would it take?"** (target solver: two numeric inputs, **never a slider**).
- **Sheets** (one shared bottom sheet, `openSheet`):

  | Sheet | Edits |
  |---|---|
  | Edit your goal | Target %/yr, deadline, monthly deposit |
  | Your risk dials | Objective, risk, Kelly ceiling, trust in backtests, crash-risk limit, on-track bar |
  | Card thresholds | Generated from `plan.thresholds_spec`, with Default / set-by-you / Reset |
  | Your protective rules | Checkbox per stop, guard % |

  Each sheet shows a live **"If you save this"** odds preview (`POST /plan/frontier/preview`, 650 ms debounce with a sequence guard).
  Saving re-solves the goal (~20 s).

## 5. Card anatomy (`recCard`, PR 103 "one shape for every card")

1. **Title with its number** + a severity pill: Important / Worth doing / Nice to have / Minor. The tooltip shows the priority breakdown.
2. **Scope chip:** SLEEVE · name / CORE / BOOK.
3. **One or two plain sentences** (`summary`).
4. **"Accept will: …"**
5. **Before → after chips** (e.g. "Commodities 6% → **10%**").
6. Refired note ("You marked this handled. It is back because…") and ⚠ contradiction notes.
7. **Decision tiles** when Accept needs a choice: `rule_vs_sleeve` (Sell vs Keep & move rule), `held_cash` (Keep waiting / Buy back now /
   Invest part elsewhere). The recommended tile is highlighted but still needs a click.
8. **Details** (folded): full why, impact, 💰 *How it's funded* ("Use ₪X from cash", "Sell N TICKER (~₪) · est. tax"), alternatives, steps, and the
   **Source Code & Data** audit (inputs, formulas with substituted values, Adversary findings).
9. Buttons: **✓ Accept** for executable cards (confirm: "This updates your InvestWise book only. No brokerage order is placed."),
   **✓ Mark as done** for guidance, and **Ignore** always.

Accept/Ignore post the card's *rendered state* (amount, severity), so the server can bring a card back when its number moves.
**Heads-up** rows are one line each, in a folded list at the bottom: swipe left or ✕ to dismiss. They never come back and never notify.

## 6. Rendering and state

- Vanilla JS template literals → `innerHTML`, with inline `onclick` handlers. Formatters: `ils()`, `pct()`, `ago()`, and `esc()` for HTML-escaping.
- State is module-level globals (`PLANH`, `RECMAP`, `PLX`, `RULES`…). `localStorage` holds UI conveniences only
  (`iw_plantab`, `iw_gran`). PR 69 deleted the per-device snooze store and clears its legacy keys on load.
- Today's six requests fire in parallel, and each block renders as it lands (PR 104c.1).
- Charts: **Chart.js 4.4.1** (cdnjs) through one helper, `drawChart`. The rules for charts:
  - Y is **% change, not value**, so the benchmark shares the axis.
  - `spanGaps:false` is load-bearing: unmeasured days break the line instead of drawing 0%.
  - x-axis dates must exist (`maxTicksLimit: 6`).

## 7. Design tokens

**Dark only** (`color-scheme: dark`, theme `#0b0f17`). There is no light theme and no reduced-motion handling.

| Token | Value | Use |
|---|---|---|
| `--bg` | `#0b0f17` | Page |
| `--card` / `--card2` / `--soft` | `#111824` / `#0f1622` / `#16202e` | Surfaces |
| `--line` | `#1f2a3a` | Borders, chart grid |
| `--ink` / `--mut` | `#e8eef6` / `#8b97a8` | Text / muted text, axis ticks |
| `--blue` | `#3b82f6` | Brand, primary, impact boxes |
| `--green` | `#34d399` | OK, up, Accept, recommended |
| `--amber` | `#fbbf24` | Warning, stale, "needs" |
| `--red` | `#f87171` | Bad, triggered, down |

- **Type:** system UI stack; `ui-monospace` for formulas. Section `h2` is 12 px uppercase, muted, letter-spacing .07em. The hero number
  is 30 px/800. Use `tabular-nums` for numbers.
- **Shape:** cards have radius 14–16 px, a 1 px `--line` border and 14–16 px padding; pills 999 px; buttons 9–12 px. Content max width is
  600 px (1,100 px for Agents).
- **Icons are emoji.** There is no icon font.
- **Mobile:** `viewport-fit=cover` plus `env(safe-area-inset-*)`. Bottom-nav buttons are ≥ 48 px; Edit buttons ≥ 72 × 40. Pill rows scroll
  horizontally rather than wrap.
  - Real incident (PR 82): a 102 px action group pushed a 360 px phone page to 489 px, Chrome zoomed out, and the bottom bar fell off-screen.
    **Test on a phone width.**
- **Debt:** `.seg` is defined twice; some JS uses undefined vars with fallbacks (`--warn`, `--fg`, `--muted`); much PR 104 UI uses inline
  styles; native `confirm()`/`alert()` are used.

**Language:** English only (`<html lang="en">`), with ₪ throughout. No RTL. QA lists skip the Hebrew/RTL pass unless a PR touches it.

**Accessibility (honest state):** minimal.
- What exists: Plan sub-tabs have `role="tablist"`, sheets have `role="dialog"`, plus a few `aria-label`s and `title` tooltips.
- Gaps: no focus styles, no focus trap or Escape on sheets, many clickable `<span>`/`<div>`, and no contrast audit.

## 8. Service worker (`sw.js`)

- `VERSION = 'iw-vNN'` (currently **`iw-v51`**). **Bump it in the same commit as any change to `index.html` or another shell asset**,
  and record the new version in STATUS.
- **install:** pre-cache the shell with `new Request(url, {cache:'reload'})`. A plain `cache.add` re-cached the old HTML under the new key
  ("the bug we shipped three times"; asserted by `tests/test_shell_cache_headers.py`). Then `skipWaiting()`.
- **activate:** delete other versions, then `clients.claim()`.
- **fetch:**

  | Request | Strategy |
  |---|---|
  | `/api/*` | **Never intercepted**: a stale portfolio is worse than a clear failure |
  | Navigations and shell HTML | **Network-first**, cached fallback |
  | Icons, manifest | Cache-first |
  | Chart.js CDN | Stale-while-revalidate |

- **push:** `category: 'info'` (price moves, digest) is silent. Action pushes map to a Today card and `requireInteraction`.
- **notificationclick:** focus or open `/app/`.

## 9. Web push (client)

🔔 → permission → `GET /push/public-key` (VAPID) → `pushManager.subscribe` → `POST /push/subscribe` → `POST /push/test`.
On every load: if permission is granted but the subscription is missing, **re-subscribe**. That is the fix for the two-week silent outage
after the iw-v9→v10 bump. If subscribed, re-POST and `POST /push/check` to catch up. Server-side jobs: [ARCHITECTURE.md §7](ARCHITECTURE.md#7-background-work).

## 10. UX principles (product voice)

These are evident in code and copy. Hold new UI to them:

1. **Every number says what it measures.** Use "est." vs "measured", units in names, and weight caps in points of the book. No filler why/impact text.
2. **A card may not claim what it doesn't produce.** "Accept will…" + before/after chips; swaps called swaps; "Marked as done: nothing was bought or sold".
3. **A card asks you to do something and Accept does it.** Otherwise it's a heads-up.
4. **Short cards.** Title + number, 1–2 sentences, the Accept line, chips; everything else behind Details. "Your goal" is said once.
5. **Every decision is a setting.** It shows its default, "set by you", and Reset.
6. **The app never trades for you.** Every write confirm says it updates the InvestWise book only; "Remove sleeve" says it sells nothing;
   standing orders say "set the same transfer up at your bank".
7. **The server is the single source of truth.** The banner counts only carded rules; two sources for one fact is a bug.
8. **An honest empty state beats a full wrong one.** No reconstructed history on the account chart; "could not be priced: not ₪0"; "Some checks didn't run".
9. **A caption may only certify what was checked.**
10. **Correct-but-unwelcome answers look like answers, not errors.** UNREACHABLE is amber, not red. Never use a slider where most positions are unattainable.
11. **Lead with the owner's question:** "You put in X, it is worth Y."
12. **Phone first;** fixes cite real Pixel incidents.
13. **Voice:** plain, second person, short declaratives. Rounded ₪, bold key figure. Engineering rationale goes in code comments, not on screen.
14. **"Not financial advice."** on advisory surfaces.

## 11. Testing the UI

- There are no browser/E2E tests. Static-HTML string tests pin structure: `test_plan_tab_layout.py`, `test_shell_cache_headers.py`,
  `test_every_limit_is_a_setting.py` (`test_the_old_goal_settings_form_is_gone`), `test_history_is_read_only.py`, `test_today_loads_fast.py`, and others.
- Post-deploy checks:
  - the API-level per-PR smoke ([DEVELOPMENT.md §8](DEVELOPMENT.md#8-shipping-a-pr))
  - a **Pixel 9 manual QA** list in `qa/QA-YYYY-MM-DD-<slug>.md`

  QA list format:
  - `## 0. Pre-flight` (prod serves the SHA, CI green, PWA picked up `iw-vNN`)
  - numbered checks of the form "tap → you should see **exact copy**" plus a **Fail:** line
  - "Looks bad but isn't"
  - a roll-up reply template (✅/❌ per item)
