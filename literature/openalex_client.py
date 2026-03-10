
import logging
import requests
from typing import List, Optional, Dict, Any
from .base_client import PaperMetadata

logger = logging.getLogger(__name__)

class OpenAlexClient:
    """
    Client for OpenAlex API to enrich paper metadata.
    Focuses on retrieving citation counts, open access status, and institutions.
    """
    
    BASE_URL = "https://api.openalex.org/works"
    
    def __init__(self, email: Optional[str] = None):
        """
        Initialize OpenAlex client.
        
        Args:
            email: Optional email to include in 'User-Agent' for polite pool access (faster/better limits).
        """
        self.email = email
        self.headers = {}
        if email:
            self.headers["User-Agent"] = f"mailto:{email}"

    def enrich_paper(self, paper: PaperMetadata) -> PaperMetadata:
        """
        Enrich a single paper with OpenAlex data.
        Try to match by DOI first, then PubMed ID, then title (if needed).
        """
        work_data = None
        
        # 1. Try match by DOI
        if paper.doi:
            # Clean DOI
            doi = paper.doi.replace("doi:", "").strip()
            work_data = self._get_work_by_id(f"doi:{doi}")
            
        # 2. Try match by PubMed ID
        if not work_data and paper.pubmed_id:
            work_data = self._get_work_by_id(f"pmid:{paper.pubmed_id}")
            
        # 3. Try match by title (fuzzy search) - cautious to avoid mismatch
        # Skipping for now to avoid false positives, can enable if needed.
        
        if work_data:
            self._update_paper_with_work(paper, work_data)
            logger.info(f"Enriched paper '{paper.title[:30]}...' with OpenAlex data")
        else:
            logger.debug(f"Could not find OpenAlex record for '{paper.title[:30]}...'")
            
        return paper

    def enrich_papers(self, papers: List[PaperMetadata]) -> List[PaperMetadata]:
        """
        Batch enrich a list of papers.
        Currently processes sequentially, but could be optimized with batch filters if needed.
        """
        for paper in papers:
            try:
                self.enrich_paper(paper)
            except Exception as e:
                logger.warning(f"Failed to enrich paper {paper.id}: {e}")
        return papers

    def _get_work_by_id(self, openalex_id_or_doi: str) -> Optional[Dict]:
        """Fetch work data from OpenAlex."""
        url = f"{self.BASE_URL}/{openalex_id_or_doi}"
        try:
            response = requests.get(url, headers=self.headers, timeout=5)
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                return None
            else:
                logger.warning(f"OpenAlex API error {response.status_code} for {openalex_id_or_doi}")
                return None
        except Exception as e:
            logger.warning(f"OpenAlex request failed: {e}")
            return None

    def _update_paper_with_work(self, paper: PaperMetadata, work: Dict):
        """Update PaperMetadata fields with OpenAlex work data."""
        if not isinstance(paper.raw_data, dict):
            paper.raw_data = {}
        
        # 1. Citations
        if 'cited_by_count' in work:
            paper.citation_count = work['cited_by_count']
            
        # 2. Open Access
        oa = work.get('open_access', {})
        if oa.get('is_oa'):
            paper.is_open_access = True
            paper.open_access_url = oa.get('oa_url')
            if not paper.pdf_url and oa.get('oa_url') and oa.get('oa_url').endswith('.pdf'):
                paper.pdf_url = oa.get('oa_url')
                
        # 3. Institutions (Affiliations)
        institutions = set()
        for authorship in work.get('authorships', []):
            for inst in authorship.get('institutions', []):
                if inst.get('display_name'):
                    institutions.add(inst['display_name'])
        
        if institutions:
            paper.institutions = list(institutions)
            
        # 4. Fields / Concepts (if original paper has none)
        if not paper.fields:
            concepts = work.get('concepts', [])
            # Filter for higher level concepts to avoid noise
            # Level 0 = Discipline (e.g. Biology), Level 1 = Field (e.g. Genetics)
            relevant_concepts = [c['display_name'] for c in concepts if c['level'] <= 2 and c['score'] > 0.5]
            paper.fields = relevant_concepts[:5]  # Top 5
            
        # 5. Publication Date (precision fix if needed)
        # 6. Journal Name (canonical name)
        source = {}
        if not paper.journal and work.get('primary_location'):
            source = work['primary_location'].get('source', {})
            if source and source.get('display_name'):
                paper.journal = source['display_name']
        elif work.get('primary_location'):
            source = work['primary_location'].get('source', {})

        source_id = None
        if source and source.get('id'):
            source_id = str(source['id']).rstrip('/').split('/')[-1]

        biblio = work.get('biblio', {}) if isinstance(work.get('biblio', {}), dict) else {}
        issn_list = source.get('issn') if isinstance(source, dict) else None
        issn = source.get('issn_l') if isinstance(source, dict) else None
        if not issn and isinstance(issn_list, list) and issn_list:
            issn = issn_list[0]

        paper.raw_data['openalex'] = {
            'work_id': work.get('id'),
            'source_id': source_id,
            'source_name': source.get('display_name') if source else None,
            'source_type': source.get('type') if source else None,
            'volume': biblio.get('volume'),
            'issue': biblio.get('issue'),
            'first_page': biblio.get('first_page'),
            'last_page': biblio.get('last_page'),
            'issn': issn,
            'issn_list': issn_list
        }
