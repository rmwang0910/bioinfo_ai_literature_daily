"""
OpenAI Provider实现 (简化版)
"""
import json
import logging
import os
import time
from typing import Any, Dict, Optional

try:
    from openai import OpenAI, APIConnectionError, APITimeoutError, RateLimitError, APIStatusError
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False
    APIConnectionError = Exception
    APITimeoutError = Exception
    RateLimitError = Exception
    APIStatusError = Exception

from .base import LLMProvider, LLMResponse
from core.config import LLMConfig

logger = logging.getLogger(__name__)


class OpenAIProvider(LLMProvider):
    """
    OpenAI和兼容API的Provider
    
    支持OpenAI、通义千问等OpenAI兼容的API
    """
    
    def __init__(self, config: Optional[LLMConfig] = None):
        """初始化"""
        if not HAS_OPENAI:
            raise ImportError("需要安装openai: pip install openai")
        
        # 如果没有传入config，从环境变量或默认值创建
        if config is None:
            from core.config import get_config
            config_obj = get_config()
            config = config_obj.llm
        
        self.config = config
        self.client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout
        )
        self.max_retries = max(1, int(os.getenv("LLM_MAX_RETRIES", "3")))
        self.retry_backoff = max(0.0, float(os.getenv("LLM_RETRY_BACKOFF", "1.5")))
        retry_status_codes_raw = os.getenv("LLM_RETRY_STATUS_CODES", "408,409,429,500,502,503,504")
        self.retry_status_codes = {
            int(code.strip())
            for code in retry_status_codes_raw.split(",")
            if code.strip().isdigit()
        }
        
        logger.info(f"OpenAI Provider initialized: {config.base_url}")
    
    def generate(
        self,
        prompt: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs
    ) -> str:
        """生成文本"""
        last_exception = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.config.model,
                    messages=[
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=max_tokens or self.config.max_tokens,
                    temperature=temperature or self.config.temperature,
                    **kwargs
                )
                return response.choices[0].message.content or ""
            except Exception as e:
                last_exception = e
                can_retry = self._can_retry(e) and attempt < self.max_retries
                if can_retry:
                    import random
                    base_wait = 0.0 if self.retry_backoff <= 0 else self.retry_backoff ** (attempt - 1)
                    # 加入随机抖动，避免并发线程同时重试导致雪崩
                    jitter = random.uniform(0, base_wait * 0.5) if base_wait > 0 else 0
                    wait_seconds = base_wait + jitter
                    logger.warning(
                        f"LLM请求失败（第{attempt}/{self.max_retries}次）: {e}; "
                        f"{wait_seconds:.1f}s 后重试"
                    )
                    if wait_seconds > 0:
                        time.sleep(wait_seconds)
                    continue
                logger.error(f"LLM生成失败: {e}")
                raise
        if last_exception:
            raise last_exception
        raise RuntimeError("LLM生成失败：未知错误")

    def _can_retry(self, error: Exception) -> bool:
        if isinstance(error, (APIConnectionError, APITimeoutError, RateLimitError)):
            return True
        if isinstance(error, APIStatusError):
            status_code = getattr(error, "status_code", None)
            return status_code in self.retry_status_codes
        status_code = getattr(error, "status_code", None)
        if isinstance(status_code, int) and status_code in self.retry_status_codes:
            return True
        message = str(error).lower()
        transient_signals = [
            "connection error",
            "timed out",
            "timeout",
            "temporarily unavailable",
            "try again",
            "rate limit"
        ]
        return any(signal in message for signal in transient_signals)
    
    def generate_structured(
        self,
        prompt: str,
        schema: Dict[str, Any],
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """生成结构化输出"""
        
        # 添加JSON格式说明
        schema_str = json.dumps(schema, indent=2, ensure_ascii=False)
        full_prompt = f"""{prompt}

请以JSON格式返回结果,严格遵循以下schema:

{schema_str}

只返回JSON,不要包含其他说明文字。"""
        
        try:
            response = self.generate(
                full_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                **kwargs
            )
            
            # 解析JSON
            # 尝试提取JSON (可能被包在```json中)
            content = response.strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            
            result = json.loads(content.strip())
            return result
            
        except json.JSONDecodeError as e:
            logger.error(f"JSON解析失败: {e}\n响应: {response}")
            # 返回原始文本
            return {"raw_response": response}
        except Exception as e:
            logger.error(f"结构化生成失败: {e}")
            raise
