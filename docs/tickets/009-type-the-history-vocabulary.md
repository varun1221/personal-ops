# 009 — Give Change kinds and write paths their own types

**Priority:** low · **Size:** M · **Type:** refactor · **Found by:** standards review

## Why

`kind` ("created", "completed", …), `via` ("fast_path", "approved") and
Sighting `status` are free strings passed across nine call sites. A typo
records a kind nothing ever reads. `(note_path, description)` also travels
together through every History function, and appears as a bare tuple in
`Sighting.entry`.

## What

- `ChangeKind` and `WritePath` as `StrEnum`s; the database keeps storing text.
- A small `EntryRef(note_path, description)` for the pair.

## Done when

- [ ] No call site passes a raw kind or path string.
- [ ] Existing databases read back unchanged (the enums' values are the stored strings).
- [ ] Behaviour identical — the existing seams' tests pass untouched.
