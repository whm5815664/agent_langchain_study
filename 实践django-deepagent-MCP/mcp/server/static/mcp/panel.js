/**
 * MCP 本机桥接设置弹窗
 * API: /api/mcp/control/
 */
(function () {
  const API = "/api/mcp/control/";

  function $(id) {
    return document.getElementById(id);
  }

  function setBridgeUrl(base) {
    if (base) {
      window.MCP_LOCAL_BRIDGE = String(base).replace(/\/$/, "");
    }
  }

  function setPill(online, text) {
    const pill = $("mcpBridgePill");
    const label = $("mcpBridgeLabel");
    if (!pill || !label) return;
    pill.classList.remove("online", "offline");
    pill.classList.add(online ? "online" : "offline");
    label.textContent = text;
  }

  function collectPrograms() {
    const programs = {};
    document.querySelectorAll("[data-program-key]").forEach(function (input) {
      programs[input.getAttribute("data-program-key")] = input.value.trim();
    });
    return programs;
  }

  function collectPayload(extra) {
    const payload = {
      host: ($("mcpHost") && $("mcpHost").value.trim()) || "127.0.0.1",
      port: Number(($("mcpPort") && $("mcpPort").value) || 18765),
      programs: collectPrograms(),
    };
    return Object.assign(payload, extra || {});
  }

  function renderPrograms(fields, values) {
    const box = $("mcpProgramFields");
    if (!box) return;
    const vals = values || {};
    if (!fields || !fields.length) {
      box.innerHTML = '<p class="mcp-hint">当前没有工具声明 PROGRAMS。</p>';
      return;
    }
    box.innerHTML = fields
      .map(function (f) {
        const key = f.key;
        const value = vals[key] != null ? String(vals[key]) : "";
        const hint = f.hint ? '<p class="mcp-hint">' + escapeHtml(f.hint) + "</p>" : "";
        return (
          '<label class="mcp-field">' +
          "<span>" +
          escapeHtml(f.label || key) +
          " <code>(" +
          escapeHtml(key) +
          ")</code></span>" +
          '<input type="text" data-program-key="' +
          escapeAttr(key) +
          '" value="' +
          escapeAttr(value) +
          '" placeholder="可执行文件完整路径" autocomplete="off" />' +
          hint +
          "</label>"
        );
      })
      .join("");
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function escapeAttr(s) {
    return escapeHtml(s).replace(/'/g, "&#39;");
  }

  function applyStatusOnly(data) {
    const cfg = (data && data.config) || {};
    const st = (data && data.status) || {};
    const base = data.base_url || st.base_url || "http://127.0.0.1:" + (cfg.port || 18765);
    setBridgeUrl(base);
    const online = !!(st.online || data.online);
    setPill(online, online ? "桥接已启动 · :" + (st.port || cfg.port || "?") : "桥接未启动");
    if ($("mcpStatusLine") && (st.message || data.message)) {
      $("mcpStatusLine").textContent = st.message || data.message;
    }
    const tools = st.tools || data.tools || [];
    if ($("mcpToolsLine")) {
      $("mcpToolsLine").textContent = tools.length
        ? "已加载工具: " + tools.join(", ")
        : "暂无本地工具";
    }
  }

  function applyState(data) {
    const cfg = (data && data.config) || {};
    const st = (data && data.status) || {};
    if ($("mcpHost") && cfg.host) $("mcpHost").value = cfg.host;
    if ($("mcpPort") && cfg.port != null) $("mcpPort").value = cfg.port;
    if (data.program_fields) {
      renderPrograms(data.program_fields || [], cfg.programs || {});
    }

    const base = data.base_url || st.base_url || "http://127.0.0.1:" + (cfg.port || 18765);
    setBridgeUrl(base);
    if ($("mcpBaseUrlHint")) $("mcpBaseUrlHint").textContent = "将监听 " + base;

    const online = !!(st.online || data.online);
    const msg = st.message || data.message || (online ? "本地桥接在线" : "本地桥接未运行");
    if ($("mcpStatusLine")) $("mcpStatusLine").textContent = msg;
    const tools = st.tools || data.tools || [];
    if ($("mcpToolsLine")) {
      $("mcpToolsLine").textContent = tools.length
        ? "已加载工具: " + tools.join(", ")
        : "暂无本地工具";
    }
    setPill(online, online ? "桥接已启动 · :" + (st.port || cfg.port || "?") : "桥接未启动");
  }

  async function api(action, extra) {
    const body = collectPayload(Object.assign({ action: action }, extra || {}));
    const res = await fetch(API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(function () {
      return { ok: false, message: "响应解析失败" };
    });
    applyState(data);
    return data;
  }

  async function refreshFull() {
    const res = await fetch(API, { method: "GET", cache: "no-store" });
    const data = await res.json();
    applyState(data);
    return data;
  }

  async function refreshLight() {
    const res = await fetch(API + "?light=1", { method: "GET", cache: "no-store" });
    const data = await res.json();
    applyStatusOnly(data);
    return data;
  }

  function bind() {
    const openBtn = $("mcpOpenBtn");
    const dialog = $("mcpDialog");
    if (!openBtn || !dialog) return;

    openBtn.addEventListener("click", function () {
      // 先打开弹窗，再后台加载完整配置，避免等待
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "open");
      refreshFull().catch(function (e) {
        console.warn("[mcp panel]", e);
      });
    });

    $("mcpSaveBtn") &&
      $("mcpSaveBtn").addEventListener("click", async function () {
        $("mcpSaveBtn").disabled = true;
        try {
          await api("save");
        } finally {
          $("mcpSaveBtn").disabled = false;
        }
      });

    function syncHint() {
      const host = ($("mcpHost") && $("mcpHost").value.trim()) || "127.0.0.1";
      const port = ($("mcpPort") && $("mcpPort").value) || "18765";
      if ($("mcpBaseUrlHint")) $("mcpBaseUrlHint").textContent = "将监听 http://" + host + ":" + port;
    }
    $("mcpHost") && $("mcpHost").addEventListener("input", syncHint);
    $("mcpPort") && $("mcpPort").addEventListener("input", syncHint);

    // 首屏只做轻量状态探测
    refreshLight().catch(function (e) {
      console.warn("[mcp panel]", e);
      setPill(false, "桥接未启动");
    });

    setInterval(function () {
      if (document.hidden) return;
      refreshLight().catch(function () {});
    }, 15000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }

  window.McpPanel = { refresh: refreshFull, api: api };
})();
