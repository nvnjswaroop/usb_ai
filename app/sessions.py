"""Session persistence — shared by sessions/chat/export/ppt routers.

Kept as a module (not part of the DI container) because every router that
operates on chat sessions needs to mutate _SESSION_INDEX and the disk JSON.
Container DI would force one owner, which doesn't fit this bookkeeping role.

Why here, not in main.py: After Group 3 (router split), main.py is just glue;
this is the only place session disk state is read or written.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from logging_config import getLogger
_log = getLogger("usbai")


PERSONALITIES = {
    "chat":    "You are a helpful AI assistant. Completely private and local. Be concise, accurate, honest.",
    "coder":   "You are an expert software engineer. Write clean efficient code. Always add # filename: name.ext at the top of each code block.",
    "teacher": "You are a patient clear teacher. Explain step by step with real examples.",
    "doctor":  "You are a medical information assistant. Give accurate information and always recommend seeing a real doctor.",
    "math":    "You are a mathematics expert. Show all working step by step. Check arithmetic carefully.",
    "vision":  "You are a vision AI assistant. Analyse images in detail. Describe objects, text, colours, and context accurately.",
}


@dataclass
class SessionStore:
    """Session + index state owned by the app at startup.

    A single instance lives at app.state.session_store; routers mutate via
    the methods offered here. Tests can swap it via dependency_overrides.

    In-memory caches:
      - _index_cache: parsed session-index list, invalidated on save() (Group 5).
      - _search_cache: per-file parsed sessions keyed by path, keyed by
        (mtime, size) tuple — invalidates when the file changes on disk.
        Used by search_text() to avoid re-parsing on every search.
    """
    history_dir: Path
    index_path:  Path
    _index_cache: Optional[list] = None
    _search_cache: Optional[dict] = None  # path -> (mtime, size, dict)

    def __post_init__(self):
        if self._search_cache is None:
            self._search_cache = {}

    @classmethod
    def default(cls, history_dir: Path) -> "SessionStore":
        return cls(history_dir=history_dir, index_path=history_dir / "_index.json")

    def _path(self, sid: str) -> Path:
        return self.history_dir / f"{sid}.json"

    def _invalidate(self) -> None:
        """Drop both caches — called by save/delete so subsequent reads rebuild."""
        self._index_cache = None
        # search cache stays valid for unchanged files; entries for the saved
        # id will be lazy-refreshed on next search because mtime/size changed.

    def load(self, sid: str) -> dict:
        p = self._path(sid)
        if not p.exists():
            return {"id": sid, "title": "New Chat", "messages": [],
                    "created": time.time(), "updated": time.time()}
        return json.loads(p.read_text(encoding="utf-8"))

    def save(self, data: dict) -> None:
        data["updated"] = time.time()
        p = self._path(data["id"])
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        # ponytail: write index eagerly; replaces the per-call glob+json.dumps disk
        # storm in api_sessions. Cost: one extra write per save, gain: O(1) reads.
        try:
            idx = json.loads(self.index_path.read_text(encoding="utf-8")) if self.index_path.exists() else []
        except (OSError, ValueError):
            idx = []
        rec = next((i for i in idx if i["id"] == data["id"]),
                   {"id": data["id"]})
        rec.update({"id": data["id"], "title": data.get("title", "Chat"),
                    "updated": data["updated"],
                    "message_count": len(data.get("messages", []))})
        idx = [i for i in idx if i["id"] != data["id"]]
        idx.append(rec)
        idx.sort(key=lambda x: (x.get("updated", 0), x["id"]), reverse=True)
        try:
            self.index_path.write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass  # ponytail: index is advisory; list_sessions falls back to glob.
        # ponytail: cache invalidated on every save — list_index will rebuild on next call.
        self._index_cache = None

    def list_index(self, limit: int = 100, rebuild_if_empty: bool = True) -> list:
        # ponytail: in-memory cache — valid until next save() invalidates it.
        # Single-process server; no stale reads because save() clears cache on write.
        if self._index_cache is not None:
            return self._index_cache[:limit]
        out: list = []
        try:
            if self.index_path.exists():
                out = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            out = []
        if not out and rebuild_if_empty:
            for p in self.history_dir.glob("*.json"):
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                    out.append({"id": d["id"], "title": d.get("title", "Chat"),
                                "updated": d.get("updated", 0),
                                "message_count": len(d.get("messages", []))})
                except (OSError, ValueError) as e:
                    _log.warning(f"session load failed {p.name}: {e}")
            out.sort(key=lambda x: x.get("updated", 0), reverse=True)
        self._index_cache = out
        return out[:limit]

    def delete(self, sid: str) -> bool:
        p = self._path(sid)
        if p.exists():
            p.unlink()
            # Invalidate cache — deleted session must not appear in list or search.
            self._index_cache = None
            # Prune from search cache if present.
            self._search_cache.pop(str(p), None)
            return True
        return False

    def search_text(self, query: str) -> list:
        """Search session history, caching parsed session dicts by (mtime, size).

        ponytail: in-memory cache keyed by file mtime+size — rebuilds only when
        the session file changes on disk. Eliminates the per-search glob+full-parse
        overhead (Group 5 target). A full-text index is still the future; this
        is the in-memory cache step.
        """
        q = query.lower()
        results: list = []
        for p in self.history_dir.glob("*.json"):
            if p == self.index_path:
                continue
            try:
                stat = p.stat()
                key = str(p)
                cached = self._search_cache.get(key)
                if cached is not None and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
                    d = cached[2]
                else:
                    d = json.loads(p.read_text(encoding="utf-8"))
                    self._search_cache[key] = (stat.st_mtime, stat.st_size, d)
                matches = [
                    {"index": i, "role": m.get("role", ""),
                     "excerpt": m.get("content", "")[:120]}
                    for i, m in enumerate(d.get("messages", []))
                    if q in m.get("content", "").lower()
                ]
                if matches:
                    results.append({"id": d["id"], "title": d.get("title", "Chat"),
                                    "updated": d.get("updated", 0), "matches": matches})
            except (OSError, ValueError) as e:
                _log.warning(f"session search failed {p.name}: {e}")
        results.sort(key=lambda x: x["updated"], reverse=True)
        return results


def system_for_mode(mode: str) -> str:
    return PERSONALITIES.get(mode, PERSONALITIES["chat"])
