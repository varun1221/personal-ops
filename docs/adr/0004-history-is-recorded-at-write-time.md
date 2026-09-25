# History is recorded at write time, and the vault stays authoritative

A Change is recorded by the code that makes it — the actions server and the fast
path — at the moment the write succeeds, not discovered afterwards by scanning
the vault. Writing it down as it happens gives an exact timestamp and says which
path made the change. A scan could only ever say "somewhere between the last two
runs", which is not an answer to "when did I send Sarah the deck?"

The cost is coverage: an edit made in Obsidian is never seen. So the database is
never asked whether an Entry is open. That is read from the vault every time, and
History only supplies *when*. An Entry that is done in the vault with no recorded
completion is reported as done on an unknown date, rather than contradicted.

## Considered options

**Scan the vault on each run and diff against a snapshot.** Sees every edit, but
every timestamp becomes a range bounded by how often `ops` is run, and identity
across a hand edit is a guess. Rejected because the question this exists to
answer is *when*, and a scan answers it worst.

**Write block IDs (`^a1b2c3`) onto task lines** so identity survives any edit.
Rejected because it writes to the vault in order to track the vault — mutation
nobody asked for, from the one project whose premise is that nothing is written
without a yes.

## Consequences

**Switching to scanning later changes what every stored timestamp means.** Old
rows are exact and new ones would be ranges; they cannot be mixed silently.

**Every write path must record, or its changes are invisible.** Recording lives
in the callers — the six actions tools and the fast path's `add`, `done` and `rm`
— not in the shared vaultlib write helpers, because a move *is* a create and a
delete underneath and has to be recorded as one "moved" Change, or the Entry's
History ends where the move began. Nine call sites can drift, so every one is
pinned by a test that makes the write and then asks `entry_history` what
happened.

**Recording failures must not fail the write.** The vault is the thing the person
cares about; a History row that could not be written is a gap, not an error.
