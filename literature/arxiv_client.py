"""
arXiv API client for searching and retrieving scientific papers.

Uses the official arxiv Python package with caching support.
Note: The arxiv package may have compatibility issues with Python 3.11+
due to sgmllib3k dependency. This module includes fallback handling.
"""

from typing import List, Optional
from datetime import datetime
import logging

# Handle arxiv import with fallback for Python 3.11+ compatibility
try:
    import arxiv
    HAS_ARXIV = True
except ImportError as e:
    HAS_ARXIV = False
    arxiv = None
    logging.warning(
        f"arxiv package not available: {e}. "
        "arXiv search functionality will be limited. "
        "Consider using Semantic Scholar as an alternative."
    )

from .base_client import (
    BaseLiteratureClient,
    PaperMetadata,
    PaperSource,
    Author
)
from core.config import get_config


class ArxivClient(BaseLiteratureClient):
    """
    Client for interacting with the arXiv API.

    Uses the official arxiv Python package:
    https://github.com/lukasschwab/arxiv.py
    """

    def __init__(self, api_key: Optional[str] = None, cache_enabled: bool = True):
        """
        Initialize the arXiv client.

        Args:
            api_key: Not used for arXiv (public API), kept for interface consistency
            cache_enabled: Whether to enable caching for API responses

        Raises:
            RuntimeError: If arxiv package is not available
        """
        super().__init__(api_key=api_key, cache_enabled=cache_enabled)

        # Check if arxiv is available
        if not HAS_ARXIV:
            self.logger.warning(
                "arxiv package not available. ArxivClient will return empty results. "
                "Use SemanticScholarClient as an alternative."
            )
            self.client = None
            self.max_results = 10
            self.cache = None
            return

        # Get configuration
        config = get_config()
        self.max_results = config.literature.max_results_per_query

        # Initialize cache if enabled
        self.cache = None  # Cache功能暂时禁用

        # Configure arxiv client
        self.client = arxiv.Client(
            page_size=100,  # Max results per page
            delay_seconds=3.0,  # Rate limiting: 3 seconds between requests
            num_retries=3
        )

        self.logger.info("Initialized arXiv client")

    def search(
        self,
        query: str,
        max_results: int = 10,
        fields: Optional[List[str]] = None,
        year_from: Optional[int] = None,
        year_to: Optional[int] = None,
        **kwargs
    ) -> List[PaperMetadata]:
        """
        Search for papers on arXiv.

        Args:
            query: Search query (can use arXiv query syntax)
            max_results: Maximum number of results to return
            fields: Optional filter by categories (e.g., ["cs.AI", "cs.LG"])
            year_from: Optional start year filter
            year_to: Optional end year filter
            **kwargs: Additional options:
                - sort_by: arxiv.SortCriterion (Relevance, LastUpdatedDate, SubmittedDate)
                - sort_order: arxiv.SortOrder (Ascending, Descending)

        Returns:
            List of PaperMetadata objects
        """
        if not self._validate_query(query):
            return []

        # Return empty if arxiv not available
        if not HAS_ARXIV or self.client is None:
            self.logger.warning("arxiv package not available, returning empty results")
            return []

        # Check cache
        cache_params = {
            "query": query,
            "max_results": max_results,
            "fields": fields,
            "year_from": year_from,
            "year_to": year_to
        }

        if self.cache:
            cached_result = self.cache.get("arxiv", "search", cache_params)
            if cached_result is not None:
                return cached_result

        try:
            # Build query with filters
            search_query = self._build_query(query, fields, year_from, year_to)

            # Get sort parameters
            sort_by = kwargs.get("sort_by", arxiv.SortCriterion.Relevance)
            sort_order = kwargs.get("sort_order", arxiv.SortOrder.Descending)

            # Create search
            # 使用传入的 max_results，arXiv API 没有硬性上限，但通常建议不超过 2000
            effective_max = min(max_results, 2000)  # arXiv API 建议上限
            search = arxiv.Search(
                query=search_query,
                max_results=effective_max,
                sort_by=sort_by,
                sort_order=sort_order
            )

            # Execute search
            results = self.client.results(search)

            # Convert to PaperMetadata
            papers = [self._arxiv_to_metadata(result) for result in results]

            # Filter by year if specified (arXiv doesn't support year in query directly)
            if year_from or year_to:
                filtered_papers = []
                for paper in papers:
                    if paper.year:
                        if year_from and paper.year < year_from:
                            continue
                        if year_to and paper.year > year_to:
                            continue
                    filtered_papers.append(paper)
                papers = filtered_papers

            # Cache results
            if self.cache:
                self.cache.set("arxiv", "search", cache_params, papers)
            
            # 使用WARNING级别，便于用户在精简日志模式下感知检索规模
            self.logger.warning(f"Found {len(papers)} papers on arXiv for query: {query}")
            return papers

        except Exception as e:
            self._handle_api_error(e, f"search query='{query}'")
            return []

    def get_paper_by_id(self, paper_id: str) -> Optional[PaperMetadata]:
        """
        Retrieve a specific paper by arXiv ID.

        Args:
            paper_id: arXiv ID (e.g., "2103.00020" or "arXiv:2103.00020")

        Returns:
            PaperMetadata object or None if not found
        """
        # Remove "arXiv:" prefix if present
        paper_id = paper_id.replace("arXiv:", "").strip()

        # Return None if arxiv not available
        if not HAS_ARXIV or self.client is None:
            self.logger.warning("arxiv package not available, cannot retrieve paper")
            return None

        # Check cache
        cache_params = {"paper_id": paper_id}

        if self.cache:
            cached_result = self.cache.get("arxiv", "get_paper", cache_params)
            if cached_result is not None:
                return cached_result

        try:
            # Use id_list parameter for direct ID lookup
            search = arxiv.Search(id_list=[paper_id])
            results = list(self.client.results(search))

            if not results:
                self.logger.warning(f"Paper not found: {paper_id}")
                return None

            paper = self._arxiv_to_metadata(results[0])

            # Cache result
            if self.cache:
                self.cache.set("arxiv", "get_paper", cache_params, paper)

            return paper

        except Exception as e:
            self._handle_api_error(e, f"get_paper_by_id id={paper_id}")
            return None

    def get_paper_references(self, paper_id: str, max_refs: int = 50) -> List[PaperMetadata]:
        """
        Get papers cited by the given paper.

        Note: arXiv API doesn't provide citation information directly.
        This method returns an empty list.

        For citation data, use Semantic Scholar API instead.

        Args:
            paper_id: arXiv ID
            max_refs: Maximum number of references (unused)

        Returns:
            Empty list (arXiv doesn't provide citations)
        """
        self.logger.warning("arXiv API does not provide citation data. Use Semantic Scholar instead.")
        return []

    def get_paper_citations(self, paper_id: str, max_cites: int = 50) -> List[PaperMetadata]:
        """
        Get papers that cite the given paper.

        Note: arXiv API doesn't provide citation information directly.
        This method returns an empty list.

        For citation data, use Semantic Scholar API instead.

        Args:
            paper_id: arXiv ID
            max_cites: Maximum number of citations (unused)

        Returns:
            Empty list (arXiv doesn't provide citations)
        """
        self.logger.warning("arXiv API does not provide citation data. Use Semantic Scholar instead.")
        return []

    def _build_query(
        self,
        query: str,
        fields: Optional[List[str]] = None,
        year_from: Optional[int] = None,
        year_to: Optional[int] = None
    ) -> str:
        """
        Build arXiv query with filters.

        Args:
            query: Base query string
            fields: arXiv categories to filter by
            year_from: Start year
            year_to: End year

        Returns:
            Formatted query string
        """
        query_parts = [query]

        # Add category filters
        if fields:
            category_queries = [f"cat:{field}" for field in fields]
            query_parts.append(f"({' OR '.join(category_queries)})")

        # Note: arXiv search doesn't support year range in query directly
        # Year filtering would need to be done post-search on submittedDate
        # We'll accept it as a parameter for interface consistency but note limitation

        return " AND ".join(query_parts)

    def _arxiv_to_metadata(self, result: arxiv.Result) -> PaperMetadata:
        """
        Convert arxiv.Result to PaperMetadata.

        Args:
            result: arxiv.Result object

        Returns:
            PaperMetadata object
        """
        # Extract arXiv ID (remove version if present)
        arxiv_id = result.entry_id.split("/")[-1].split("v")[0]

        # Convert authors
        authors = [
            Author(name=author.name)
            for author in result.authors
        ]

        # Extract publication year
        year = result.published.year if result.published else None

        # Extract fields (categories)
        fields = [cat.lower() for cat in result.categories]

        return PaperMetadata(
            id=arxiv_id,
            source=PaperSource.ARXIV,
            doi=result.doi,
            arxiv_id=arxiv_id,
            title=result.title,
            abstract=result.summary,
            authors=authors,
            publication_date=result.published,
            journal=result.journal_ref,
            year=year,
            url=result.entry_id,
            pdf_url=result.pdf_url,
            fields=fields,
            raw_data={
                "entry_id": result.entry_id,
                "updated": result.updated.isoformat() if result.updated else None,
                "comment": result.comment,
                "primary_category": result.primary_category
            }
        )

    def get_categories(self) -> List[str]:
        """
        Get list of arXiv categories.

        Returns:
            List of category codes

        Common categories:
            - cs.AI: Artificial Intelligence
            - cs.CL: Computation and Language
            - cs.CV: Computer Vision
            - cs.LG: Machine Learning
            - physics.gen-ph: General Physics
            - q-bio: Quantitative Biology
            - astro-ph: Astrophysics
        """
        # https://arxiv.org/category_taxonomy
        return [
            "cs.AI", "cs.CL", "cs.CV", "cs.LG", "cs.NE", "cs.RO",
            "physics.gen-ph", "physics.comp-ph", "quant-ph",
            "q-bio.BM", "q-bio.GN", "q-bio.NC", "q-bio.QM",
            "astro-ph", "cond-mat", "math.ST", "stat.ML"
        ]
