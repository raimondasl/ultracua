'use strict';

// Cross-language smoke test: a Node process drives the Python daemon over JSON-RPC.
//   node smoke.js <repoRoot> [cacheRoot url goal]
// With just <repoRoot>: calls health. With the extra args: also REPLAYS a pre-learned
// flow from <cacheRoot> (0 LLM), proving a real cross-language replay.

const { UltracuaClient } = require('./index');

(async () => {
  const repoRoot = process.argv[2] || process.cwd();
  const [, , , cacheRoot, url, goal] = process.argv;

  const client = new UltracuaClient({ cwd: repoRoot }).start();
  try {
    const health = await client.call('health');
    console.log('health: ' + JSON.stringify(health));

    if (cacheRoot && url && goal) {
      const res = await client.call('run', {
        url,
        goal,
        mode: 'replay',
        cache_root: cacheRoot,
        headless: true,
      });
      console.log('replay: ' + JSON.stringify(res));
      if (!res.success || res.llm_calls !== 0) {
        console.error('FAIL: expected a successful 0-LLM replay');
        process.exit(1);
      }
    }
    console.log('OK');
  } finally {
    client.close();
  }
})().catch((e) => {
  console.error('ERR ' + e.message);
  process.exit(1);
});
