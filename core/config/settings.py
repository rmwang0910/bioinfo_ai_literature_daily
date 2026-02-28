"""
独立的配置管理系统（简化版，不依赖 pydantic-settings）
"""
import os
from typing import Optional


class LLMConfig:
    """LLM配置"""
    
    def __init__(self):
        self.api_key = os.getenv("LLM_API_KEY", "")
        self.base_url = os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.model = os.getenv("LLM_MODEL", "qwen-plus")
        self.max_tokens = int(os.getenv("LLM_MAX_TOKENS", "4096"))
        self.temperature = float(os.getenv("LLM_TEMPERATURE", "0.7"))
        self.timeout = int(os.getenv("LLM_TIMEOUT", "120"))


class LiteratureConfig:
    """文献搜索配置"""
    
    def __init__(self):
        self.search_timeout = int(os.getenv("LIT_SEARCH_TIMEOUT", "120"))
        self.max_results_per_query = int(os.getenv("LIT_MAX_RESULTS_PER_QUERY", "100"))
        self.api_timeout = int(os.getenv("LIT_API_TIMEOUT", "30"))
        self.pdf_download_timeout = int(os.getenv("LIT_PDF_DOWNLOAD_TIMEOUT", "60"))
        self.cache_enabled = os.getenv("LIT_CACHE_ENABLED", "false").lower() == "true"
        self.semantic_scholar_api_key = os.getenv("LIT_SEMANTIC_SCHOLAR_API_KEY")
        self.pubmed_api_key = os.getenv("LIT_PUBMED_API_KEY")
        self.pubmed_email = os.getenv("LIT_PUBMED_EMAIL", "biolit@example.com")


class AppConfig:
    """应用配置"""
    
    def __init__(self):
        self.llm = LLMConfig()
        self.literature = LiteratureConfig()


_config: Optional[AppConfig] = None


def get_config(reload: bool = False) -> AppConfig:
    """获取配置"""
    global _config
    if _config is None or reload:
        _config = AppConfig()
    return _config
