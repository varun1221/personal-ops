# Setup

Everything needed to run this against a real vault. To just try it, the
quickstart in the [README](../README.md) needs no configuration at all.


```bash
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]"
cp .env.example .env
```

Then edit `.env`:

- `OBSIDIAN_VAULT_PATH` — start at `./fixtures/vault`, switch to your real vault once
  you trust it.
- `MODEL_PROVIDER` + the matching API key. `anthropic` for reliable tool calling while
  you debug the graph; `mistral` for the free tier. An Anthropic key comes from
  console.anthropic.com and is billed per token on its own account — a Claude Pro
  or Max subscription does not include API access. A Mistral free-tier key comes
  from console.mistral.ai. A 401 `Invalid API Key` from either means the key was
  revoked or expired, not that the config is wrong.
- `OPS_STATE_DIR` — optional. Where the history store and conversation checkpoints
  live. Defaults to `$XDG_STATE_HOME/ops`, else `~/.local/state/ops`. Keep it out
  of the vault: a sync service would copy a live SQLite file mid-write.

## Gmail

Only needed without `--no-gmail`. In Google Cloud Console: create a project, enable the
Gmail API, create an OAuth client ID of type **Desktop app**, download the JSON, and
point `GMAIL_CREDENTIALS_PATH` at it. First call opens a browser once; the token caches
to `GMAIL_TOKEN_PATH`.

Get consent out of the way outside the agent:

```bash
npx @modelcontextprotocol/inspector ./.venv/bin/python servers/gmail/server.py
```

## Verifying a server on its own

Always do this before blaming the agent:

```bash
npx @modelcontextprotocol/inspector ./.venv/bin/python servers/obsidian/server.py
```

`get_tools()` gathers across servers without `return_exceptions`, so one server failing
to start can take down the whole toolset. `agent/mcp_client.py` loads per-server to turn
that into a named failure, but the Inspector is still the fastest way to isolate one.

