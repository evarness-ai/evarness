# The launch recording

`launch.tape` is a [VHS](https://github.com/charmbracelet/vhs) script that records the
three-command story the launch posts tell: `evarness email demo` to its "What Evarness just
did" summary, the proof bundle exported and verified to exit 0, and `evarness learning
status` showing proposals recorded and nothing applied.

```bash
uv tool install "evarness[email]"      # the command the tape runs
brew install vhs                       # or see the VHS README for Linux and Windows
vhs docs/assets/recording/launch.tape  # writes launch.gif, launch.mp4 and launch.webm here
```

The outputs are not committed: the GIF is for X and LinkedIn (under a few MB), the MP4 or
WebM for the GitHub Release and the README. Re-run the tape for each release so the
recording matches what the install line installs.
