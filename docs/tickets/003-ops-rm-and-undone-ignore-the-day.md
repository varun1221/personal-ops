# 003 — `ops rm` and `ops undone` ignore the day you name

**Priority:** high · **Size:** S · **Type:** bug

## What happens

`cmd_rm` and `cmd_done` both accept a `day_word`, but `main()` never passes
one (`ops.py`, the `undone` and `rm` branches). Everything after the verb
becomes the search text, so:

```
ops rm gym tomorrow      → searches *today's* note for "gym tomorrow"
ops undone standup yesterday → searches today's note for "standup yesterday"
```

Both fail with "no match", and the only workaround is to not have the entry be
on another day. Found while writing `tests/test_ops_history.py`.

## Fix

When the last word is a day (`today`, `tomorrow`, `yesterday`, `tmr`, or a
`YYYY-MM-DD` date), split it off and pass it as `day_word`. The same rule for
both commands; `resolve_day` already understands the words.

## Done when

- [ ] `ops rm gym tomorrow` removes tomorrow's gym block (after the usual y/N).
- [ ] `ops undone standup yesterday` reopens yesterday's standup.
- [ ] A title that genuinely ends in a day word still works when quoted or given
      without one — decide and test which wins; ambiguity should refuse, not guess.
- [ ] Tests through `ops.main`, alongside `tests/test_ops_history.py`.
