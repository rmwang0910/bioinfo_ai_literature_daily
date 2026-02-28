"""
bioRxiv API client for searching and retrieving preprints.

bioRxiv provides RSS feeds and a REST API for accessing preprints.
API Documentation: https://api.biorxiv.org/
"""

from typing import List, Optional
from datetime import datetime
import logging
import requests
import time
import re

try:
    import feedparser
    HAS_FEEDPARSER = True
except ImportError:
    HAS_FEEDPARSER = False
    logging.warning("feedparser not available. Install with: pip install feedparser")

from .base_client import (
    BaseLiteratureClient,
    PaperMetadata,
    PaperSource,
    Author
)
from core.config import get_config

logger = logging.getLogger(__name__)


class BioRxivClient(BaseLiteratureClient):
    """
    Client for interacting with bioRxiv API.
    
    bioRxiv is a preprint server for biology papers.
    Uses Europe PMC API for searching bioRxiv preprints.
    Key: Uses source:"bioRxiv" to filter results.
    API: https://www.ebi.ac.uk/europepmc/webservices/rest/search
    """

    def __init__(self, api_key: Optional[str] = None, cache_enabled: bool = True):
        """
        Initialize the bioRxiv client.
        
        Args:
            api_key: Not used for bioRxiv (public API), kept for interface consistency
            cache_enabled: Whether to enable caching for API responses
        """
        super().__init__(api_key=api_key, cache_enabled=cache_enabled)
        
        # Get configuration
        config = get_config()
        self.max_results = config.literature.max_results_per_query
        self.api_timeout = config.literature.api_timeout
        
        # 使用 Europe PMC API 来搜索 bioRxiv 预印本
        # 关键：使用 source:"bioRxiv" 来限定来源
        self.base_url = "https://www.ebi.ac.uk/europepmc/webservices/rest"
        self.biorxiv_api_url = "https://api.biorxiv.org"  # 保留用于获取详情
        
        # Rate limiting (be respectful)
        self.min_delay = 1.0  # 1 second between requests
        self.last_request_time = 0
        
        # Initialize cache if enabled
        self.cache = None  # Cache功能暂时禁用
        
        self.logger.info("Initialized bioRxiv client")
    
    def _rate_limit_delay(self):
        """Apply rate limiting delay."""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_delay:
            time.sleep(self.min_delay - elapsed)
        self.last_request_time = time.time()
    
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
        Search for papers on bioRxiv.
        
        Args:
            query: Search query string (supports simple text search, not complex boolean)
            max_results: Maximum number of results to return
            fields: Not used for bioRxiv (kept for interface consistency)
            year_from: Optional start year filter
            year_to: Optional end year filter
            **kwargs: Additional options
        
        Returns:
            List of PaperMetadata objects
        """
        if not self._validate_query(query):
            return []
        
        try:
            # 使用 Europe PMC API 搜索 bioRxiv 预印本
            # 关键：使用 source:"bioRxiv" 来限定来源
            # API: https://www.ebi.ac.uk/europepmc/webservices/rest/search
            
            # 构建查询：添加 bioRxiv 源限定
            if query and query.strip():
                # 如果查询中还没有 source:"bioRxiv"，添加它
                if 'source:"bioRxiv"' not in query and 'source:bioRxiv' not in query.upper():
                    europepmc_query = f'{query} AND source:"bioRxiv"'
                else:
                    europepmc_query = query
            else:
                # 如果没有查询，只获取 bioRxiv 预印本
                europepmc_query = 'source:"bioRxiv"'
            
            all_papers = []
            page = 1
            page_size = min(100, max_results)  # Europe PMC 每页最多 1000，但我们限制为 100
            
            while len(all_papers) < max_results:
                self._rate_limit_delay()
                
                # Build query parameters for Europe PMC
                params = {
                    "query": europepmc_query,
                    "format": "json",
                    "resultType": "core",  # 使用 core 结果类型，包含更多信息
                    "pageSize": page_size,
                    "page": page,
                }
                
                # Make API request to Europe PMC
                try:
                    full_url = f"{self.base_url}/search"
                    self.logger.debug(f"Europe PMC API request: {full_url} with params: {params}")
                    
                    response = requests.get(
                        full_url,
                        params=params,
                        timeout=self.api_timeout
                    )
                    response.raise_for_status()
                    data = response.json()
                    
                    self.logger.debug(f"Europe PMC API response: hitCount={data.get('hitCount', 0)}")
                    
                except requests.exceptions.RequestException as e:
                    self.logger.warning(f"Europe PMC API request failed: {e}")
                    break
                
                # Parse Europe PMC response
                result_list = data.get("resultList", {})
                results = result_list.get("result", [])
                
                if not results:
                    # 没有更多结果
                    break
                
                for item in results:
                    if len(all_papers) >= max_results:
                        break
                    
                    paper = self._parse_europepmc_item(item)
                    if paper:
                        # Filter by year if specified
                        if year_from or year_to:
                            if paper.year:
                                if year_from and paper.year < year_from:
                                    continue
                                if year_to and paper.year > year_to:
                                    continue
                        
                        all_papers.append(paper)
                
                # Check if there are more results
                total_hits = result_list.get("hitCount", 0)
                if len(all_papers) >= total_hits or len(results) < page_size:
                    break
                
                page += 1
            
            if len(all_papers) == 0:
                # 使用WARNING级别，便于用户在精简日志模式下感知检索规模
                self.logger.warning(
                    f"bioRxiv search (via Europe PMC) returned 0 results for query: '{query}'. "
                    f"This may indicate that there are no matching preprints on bioRxiv."
                )
            else:
                # 使用WARNING级别，便于用户在精简日志模式下感知检索规模
                self.logger.warning(f"Found {len(all_papers)} papers on bioRxiv (via Europe PMC) for query: {query}")
            
            return all_papers[:max_results]
            
        except Exception as e:
            self._handle_api_error(e, f"search query='{query}'")
            return []
    
    def _simplify_query_for_biorxiv(self, query: str) -> str:
        """
        简化查询字符串，使其适合 bioRxiv API 的简单文本搜索格式。
        
        注意：bioRxiv API 的文本搜索功能可能有限。根据 API 文档，
        `/details/biorxiv` 端点主要用于获取特定论文详情，而不是全文搜索。
        如果搜索返回 0 结果，可能是 API 不支持文本搜索，或者需要使用其他方式。
        
        Args:
            query: 原始查询字符串（可能包含括号和AND/OR等逻辑运算符）
        
        Returns:
            简化后的查询字符串
        """
        import re
        
        # 移除括号
        simplified = query.replace('(', '').replace(')', '')
        
        # 将 AND 转换为空格（多个关键词用空格连接）
        simplified = re.sub(r'\s+AND\s+', ' ', simplified, flags=re.IGNORECASE)
        
        # 将 OR 转换为空格（对于简单搜索，OR 也用空格处理）
        simplified = re.sub(r'\s+OR\s+', ' ', simplified, flags=re.IGNORECASE)
        
        # 移除多余的空白字符
        simplified = ' '.join(simplified.split())
        
        # 如果查询为空，返回 None（表示不进行搜索）
        result = simplified.strip()
        
        # 记录简化后的查询用于调试
        self.logger.debug(f"Simplified bioRxiv query: '{query}' -> '{result}'")
        
        return result if result else None
    
    def _llm_semantic_match(
        self, 
        llm_client, 
        candidate_papers: List[PaperMetadata], 
        query: str, 
        max_results: int
    ) -> List[PaperMetadata]:
        """
        使用 LLM 进行语义匹配，智能筛选与查询相关的论文
        
        Args:
            llm_client: LLM 客户端
            candidate_papers: 候选论文列表
            query: 搜索查询
            max_results: 最大返回数量
            
        Returns:
            匹配的论文列表
        """
        if not candidate_papers:
            return []
        
        # 构建 LLM 提示词
        # 将候选论文的标题和摘要整理成列表
        papers_info = []
        for i, paper in enumerate(candidate_papers[:100], 1):  # 限制最多100篇，避免token过多
            title = paper.title or "无标题"
            abstract = (paper.abstract or "无摘要")[:300]  # 限制摘要长度
            papers_info.append(f"{i}. 标题: {title}\n   摘要: {abstract}")
        
        prompt = f"""你是一个文献检索专家。请从以下候选论文中，选择与用户查询最相关的论文。

用户查询：{query}

候选论文列表：
{chr(10).join(papers_info)}

请分析每篇论文与查询的相关性，返回最相关的论文编号（用逗号分隔）。
只返回编号，例如：1,3,5,7,12

相关论文编号："""
        
        try:
            response = llm_client.generate(prompt, max_tokens=200, temperature=0.3)
            
            # 解析响应，提取论文编号
            import re
            numbers = re.findall(r'\d+', response)
            selected_indices = [int(n) - 1 for n in numbers if int(n) <= len(candidate_papers[:100])]
            
            # 获取选中的论文
            matched_papers = [candidate_papers[i] for i in selected_indices if 0 <= i < len(candidate_papers[:100])]
            
            # 限制返回数量
            return matched_papers[:max_results]
            
        except Exception as e:
            self.logger.warning(f"LLM semantic matching error: {e}")
            return self._keyword_filter_papers(candidate_papers, query, max_results)
    
    def _keyword_filter_papers(
        self, 
        candidate_papers: List[PaperMetadata], 
        query: str, 
        max_results: int
    ) -> List[PaperMetadata]:
        """
        使用关键词匹配作为后备方案
        
        Args:
            candidate_papers: 候选论文列表
            query: 搜索查询
            max_results: 最大返回数量
            
        Returns:
            匹配的论文列表
        """
        simplified_query = self._simplify_query_for_biorxiv(query)
        if not simplified_query:
            return candidate_papers[:max_results]
        
        keywords_to_match = [kw.strip().lower() for kw in simplified_query.split() if kw.strip()]
        matched_papers = []
        
        for paper in candidate_papers:
            if len(matched_papers) >= max_results:
                break
            
            title_lower = (paper.title or "").lower()
            abstract_lower = (paper.abstract or "").lower()
            text_to_search = f"{title_lower} {abstract_lower}"
            
            # 检查是否包含所有关键词（AND 逻辑）
            if all(kw in text_to_search for kw in keywords_to_match):
                matched_papers.append(paper)
        
        return matched_papers
    
    def get_paper_by_id(self, paper_id: str) -> Optional[PaperMetadata]:
        """
        Retrieve a specific paper by bioRxiv DOI or ID.
        
        Args:
            paper_id: bioRxiv ID or DOI (e.g., "10.1101/2023.01.01.123456")
        
        Returns:
            PaperMetadata object or None if not found
        """
        try:
            self._rate_limit_delay()
            
            # 使用 Europe PMC API 通过 DOI 搜索
            # 格式：DOI:10.1101/xxx AND source:"bioRxiv"
            query = f'DOI:{paper_id} AND source:"bioRxiv"'
            
            params = {
                "query": query,
                "format": "json",
                "pageSize": 1,
            }
            
            response = requests.get(
                f"{self.base_url}/search",
                params=params,
                timeout=self.api_timeout
            )
            response.raise_for_status()
            data = response.json()
            
            result_list = data.get("resultList", {})
            results = result_list.get("result", [])
            
            if not results:
                return None
            
            return self._parse_europepmc_item(results[0])
            
        except Exception as e:
            self._handle_api_error(e, f"get_paper_by_id id={paper_id}")
            return None
    
    def get_paper_references(self, paper_id: str, max_refs: int = 50) -> List[PaperMetadata]:
        """
        Get papers cited by the given paper.
        
        Note: bioRxiv API doesn't provide citation information directly.
        This method returns an empty list.
        
        Args:
            paper_id: bioRxiv ID
            max_refs: Maximum number of references (unused)
        
        Returns:
            Empty list (bioRxiv doesn't provide citations)
        """
        self.logger.warning("bioRxiv API does not provide citation data.")
        return []
    
    def get_paper_citations(self, paper_id: str, max_cites: int = 50) -> List[PaperMetadata]:
        """
        Get papers that cite the given paper.
        
        Note: bioRxiv API doesn't provide citation information directly.
        This method returns an empty list.
        
        Args:
            paper_id: bioRxiv ID
            max_cites: Maximum number of citations (unused)
        
        Returns:
            Empty list (bioRxiv doesn't provide citations)
        """
        self.logger.warning("bioRxiv API does not provide citation data.")
        return []
    
    def _parse_europepmc_item(self, item: dict) -> Optional[PaperMetadata]:
        """
        Parse Europe PMC API response item to PaperMetadata.
        
        Args:
            item: Europe PMC API response item
            
        Returns:
            PaperMetadata object or None if invalid
        """
        try:
            # Extract basic information
            title = item.get('title', '').strip()
            if not title:
                return None
            
            # Extract authors
            authors = []
            author_string = item.get('authorString', '')
            if author_string:
                # Europe PMC 的 authorString 格式通常是 "Author1, Author2, ..."
                author_names = [name.strip() for name in author_string.split(',')]
                authors = [Author(name=name) for name in author_names if name]
            
            # Extract abstract
            abstract = item.get('abstractText', '')
            
            # Extract DOI
            doi = item.get('doi', '')
            
            # Extract publication date
            pub_date = None
            year = None
            
            # Europe PMC 可能有多个日期字段
            # 优先使用 firstPublicationDate（对于预印本更准确）
            pub_date_str = item.get('firstPublicationDate') or item.get('pubDate') or item.get('epubDate', '')
            if pub_date_str:
                try:
                    # Europe PMC 日期格式通常是 "YYYY-MM-DD" 或 "YYYY"
                    if len(pub_date_str) >= 4:
                        year = int(pub_date_str[:4])
                    if len(pub_date_str) >= 10:
                        pub_date = datetime.strptime(pub_date_str[:10], '%Y-%m-%d')
                except (ValueError, IndexError):
                    pass
            
            # Extract URL
            url = None
            full_text_url_list = item.get('fullTextUrlList', {})
            if isinstance(full_text_url_list, dict):
                full_text_urls = full_text_url_list.get('fullTextUrl', [])
                if isinstance(full_text_urls, list) and len(full_text_urls) > 0:
                    # 优先选择 bioRxiv URL
                    for url_item in full_text_urls:
                        if isinstance(url_item, dict):
                            url_candidate = url_item.get('url', '')
                            if 'biorxiv' in url_candidate.lower():
                                url = url_candidate
                                break
                    # 如果没有找到 bioRxiv URL，使用第一个
                    if not url and len(full_text_urls) > 0:
                        url = full_text_urls[0].get('url', '') if isinstance(full_text_urls[0], dict) else ''
            
            # 如果还是没有 URL，构建 bioRxiv URL
            if not url:
                if doi:
                    url = f"https://www.biorxiv.org/content/{doi}"
                else:
                    pmcid = item.get('pmcid', '')
                    if pmcid:
                        url = f"https://www.biorxiv.org/content/early/{pmcid}"
            
            # Extract PDF URL
            pdf_url = None
            if isinstance(full_text_url_list, dict):
                full_text_urls = full_text_url_list.get('fullTextUrl', [])
                for url_item in full_text_urls:
                    if isinstance(url_item, dict):
                        if url_item.get('documentStyle') == 'pdf':
                            pdf_url = url_item.get('url', '')
                            break
                        # 也检查 URL 中是否包含 .pdf
                        url_candidate = url_item.get('url', '')
                        if url_candidate.endswith('.pdf') or '.pdf' in url_candidate:
                            pdf_url = url_candidate
                            break
            
            # 如果没有找到 PDF URL，尝试构建
            if not pdf_url and doi:
                pdf_url = f"https://www.biorxiv.org/content/{doi}.full.pdf"
            
            # Extract journal/source
            source = item.get('source', 'bioRxiv')
            journal = source if source else 'bioRxiv'
            
            return PaperMetadata(
                id=item.get('id', doi or title[:50]),
                source=PaperSource.BIORXIV,
                doi=doi,
                title=title,
                abstract=abstract,
                authors=authors,
                publication_date=pub_date,
                year=year,
                journal=journal,
                url=url,
                pdf_url=pdf_url,
                raw_data=item
            )
            
        except Exception as e:
            self.logger.warning(f"Error parsing Europe PMC item: {e}")
            return None
    
    def _parse_biorxiv_item(self, item: dict) -> Optional[PaperMetadata]:
        """
        Parse bioRxiv API response item to PaperMetadata.
        
        Args:
            item: bioRxiv API response item
        
        Returns:
            PaperMetadata object or None if invalid
        """
        try:
            # Extract basic information
            title = item.get('title', '').strip()
            if not title:
                return None
            
            # Extract authors
            authors = []
            author_list = item.get('authors', [])
            if isinstance(author_list, str):
                # If authors is a string, try to parse it
                author_names = [name.strip() for name in author_list.split(';')]
                authors = [Author(name=name) for name in author_names if name]
            elif isinstance(author_list, list):
                for author in author_list:
                    if isinstance(author, str):
                        authors.append(Author(name=author))
                    elif isinstance(author, dict):
                        name = author.get('name', author.get('author', ''))
                        if name:
                            authors.append(Author(name=name))
            
            # Extract abstract
            abstract = item.get('abstract', '').strip()
            
            # Extract DOI
            doi = item.get('doi', '')
            if doi and not doi.startswith('10.'):
                doi = f"10.1101/{doi}" if not doi.startswith('10.') else doi
            
            # Extract publication date
            pub_date = None
            date_str = item.get('date', '')
            if date_str:
                try:
                    # bioRxiv dates are typically in format: "2023-01-15"
                    pub_date = datetime.strptime(date_str.split('T')[0], '%Y-%m-%d')
                except (ValueError, IndexError):
                    try:
                        pub_date = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                        # Convert to offset-naive
                        if pub_date.tzinfo:
                            pub_date = pub_date.replace(tzinfo=None)
                    except (ValueError, AttributeError):
                        pass
            
            # Extract year
            year = pub_date.year if pub_date else None
            if not year and date_str:
                try:
                    year = int(date_str.split('-')[0])
                except (ValueError, IndexError):
                    pass
            
            # Extract URL
            url = item.get('jatsxml', '')
            if not url and doi:
                url = f"https://www.biorxiv.org/content/{doi}"
            elif not url:
                biorxiv_id = item.get('biorxiv_id', '')
                if biorxiv_id:
                    url = f"https://www.biorxiv.org/content/early/{biorxiv_id}"
            
            # Extract PDF URL
            pdf_url = item.get('pdf', '')
            if not pdf_url and doi:
                pdf_url = f"https://www.biorxiv.org/content/{doi}.full.pdf"
            
            return PaperMetadata(
                id=item.get('biorxiv_id', doi or title[:50]),
                source=PaperSource.BIORXIV,
                doi=doi,
                title=title,
                abstract=abstract,
                authors=authors,
                publication_date=pub_date,
                year=year,
                url=url,
                pdf_url=pdf_url,
                raw_data=item
            )
            
        except Exception as e:
            self.logger.warning(f"Error parsing bioRxiv item: {e}")
            return None
