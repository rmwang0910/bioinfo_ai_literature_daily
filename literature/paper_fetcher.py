"""
单篇文献获取工具

支持按 PMID / DOI / Title 获取单篇文献元数据。
自动识别输入类型，依次尝试 PubMed、bioRxiv、arXiv 和 OpenAlex。
"""
from __future__ import annotations

import re
import logging
import requests
from typing import Optional

from .base_client import PaperMetadata, Author
from .pubmed_client import PubMedClient
from .openalex_client import OpenAlexClient

logger = logging.getLogger(__name__)


def detect_id_type(query: str) -> str:
    """自动判断输入类型: pmid / doi / title"""
    q = query.strip()
    if re.match(r'^\d{6,8}$', q):
        return 'pmid'
    elif re.match(r'^10\.\d{4,}/', q):
        return 'doi'
    else:
        return 'title'


class SinglePaperFetcher:
    """按 PMID / DOI / Title 获取单篇文献"""

    def __init__(self):
        self.pubmed = PubMedClient()
        self.openalex = OpenAlexClient()

    def fetch(self, query: str) -> Optional[PaperMetadata]:
        """
        获取单篇文献元数据

        Args:
            query: PMID（6-8位纯数字）、DOI（10.xxx/...）或标题文本

        Returns:
            PaperMetadata 或 None
        """
        id_type = detect_id_type(query.strip())
        logger.info(f"识别输入类型: {id_type} | 内容: {query[:60]}")

        paper = None
        if id_type == 'pmid':
            paper = self._fetch_by_pmid(query.strip())
        elif id_type == 'doi':
            paper = self._fetch_by_doi(query.strip())
        else:
            paper = self._fetch_by_title(query.strip())

        if not paper:
            return None

        # 用 OpenAlex 补全元数据（OA URL、引用数、机构等）
        try:
            self.openalex.enrich_paper(paper)
        except Exception as e:
            logger.warning(f"OpenAlex 补全失败（不影响主流程）: {e}")

        return paper

    def _fetch_by_pmid(self, pmid: str) -> Optional[PaperMetadata]:
        paper = self.pubmed.get_paper_by_id(pmid)
        if not paper:
            logger.warning(f"PubMed 未找到 PMID: {pmid}")
        return paper

    def _fetch_by_doi(self, doi: str) -> Optional[PaperMetadata]:
        # PubMed doi[aid] 字段搜索
        results = self.pubmed.search(f'"{doi}"[aid]', max_results=1)
        if results:
            return results[0]

        # OpenAlex 查 DOI → 提取 PMID → 回到 PubMed
        try:
            work = self.openalex._get_work_by_id(f"doi:{doi}")
            if work:
                pmid = (
                    work.get('ids', {})
                    .get('pmid', '')
                    .replace('https://pubmed.ncbi.nlm.nih.gov/', '')
                    .rstrip('/')
                )
                if pmid:
                    return self._fetch_by_pmid(pmid)
        except Exception as e:
            logger.warning(f"OpenAlex DOI 查询失败: {e}")

        logger.warning(f"未能通过 DOI 找到文献: {doi}")
        return None

    def _fetch_by_title(self, title: str) -> Optional[PaperMetadata]:
        # 1. PubMed 精确搜索
        results = self.pubmed.search(f'"{title}"[ti]', max_results=1)
        if results:
            return results[0]
        # 放宽：不加引号
        results = self.pubmed.search(f'{title}[ti]', max_results=1)
        if results:
            return results[0]

        # 2. bioRxiv/medRxiv 搜索（很多新论文是预印本）
        paper = self._search_biorxiv_by_title(title)
        if paper:
            logger.info(f"bioRxiv 找到: {paper.title[:50]}")
            return paper

        # 3. arXiv 搜索
        paper = self._search_arxiv_by_title(title)
        if paper:
            logger.info(f"arXiv 找到: {paper.title[:50]}")
            return paper

        # 4. OpenAlex 标题搜索（覆盖最广）
        paper = self._search_openalex_by_title(title)
        if paper:
            logger.info(f"OpenAlex 找到: {paper.title[:50]}")
            return paper

        logger.warning(f"未能通过标题找到文献: {title[:60]}")
        return None

    def _search_biorxiv_by_title(self, title: str) -> Optional[PaperMetadata]:
        """bioRxiv/medRxiv 标题搜索"""
        try:
            from .biorxiv_client import BioRxivClient
            client = BioRxivClient()
            # 用标题关键词搜索
            keywords = ' '.join(title.split()[:6])  # 取前6个词
            results = client.search(keywords, max_results=5)
            # 模糊匹配标题
            title_lower = title.lower()
            for r in results:
                if self._title_similar(r.title, title):
                    return r
        except Exception as e:
            logger.debug(f"bioRxiv 搜索失败: {e}")
        return None

    def _search_arxiv_by_title(self, title: str) -> Optional[PaperMetadata]:
        """arXiv 标题搜索"""
        try:
            from .arxiv_client import ArxivClient
            client = ArxivClient()
            # arXiv 搜索语法：ti:关键词
            keywords = ' '.join(title.split()[:6])
            results = client.search(f'ti:{keywords}', max_results=5)
            for r in results:
                if self._title_similar(r.title, title):
                    return r
        except Exception as e:
            logger.debug(f"arXiv 搜索失败: {e}")
        return None

    def _search_openalex_by_title(self, title: str) -> Optional[PaperMetadata]:
        """OpenAlex 标题搜索"""
        try:
            # OpenAlex search API
            url = "https://api.openalex.org/works"
            params = {
                "filter": f"title.search:{title}",
                "per_page": 5
            }
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code != 200:
                return None
            data = resp.json()
            results = data.get("results", [])
            for work in results:
                work_title = work.get("title", "")
                if self._title_similar(work_title, title):
                    return self._openalex_work_to_paper(work)
        except Exception as e:
            logger.debug(f"OpenAlex 标题搜索失败: {e}")
        return None

    def _title_similar(self, t1: str, t2: str) -> bool:
        """简单标题相似度判断（忽略大小写和标点）"""
        import re
        def normalize(s):
            return re.sub(r'[^a-z0-9\s]', '', s.lower()).split()
        words1 = set(normalize(t1))
        words2 = set(normalize(t2))
        if not words1 or not words2:
            return False
        # 交集 / 较小集合 > 0.7
        overlap = len(words1 & words2) / min(len(words1), len(words2))
        return overlap > 0.7

    def _openalex_work_to_paper(self, work: dict) -> PaperMetadata:
        """将 OpenAlex work 转换为 PaperMetadata"""
        # 提取作者
        authors = []
        for auth in work.get("authorships", [])[:10]:
            name = auth.get("author", {}).get("display_name", "")
            if name:
                authors.append(Author(name=name))

        # 提取 DOI
        doi = work.get("doi", "")
        if doi:
            doi = doi.replace("https://doi.org/", "")

        # 提取 PMID
        pmid = work.get("ids", {}).get("pmid", "")
        if pmid:
            pmid = pmid.replace("https://pubmed.ncbi.nlm.nih.gov/", "").rstrip("/")

        # 提取期刊
        journal = ""
        source = work.get("primary_location", {}).get("source", {})
        if source:
            journal = source.get("display_name", "")

        # 提取摘要（OpenAlex 用 inverted index 存储，需要还原）
        abstract = ""
        abstract_inv = work.get("abstract_inverted_index", {})
        if abstract_inv:
            # 还原 inverted index 为文本
            word_positions = []
            for word, positions in abstract_inv.items():
                for pos in positions:
                    word_positions.append((pos, word))
            word_positions.sort()
            abstract = " ".join(w for _, w in word_positions)

        return PaperMetadata(
            id=work.get("id", ""),
            title=work.get("title", ""),
            authors=authors,
            abstract=abstract,
            journal=journal,
            doi=doi,
            pubmed_id=pmid,
            publication_date=work.get("publication_date", ""),
            year=work.get("publication_year"),
            citation_count=work.get("cited_by_count", 0),
            is_open_access=work.get("open_access", {}).get("is_oa", False),
            open_access_url=work.get("open_access", {}).get("oa_url"),
            source="openalex",
            raw_data=work
        )
