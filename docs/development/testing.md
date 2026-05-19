---
title: 测试
keywords: 测试, pytest, fixtures, 覆盖率
---

# 测试：跑测试 / fixtures / 覆盖率

## 跑全部测试

```bash
cd ~/memory-system/memoryd
uv run pytest -v
```

约 370+ 测试，全通过。

## 跑某个文件

```bash
uv run pytest tests/test_schema.py -v
uv run pytest tests/test_knowledge_graph_extract.py -v
uv run pytest tests/test_mcp_server.py -v
```

## 跑某个测试

```bash
uv run pytest tests/test_schema.py::test_frontmatter_roundtrip -v
```

## 异步测试

`pyproject.toml` 已设：

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

所有 `async def test_*` 自动以异步模式跑。

## 测试目录结构

```
memoryd/tests/
├── conftest.py                       # 共享 fixtures
├── test_schema.py                    # frontmatter
├── test_scope.py                     # scope_hash 派生
├── test_storage.py                   # markdown 读写
├── test_search.py                    # ripgrep
├── test_search_hybrid.py             # 向量 + RRF
├── test_search_scoring.py            # BM25 / 实体加权
├── test_chunking.py
├── test_embeddings_*.py
├── test_llm_*.py
├── test_knowledge_graph_*.py
├── test_profile_*.py
├── test_sync_memories_json*.py
├── test_sync_conflict.py
├── test_mcp_server.py
├── test_mcp_tools_*.py
├── test_governance_*.py
├── test_importers_*.py
├── test_mirror_*.py
├── test_setup_*.py
├── test_cli.py
├── test_web_*.py
└── test_tui_digest.py
```

## 关键 fixtures（conftest.py）

| fixture | 作用 |
|---|---|
| `tmp_data_root` | 临时 `~/.local/share/memoryd/` 替代品 |
| `tmp_scope` | 临时 scope_hash + 文件结构 |
| `tmp_config` | 隔离的 `~/.config/memoryd/config.toml` |
| `index_conn` | 临时 SQLite + migrations 已跑 |
| `fake_llm_provider` | 走预设回复的 LLMProvider mock |
| `fake_embedder` | 返回随机向量的 Embedder mock |
| `kg_store_with_data` | 预填充 entities / relations 的 KnowledgeGraphStore |

## monkeypatch 模式

memoryd 测试普遍走 monkeypatch 隔离环境：

```python
def test_capture(tmp_data_root, monkeypatch):
    monkeypatch.setenv("MEMORYD_DATA_ROOT", str(tmp_data_root))
    ...
```

避免污染用户的真实数据根。

## 测试 LLM 调用

不打真实 API。所有 LLM 测试走：

```python
from memoryd.llm.base import LLMMessage, LLMProvider, JudgeResult

class FakeLLM(LLMProvider):
    async def generate_text(self, messages): return "..."
    async def generate_json(self, messages, schema): return schema(**{...})

monkeypatch.setattr("memoryd.llm.factory.get_llm", lambda: FakeLLM())
```

## 测试 MCP server

```python
from memoryd.mcp_server import build_server, list_tool_names

async def test_mcp_tool_count():
    mcp = build_server(include_admin=False)
    names = await list_tool_names(mcp)
    assert len(names) == 13
    assert "mem_save" in names
```

## 覆盖率（可选）

```bash
uv pip install pytest-cov
uv run pytest --cov=memoryd --cov-report=html
open htmlcov/index.html
```

## ruff lint

```bash
uv run ruff check src/
uv run ruff format src/   # 格式化
```

## 集成测试：单端流程

最小闭环：

```python
def test_capture_to_search(tmp_data_root):
    from memoryd.cli import capture_session
    from memoryd.search import search_sessions
    capture_session({
        "session_id": "test-1",
        "transcript_path": "",
        "cwd": str(tmp_data_root)
    })
    hits = search_sessions("test", scope_hash=...)
    assert len(hits) == 1
```

## 测试运行环境

CI（GitHub Actions）跑 Python 3.11 / 3.12。本地建议 3.12。

依赖标记：

- `pytest.mark.requires_llm` — 需要真实 API key（默认 skip）
- `pytest.mark.requires_milvus` — 需要 milvus-lite 可用
- `pytest.mark.slow` — 慢测试，CI 跑全集

## 加新测试

约定：

1. 测试文件名对应被测模块（`test_foo.py` ↔ `foo.py`）
2. 测试函数名描述行为（`test_extract_entities_returns_empty_when_llm_unavailable`）
3. 优先用现成 fixture，避免每个测试自己造数据
4. 不打真实 API / 真实文件系统外部 path（用 monkeypatch + tmp_path）

## 故障排查

| 现象 | 排查 |
|---|---|
| `ImportError` jieba/onnxruntime | `uv pip install -e ".[dev]"` 重新安装 |
| `LLMUnavailable` | 测试应该用 fake LLM，不该真调；检查 monkeypatch |
| `Milvus Lite cannot init` | Windows 上跳过这部分测试（`pytest.mark.skipif`） |
| `keyring backend not available` | Linux CI 可能没有 secret-service；mock keyring |
