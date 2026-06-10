
import json
import os
import math
import logging
from typing import List, Dict, Optional, Any
from pathlib import Path

# Try to import openai, but handle if not available
try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

logger = logging.getLogger(__name__)

class RAGFieldClassifier:
    """
    A RAG-based classifier that maps user queries to scientific fields 
    using a hierarchical taxonomy (Micro/Meso/Macro).
    Inspired by the CWTS publication classification system structure.
    """
    
    def __init__(self, 
                 taxonomy_path: str = "data/field_taxonomy.json", 
                 embedding_cache_path: str = "data/field_embeddings.json",
                 api_key: Optional[str] = None,
                 base_url: Optional[str] = None,
                 compute_missing_embeddings: Optional[bool] = None):
        """
        Initialize the classifier.
        
        Args:
            taxonomy_path: Path to the JSON file containing field definitions.
            embedding_cache_path: Path to save/load pre-computed embeddings.
            api_key: OpenAI API key (optional, will try env var if None).
            base_url: OpenAI Base URL (optional).
            compute_missing_embeddings: Whether to call the embedding API for
                uncached fields during initialization. Defaults to false.
        """
        self.taxonomy_path = taxonomy_path
        self.embedding_cache_path = embedding_cache_path
        self.fields = []
        self.embeddings = {}
        self.client = None
        self.parent_map = {}  # Map field name to parent name
        self.children_map = {} # Map field name to list of children names
        
        # Load taxonomy
        self._load_taxonomy()
        self._build_hierarchy_index()
        
        # Initialize OpenAI client
        if OpenAI:
            api_key = api_key or os.environ.get("OPENAI_API_KEY")
            base_url = base_url or os.environ.get("OPENAI_BASE_URL")
            if api_key:
                self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=8)
            else:
                logger.warning("OpenAI API key not found. RAG functionality will be limited to keyword matching.")

        if compute_missing_embeddings is None:
            compute_missing_embeddings = os.getenv("BIOAI_BUILD_FIELD_EMBEDDINGS", "").lower() in {"1", "true", "yes"}

        # Load cached embeddings. Building missing embeddings is opt-in because
        # doing it during Web session creation can block unrelated workflows.
        self._load_embeddings(compute_missing=compute_missing_embeddings)

    def _load_taxonomy(self):
        """Load field definitions from JSON."""
        path = Path(self.taxonomy_path)
        if not path.exists():
            # If not found relative to cwd, try relative to file
            path = Path(__file__).parent.parent.parent / self.taxonomy_path
            
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.fields = data.get("fields", [])
                logger.info(f"Loaded {len(self.fields)} fields from taxonomy.")
            except Exception as e:
                logger.error(f"Failed to load taxonomy from {path}: {e}")
        else:
            logger.warning(f"Taxonomy file not found at {path}")

    def _build_hierarchy_index(self):
        """Build parent/child maps for hierarchy traversal."""
        self.parent_map = {}
        self.children_map = {}
        
        for field in self.fields:
            name = field['name']
            parent = field.get('parent')
            
            if parent:
                self.parent_map[name] = parent
                if parent not in self.children_map:
                    self.children_map[parent] = []
                self.children_map[parent].append(name)

    def get_descendants(self, field_name: str) -> List[Dict]:
        """
        Get all descendant fields (children, grandchildren, etc.) for a given field.
        Returns a list of field objects.
        """
        descendants = []
        # Get immediate children
        children_names = self.children_map.get(field_name, [])
        
        for child_name in children_names:
            # Find child field object
            child_field = next((f for f in self.fields if f['name'] == child_name), None)
            if child_field:
                descendants.append(child_field)
                # Recursively get grandchildren
                descendants.extend(self.get_descendants(child_name))
        
        return descendants

    def _load_embeddings(self, compute_missing: bool = False):
        """Load embeddings from cache or compute them."""
        path = Path(self.embedding_cache_path)
        if not path.exists():
            # Try relative to file location
            path = Path(__file__).parent.parent.parent / self.embedding_cache_path
            
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    self.embeddings = json.load(f)
                logger.info(f"Loaded {len(self.embeddings)} embeddings from cache.")
            except Exception as e:
                logger.warning(f"Failed to load embedding cache: {e}")
        
        # Check if we need to compute missing embeddings
        if self.client and compute_missing:
            updates = False
            for field in self.fields:
                name = field['name']
                if name not in self.embeddings:
                    # Construct text to embed: Name + Description + Keywords
                    text = f"{name}: {field.get('description', '')} Keywords: {', '.join(field.get('keywords', []))}"
                    embedding = self._get_embedding(text)
                    if embedding:
                        self.embeddings[name] = embedding
                        updates = True
            
            if updates:
                self._save_embeddings(path)
        elif self.client and self.fields:
            missing_count = sum(1 for field in self.fields if field['name'] not in self.embeddings)
            if missing_count:
                logger.info(
                    "Skipping %s missing field embeddings. Set BIOAI_BUILD_FIELD_EMBEDDINGS=true to build them.",
                    missing_count,
                )

    def _save_embeddings(self, path: Path):
        """Save embeddings to cache."""
        try:
            # Ensure directory exists
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self.embeddings, f)
            logger.info(f"Saved embeddings to {path}")
        except Exception as e:
            logger.error(f"Failed to save embeddings: {e}")

    def _get_embedding(self, text: str) -> List[float]:
        """Get embedding from OpenAI API."""
        if not self.client:
            return []
        try:
            text = text.replace("\n", " ")
            response = self.client.embeddings.create(input=[text], model="text-embedding-3-small")
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"Error getting embedding: {e}")
            return []

    def _cosine_similarity(self, v1: List[float], v2: List[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if not v1 or not v2:
            return 0.0
        
        dot_product = sum(a * b for a, b in zip(v1, v2))
        norm_a = math.sqrt(sum(a * a for a in v1))
        norm_b = math.sqrt(sum(b * b for b in v2))
        
        if norm_a == 0 or norm_b == 0:
            return 0.0
            
        return dot_product / (norm_a * norm_b)

    def classify(self, query: str, top_k: int = 3, threshold: float = 0.4) -> List[Dict]:
        """
        Classify the query into fields using RAG (embedding similarity).
        Returns a list of matched field objects with scores.
        """
        if not self.client:
            # Fallback to simple keyword matching if no LLM
            return self._keyword_search(query)
            
        query_embedding = self._get_embedding(query)
        if not query_embedding:
            return self._keyword_search(query)
            
        scores = []
        for field in self.fields:
            name = field['name']
            if name in self.embeddings:
                score = self._cosine_similarity(query_embedding, self.embeddings[name])
                if score >= threshold:
                    scores.append({
                        "field": field,
                        "score": score
                    })
        
        # Sort by score descending
        scores.sort(key=lambda x: x['score'], reverse=True)
        
        return scores[:top_k]

    def _keyword_search(self, query: str) -> List[Dict]:
        """Simple keyword matching fallback."""
        query = query.lower()
        matches = []
        for field in self.fields:
            score = 0
            if query in field['name'].lower():
                score += 0.8
            for kw in field.get('keywords', []):
                if kw.lower() in query:
                    score += 0.5
            
            if score > 0:
                matches.append({"field": field, "score": min(score, 1.0)})
        
        matches.sort(key=lambda x: x['score'], reverse=True)
        return matches

if __name__ == "__main__":
    # Test
    logging.basicConfig(level=logging.INFO)
    classifier = RAGFieldClassifier()
    
    # Test hierarchy
    print("\n--- Testing Hierarchy Expansion ---")
    macro_field = "Artificial Intelligence"
    descendants = classifier.get_descendants(macro_field)
    print(f"Descendants of '{macro_field}':")
    for d in descendants:
        print(f"  - {d['name']} ({d['level']})")
        
    test_query = "Pediatric genetic disorders"
    results = classifier.classify(test_query)
    print(f"Query: {test_query}")
    for res in results:
        print(f"Match: {res['field']['name']} ({res['field']['level']}) - Score: {res['score']:.4f}")
