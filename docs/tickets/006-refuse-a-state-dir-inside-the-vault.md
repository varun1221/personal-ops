# 006 — Refuse a state directory inside the vault

**Priority:** low · **Size:** S · **Found by:** spec review

## Why

History and checkpoints must live outside the vault: Obsidian Sync, iCloud or
Dropbox would copy a live SQLite file mid-write. Today that is only a sentence
in `docs/setup.md`; nothing stops `OPS_STATE_DIR=~/vault/.ops`.

## What

When the vault path is known, refuse to open the history store if the state
directory is inside it (resolved paths, symlinks followed), with a message that
says why and where to put it instead.

## Done when

- [x] `OPS_STATE_DIR` inside the vault fails loudly, naming both paths.
- [x] A vault *inside* the state directory (odd, but harmless) is still allowed.
- [x] Recording still never raises into a vault write (ADR 0004): a refused
      store is a gap in History with the reason on stderr, not a failed write.
