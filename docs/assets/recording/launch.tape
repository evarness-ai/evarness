# The launch recording: three commands, no cuts, about 25 seconds. Rendered with VHS
# (https://github.com/charmbracelet/vhs): `vhs docs/assets/recording/launch.tape`.
# It re-records identically on a fresh checkout, so every release can refresh it.
# Prerequisites: `evarness` on PATH from a wheel (the README install line); no model
# server needed. The demo runs in its own home under /tmp/evarness-launch-home (the tape
# sets EVARNESS_DEMO_HOME and wipes the folder first), and the proof bundle is exported
# from that home's ledger with --db, so the viewer's own ~/.evarness is never touched.

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

Type "evarness governance proof-bundle export --db demo/governance/audit.db --out bundle.json && evarness governance proof-bundle verify bundle.json"
Sleep 500ms
Enter
Sleep 5s

Type "evarness learning status"
Sleep 500ms
Enter
Sleep 6s
