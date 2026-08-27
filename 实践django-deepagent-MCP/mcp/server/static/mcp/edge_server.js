/** Edge MCP Server：WebSocket + JSON-RPC */

(function () {
  const MANIFEST_URL = "/static/mcp/manifest.json";

  function wsUrl(sessionId) {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    return proto + "//" + location.host + "/ws/mcp/?session_id=" + encodeURIComponent(sessionId);
  }

  function sendJson(ws, obj) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
  }

  async function loadTools() {
    const res = await fetch(MANIFEST_URL);
    const data = await res.json();
    return data.tools || [];
  }

  async function runTool(name, args, sessionId) {
    if (window.EdgeToolHandlersReady) {
      await window.EdgeToolHandlersReady;
    }
    const fn = window.EdgeToolHandlers && window.EdgeToolHandlers[name];
    if (!fn) return { content: [{ type: "text", text: "未知工具: " + name }], isError: true };
    return fn(args || {}, { sessionId });
  }

  async function handleRequest(ws, msg) {
    const { id, method, params } = msg;
    try {
      if (method === "initialize") {
        sendJson(ws, {
          jsonrpc: "2.0",
          id,
          result: {
            protocolVersion: "2024-11-05",
            capabilities: { tools: {} },
            serverInfo: { name: "browser-edge-mcp", version: "1.0" },
          },
        });
        return;
      }
      if (method === "tools/list") {
        const tools = await loadTools();
        sendJson(ws, { jsonrpc: "2.0", id, result: { tools } });
        return;
      }
      if (method === "tools/call") {
        const sessionId = window.__edgeSessionId || "";
        const result = await runTool(params.name, params.arguments, sessionId);
        sendJson(ws, { jsonrpc: "2.0", id, result });
        return;
      }
      sendJson(ws, { jsonrpc: "2.0", id, error: { code: -32601, message: "未知方法: " + method } });
    } catch (e) {
      sendJson(ws, { jsonrpc: "2.0", id, error: { code: -32000, message: e.message || String(e) } });
    }
  }

  window.EdgeMcp = {
    sessionId: "",
    ws: null,
    onStatus: null,
    _timer: null,

    connect(sessionId) {
      this.sessionId = sessionId;
      window.__edgeSessionId = sessionId;
      if (this._timer) clearTimeout(this._timer);
      if (this.onStatus) this.onStatus("reconnect", "Edge 连接中…");

      const ws = new WebSocket(wsUrl(sessionId));
      this.ws = ws;

      ws.onopen = () => {
        loadTools().then((tools) => {
          if (this.onStatus) this.onStatus("online", "Edge 已连接 · " + tools.length + " 工具");
        });
      };
      ws.onclose = () => {
        if (this.onStatus) this.onStatus("offline", "Edge 离线");
        this._timer = setTimeout(() => this.connect(sessionId), 1500);
      };
      ws.onerror = () => {
        if (this.onStatus) this.onStatus("offline", "Edge 连接错误");
      };
      ws.onmessage = (ev) => {
        let msg;
        try {
          msg = JSON.parse(ev.data);
        } catch (_) {
          return;
        }
        if (msg.method && msg.id != null) handleRequest(ws, msg);
      };
    },

    disconnect() {
      if (this._timer) clearTimeout(this._timer);
      if (this.ws) {
        this.ws.onclose = null;
        this.ws.close();
      }
    },
  };
})();
