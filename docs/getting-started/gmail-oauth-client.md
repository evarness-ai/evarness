# Gmail: the OAuth client

`evarness auth gmail login` reads your Gmail through Google's API with the standard OAuth 2.0
installed-app flow. Before the first login you create a small Google Cloud OAuth client of
your own, once per machine; this page walks through it in about ten minutes. Tokens then
live in the OS keyring (macOS Keychain; ADR-0003), and only the non-secret client
configuration sits on disk.

If your Gmail account can create an app password, IMAP needs none of this: see
[Connect your mailbox](connect-your-mailbox.md#imap-with-an-app-password).

The commands, once the client exists:

<!-- ci: skip the login opens a browser for Google's consent screen -->
```bash
evarness auth gmail login --user <your-address@gmail.com>
evarness auth gmail status
evarness auth gmail logout --user <your-address@gmail.com>
```

Without the client, `login` stops with `OAuth client secrets not found at ...` and names
this page.

## Why you have to do this manually

Google's OAuth model requires every Evarness install to use its own OAuth
2.0 *client* (a `client_id` + `client_secret` pair), not a shared one
you'd get from us. The setup is identical to what `gcloud`,
`google-cloud-sdk`, or any installed Google client tool needs.

You do it **once per machine**. Subsequent `evarness auth gmail login`
runs reuse the same client to authorize additional addresses. It also needs the
`[email]` extra, which `uv tool install "evarness[email]"` includes; `evarness doctor`'s
extras row says whether it is installed.

## Setup (≈10 minutes)

### 1. Create a Google Cloud project

1. Visit [console.cloud.google.com](https://console.cloud.google.com).
2. Top-bar dropdown → **"New Project"**. Name it anything (e.g. `evarness-personal`).
3. Make sure your new project is selected in the top-bar dropdown for the rest of these steps.

### 2. Enable the Gmail API

1. Left nav → **"APIs & Services" → "Library"**.
2. Search **"Gmail API"**. Open it. Click **"Enable"**.

### 3. Configure the OAuth consent screen

1. Left nav → **"APIs & Services" → "OAuth consent screen"**.
2. Choose **"External"** user type (the only option for personal Gmail accounts that aren't part of a Google Workspace).
3. Fill the minimum required fields:
   - **App name**: `Evarness` (or your preference)
   - **User support email**: your address
   - **Developer contact**: your address
4. **Scopes**: click "Add or Remove Scopes" and select **`.../auth/gmail.modify`**. It lets Evarness read your mail and move messages to Trash and back (ADR-0118). It does **not** allow permanent deletion, which needs full mail access that Evarness never requests. (Before 2026-09-21 this was `gmail.readonly`; see "Upgrading from read-only" below.)
5. **Test users**: add your own Gmail address. (External + Test mode means only listed test users can authorize — fine for personal use; no Google verification needed.)
6. Save.

### 4. Create OAuth 2.0 credentials (Desktop app type)

1. Left nav → **"APIs & Services" → "Credentials"**.
2. **"+ Create Credentials" → "OAuth client ID"**.
3. **Application type**: `Desktop app`.
4. **Name**: `Evarness desktop client` (or your preference).
5. Click **"Create"**.
6. In the resulting dialog, click **"Download JSON"**. Save the file.

### 5. Place the client secrets file where Evarness expects it

The paths below assume the default Evarness home, `~/.evarness`. If `evarness setup`
put your home elsewhere (`EVARNESS_HOME`), use that directory instead; the login's
error message prints the exact path it looked at.

**Run these commands one at a time** — do not paste them as a single
block. Some Markdown viewers strip newlines on copy, which collapses
the four commands into one `mkdir` invocation that then fails on the
`mv` (mkdir treats the trailing arguments as additional directories
to create and chokes on the existing Downloads file).

Create the credentials directory:

<!-- ci: skip creates the credentials directory under your own home -->
```bash
mkdir -p ~/.evarness/workspace/credentials
```

Move the downloaded file to the canonical name (your filename will
include a long client-id hash):

<!-- ci: skip moves the JSON you downloaded from Google Cloud -->
```bash
mv ~/Downloads/client_secret_*.json ~/.evarness/workspace/credentials/google_oauth_client.json
```

Lock down read/write permissions to your user only:

<!-- ci: skip needs the JSON from the step above -->
```bash
chmod 600 ~/.evarness/workspace/credentials/google_oauth_client.json
```

Verify the file landed at the expected path with the right mode:

<!-- ci: skip needs the JSON from the step above -->
```bash
ls -la ~/.evarness/workspace/credentials/google_oauth_client.json
```

You should see a `-rw-------` line at
`~/.evarness/workspace/credentials/google_oauth_client.json`. That path is
the default Evarness looks for; pass `--client-secrets PATH` to
`evarness auth gmail login` if you want it elsewhere.

### 6. Run the Evarness login

<!-- ci: skip opens a browser for Google's consent screen -->
```bash
evarness auth gmail login --user your-address@gmail.com
```

Evarness opens your default browser to Google's auth screen. Pick the
account (must match `--user`; Evarness rejects mismatches), allow the
permission it asks for, and the callback returns to a transient localhost
port. Tokens land in the keyring, and the account is registered in
Evarness's own database under its home. Then run `evarness email setup`; see
[Connect your mailbox](connect-your-mailbox.md#2-run-setup).

On macOS the first Keychain write triggers a system prompt
("`evarness` wants to access your keychain"). Choose **"Always Allow"**
so background routines don't re-prompt.

## Verify

```bash
evarness auth gmail status
```

You should see your address with `✓`, an expiry timestamp, and
`refresh token: yes`.

## Multiple addresses

Repeat steps 5 (path overrides if you like) and 6 with each address:

<!-- ci: skip opens a browser for Google's consent screen -->
```bash
evarness auth gmail login --user user.in@example.com
evarness auth gmail login --user user.us@example.com
```

The same OAuth client (step 4) handles both. Each address gets its
own Keychain entry, own `email_accounts` row, and (later) its own
sweep cursor.

## Upgrading from read-only (so Evarness can trash mail)

A token granted before 2026-09-21 is read-only. It keeps reading mail, but `trash_email`
answers "connected read-only" until you grant `gmail.modify`. Do this **in this order**:

1. Pull the version with this change onto the Mac: a login from older code asks for
   read-only again.
2. In the consent screen's Scopes (step 3.4 above), add `.../auth/gmail.modify`.
3. On the Mac, once per address:
   <!-- ci: skip opens a browser for Google's consent screen -->
   ```bash
   evarness auth gmail login --user you@gmail.com
   ```
   Google shows the new permission ("read, compose and send, and permanently delete"
   is **not** in the list; "manage" is). Allow it.
4. If Evarness also runs on a server, its keyring holds its own copy of the token: reconnect
   the account there from the web app (below), or run the same login on it, then restart
   its API.
5. Check it: ask Evarness to trash one email. The approval card lists it by subject; approve,
   then say "undo that" and it comes back.

## Reconnecting from the web app (the server runs the OAuth flow)

On a harness you reach over the network, a revoked Gmail, Calendar or Drive token can be
fixed from the phone:
Health shows **Reconnect** on the red row, and **Settings > Connections** lists every
Google account with Reconnect / Connect / Add a Google account. The server stores the new
token in its own keyring; the phone never sees it. The login above keeps working as the
fallback.

One-time setup, in the same Google Cloud project:

1. **Stop the weekly expiry.** APIs & Services > OAuth consent screen > Publishing
   status: if it says "Testing", choose "Publish app". Testing-mode refresh tokens expire
   after 7 days. For personal use Google shows an "unverified app" warning at login;
   click through it.
2. **Create a Web client.** Credentials > Create credentials > OAuth client ID > **Web
   application**. Authorised redirect URI:
   `https://<your-host>.ts.net/api/v1/connections/google/callback` (the harness's
   `EVARNESS_PUBLIC_URL` + that path; Settings > Connections shows the exact value).
3. **Give it to the server.** Download the client JSON and add it in Settings >
   Connections. It is stored in the server's keyring and never shown again. The Desktop
   client above keeps working for the Mac.
4. `EVARNESS_PUBLIC_URL` must be set in the server's environment
   (`EVARNESS_PUBLIC_URL=https://<your-host>.ts.net`); without it Reconnect refuses to start.

Safety rules the flow ships with: a random `state` and a PKCE verifier per start, each
link usable once for 10 minutes; the account Google approved must be the one being
reconnected (a different one saves nothing); the same scopes as the Mac login and no
others; no token in any response, redirect or log line; every start and outcome in the
governance audit ledger. Code: `src/evarness_personal/connections/google.py`.

## Troubleshooting

**"OAuth client secrets not found at …"** — step 5 wasn't done, or the file is at a non-default path (the message prints the path it looked at). Re-run with `--client-secrets <path>`. If you pasted step 5 as a single block and saw `mkdir: …client_secret_…json: File exists`, the newlines got stripped on copy — the `mv` never ran. Run each step-5 command on its own line.

**"OAuth authorized 'other@gmail.com' but CLI requested 'user@gmail.com'"** — you picked a different account in the browser than you passed to `--user`. Re-run with the matching address, or sign out of the other account in the browser first.

**"Access blocked: This app's request is invalid"** — the OAuth consent screen (step 3) wasn't completed, or your address isn't on the **test users** list. Go back to step 3.

**Token revoked / expired refresh token** — happens if you revoke access from your Google Account's security settings or after 6 months of inactivity. Run `evarness auth gmail logout --user <addr>` then `evarness auth gmail login --user <addr>` again.

## What gets stored where

| What | Where | Format |
|---|---|---|
| OAuth `client_id` + `client_secret` | `~/.evarness/workspace/credentials/google_oauth_client.json` | plain JSON (chmod 600) |
| OAuth access + refresh tokens | macOS Keychain (`evarness-gmail` service, account = email address) | JSON blob matching `google.oauth2.credentials.Credentials.to_json()` |
| Account registration row | `email_accounts` in Evarness's database (`data/evarness.db` under the Evarness home) | row with `provider='gmail'`, `address`, `active=1` |

Nothing in this flow involves Evarness sending data to a third party other
than Google itself for the OAuth handshake.

## Related

- ADR-0003 — Keychain over vault (credential storage rationale).
- ADR-0011 — Keychain credentials module implementation shape.
- ADR-0016 — Email accounts registry shape.
- `src/evarness_personal/plugins/gmail/gmail_oauth.py` — the implementation.
