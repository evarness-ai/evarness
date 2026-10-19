# ADR-0122: The loop delivers, or it is not proven

Status: Proposed (2026-09-24). The owner decided the rules below in a grill-me interview

**Status pass (2026-10-18):** Accepted; the decision is implemented (`services/routines/seeded.py`, `foundation/clock.py`, `config/heartbeats.yaml`). the core parts are implemented; finance and CalDAV parts are private; `loop-proof-plan.md` was never written

on the same day. The work is planned in `docs/architecture/loop-proof-plan.md`.
Relates to: ADR-0005 (soft migration of reminders), ADR-0029 (brief integration),
ADR-0030 (delivery-only reminders), ADR-0039 (finance alerts), ADR-0075 (calendar
providers), ADR-0116 (health watch), ADR-0120 (settings store), ADR-0121 (finance reads
trusted mail in full), cloud-trial plan decision 13 (private data never fails over).
Amends ADR-0030 (reminders gain recurrence, snooze and delivery retries; the legacy store
is retired rather than soft-migrated), ADR-0039 (the "future subscriber" for
`finance.alert` is the reminder store) and ADR-0075 (Apple is written over CalDAV from
a Linux host; the bill mirror is dropped). Supersedes nothing.

## Context

IRIS was built to be an assistant that is always on. On 2026-09-24 the owner said it
still is not: nothing arrives unless asked. A code map of `main` (9c17e715) showed why —
every detector exists and no delivery does:

- `finance_monitor` emits `finance.alert`; nothing subscribes.
- The morning brief renders, but only a user-authored **and approved** routine schedules
  it; the two the owner ever created are `retired` with `run_count 0`, and the VM's
  routine store was never seeded.
- Calendar writes on the VM stop at local ICS; Google sync is CLI-only; the Apple provider
  needs EventKit, which a Linux VM cannot call.
- Reminders live in two stores with two ticks; a failed send is logged after the row is
  marked fired.
- Heartbeat history is in memory; a restart erases it; the Activity feed never shows it.
- Cron jobs run in UTC.

The owner also believed they had configured news sources and interests for the digest.
No store holds them: news is a fixed query per category. The only preference record is
`brief_prefs.json` (section on/off and order).

## Decision

1. **Proof is a 7-day unattended run on the VM graded by the owner.** A missed digest,
   a missed reminder, a silent death or noise means not proven. Nothing else counts.
2. **One feature end to end: email → outcome → delivery.** Six buckets (`bill`, `event`,
   `needs_reply`, `fyi`, `promo`, `unsure`); nothing silently dropped; push only for bills
   and same-day events; everything else in the digest; `unsure` asks the owner.
3. **The digest is seeded core**, not user-authored: a `morning-digest` routine that
   exists on every install, is never auto-retired, and reads Settings → Digest
   (`sections`, `time`, `news_topics`, `news_sources` — preferred, never a strict list).
   One digest, three renderings (web full, Telegram chunked, push headline); a partial
   digest beats none. `IRIS_TZ` is set (America/Chicago).
4. **Bills push through the reminder store**, from a `finance.alert` subscriber: T-3d,
   day-of, overdue up to three times, amount-change once; deduped on `(bill_id, kind)`;
   resolved by "paid" in any channel or by ingest. Anomalies are digest-only.
5. **Apple Calendar is primary over iCloud CalDAV; Google is read-only.** Emails with an
   explicit date and time become events; ambiguous ones become cards; the bill mirror goes.
6. **Three chained bursts a day** (06:15/12:15/18:15 CT) replace the 10-minute sweep and
   the separate finance and index clocks. The 60 s ticks stay.
7. **An LLM judges every non-promo email** on a dedicated `judge` tier
   (`qwen3.5:4b-q4_K_M`, Mac-direct, **no cloud failover**). Every judgment is a row; the
   owner's corrections — including a Gmail label change — grade it; a bucket retires its
   LLM call at ≥95% for two weeks.
8. **One reminder store** with recurrence, snooze, accept-then-fire and three retries;
   a reminder no channel could deliver lands in the next digest, never in a log only.
9. **The loop must prove it is alive:** runs in `ActivityStore`, four watchdog checks
   paged by the health watch, a footer on every digest.
10. **Gmail is labelled, never archived or marked read,** until a bucket passes the bar.
11. **Rollout:** local shakedown, owner-run VM roll, deploy freeze during the 7 days.
12. **Learning: every owner signal changes one thing tomorrow, and the digest says so.**
    A 👎, a re-bucket or Gmail relabel, a sender confirm/ignore, paid/dismiss/snooze, a
    fact said in chat, a corrected figure, a Settings edit, reading behaviour and answer
    feedback each map to exactly one store and one effect observable by the next day
    (graph §9). Confirmed facts are read at render time. Every digest ends with
    `learned yesterday: …`, never omitted. Structure (section order) and low-tier facts
    are proposed with one tap, never applied on inference. Propose-only mechanisms stay
    outside the loop until each has a named effect. (Added 2026-09-25 after the owner's
    review of the graph: "an agent which self-evolves is the useful one, otherwise it's
    robotic.")

## Alternatives considered

- **Keep the digest user-authored** (create it in chat, approve it). Rejected: that path
  is where both existing digests died; the scorecard needs a row that survives restarts,
  redeploys and a fresh VM.
- **Category map instead of an LLM judge** (learned triage category → bucket, LLM only
  for extraction). Rejected by the owner for now: judge everything, measure a week, then
  retire calls per bucket as accuracy proves out. The map remains as a hint.
- **Cloud failover for the judge.** Rejected: email bodies stay on the owner's machines
  (decision 13). The cost — a deferred judge step when the Mac sleeps — is reported in the
  digest and is itself a scorecard signal.
- **Google Calendar as the write target.** Rejected: the owner uses Apple Calendar.
  Relaying through the Mac's EventKit was rejected because the Mac sleeps; CalDAV is
  headless.
- **Keep the 10-minute sweep.** Rejected: the shared Mac model contends with chat, and
  bill pushes at T-3d need no minute-level freshness. Accepted cost: a same-day-event
  email can be up to 6 h late.
- **Learning by inference alone** (apply mined behaviours and intentions to the digest
  automatically). Rejected: the owner's 2026-09-18 rule — a gate that costs the owner work
  is an off switch, but so is a change the owner did not ask for. Proposals with one tap
  keep the human in the loop for structure; corrections apply directly because the owner
  made them.
- **A strict news-source allowlist.** Rejected by the owner: preferred sources rank
  first, others still allowed, so a quiet day does not empty the section.

## Consequences

- Positive: every owner action has a named consequence the next day and is reported, so
  "does it learn me" is gradable on the same scorecard (rows V31–V37).
- Positive: every detector gets a delivery path through one reminder store and one
  digest; failures page instead of logging; the judge's accuracy is measurable from the
  owner's normal behaviour, with no grading chores.
- Negative: the burst cadence delays same-day-event emails by up to 6 h; judging every
  email costs ~180 Mac inferences a day in week 1; CalDAV needs an app-specific password
  the owner must place on the VM by hand.
- Reversible: bucket-by-bucket retirement of the judge, burst cadence, push kinds and
  auto-archive are all settings; the legacy reminder store migration is one-way.
