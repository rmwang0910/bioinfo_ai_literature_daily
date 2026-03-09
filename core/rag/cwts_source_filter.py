import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

from literature.base_client import PaperMetadata

logger = logging.getLogger(__name__)


class CWTSSourceFilter:
    def __init__(self, data_path: str = "data/cwts_source_map.json"):
        self.data_path = data_path
        self.sources: List[Dict] = []
        self.by_source_id: Dict[str, Dict] = {}
        self.by_name: Dict[str, Dict] = {}
        self._load_data()

    def _load_data(self):
        path = Path(self.data_path)
        if not path.exists():
            path = Path(__file__).parent.parent.parent / self.data_path
        if not path.exists():
            logger.warning(f"CWTS source map not found: {path}")
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.sources = data.get("sources", [])
            for item in self.sources:
                source_id = str(item.get("source_id", "")).strip()
                if source_id:
                    self.by_source_id[source_id] = item
                name = str(item.get("name", "")).strip()
                if name:
                    self.by_name[self._normalize(name)] = item
            logger.info(f"Loaded {len(self.sources)} CWTS sources from {path}")
        except Exception as e:
            logger.warning(f"Failed to load CWTS source map: {e}")

    def _normalize(self, text: str) -> str:
        text = re.sub(r"\s+", " ", text or "").strip().lower()
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text).strip()

    def _extract_source_id(self, paper: PaperMetadata) -> Optional[str]:
        raw = paper.raw_data if isinstance(paper.raw_data, dict) else {}
        openalex = raw.get("openalex", {}) if isinstance(raw, dict) else {}
        sid = openalex.get("source_id")
        if sid:
            return str(sid).strip()
        return None

    def _match_by_name(self, name: str) -> Optional[Dict]:
        normalized = self._normalize(name)
        if not normalized:
            return None
        exact = self.by_name.get(normalized)
        if exact:
            return exact
        for key, value in self.by_name.items():
            if normalized in key or key in normalized:
                return value
        return None

    def get_source_entry(self, paper: PaperMetadata) -> Optional[Dict]:
        sid = self._extract_source_id(paper)
        if sid and sid in self.by_source_id:
            return self.by_source_id[sid]
        journal = (paper.journal or paper.venue or "").strip()
        if journal:
            return self._match_by_name(journal)
        return None

    def get_fields_for_paper(self, paper: PaperMetadata) -> List[str]:
        entry = self.get_source_entry(paper)
        if not entry:
            return []
        return [str(f).strip() for f in entry.get("fields", []) if str(f).strip()]

    def get_micro_topics_for_paper(self, paper: PaperMetadata) -> List[str]:
        entry = self.get_source_entry(paper)
        if not entry:
            return []
        return [str(t).strip() for t in entry.get("micro_topics", []) if str(t).strip()]

    def get_source_name_for_paper(self, paper: PaperMetadata) -> Optional[str]:
        entry = self.get_source_entry(paper)
        if not entry:
            return None
        name = str(entry.get("name", "")).strip()
        return name or None
