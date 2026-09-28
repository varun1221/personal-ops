# 001 — Record the demo GIF

**Priority:** high · **Size:** S · **Owner:** Varun (cannot be done by an agent)

## Why

The README's second line is `docs/demo.gif`, and the file does not exist. On
GitHub that renders as a broken image directly under the tagline — the first
thing anyone sees.

## What

Record the scene scripted in [`docs/record-demo.md`](../record-demo.md): the
assistant proposes adding an event, the approval prompt shows exactly what it
will write, the answer is no, and the vault is unchanged.

## Done when

- [ ] `docs/demo.gif` exists, under ~5 MB, readable at GitHub's README width.
- [ ] It runs against `fixtures/vault`, not a real vault — nothing personal on screen.
- [ ] The README alt text still describes what the GIF shows.
