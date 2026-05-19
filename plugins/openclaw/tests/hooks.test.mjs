import { test } from "node:test";
import assert from "node:assert/strict";
import { buildBeforeAgentStartHook } from "../src/hooks/before_agent_start.js";
import {
  buildAgentEndHook,
  extractRecentTurns,
  naiveSummarize,
} from "../src/hooks/agent_end.js";

// ---------------------------------------------------------------------------
// before_agent_start
// ---------------------------------------------------------------------------

function fakeClient({ identity, entities, recent } = {}) {
  return {
    profile: { identity: async () => identity || { ok: false } },
    kg: { entities: async () => entities || { ok: false } },
    recent: async () => recent || { ok: false },
  };
}

test("before_agent_start returns {} when all backends fail", async () => {
  const hook = buildBeforeAgentStartHook({ client: fakeClient() });
  const res = await hook({});
  assert.deepEqual(res, {});
});

test("before_agent_start composes markdown with identity + entities + recent", async () => {
  const client = fakeClient({
    identity: { ok: true, data: "abble，36 岁，前端独立开发者，住南京。" },
    entities: { ok: true, data: [
      { name: "wolin-admin", score: 0.92, summary: "切 Solid 中" },
      { name: "zhuzhen-team", score: 0.71 },
    ]},
    recent: { ok: true, data: [
      { title: "2026-05-18 切 Solid 决策", slug: "2026-05-18-solid" },
      { slug: "2026-05-17-cron" },
    ]},
  });
  const hook = buildBeforeAgentStartHook({
    client,
    now: () => new Date("2026-05-19T12:00:00Z"),
  });
  const res = await hook({});
  assert.ok(res.additionalContext);
  const md = res.additionalContext;
  assert.ok(md.includes("## 与 abble 的最近上下文"));
  assert.ok(md.includes("### Identity 节选"));
  assert.ok(md.includes("abble，36 岁"));
  assert.ok(md.includes("### 最近 30 天 top 实体"));
  assert.ok(md.includes("wolin-admin"));
  assert.ok(md.includes("score=0.92"));
  assert.ok(md.includes("### 最近 5 条 long-term 记忆"));
  assert.ok(md.includes("2026-05-18 切 Solid 决策"));
});

test("before_agent_start truncates identity to 300 chars", async () => {
  const long = "x".repeat(800);
  const client = fakeClient({ identity: { ok: true, data: long } });
  const hook = buildBeforeAgentStartHook({ client });
  const res = await hook({});
  assert.ok(res.additionalContext);
  assert.ok(res.additionalContext.includes("..."));
  // 完整保留 800 个 x 不应该出现
  assert.equal(res.additionalContext.includes("x".repeat(800)), false);
});

test("before_agent_start swallows thrown client errors", async () => {
  const client = {
    profile: { identity: async () => { throw new Error("boom"); } },
    kg: { entities: async () => { throw new Error("boom"); } },
    recent: async () => { throw new Error("boom"); },
  };
  const hook = buildBeforeAgentStartHook({ client, logger: { warn: () => {}, info: () => {} } });
  const res = await hook({});
  assert.deepEqual(res, {});
});

// ---------------------------------------------------------------------------
// agent_end helpers
// ---------------------------------------------------------------------------

test("extractRecentTurns handles string and array content", () => {
  const text = extractRecentTurns([
    { role: "user", content: "前面的轮，应被裁掉" },
    { role: "assistant", content: "前面的回，应被裁掉" },
    { role: "user", content: "切到 Solid 吗？" },
    { role: "assistant", content: [{ type: "text", text: "建议先小流量灰度" }] },
  ], { maxTurns: 1 });
  assert.ok(text.includes("[Human]: 切到 Solid 吗"));
  assert.ok(text.includes("[Assistant]: 建议先小流量灰度"));
  assert.equal(text.includes("前面的轮"), false);
});

test("extractRecentTurns returns empty on empty input", () => {
  assert.equal(extractRecentTurns([]), "");
  assert.equal(extractRecentTurns(null), "");
});

test("naiveSummarize truncates long text with ellipsis", () => {
  const s = "a".repeat(2000);
  const out = naiveSummarize(s, { maxChars: 100 });
  assert.equal(out.endsWith("\n..."), true);
  assert.ok(out.length <= 105);
});

// ---------------------------------------------------------------------------
// agent_end hook
// ---------------------------------------------------------------------------

function makeCaptureSpy() {
  const calls = [];
  return {
    spy: calls,
    client: {
      capture: async (args) => {
        calls.push(args);
        return { ok: true, data: "captured" };
      },
    },
  };
}

async function tick() {
  // 让 microtasks 跑完——fire-and-forget 链路涉及两次 await，需要多次让步
  for (let i = 0; i < 5; i++) {
    await new Promise((r) => setImmediate(r));
  }
}

test("agent_end calls summarize + capture when there is enough conversation", async () => {
  const { spy, client } = makeCaptureSpy();
  const summarized = [];
  const hook = buildAgentEndHook({
    client,
    summarize: async (txt) => {
      summarized.push(txt);
      return "三人称要点：abble 决定切 Solid。";
    },
    logger: { info: () => {}, warn: () => {} },
  });
  await hook({
    sessionId: "ow-1",
    cwd: "/tmp/proj",
    messages: [
      { role: "user", content: "切到 Solid 的话，admin 表格组件要重写多少？" },
      { role: "assistant", content: "大约 60% 表格组件需要适配 Solid signal 模型。" },
    ],
  });
  await tick();
  assert.equal(summarized.length, 1);
  assert.ok(summarized[0].includes("[Human]:"));
  assert.equal(spy.length, 1);
  assert.equal(spy[0].source, "openclaw");
  assert.equal(spy[0].session_id, "ow-1");
  assert.equal(spy[0].cwd, "/tmp/proj");
  assert.ok(spy[0].content.includes("三人称要点"));
});

test("agent_end skips when turn text is too short", async () => {
  const { spy, client } = makeCaptureSpy();
  const hook = buildAgentEndHook({
    client,
    summarize: async () => "should-not-be-called",
    logger: { info: () => {}, warn: () => {} },
  });
  await hook({ messages: [{ role: "user", content: "hi" }] });
  await tick();
  assert.equal(spy.length, 0);
});

test("agent_end falls back to naive summarize when LLM throws", async () => {
  const { spy, client } = makeCaptureSpy();
  const hook = buildAgentEndHook({
    client,
    summarize: async () => { throw new Error("llm down"); },
    logger: { info: () => {}, warn: () => {} },
  });
  await hook({
    messages: [
      { role: "user", content: "abble 想知道 Solid 切换的回滚成本" },
      { role: "assistant", content: "回滚成本主要是 admin 表单组件，估算 3 人天。" },
    ],
  });
  await tick();
  assert.equal(spy.length, 1);
  assert.ok(spy[0].content.length > 0);
});

test("agent_end honors autoCapture=false", async () => {
  const { spy, client } = makeCaptureSpy();
  const hook = buildAgentEndHook({
    client,
    autoCapture: false,
    summarize: async () => "x",
    logger: { info: () => {}, warn: () => {} },
  });
  await hook({
    messages: [
      { role: "user", content: "这条不应被 capture" },
      { role: "assistant", content: "也不应被 capture" },
    ],
  });
  await tick();
  assert.equal(spy.length, 0);
});

test("agent_end swallows capture failure (fire-and-forget)", async () => {
  const client = {
    capture: async () => ({ ok: false, error: "memoryd 进程崩了" }),
  };
  const warns = [];
  const hook = buildAgentEndHook({
    client,
    summarize: async () => "三人称要点：abble 想测试 capture 失败时不该抛。",
    logger: { info: () => {}, warn: (m) => warns.push(m) },
  });
  // 不应该抛
  await hook({
    messages: [
      { role: "user", content: "abble 想看 capture 失败的处理路径" },
      { role: "assistant", content: "answer 这条够长用来通过 MIN_TURN_CHARS 门槛" },
    ],
  });
  await tick();
  assert.ok(warns.some((m) => /capture failed/.test(m)),
    `expected a 'capture failed' warning, got: ${JSON.stringify(warns)}`);
});
