# 005 — Wrong status when one note has the same task twice

**Priority:** low · **Size:** S · **Type:** bug (edge case) · **Found by:** spec review

## What happens

`history.answers.vault_state` returns the status of the *first* line whose
wording matches. If a note holds a done "Call mum" and an open "Call mum",
`entry_history` and `ops history` can report the wrong one.

## Fix

Collect every matching line. One match → its status. Several → report
`"ambiguous"` and name the line numbers, consistent with how the rest of the
project refuses rather than guesses.

## Done when

- [x] Two same-wording lines with different states produce "ambiguous", not a guess.
- [x] `ops history` and `entry_history` both say so in plain language.
- [x] Test in `tests/test_memory_server.py`.
