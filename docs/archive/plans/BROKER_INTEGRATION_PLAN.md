# InvestWise Pro — Broker Integration Execution Plan

**Goal:** let users take real actions (sync holdings, place orders) through InvestWise against an Israeli online broker — starting **broker-agnostic**, ending at **full two-way sync** (orders + positions + cash kept in sync). First concrete adapters target **Interactive Israel (inter-il.com)** and **IBI Trade (ibi.co.il)**.

> Status: design/plan only — no broker code is built yet. Nothing here moves real money until you have API credentials, a signed brokerage API agreement, and you explicitly enable live trading.

---

## 1. Design principle — one interface, many brokers

Add a `BrokerProvider` abstraction mirroring the existing market-data provider pattern (`app/providers/`), so the rest of InvestWise never knows which broker it's talking to.

```
app/brokers/
  base.py          # BrokerProvider ABC + DTOs (BrokerAccount, BrokerPosition, Order, Fill)
  mock.py          # deterministic in-memory broker (build + test against this NOW)
  inter_il.py      # Interactive Israel adapter   (added when creds exist)
  ibi.py           # IBI Trade adapter            (added when creds exist)
  registry.py      # pick adapter from config; wrap in the resilience tier (retry/breaker/rate-limit)
```

```python
class BrokerProvider(ABC):
    name: str
    # read
    def get_accounts(self) -> list[BrokerAccount]: ...
    def get_positions(self, account_id: str) -> list[BrokerPosition]: ...
    def get_cash(self, account_id: str) -> Money: ...
    # write
    def place_order(self, account_id: str, order: Order) -> OrderAck: ...
    def get_order(self, order_id: str) -> Order: ...
    def cancel_order(self, order_id: str) -> bool: ...
```

Everything reuses the existing resilience middle tier (cache → rate-limit → circuit-breaker → retry) already used for market data, so a flaky broker API can't take the app down.

---

## 2. Phased rollout

### Phase 0 — Abstraction + mock (no broker account needed)
Build `BrokerProvider`, DTOs, the `mock` adapter, registry, and config (`BROKER=mock`). New tables (below) + endpoints wired against the mock. Ship behind a `BROKER_ENABLED=false` flag. **This is fully buildable today** and lets the whole flow be tested end-to-end with zero real risk.

### Phase 1 — Read-only sync (per your Q4 = full two-way, this is step 1 of it)
Connect a real broker read-only: import accounts, positions, and cash into InvestWise.
- `POST /api/v1/broker/connect` → store credentials (encrypted), link a broker account to an InvestWise entity.
- `POST /api/v1/broker/sync` (+ hourly scheduler job, reusing the market-refresh scheduler) → pull positions/cash and **reconcile** against the user's holdings (show drift; let the user accept the broker as source of truth).
- Today/Holdings show a "Synced from <broker> · N min ago" badge.

### Phase 2 — Order placement (manual confirm)
Turn the **Accept** button you just shipped into real execution. This is exactly the "stage as pending" path you asked for:
1. Accept a recommendation → creates a **pending order** (status `STAGED`) instead of mutating holdings.
2. User reviews staged orders on a new **Orders** screen → taps **Execute** → `place_order` to the broker.
3. Webhook/poll updates order status `STAGED → SUBMITTED → FILLED/REJECTED`; on `FILLED`, holdings update from the actual fill (price, qty, fees) — not the estimate.
A hard `TRADING_ENABLED` flag + per-order confirmation + daily notional limit guard against runaway automation.

### Phase 3 — Full two-way sync
Continuous reconciliation: scheduled sync + order webhooks keep positions/cash/orders aligned both directions; conflict rules (broker wins for fills; InvestWise owns plan/targets). Optional auto-execute for low-risk rebalances under a user-set threshold (off by default).

---

## 3. Data model additions

```
broker_connections : id, user_id, entity_id, broker (mock|inter_il|ibi),
                     external_account_id, status, credential_ref (vault key), last_synced_at
orders             : id, user_id, account_id, recommendation_id?, side(BUY/SELL),
                     ticker, market, quantity, order_type(MARKET/LIMIT), limit_price?,
                     status(STAGED/SUBMITTED/FILLED/PARTIAL/REJECTED/CANCELLED),
                     broker_order_id?, avg_fill_price?, filled_qty, fees, created_at, updated_at
order_events       : id, order_id, status, payload(json), at   # immutable audit trail
```
The existing `recommendations.apply` spec already produces the trades; Phase 2 routes that spec into a staged `Order` instead of editing holdings directly. Minimal rework — the "apply engine" gains a `mode="stage"` alongside today's `mode="apply"`.

---

## 4. Auth, security & compliance (the gating items)

- **Connection method** depends on what each broker offers — to confirm with them: OAuth2 (preferred), API key/secret, or signed-session. Until confirmed, the adapter interface assumes OAuth2 + refreshable token.
- **Credential storage:** never in DB plaintext or git. Use a secrets manager (Railway secrets / Vault); store only a `credential_ref`. Encrypt tokens at rest (the app already ships `cryptography`).
- **Scopes:** request read-only first; trading scope only when Phase 2 is enabled.
- **Auth must be ON** (`REQUIRE_AUTH=true`) before any broker is connected — which depends on the credential-policy decision still in your backlog.
- **Regulatory:** order routing to an Israeli broker may require their API partner agreement and ISA-related compliance; InvestWise stays an "order-entry assistant" — the user confirms every trade. No discretionary trading without explicit, separate consent.
- **Audit:** every order + state change written to the immutable `order_events` + existing audit log.

---

## 5. What I need from you to start each phase

- **Phase 0 (mock):** nothing — I can build it now.
- **Phase 1 (read-only):** which broker first, and **API docs + sandbox/API credentials** from inter-il and/or IBI (most Israeli retail brokers don't publish open APIs — we may need to request partner/API access, or fall back to a secure statement/CSV import). Confirm whether they offer an API at all.
- **Phase 2 (orders):** signed broker API/partner agreement, trading scope, and your sign-off on the order-confirmation UX + notional limits.

**Open question for you:** do inter-il / IBI already give you API access, or should the first real integration instead be a **secure holdings import** (broker statement / CSV / Open-Banking-style feed) while we pursue API access in parallel?

---

## 6. Recommended next step

Build **Phase 0 (the broker abstraction + mock + Orders screen + staged-order flow)** now. It makes "Accept → stage → execute" real against a simulated broker, so the moment you have inter-il/IBI credentials we drop in the adapter and flip `BROKER=inter_il` — no UI or core changes.
