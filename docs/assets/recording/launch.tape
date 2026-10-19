# The launch recording: three commands, no cuts, about 25 seconds. Rendered with VHS
# (https://github.com/charmbracelet/vhs): `vhs docs/assets/recording/launch.tape`.
# It re-records identically on a fresh checkout, so every release can refresh it.
# Prerequisites: `evarness` on PATH from a wheel (the README install line), a clean
# EVARNESS_HOME (the tape sets one), no model server needed.

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

Env EVARNESS_HOME "/tmp/evarness-launch-home"
Hide
Type "rm -rf /tmp/evarness-launch-home && clear"
Enter
Show

Type "evarness email demo"
Sleep 500ms
Enter
Sleep 9s

Type "evarness governance proof-bundle export --out bundle.json && evarness governance proof-bundle verify bundle.json"
Sleep 500ms
Enter
Sleep 5s

Type "evarness learning status"
Sleep 500ms
Enter
Sleep 6s
