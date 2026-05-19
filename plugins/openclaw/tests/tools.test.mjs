import { test } from "node:test";
import assert from "node:assert/strict";
import { buildTools, registerTools } from "../src/tools/index.js";
import { buildMemorySearchTool } from "../src/tools/memory_search.js";
import { buildMemoryGetTool } from "../src/tools/memory_get.js";
import { buildMemoryTranscriptTool } from "../src/tools/memory_transcript.js";

/**
 * 一个最小可用的 mock client。每个方法返回 { ok, data }，
 * 也可以在初始化时给某个方法塞 error 模拟失败。
 */
function makeMockClient(overrides = {}) {
  const calls = [];
  const default_ = {
    search: async (args) => {
      calls.push(["search", args]);
      return {
        ok: true,
        data: [
          {
            slug: "2026-05-19-react-solid",
            excerpt: "abble 把 wolin admin 切到 Solid",
            scope_hash: "abc123",
            created_at: "2026-05-19T10:00:00Z",
            dura_score: 0.87,
            source: "claude-code",
          },
        ],
      };
    },
    get: async (id) => {
      calls.push(["get", id]);
      return { ok: true, data: `---\nslug: ${id}\n---\n\n# body\n` };
    },
    transcript: async (id) => {
      calls.push(["transcript", id]);
      return { ok: true, data: "[Human]: hi\n[Assistant]: hello" };
    },
  };
  return {
    client: { ...default_, ...overrides },
    calls,
  };
}

test("buildTools returns exactly 3 tools with expected names", () => {
  const { client } = makeMockClient();
  const tools = buildTools({ client });
  assert.equal(tools.length, 3);
  assert.deepEqual(
    tools.map((t) => t.name).sort(),
    ["memory_get", "memory_search", "memory_transcript"]
  );
});

test("each tool has required inputSchema with required fields", () => {
  const { client } = makeMockClient();
  const [s, g, t] = buildTools({ client });
  for (const tool of [s, g, t]) {
    assert.equal(tool.inputSchema.type, "object");
    assert.ok(Array.isArray(tool.inputSchema.required));
    assert.ok(tool.inputSchema.required.length >= 1);
  }
  assert.deepEqual(s.inputSchema.required, ["query"]);
  assert.deepEqual(g.inputSchema.required, ["memory_id"]);
  assert.deepEqual(t.inputSchema.required, ["memory_id"]);
});

test("memory_search execute returns content array, calls client.search", async () => {
  const { client, calls } = makeMockClient();
  const tool = buildMemorySearchTool({ client });
  const res = await tool.execute("call-1", { query: "react solid", top_k: 3 });
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], "search");
  assert.equal(calls[0][1].query, "react solid");
  assert.equal(calls[0][1].top_k, 3);
  assert.ok(Array.isArray(res.content));
  const payload = JSON.parse(res.content[0].text);
  assert.equal(payload.length, 1);
  assert.equal(payload[0].memory_id, "2026-05-19-react-solid");
  assert.equal(payload[0].scope, "abc123");
  assert.equal(payload[0].dura_score, 0.87);
});

test("memory_search defaults: top_k=10, scope=auto", async () => {
  const { client, calls } = makeMockClient();
  const tool = buildMemorySearchTool({ client });
  await tool.execute("c", { query: "x" });
  assert.equal(calls[0][1].top_k, 10);
  assert.equal(calls[0][1].scope, "auto");
});

test("memory_search returns error payload on client failure", async () => {
  const { client } = makeMockClient({
    search: async () => ({ ok: false, error: "memoryd timeout" }),
  });
  const tool = buildMemorySearchTool({ client, logger: { warn: () => {} } });
  const res = await tool.execute("c", { query: "q" });
  const payload = JSON.parse(res.content[0].text);
  assert.equal(payload.error, "memoryd timeout");
  assert.deepEqual(payload.results, []);
});

test("memory_get returns raw markdown from client", async () => {
  const { client, calls } = makeMockClient();
  const tool = buildMemoryGetTool({ client });
  const res = await tool.execute("c", { memory_id: "2026-05-19-x" });
  assert.equal(calls[0][0], "get");
  assert.equal(calls[0][1], "2026-05-19-x");
  assert.ok(res.content[0].text.includes("slug: 2026-05-19-x"));
});

test("memory_transcript returns empty marker on empty transcript", async () => {
  const { client } = makeMockClient({
    transcript: async () => ({ ok: true, data: "   \n  " }),
  });
  const tool = buildMemoryTranscriptTool({ client });
  const res = await tool.execute("c", { memory_id: "m" });
  assert.ok(res.content[0].text.includes("transcript empty"));
});

test("memory_transcript returns failure message on client error", async () => {
  const { client } = makeMockClient({
    transcript: async () => ({ ok: false, error: "no path" }),
  });
  const tool = buildMemoryTranscriptTool({ client, logger: { warn: () => {} } });
  const res = await tool.execute("c", { memory_id: "m" });
  assert.ok(res.content[0].text.includes("transcript unavailable"));
});

test("registerTools wires 3 tools onto api.registerTool", () => {
  const registered = [];
  const api = {
    registerTool(factory, opts) {
      registered.push({ factory, opts });
    },
  };
  const { client } = makeMockClient();
  registerTools(api, { client });
  assert.equal(registered.length, 3);
  // factories should be functions that produce tool definitions
  for (const r of registered) {
    const tool = r.factory({});
    assert.equal(typeof tool.execute, "function");
    assert.ok(["memory_search", "memory_get", "memory_transcript"].includes(tool.name));
    assert.equal(r.opts.name, tool.name);
  }
});

test("registerTools throws if api lacks registerTool", () => {
  const { client } = makeMockClient();
  assert.throws(() => registerTools({}, { client }), /registerTool/);
});
