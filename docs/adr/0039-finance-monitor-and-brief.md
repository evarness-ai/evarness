# ADR-0039: Finance Monitor heartbeat + morning-brief integration

Status: Accepted (2026-06-13)

**Status pass (2026-10-18):** the code this record describes is in the private domain tree, not in this repository. heartbeat entry and brief slots reference `finance_workflows`; no finance module in this repository

Context: Finance build F4 (final finance phase). Implements ADR-0006's "Finance Monitor heartbeat (daily) + morning-briefing integration". Reuses the heartbeat (ADR-0013) + brief-slot (ADR-0029) machinery.

## Context

F0–F3 made the finance data reliable and queryable on demand. F4 makes it
**proactive**: a daily check that surfaces bills due/overdue and unusual
spend, delivered both as a background alert and in the morning brief.

## Decision

1. **Monitor core (`iris_harness.finance.monitor`)** — pure, deterministic
   (explicit `as_of`): `bills_due` (statement `amount_due`+`due_date` for
   the latest statement per account, plus manual bills, within a window
   and recently overdue) and `unusual_spend` (a category's current-month
   spend ÷ its trailing-`N`-month average ≥ ratio threshold, excluding the
   `uncategorized` bucket; a material first-time category also flags).
   `run_finance_monitor` bundles both + net worth. All per-currency
   (ADR-0007).

2. **Bills = three signals (ADR-0006).** Statement-derived dues (primary,
   already extracted in F2) + a **manual** `finance_bills` table
   (`iris finance bill add`). Recurring-vendor detection is the third
   signal, deferred.

3. **`finance_monitor` heartbeat** (`config/heartbeats.yaml`, cron
   `0 8 * * *`) — `FinanceMonitorHandler` runs the monitor, emits
   `finance.alert` (with pre-rendered one-line bills/anomalies) when there's
   something to say, and returns a `HeartbeatRun` summary. Follows the
   email-sweep pattern: never crash the heartbeat; emit an event for channel
   subscribers.

4. **Morning-brief integration** — a `finance-brief` skill with two
   read-only slot tools (`brief_bills_due`, `brief_unusual_spend`); the
   `morning-briefing` manifest gains a **Bills due** and **Unusual spend**
   section (placed right after the user's tasks). This is the guaranteed
   daily surface even if the user never sees a push notification.

5. **CLI** — `iris finance monitor` (run now), `iris finance bills`,
   `iris finance bill add|list`.

## Alternatives considered

- **Deliver alerts only via push/notification channels.** Rejected as the
  *primary* surface — wiring per-channel delivery is heavier and the brief
  is the established daily touchpoint; the event is emitted for a channel
  subscriber to consume when one is wired.
- **Baseline = average over months-with-data.** Chose average over a fixed
  `lookback_months` window (sparse prior months legitimately lower the
  baseline → a sudden spend is genuinely unusual relative to the window).
  Simpler and explainable; tunable.

## Consequences

**Positive:**
- The agent is proactive: bills due/overdue and spend spikes surface daily
  without the user asking.
- Monitor logic is pure + deterministic (`as_of`), fully unit-tested
  without the scheduler.

**Negative / still open:**
- ~~Recurring-vendor bill discovery (signal 3) not built.~~ **Built (2026-06-29):**
  `iris_harness.finance.recurring.detect_recurring_bills` mines the debit history for payees that
  recur on a regular cadence (subscriptions/EMIs/utilities) and predicts the next due date;
  surfaced via `monitor.recurring_bills` (suppressible) + `iris finance recurring`. See the
  decisions log entry (ADR-0087).
- Spend baseline is a simple window average; no seasonality.
- Channel delivery of `finance.alert` relies on a future subscriber; the
  brief is the guaranteed surface today.

## Related ADRs

- ADR-0006 (finance ingestion + monitor + bill signals), ADR-0007
  (per-currency), ADR-0013 (subsystem event topics), ADR-0029 (morning-brief
  slot mechanism), ADR-0037 (CLI), ADR-0038 (categories feed unusual-spend).
