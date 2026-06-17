// Type definitions for the ultracua Node/JS client.

export interface ClientOpts {
  /** Command to launch the daemon (default "uv"). */
  command?: string;
  /** Args to launch the daemon (default ["run","python","-m","ultracua.daemon"]). */
  args?: string[];
  /** Working directory for the daemon process. */
  cwd?: string;
}

export interface RunParams {
  url: string;
  goal: string;
  mode?: "auto" | "learn" | "replay";
  provider?: "anthropic" | "openai" | "gemini" | "mock";
  scope?: string;
  headless?: boolean;
  cache_root?: string;
  max_steps?: number;
}

export interface RunResult {
  mode: string;
  success: boolean;
  llm_calls: number;
  healed_steps: number;
  total_ms: number;
  avg_step_ms: number;
  final_text: string;
  note: string;
}

export interface Health {
  status: string;
  version: string;
}

export class UltracuaClient {
  constructor(opts?: ClientOpts);
  start(): this;
  call(method: "health", params?: {}): Promise<Health>;
  call(method: "run", params: RunParams): Promise<RunResult>;
  call(method: "cache.delete", params: { url: string; goal: string; scope?: string; cache_root?: string }): Promise<{ deleted: boolean }>;
  call(method: string, params?: object): Promise<any>;
  close(): void;
}
