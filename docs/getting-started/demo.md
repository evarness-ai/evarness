# Try the demo

`evarness email demo` runs the email assistant end to end on a synthetic mailbox. You need
no mail account, no credentials and no model server, the run makes no network
connections, and it never touches your own Evarness profile.

```bash
evarness email demo
```

The first run takes a few seconds. It ends with "What Evarness just did": counts of what
was fetched, judged and labelled, the approval it recorded, how many model calls it
made and on which provider, and the governance audit rows those calls wrote.

## What it does

1. **Fetch.** The email sweep reads a 200-message demo mailbox: bills, bookings and
   invitations, people waiting on an answer, newsletters, receipts and account notices,
   promotions and social mail. Every person and company in it is invented.
2. **Classify.** The email judge sorts each email into Bill, Event, Needs reply, FYI or
   Unsure, with one governed model call per email, and copies amounts and dates out
   of the email. Promotions, social mail and your own sent mail are released without a
   call, as they would be in your inbox.
3. **Label.** It shows the label preview that `evarness email setup` shows you before it
   asks: how many emails each `Evarness/*` label would go on, with sample subjects. The demo
   then records the approval for its own synthetic account, as an audit row and an
   approval row, and writes the labels into the demo mailbox. It does this only inside
   a demo home and only for the demo account. On your own mailbox nothing is written
   until you approve it.
4. **First digest.** The inbox digest: a short narrated summary, the Needs reply list,
   the bills due with their amounts and dates, and what is coming up.
5. **What Evarness just did.** The counts above.

Run it again and nothing new is fetched, judged or approved:

```bash
evarness email demo
```

Then look at every verdict the judge gave:

```bash
evarness email judgments --email-db ~/.evarness-demo/data/email.db --limit 10
```

To start over, `--reset` deletes the demo home (only a directory the demo created) and
runs again:

```bash
evarness email demo --reset
```

## How it stays isolated

- **Its own home.** Everything lives in `~/.evarness-demo` (or `--home DIR`, or
  `$EVARNESS_DEMO_HOME`): the email store, the demo mailbox's labels, the mailbox-write
  approval, the governance audit ledger and the vault master key.
- **A clean environment.** The run is a child process. Your `EVARNESS_*` settings and any
  variable that looks like a credential are dropped; only your time zone (`EVARNESS_TZ`)
  is kept.
- **A scripted model.** Every model tier runs on the scripted fake model. It replaces
  only the transport: each call still passes the governance kernel and leaves its
  audit rows, as a real model's call would. Your own plugin's tests use the same model,
  from `evarness.testing` ([Test your plugin](../guides/test-your-plugin.md)).
- **A throwaway vault key.** The demo generates one into its home and never reads or
  writes your OS keyring.
- **No network.** The run refuses every outbound connection.

## See it in the web console

Start the Evarness API on the demo's home with the demo's vault key, then open the console
as [The email assistant](../guides/email.md) describes:

<!-- ci: skip runs the API in the foreground until stopped -->
```bash
export EVARNESS_AUTH_SECRET="$(openssl rand -hex 32)"
EVARNESS_HOME=~/.evarness-demo EVARNESS_VAULT_MASTER_KEY="$(cat ~/.evarness-demo/vault-master.key)" evarness serve
```

Open Chat and Evarness starts with its one-time welcome, a turn no model writes. Inbox and
Setup show the demo mailbox, Governance shows the run's audit rows, and Sessions and
Call trace show the welcome turn.

## Next

[Connect your mailbox](connect-your-mailbox.md).
