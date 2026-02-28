"""
OpenAI Provider实现 (简化版)
"""
import json
import logging
from typing import Any, Dict, Optional

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

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
        
        logger.info(f"OpenAI Provider initialized: {config.base_url}")
    
    def generate(
        self,
        prompt: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs
    ) -> str:
        """生成文本"""
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
            
            return response.choices[0].message.content
            
        except Exception as e:
            logger.error(f"LLM生成失败: {e}")
            raise
    
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
