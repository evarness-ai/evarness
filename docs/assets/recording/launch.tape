# The launch recording: two commands, no cuts, about 15 seconds. Rendered with VHS
# (https://github.com/charmbracelet/vhs): `vhs docs/assets/recording/launch.tape`.
# It re-records identically on a fresh checkout, so every release can refresh it.
# Prerequisites: `evarness` on PATH from a wheel (the README install line); no model
# server needed. The demo runs in its own home under /tmp/evarness-launch-home (the tape
# sets EVARNESS_DEMO_HOME and wipes the folder first), and the proof bundle is exported
# from that home's ledger with --db and that home's (empty) session logs with
# --session-logs. Both flags matter: without --session-logs the export reads the
# recorder's own ~/.evarness/logs and checks the demo's ledger against someone else's
# chat sessions, which fails every-call-and-answer-audited for a reason that has nothing
# to do with the demo.
#
# Why two commands and not three: a scripted run leaves the learning loop nothing to learn
# from, so `evarness learning status` after the demo shows zeros. The loop is described in
# the README and shown on a connected mailbox, not here. The bundle's "model call(s) ...
# observed" count is 0 as well: the demo records no chat sessions, so the
# every-call-and-answer-audited invariant has nothing to observe; the README says so.

Output docs/assets/recording/launch.gif
Output docs/assets/recording/launch.mp4
Output docs/assets/recording/launch.webm

Set Shell "bash"
Set FontSize 18
Set Width 1200
Set Height 640
Set Theme "Catppuccin Mocha"
Set TypingSpeed 40ms
Set Padding 24

Env EVARNESS_DEMO_HOME "/tmp/evarness-launch-home/demo"
Hide
Type "rm -rf /tmp/evarness-launch-home && mkdir -p /tmp/evarness-launch-home && cd /tmp/evarness-launch-home && clear"
Enter
Show

Type "evarness email demo"
Sleep 500ms
Enter
Sleep 9s

Type "evarness governance proof-bundle export --db demo/governance/audit.db --session-logs demo/logs --out bundle.json && evarness governance proof-bundle verify bundle.json"
Sleep 500ms
Enter
Sleep 6s
