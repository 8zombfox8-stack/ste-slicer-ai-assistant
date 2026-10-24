# Workspace: folders (projects) of any depth and chats inside them, stored as JSON in the results folder.
#
#   <output_dir>/workspace/tree.json        — {"nodes": {id: {id, type: folder|chat, name, parent, created, updated}}}
#   <output_dir>/workspace/chats/<id>.json  — {"messages": [...LLM history...], "events": [...chat feed...]}

import json
import os
import threading
import time
import uuid

DEFAULT_CHAT_NAME = "Новый чат"


class Workspace:
    def __init__(self, settings):
        self._settings = settings
        self._lock = threading.RLock()
        self._nodes = None

    # ---- paths ----

    def _root(self):
        path = os.path.join(self._settings.get("output_dir"), "workspace")
        chats = os.path.join(path, "chats")
        if not os.path.isdir(chats):
            os.makedirs(chats)
        return path

    def _tree_path(self):
        return os.path.join(self._root(), "tree.json")

    def _chat_path(self, chat_id):
        return os.path.join(self._root(), "chats", "%s.json" % chat_id)

    # ---- tree ----

    def _load(self):
        if self._nodes is not None:
            return
        self._nodes = {}
        try:
            with open(self._tree_path(), encoding="utf-8") as f:
                self._nodes = json.load(f).get("nodes", {})
        except (OSError, ValueError):
            self._nodes = {}
        if not any(n["type"] == "chat" for n in self._nodes.values()):
            folder = self._add("folder", "Мой проект", None)
            self._add("chat", DEFAULT_CHAT_NAME, folder["id"])

    def _save(self):
        path = self._tree_path()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"nodes": self._nodes}, f, ensure_ascii=False, indent=1)
        if os.path.exists(path):
            os.remove(path)
        os.rename(tmp, path)

    def _add(self, node_type, name, parent):
        now = time.time()
        node = {"id": uuid.uuid4().hex[:12], "type": node_type, "name": name.strip() or DEFAULT_CHAT_NAME,
                "parent": parent, "created": now, "updated": now}
        self._nodes[node["id"]] = node
        self._save()
        return node

    def tree(self):
        with self._lock:
            self._load()
            return sorted(self._nodes.values(), key=lambda n: (n["type"] != "folder", n["created"]))

    def get(self, node_id):
        with self._lock:
            self._load()
            return self._nodes.get(node_id)

    def create(self, node_type, name, parent=None):
        if node_type not in ("folder", "chat"):
            raise ValueError("Тип должен быть folder или chat")
        with self._lock:
            self._load()
            if parent is not None:
                p = self._nodes.get(parent)
                if p is None or p["type"] != "folder":
                    raise ValueError("Родитель должен быть папкой")
            default = "Новая папка" if node_type == "folder" else DEFAULT_CHAT_NAME
            return self._add(node_type, name or default, parent)

    def rename(self, node_id, name):
        with self._lock:
            self._load()
            node = self._nodes.get(node_id)
            if node is None:
                raise ValueError("Не найдено")
            node["name"] = name.strip()[:120] or node["name"]
            node["updated"] = time.time()
            self._save()
            return node

    def move(self, node_id, parent):
        with self._lock:
            self._load()
            node = self._nodes.get(node_id)
            if node is None:
                raise ValueError("Не найдено")
            ancestor = parent
            while ancestor is not None:  # a folder cannot be moved into itself
                if ancestor == node_id:
                    raise ValueError("Нельзя переместить папку внутрь самой себя")
                ancestor = self._nodes.get(ancestor, {}).get("parent")
            node["parent"] = parent
            self._save()
            return node

    def descendants(self, node_id):
        with self._lock:
            self._load()
            result, queue = [], [node_id]
            while queue:
                current = queue.pop()
                result.append(current)
                queue.extend(n["id"] for n in self._nodes.values() if n["parent"] == current)
            return result

    def delete(self, node_id):
        with self._lock:
            ids = self.descendants(node_id)
            for i in ids:
                node = self._nodes.pop(i, None)
                if node and node["type"] == "chat" and os.path.exists(self._chat_path(i)):
                    os.remove(self._chat_path(i))
            if not any(n["type"] == "chat" for n in self._nodes.values()):
                self._add("chat", DEFAULT_CHAT_NAME, None)  # always keep at least one chat
            self._save()
            return ids

    def touch(self, chat_id):
        with self._lock:
            self._load()
            if chat_id in self._nodes:
                self._nodes[chat_id]["updated"] = time.time()
                self._save()

    # ---- chat content ----

    def loadChat(self, chat_id):
        try:
            with open(self._chat_path(chat_id), encoding="utf-8") as f:
                data = json.load(f)
            return data.get("messages") or [], data.get("events") or []
        except (OSError, ValueError):
            return [], []

    def saveChat(self, chat_id, messages, events):
        with self._lock:
            if self.get(chat_id) is None:
                return  # deleted meanwhile
            path = self._chat_path(chat_id)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"messages": [_storable(m) for m in messages], "events": events}, f,
                          ensure_ascii=False, default=str)
            if os.path.exists(path):
                os.remove(path)
            os.rename(tmp, path)


def _storable(message):
    # Images are sent to the LLM as base64 data URLs; keep chat files small by storing a note instead.
    content = message.get("content")
    if isinstance(content, list):
        parts = []
        for part in content:
            if part.get("type") == "image_url":
                parts.append({"type": "text", "text": "(изображение было приложено)"})
            else:
                parts.append(part)
        message = dict(message, content=parts)
    return message
