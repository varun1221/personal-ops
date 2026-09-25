# Personal Ops Agent

An agent that reads a person's Obsidian vault and Gmail to answer questions about
their commitments, and proposes changes to the vault that a human approves or
refuses. This file is the glossary: what the words mean here, and which word to
use when several would do.

## Language

### The vault

**Vault**:
The Obsidian directory the agent reads. Exactly one, always local, never assumed
to be laid out any particular way.
_Avoid_: library, notes folder, workspace

**Note**:
One markdown file in the vault.
_Avoid_: document, page, file

**Daily note**:
A note named for a single date, `YYYY-MM-DD.md`. Its filename is the
authoritative date for everything inside it — a line in a daily note gets its
date from the name of the file, never from the line.
_Avoid_: journal, diary entry, log

**Vault format**:
The convention a particular vault uses to record calendars and tasks — Full
Calendar, Tasks plugin, or Day Planner. A property of the vault, detected from
its own config, not a setting the agent imposes.
_Avoid_: schema, layout, plugin mode

### What is in it

**Entry**:
Anything in the vault the person has to do or be at. The umbrella term: every
Task and every Event is an Entry, and one Entry can be both at once.
_Avoid_: item, thing, record

**Task**:
An Entry seen as work to complete. It has a checkbox, and may have a due date.
_Avoid_: todo, action item, ticket

**Event**:
An Entry seen as time occupied. It has a date, and usually a start time.
_Avoid_: appointment, meeting, booking

**Day Planner entry**:
An Entry that is both a Task and an Event — a checkbox carrying a time block,
inside a daily note. It appears when listing tasks *and* when listing events;
that is a consequence of what it is, not a special case.
_Avoid_: planner block, timed task

**Time block**:
The leading `HH:MM - HH:MM` that makes a checkbox a Day Planner entry. What
separates a Day Planner entry from an ordinary Task.
_Avoid_: slot, span, timeslot

**Free slot**:
A gap between time blocks on a day, long enough to hold something. All-day
events do not consume it.
_Avoid_: opening, availability, gap

**Due date**:
When a Task is owed. Distinct from its *scheduled* date, which is when the
person meant to start it. Only the due date can make a Task overdue.
_Avoid_: deadline, target date

**Overdue**:
An open Task whose due date is before today. A Task with no due date is never
overdue — a backlog is not a missed deadline.
_Avoid_: late, outstanding, stale

**Commitment**:
A promise recorded as prose — in a note or an email — that has not been made
into an Entry. Finding these and turning them into Entries is what the agent is
for; a commitment that exists only in someone's head cannot be found by any tool.
_Avoid_: promise, obligation, follow-up

### Working with it

**Capture**:
Getting something into the vault in one gesture, before the thought is lost.
Distinct from asking the agent to do something: a capture states what to write,
it does not describe an outcome to work out.
_Avoid_: quick add, input, entry (the noun means something else here)

**Fast path**:
The route that serves a request without a model — parsed locally and written
directly. Its value is that it is cheap enough to use without thinking.
_Avoid_: shortcut, offline mode, cache

**Gated tool**:
A tool whose call cannot happen until a human has said yes to it. Every tool
that mutates the vault is gated; no tool that only reads is.
_Avoid_: dangerous tool, protected tool, privileged tool

**Approval**:
A person's yes or no to one proposed gated call, made against a rendering of
exactly what the call would do. A no is a complete answer, not an error — and it
is an Approval that was answered no, never a Refusal, which is the tool's word.
_Avoid_: confirmation, permission, sign-off

**Working set**:
What the conversation has established so far and can refer back to — the events
and tasks most recently named. It is short-term memory for resolving "the second
one", not a history of the conversation.
_Avoid_: context, memory, session state

**Refusal**:
A tool declining to act because what it was asked is ambiguous or invalid,
naming what it found rather than choosing. Distinct from an Approval that was
answered no: a refusal is the tool's decision, an approval is the person's.
_Avoid_: rejection, failure, error

### What it remembers

**Change**:
One recorded mutation to an Entry — created, completed, reopened, renamed, moved
or deleted — with when it happened and which path made it, the fast path or an
approved call. A Change records *when*; whether an Entry is open or done is
always read from the vault, never from its Changes.
_Avoid_: event (that is time occupied), log entry, edit

**History**:
All the Changes to one Entry, in order. What "when did I…" is answered from. An
Entry changed outside the agent has a gap in its History, and an answer says so
rather than guessing a date.
_Avoid_: log, audit trail, timeline

**Sighting**:
A Commitment the agent found and showed the person: where it was found, when it
was first raised, and what became of it — still open, captured as an Entry, or
dismissed. The same promise in two sources is two Sightings.
_Avoid_: detection, match, finding
