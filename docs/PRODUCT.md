# Product

> What InvestWise Pro is, who it is for, and the principles behind its design. Start here if you are new.

## What it is

InvestWise Pro is a **personal wealth cockpit**. You tell it what you own and what you're trying to achieve, and it tells you,
every day, **the few things worth doing next**. Each one is sized, funded and explained, and can be executed with one tap
on your tracked book.

It is built for **one owner** (Eran): an Israeli investor with a ₪20–25k book of leveraged and core ETFs (SOXL, QQQ, VTI,
BND, commodities…), deposits made monthly, and a goal expressed as **an annual return on the money put in, by a
deadline**. Everything is valued in **shekels**. It runs as an installed PWA on a Pixel 9.

It is **not** a broker. It never places orders. Accepting a card updates the book InvestWise tracks; you place the same
trade yourself. And it is **not financial advice**: every number traces to your data, a market price, or a labelled
assumption, and nothing is invented.

## The problem it solves

A self-directed investor with a plan faces three problems:
1. **Drift.** Markets move the book away from the plan, and the rules you set (stops, take-profits, caps) fire at inconvenient times.
2. **Contradictory advice.** Tools, newsletters and gut feel each say something different, with no single answer to "what now?"
3. **Unknown odds.** "Am I on track for my goal, and what would change that?" usually goes unanswered.

InvestWise answers with **one list of cards** that never contradict each other. Each card tells you why, what Accept will
do (before → after), and how it is funded. Behind them sits **one goal model** that says, in a single number, your chance
of reaching the goal with your protective rules as they are.

## How it works (user's view)

1. **Set up:** add holdings (manually, CSV, or a sample), cash, and monthly deposits.
2. **Plan:** pick an objective (Grow / Balanced / Income / Preserve), a goal (e.g. 30%/yr on what you put in, by 2031), a core
   basket, and optional **sleeves** (rule-based strategies such as "trend-filtered semiconductors", each with a % of the book).
   Adjust the risk dials and card thresholds if you want; every decision has a sensible default.
3. **Today:** open the app. You see a health score (On plan + On track), your goal odds, an account-history chart against SPY,
   and **What to do now**. Accept, Mark as done, or Ignore each card. Smaller notes collect in a folded **Heads-up** list.
4. **Notifications:** important cards, triggered rules, big price moves and a 07:00 digest arrive as push notifications.
5. **Plan › Goal:** see the odds, edit the goal or dials in a sheet with a live "if you save this" preview, and accept goal cards
   such as "retune your protection".
6. **Holdings › Rules:** stops, take-profits, trailing stops, caps, bands, profit-protect, trend-break and a whole-book drawdown
   guard. A firing never trades by itself. It raises a card.

The screens are documented in [FRONTEND.md](FRONTEND.md); the logic in [DOMAIN.md](DOMAIN.md).

## Product principles

These are the owner's rules, distilled from 100+ PRs. Each one exists because its opposite shipped once.

1. **Never invent a number.** Every figure is measured, priced, or a labelled assumption. Say "est." or "measured".
2. **A card may not claim what it doesn't produce.** Claims are computed by simulating the trade. A swap is called a swap.
3. **A card asks you to do something, and Accept does it.** Anything else is a heads-up.
4. **One answer.** One target mix, one goal, one priority scale, one funding ledger. The server alone decides what shows.
   Two sources for one fact is a bug.
5. **Sell only what you have too much of.** Funding never raids an on-target class, a sleeve, or the core.
6. **The goal is the objective.** Risk, Kelly and the frontier are tools for reaching it. The goal moves only when you deposit,
   withdraw, or change it.
7. **Every decision is a setting.** If the owner decides a limit, it is visible where it acts and editable in the app, with his
   value as the default.
8. **Honest emptiness beats confident fiction.** An empty chart that says why beats a reconstructed one. "Could not be priced"
   beats ₪0. A correct but unwelcome answer ("out of reach") looks like an answer, not an error.
9. **Short.** Title with its number, one or two sentences, what Accept will do, before → after. Everything else goes behind Details.
10. **Phone first.** The owner uses a Pixel 9; QA happens there.

## Scope today (2026-10-07)

| In | Out (for now) |
|---|---|
| One user, one entity ("Personal"), ILS base | Multi-user, spouse/corporate entities (modelled, unused) |
| US ETFs/stocks, TASE companies, commodities, cash | Israeli index funds on TASE (the provider can't price them) |
| Tracked-book execution + ledger | Real broker orders (parked; see [DECISIONS.md D41](DECISIONS.md)) |
| Goal-seeking frontier, Kelly sizing, fat-tail check | Tax-lot optimisation; inflation-indexed Israeli CGT basis (disclosed only) |
| English UI, dark theme | Hebrew/RTL, a light theme |

What's next: [BACKLOG.md](BACKLOG.md). How we got here: [HISTORY.md](HISTORY.md).
