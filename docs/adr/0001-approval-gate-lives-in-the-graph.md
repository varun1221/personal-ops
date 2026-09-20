# The approval gate lives in the graph, not in the MCP server

An MCP server cannot pause its caller's graph — it is a separate process that
can only return a value — so the gate cannot live where the write lives. The
agent's tool node checks each proposed call against `GATED_TOOLS`, calls
`interrupt()`, and dispatches only once `Command(resume=...)` comes back
approved.

## Considered options

**Gate inside the actions server.** The natural place, and impossible: the
server has no way to suspend the conversation and ask. The most it could do is
refuse, which is not the same as asking.

**`tool_interceptors` on `MultiServerMCPClient`.** A more declarative hook that
would put the gate closer to dispatch. Worth revisiting; the hand-rolled node
was chosen because the interrupt-ordering constraint in ADR-0002 is easier to
see and to test when it is written out explicitly.

## Consequences

The servers stay ignorant that an agent exists, so each one is drivable on its
own with the MCP Inspector — which is the fastest way to tell a server bug from
an agent bug.

The cost is that **the gate is a property of this client, not of the tools**.
Anyone who points their own agent at `servers/actions/` gets no approval step.
The server is safe to run, not safe by itself.
