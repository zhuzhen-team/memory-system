---
title: Web 仪表板
keywords: Web Dashboard, FastAPI, HTMX, Cytoscape, 路由
---

# Web 仪表板：本地浏览界面

memoryd 启动一个本地 FastAPI 服务（绑 127.0.0.1），提供浏览-only 的 Web 界面。
**不可编辑**——编辑走 CLI 或直接改 markdown。

## 启动

```bash
memoryd web                       # 随机端口；stderr 输出 token URL
memoryd web --port=8088
memoryd web --no-browser          # CI / SSH 场景
```

启动后 stderr 出一行：

```
memoryd web on http://127.0.0.1:<port>/?token=<256-bit-token>
```

把整个 URL（含 `?token=`）复制到浏览器。token 每次重启变。

## 安全模型

- 绑定 `127.0.0.1`（loopback；非 0.0.0.0）
- 256-bit token 每次启动生成；不持久化
- 三种 token 携带方式都接受：
  - query string `?token=<x>`
  - cookie `memoryd_token=<x>`
  - `Authorization: Bearer <x>`
- 拒绝时返回 401 JSON；无登录页
- 不支持 HTTPS（本机；v2 视需要加）
- 敏感 scope：list 显 🔒 占位；detail 直接 403；search 排除其内容

## 路由清单

源码：[memoryd/src/memoryd/web/routes.py](https://github.com/zhuzhen-team/memory-system/blob/main/memoryd/src/memoryd/web/routes.py)

### 浏览（HTML）

| 路径 | 用途 |
|---|---|
| `/` | 仪表板首页：最近 20 条记忆 + 统计 + 各页面跳转 |
| `/memories?type=&scope=&page=` | 列表 |
| `/memories/{slug}` | 详情；sensitive 一律 403 |
| `/search?q=` | 全文搜索结果页（HTMX 完整刷新版） |
| `/htmx/memory-list?...` | HTMX 局部刷新片段 |
| `/audit?scope=&since=&event_type=` | 审计日志表格 |
| `/digest` | pending promotions 列表（read-only） |
| `/relations` | 知识图谱（Cytoscape.js 全图） |
| `/relations/entity/{entity_id}` | 单实体 N-hop 子图视图 |
| `/trends?window=7d` | 趋势页：top trigger + entity 上升 + recall hot |
| `/identity` | 当前 identity.md + 最近版本号 + 月度报告列表 |
| `/identity/version/{n}` | 第 n 版历史快照 |
| `/identity/diff?from=&to=` | 两版 diff |

### API（JSON / Markdown）

| 路径 | 用途 |
|---|---|
| `/api/graph/global` | 全图 cytoscape elements JSON |
| `/api/graph/{entity_id}` | 单实体 N-hop 子图 JSON |
| `/api/trends/triggers?window=7d` | trigger 频次 |
| `/api/trends/entities?window=30d` | entity 上升榜 |
| `/api/identity/report/{period}` | 月度报告 markdown 原文（PlainTextResponse） |

### 探活

| 路径 | 用途 |
|---|---|
| `/healthz` | 公共探活，不需 token |

## 页面截图

!!! note "截图待补"
    截图集中放在 `docs/assets/screenshots/`，本节后续补齐。

## 模板

- 模板目录：[memoryd/src/memoryd/web/templates/](https://github.com/zhuzhen-team/memory-system/tree/main/memoryd/src/memoryd/web/templates)
- 静态资源：[memoryd/src/memoryd/web/static/](https://github.com/zhuzhen-team/memory-system/tree/main/memoryd/src/memoryd/web/static)

模板引擎：Jinja2。CSS 走原生（轻量），交互走 HTMX；知识图谱页用 Cytoscape.js（外链 CDN）。

## 降级行为

任何 API 路由如果上游模块未就绪（KG 没建表 / Profile 没数据 / 缺包）返回**友好降级**而非 500。

例如 `/relations` 在没有 entities 时显示"还没有抽出的实体"，让 Web 端的"可观察性优先"。

## 启动配置

```python
# memoryd/web/server.py
def run(port: int | None = None, open_browser: bool = True) -> int:
    ...
```

- `port=None` → 随机选 8001-9999 区间空闲端口
- `open_browser=True` → 调系统默认浏览器打开 token URL（默认）
- 绑 host 永远是 127.0.0.1，不可改

## 设计权衡

- **浏览-only**。编辑走 CLI / 直接改 markdown（避免做"半 CMS"）
- **不持久化 token**。每次 `memoryd web` 重启都换；丢了就重启
- **不支持 HTTPS / 多用户**。本机单用户场景，加密走 OS keyring 已经够
- **没有 SPA framework**。HTMX + Jinja2 已经能干完所有事，省掉 npm 链
