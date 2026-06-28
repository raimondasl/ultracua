# @ultracua/client

Node/JS client for the **ultracua** Python core. It spawns the Python daemon and speaks
newline-delimited **JSON-RPC over stdio** — the language-agnostic surface for driving the
agent from JS/TS (or any language that can speak the same protocol).

```js
const { UltracuaClient } = require('@ultracua/client');

const client = new UltracuaClient().start();           // launches: uv run python -m ultracua.daemon
console.log(await client.call('health'));              // { status: 'ok', version: '...' }

const res = await client.call('run', {                 // learn or replay a flow
  url: 'https://example.com',
  goal: 'open the more information link',
  mode: 'auto',            // 'learn' | 'replay' | 'auto'
  provider: 'anthropic',   // 'anthropic' | 'openai' | 'gemini' | 'mock'
});
console.log(res);          // { mode, success, llm_calls, total_ms, avg_step_ms, final_text, ... }

client.close();
```

The daemon process stays warm across calls (provider + cache reused). Configure how it's
launched via `new UltracuaClient({ command, args, cwd })` — e.g. point `command` at a
Python that has `ultracua` installed if you're not using `uv`.

## Protocol

One JSON object per line, request → response:

```
--> {"jsonrpc":"2.0","id":1,"method":"health","params":{}}
<-- {"jsonrpc":"2.0","id":1,"result":{"status":"ok","version":"0.42.0"}}
```

Methods: `health`, `run`, `cache.delete`. See `index.d.ts` for parameter/return types.

## Smoke test

```bash
node smoke.js <repoRoot>                          # health only
node smoke.js <repoRoot> <cacheRoot> <url> <goal> # also replays a pre-learned flow (0 LLM)
```
