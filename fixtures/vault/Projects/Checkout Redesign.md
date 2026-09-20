---
tags: [project]
status: active
---

# Checkout Redesign

## Status

Spec is ~60% drafted. Pricing edge cases are the remaining gap and the thing
blocking Sarah.

## Open threads

- Draft spec owed to Sarah by 21 Aug — see [[2026-08-13 Sync with Sarah]]
- Auth refactor PR from Marcus needs review before it merges into this work
- Design review scheduled 20 Aug covers the visual side only, not the spec

## Notes

The retry path is genuinely tricky — payment provider returns a 202 with an
opaque token and we have to poll. Worth its own section.
