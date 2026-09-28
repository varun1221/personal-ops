# 008 — Settle two names that clash with the glossary

**Priority:** low · **Size:** S · **Type:** decision + rename · **Found by:** standards review

## The clashes

1. **`past_rejections`.** `CONTEXT.md` lists *rejection* under **Avoid** (for
   Refusal) and says an Approval answered no is never a Refusal. The tool name,
   its prompt text and `ApprovalRecord` all say "rejection".
2. **The `memory` server.** *memory* is under **Avoid** for Working set, and the
   system prompt now describes two different things as memory.

## Decide

- Rename to fit the glossary (e.g. `declined_approvals`, and a `history`
  server) — or
- Amend the glossary if "rejection" and "memory" are the words people actually use.

Either is fine; leaving the code and glossary disagreeing is not.

## Done when

- [ ] Code, prompt, README and `CONTEXT.md` use one word for each idea.
- [ ] Tool renames are reflected in `cli.MEMORY_TOOLS` and the eval scenarios.
