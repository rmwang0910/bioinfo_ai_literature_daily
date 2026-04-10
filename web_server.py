#!/usr/bin/env python
"""
Web UI server for bioinfo_ai_literature_daily.

Provides a browser-based interface for literature search, result browsing,
configuration management, and single-paper deep analysis.

No external web framework required (stdlib http.server only).

Run:
  python web_server.py --port 8080
Then open:
  http://127.0.0.1:8080
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import queue
import sys
import threading
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs

# Ensure project root is on sys.path so imports work
_PROJECT_DIR = Path(__file__).resolve().parent
if str(_PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(_PROJECT_DIR))

from datetime import datetime

from main import BioinfoAILiteratureDaily
from agent_main import LiteratureAgent
from literature.base_client import PaperMetadata

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Utility helpers (following AutoSkill web_ui.py patterns)
# ---------------------------------------------------------------------------

def _web_dir() -> Path:
    return _PROJECT_DIR / "web"


def _safe_static_path(path: str) -> Optional[Path]:
    web = _web_dir().resolve()
    rel = str(path or "").lstrip("/")
    if not rel.startswith("static/"):
        return None
    candidate = (web / rel.replace("/", os.sep)).resolve()
    try:
        candidate.relative_to(web)
    except Exception:
        return None
    return candidate


def _content_type_for(path: str) -> str:
    p = str(path or "").lower()
    if p.endswith(".html"):
        return "text/html; charset=utf-8"
    if p.endswith(".css"):
        return "text/css; charset=utf-8"
    if p.endswith(".js"):
        return "application/javascript; charset=utf-8"
    if p.endswith(".json"):
        return "application/json; charset=utf-8"
    if p.endswith(".svg"):
        return "image/svg+xml"
    if p.endswith(".png"):
        return "image/png"
    if p.endswith(".jpg") or p.endswith(".jpeg"):
        return "image/jpeg"
    return "application/octet-stream"


def _json_response(handler: BaseHTTPRequestHandler, payload: Any, *, status: int = 200) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(int(status))
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _read_json(handler: BaseHTTPRequestHandler, *, max_bytes: int = 5_000_000) -> Dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or 0)
    if length <= 0:
        return {}
    if length > int(max_bytes):
        raise ValueError(f"request too large: {length} bytes")
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    obj = json.loads(raw.decode("utf-8"))
    if obj is None:
        return {}
    if not isinstance(obj, dict):
        raise ValueError("request body must be a JSON object")
    return obj


def _paper_to_dict(paper: PaperMetadata) -> Dict[str, Any]:
    """Convert PaperMetadata to a JSON-safe dict with extra useful fields."""
    d = paper.to_dict()
    d["is_open_access"] = getattr(paper, "is_open_access", False)
    d["open_access_url"] = getattr(paper, "open_access_url", None)
    d["institutions"] = getattr(paper, "institutions", [])
    return d


# ---------------------------------------------------------------------------
# Saved papers store (persistent, cross-session)
# ---------------------------------------------------------------------------

class SavedPapersStore:
    """Thread-safe JSON file store for saved/archived papers."""

    def __init__(self, path: Optional[Path] = None):
        self._path = path or (_PROJECT_DIR / "cache" / "saved_papers.json")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _load(self) -> List[Dict[str, Any]]:
        if not self._path.exists():
            return []
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _save(self, papers: List[Dict[str, Any]]):
        tmp = str(self._path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(papers, f, ensure_ascii=False, indent=2)
        os.replace(tmp, str(self._path))

    def list_all(self) -> List[Dict[str, Any]]:
        with self._lock:
            return self._load()

    def add(self, papers: List[Dict[str, Any]]) -> int:
        """Add papers, deduplicate by DOI/title. Returns count actually added."""
        with self._lock:
            existing = self._load()
            existing_keys = set()
            for p in existing:
                if p.get("doi"):
                    existing_keys.add(p["doi"])
                elif p.get("title"):
                    existing_keys.add(p["title"])

            added = 0
            for p in papers:
                key = p.get("doi") or p.get("title")
                if key and key in existing_keys:
                    continue
                p["saved_at"] = datetime.now().isoformat()
                existing.append(p)
                if key:
                    existing_keys.add(key)
                added += 1

            self._save(existing)
            return added

    def delete(self, ids: List[str]) -> int:
        """Delete papers by DOI or title. Returns count deleted."""
        id_set = set(ids)
        with self._lock:
            existing = self._load()
            before = len(existing)
            remaining = [
                p for p in existing
                if p.get("doi") not in id_set and p.get("title") not in id_set
            ]
            self._save(remaining)
            return before - len(remaining)


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

class Session:
    """Holds per-session state: agent instances, results, config."""

    def __init__(self, config_path: Optional[str] = None):
        self.session_id: str = str(uuid.uuid4())
        self.agent = LiteratureAgent(config_path=config_path, mode="interactive")
        self.config: Dict[str, Any] = copy.deepcopy(self.agent.base_agent.config)
        self.papers: List[PaperMetadata] = []
        self.paper_summaries: Dict[str, str] = {}
        self.overall_summary: Optional[str] = None
        self.keyword_validation_info: Optional[Dict] = None
        self.busy: bool = False  # prevent concurrent long ops

    def sync_config_to_agent(self):
        """Push session config overlay into the agent's base_agent.config."""
        self.agent.base_agent.config = copy.deepcopy(self.config)


class SessionManager:
    """Thread-safe session store."""

    def __init__(self, config_path: Optional[str] = None):
        self._lock = threading.Lock()
        self._sessions: Dict[str, Session] = {}
        self._config_path = config_path
        self.saved_store = SavedPapersStore()

    def create(self) -> Session:
        s = Session(config_path=self._config_path)
        with self._lock:
            self._sessions[s.session_id] = s
        return s

    def get(self, session_id: str) -> Optional[Session]:
        with self._lock:
            return self._sessions.get(session_id)

    def delete(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None


# ---------------------------------------------------------------------------
# Background workers (run in threads, push events to queue)
# ---------------------------------------------------------------------------

def _search_worker(session: Session, eq: queue.Queue):
    """Execute search + summarize using current session config (parse already done)."""
    try:
        agent = session.agent

        # Ensure agent config is in sync with session config
        session.sync_config_to_agent()

        # Step 1: Search (with progress callback)
        eq.put({"type": "status", "step": "searching", "message": "正在搜索文献..."})

        def _on_search_progress(step: str, message: str):
            eq.put({"type": "status", "step": step, "message": message})

        papers = agent.base_agent.search_literature(progress_callback=_on_search_progress)
        session.papers = papers or []

        if not papers:
            eq.put({"type": "papers", "data": [], "total": 0})
            eq.put({"type": "done", "total_papers": 0})
            return

        papers_data = [_paper_to_dict(p) for p in papers]
        eq.put({"type": "papers", "data": papers_data, "total": len(papers)})

        # Step 4: Get validation info
        kv_info = getattr(agent.base_agent, "_keyword_validation_info", None)
        session.keyword_validation_info = kv_info

        # Step 5: Overall summary
        eq.put({"type": "status", "step": "summarizing", "message": "正在生成文献综述..."})
        try:
            summary = agent.summarize_literature(papers, keyword_validation_info=kv_info)
            session.overall_summary = summary
            eq.put({"type": "summary", "data": summary})
        except Exception as e:
            eq.put({"type": "status", "step": "summarizing", "message": f"综述生成失败: {e}"})

        # Step 6: Per-paper summaries
        total = len(papers)
        eq.put({"type": "status", "step": "paper_summaries", "message": f"正在为 {total} 篇论文生成中文总结..."})
        for i, paper in enumerate(papers):
            eq.put({
                "type": "status", "step": "paper_summaries",
                "message": f"总结进度: {i + 1}/{total}",
                "progress": {"current": i + 1, "total": total}
            })
            try:
                s = agent._summarize_single_paper(paper, i + 1)
                if s:
                    key = paper.doi or paper.title or str(i)
                    session.paper_summaries[key] = s
                    eq.put({"type": "paper_summary", "paper_index": i, "paper_id": key, "summary": s})
            except Exception as e:
                logger.warning(f"Paper summary failed for index {i}: {e}")

        eq.put({"type": "done", "total_papers": len(papers)})

    except Exception as e:
        logger.error(f"Search worker error: {traceback.format_exc()}")
        eq.put({"type": "error", "message": str(e)})


def _analyze_worker(session: Session, paper_query: str, eq: queue.Queue):
    """Execute single paper deep analysis, pushing progress events."""
    try:
        from literature.paper_fetcher import SinglePaperFetcher
        from literature.pdf_downloader import PDFDownloader

        agent = session.agent

        # Step 1: Fetch metadata
        eq.put({"type": "status", "step": "fetching", "message": "正在获取文献元数据..."})
        paper = SinglePaperFetcher().fetch(paper_query)
        if not paper:
            eq.put({"type": "error", "message": f"未找到文献: {paper_query}"})
            return
        eq.put({"type": "paper_meta", "data": _paper_to_dict(paper)})

        # Step 2: Try BGPT first
        analysis = None
        source_desc = None
        pdf_bytes = None

        try:
            from literature.bgpt_client import BGPTClient
            bgpt_api_key = agent.base_agent.config.get("bgpt", {}).get("api_key") or None
            bgpt = BGPTClient(api_key=bgpt_api_key)
            bgpt_data = None
            if paper.doi:
                bgpt_data = bgpt.fetch_by_doi(paper.doi)
            if not bgpt_data and paper.title:
                bgpt_data = bgpt.fetch_by_title(paper.title)

            if bgpt_data and agent._is_analysis_valid(bgpt_data):
                eq.put({"type": "status", "step": "analyzing", "message": "BGPT 命中，翻译结构化数据..."})
                source_desc = "BGPT全文解析"
                analysis = agent._translate_bgpt_analysis(bgpt_data)
        except Exception as e:
            logger.warning(f"BGPT fetch failed: {e}")

        # Step 3: Download full text if BGPT miss
        if not analysis:
            eq.put({"type": "status", "step": "downloading", "message": "正在下载全文..."})
            try:
                content, pdf_source, pdf_bytes = PDFDownloader().get_fulltext(paper)
            except Exception as e:
                content, pdf_source, pdf_bytes = None, str(e), None

            if not content:
                # Fallback to abstract
                content = paper.abstract
                pdf_source = "摘要"
                if not content:
                    eq.put({"type": "error", "message": "无法获取文献全文或摘要"})
                    return

            source_desc = pdf_source

            # Step 4: LLM analysis
            eq.put({"type": "status", "step": "analyzing", "message": f"LLM 正在分析文献 ({source_desc})..."})
            try:
                analysis = agent._analyze_paper_with_llm(paper, content, source_desc)
            except Exception as e:
                eq.put({"type": "error", "message": f"LLM 分析失败: {e}"})
                return

        eq.put({"type": "analysis", "data": analysis, "source": source_desc, "paper": _paper_to_dict(paper)})
        eq.put({"type": "done"})

    except Exception as e:
        logger.error(f"Analyze worker error: {traceback.format_exc()}")
        eq.put({"type": "error", "message": str(e)})


def _sanitize(obj: Any) -> Any:
    """Make an object JSON-serializable."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    return str(obj)


def _safe_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return config with sensitive fields masked."""
    c = copy.deepcopy(config)
    email = c.get("email", {})
    if email.get("smtp_password"):
        email["smtp_password"] = "***"
    return _sanitize(c)


# ---------------------------------------------------------------------------
# HTTP handler factory
# ---------------------------------------------------------------------------

def make_handler(manager: SessionManager):
    web = _web_dir()

    class Handler(BaseHTTPRequestHandler):
        server_version = "BioLitDaily/0.1"

        def log_message(self, format, *args):
            msg = str(format or "") % args
            logger.info(f"[web] {msg}")

        # -- NDJSON helpers --
        def _start_ndjson(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

        def _write_ndjson(self, payload: Dict[str, Any]):
            data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
            self.wfile.write(data)
            self.wfile.flush()

        def _drain_queue(self, eq: queue.Queue, thread: threading.Thread):
            """Read events from queue and write as NDJSON until done/error."""
            self._start_ndjson()
            while True:
                try:
                    event = eq.get(timeout=0.5)
                    self._write_ndjson(event)
                    if event.get("type") in ("done", "error"):
                        break
                except queue.Empty:
                    if not thread.is_alive():
                        break

        # -- GET routes --
        def do_GET(self):
            parsed = urlparse(self.path or "/")
            path = parsed.path or "/"

            if path == "/api/health":
                return _json_response(self, {"ok": True})

            # Serve index.html
            if path == "/" or path == "/index.html":
                index = web / "index.html"
                if not index.exists():
                    return _json_response(self, {"error": "web/index.html not found"}, status=500)
                data = index.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", _content_type_for(str(index)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

            # Serve static files
            static_path = _safe_static_path(path)
            if static_path is not None and static_path.exists() and static_path.is_file():
                data = static_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", _content_type_for(str(static_path)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

            _json_response(self, {"error": "not found"}, status=404)

        # -- POST routes --
        def do_POST(self):
            parsed = urlparse(self.path or "/")
            path = parsed.path or "/"

            try:
                body = _read_json(self)
            except Exception as e:
                return _json_response(self, {"error": str(e)}, status=400)

            # --- Session create ---
            if path == "/api/session/create":
                session = manager.create()
                return _json_response(self, {
                    "session_id": session.session_id,
                    "config": _safe_config(session.config),
                })

            # --- Get config ---
            if path == "/api/config":
                session = manager.get(str(body.get("session_id", "")))
                if not session:
                    return _json_response(self, {"error": "unknown session"}, status=404)
                return _json_response(self, {"config": _safe_config(session.config)})

            # --- Update config ---
            if path == "/api/config/update":
                session = manager.get(str(body.get("session_id", "")))
                if not session:
                    return _json_response(self, {"error": "unknown session"}, status=404)
                patch = body.get("config", {})
                for section, values in patch.items():
                    if section not in session.config:
                        session.config[section] = {}
                    if isinstance(values, dict):
                        session.config[section].update(values)
                    else:
                        session.config[section] = values
                session.sync_config_to_agent()
                return _json_response(self, {"ok": True, "config": _safe_config(session.config)})

            # --- Parse natural language input ---
            if path == "/api/parse":
                session = manager.get(str(body.get("session_id", "")))
                if not session:
                    return _json_response(self, {"error": "unknown session"}, status=404)
                user_input = str(body.get("user_input", "")).strip()
                if not user_input:
                    return _json_response(self, {"error": "user_input is required"}, status=400)

                try:
                    agent = session.agent
                    parsed = agent.parse_user_request(user_input)

                    # Merge keywords
                    if not parsed.get("keywords"):
                        topic_kw = parsed.get("topic_keywords")
                        if isinstance(topic_kw, list) and topic_kw:
                            parsed["keywords"] = [str(k).strip() for k in topic_kw if str(k).strip()]
                        elif parsed.get("boolean_query"):
                            parsed["keywords"] = [str(parsed["boolean_query"]).strip()]

                    if not parsed.get("keywords"):
                        return _json_response(self, {"error": "未能从输入中提取搜索关键词，请提供更明确的关键词。"}, status=400)

                    # Update agent config from parsed result
                    agent.update_config_from_request(parsed, user_input)
                    session.config = copy.deepcopy(agent.base_agent.config)

                    return _json_response(self, {
                        "ok": True,
                        "parsed": _sanitize(parsed),
                        "config": _safe_config(session.config),
                    })
                except Exception as e:
                    return _json_response(self, {"error": f"解析失败: {e}"}, status=500)

            # --- Search (NDJSON streaming, uses current session config) ---
            if path == "/api/search":
                session = manager.get(str(body.get("session_id", "")))
                if not session:
                    return _json_response(self, {"error": "unknown session"}, status=404)
                if session.busy:
                    return _json_response(self, {"error": "session is busy"}, status=409)

                # Check keywords are configured
                kws = (session.config.get("search") or {}).get("keywords") or []
                if not kws:
                    return _json_response(self, {"error": "请先解析需求或在高级参数中填写关键词"}, status=400)

                session.busy = True
                session.papers = []
                session.paper_summaries = {}
                session.overall_summary = None

                eq = queue.Queue()
                t = threading.Thread(target=_search_worker, args=(session, eq), daemon=True)
                t.start()

                try:
                    self._drain_queue(eq, t)
                except BrokenPipeError:
                    pass
                finally:
                    session.busy = False
                return

            # --- Analyze single paper (NDJSON streaming) ---
            if path == "/api/analyze":
                session = manager.get(str(body.get("session_id", "")))
                if not session:
                    return _json_response(self, {"error": "unknown session"}, status=404)
                paper_query = str(body.get("query", "")).strip()
                if not paper_query:
                    return _json_response(self, {"error": "query is required (PMID/DOI/title)"}, status=400)
                if session.busy:
                    return _json_response(self, {"error": "session is busy"}, status=409)

                session.busy = True
                eq = queue.Queue()
                t = threading.Thread(target=_analyze_worker, args=(session, paper_query, eq), daemon=True)
                t.start()

                try:
                    self._drain_queue(eq, t)
                except BrokenPipeError:
                    pass
                finally:
                    session.busy = False
                return

            # --- Send email ---
            # --- Export RIS (binary download) ---
            if path == "/api/export/ris":
                session = manager.get(str(body.get("session_id", "")))
                if not session:
                    return _json_response(self, {"error": "unknown session"}, status=404)
                if not session.papers:
                    return _json_response(self, {"error": "no papers to export"}, status=400)

                indices = body.get("indices")  # list of int, or None for all
                if indices is not None:
                    selected = [session.papers[i] for i in indices if 0 <= i < len(session.papers)]
                else:
                    selected = list(session.papers)

                if not selected:
                    return _json_response(self, {"error": "no papers selected"}, status=400)

                # Generate RIS content using the agent's existing method
                agent = session.agent.base_agent
                lines: list[str] = []
                for paper in selected:
                    ris_ty, ris_m3 = agent._get_ris_type(paper)
                    lines.append(f"TY  - {ris_ty}")
                    title = (paper.title or "").strip()
                    if title:
                        lines.append(f"T1  - {title}")
                    for author in agent._format_ris_authors(paper):
                        lines.append(f"A1  - {author}")
                    journal_full = (paper.journal or paper.venue or "").strip() or None
                    journal_abbrev = (paper.journal or "").strip() or None
                    if journal_full:
                        lines.append(f"JF  - {journal_full}")
                    if journal_abbrev:
                        lines.append(f"JO  - {journal_abbrev}")
                    ris_date = agent._format_ris_date(paper)
                    if ris_date:
                        lines.append(f"Y1  - {ris_date}")
                    elif paper.year:
                        lines.append(f"Y1  - {paper.year}//")
                    if paper.pubmed_id:
                        lines.append(f"ID  - PMID:{paper.pubmed_id}")
                    elif paper.arxiv_id:
                        lines.append(f"ID  - arXiv:{paper.arxiv_id}")
                    db_label = agent._get_ris_db_label(paper)
                    if db_label:
                        lines.append(f"DB  - {db_label}")
                    if paper.doi:
                        lines.append(f"DO  - {paper.doi}")
                    if ris_m3:
                        lines.append(f"M3  - {ris_m3}")
                    url = paper.url
                    if not url and paper.pubmed_id:
                        url = f"https://pubmed.ncbi.nlm.nih.gov/{paper.pubmed_id}/"
                    if url:
                        lines.append(f"UR  - {url}")
                    abstract = (paper.abstract or "").strip()
                    if abstract:
                        lines.append(f"N2  - {abstract}")
                    for kw in (paper.keywords or []):
                        kw = str(kw).strip()
                        if kw:
                            lines.append(f"KW  - {kw}")
                    lines.append("ER  - ")
                    lines.append("")

                ris_bytes = "\n".join(lines).encode("utf-8")
                from datetime import datetime as _dt
                fname = f"{_dt.now().strftime('%Y%m%d')}_literature.ris"

                self.send_response(200)
                self.send_header("Content-Type", "application/x-research-info-systems; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
                self.send_header("Content-Length", str(len(ris_bytes)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(ris_bytes)
                return

            if path == "/api/email/send":
                session = manager.get(str(body.get("session_id", "")))
                if not session:
                    return _json_response(self, {"error": "unknown session"}, status=404)
                if not session.papers:
                    return _json_response(self, {"error": "no papers to send"}, status=400)

                # Select papers by indices (or all)
                indices = body.get("indices")
                if indices is not None:
                    selected = [session.papers[i] for i in indices if 0 <= i < len(session.papers)]
                else:
                    selected = list(session.papers)
                if not selected:
                    return _json_response(self, {"error": "no papers selected"}, status=400)

                # Filter paper_summaries to only include selected papers
                selected_summaries = None
                if session.paper_summaries:
                    selected_summaries = {}
                    for p in selected:
                        for key in (p.doi, p.title):
                            if key and key in session.paper_summaries:
                                selected_summaries[key] = session.paper_summaries[key]
                                break

                # Optionally override to_email
                to_email = str(body.get("to_email", "")).strip()
                if to_email:
                    session.config.setdefault("email", {})["to_email"] = to_email
                    session.sync_config_to_agent()

                try:
                    ok = session.agent.base_agent.send_email(
                        selected,
                        summary=session.overall_summary,
                        paper_summaries=selected_summaries if selected_summaries else None,
                    )
                    if ok:
                        return _json_response(self, {"ok": True, "message": f"邮件发送成功（{len(selected)} 篇文献）"})
                    else:
                        return _json_response(self, {"error": "邮件发送失败，请检查 SMTP 配置"}, status=500)
                except Exception as e:
                    return _json_response(self, {"error": f"邮件发送异常: {e}"}, status=500)

            # --- Saved papers: list ---
            if path == "/api/saved/list":
                papers = manager.saved_store.list_all()
                return _json_response(self, {"papers": papers})

            # --- Saved papers: add ---
            if path == "/api/saved/add":
                items = body.get("papers")
                if not items or not isinstance(items, list):
                    return _json_response(self, {"error": "papers array is required"}, status=400)
                added = manager.saved_store.add(items)
                total = len(manager.saved_store.list_all())
                return _json_response(self, {"ok": True, "added": added, "total": total})

            # --- Saved papers: delete ---
            if path == "/api/saved/delete":
                ids = body.get("ids")
                if not ids or not isinstance(ids, list):
                    return _json_response(self, {"error": "ids array is required"}, status=400)
                deleted = manager.saved_store.delete(ids)
                total = len(manager.saved_store.list_all())
                return _json_response(self, {"ok": True, "deleted": deleted, "total": total})

            _json_response(self, {"error": "not found"}, status=404)

    return Handler


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="BioLit Daily Web UI")
    parser.add_argument("--host", default=os.environ.get("BIOLIT_WEB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("BIOLIT_WEB_PORT", "8080")))
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    mgr = SessionManager(config_path=args.config)
    handler_cls = make_handler(mgr)
    server = ThreadingHTTPServer((args.host, args.port), handler_cls)
    host, port = server.server_address[:2]
    print(f"BioLit Daily Web UI: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
