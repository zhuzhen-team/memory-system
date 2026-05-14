---
title: 长期记忆完整治理（Plan 3）设计
date: 2026-05-14
status: 已批准（light brainstorming 完成；digest 每周 / TTL 90 天）
related:
  - docs/superpowers/specs/2026-05-09-personal-usage-and-boundary-spec.md
  - docs/superpowers/specs/2026-05-14-tri-client-capture-fix-design.md
role: 设计文档——Plan 3 实施 plan 与 SDD 都引用本文档
---

# Plan 3：长期记忆完整治理设计

## 0. 这份文档是什么

Plan 1 + Plan 2 + Plan 2.5 完成了 capture 层：三端会话结束后自动落 Markdown，单一 `search_memory` MCP 工具，5 个 source tag 区分通路。但用户契约 spec §3-§4 还要求：**类型治理**（decision / preference / fact / playbook / warning）、**4 准则候选筛选**、**LLM 驱动摘要**、**TTL + decay + soft-forget**、**周期 digest 复盘**、**SQLite 索引**（Markdown 仍 source of truth）。Plan 3 补这一整套。

Plan 3 不动 spec（user contract）；不动 Plan 2.5 的 capture 通路；扩展 schema、storage 层、search 层、MCP 工具集；新增 LLM provider abstraction 和 governance jobs。

## 1. 上游与硬约束（已交付，不要破）

| 已交付（不动） | 状态 |
|---|---|
| Plan 1 schema: `SessionMemory(frontmatter, body)`, `Frontmatter` Pydantic 模型 | merged `2e7fb25` |
| Plan 1 storage: `save_session` / `load_session` / `list_sessions` 按 scope_hash 目录组织 | merged `2e7fb25` |
| Plan 1 search: ripgrep-based `search_sessions` | merged `2e7fb25` |
| Plan 1 MCP server: `search_memory` 工具（1/12 used） | merged `2e7fb25` |
| Plan 2 + 2.5 capture：CC SessionEnd hook / Codex notify wrapper + rollout FS-watch / OpenClaw SDK plugin + sessions FS-watch | merged `5a61f43` + `b140b35` |
| 5 个 source tag：`claude-code` / `codex-notify` / `codex-rollout` / `openclaw` / `openclaw-fs` | merged `b140b35` |

| 硬约束 | 来源 |
|---|---|
| MCP 工具 ≤ 12 个（目前 1 used） | spec §3 |
| Markdown 是 source of truth；SQLite 只索引、可重建 | spec §3 / detailed-plan |
| 不双向同步 CLAUDE.md / AGENTS.md / auto-memory（只单向 import → Plan 8） | spec §6 / §8 |
| 不接管三端原生记忆机制 | spec §8 |
| 默认静默——不在 SessionStart 主动注入 | spec §3 |
| 全本地——绝不联网（LLM API key 由用户主动配置算"主动调外部模型"，spec §4.7 #24 允许） | spec §3 / §4.7 |

## 2. 总体架构

```
┌──────────────────────────────────────────────────────────────────────┐
│ Markdown source of truth（不变，只扩展类型目录）                      │
│   ~/.local/share/memoryd/scopes/<scope_hash>/                        │
│     sessions/<slug>.md        ← Plan 1-2.5 5 个 source 都落这里      │
│     decisions/<slug>.md       ← NEW Plan 3                          │
│     preferences/<slug>.md     ← NEW Plan 3                          │
│     facts/<slug>.md           ← NEW Plan 3                          │
│     playbooks/<slug>.md       ← NEW Plan 3                          │
│     warnings/<slug>.md        ← NEW Plan 3                          │
│   forgotten/<slug>.md         ← soft-forgotten 兜底（迁出 scope）   │
└──────────────────────────────────────────────────────────────────────┘
                              ↓                       ↑
                              ↓ index on save         ↑ rebuild on demand
                              ↓                       ↑
┌──────────────────────────────────────────────────────────────────────┐
│ SQLite index（可重建；不进同步盘——Plan 6）                            │
│   ~/.local/share/memoryd/index.db                                    │
│     memories(slug PK, type, scope_hash, ttl_days, decay_state,       │
│              last_recalled_at, recall_count, fingerprint, body_path) │
│     triggers(slug FK, trigger)                                       │
│     promotions(id PK, source_session_slug, proposed_*, status)       │
└──────────────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────────────┐
│ LLM layer（pluggable provider）                                       │
│   anthropic / openai / openrouter / local（ollama）                   │
│   走 ANTHROPIC_API_KEY/OPENAI_API_KEY 等 env；尊重 HTTPS_PROXY        │
│   默认 anthropic + claude-haiku-4-5（便宜快，4 准则评分够用）         │
└──────────────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────────────┐
│ Governance jobs                                                       │
│   - session-end 后台 fork `memoryd analyze-session <slug>` →          │
│     LLM 跑 DURA → 写 promotions(pending)                              │
│   - launchd cron job `memoryd decay-sweep`（每天 03:00）→             │
│     更新 decay_state、迁 soft-forgotten 到 forgotten/                 │
│   - digest TUI（每周一上午 9:00 桌面通知）→                           │
│     用户批量 approve/reject pending promotions + 重复合并 + TTL 到期  │
└──────────────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────────────┐
│ MCP server（7/12 used after Plan 3）                                  │
│   1. search_memory（扩展加 type / include_decayed 参数）              │
│   2. promote_to_long_term（智能体主动提升 session 段）                │
│   3. record_long_term（智能体在对话中直接记长期记忆）                 │
│   4. list_by_type                                                     │
│   5. get_memory                                                       │
│   6. list_promotions                                                  │
│   7. merge_duplicates                                                 │
│ 预算剩余：5（Plan 4 sensitive 占 1，Plan 8 memory-searcher 占 0）     │
└──────────────────────────────────────────────────────────────────────┘
```

## 3. 类型扩展 schema

`MemoryType` Literal 从 Plan 1 的 `"session"` 扩展为 6 个：

```python
MemoryType = Literal["session", "decision", "preference", "fact", "playbook", "warning"]
```

类型语义（来自 spec §3 / 4 月研究 §4.2）：

| 类型 | 语义 | TTL 默认 |
|---|---|---|
| `session` | 自动捕获的会话摘要（Plan 1-2.5） | 90 天 → decay → soft-forget |
| `decision` | 用户做的明确决策（如"logo 选深蓝+银灰"） | 不过期，仅 rarely-recalled 提示 |
| `preference` | 用户工作偏好（如"PR 用 merge 不用 squash"） | 不过期 |
| `fact` | 客观事实（如"项目数据库是 postgres 15"） | 不过期 |
| `playbook` | 操作流程（如"deploy 走 GitHub Actions main 分支"） | 不过期 |
| `warning` | 踩过的坑（如"不要直接 push main，CI 会跑两次"） | 不过期 |

Frontmatter 新增字段：

```yaml
---
title: ...
slug: ...
type: decision           # 6 种之一
scope_hash: ...
triggers: [...]
source: ...
created_at: ...
updated_at: ...

# Plan 3 新字段
promoted_from: 2026-05-13-monday-discussion  # 长期记忆引用其源 session slug
supersedes: [2026-04-30-old-logo-decision]   # 决策替代
ttl_days: 90                                 # session 默认 90；长期记忆默认 null
decay_state: alive                           # alive | dim | soft-forgotten
last_recalled_at: 2026-05-12T10:00:00+00:00  # 上次 search_memory 命中
recall_count: 3                              # 累计命中次数
relations: [2026-05-09-related-fact]         # 关联记忆 slug
dura_score:                                  # 仅长期记忆有；提升时 LLM 评分
  D: 0.85
  U: 0.92
  R: 0.78
  A: 0.95
---
```

Schema 兼容性：所有新字段都 optional（`Field(default=None)`），Plan 1-2.5 已存在的 `.md` 文件 load 不破坏。

## 4. 4 准则（DURA）LLM prompt template

每次 session 结束后台跑 `memoryd analyze-session <session-slug>`，调 LLM 提候选。Prompt template draft（写到 `memoryd/src/memoryd/prompts/dura_extract.txt`，可被 `memoryd config` 覆盖）：

```
你正在从一段 AI 会话里抽取值得长期记住的内容。

4 准则（DURA），每项给 0.0-1.0 分。只有 4 项都 ≥ 0.6 才推荐提升。

- **D**urability 持久性：3 个月后这条信息还有意义吗？
  - 高分例：用户对项目架构做的决策、明确的工作偏好
  - 低分例：临时调试日志、一次性 prompt 调整
- **U**niqueness 独特性：这条是否已经在现有记忆里？
  - 高分例：新决策、首次表达的偏好
  - 低分例：用户重述已有事实、低信息量的回应
- **R**etrievability 可检索性：用户能想出 ≥2 个触发词来找它吗？
  - 高分例：含具体技术名词 / 项目名 / 人名
  - 低分例：纯抽象描述、无独特关键词
- **A**uthority 权威性：是用户明确决策 / 事实，还是 AI 推断？
  - 高分例：用户说"决定 X"、"我喜欢 Y"
  - 低分例：AI 建议被用户忽略 / AI 推测用户偏好

输入：
- Session 正文（已截到 8000 字符内）：
  <<<
  {{session_body}}
  >>>
- Scope（项目根路径）：{{scope_root}}
- 该 scope 现有长期记忆 titles（避免重复推荐）：
  {{existing_titles}}

输出：仅 JSON，无解释，candidates 数组。candidates 元素 schema：
{
  "type": "decision" | "preference" | "fact" | "playbook" | "warning",
  "title": "<一行，≤100 字>",
  "body": "<markdown 正文，≤500 字>",
  "triggers": ["<关键词>", "<关键词>", ...],   // ≥ 2 个，用户场景里能想起来的
  "dura": { "D": 0.0-1.0, "U": 0.0-1.0, "R": 0.0-1.0, "A": 0.0-1.0 },
  "reasoning": "<一行说明为什么提升>",
  "supersedes": []   // 如果替代某条已有记忆，填其 slug
}

如果没有任何候选满足 4 项 ≥ 0.6，输出 `[]`。
绝不输出非 JSON 内容（包括 ```json fence）。
```

写到 promotions table（status=pending），digest TUI 让用户审批。

替代关系：LLM 若识别 supersedes，digest 时用户确认后 SQLite + Markdown 都打 supersedes 链。被取代条目 decay_state → `dim` 立刻；30 天后 → `soft-forgotten`。

## 5. TTL + decay + soft-forget 状态机

```
                    ttl_days 到期 +
                    last_recalled_at 距今 > ttl_days
                    ─────────────────────────────────►
            ┌───────────┐                       ┌──────────┐
   default  │   alive   │                       │   dim    │
   ─────────►           │                       │          │
            │ search 命 │                       │ search 仍│
            │ 中正常返回│                       │ 出，rank │
            │           │                       │ 降低     │
            └───────────┘                       └────┬─────┘
                  ▲                                  │ 再 30 天
                  │ search 命中                      │ 没命中
                  │ 重置 last_recalled_at            ▼
                  │                            ┌──────────────┐
                  │                            │ soft-forgotten│
                  │                            │              │
                  └─── 用户在 digest "recall" ─┤ search 默认  │
                       手动恢复                 │ 不返回；need │
                                                │ include_decay│
                                                │ ed=true 才出 │
                                                └─────┬────────┘
                                                      │ 再 90 天
                                                      │ 没命中
                                                      ▼
                                       move to forgotten/ subdir
                                       SQLite index 不动；body 路径迁移
                                       用户 digest 可手动恢复或永久删除
```

`memoryd decay-sweep` 每天 03:00 跑一次（launchd plist，Plan 3 加新条目；和 mirror daemon 同 plist 文件）：

- `alive` + `created_at + ttl_days < now` + `last_recalled_at` 距今 > ttl_days → `dim`
- `dim` + 30 天没命中 → `soft-forgotten`
- `soft-forgotten` + 90 天没命中 → 物理迁移 .md 到 `<scope_hash>/forgotten/`

长期记忆 `ttl_days=null`：永不进 dim/soft-forgotten。但 `last_recalled_at > 180 天` 触发 digest 里的 "rarely-recalled" 提示让用户决定。

## 6. 周期 digest

每周一上午 9:00：

- launchd 跑 `memoryd digest --notify`
- 桌面通知（macOS `osascript -e 'display notification ...'`）
- 用户在 terminal 跑 `memoryd digest` 进 TUI

TUI 三栏（参考 engram 设计）：

```
┌─ memoryd weekly digest ──────────────────────────────────────┐
│                                                              │
│ 候选提升 (12 待审)         │ 重复合并 (3 对)                  │
│ ─────────────────────      │ ─────────                        │
│ ▸ [decision] logo 方向 ... │ ▸ logo-blue-2026-04 ~            │
│   D=0.85 U=0.91 R=0.80    │   logo-blue-2026-05 (dup 0.92)   │
│   A=0.95  source: openclaw│                                  │
│ ▸ [preference] PR 用 m... │                                  │
│ ▸ [fact] db = postgres 15 │                                  │
│ ...                       │                                  │
│                                                              │
│ TTL / decay 提醒 (5 条)                                      │
│ ───────────                                                  │
│ ▸ [session] 2026-02-12 ... → 进 dim (180 天没召回)          │
│ ▸ [decision] 2026-03-01 ... → rarely-recalled 提示          │
│                                                              │
│ 操作: [a]ll-approve [r]eject [m]erge [s]kip [q]uit          │
└──────────────────────────────────────────────────────────────┘
```

`memoryd digest` 也支持 `--non-interactive --output json` 给脚本用（spec §3 没明说，但 audit / 测试方便）。

## 7. 新增 MCP 工具（共 7 / 12 used after Plan 3）

| # | 工具 | 签名 | 用途 |
|---|---|---|---|
| 1 | `search_memory` | `(query, scope_hash?, type?, include_decayed=false)` | Plan 1 既有，加 type / include_decayed |
| 2 | `promote_to_long_term` | `(session_slug, type, title?, body?, triggers?, reason)` | 智能体在会话中说"这条记下来"——直接提升而不等 digest |
| 3 | `record_long_term` | `(type, title, body, triggers, scope_hash?, supersedes?)` | 智能体写一条全新长期记忆（不是 session 提升来） |
| 4 | `list_by_type` | `(type, scope_hash?, limit=20)` | 智能体想看 scope 的所有 decisions 等 |
| 5 | `get_memory` | `(slug)` | 智能体取单条详情（不只是 search excerpt） |
| 6 | `list_promotions` | `(scope_hash?, status?)` | 列待审批 / 已批准 / 已拒绝 |
| 7 | `merge_duplicates` | `(keep_slug, drop_slugs[])` | 数字体在 digest TUI / CLI 之外也能合并 |

预算剩余 5 工具，留 Plan 4 (`request_sensitive_read` × 1) 和 Plan 6-8 余地。

## 8. Session-end LLM 调用流程

Plan 2.5 capture 通路在 `save_session` 之后立刻 fork：

```python
# memoryd/src/memoryd/cli.py capture_session 末尾
def capture_session(...) -> Path:
    ...
    path = save_session(memory_root, session)
    # NEW Plan 3: fork analyze
    _spawn_analyze(path)
    return path


def _spawn_analyze(session_md_path: Path) -> None:
    """Background spawn `memoryd analyze-session <path>`. Never blocks."""
    if not _llm_configured():
        return  # 用户没配 LLM API key → 静默跳过
    subprocess.Popen(
        [sys.executable, "-m", "memoryd", "analyze-session", str(session_md_path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
```

`memoryd analyze-session`：
1. load_session(path) → SessionMemory
2. 查现有 scope 长期记忆 titles（SQLite）
3. 拼 prompt → LLM call
4. 解析返回 JSON → 写 promotions table（pending）
5. 完成；不动 .md 文件

如果 LLM API 失败 / 超时 / 返回非 JSON → 重试一次，再失败 → 写 log + skip。绝不阻塞主进程。

## 9. LLM provider abstraction

`memoryd/src/memoryd/llm.py`：

```python
class LLMProvider(Protocol):
    def complete(self, *, system: str, user: str, model: str | None = None) -> str: ...


class AnthropicProvider:
    """Uses anthropic SDK; respects HTTPS_PROXY env."""
    ...


class OpenAIProvider:
    """Uses openai SDK; respects HTTPS_PROXY env."""
    ...


class OpenRouterProvider:
    """OpenAI-compatible base_url=openrouter.ai; supports many models."""
    ...


class LocalOllamaProvider:
    """Connects to http://localhost:11434; no internet."""
    ...


def get_provider() -> LLMProvider:
    """Read memoryd config; instantiate provider."""
    ...
```

配置走 `~/.config/memoryd/config.toml`：

```toml
[llm]
provider = "anthropic"            # anthropic | openai | openrouter | local
model = "claude-haiku-4-5"        # 默认 haiku（cheap + fast）
api_key_env = "ANTHROPIC_API_KEY" # 不存 API key 本身，存 env 名
```

`memoryd config set llm.provider openai` 改配置；测试通过 `memoryd config show`。

## 10. 文件结构

### 新建

```
memoryd/src/memoryd/
  types.py             # MemoryType Literal 扩展 + 类型语义文档
  index.py             # SQLite schema 定义 + connection 管理 + rebuild
  llm.py               # Provider Protocol + 4 个实现
  prompts/
    __init__.py
    dura_extract.txt   # 4 准则 prompt 模板
  governance/
    __init__.py
    analyze.py         # analyze-session 实现
    decay.py           # decay-sweep 实现（状态机迁移）
    digest.py          # digest 主逻辑（无 TUI）
    merge.py           # merge_duplicates 实现
  tui.py               # textual / rich-based digest TUI
  config.py            # 用户级 ~/.config/memoryd/config.toml 读写

memoryd/tests/
  test_types.py
  test_index.py
  test_llm.py
  test_governance_analyze.py
  test_governance_decay.py
  test_governance_digest.py
  test_governance_merge.py
  test_config.py
  test_tui.py           # 用 textual.testing 跑

memoryd/src/memoryd/migrations/
  001_initial_schema.sql

scripts/launchd/
  com.memoryd.decay-sweep.plist    # 每天 03:00
  com.memoryd.weekly-digest.plist  # 每周一 09:00 桌面通知
```

### 修改

```
memoryd/pyproject.toml              # 加 anthropic / openai / textual / rich 依赖
memoryd/src/memoryd/schema.py       # MemoryType 扩展 + Frontmatter 新字段
memoryd/src/memoryd/storage.py      # save_session 后 index；扩展 save_decision 等
memoryd/src/memoryd/search.py       # SQLite-backed search；ripgrep 仅 fallback
memoryd/src/memoryd/server.py       # 注册 6 新工具
memoryd/src/memoryd/cli.py          # 加 analyze-session / decay-sweep / digest /
                                    #   rebuild-index / config / move-scope 子命令
memoryd/src/memoryd/setup.py        # 加 install-decay-cron / install-digest-cron
memoryd/README.md                   # 大改：长期记忆使用文档
```

## 11. 不在 Plan 3 内（边界）

| 不做 | 推迟到 |
|---|---|
| 敏感作用域加密 / 授权对话 | Plan 4 |
| Windows / Linux daemon 等价物 | Plan 5 |
| 多电脑同步 | Plan 6 |
| Web Dashboard | Plan 7 |
| 旧记忆导入（claude-md / agents-md / mcp-memory-service） | Plan 8 |
| memory-searcher sub-agent 模板 | Plan 8 |
| 向量 / 语义搜索 | v2（spec §10） |
| Obsidian Local REST API 双向 | v2 |

## 12. 风险与回退

| 风险 | 触发 | 回退 |
|---|---|---|
| LLM API 失败 / 超时 | analyze-session 不可用 | 静默跳过，promotions table 不增；用户 session 仍正常存 |
| SQLite 损坏 | `.db` 文件被 lock / 截断 | `memoryd rebuild-index` 从 Markdown 重建（30 秒内） |
| LLM 提取出错（瞎编 supersedes 链） | digest 显示明显错误 | 用户 reject；reasoning 字段记录在 promotions，便于检视 |
| API key 走私 / 数据外发顾虑 | 用户在意 | 改 `memoryd config set llm.provider local`，跑本地 ollama；spec §3 数据观允许"主动调外部模型" |
| decay 误删活跃记忆 | 用户发现"那条 fact 找不到了" | `memoryd recall <slug>` 把它从 forgotten/ 拉回 alive；soft-forget 不物理删 |
| HTTPS_PROXY 配置（用户在墙内） | LLM API 直连失败 | LLM client 默认 read env `HTTPS_PROXY`；test 验证 |
| LLM 返回非 JSON | analyze-session 异常 | 重试一次（更严的"only JSON"prompt）；再失败 skip 并 log |
| 4 准则评分主观偏差 | 用户觉得提升候选质量差 | Prompt 模板存 .txt，用户可 `memoryd config set prompts.dura ~/my-custom-prompt.txt` 覆盖；spec 已留接口 |

## 13. 完成判据

下面任一未达成即未完成：

1. ✅ pytest 全绿（预期 61 + 新增 60+ ≈ 120+ passed）
2. ✅ MemoryType 6 种全部能 save/load roundtrip
3. ✅ SQLite index 在 save 后自动 sync；`memoryd rebuild-index` 从 Markdown 全量重建后数据一致
4. ✅ 至少一种 LLM provider（默认 anthropic）端到端：跑一个 sample session → analyze-session → promotions table 出现合理候选
5. ✅ `memoryd digest` TUI 能 approve / reject / merge / skip；批准的候选真的写出对应类型 .md 并 index
6. ✅ decay-sweep：人为把 last_recalled_at 拨回 100 天前的 session，跑 sweep 后 decay_state 变 dim；再拨 30 天，变 soft-forgotten
7. ✅ search_memory 默认排除 soft-forgotten；`include_decayed=true` 时返回
8. ✅ 6 个新 MCP 工具都在 CC `/mcp` 列表里出现，且都能在简单 fixture 下端到端调通
9. ✅ MCP 工具总数 ≤ 12（实际 7）
10. ✅ Plan 1-2.5 已有功能无回归（CC capture / Codex mirror / OpenClaw plugin / 5 个 source tag 都正常）
11. ✅ promote_to_long_term 在 CC 会话中说"记一下这个决策"能真的写出 decision .md

## 14. 变更记录

| 日期 | 改了什么 | 为什么 |
|---|---|---|
| 2026-05-14 | 初版 | Plan 2.5 capture 通路完成；上长期记忆治理层 |
