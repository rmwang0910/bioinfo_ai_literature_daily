
import json
import logging
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def update_journals():
    json_path = Path("data/journal_taxonomy.json")
    if not json_path.exists():
        logger.error(f"File not found: {json_path}")
        return

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        journals = data.get("journals", [])
        
        # Updates provided by user
        updates = {
            "Nature Methods": {
                "impact_factor_range": "20-30",
                "fields": ["Methods", "Bioinformatics", "Genomics"],
                "description": "Publishes novel methods and significant improvements to tried-and-tested basic research techniques in the life sciences."
            },
            "Nature Biotechnology": {
                "impact_factor_range": "45-60",
                "fields": ["Biotechnology", "Genomics", "Bioinformatics"],
                "description": "Publishes new concepts in technology/methodology of relevance to the biological, biomedical, agricultural and environmental sciences."
            },
            "Nucleic Acids Research": {
                "impact_factor_range": "10-20",
                "fields": ["Genomics", "Bioinformatics", "Databases"],
                "description": "Publishes the results of leading edge research into physical, chemical, biochemical and biological aspects of nucleic acids and proteins involved in nucleic acid metabolism."
            },
            "BMC Bioinformatics": {
                "impact_factor_range": "3-5",
                "fields": ["Bioinformatics"],
                "description": "Publishes original research articles in all aspects of the development, testing and novel application of computational and statistical methods for the modeling and analysis of all kinds of biological data."
            },
            "Journal of the American Medical Informatics Association": {
                "impact_factor_range": "4-6",
                "fields": ["Medical Informatics", "Medicine"],
                "description": "Peer-reviewed journal for biomedical and health informatics."
            }
        }
        
        updated_count = 0
        for journal in journals:
            name = journal.get("name")
            if name in updates:
                logger.info(f"Updating {name}...")
                # Update fields
                for key, value in updates[name].items():
                    journal[key] = value
                updated_count += 1
                
        if updated_count > 0:
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.info(f"Successfully updated {updated_count} journals.")
        else:
            logger.warning("No journals matched for update.")
            
    except Exception as e:
        logger.error(f"Failed to update journals: {e}")

if __name__ == "__main__":
    update_journals()
