"""
Unified literature search interface across all sources.

Searches arXiv, Semantic Scholar, and PubMed simultaneously, deduplicates results,
and ranks by relevance.
"""

from typing import List, Optional, Dict, Any, Set
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from collections import defaultdict
import logging

from .base_client import PaperMetadata, PaperSource
from .arxiv_client import ArxivClient
from .pubmed_client import PubMedClient
from .biorxiv_client import BioRxivClient
from core.config import get_config

logger = logging.getLogger(__name__)


class UnifiedLiteratureSearch:
    """
    Unified search interface for all literature sources.

    Coordinates searches across arXiv, Semantic Scholar, and PubMed,
    deduplicates results, and provides ranked output.
    """

    def __init__(
        self,
        arxiv_enabled: bool = True,
        semantic_scholar_enabled: bool = True,
        pubmed_enabled: bool = True,
        biorxiv_enabled: bool = True,
        semantic_scholar_api_key: Optional[str] = None,
        pubmed_api_key: Optional[str] = None,
        pubmed_email: Optional[str] = None
    ):
        """
        Initialize unified search.

        Args:
            arxiv_enabled: Whether to search arXiv
            semantic_scholar_enabled: Whether to search Semantic Scholar (currently disabled)
            pubmed_enabled: Whether to search PubMed
            biorxiv_enabled: Whether to search bioRxiv
            semantic_scholar_api_key: Optional Semantic Scholar API key
            pubmed_api_key: Optional PubMed API key
            pubmed_email: Optional email for PubMed
        """
        self.clients: Dict[PaperSource, Any] = {}

        if arxiv_enabled:
            self.clients[PaperSource.ARXIV] = ArxivClient()

        # Semantic Scholar已移除
        # if semantic_scholar_enabled:
        #     self.clients[PaperSource.SEMANTIC_SCHOLAR] = SemanticScholarClient(
        #         api_key=semantic_scholar_api_key
        #     )

        if pubmed_enabled:
            self.clients[PaperSource.PUBMED] = PubMedClient(
                api_key=pubmed_api_key,
                email=pubmed_email
            )
        
        if biorxiv_enabled:
            self.clients[PaperSource.BIORXIV] = BioRxivClient()

        self.pdf_extractor = None  # PDF提取功能暂时禁用

        # Load timeout config
        config = get_config()
        self.search_timeout = config.literature.search_timeout
        self.pdf_timeout = config.literature.pdf_download_timeout

        logger.info(f"Initialized unified search with {len(self.clients)} sources (timeout={self.search_timeout}s)")

    def search(
        self,
        query: str,
        max_results_per_source: int = 10,
        total_max_results: Optional[int] = None,
        fields: Optional[List[str]] = None,
        year_from: Optional[int] = None,
        year_to: Optional[int] = None,
        deduplicate: bool = True,
        extract_full_text: bool = False,
        sources: Optional[List[PaperSource]] = None,
        **kwargs
    ) -> List[PaperMetadata]:
        """
        Search across all enabled literature sources.

        Args:
            query: Search query
            max_results_per_source: Max results to retrieve from each source
            total_max_results: Total max results to return after deduplication
            fields: Optional filter by research fields
            year_from: Optional start year filter
            year_to: Optional end year filter
            deduplicate: Whether to deduplicate results by DOI/arXiv/title
            extract_full_text: Whether to extract full PDF text for results
            sources: Optional list of specific sources to search (if None, uses all enabled)
            **kwargs: Additional source-specific parameters

        Returns:
            List of PaperMetadata objects, deduplicated and ranked
        """
        # Filter clients if specific sources requested
        search_clients = self.clients
        if sources:
            search_clients = {s: c for s, c in self.clients.items() if s in sources}

        if not search_clients:
            logger.warning("No sources enabled for search")
            return []

        # Search all sources in parallel
        all_papers: List[PaperMetadata] = []

        # Remove max_results from kwargs if present to avoid duplicate argument
        # (max_results_per_source is passed explicitly to _search_source)
        kwargs_filtered = {k: v for k, v in kwargs.items() if k != 'max_results'}

        with ThreadPoolExecutor(max_workers=len(search_clients)) as executor:
            future_to_source = {
                executor.submit(
                    self._search_source,
                    client,
                    source,
                    query,
                    max_results_per_source,
                    fields,
                    year_from,
                    year_to,
                    **kwargs_filtered
                ): source
                for source, client in search_clients.items()
            }

            try:
                for future in as_completed(future_to_source, timeout=self.search_timeout):
                    source = future_to_source[future]
                    try:
                        papers = future.result()
                        all_papers.extend(papers)
                        logger.info(f"Retrieved {len(papers)} papers from {source.value}")
                    except Exception as e:
                        logger.error(f"Error searching {source.value}: {e}")
            except FuturesTimeoutError:
                completed_sources = [s.value for s, c in search_clients.items()
                                     if any(f.done() for f in future_to_source if future_to_source[f] == s)]
                logger.warning(f"Literature search timed out after {self.search_timeout}s. Completed sources: {completed_sources}")

        logger.info(f"Total papers retrieved (before dedup): {len(all_papers)}")

        # Deduplicate if requested
        if deduplicate:
            all_papers = self._deduplicate_papers(all_papers)
            logger.info(f"Papers after deduplication: {len(all_papers)}")

        # Rank papers
        all_papers = self._rank_papers(all_papers, query)

        # Limit total results
        if total_max_results:
            all_papers = all_papers[:total_max_results]

        # Extract full text if requested
        if extract_full_text:
            self._extract_full_text(all_papers, self.pdf_timeout)

        return all_papers

    def search_by_doi(self, doi: str) -> Optional[PaperMetadata]:
        """
        Search for a paper by DOI across all sources.

        Args:
            doi: DOI identifier

        Returns:
            PaperMetadata or None if not found
        """
        # Semantic Scholar已禁用,直接使用PubMed搜索DOI
        if PaperSource.PUBMED in self.clients:
            papers = self.clients[PaperSource.PUBMED].search(f'"{doi}"[DOI]', max_results=1)
            if papers:
                return papers[0]

        return None

    def search_by_arxiv_id(self, arxiv_id: str) -> Optional[PaperMetadata]:
        """
        Search for a paper by arXiv ID.

        Args:
            arxiv_id: arXiv identifier (e.g., "2103.00020")

        Returns:
            PaperMetadata or None if not found
        """
        # Try arXiv first
        if PaperSource.ARXIV in self.clients:
            paper = self.clients[PaperSource.ARXIV].get_paper_by_id(arxiv_id)
            if paper:
                return paper

        # Semantic Scholar已禁用
        return None

    def get_citations(
        self,
        paper: PaperMetadata,
        max_citations: int = 50
    ) -> List[PaperMetadata]:
        """
        Get papers that cite the given paper.

        Args:
            paper: Paper to find citations for
            max_citations: Maximum number of citations to return

        Returns:
            List of citing papers
        """
        # Semantic Scholar已禁用
        # 直接使用PubMed获取引用
        if PaperSource.PUBMED in self.clients and paper.pubmed_id:
            return self.clients[PaperSource.PUBMED].get_paper_citations(
                paper.pubmed_id, max_citations
            )

        logger.warning(f"Could not retrieve citations for paper {paper.id}")
        return []

    def get_references(
        self,
        paper: PaperMetadata,
        max_references: int = 50
    ) -> List[PaperMetadata]:
        """
        Get papers referenced by the given paper.

        Args:
            paper: Paper to find references for
            max_references: Maximum number of references to return

        Returns:
            List of referenced papers
        """
        # Semantic Scholar已禁用,直接使用PubMed
        if PaperSource.PUBMED in self.clients and paper.pubmed_id:
            return self.clients[PaperSource.PUBMED].get_paper_references(
                paper.pubmed_id, max_references
            )

        logger.warning(f"Could not retrieve references for paper {paper.id}")
        return []

    def _search_source(
        self,
        client: Any,
        source: PaperSource,
        query: str,
        max_results: int,
        fields: Optional[List[str]],
        year_from: Optional[int],
        year_to: Optional[int],
        **kwargs
    ) -> List[PaperMetadata]:
        """
        Search a single source.

        Args:
            client: Literature client
            source: Paper source
            query: Search query
            max_results: Max results
            fields: Optional fields filter
            year_from: Start year
            year_to: End year
            **kwargs: Additional parameters

        Returns:
            List of papers from this source
        """
        try:
            # Remove max_results from kwargs if present to avoid duplicate argument
            kwargs_filtered = {k: v for k, v in kwargs.items() if k != 'max_results'}
            return client.search(
                query=query,
                max_results=max_results,
                fields=fields,
                year_from=year_from,
                year_to=year_to,
                **kwargs_filtered
            )
        except Exception as e:
            logger.error(f"Error searching {source.value}: {e}")
            return []

    def _deduplicate_papers(self, papers: List[PaperMetadata]) -> List[PaperMetadata]:
        """
        Deduplicate papers by DOI, arXiv ID, PubMed ID, or title similarity.

        Priority: DOI > arXiv > PubMed > Title

        Args:
            papers: List of papers (may contain duplicates)

        Returns:
            Deduplicated list of papers
        """
        seen_dois: Set[str] = set()
        seen_arxiv: Set[str] = set()
        seen_pubmed: Set[str] = set()
        seen_titles: Set[str] = set()

        unique_papers = []

        for paper in papers:
            # Check DOI
            if paper.doi:
                doi_norm = paper.doi.lower().strip()
                if doi_norm in seen_dois:
                    continue
                seen_dois.add(doi_norm)
                unique_papers.append(paper)
                continue

            # Check arXiv ID
            if paper.arxiv_id:
                arxiv_norm = paper.arxiv_id.lower().strip()
                if arxiv_norm in seen_arxiv:
                    continue
                seen_arxiv.add(arxiv_norm)
                unique_papers.append(paper)
                continue

            # Check PubMed ID
            if paper.pubmed_id:
                pubmed_norm = paper.pubmed_id.lower().strip()
                if pubmed_norm in seen_pubmed:
                    continue
                seen_pubmed.add(pubmed_norm)
                unique_papers.append(paper)
                continue

            # Check title similarity (fuzzy match)
            # Skip papers without titles - they can't be properly deduplicated
            if not paper.title:
                continue
            title_norm = self._normalize_title(paper.title)
            if title_norm in seen_titles:
                continue
            seen_titles.add(title_norm)
            unique_papers.append(paper)

        return unique_papers

    def _normalize_title(self, title: str) -> str:
        """
        Normalize title for fuzzy matching.

        Args:
            title: Paper title

        Returns:
            Normalized title (empty string if title is None)
        """
        if not title:
            return ""
        import re
        # Lowercase, remove punctuation, extra spaces
        title = title.lower()
        title = re.sub(r'[^\w\s]', '', title)
        title = re.sub(r'\s+', ' ', title)
        return title.strip()

    def _rank_papers(self, papers: List[PaperMetadata], query: str) -> List[PaperMetadata]:
        """
        Rank papers by relevance.

        Uses a simple scoring based on:
        - Citation count (Semantic Scholar)
        - Title/abstract relevance to query
        - Publication date (more recent = higher)

        Args:
            papers: List of papers
            query: Original search query

        Returns:
            Ranked list of papers
        """
        query_terms = set(query.lower().split())

        def score_paper(paper: PaperMetadata) -> float:
            # Skip None papers
            if paper is None:
                return 0.0

            score = 0.0

            # Citation score (normalized, max 100 points)
            if paper.citation_count and paper.citation_count > 0:
                score += min(paper.citation_count / 10.0, 100.0)

            # Title relevance (max 50 points)
            if paper.title:
                title_terms = set(paper.title.lower().split())
                title_overlap = len(query_terms & title_terms) / max(len(query_terms), 1)
                score += title_overlap * 50.0

            # Abstract relevance (max 30 points)
            if paper.abstract:
                abstract_terms = set(paper.abstract.lower().split())
                abstract_overlap = len(query_terms & abstract_terms) / max(len(query_terms), 1)
                score += abstract_overlap * 30.0

            # Recency score (max 20 points, last 5 years)
            if paper.year:
                from datetime import datetime
                current_year = datetime.now().year
                years_ago = current_year - paper.year
                if years_ago <= 5:
                    score += (5 - years_ago) * 4.0

            return score

        papers_with_scores = [(paper, score_paper(paper)) for paper in papers]
        papers_with_scores.sort(key=lambda x: x[1], reverse=True)

        return [paper for paper, _ in papers_with_scores]

    def _extract_full_text(self, papers: List[PaperMetadata], pdf_timeout: int = 30):
        """
        Extract full text for papers with PDF URLs.

        Modifies papers in-place.

        Args:
            papers: List of papers to extract text for
            pdf_timeout: Timeout per paper extraction in seconds (default: 30s)
        """
        for paper in papers:
            if paper.pdf_url and not paper.full_text:
                try:
                    with ThreadPoolExecutor(max_workers=1) as executor:
                        future = executor.submit(self.pdf_extractor.extract_paper_text, paper)
                        future.result(timeout=pdf_timeout)
                except FuturesTimeoutError:
                    logger.warning(f"PDF extraction timed out after {pdf_timeout}s for {paper.id}")
                except Exception as e:
                    logger.warning(f"Could not extract PDF for {paper.id}: {e}")
