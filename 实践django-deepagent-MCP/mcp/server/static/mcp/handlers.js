/**
 * Edge MCP 工具处理器
 * - runtime=browser：浏览器原生能力
 * - runtime=local：转发到本机桥接（python -m mcp.server.runtime.bridge）
 */
(function () {
  const MANIFEST_URL = "/static/mcp/manifest.json";
  const DEFAULT_BRIDGE = "http://127.0.0.1:18765";

  function bridgeBase() {
    return (window.MCP_LOCAL_BRIDGE || DEFAULT_BRIDGE).replace(/\/$/, "");
  }

  function okText(data) {
    const text = typeof data === "string" ? data : JSON.stringify(data, null, 2);
    return { content: [{ type: "text", text }] };
  }

  function errText(message) {
    return { content: [{ type: "text", text: String(message) }], isError: true };
  }

  const BROWSER_HANDLERS = {
    get_device_info: async function () {
      return okText({
        userAgent: navigator.userAgent,
        platform: navigator.platform,
        language: navigator.language,
        languages: navigator.languages,
        screen: {
          width: screen.width,
          height: screen.height,
          colorDepth: screen.colorDepth,
          pixelRatio: window.devicePixelRatio,
        },
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      });
    },

    get_local_time: async function () {
      const now = new Date();
      return okText({
        iso: now.toISOString(),
        locale: now.toLocaleString(),
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      });
    },

    get_geolocation: async function () {
      if (!navigator.geolocation) {
        return errText("浏览器不支持定位");
      }
      return new Promise(function (resolve) {
        navigator.geolocation.getCurrentPosition(
          function (pos) {
            resolve(
              okText({
                latitude: pos.coords.latitude,
                longitude: pos.coords.longitude,
                accuracy: pos.coords.accuracy,
              })
            );
          },
          function (err) {
            resolve(errText("定位失败: " + (err.message || "用户拒绝或超时")));
          },
          { enableHighAccuracy: false, timeout: 15000, maximumAge: 60000 }
        );
      });
    },

    read_clipboard: async function () {
      try {
        const text = await navigator.clipboard.readText();
        return okText({ text: text });
      } catch (e) {
        return errText("读取剪贴板失败: " + e.message);
      }
    },

    write_clipboard: async function (args) {
      try {
        await navigator.clipboard.writeText(String((args && args.text) || ""));
        return okText({ ok: true });
      } catch (e) {
        return errText("写入剪贴板失败: " + e.message);
      }
    },

    show_notification: async function (args) {
      if (!("Notification" in window)) {
        return errText("浏览器不支持通知");
      }
      if (Notification.permission === "default") {
        await Notification.requestPermission();
      }
      if (Notification.permission !== "granted") {
        return errText("通知权限被拒绝");
      }
      new Notification(String((args && args.title) || ""), {
        body: String((args && args.body) || ""),
      });
      return okText({ ok: true });
    },

    prompt_user: async function (args) {
      const message = String((args && args.message) || "");
      const defaultValue = args && args.default_value != null ? String(args.default_value) : undefined;
      const value = window.prompt(message, defaultValue);
      if (value === null) {
        return okText({ cancelled: true });
      }
      return okText({ value: value });
    },

    confirm_user: async function (args) {
      const confirmed = window.confirm(String((args && args.message) || ""));
      return okText({ confirmed: confirmed });
    },

    local_storage_get: async function (args) {
      const key = String((args && args.key) || "");
      return okText({ key: key, value: localStorage.getItem(key) });
    },

    local_storage_set: async function (args) {
      const key = String((args && args.key) || "");
      const value = args && args.value != null ? String(args.value) : "";
      localStorage.setItem(key, value);
      return okText({ ok: true, key: key });
    },

    download_text_file: async function (args) {
      const filename = String((args && args.filename) || "download.txt");
      const content = String((args && args.content) || "");
      const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = filename;
      link.click();
      URL.revokeObjectURL(link.href);
      return okText({ ok: true, filename: filename });
    },
  };

  async function callLocalTool(name, args) {
    try {
      const response = await fetch(bridgeBase() + "/tools/call", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name, arguments: args || {} }),
      });
      if (!response.ok) {
        const detail = await response.text();
        return errText(
          "本地桥接 HTTP " +
            response.status +
            "。请在页面「MCP 设置」中启动本地桥接\n" +
            detail
        );
      }
      const result = await response.json();
      if (result && Array.isArray(result.content)) {
        return result;
      }
      return okText(result);
    } catch (e) {
      return errText(
        "无法连接本地桥接 (" +
          bridgeBase() +
          ")。请在页面「MCP 设置」中配置端口并启动\n" +
          e.message
      );
    }
  }

  async function buildHandlers(manifest) {
    const handlers = {};
    const tools = (manifest && manifest.tools) || [];

    for (let i = 0; i < tools.length; i++) {
      const tool = tools[i];
      const name = tool && tool.name;
      if (!name) continue;

      if (tool.runtime === "local") {
        handlers[name] = function (args) {
          return callLocalTool(name, args);
        };
      } else if (BROWSER_HANDLERS[name]) {
        handlers[name] = BROWSER_HANDLERS[name];
      } else {
        handlers[name] = function () {
          return errText("未实现的工具: " + name);
        };
      }
    }

    return handlers;
  }

  async function initEdgeHandlers() {
    try {
      const response = await fetch(MANIFEST_URL, { cache: "no-store" });
      if (!response.ok) {
        throw new Error("manifest 加载失败: HTTP " + response.status);
      }
      const manifest = await response.json();
      window.EdgeToolHandlers = await buildHandlers(manifest);
      return window.EdgeToolHandlers;
    } catch (e) {
      console.error("[handlers.js]", e);
      window.EdgeToolHandlers = BROWSER_HANDLERS;
      return window.EdgeToolHandlers;
    }
  }

  window.EdgeToolHandlersReady = initEdgeHandlers();
})();
