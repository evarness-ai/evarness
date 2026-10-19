# The launch recording

`launch.tape` is a [VHS](https://github.com/charmbracelet/vhs) script that records the
two-command story the launch posts tell: `evarness email demo` to its "What Evarness just
did" summary, then the proof bundle exported from the demo's ledger and verified to exit 0.

```bash
uv tool install "evarness[email]"      # the command the tape runs
brew install vhs                       # or see the VHS README for Linux and Windows
vhs docs/assets/recording/launch.tape  # writes launch.gif, launch.mp4 and launch.webm here
```

The outputs are not committed. Attach all three to the GitHub Release: the README embeds
`https://github.com/evarness-ai/evarness/releases/latest/download/launch.gif`, which
always resolves to the newest release's asset, the GIF is also what X and LinkedIn take
(under a few MB), and the MP4 or WebM is for the release notes. Re-run the tape against the
published wheel for each release, so the recording matches what the install line installs;
the recording must never show a wheel older than the one it is attached to.
