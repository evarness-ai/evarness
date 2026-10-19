# The launch recording

`launch.tape` is a [VHS](https://github.com/charmbracelet/vhs) script that records the
two-command story the launch posts tell: `evarness email demo` to its "What Evarness just
did" summary, then the proof bundle exported from the demo's ledger and verified to exit 0.

```bash
uv tool install "evarness[email]"      # the command the tape runs
brew install vhs                       # or see the VHS README for Linux and Windows
vhs docs/assets/recording/launch.tape  # writes launch.gif, launch.mp4 and launch.webm here
```

`launch.gif` is committed: the README embeds it by its `raw/main` URL, which renders on GitHub
and on PyPI (PyPI's image proxy refuses the release-asset redirect, so the GIF cannot live on
the release alone). The MP4 and WebM are not committed; attach all three to the GitHub Release
for the release notes and for X and LinkedIn. Re-run the tape against the published wheel for
each release and commit the new GIF with the release-prep change, so the recording matches what
the install line installs; the recording must never show a wheel older than the one it is
attached to.
