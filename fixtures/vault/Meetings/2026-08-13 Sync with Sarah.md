---
tags: [meeting]
attendees: [Sarah Chen]
date: 2026-08-13
---

# Sync with Sarah — 13 Aug

## Context

Sarah is picking up the checkout implementation and needs the spec to scope it.

## Decisions

- Spec covers pricing edge cases, tax handling, and the retry path. Currency
  conversion is explicitly out of scope for v1.
- Sarah will start on the retry path since it's independent of the spec.

## Follow-ups

- **Me: send Sarah the draft checkout spec by Friday 21 Aug.** She's blocked on
  the pricing section specifically.
- Sarah: sketch the retry state machine, share async.
