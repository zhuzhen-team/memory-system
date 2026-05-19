/* memoryd Plan 11 - Cytoscape.js loader (vanilla JS, no deps beyond cytoscape CDN)
 *
 * 责任：
 *   1. 从 #graph-filters 读过滤器
 *   2. 调 /api/graph/global 或 /api/graph/<focus> 获取 elements
 *   3. 渲染 + 点击节点 → 调 /api/graph/<id> 局部展开
 *   4. 节点点击同时把详情塞进 #entity-detail
 *
 * 设计约束：纯 vanilla，不依赖 jQuery / 任何 bundler；CDN 加载 cytoscape。
 */
(function () {
  "use strict";

  const TYPE_COLORS = {
    person: "#f0a3c8",
    organization: "#d4a72c",
    place: "#7ec07e",
    library: "#c297ff",
    tool: "#58a6ff",
    project: "#ffb976",
    concept: "#cdd1d7",
    unknown: "#999",
  };
  const TYPE_SHAPES = {
    person: "ellipse",
    organization: "round-rectangle",
    place: "round-diamond",
    library: "star",
    tool: "round-triangle",
    project: "round-octagon",
    concept: "ellipse",
  };
  const PREDICATE_COLORS = {
    mentions: "#9da7b3",
    supersedes: "#cf222e",
    superseded_by: "#cf222e",
    prefers: "#0969da",
    works_on: "#1a7f37",
    uses: "#8250df",
    conflicts_with: "#cf222e",
    cites: "#bf3989",
    runs_on: "#bc4c00",
    belongs_to: "#9a6700",
    located_at: "#0969da",
  };

  let cy = null;

  function waitForCytoscape(cb) {
    if (typeof cytoscape !== "undefined") {
      cb();
      return;
    }
    let waited = 0;
    const t = setInterval(function () {
      if (typeof cytoscape !== "undefined") {
        clearInterval(t);
        cb();
      } else if ((waited += 100) > 5000) {
        clearInterval(t);
        const cyEl = document.getElementById("cy");
        if (cyEl) {
          cyEl.innerHTML =
            '<p style="padding:1em;color:#888">Cytoscape.js 加载失败（CDN 不可达？）</p>';
        }
      }
    }, 100);
  }

  function token() {
    const filters = document.getElementById("graph-filters");
    return (filters && filters.dataset.token) || "";
  }

  function filterParams() {
    const form = document.getElementById("graph-filters");
    if (!form) return new URLSearchParams({ token: token() });
    const p = new URLSearchParams();
    Array.from(form.elements).forEach(function (el) {
      if (!el.name) return;
      if (el.value !== "" && el.value !== null) p.set(el.name, el.value);
    });
    p.set("token", token());
    return p;
  }

  async function fetchJson(url) {
    try {
      const r = await fetch(url, { credentials: "same-origin" });
      if (!r.ok) return { elements: [], available: false };
      return await r.json();
    } catch (e) {
      return { elements: [], available: false };
    }
  }

  function renderEntityDetail(data) {
    const panel = document.getElementById("entity-detail");
    if (!panel) return;
    if (!data || !data.entity) {
      panel.innerHTML = '<p class="placeholder">该实体已无数据。</p>';
      return;
    }
    const e = data.entity;
    panel.innerHTML =
      '<article class="entity-card-full">' +
      "<header>" +
      '<span class="badge badge--' + e.type + '">' + e.type + "</span>" +
      "<h3>" + escapeHtml(e.name) + "</h3>" +
      '<small class="muted">' + escapeHtml(e.id) + "</small>" +
      "</header>" +
      '<dl class="kv">' +
      "<dt>提及次数</dt><dd>" + (e.mention_count || 0) + "</dd>" +
      "<dt>状态</dt><dd>" + escapeHtml(e.decay_state || "fresh") + "</dd>" +
      "</dl>" +
      '<p><a class="link" href="/relations/entity/' +
      encodeURIComponent(e.id) +
      "?token=" + encodeURIComponent(token()) +
      '">聚焦此实体 →</a></p>' +
      "</article>";
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function styles() {
    return [
      {
        selector: "node",
        style: {
          "background-color": function (ele) {
            return TYPE_COLORS[ele.data("type")] || TYPE_COLORS.unknown;
          },
          shape: function (ele) {
            return TYPE_SHAPES[ele.data("type")] || "ellipse";
          },
          label: "data(label)",
          "text-valign": "bottom",
          "text-margin-y": 4,
          "font-size": 10,
          color: "#444",
          "text-outline-color": "#fff",
          "text-outline-width": 2,
          width: function (ele) {
            const c = ele.data("mention_count") || 1;
            return Math.max(20, Math.min(40, 16 + Math.sqrt(c) * 3));
          },
          height: function (ele) {
            const c = ele.data("mention_count") || 1;
            return Math.max(20, Math.min(40, 16 + Math.sqrt(c) * 3));
          },
          "border-width": 1,
          "border-color": "#555",
        },
      },
      {
        selector: "node:selected",
        style: {
          "border-width": 3,
          "border-color": "#0969da",
        },
      },
      {
        selector: "edge",
        style: {
          width: 1.5,
          "line-color": function (ele) {
            return PREDICATE_COLORS[ele.data("predicate")] || "#aaa";
          },
          "target-arrow-color": function (ele) {
            return PREDICATE_COLORS[ele.data("predicate")] || "#aaa";
          },
          "target-arrow-shape": "triangle",
          "curve-style": "bezier",
          label: "data(predicate)",
          "font-size": 8,
          color: "#666",
          "text-rotation": "autorotate",
          "text-background-color": "#fff",
          "text-background-opacity": 0.7,
          "text-background-padding": 1,
        },
      },
    ];
  }

  function init() {
    const container = document.getElementById("cy");
    if (!container) return;
    cy = cytoscape({
      container: container,
      elements: [],
      style: styles(),
      layout: { name: "cose", animate: false, fit: true, padding: 30 },
      wheelSensitivity: 0.2,
    });
    cy.on("tap", "node", async function (evt) {
      const nodeId = evt.target.id();
      const p = new URLSearchParams({ depth: "1", token: token() });
      const data = await fetchJson(
        "/api/graph/" + encodeURIComponent(nodeId) + "?" + p.toString()
      );
      renderEntityDetail(data);
      // 局部展开：合并新元素，不重画
      if (data && data.elements && data.elements.length) {
        const existing = new Set(cy.elements().map((e) => e.id()));
        const fresh = data.elements.filter((el) => !existing.has(el.data.id));
        if (fresh.length) {
          cy.add(fresh);
          cy.layout({ name: "cose", animate: false, fit: false, padding: 30 }).run();
        }
      }
    });
  }

  async function reload() {
    if (!cy) return;
    const filters = document.getElementById("graph-filters");
    const focus = filters && filters.dataset.focusEntity;
    const params = filterParams();
    let url;
    if (focus) {
      url = "/api/graph/" + encodeURIComponent(focus) + "?" + params.toString();
    } else {
      url = "/api/graph/global?" + params.toString();
    }
    const data = await fetchJson(url);
    cy.elements().remove();
    if (data.elements && data.elements.length) {
      cy.add(data.elements);
      cy.layout({ name: "cose", animate: false, fit: true, padding: 30 }).run();
    }
  }

  function bindFilters() {
    const btn = document.getElementById("apply-filters");
    if (btn) btn.addEventListener("click", reload);
    const filters = document.getElementById("graph-filters");
    if (filters) {
      filters.addEventListener("change", function (ev) {
        // change-triggered selects 自动刷新；range 不触发 change 直到松开 → 体验自然
        if (ev.target && (ev.target.tagName === "SELECT" || ev.target.type === "number")) {
          reload();
        }
      });
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    waitForCytoscape(function () {
      init();
      bindFilters();
      reload();
    });
  });
})();
