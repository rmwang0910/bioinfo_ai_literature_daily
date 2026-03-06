"""
Abstract base class for literature API clients.

Provides common interface and functionality for arXiv, Semantic Scholar, PubMed, etc.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class PaperSource(str, Enum):
    """Source of the paper."""
    ARXIV = "arxiv"
    SEMANTIC_SCHOLAR = "semantic_scholar"
    PUBMED = "pubmed"
    BIORXIV = "biorxiv"
    UNKNOWN = "unknown"
    MANUAL = "manual"


@dataclass
class Author:
    """Author information."""
    name: str
    affiliation: Optional[str] = None
    email: Optional[str] = None
    author_id: Optional[str] = None  # Author ID from source API


@dataclass
class PaperMetadata:
    """
    Unified paper metadata across all literature sources.

    This standardized format ensures consistent handling of papers
    regardless of source API.
    """
    # Identifiers
    id: str  # Unique ID from source
    source: PaperSource
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None
    pubmed_id: Optional[str] = None

    # Core metadata
    title: str = ""
    abstract: str = ""
    authors: List[Author] = None

    # Publication info
    publication_date: Optional[datetime] = None
    journal: Optional[str] = None
    venue: Optional[str] = None
    year: Optional[int] = None

    # Links & Resources
    url: Optional[str] = None
    pdf_url: Optional[str] = None
    
    # Open Access info
    is_open_access: bool = False
    open_access_url: Optional[str] = None
    
    # Citations & Influence
    citation_count: int = 0
    reference_count: int = 0
    influential_citation_count: int = 0

    # Fields & Keywords
    fields: List[str] = None  # Research fields/domains
    keywords: List[str] = None
    
    # Institutions
    institutions: List[str] = None

    # Full text (if downloaded)
    full_text: Optional[str] = None

    # Raw response from API (for debugging)
    raw_data: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        """Initialize default values for mutable fields."""
        if self.authors is None:
            self.authors = []
        if self.fields is None:
            self.fields = []
        if self.keywords is None:
            self.keywords = []
        if self.institutions is None:
            self.institutions = []

    @property
    def primary_identifier(self) -> str:
        """Get the primary identifier (DOI > arXiv > PubMed > source ID)."""
        return self.doi or self.arxiv_id or self.pubmed_id or self.id

    @property
    def author_names(self) -> List[str]:
        """Get list of author names."""
        return [author.name for author in self.authors]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for database storage."""
        return {
            "id": self.id,
            "source": self.source.value,
            "doi": self.doi,
            "arxiv_id": self.arxiv_id,
            "pubmed_id": self.pubmed_id,
            "title": self.title,
            "abstract": self.abstract,
            "authors": [{"name": a.name, "affiliation": a.affiliation} for a in self.authors],
            "publication_date": self.publication_date.isoformat() if self.publication_date else None,
            "journal": self.journal,
            "venue": self.venue,
            "year": self.year,
            "url": self.url,
            "pdf_url": self.pdf_url,
            "citation_count": self.citation_count,
            "reference_count": self.reference_count,
            "influential_citation_count": self.influential_citation_count,
            "fields": self.fields,
            "keywords": self.keywords,
            "full_text": self.full_text
        }


class BaseLiteratureClient(ABC):
    """
    Abstract base class for literature API clients.

    All literature clients (arXiv, Semantic Scholar, PubMed) should inherit
    from this class and implement the required methods.
    """

    def __init__(self, api_key: Optional[str] = None, cache_enabled: bool = True):
        """
        Initialize the literature client.

        Args:
            api_key: Optional API key for the service
            cache_enabled: Whether to enable caching for API responses
        """
        self.api_key = api_key
        self.cache_enabled = cache_enabled
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    @abstractmethod
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
        Search for papers matching the query.

        Args:
            query: Search query string
            max_results: Maximum number of results to return
            fields: Optional filter by research fields/domains
            year_from: Optional start year for publication date filter
            year_to: Optional end year for publication date filter
            **kwargs: Additional source-specific parameters

        Returns:
            List of PaperMetadata objects
        """
        pass

    @abstractmethod
    def get_paper_by_id(self, paper_id: str) -> Optional[PaperMetadata]:
        """
        Retrieve a specific paper by its ID.

        Args:
            paper_id: Paper identifier (source-specific format)

        Returns:
            PaperMetadata object or None if not found
        """
        pass

    @abstractmethod
    def get_paper_references(self, paper_id: str, max_refs: int = 50) -> List[PaperMetadata]:
        """
        Get papers cited by the given paper.

        Args:
            paper_id: Paper identifier
            max_refs: Maximum number of references to return

        Returns:
            List of PaperMetadata objects for referenced papers
        """
        pass

    @abstractmethod
    def get_paper_citations(self, paper_id: str, max_cites: int = 50) -> List[PaperMetadata]:
        """
        Get papers that cite the given paper.

        Args:
            paper_id: Paper identifier
            max_cites: Maximum number of citations to return

        Returns:
            List of PaperMetadata objects for citing papers
        """
        pass

    def get_source_name(self) -> str:
        """
        Get the name of this literature source.

        Returns:
            Source name (e.g., "arXiv", "Semantic Scholar")
        """
        return self.__class__.__name__.replace("Client", "")

    def _handle_api_error(self, error: Exception, operation: str):
        """
        Handle API errors with consistent logging.

        Args:
            error: The exception that occurred
            operation: Description of the operation being performed
        """
        self.logger.error(
            f"Error in {self.get_source_name()} API during {operation}: {str(error)}",
            exc_info=True
        )
        # Could add retry logic, circuit breaker, etc. here

    def _validate_query(self, query: str) -> bool:
        """
        Validate search query.

        Args:
            query: Search query string

        Returns:
            True if valid, False otherwise
        """
        if not query or not query.strip():
            self.logger.warning("Empty query provided")
            return False

        if len(query) > 1000:
            self.logger.warning(f"Query too long ({len(query)} chars), truncating to 1000")
            return True

        return True

    def _normalize_paper_metadata(self, raw_data: Dict[str, Any]) -> PaperMetadata:
        """
        Convert source-specific response to standardized PaperMetadata.

        This method should be overridden by each client to handle their
        specific response format.

        Args:
            raw_data: Raw API response data

        Returns:
            Standardized PaperMetadata object
        """
        raise NotImplementedError("Subclasses must implement _normalize_paper_metadata")
