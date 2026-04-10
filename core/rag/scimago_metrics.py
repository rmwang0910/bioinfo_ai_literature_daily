import csv
import logging
import re
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class ScimagoMetrics:
    def __init__(self, csv_path: str):
        self.csv_path = csv_path
        self.by_name: Dict[str, Dict] = {}
        self.by_issn: Dict[str, Dict] = {}
        self._load_csv()

    def _normalize(self, text: str) -> str:
        text = (text or "").strip().lower()
        text = re.sub(r"[^a-z0-9]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def _normalize_header(self, text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", (text or "").lower())

    def _detect_delimiter(self, sample: str) -> str:
        try:
            return csv.Sniffer().sniff(sample).delimiter
        except Exception:
            return ","

    def _pick_header(self, headers: Dict[str, str], candidates: Tuple[str, ...]) -> Optional[str]:
        for cand in candidates:
            if cand in headers:
                return headers[cand]
        return None

    def _find_header(self, headers: Dict[str, str], predicate) -> Optional[str]:
        for norm, original in headers.items():
            if predicate(norm):
                return original
        return None

    def _parse_number(self, value: str) -> Optional[float]:
        if value is None:
            return None
        val = str(value).strip()
        if not val:
            return None
        if "," in val and "." not in val:
            val = val.replace(",", ".")
        else:
            val = val.replace(",", "")
        try:
            return float(val)
        except Exception:
            return None

    def _load_csv(self):
        path = Path(self.csv_path)
        if not path.exists():
            path = Path(__file__).parent.parent.parent / self.csv_path
        if not path.exists():
            logger.warning(f"Scimago CSV not found: {path}")
            return
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                sample = f.read(4096)
                f.seek(0)
                delimiter = self._detect_delimiter(sample)
                reader = csv.DictReader(f, delimiter=delimiter)
                headers = {self._normalize_header(h): h for h in reader.fieldnames or []}

                title_key = self._pick_header(
                    headers,
                    ("title", "journaltitle", "source", "sourcetitle", "journal"),
                )
                issn_key = self._pick_header(headers, ("issn", "issn1", "issn2", "issnprint", "issnprint1"))
                sjr_key = self._pick_header(headers, ("sjr", "sjrscore"))
                cites_key = self._find_header(
                    headers,
                    lambda h: ("cites" in h or "citation" in h) and "doc" in h and ("2" in h or "2y" in h),
                )

                for row in reader:
                    name = (row.get(title_key) or "").strip()
                    if not name:
                        continue
                    issn_raw = (row.get(issn_key) or "").strip()
                    sjr_val = self._parse_number(row.get(sjr_key))
                    cites_val = self._parse_number(row.get(cites_key))
                    metrics = {
                        "name": name,
                        "issn": issn_raw,
                        "sjr": sjr_val,
                        "cites_per_doc_2y": cites_val,
                    }
                    self.by_name[self._normalize(name)] = metrics
                    if issn_raw:
                        for part in re.split(r"[;,/\\s]+", issn_raw):
                            part = part.strip()
                            if part:
                                self.by_issn[part] = metrics

            logger.info(f"Loaded Scimago metrics from {path}")
        except Exception as e:
            logger.warning(f"Failed to load Scimago CSV: {e}")

    # 常见期刊缩写→全称映射（用于缩写无法子串匹配时的回退）
    _ABBREV_MAP = {
        "int j surg": "international journal of surgery",
        "pnas": "proceedings of the national academy of sciences of the united states of america",
        "bmj": "british medical journal",
        "jama": "jama journal of the american medical association",
        "nejm": "new england journal of medicine",
        "lancet": "the lancet",
        "nat med": "nature medicine",
        "nat commun": "nature communications",
        "nat genet": "nature genetics",
        "nat methods": "nature methods",
        "nat biotechnol": "nature biotechnology",
        "nat cell biol": "nature cell biology",
        "nat rev genet": "nature reviews genetics",
        "nat mach intell": "nature machine intelligence",
        "nat comput sci": "nature computational science",
        "sci adv": "science advances",
        "cell rep": "cell reports",
        "cell syst": "cell systems",
        "nucleic acids res": "nucleic acids research",
        "genome res": "genome research",
        "genome biol": "genome biology",
        "brief bioinform": "briefings in bioinformatics",
        "bmc bioinformatics": "bmc bioinformatics",
        "plos comput biol": "plos computational biology",
        "plos biol": "plos biology",
        "j comput biol": "journal of computational biology",
        "bioinformatics": "bioinformatics",
        "front immunol": "frontiers in immunology",
        "front oncol": "frontiers in oncology",
        "front genet": "frontiers in genetics",
        "front cell dev biol": "frontiers in cell and developmental biology",
        "j clin invest": "journal of clinical investigation",
        "j exp med": "journal of experimental medicine",
        "embo j": "embo journal",
        "mol cell": "molecular cell",
        "mol syst biol": "molecular systems biology",
        "iscience": "iscience",
        "adv sci": "advanced science",
        "signal transduct target ther": "signal transduction and targeted therapy",
    }

    def get_metrics(self, journal_name: str, issn: Optional[str] = None) -> Optional[Dict]:
        if issn and issn in self.by_issn:
            return self.by_issn[issn]
        norm = self._normalize(journal_name or "")
        if not norm:
            return None
        # 精确匹配
        if norm in self.by_name:
            return self.by_name[norm]
        # 缩写映射回退
        if norm in self._ABBREV_MAP:
            full = self._normalize(self._ABBREV_MAP[norm])
            if full in self.by_name:
                return self.by_name[full]
        # 子串匹配：要求双方长度差不超过 2 倍，且匹配方长度 >= 6，避免短名误匹配
        for key, value in self.by_name.items():
            if len(key) < 6 and len(norm) < 6:
                continue
            if norm in key and len(key) <= len(norm) * 2:
                return value
            if key in norm and len(norm) <= len(key) * 2:
                return value
        return None

    def get_impact_factor(self, journal_name: str, issn: Optional[str] = None, metric: str = "cites_per_doc_2y") -> Tuple[Optional[float], Optional[str]]:
        metrics = self.get_metrics(journal_name, issn)
        if not metrics:
            return None, None
        if metric == "sjr" and metrics.get("sjr") is not None:
            return metrics.get("sjr"), "SJR"
        if metrics.get("cites_per_doc_2y") is not None:
            return metrics.get("cites_per_doc_2y"), "Cites/Doc(2y)"
        if metrics.get("sjr") is not None:
            return metrics.get("sjr"), "SJR"
        return None, None
