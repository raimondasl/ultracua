'use strict';

// Node/JS client for the ultracua Python daemon: spawns it and speaks newline-delimited
// JSON-RPC over stdio. This is the cross-language binding for the Python core.

const { spawn } = require('child_process');

class UltracuaClient {
  /**
   * @param {{command?: string, args?: string[], cwd?: string}} [opts]
   *   How to launch the daemon. Default: `uv run python -m ultracua.daemon`.
   */
  constructor(opts = {}) {
    // On Windows, spawn() resolves an exact name (with extension) via PATH — use uv.exe.
    this.command = opts.command || (process.platform === 'win32' ? 'uv.exe' : 'uv');
    this.args = opts.args || ['run', 'python', '-m', 'ultracua.daemon'];
    this.cwd = opts.cwd;
    this._id = 0;
    this._buf = '';
    this._pending = new Map();
  }

  start() {
    this.proc = spawn(this.command, this.args, {
      cwd: this.cwd,
      stdio: ['pipe', 'pipe', 'inherit'], // daemon stderr -> our stderr
    });
    this.proc.stdout.setEncoding('utf8');
    this.proc.stdout.on('data', (d) => this._onData(d));
    this.proc.on('error', (e) => {
      for (const p of this._pending.values()) p.reject(e);
      this._pending.clear();
    });
    return this;
  }

  _onData(chunk) {
    this._buf += chunk;
    let i;
    while ((i = this._buf.indexOf('\n')) >= 0) {
      const line = this._buf.slice(0, i).trim();
      this._buf = this._buf.slice(i + 1);
      if (!line) continue;
      let msg;
      try {
        msg = JSON.parse(line);
      } catch {
        continue; // ignore non-JSON noise on stdout
      }
      const p = this._pending.get(msg.id);
      if (!p) continue;
      this._pending.delete(msg.id);
      if (msg.error) p.reject(new Error(msg.error.message));
      else p.resolve(msg.result);
    }
  }

  /**
   * @param {string} method
   * @param {object} [params]
   * @returns {Promise<any>}
   */
  call(method, params = {}) {
    const id = ++this._id;
    const req = JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n';
    return new Promise((resolve, reject) => {
      this._pending.set(id, { resolve, reject });
      this.proc.stdin.write(req);
    });
  }

  close() {
    if (this.proc && this.proc.stdin) this.proc.stdin.end();
  }
}

module.exports = { UltracuaClient };
