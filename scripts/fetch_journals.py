
import requests
import json
import logging
from typing import List, Dict, Optional

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# OpenAlex API Base URL
API_BASE = "https://api.openalex.org"

def search_concepts(query: str, limit: int = 1) -> List[Dict]:
    """Search for concept IDs in OpenAlex."""
    url = f"{API_BASE}/concepts?search={query}"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            return response.json().get('results', [])[:limit]
        else:
            logger.error(f"Failed to search concepts for {query}: {response.status_code}")
            return []
    except Exception as e:
        logger.error(f"Error searching concepts for {query}: {e}")
        return []

def get_top_journals(concept_id: str, limit: int = 20) -> List[Dict]:
    """Fetch top journals (sources) for a given concept ID."""
    # Filter for sources that are journals and have works associated with the concept
    # Note: OpenAlex sources endpoint allows filtering by x_concepts
    url = f"{API_BASE}/sources?filter=type:journal,x_concepts.id:{concept_id}&sort=cited_by_count:desc&per_page={limit}"
    
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            return response.json().get('results', [])
        else:
            logger.error(f"Failed to fetch journals for {concept_id}: {response.status_code}")
            return []
    except Exception as e:
        logger.error(f"Error fetching journals for {concept_id}: {e}")
        return []

def extract_journal_info(source_data: Dict, primary_field: str) -> Dict:
    """Extract relevant information from OpenAlex source object."""
    return {
        "name": source_data.get("display_name"),
        "abbreviations": source_data.get("abbreviated_title") or [],
        "issn": source_data.get("issn_l"),
        "publisher": source_data.get("host_organization_name"),
        "metrics": {
            "cited_by_count": source_data.get("cited_by_count"),
            "works_count": source_data.get("works_count"),
            # Approximate impact factor proxy (citations per work)
            "impact_factor_proxy": round(source_data.get("cited_by_count", 0) / max(1, source_data.get("works_count", 1)), 2)
        },
        "primary_field": primary_field,
        "description": f"A leading journal in {primary_field} published by {source_data.get('host_organization_name', 'Unknown')}."
    }

def main():
    target_fields = [
        "Bioinformatics",
        "Artificial Intelligence",
        "Genomics",
        "Medicine",
        "Machine Learning",
        "Computational Biology",
        "Deep Learning"
    ]
    
    all_journals = {}
    
    for field in target_fields:
        logger.info(f"Processing field: {field}...")
        concepts = search_concepts(field)
        if not concepts:
            logger.warning(f"No concept found for {field}")
            continue
            
        concept = concepts[0]
        concept_id = concept['id']
        logger.info(f"Found concept: {concept['display_name']} ({concept_id})")
        
        journals = get_top_journals(concept_id, limit=15)
        logger.info(f"Found {len(journals)} journals for {field}")
        
        for j in journals:
            j_id = j['id']
            if j_id not in all_journals:
                all_journals[j_id] = extract_journal_info(j, field)
            else:
                # If already exists, maybe append field?
                # For simplicity, we keep the first field found or just use a list
                current = all_journals[j_id]
                if isinstance(current['primary_field'], str):
                    current['primary_field'] = [current['primary_field']]
                if field not in current['primary_field']:
                    current['primary_field'].append(field)
    
    # Convert to list
    journal_list = list(all_journals.values())
    
    # Save to JSON
    output_file = "data/journal_taxonomy.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump({"journals": journal_list}, f, indent=2, ensure_ascii=False)
    
    logger.info(f"Successfully saved {len(journal_list)} journals to {output_file}")

if __name__ == "__main__":
    main()
