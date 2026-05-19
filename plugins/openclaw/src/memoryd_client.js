/**
 * memoryd 统一客户端
 *
 * 抽象 plugin 调用本地 memoryd 的两种方式：
 *   1. `cli`  —— spawn `memoryd <subcmd> --json`（默认）
 *   2. `http` —— 调本地 `memoryd-server`（如果用户开了 Web Dashboard）
 *
 * 所有方法都支持注入 `spawnImpl` / `fetchImpl`（给测试 mock 用），
 * 失败时返回结构化错误而不是抛——上层 hook 是 fire-and-forget。
 *
 * 设计参考：
 *   - memsearch openclaw plugin 的 `runCmd` + getMemsearchCmd 模式
 *   - 本仓库现有 `payload.js` 的 spawn 复用 stdin JSON 协议
 *
 * 不引新依赖：只用 node:child_process / node:http / node:url。
 */

import { spawn } from "node:child_process";
import { request as httpRequest } from "node:http";
import { URL } from "node:url";
import { homedir } from "node:os";
import { join } from "node:path";

export const DEFAULT_MEMORYD_BIN =
  process.env.MEMORYD_BIN ||
  "/Users/abble/memory-system/memoryd/.venv/bin/memoryd";

export const DEFAULT_HTTP_PORT = Number(process.env.MEMORYD_HTTP_PORT || 8765);

/**
 * 主入口：返回一个 client 对象。
 *
 * @param {object} opts
 * @param {'cli'|'http'} [opts.mode='cli']
 * @param {string} [opts.binPath]               cli 模式下 memoryd 可执行文件
 * @param {number} [opts.port]                  http 模式下监听端口
 * @param {string} [opts.host='127.0.0.1']
 * @param {number} [opts.timeoutMs=15000]
 * @param {Function} [opts.spawnImpl]           覆盖 node:child_process.spawn（测试用）
 * @param {Function} [opts.fetchImpl]           覆盖 http 请求实现（测试用）
 */
export function createMemorydClient(opts = {}) {
  const mode = opts.mode || "cli";
  const bin = opts.binPath || DEFAULT_MEMORYD_BIN;
  const port = opts.port || DEFAULT_HTTP_PORT;
  const host = opts.host || "127.0.0.1";
  const timeoutMs = opts.timeoutMs || 15000;
  const spawnImpl = opts.spawnImpl || spawn;
  const fetchImpl = opts.fetchImpl || defaultHttpFetch;

  const transport = mode === "http"
    ? createHttpTransport({ host, port, timeoutMs, fetchImpl })
    : createCliTransport({ bin, timeoutMs, spawnImpl });

  return {
    mode,
    bin,
    port,

    /** memory_search 后端 —— hybrid search */
    async search({ query, top_k = 10, scope = "auto" } = {}) {
      if (!query || typeof query !== "string") {
        return { ok: false, error: "query is required" };
      }
      const args = ["search", query, "--limit", String(top_k), "--json"];
      if (scope && scope !== "auto") args.push("--scope", scope);
      return transport.invoke({
        subcommand: "search",
        argv: args,
        httpPath: "/v1/search",
        httpBody: { query, top_k, scope },
        parseJson: true,
      });
    },

    /** memory_get 后端 —— 取 markdown 全文 */
    async get(memoryId) {
      if (!memoryId || typeof memoryId !== "string") {
        return { ok: false, error: "memory_id is required" };
      }
      return transport.invoke({
        subcommand: "show",
        argv: ["show", memoryId],
        httpPath: `/v1/memory/${encodeURIComponent(memoryId)}`,
        parseJson: false, // memoryd show 当前输出 raw markdown
      });
    },

    /** memory_transcript 后端 —— 取所属 session 的完整 transcript */
    async transcript(memoryId) {
      if (!memoryId || typeof memoryId !== "string") {
        return { ok: false, error: "memory_id is required" };
      }
      return transport.invoke({
        subcommand: "transcript",
        argv: ["show", memoryId, "--transcript"],
        httpPath: `/v1/memory/${encodeURIComponent(memoryId)}/transcript`,
        parseJson: false,
      });
    },

    /** 异步 capture —— agent_end hook 用 */
    async capture({ content, source = "openclaw", session_id, cwd, type } = {}) {
      if (!content || typeof content !== "string") {
        return { ok: false, error: "content is required" };
      }
      const payload = {
        content,
        session_id: session_id || `openclaw-${Date.now()}`,
        cwd: cwd || process.cwd(),
        transcript_path: "",
        source,
        type,
      };
      return transport.invoke({
        subcommand: "capture",
        argv: ["capture", "--source", source],
        stdin: JSON.stringify(payload),
        httpPath: "/v1/capture",
        httpBody: payload,
        parseJson: false,
      });
    },

    /** profile.identity —— before_agent_start 用 */
    profile: {
      async identity({ max_chars = 300 } = {}) {
        return transport.invoke({
          subcommand: "profile",
          argv: ["show", "profile-identity", "--scope", "_internal"],
          httpPath: `/v1/profile/identity?max_chars=${max_chars}`,
          parseJson: false,
          truncate: max_chars,
        });
      },
    },

    /** kg.entities —— before_agent_start 用 */
    kg: {
      async entities({ top = 10, window_days = 30 } = {}) {
        return transport.invoke({
          subcommand: "kg-entities",
          argv: [
            "list",
            "--type", "entity",
            "--limit", String(top),
            "--json",
          ],
          httpPath: `/v1/kg/entities?top=${top}&window_days=${window_days}`,
          parseJson: true,
        });
      },
    },

    /** 最近 N 条 long-term —— before_agent_start 用 */
    async recent({ limit = 5, type = "long-term" } = {}) {
      return transport.invoke({
        subcommand: "recent",
        argv: [
          "list",
          "--type", type,
          "--limit", String(limit),
          "--json",
        ],
        httpPath: `/v1/recent?limit=${limit}&type=${encodeURIComponent(type)}`,
        parseJson: true,
      });
    },
  };
}

// ---------------------------------------------------------------------------
// CLI transport
// ---------------------------------------------------------------------------

function createCliTransport({ bin, timeoutMs, spawnImpl }) {
  return {
    async invoke({ argv, stdin = null, parseJson = false, truncate }) {
      try {
        const out = await runCli(spawnImpl, bin, argv, { stdin, timeoutMs });
        if (out.code !== 0) {
          return {
            ok: false,
            error: `memoryd ${argv[0]} exited ${out.code}: ${out.stderr.trim()}`,
            stdout: out.stdout,
          };
        }
        let data = out.stdout;
        if (typeof truncate === "number" && data.length > truncate) {
          data = data.slice(0, truncate) + "...";
        }
        if (parseJson) {
          try {
            return { ok: true, data: JSON.parse(data || "[]") };
          } catch (e) {
            return { ok: false, error: `JSON parse failed: ${e.message}`, stdout: data };
          }
        }
        return { ok: true, data };
      } catch (e) {
        return { ok: false, error: e?.message || String(e) };
      }
    },
  };
}

function runCli(spawnImpl, bin, argv, { stdin = null, timeoutMs = 15000 } = {}) {
  return new Promise((resolve, reject) => {
    let child;
    try {
      child = spawnImpl(bin, argv, { stdio: ["pipe", "pipe", "pipe"] });
    } catch (e) {
      reject(e);
      return;
    }
    let stdout = "";
    let stderr = "";
    let settled = false;
    const finish = (result, error) => {
      if (settled) return;
      settled = true;
      clearTimeout(t);
      if (error) reject(error); else resolve(result);
    };
    const t = setTimeout(() => {
      try { child.kill("SIGTERM"); } catch (_) { /* ignore */ }
      finish(null, new Error(`memoryd ${argv[0]} timeout after ${timeoutMs}ms`));
    }, timeoutMs);
    child.stdout?.on("data", (c) => { stdout += c.toString(); });
    child.stderr?.on("data", (c) => { stderr += c.toString(); });
    child.on("error", (err) => finish(null, err));
    child.on("close", (code) => finish({ code, stdout, stderr }));
    if (stdin && child.stdin) {
      child.stdin.write(stdin);
      child.stdin.end();
    } else if (child.stdin) {
      child.stdin.end();
    }
  });
}

// ---------------------------------------------------------------------------
// HTTP transport
// ---------------------------------------------------------------------------

function createHttpTransport({ host, port, timeoutMs, fetchImpl }) {
  return {
    async invoke({ httpPath, httpBody, parseJson = false, truncate }) {
      try {
        const url = `http://${host}:${port}${httpPath}`;
        const method = httpBody ? "POST" : "GET";
        const res = await fetchImpl({
          url,
          method,
          body: httpBody ? JSON.stringify(httpBody) : null,
          timeoutMs,
        });
        if (res.statusCode >= 400) {
          return { ok: false, error: `HTTP ${res.statusCode}: ${res.body}` };
        }
        let data = res.body;
        if (typeof truncate === "number" && data.length > truncate) {
          data = data.slice(0, truncate) + "...";
        }
        if (parseJson) {
          try {
            return { ok: true, data: JSON.parse(data || "[]") };
          } catch (e) {
            return { ok: false, error: `JSON parse failed: ${e.message}`, body: data };
          }
        }
        return { ok: true, data };
      } catch (e) {
        return { ok: false, error: e?.message || String(e) };
      }
    },
  };
}

function defaultHttpFetch({ url, method = "GET", body = null, timeoutMs = 15000 } = {}) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const req = httpRequest({
      hostname: u.hostname,
      port: u.port,
      path: u.pathname + u.search,
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      timeout: timeoutMs,
    }, (res) => {
      let buf = "";
      res.setEncoding("utf-8");
      res.on("data", (c) => { buf += c; });
      res.on("end", () => resolve({ statusCode: res.statusCode || 0, body: buf }));
    });
    req.on("timeout", () => req.destroy(new Error(`http request timeout ${timeoutMs}ms`)));
    req.on("error", reject);
    if (body) req.write(body);
    req.end();
  });
}

// ---------------------------------------------------------------------------
// Convenience: 兜底文件系统读取最近的 long-term .md（HTTP / CLI 都不可用时）
// ---------------------------------------------------------------------------

/**
 * 当 memoryd 不可达时的 zero-dependency 兜底——直接扫数据目录拿最近 N 条
 * long-term 的标题。仅用于 before_agent_start 的"宁可不准也别空"语义。
 */
export function fallbackRecentSlugs({ dataRoot, limit = 5 } = {}) {
  try {
    const fs = require("node:fs"); // 延迟引用，保持 ESM 兼容
    const path = require("node:path");
    const root = dataRoot || join(homedir(), ".local", "share", "memoryd");
    const scopesDir = path.join(root, "scopes");
    if (!fs.existsSync(scopesDir)) return [];
    const slugs = [];
    for (const sd of fs.readdirSync(scopesDir)) {
      const dir = path.join(scopesDir, sd, "long-term");
      if (!fs.existsSync(dir)) continue;
      for (const f of fs.readdirSync(dir)) {
        if (f.endsWith(".md")) {
          slugs.push({
            slug: f.replace(/\.md$/, ""),
            scope_hash: sd,
            mtime: fs.statSync(path.join(dir, f)).mtimeMs,
          });
        }
      }
    }
    slugs.sort((a, b) => b.mtime - a.mtime);
    return slugs.slice(0, limit);
  } catch (_) {
    return [];
  }
}
