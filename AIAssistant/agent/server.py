# Local HTTP server: serves the web chat (web/), the chat API for it,
# and the MCP endpoint (JSON-RPC 2.0 over HTTP: initialize, tools/list, tools/call).

import base64
import json
import mimetypes
import os
import re
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs, unquote

from UM.Logger import Logger

from .docs import extract_docx_text

MCP_PROTOCOL_VERSION = "2024-11-05"
UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "steslicer_ai_uploads")
IMAGE_EXT = (".png", ".jpg", ".jpeg")
MAX_BODY = 200 * 1024 * 1024


class ChatServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def start_server(web_dir, agent, settings, tools, workspace):
    class Handler(_Handler):
        pass
    Handler.web_dir = web_dir
    Handler.agent = agent
    Handler.settings = settings
    Handler.tools = tools
    Handler.workspace = workspace
    server = ChatServer(("127.0.0.1", 0), Handler)  # the OS picks a free port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class _Handler(BaseHTTPRequestHandler):
    web_dir = None
    agent = None
    settings = None
    tools = None
    workspace = None

    def log_message(self, fmt, *args):
        pass  # keep the slicer log clean

    # ---- helpers ----

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > MAX_BODY:
            raise ValueError("Слишком большой запрос")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    # ---- GET ----

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path == "/api/events":
            after = int(query.get("after", ["-1"])[0])
            try:
                self._send_json(self.agent.eventsAfter(query.get("chat", [""])[0], after))
            except ValueError as e:
                self._send_json({"error": str(e), "missing_chat": True}, status=404)
        elif url.path == "/api/tree":
            self._send_json({"nodes": self.workspace.tree(), "running_chat": self.agent.runningChat()})
        elif url.path == "/api/settings":
            self._send_json(self.settings.publicView())
        elif url.path.startswith("/files/"):
            self._serve_file(self.settings.get("output_dir"), unquote(url.path[len("/files/"):]))
        elif url.path == "/api/status":
            try:
                self._send_json(self.tools.call("get_slicer_status", {}))
            except Exception as e:
                self._send_json({"error": str(e)}, status=500)
        else:
            self._serve_static(url.path)

    def _serve_static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        self._serve_file(self.web_dir, path)

    def _serve_file(self, base_dir, path):
        base = os.path.normpath(base_dir)
        full = os.path.normpath(os.path.join(base, path.lstrip("/")))
        if not full.startswith(base + os.sep) or not os.path.isfile(full):
            self.send_error(404)
            return
        with open(full, "rb") as f:
            body = f.read()
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ---- POST ----

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            data = self._read_json()
            if path == "/api/message":
                self._post_message(data)
            elif path == "/api/settings":
                for key in ("base_url", "api_key", "model", "output_dir", "machine_rate_rub"):
                    if data.get(key):
                        self.settings.set(key, data[key])
                if "interactive" in data:
                    self.settings.set("interactive", "1" if data["interactive"] else "0")
                self._send_json(self.settings.publicView())
            elif path == "/api/respond":
                ok = self.agent.interaction.respond(data.get("request_id"), data.get("response") or {})
                self._send_json({"ok": ok}, status=200 if ok else 409)
            elif path == "/api/stop":
                self._send_json({"ok": self.agent.stop()})
            elif path == "/api/resume":
                self._send_json({"ok": self.agent.resume(data.get("chat"))})
            elif path.startswith("/api/tree/"):
                self._post_tree(path[len("/api/tree/"):], data)
            elif path == "/mcp":
                self._post_mcp(data)
            else:
                self.send_error(404)
        except Exception as e:
            Logger.logException("e", "AI Assistant: request %s failed", path)
            self._send_json({"error": str(e)}, status=500)

    def _post_message(self, data):
        attachments = []
        for item in data.get("attachments") or []:
            attachments.append(_save_attachment(item))
        accepted = self.agent.submit(data.get("chat"), data.get("text", ""), attachments)
        self._send_json({"accepted": accepted}, status=200 if accepted else 409)

    def _post_tree(self, action, data):
        ws = self.workspace
        node_id = data.get("id")
        if action == "create":
            node = ws.create(data.get("type"), data.get("name"), data.get("parent"))
            self._send_json({"node": node})
        elif action == "rename":
            self._send_json({"node": ws.rename(node_id, data.get("name", ""))})
        elif action == "move":
            self._send_json({"node": ws.move(node_id, data.get("parent"))})
        elif action == "delete":
            running = self.agent.runningChat()
            if running and running in ws.descendants(node_id):
                self._send_json({"error": "Нельзя удалить: в этом чате сейчас работает ассистент. "
                                          "Сначала нажмите «Стоп»."}, status=409)
                return
            removed = ws.delete(node_id)
            self.agent.forget(removed)
            self._send_json({"removed": removed})
        else:
            self.send_error(404)

    # ---- MCP (JSON-RPC 2.0) ----

    def _post_mcp(self, request):
        if isinstance(request, list):
            responses = [r for r in (self._mcp_one(req) for req in request) if r is not None]
            self._send_json(responses)
            return
        response = self._mcp_one(request)
        if response is None:  # notification: no body
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._send_json(response)

    def _mcp_one(self, req):
        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}
        if req_id is None:
            return None
        try:
            if method == "initialize":
                result = {"protocolVersion": MCP_PROTOCOL_VERSION,
                          "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "ste-slicer-ai-assistant", "version": "0.1.0"}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": self.tools.mcpList()}
            elif method == "tools/call":
                try:
                    out = self.tools.call(params.get("name"), params.get("arguments") or {})
                    result = {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, default=str)}],
                              "isError": False}
                except Exception as e:
                    result = {"content": [{"type": "text", "text": str(e)}], "isError": True}
            else:
                return {"jsonrpc": "2.0", "id": req_id,
                        "error": {"code": -32601, "message": "Method not found: %s" % method}}
            return {"jsonrpc": "2.0", "id": req_id, "result": result}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": str(e)}}


def _save_attachment(item):
    name = os.path.basename(item.get("name") or "file")
    name = re.sub(r"[^\w.\-() ]", "_", name) or "file"
    raw = base64.b64decode(item.get("data", ""))
    if not os.path.isdir(UPLOAD_DIR):
        os.makedirs(UPLOAD_DIR)
    path = os.path.join(UPLOAD_DIR, name)
    with open(path, "wb") as f:
        f.write(raw)
    result = {"name": name, "path": path, "kind": "file"}
    if name.lower().endswith(".docx"):
        try:
            result["kind"] = "document"
            result["text"] = extract_docx_text(path)
        except Exception as e:
            result["kind"] = "file"
            Logger.log("w", "AI Assistant: cannot read %s: %s", name, e)
    elif name.lower().endswith(IMAGE_EXT):
        mime = "image/png" if name.lower().endswith(".png") else "image/jpeg"
        result["kind"] = "image"
        result["data_url"] = "data:%s;base64,%s" % (mime, item.get("data", ""))
    return result
