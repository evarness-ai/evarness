# Security policy

## Reporting a vulnerability

Report security issues privately, through GitHub's private vulnerability reporting: open
the repository's **Security** tab and choose **Report a vulnerability**. Do not open a
public issue, pull request or discussion for a suspected vulnerability.

Include what you can: the affected component and version (or commit), steps to reproduce,
and the impact you expect. The maintainers acknowledge the report in the advisory thread,
keep you updated there, and credit you in the advisory unless you ask not to be.

## Supported versions

Evarness is pre-1.0. Security fixes land on the latest release only.

| Version | Supported |
|---|---|
| 0.2.x | Yes |
| < 0.2 | No |

## Scope

In scope:

- **Governance bypasses**: any path that runs a tool, calls a model or answers without
  passing the governance kernel (`src/evarness/kernel/governance`), skips the egress
  gate, a judge or a required approval, or leaves no audit row.
- **Data egress**: personal or secret-classified data (email contents, identity, vault
  contents) reaching a cloud model, a log, a trace or any network destination it should
  not.
- **The harness**: the runtime, the agentic loop, the plugin host and SDK
  (`src/evarness`), and the memris memory graph (`src/memris`).
- **The servers**: the Evarness API, the Governor, the evaluator and the channel gateway
  (`src/evarness/server`), including authentication and device pairing.
- **The sandbox**: the code-execution sandbox and its container isolation
  (`src/evarness/tools/sandbox`).
- **The vault and credentials**: secret storage, OAuth tokens and IMAP app passwords.
- **Mailbox writes**: a label, move or other mailbox change made without the owner's
  approval.

Out of scope: vulnerabilities in third-party dependencies with no Evarness-specific exploit
path (report those upstream), third-party plugins (report to their authors), and
deployments that disable a governance control on purpose (for example, running with a
weak or published `EVARNESS_AUTH_SECRET`).
