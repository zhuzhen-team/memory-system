/* memoryd Plan 11 - HTMX extras + theme toggle (vanilla JS) */
(function () {
  "use strict";

  // ---- 主题切换：浅 / 暗 / 自动 ----
  const KEY = "memoryd-theme";
  function applyTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
  }
  function nextTheme(t) {
    return t === "auto" ? "light" : t === "light" ? "dark" : "auto";
  }
  function labelFor(t) {
    return t === "auto" ? "主题：自动" : t === "light" ? "主题：浅色" : "主题：暗色";
  }

  function initTheme() {
    let saved = "auto";
    try {
      saved = localStorage.getItem(KEY) || "auto";
    } catch (e) { /* localStorage 不可用就降级 */ }
    applyTheme(saved);
    const btn = document.querySelector(".theme-toggle");
    if (!btn) return;
    btn.textContent = labelFor(saved);
    btn.addEventListener("click", function () {
      const cur = document.documentElement.getAttribute("data-theme") || "auto";
      const nxt = nextTheme(cur);
      applyTheme(nxt);
      try { localStorage.setItem(KEY, nxt); } catch (e) { /* ignore */ }
      btn.textContent = labelFor(nxt);
    });
  }

  // ---- HTMX：自动塞 token query param ----
  // 现有模板在 href 上手工拼 ?token=...，但 hx-get 也需要。
  // 用 htmx:configRequest 钩子统一拼，避免每个 hx-* 都手工带。
  function bindHtmxToken() {
    if (typeof htmx === "undefined") return;
    // 从某个 meta / data attribute 拿 token；这里走 hidden input 兜底
    function findToken() {
      const inp = document.querySelector('input[name="token"]');
      if (inp && inp.value) return inp.value;
      const filters = document.getElementById("graph-filters");
      if (filters && filters.dataset.token) return filters.dataset.token;
      // 最终兜底：从当前 URL 取
      const qp = new URLSearchParams(window.location.search);
      return qp.get("token") || "";
    }
    document.body.addEventListener("htmx:configRequest", function (evt) {
      const t = findToken();
      if (t && evt.detail && evt.detail.parameters && !evt.detail.parameters.token) {
        evt.detail.parameters.token = t;
      }
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initTheme();
    bindHtmxToken();
  });
})();
