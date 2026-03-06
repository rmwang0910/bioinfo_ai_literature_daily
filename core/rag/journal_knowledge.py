
import json
import logging
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

class JournalKnowledgeBase:
    """
    A knowledge base for scientific journals, supporting RAG-based retrieval.
    """
    
    def __init__(self, data_path: str = "data/journal_taxonomy.json"):
        self.data_path = data_path
        self.journals = []
        self._load_data()
        
    def _load_data(self):
        path = Path(self.data_path)
        if not path.exists():
            # Try relative path
            path = Path(__file__).parent.parent.parent / self.data_path
            
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.journals = data.get("journals", [])
                logger.info(f"Loaded {len(self.journals)} journals from {path}")
            except Exception as e:
                logger.error(f"Failed to load journal data: {e}")
        else:
            logger.warning(f"Journal data file not found at {path}")

    def search_journals(self, query: str, field: str = None) -> List[Dict]:
        """
        Search for journals by name or field.
        """
        results = []
        query = query.lower()
        
        for journal in self.journals:
            # Check field filter
            if field:
                j_fields = [f.lower() for f in journal.get("fields", [])]
                if field.lower() not in j_fields:
                    continue
            
            # Check name match
            if query in journal['name'].lower():
                results.append(journal)
                continue
                
            # Check abbreviation match
            for abbr in journal.get('abbreviations', []):
                if query in abbr.lower():
                    results.append(journal)
                    break
        
        return results

    def get_journals_by_field(self, field: str) -> List[Dict]:
        """Get all journals in a specific field."""
        return [
            j for j in self.journals 
            if any(field.lower() in f.lower() for f in j.get("fields", []))
        ]

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    kb = JournalKnowledgeBase()
    
    # Test
    print("\n--- Bioinformatics Journals ---")
    bio_journals = kb.get_journals_by_field("Bioinformatics")
    for j in bio_journals:
        print(f"- {j['name']} (IF: {j.get('impact_factor_range')})")

    print("\n--- AI Journals ---")
    ai_journals = kb.get_journals_by_field("Artificial Intelligence")
    for j in ai_journals:
        print(f"- {j['name']}")
