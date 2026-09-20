# The fast path writes without asking

`ops add gym at 6pm` writes to the vault with no approval prompt, even though
the same change made through the agent would raise one. The gate exists so a
human reviews what a *model inferred*. On the fast path nothing was inferred:
the person typed the instruction, a regex parsed it, and the resolved time is
echoed straight back. Asking someone to confirm what they just typed is friction
with no safety in it.

This is the deviation a reader is most likely to mistake for an oversight, which
is why it is written down.

## Consequences

**The parser has to refuse rather than guess, or the reasoning collapses.**
`parse_capture` returns `None` for anything ambiguous — no time, a bare number
that might be part of a title, `"6-7"` with no am/pm — and those fall through to
the agent, which does prompt. The refusals are load-bearing, so
`tests/test_quickparse.py` weights them more heavily than the successes.

**Deleting still confirms.** `ops rm` asks, because deleting is the one thing
here that loses data that the person cannot get back from the terminal.

Capture has to be cheap or it does not happen, and a planner nobody captures
into is an empty folder. That is the value being bought.
