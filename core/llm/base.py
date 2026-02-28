"""
简化的LLM Provider基类
"""
from typing import Optional, Dict, Any
from dataclasses import dataclass


@dataclass
class LLMResponse:
    """LLM响应"""
    content: str
    model: str
    usage: Optional[Dict[str, int]] = None


class LLMProvider:
    """LLM Provider基类"""
    
    def generate(
        self,
        prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.7,
        **kwargs
    ) -> str:
        """
        生成文本
        
        Args:
            prompt: 输入提示
            max_tokens: 最大token数
            temperature: 温度参数
            
        Returns:
            生成的文本
        """
        raise NotImplementedError
    
    def generate_structured(
        self,
        prompt: str,
        schema: Dict[str, Any],
        max_tokens: int = 1000,
        temperature: float = 0.7,
        **kwargs
    ) -> Dict[str, Any]:
        """
        生成结构化输出
        
        Args:
            prompt: 输入提示
            schema: JSON schema
            max_tokens: 最大token数
            temperature: 温度参数
            
        Returns:
            结构化数据
        """
        raise NotImplementedError
