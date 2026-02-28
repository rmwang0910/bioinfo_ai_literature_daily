"""
独立文献搜索模块
支持 PubMed 和 arXiv 等多源检索
"""

from .base_client import PaperMetadata, PaperSource, Author, BaseLiteratureClient
from .pubmed_client import PubMedClient
from .arxiv_client import ArxivClient
from .biorxiv_client import BioRxivClient
from .unified_search import UnifiedLiteratureSearch

__all__ = [
    'PaperMetadata',
    'PaperSource',
    'Author',
    'BaseLiteratureClient',
    'PubMedClient',
    'ArxivClient',
    'BioRxivClient',
    'UnifiedLiteratureSearch',
]
