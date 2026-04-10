#!/usr/bin/env python
"""
文献智能体 - 主程序

支持自然语言输入，自动解析用户需求，搜索文献，总结并发送邮件
"""
from __future__ import annotations

import os
import sys
import re
import logging
import argparse
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# 配置logging：默认只在控制台输出重要日志（WARNING及以上）
log_level_name = os.environ.get("BIOAI_LOG_LEVEL", "WARNING").upper()
log_level = getattr(logging, log_level_name, logging.WARNING)
logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 导入本地配置模块（独立，不依赖 BioLitKG）
try:
    from core.config import get_config
except ImportError as e:
    logger.warning(f"无法导入配置模块: {e}")
    get_config = None

# 导入 LLM 模块（优先使用本地模块）
try:
    from core.llm.openai import OpenAIProvider
    logger.info("✅ 成功导入本地 LLM 模块")
except ImportError as e:
    # 如果本地没有，尝试从 BioLitKG 导入（向后兼容）
    biolitkg_path = Path(__file__).parent.parent.parent / "AI" / "BioLitKG"
    if biolitkg_path.exists() and str(biolitkg_path) not in sys.path:
        sys.path.insert(0, str(biolitkg_path))
    try:
        from core.llm.openai import OpenAIProvider
        logger.info("✅ 从 BioLitKG 导入 LLM 模块（向后兼容）")
    except ImportError as e2:
        logger.warning(f"无法导入LLM模块: {e2}，将使用简单模式")
        OpenAIProvider = None

from main import BioinfoAILiteratureDaily

try:
    from core.rag.field_classifier import RAGFieldClassifier
    logger.info("✅ 成功导入 RAG 领域分类器模块")
except ImportError as e:
    logger.warning(f"无法导入 RAG 领域分类器模块: {e}")
    RAGFieldClassifier = None

class LiteratureAgent:
    """文献智能体"""
    
    def __init__(self, config_path: Optional[str] = None, mode: str = "interactive"):
        """
        初始化智能体
        
        Args:
            config_path: 配置文件路径（可选）
            mode: 运行模式
                - "scheduled": 定时触发模式，使用config.yaml配置
                - "interactive": 交互式模式，使用config.yaml并以用户输入为准
        """
        # 设置提示词文件夹路径
        self.prompts_dir = Path(__file__).parent / "prompts"
        self.mode = mode
        self.base_agent = BioinfoAILiteratureDaily(config_path)
        # 是否强制使用LLM进行关键词扩展（由config.search.force_llm_expand_keywords控制）
        search_cfg = getattr(self.base_agent, "config", {}).get("search", {}) if getattr(self.base_agent, "config", None) else {}
        self.force_llm_expand_keywords = bool(search_cfg.get("force_llm_expand_keywords", False))
        
        if mode == "scheduled":
            logger.info("运行模式: 定时触发模式（使用config.yaml配置）")
        elif mode == "interactive":
            logger.info("运行模式: 交互式模式（完全依赖用户输入）")
        else:
            logger.warning(f"未知模式: {mode}，使用交互式模式")
            self.mode = "interactive"
        
        # 初始化LLM（用于需求解析和文献总结）
        self.llm_client = None
        self.use_llm = False
        if OpenAIProvider and get_config:
            try:
                config = get_config()
                if config.llm.api_key:
                    self.llm_client = OpenAIProvider(config.llm)
                    self.use_llm = True
                    logger.info("LLM已初始化，支持智能需求解析和文献总结")
                else:
                    logger.warning("未设置 LLM_API_KEY，将使用简单模式")
            except Exception as e:
                logger.warning(f"无法初始化LLM: {e}，将使用简单模式")
        
        # 初始化 RAG 领域分类器
        self.field_classifier = None
        if RAGFieldClassifier:
            try:
                self.field_classifier = RAGFieldClassifier()
                logger.info("RAG 领域分类器已初始化，支持基于知识库的领域增强")
            except Exception as e:
                logger.warning(f"无法初始化 RAG 领域分类器: {e}")
    
    def _load_prompt(self, prompt_name: str) -> str:
        """
        从文件加载提示词模板
        
        Args:
            prompt_name: 提示词文件名（不含扩展名）
        
        Returns:
            提示词模板内容
        """
        prompt_file = self.prompts_dir / f"{prompt_name}.txt"
        if not prompt_file.exists():
            logger.warning(f"提示词文件不存在: {prompt_file}，将使用默认提示词")
            return ""
        
        try:
            with open(prompt_file, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            logger.error(f"加载提示词文件失败: {prompt_file}, 错误: {e}")
            return ""
    
    def determine_keyword_operator(self, keywords: List[str], user_input: str = "") -> str:
        """
        使用LLM判断关键词之间应该使用AND还是OR
        
        Args:
            keywords: 关键词列表
            user_input: 用户原始输入（用于上下文理解）
        
        Returns:
            'AND' 或 'OR'
        """
        if not self.use_llm or len(keywords) <= 1:
            # 如果只有一个关键词或没有LLM，默认使用AND
            return 'AND'
        
        # 检查关键词是否已经包含逻辑运算符
        has_operator = any(' AND ' in kw.upper() or ' OR ' in kw.upper() or ' NOT ' in kw.upper() for kw in keywords)
        if has_operator:
            return 'AND'  # 如果已包含运算符，返回默认值（实际不会用到）
        
        # 从文件加载提示词模板
        prompt_template = self._load_prompt("determine_keyword_operator")
        if not prompt_template:
            # 如果加载失败，使用默认提示词
            prompt_template = """你是一个文献搜索助手。请根据用户需求和关键词列表，判断应该使用AND（交集）还是OR（并集）来组合关键词。

用户需求：{user_input}

关键词列表：{keywords}

判断规则：
- 使用AND（交集）：当用户想要找到**同时包含所有关键词**的文献时
  - 例如："找关于生信和AI的文献" → 需要同时包含"生信"和"AI"
  - 例如："bioinformatics AND artificial intelligence" → 需要同时包含两者
  - 例如："找结合了机器学习和基因组学的论文" → 需要同时包含两者

- 使用OR（并集）：当用户想要找到**包含任一关键词**的文献时
  - 例如："找关于生信或AI的文献" → 包含"生信"或"AI"都可以
  - 例如："找CRISPR或者基因编辑相关的" → 包含任一即可
  - 例如："找多个主题的文献" → 通常表示并集

请根据用户需求的语义，判断应该使用AND还是OR。

只输出一个词：AND 或 OR"""
        
        # 格式化提示词
        prompt = prompt_template.format(
            user_input=user_input if user_input else "搜索相关文献",
            keywords=keywords
        )
        
        try:
            response = self.llm_client.generate(prompt, max_tokens=50, temperature=0.3)
            response = response.strip().upper()
            
            if 'AND' in response:
                operator = 'AND'
                logger.info(f"LLM判断：使用AND（交集）- 需要同时包含所有关键词")
            elif 'OR' in response:
                operator = 'OR'
                logger.info(f"LLM判断：使用OR（并集）- 包含任一关键词即可")
            else:
                # 默认使用AND（更严格，结果更相关）
                operator = 'AND'
                logger.warning(f"LLM响应无法解析，默认使用AND: {response}")
            
            return operator
        except Exception as e:
            logger.warning(f"LLM判断关键词运算符失败: {e}，默认使用AND")
            return 'AND'
    
    def _parse_date_range(self, user_input: str) -> Dict[str, Any]:
        """
        统一的日期/时间范围解析。正则优先，LLM 兜底。

        Returns:
            包含 'min_date'/'max_date' 或 'days_back' 的字典，解析失败返回空字典。
        """
        from datetime import datetime, timedelta

        result: Dict[str, Any] = {}
        current_year = datetime.now().year
        current_date = datetime.now()

        # --- 具体日期范围（最高优先级）---
        # "2023年1月1日到2024年12月31日"
        m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日[到至-](\d{4})年(\d{1,2})月(\d{1,2})日', user_input)
        if m:
            result['min_date'] = f"{int(m.group(1))}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            result['max_date'] = f"{int(m.group(4))}-{int(m.group(5)):02d}-{int(m.group(6)):02d}"
            return result

        # --- 年份范围 ---
        # "2023-2024年" 或 "2022年到2024年"
        m = re.search(r'(\d{4})[年-](\d{4})年?', user_input)
        if not m:
            m = re.search(r'(\d{4})年[到至](\d{4})年', user_input)
        if m:
            result['min_date'] = f"{int(m.group(1))}-01-01"
            result['max_date'] = f"{int(m.group(2))}-12-31"
            return result

        # "X年至今/到现在/到当前"
        m = re.search(r'(\d{4})年[到至](?:今|现在|当前)', user_input)
        if m:
            result['min_date'] = f"{int(m.group(1))}-01-01"
            result['max_date'] = current_date.strftime('%Y-%m-%d')
            return result

        # "X年初" / "X年底"
        m = re.search(r'(\d{4})年初', user_input)
        if m:
            y = int(m.group(1))
            return {'min_date': f"{y}-01-01", 'max_date': f"{y}-03-31"}
        m = re.search(r'(\d{4})年底', user_input)
        if m:
            y = int(m.group(1))
            return {'min_date': f"{y}-10-01", 'max_date': f"{y}-12-31"}

        # 相对时间："去年"、"今年"、"明年"
        if '去年' in user_input:
            y = current_year - 1
            return {'min_date': f"{y}-01-01", 'max_date': f"{y}-12-31"}
        if '今年' in user_input:
            return {'min_date': f"{current_year}-01-01", 'max_date': current_date.strftime('%Y-%m-%d')}
        if '明年' in user_input:
            y = current_year + 1
            return {'min_date': f"{y}-01-01", 'max_date': f"{y}-12-31"}

        # "YYYY年M月到N月"
        m = re.search(r'(\d{4})年(\d{1,2})月[到至-](\d{1,2})月', user_input)
        if m:
            year, ms, me = int(m.group(1)), int(m.group(2)), int(m.group(3))
            result['min_date'] = f"{year}-{ms:02d}-01"
            if me == 12:
                result['max_date'] = f"{year}-12-31"
            else:
                last_day = (datetime(year, me + 1, 1) - timedelta(days=1)).day
                result['max_date'] = f"{year}-{me:02d}-{last_day}"
            return result

        # "YYYY年M月"（单月）
        m = re.search(r'(\d{4})年(\d{1,2})月(?![到至-]|\d)', user_input)
        if m:
            year, month = int(m.group(1)), int(m.group(2))
            result['min_date'] = f"{year}-{month:02d}-01"
            if month == 12:
                result['max_date'] = f"{year}-12-31"
            else:
                last_day = (datetime(year, month + 1, 1) - timedelta(days=1)).day
                result['max_date'] = f"{year}-{month:02d}-{last_day}"
            return result

        # "YYYY年"（单个年份，放在月份之后避免误匹配）
        m = re.search(r'(\d{4})年(?![到至初底-]|\d)', user_input)
        if m:
            y = int(m.group(1))
            return {'min_date': f"{y}-01-01", 'max_date': f"{y}-12-31"}

        # --- 相对天数 ---
        m = re.search(r'最近(\d+)天', user_input)
        if m:
            return {'days_back': int(m.group(1))}
        m = re.search(r'最近(\d+)周', user_input)
        if m:
            return {'days_back': int(m.group(1)) * 7}
        m = re.search(r'最近(\d+)月', user_input)
        if m:
            return {'days_back': int(m.group(1)) * 30}
        m = re.search(r'(最近|近|过去)(\d+)年', user_input)
        if m:
            return {'days_back': int(m.group(2)) * 365}

        # "YYYY-MM-DD 到 YYYY-MM-DD"（ISO 格式）
        m = re.search(r'(\d{4}-\d{2}-\d{2})\s*[到至]\s*(\d{4}-\d{2}-\d{2})', user_input)
        if m:
            return {'min_date': m.group(1), 'max_date': m.group(2)}

        # --- LLM 兜底 ---
        if self.use_llm:
            logger.info(f"正则日期解析未命中，尝试 LLM 解析: {user_input}")
            llm_result = self._parse_time_with_llm(user_input)
            if llm_result:
                return llm_result

        return result

    def _parse_simple_request(self, user_input: str) -> Dict[str, Any]:
        """
        简单模式：使用正则表达式和规则解析用户需求

        Args:
            user_input: 用户输入的自然语言需求

        Returns:
            解析后的参数字典
        """
        parsed = {}

        # 日期解析（统一入口）
        date_result = self._parse_date_range(user_input)
        if date_result:
            parsed.update(date_result)
            logger.info(f"简单解析：日期解析结果: {date_result}")

        # 解析关键词（简单提取）
        keywords = []
        
        # 先提取邮箱，避免邮箱地址被误识别为关键词
        email_pattern = r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})'
        email_matches = re.findall(email_pattern, user_input)
        # 从输入中移除邮箱地址，避免干扰关键词提取
        user_input_clean = user_input
        for email in email_matches:
            user_input_clean = user_input_clean.replace(email, ' ')
        
        # 移除常见的无意义词和标点
        user_input_clean = re.sub(r'[，,。.、]', ' ', user_input_clean)
        user_input_clean = re.sub(r'\s+', ' ', user_input_clean).strip()
        
        # 移除常见的时间表达（避免干扰关键词提取）
        user_input_clean = re.sub(r'\d{4}年', ' ', user_input_clean)  # 移除"2026年"
        user_input_clean = re.sub(r'最近\d+天', ' ', user_input_clean)  # 移除"最近7天"
        # 新增：移除"最近N年/近N年/过去N年"这类时间短语，避免形成"找最近"这类伪关键词
        user_input_clean = re.sub(r'找?最近\d+年', ' ', user_input_clean)
        user_input_clean = re.sub(r'(近|过去)\d+年', ' ', user_input_clean)
        user_input_clean = re.sub(r'发送到', ' ', user_input_clean)  # 移除"发送到"
        user_input_clean = re.sub(r'\s+', ' ', user_input_clean).strip()
        
        # 优先处理"最近N年/近N年/过去N年 + XXX文献"模式，用于抽取主题关键词
        # 例如："找最近3年抑郁症和代谢物文献"
        if not keywords:
            year_kw_match = re.search(r'(?:最近|近|过去)\d+年([^，,。\.]+?)文献', user_input)
            if year_kw_match:
                raw_kw_str = year_kw_match.group(1)
                # 用常见连接词拆分：和/及/与/、/以及
                parts = re.split(r'和|及|与|、|以及', raw_kw_str)
                for part in parts:
                    part = part.strip()
                    if not part:
                        continue
                    # 去掉常见无意义后缀
                    part = re.sub(r'(的|相关|研究|文献)$', '', part).strip()
                    if not part:
                        continue
                    if self._is_valid_keyword(part):
                        keywords.append(part)
        
        # 优先处理"或者"、"或"分隔的关键词
        if '或者' in user_input_clean or '或' in user_input_clean:
            # 找到"关于"后面的内容
            about_match = re.search(r'关于([^，,。.]+?)(?:的|相关|文献|，|。|$)', user_input_clean)
            if about_match:
                content = about_match.group(1).strip()
                # 按"或者"或"或"分割
                parts = re.split(r'或者|或', content)
                for part in parts:
                    part = part.strip()
                    # 移除"的"、"相关"、"文献"等后缀
                    part = re.sub(r'[的的相关文献]+$', '', part).strip()
                    if part:
                        # 提取英文关键词（如CRISPR）
                        if re.match(r'^[A-Za-z]+$', part):
                            if self._is_valid_keyword(part):
                                keywords.append(part)
                        # 提取中文关键词（如基因编辑）
                        elif re.match(r'^[\u4e00-\u9fa5]+$', part):
                            if self._is_valid_keyword(part):
                                keywords.append(part)
                        # 混合关键词（如"single cell"）
                        elif re.match(r'^[A-Za-z\s]+$', part):
                            if self._is_valid_keyword(part):
                                keywords.append(part)
        
        # 如果没有找到关键词，尝试其他模式
        if not keywords:
            # 提取"关于XXX"模式 - 改进正则，避免匹配到"年关于"
            about_match = re.search(r'关于([^的发送到年，,。.]+?)(?:的|相关|文献|，|。|$)', user_input_clean)
            if about_match:
                content = about_match.group(1).strip()
                # 移除"相关"、"文献"等后缀
                content = re.sub(r'[的相关文献]+$', '', content).strip()
                # 过滤掉无效内容
                if content and len(content) >= 2 and content not in ['年', '发送', '到']:
                    if self._is_valid_keyword(content):
                        keywords.append(content)
            
            # 提取英文关键词（至少3个字符，且不在常见无意义词中）
            english_kw = re.findall(r'\b([A-Za-z]{3,}(?:\s+[A-Za-z]+)*)\b', user_input_clean)
            for kw in english_kw:
                kw = kw.strip()
                # 过滤掉常见无意义词
                if kw.lower() not in ['and', 'or', 'the', 'for', 'with', 'from', 'that', 'this']:
                    if self._is_valid_keyword(kw):
                        keywords.append(kw)
            
            # 提取中文关键词（至少2个字符）
            chinese_kw = re.findall(r'([\u4e00-\u9fa5]{2,})', user_input_clean)
            for kw in chinese_kw:
                kw = kw.strip()
                # 过滤掉无意义词
                invalid_chinese = ['关于', '相关', '文献', '发送', '到', '找', '搜索', '年关于', '发送到', '的文献', '文献的']
                if kw not in invalid_chinese:
                    if '或者' in kw or '或' in kw:
                        split_parts = re.split(r'或者|或', kw)
                        for part in split_parts:
                            part = part.strip()
                            part = re.sub(r'(的|相关|研究|文献|回顾性研究|回顾性)$', '', part).strip()
                            if part and self._is_valid_keyword(part):
                                keywords.append(part)
                    elif self._is_valid_keyword(kw):
                        keywords.append(kw)
        
        # 清理和去重关键词
        if keywords:
            # 去重
            keywords = list(set(keywords))
            # 过滤无效关键词
            keywords = [kw for kw in keywords if self._is_valid_keyword(kw)]
            # 移除包含"的"、"相关"、"文献"等无意义后缀的关键词
            keywords = [kw for kw in keywords if not kw.endswith('的') and not kw.endswith('相关') and not kw.endswith('文献')]
            # 移除包含"发送"、"到"等无意义词的关键词
            keywords = [kw for kw in keywords if '发送' not in kw and '到' not in kw and '年' not in kw]
            if keywords:
                parsed['keywords'] = keywords
                logger.info(f"简单解析：提取到关键词: {parsed['keywords']}")
        
        # 解析邮箱
        email_match = re.search(r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', user_input)
        if email_match:
            parsed['to_email'] = email_match.group(1)
            logger.info(f"简单解析：提取到邮箱: {parsed['to_email']}")

        filter_cfg: Dict[str, Any] = {}

        min_if_patterns = [
            r'影响因子\s*(?:>=|＞=|大于等于|不低于|不少于)\s*(\d+(?:\.\d+)?)',
            r'影响因子\s*(?:>|＞|高于|大于)\s*(\d+(?:\.\d+)?)',
            r'IF\s*(?:>=|>)\s*(\d+(?:\.\d+)?)'
        ]
        for p in min_if_patterns:
            m = re.search(p, user_input, re.IGNORECASE)
            if m:
                filter_cfg['min_impact_factor'] = float(m.group(1))
                break

        max_if_patterns = [
            r'影响因子\s*(?:<=|＜=|小于等于|不高于|不超过|最多)\s*(\d+(?:\.\d+)?)',
            r'影响因子\s*(?:<|＜|低于|小于)\s*(\d+(?:\.\d+)?)',
            r'IF\s*(?:<=|<)\s*(\d+(?:\.\d+)?)'
        ]
        for p in max_if_patterns:
            m = re.search(p, user_input, re.IGNORECASE)
            if m:
                filter_cfg['max_impact_factor'] = float(m.group(1))
                break

        if not filter_cfg.get('min_impact_factor'):
            m = re.search(r'影响因子\s*(\d+(?:\.\d+)?)\s*以上', user_input, re.IGNORECASE)
            if m:
                filter_cfg['min_impact_factor'] = float(m.group(1))
        if not filter_cfg.get('max_impact_factor'):
            m = re.search(r'影响因子\s*(\d+(?:\.\d+)?)\s*以下', user_input, re.IGNORECASE)
            if m:
                filter_cfg['max_impact_factor'] = float(m.group(1))

        journal_patterns = [
            r'(?:期刊|journal)[为是:：\s]+([^。；;]+)',
            r'(?:限定|只看|仅看)([^。；;]+?)(?:期刊|journal)'
        ]
        for p in journal_patterns:
            m = re.search(p, user_input, re.IGNORECASE)
            if not m:
                continue
            journal_str = m.group(1).strip()
            journal_str = re.split(r'(?:领域|方向|学科|field|domain|影响因子|IF|发送|邮箱)', journal_str, maxsplit=1)[0].strip()
            journal_parts = re.split(r'[、,，;；]|和|或|以及|/|\|', journal_str)
            journals = [j.strip(" \"'""‘’") for j in journal_parts if j.strip(" \"'""‘’")]
            if journals:
                filter_cfg['allowed_journals'] = journals
                break

        field_patterns = [
            r'(?:领域|方向|学科|field|domain)[为是:：\s]+([^。；;]+)',
            r'(?:限定|只看|仅看)([^。；;]+?)(?:领域|方向|学科|field|domain)'
        ]
        for p in field_patterns:
            m = re.search(p, user_input, re.IGNORECASE)
            if not m:
                continue
            field_str = m.group(1).strip()
            field_str = re.split(r'(?:期刊|journal|影响因子|IF|发送|邮箱)', field_str, maxsplit=1)[0].strip()
            field_parts = re.split(r'[、,，;；]|和|或|以及|/|\|', field_str)
            fields = [f.strip(" \"'""‘’") for f in field_parts if f.strip(" \"'""‘’")]
            if fields:
                filter_cfg['allowed_fields'] = fields
                break

        if filter_cfg:
            parsed['filter'] = parsed.get('filter', {})
            parsed['filter'].update(filter_cfg)
            logger.info(f"简单解析：提取到过滤条件: {filter_cfg}")
        
        return parsed
    
    def _is_valid_keyword(self, keyword: str) -> bool:
        """
        判断关键词是否有效
        
        Args:
            keyword: 关键词字符串
        
        Returns:
            True if valid, False otherwise
        """
        if not keyword or len(keyword.strip()) < 2:
            return False
        
        keyword = keyword.strip()
        
        # 过滤单个字母或数字
        if len(keyword) == 1:
            return False
        
        # 过滤常见的无意义词
        invalid_keywords = [
            '的', '了', '和', '与', '或', '或者', '相关', '文献', '关于', '找', '搜索',
            'com', 'gmail', 'qq', 'email', 'mail', '发送', '到', '邮箱',
            '年', '月', '日', '最近', '天', '周', '月',
            '年关于', '发送到', '文献的', '的文献', '关于的'
        ]
        if keyword.lower() in [kw.lower() for kw in invalid_keywords]:
            return False
        
        # 过滤看起来像邮箱地址的一部分
        if re.match(r'^[a-z]+$', keyword.lower()) and len(keyword) <= 5:
            # 短字母组合可能是邮箱的一部分
            common_email_parts = ['com', 'gmail', 'qq', 'mail', 'edu', 'org', 'net', 'cn']
            if keyword.lower() in common_email_parts:
                return False
        
        # 过滤无意义的中文字符串（如"者基因编辑的文献的"）
        if re.match(r'^[\u4e00-\u9fa5]+$', keyword):
            # 如果包含"的"、"者"等无意义的结尾，可能是解析错误
            if keyword.endswith('的') or keyword.endswith('者') or keyword.startswith('者'):
                # 检查是否是有意义的词（至少包含一个实词）
                if len(keyword) <= 3 and ('的' in keyword or '者' in keyword):
                    return False
        
        # 过滤纯数字
        if keyword.isdigit():
            return False
        
        return True
    
    def _filter_english_keywords(self, keywords: List[str]) -> List[str]:
        """
        只保留适合作为英文检索词的关键词（不包含中文字符）
        """
        if not keywords:
            return []
        
        english_keywords: List[str] = []
        for kw in keywords:
            kw_str = str(kw).strip()
            if not kw_str:
                continue
            # 跳过包含中文字符的关键词
            if re.search(r'[\u4e00-\u9fff]', kw_str):
                continue
            english_keywords.append(kw_str)
        
        # 去重并保持顺序（不区分大小写）
        seen = set()
        result: List[str] = []
        for kw in english_keywords:
            key = kw.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(kw)
        
        return result

    def _deduplicate_semantic_keywords(self, keywords: List[str]) -> List[str]:
        """
        对语义层面的关键词去重，避免类似 'single cell' 和 'single-cell' 这种重复概念
        规则：
        - 对纯英文/空格/连字符的关键词，按去掉空格和连字符、转小写后的形式去重
        - 其他（如纯中文）按原样去重
        """
        if not keywords:
            return []

        deduped: List[str] = []
        seen = set()

        for kw in keywords:
            kw_str = str(kw).strip()
            if not kw_str:
                continue

            # 纯英文/空格/连字符：用规范化形式去重
            if re.match(r'^[A-Za-z\s\-]+$', kw_str):
                norm = re.sub(r'[\s\-]+', '', kw_str).lower()
            else:
                # 中文或混合：直接用原字符串做key
                norm = kw_str

            if norm in seen:
                continue
            seen.add(norm)
            deduped.append(kw_str)

        return deduped
    
    def parse_user_request(self, user_input: str) -> Dict[str, Any]:
        """
        解析用户需求
        
        Args:
            user_input: 用户输入的自然语言需求
        
        Returns:
            解析后的参数字典
        """
        # 先进行简单的文本解析（即使没有LLM也能工作）
        parsed = self._parse_simple_request(user_input)
        
        # 确保parsed是字典类型
        if parsed is None:
            parsed = {}
        
        if not self.use_llm:
            # 简单模式：使用简单解析结果
            logger.info("使用简单模式，将使用简单解析和配置文件中的默认设置")
            return parsed
        
        # LLM模式：使用LLM进行更精确的解析
        # 从文件加载提示词模板
        from datetime import datetime
        prompt_template = self._load_prompt("parse_user_request")
        if not prompt_template:
            # 如果加载失败，使用默认提示词（这里保留原逻辑作为fallback）
            logger.warning("无法加载提示词模板，使用默认提示词")
            prompt_template = """你是一个文献搜索助手。请从用户的自然语言需求中提取以下信息：

用户需求：{user_input}

请提取以下信息（如果用户没有明确说明，使用默认值或从上下文推断）：
1. 主题关键词（topic_keywords）：核心研究主题
2. 文章类型关键词（article_type_keywords）：例如回顾性研究、综述、meta分析
3. 检索查询字符串（boolean_query）：英文布尔检索式，必须正确使用AND/OR/NOT与括号
4. 时间范围（非常重要，请仔细解析）：
   - days_back: 最近N天（如果用户说"最近7天"、"最近一周"、"最近一个月"等）
   - min_date和max_date: 日期范围（格式：YYYY-MM-DD），支持以下表达方式：
     * 单个年份："2026年" → min_date: "2026-01-01", max_date: "2026-12-31"
     * 年份范围："2023-2024年"、"2022年到2024年" → min_date: "2023-01-01", max_date: "2024-12-31"
     * 单个月份："2024年1月" → min_date: "2024-01-01", max_date: "2024-01-31"
     * 月份范围："2024年1月到3月" → min_date: "2024-01-01", max_date: "2024-03-31"
     * 具体日期："2023年1月1日到2024年12月31日" → 对应具体日期
     * 相对时间："去年" → 去年1月1日到12月31日
     * 相对时间："今年" → 今年1月1日到现在
     * 相对时间："明年" → 明年1月1日到12月31日
     * 模糊时间："2024年初" → 2024年1月1日到3月31日
     * 模糊时间："2024年底" → 2024年10月1日到12月31日
     * 至今范围："2023年至今"、"2023年到现在" → min_date: "2023-01-01", max_date: 当前日期
5. 收件人邮箱（如果用户提供了）
6. 关键词组合方式（keyword_operator: "AND"表示交集，需要同时包含所有关键词；"OR"表示并集，包含任一关键词即可。根据用户语义判断）
7. 过滤条件：影响因子范围、期刊名单、领域名单
8. 其他要求（如文献数量等）

请以JSON格式输出，格式如下：
{{
    "topic_keywords": ["keyword1", "keyword2"],
    "article_type_keywords": ["review", "retrospective analysis"] 或 null,
    "boolean_query": "(term1 OR term2) AND term3",
    "keyword_operator": "AND",
    "days_back": null,
    "min_date": "2026-01-01",
    "max_date": "2026-12-31",
    "to_email": null,
    "max_papers": 50,
    "filter": {{
        "min_abstract_length": 100,
        "exclude_keywords": [],
        "include_keywords": [],
        "allowed_journals": [],
        "allowed_fields": [],
        "min_impact_factor": null,
        "max_impact_factor": null
    }}
}}

重要提示：
- **日期解析优先级**：
  1. 如果用户指定了具体年份或日期范围（如"2026年"、"2023-2024年"、"2024年1月"等），必须设置min_date和max_date，不要使用days_back
  2. 如果用户说"最近N天/周/月"，使用days_back
  3. 如果用户没有指定时间范围，min_date、max_date和days_back都设为null
- **相对时间处理**：
  - "去年" = 当前年份-1的1月1日到12月31日
  - "今年" = 当前年份的1月1日到现在
  - "明年" = 当前年份+1的1月1日到12月31日
  - 当前日期是{current_date}
- **日期格式**：min_date和max_date必须是"YYYY-MM-DD"格式
- 对于keyword_operator，根据用户语义判断：
  * 如果用户说"和"、"同时"、"结合"、"与"等，使用"AND"
  * 如果用户说"或"、"或者"、"任一"等，使用"OR"
  * 如果不确定，默认使用"AND"（更严格，结果更相关）"""
        
        # 格式化提示词
        current_date = datetime.now().strftime('%Y年%m月%d日')
        prompt = prompt_template.format(
            user_input=user_input,
            current_date=current_date
        )
        
        try:
            response = self.llm_client.generate(prompt, max_tokens=800, temperature=0.3)
            
            # 解析JSON响应
            import json
            import re
            
            # 提取JSON部分
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                llm_parsed = json.loads(json_match.group(0))
                logger.info(f"LLM解析结果: {llm_parsed}")
                
                # 合并简单解析和LLM解析的结果
                # LLM解析的结果优先级更高，但简单解析的结果作为补充
                # 确保parsed是字典类型
                if parsed is None:
                    parsed = {}
                merged = parsed.copy() if parsed else {}  # 先使用简单解析的结果
                
                # LLM解析的结果覆盖简单解析（如果LLM提供了更准确的信息）
                for key, value in llm_parsed.items():
                    if value is not None:  # 只使用LLM提供的非空值
                        if key == 'topic_keywords' and value:
                            # 处理新的两层结构：主题关键词
                            cleaned_keywords = [kw for kw in value if self._is_valid_keyword(str(kw))]
                            if cleaned_keywords:
                                merged['topic_keywords'] = cleaned_keywords
                            else:
                                logger.warning(f"LLM返回的主题关键词全部无效")
                        elif key == 'article_type_keywords' and value:
                            # 处理新的两层结构：文章类型关键词
                            cleaned_types = [at_kw for at_kw in value if at_kw and str(at_kw).strip()]
                            if cleaned_types:
                                merged['article_type_keywords'] = cleaned_types
                        elif key == 'keywords' and value:
                            # 向后兼容：处理旧的keywords字段
                            cleaned_keywords = [kw for kw in value if self._is_valid_keyword(str(kw))]
                            if cleaned_keywords:
                                merged['keywords'] = cleaned_keywords
                            else:
                                # 如果清理后没有有效关键词，保留简单解析的结果
                                logger.warning(f"LLM返回的关键词全部无效，使用简单解析结果")
                        elif key in ['min_date', 'max_date', 'days_back'] and value:
                            # 如果LLM提供了日期信息，优先使用LLM的结果
                            merged[key] = value
                            # 如果设置了min_date/max_date，清除days_back（避免冲突）
                            if key in ['min_date', 'max_date']:
                                merged.pop('days_back', None)
                        elif key == 'logical_keywords_description' and value:
                            # 保存关键词逻辑的自然语言描述，供严格验证阶段使用
                            merged['logical_keywords_description'] = str(value).strip()
                        elif key == 'boolean_query' and value:
                            # 保存LLM构建的布尔查询字符串
                            merged['boolean_query'] = str(value).strip()
                        elif key == 'keyword_operator' and value:
                            merged['keyword_operator'] = value
                        elif key == 'keyword_mapping' and value and isinstance(value, dict):
                            merged['keyword_mapping'] = value
                        elif key == 'expanded_keywords' and value and isinstance(value, list):
                            merged['expanded_keywords'] = value
                        elif key == 'to_email' and value:
                            merged['to_email'] = value
                        elif key == 'max_papers' and value:
                            merged['max_papers'] = value
                        elif key == 'filter' and value:
                            merged_filter = merged.get('filter', {}) if isinstance(merged.get('filter'), dict) else {}
                            if isinstance(value, dict):
                                merged_filter.update(value)
                            merged['filter'] = merged_filter
                
                # 对关键词做一次语义去重，避免 'single cell' / 'single-cell' 这种重复概念
                if 'topic_keywords' in merged and isinstance(merged['topic_keywords'], list):
                    merged['topic_keywords'] = self._deduplicate_semantic_keywords(
                        [str(k) for k in merged['topic_keywords']]
                    )
                elif 'keywords' in merged and isinstance(merged['keywords'], list):
                    # 向后兼容
                    merged['keywords'] = self._deduplicate_semantic_keywords(
                        [str(k) for k in merged['keywords']]
                    )
                
                # RAG 知识增强：基于用户需求自动推断相关领域
                if self.field_classifier:
                    try:
                        # 组合查询文本：用户输入 + 提取的主题关键词
                        topic_kws = merged.get('topic_keywords', []) or merged.get('keywords', [])
                        query_text = f"{user_input} {' '.join(topic_kws)}"
                        
                        # 获取建议领域（Micro/Meso/Macro）
                        suggestions = self.field_classifier.classify(query_text, top_k=3, threshold=0.45)
                        
                        if suggestions:
                            current_filter = merged.get('filter', {})
                            current_fields = set(current_filter.get('allowed_fields', []))
                            
                            new_fields = []
                            for item in suggestions:
                                field_name = item['field']['name']
                                if field_name not in current_fields:
                                    current_fields.add(field_name)
                                    new_fields.append(f"{field_name} ({item['field']['level']})")
                                    
                                    # 自动扩展子领域（例如：选中 Machine Learning 会自动选中 Deep Learning）
                                    # 这是利用 Micro/Meso/Macro 层级结构的具体体现
                                    descendants = self.field_classifier.get_descendants(field_name)
                                    for desc in descendants:
                                        if desc['name'] not in current_fields:
                                            current_fields.add(desc['name'])
                                            new_fields.append(f"  ↳ {desc['name']} ({desc['level']})")
                            
                            if new_fields:
                                current_filter['allowed_fields'] = list(current_fields)
                                merged['filter'] = current_filter
                                logger.info(f"RAG 知识增强：根据语义推断，自动扩展领域筛选: {', '.join(new_fields)}")
                    except Exception as e:
                        logger.warning(f"RAG 领域推断失败: {e}")

                logger.info(f"合并后的解析结果: {merged}")
                return merged
            else:
                logger.warning("无法从LLM响应中提取JSON，使用简单解析结果")
                return parsed  # 返回简单解析的结果
        except Exception as e:
            logger.warning(f"LLM解析用户需求失败: {e}，使用简单解析结果")
            return parsed  # 返回简单解析的结果
    
    def summarize_papers(self, papers: List) -> Dict[str, str]:
        """
        为每篇论文生成中文总结
        
        Args:
            papers: 论文列表
        
        Returns:
            论文总结字典，key为论文ID（DOI、标题或索引），value为中文总结
        """
        if not papers or not self.use_llm:
            return {}
        
        paper_summaries = {}
        
        total = len(papers)
        logger.warning("开始为 %d 篇论文生成中文总结(并行处理)...", total)

        max_workers = min(5, total)
        completed_count = 0

        def _task(idx_paper):
            idx, p = idx_paper
            return idx, p, self._summarize_single_paper(p, idx)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_task, (i, paper)): i
                for i, paper in enumerate(papers, 1)
            }
            for future in as_completed(futures):
                try:
                    i, paper, summary = future.result()
                    completed_count += 1
                    if completed_count == 1 or completed_count % 5 == 0 or completed_count == total:
                        tp = paper.title[:50] if paper.title else "N/A"
                        logger.warning("中文总结进度: %d/%d (完成: %s)", completed_count, total, tp)
                    if summary:
                        if paper.doi:
                            paper_summaries[paper.doi] = summary
                        if paper.title:
                            paper_summaries[paper.title] = summary
                        paper_summaries[str(i)] = summary
                    else:
                        logger.warning("论文 %d/%d: 生成总结失败", i, total)
                except Exception as e:
                    i = futures[future]
                    logger.warning("论文 %d/%d: 生成总结时出错: %s", i, total, e)

        logger.warning("已为 %d 篇论文生成中文总结", len(set(paper_summaries.values())))
        return paper_summaries
    
    def _summarize_single_paper(self, paper, index: int = 0) -> Optional[str]:
        """
        为单篇论文生成中文总结
        
        Args:
            paper: 论文对象
            index: 论文索引（用于日志）
        
        Returns:
            中文总结文本
        """
        if not paper.abstract:
            return None
        
        # 限制摘要长度，避免token过多
        abstract = paper.abstract[:500] if len(paper.abstract) > 500 else paper.abstract
        
        # 从文件加载提示词模板
        prompt_template = self._load_prompt("summarize_paper")
        if not prompt_template:
            # 如果加载失败，使用默认提示词
            prompt_template = """请为以下论文生成简洁的中文总结。

标题: {title}

摘要: {abstract}

要求：
1. 用2-3句话概括研究内容和主要发现
2. 突出关键技术和方法
3. 使用中文，语言简洁明了
4. 控制在100字以内
5. 直接输出总结内容，不要包含"总结"、"摘要"等前缀

中文总结："""
        
        # 格式化提示词
        prompt = prompt_template.format(
            title=paper.title or '无标题',
            abstract=abstract
        )
        
        try:
            summary = self.llm_client.generate(prompt, max_tokens=200, temperature=0.5)
            # 清理输出，移除可能的前缀
            summary = summary.strip()
            # 移除常见的LLM输出前缀
            for prefix in ['总结：', '摘要：', '中文总结：', '总结:', '摘要:', '中文总结:']:
                if summary.startswith(prefix):
                    summary = summary[len(prefix):].strip()
            return summary
        except Exception as e:
            logger.warning(f"生成单篇论文总结失败: {e}")
            return None
    
    def _summarize_literature_simple(self, papers, keyword_validation_info: Dict = None) -> str:
        """无 LLM 的简单文献总结（fallback）"""
        if not papers:
            return "未找到相关文献。"
        summary = f"共找到 {len(papers)} 篇相关文献。\n\n"
        for i, paper in enumerate(papers[:5], 1):
            summary += f"{i}. {paper.title or '无标题'}\n"
            summary += f"   期刊: {paper.journal or '未知'}, 年份: {paper.year or '未知'}\n\n"
        if keyword_validation_info:
            summary += self._generate_validation_table(papers, keyword_validation_info)
        return summary

    def summarize_literature(self, papers, keyword_validation_info: Dict = None) -> str:
        """
        总结文献

        Args:
            papers: 论文列表
            keyword_validation_info: 关键词验证信息（用于生成验证表）

        Returns:
            总结文本
        """
        if not papers:
            return "未找到相关文献。"

        if not self.use_llm:
            return self._summarize_literature_simple(papers, keyword_validation_info)
        
        # 准备论文信息
        papers_text = ""
        for i, paper in enumerate(papers[:20], 1):  # 最多总结20篇
            authors = ", ".join([a.name for a in (paper.authors or [])[:3]])
            if len(paper.authors or []) > 3:
                authors += " et al."
            
            papers_text += f"""
论文{i}:
标题: {paper.title or '无标题'}
作者: {authors or '未知'}
期刊: {paper.journal or '未知'}
年份: {paper.year or '未知'}
摘要: {paper.abstract[:300] if paper.abstract else '无摘要'}
---
"""
        
        # 从文件加载提示词模板
        prompt_template = self._load_prompt("summarize_literature")
        if not prompt_template:
            # 如果加载失败，使用默认提示词
            prompt_template = """请总结以下 {paper_count} 篇文献，生成一个简洁的总结报告。

要求：
1. 总结主要研究方向和热点
2. 识别关键技术和方法
3. 突出重要发现和趋势
4. 控制在500字以内
5. 使用中文

文献列表：
{papers_text}

请生成总结报告："""
        
        # 格式化提示词
        prompt = prompt_template.format(
            paper_count=len(papers),
            papers_text=papers_text
        )
        
        try:
            summary = self.llm_client.generate(prompt, max_tokens=1000, temperature=0.5)
            
            # 添加关键词匹配验证表（如果有）
            if keyword_validation_info:
                validation_table = self._generate_validation_table(papers, keyword_validation_info)
                summary += "\n\n" + validation_table
            
            return summary
        except Exception as e:
            logger.warning(f"文献总结失败: {e}，使用简单总结")
            return self._summarize_literature_simple(papers, keyword_validation_info)
    
    # ------------------------------------------------------------------
    # update_config_from_request 及其 helper 方法
    # ------------------------------------------------------------------

    def _resolve_topic_keywords(self, parsed_request: Dict[str, Any]):
        """从解析结果中提取主题关键词和文章类型关键词，并做启发式分离。"""
        topic_keywords = None
        article_type_keywords: List[str] | None = None

        if 'topic_keywords' in parsed_request and parsed_request['topic_keywords']:
            topic_keywords = parsed_request['topic_keywords']
            logger.info(f"检测到主题关键词: {topic_keywords}")

        # 用户手动覆盖
        raw_keywords = parsed_request.get('keywords')
        if raw_keywords:
            if topic_keywords and raw_keywords != topic_keywords:
                logger.info(f"用户手动修改关键词: {topic_keywords} -> {raw_keywords}")
                topic_keywords = raw_keywords
            elif not topic_keywords:
                topic_keywords = raw_keywords

        if parsed_request.get('article_type_keywords'):
            article_type_keywords = list(parsed_request['article_type_keywords'])

        # 启发式：从 topic 中剥离文章类型词
        ARTICLE_TYPE_HINTS = [
            "回顾性", "回顾性研究", "队列研究", "病例对照", "病例系列",
            "综述", "系统综述", "meta分析", "meta-analysis",
            "review", "systematic review", "retrospective", "retrospective study",
            "case report", "case series", "detection method", "diagnostic",
            "screening", "methodology"
        ]
        if topic_keywords:
            cleaned_topic: List[str] = []
            extra_article_types: List[str] = []
            for kw in topic_keywords:
                kw_str = str(kw).strip()
                if not kw_str:
                    continue
                if any(hint.lower() in kw_str.lower() for hint in ARTICLE_TYPE_HINTS):
                    extra_article_types.append(kw_str)
                else:
                    cleaned_topic.append(kw_str)
            if extra_article_types:
                topic_keywords = cleaned_topic or None
                if article_type_keywords is None:
                    article_type_keywords = []
                at_set = {str(x).strip() for x in article_type_keywords if str(x).strip()}
                for at in extra_article_types:
                    if at not in at_set:
                        article_type_keywords.append(at)
                        at_set.add(at)

        return topic_keywords, article_type_keywords

    def _build_search_query(self, cleaned_keywords: List[str], parsed_request: Dict[str, Any]) -> str:
        """根据清洗后的关键词构建搜索查询字符串，并设置 config。返回最终 query。"""
        boolean_query = parsed_request.get('boolean_query')

        # 高级检索式（用户手动输入的完整 PubMed 查询）
        if len(cleaned_keywords) == 1:
            q = cleaned_keywords[0]
            upper_q = q.upper()
            if any(op in upper_q for op in [" AND ", " OR ", " NOT "]) or "[" in q or ":" in q:
                self.base_agent.config['search']['keywords'] = [q]
                self.base_agent.config['search']['semantic_keywords'] = cleaned_keywords
                logger.info(f"检测到高级检索式: {q}")
                return q

        # LLM 构建的布尔查询
        if boolean_query:
            self.base_agent.config['search']['keywords'] = [boolean_query]
            self.base_agent.config['search']['semantic_keywords'] = cleaned_keywords
            logger.info(f"使用布尔查询: {boolean_query}")
            return boolean_query

        # 常规情况：扩展关键词 → 构建 AND 查询
        expanded_keywords = cleaned_keywords
        force_expand = getattr(self, "force_llm_expand_keywords", False)

        # 优先使用统一解析中已返回的扩展结果（避免额外 LLM 调用）
        pre_expanded = parsed_request.get('expanded_keywords')
        pre_mapping = parsed_request.get('keyword_mapping')
        used_pre_expansion = False

        if pre_expanded and isinstance(pre_expanded, list) and len(pre_expanded) > 0:
            english_expanded = self._filter_english_keywords(pre_expanded)
            if english_expanded:
                expanded_keywords = english_expanded
                used_pre_expansion = True
                logger.info(f"使用统一解析扩展词: {expanded_keywords}")
        if not used_pre_expansion and pre_mapping and isinstance(pre_mapping, dict):
            primary = []
            for orig_kw in cleaned_keywords:
                mapped = pre_mapping.get(str(orig_kw).strip(), [])
                if mapped:
                    primary.append(str(mapped[0]).strip())
            english_primary = self._filter_english_keywords(primary)
            if english_primary:
                expanded_keywords = english_primary
                used_pre_expansion = True
                logger.info(f"使用 keyword_mapping 扩展: {expanded_keywords}")

        # Fallback: 单独调用 LLM 扩展
        if not used_pre_expansion and self.use_llm:
            try:
                expanded_keywords = self._expand_search_keywords_with_llm(cleaned_keywords)
                logger.info(f"关键词扩展(fallback): {expanded_keywords}")
            except Exception as e:
                if force_expand:
                    raise RuntimeError(f"LLM关键词扩展失败: {e}")
                else:
                    logger.warning(f"LLM扩展失败，使用原始关键词: {e}")
                    expanded_keywords = cleaned_keywords

        # 核心关键词裁剪
        search_cfg = self.base_agent.config.get('search', {})
        try:
            max_core = int(search_cfg.get('max_core_keywords_for_and', 2))
        except Exception:
            max_core = 2
        enforce_all = bool(search_cfg.get('enforce_all_keywords_and', False))

        query_keywords = expanded_keywords if expanded_keywords != cleaned_keywords else cleaned_keywords
        core_keywords = query_keywords
        if len(query_keywords) > max_core and not enforce_all:
            core_keywords = query_keywords[:max_core]

        if len(core_keywords) > 1:
            query = " AND ".join([f"({kw})" for kw in core_keywords])
        else:
            query = core_keywords[0] if core_keywords else ""

        self.base_agent.config['search']['keywords'] = [query]
        self.base_agent.config['search']['semantic_keywords'] = cleaned_keywords
        logger.info(f"搜索查询: {query}")
        return query

    def _update_keyword_operator(self, parsed_request: Dict[str, Any], topic_keywords, user_input: str):
        """更新关键词组合方式（AND/OR）。"""
        keywords_for_operator = topic_keywords or parsed_request.get('keywords', [])
        if not keywords_for_operator:
            return
        keywords = keywords_for_operator
        if 'keyword_operator' not in parsed_request or not parsed_request['keyword_operator']:
            if self.use_llm and len(keywords) > 1:
                has_operator = any(' AND ' in kw.upper() or ' OR ' in kw.upper() or ' NOT ' in kw.upper() for kw in keywords)
                if not has_operator:
                    operator = self.determine_keyword_operator(keywords, user_input)
                    self.base_agent.config['search']['keyword_operator'] = operator
                    logger.info(f"LLM判断关键词组合: {operator}")
        else:
            self.base_agent.config['search']['keyword_operator'] = parsed_request['keyword_operator']

    def _update_date_config(self, parsed_request: Dict[str, Any]):
        """更新日期/时间范围配置。"""
        if parsed_request.get('days_back'):
            self.base_agent.config['search']['days_back'] = parsed_request['days_back']
            logger.info(f"时间范围: 最近{parsed_request['days_back']}天")
        if parsed_request.get('min_date'):
            self.base_agent.config['search']['min_date'] = parsed_request['min_date']
            self.base_agent.config['search'].pop('days_back', None)
            logger.info(f"最小日期: {parsed_request['min_date']}")
        if parsed_request.get('max_date'):
            self.base_agent.config['search']['max_date'] = parsed_request['max_date']
            self.base_agent.config['search'].pop('days_back', None)
            logger.info(f"最大日期: {parsed_request['max_date']}")

    def _update_filter_config(self, parsed_request: Dict[str, Any]):
        """更新过滤配置（影响因子、期刊、领域等）。"""
        if not parsed_request.get('filter'):
            return
        fc = parsed_request['filter']
        cfg = self.base_agent.config['filter']
        for key in ['min_abstract_length', 'exclude_keywords', 'include_keywords',
                     'allowed_journals', 'allowed_fields', 'min_impact_factor', 'max_impact_factor']:
            if key in fc:
                cfg[key] = fc[key]

    def update_config_from_request(self, parsed_request: Dict[str, Any], user_input: str = ""):
        """根据解析的需求更新配置。"""
        # 1. 解析关键词
        topic_keywords, article_type_keywords = self._resolve_topic_keywords(parsed_request)

        # 2. 记录关键词逻辑描述
        logical_desc = parsed_request.get('logical_keywords_description')
        if logical_desc:
            if 'search' not in self.base_agent.config:
                self.base_agent.config['search'] = {}
            self.base_agent.config['search']['logical_keywords_description'] = str(logical_desc).strip()

        # 3. 构建搜索查询
        if topic_keywords:
            cleaned_keywords = [str(kw).strip() for kw in topic_keywords if self._is_valid_keyword(str(kw))]
            if cleaned_keywords:
                self._build_search_query(cleaned_keywords, parsed_request)
                # 保存文章类型关键词
                if article_type_keywords:
                    cleaned_at = [str(at).strip() for at in article_type_keywords if str(at).strip()]
                    if cleaned_at:
                        self.base_agent.config['search']['article_type_keywords'] = cleaned_at
                    else:
                        self.base_agent.config['search'].pop('article_type_keywords', None)
                else:
                    self.base_agent.config['search'].pop('article_type_keywords', None)
            else:
                logger.warning("所有关键词都被过滤，使用配置文件中的默认关键词")
        else:
            logger.warning(f"解析结果中没有关键词字段: {list(parsed_request.keys()) if parsed_request else 'None'}")

        # 4. 关键词运算符
        self._update_keyword_operator(parsed_request, topic_keywords, user_input)

        # 5. 日期配置
        self._update_date_config(parsed_request)

        # 6. 邮件配置
        if parsed_request.get('to_email'):
            self.base_agent.config['email']['to_email'] = parsed_request['to_email']

        # 7. 报告配置
        if parsed_request.get('max_papers'):
            self.base_agent.config['report']['max_papers'] = parsed_request['max_papers']

        # 8. 过滤配置
        self._update_filter_config(parsed_request)
    
    def run_with_request(self, user_input: str = None, override_config: Optional[Dict[str, Any]] = None):
        """
        根据用户需求运行智能体
        
        Args:
            user_input: 用户输入的自然语言需求（交互式模式必需，定时模式可选）
            override_config: 命令行参数覆盖的配置项（字典格式）
        """
        logger.info("=" * 80)
        logger.info("文献智能体")
        logger.info("=" * 80)
        
        # 应用命令行参数覆盖的配置
        self._override_config = override_config
        if override_config:
            self._apply_config_overrides(override_config)
        
        if self.mode == "scheduled":
            logger.info("模式: 定时触发（使用config.scheduled.yaml配置）")
            logger.info("")
            return self._run_scheduled_mode()
        else:
            # 交互式模式：完全依赖用户输入
            logger.info("模式: 交互式（完全依赖用户输入）")
            if not user_input:
                user_input = input("\n请输入您的需求: ").strip()
            logger.info(f"用户需求: {user_input}")
            logger.info("")
            return self._run_interactive_mode(user_input)
    
    def _apply_config_overrides(self, overrides: Dict[str, Any]):
        """
        应用命令行参数覆盖的配置
        
        Args:
            overrides: 配置覆盖字典，格式如 {'search': {'max_results_per_keyword': 100}, 'report': {'max_papers': 50}}
        """
        for section, values in overrides.items():
            if section not in self.base_agent.config:
                self.base_agent.config[section] = {}
            
            if isinstance(values, dict):
                for key, value in values.items():
                    if value is not None:  # 只覆盖非None的值
                        self.base_agent.config[section][key] = value
                        logger.info(f"✅ 配置覆盖: {section}.{key} = {value}")
    
    def _run_scheduled_mode(self):
        """定时触发模式：直接使用配置文件"""
        # 直接使用配置文件中的设置
        logger.info("步骤1: 使用配置文件中的设置...")
        logger.info(f"搜索关键词: {self.base_agent.config['search'].get('keywords', [])}")
        logger.info(f"时间范围: {self.base_agent.config['search'].get('days_back', 7)}天")
        logger.info(f"收件人邮箱: {self.base_agent.config['email'].get('to_email', '未设置')}")
        logger.info("")
        
        # 搜索文献
        logger.info("步骤2: 搜索文献...")
        papers = self.base_agent.search_literature()
        
        if not papers:
            logger.info("未找到新文献")
            return
        
        # 总结文献
        logger.info("步骤3: 总结文献...")
        # 获取关键词验证信息（如果有）
        keyword_validation_info = getattr(self.base_agent, '_keyword_validation_info', None)
        summary = self.summarize_literature(papers, keyword_validation_info=keyword_validation_info)
        logger.info(f"文献总结完成（{len(summary)} 字符）")
        
        # 为每篇论文生成中文总结
        logger.info("步骤4: 为每篇论文生成中文总结...")
        paper_summaries = self.summarize_papers(papers)
        logger.info(f"已为 {len(paper_summaries)} 篇论文生成中文总结")
        
        # 发送邮件
        logger.info("步骤5: 发送邮件...")
        success = self.base_agent.send_email(papers, summary=summary, paper_summaries=paper_summaries)
        
        if success:
            # 使用WARNING级别，确保在默认精简日志下也能看到任务完成提示
            logger.warning("=" * 80)
            logger.warning("智能体任务完成！")
            logger.warning("=" * 80)
        else:
            logger.error("邮件发送失败")
    
    def _run_interactive_mode(self, user_input: str):
        """交互式模式：完全依赖用户输入"""
        # 1. 解析用户需求
        logger.info("步骤1: 解析用户需求...")
        parsed_request = self.parse_user_request(user_input)
        if self._override_config:
            override_search = self._override_config.get('search', {})
            override_email = self._override_config.get('email', {}).get('to_email')
            override_days = override_search.get('days_back')
            override_min_date = override_search.get('min_date')
            override_max_date = override_search.get('max_date')
            override_keywords = override_search.get('keywords')

            if override_keywords:
                parsed_request['keywords'] = override_keywords
            if override_days is not None:
                parsed_request['days_back'] = override_days
            if override_min_date:
                parsed_request['min_date'] = override_min_date
            if override_max_date:
                parsed_request['max_date'] = override_max_date
            if override_email:
                parsed_request['to_email'] = override_email
        
        # 2. 验证必需信息（交互式模式下，不使用配置文件默认值）
        logger.info("步骤2: 验证必需信息...")
        required_info = self._validate_and_prompt_required_info(parsed_request)
        
        if not required_info:
            logger.error("缺少必需信息，任务终止")
            return
        
        # 3. 更新配置（使用用户输入的信息，不使用配置文件默认值）
        logger.info("步骤3: 更新配置...")
        logger.info(f"解析结果详情: {required_info}")
        self.update_config_from_request(required_info, user_input)
        if self._override_config:
            self._apply_config_overrides(self._override_config)

        # 使用最终用于检索的英文关键词，向用户透明化逻辑关系（AND）
        search_cfg = self.base_agent.config.get('search', {})
        eng_keywords = search_cfg.get('keywords', []) or []
        if isinstance(eng_keywords, str):
            eng_keywords = [eng_keywords]

        if not eng_keywords:
            warn_msg = "未能从您的关键词中生成英文检索词，交互式模式不会使用配置文件中的默认关键词。请在指令中直接提供英文关键词（例如：single cell, CRISPR, organoid）。"
            print(f"\n⚠️ {warn_msg}\n")
            logger.error(warn_msg)
        else:
            if len(eng_keywords) > 1:
                if len(eng_keywords) == 2:
                    logic_msg = f"我将寻找同时包含【{eng_keywords[0]}】和【{eng_keywords[1]}】的文献，并在报告中标注每篇文献的匹配情况。"
                else:
                    head = "、".join([f"【{kw}】" for kw in eng_keywords[:-1]])
                    tail = f"和【{eng_keywords[-1]}】"
                    logic_msg = f"我将寻找同时包含{head}{tail}的文献，并在报告中标注每篇文献的匹配情况。"
            else:
                logic_msg = f"我将寻找包含【{eng_keywords[0]}】的文献，并在报告中标注每篇文献的匹配情况。"

            print(f"\n{logic_msg}\n")
            logger.info(logic_msg)
        
        # 4. 搜索文献
        logger.info("步骤4: 搜索文献...")
        papers = self.base_agent.search_literature()
        
        if not papers:
            logger.info("未找到新文献")
            return
        
        # 5. 总结文献
        logger.info("步骤5: 总结文献...")
        # 获取关键词验证信息（如果有）
        keyword_validation_info = getattr(self.base_agent, '_keyword_validation_info', None)
        summary = self.summarize_literature(papers, keyword_validation_info=keyword_validation_info)
        logger.info(f"文献总结完成（{len(summary)} 字符）")
        
        # 6. 为每篇论文生成中文总结
        logger.info("步骤6: 为每篇论文生成中文总结...")
        paper_summaries = self.summarize_papers(papers)
        logger.info(f"已为 {len(paper_summaries)} 篇论文生成中文总结")
        
        # 7. 发送邮件
        logger.info("步骤7: 发送邮件...")
        success = self.base_agent.send_email(papers, summary=summary, paper_summaries=paper_summaries)
        
        if success:
            # 使用WARNING级别，确保在默认精简日志下也能看到任务完成提示
            logger.warning("=" * 80)
            logger.warning("智能体任务完成！")
            logger.warning("=" * 80)
        else:
            logger.error("邮件发送失败")
    
    def _validate_and_prompt_required_info(self, parsed_request: Dict[str, Any]) -> Dict[str, Any]:
        """
        验证必需信息，如果缺少则提示用户输入（交互式模式）
        
        Args:
            parsed_request: 解析后的需求字典
        
        Returns:
            包含所有必需信息的字典，如果用户取消则返回None
        """
        required_info = parsed_request.copy() if parsed_request else {}
        if not required_info.get('keywords'):
            topic_keywords = required_info.get('topic_keywords')
            if isinstance(topic_keywords, list) and topic_keywords:
                required_info['keywords'] = [str(k).strip() for k in topic_keywords if str(k).strip()]
            elif required_info.get('boolean_query'):
                required_info['keywords'] = [str(required_info['boolean_query']).strip()]
        
        # 1. 验证关键词（必需）
        if not required_info.get('keywords'):
            print("\n⚠️  未找到搜索关键词")
            keywords_input = input("请输入搜索关键词（多个关键词用逗号分隔）: ").strip()
            if keywords_input:
                # 解析关键词
                keywords = [kw.strip() for kw in keywords_input.split(',') if kw.strip()]
                if keywords:
                    required_info['keywords'] = keywords
                    logger.info(f"用户输入的关键词: {keywords}")
                else:
                    logger.error("关键词格式无效")
                    return None
            else:
                logger.error("未提供关键词，任务终止")
                return None
        else:
            # 如果已有关键词，让用户确认
            keywords = required_info.get('keywords', [])
            print(f"\n📝 解析到的关键词: {', '.join(keywords)}")
            confirm = input("确认使用这些关键词？(回车确认/y=确认, n=修改): ").strip().lower()
            
            if confirm in ['', 'y', 'yes', '是']:
                # 用户确认，继续使用
                logger.info(f"用户确认关键词: {keywords}")
            else:
                # 用户要修改
                print("\n请输入新的关键词（多个关键词用逗号分隔）: ")
                keywords_input = input("> ").strip()
                if keywords_input:
                    # 解析新关键词
                    new_keywords = [kw.strip() for kw in keywords_input.split(',') if kw.strip()]
                    if new_keywords:
                        required_info['keywords'] = new_keywords
                        logger.info(f"用户修改后的关键词: {new_keywords}")
                    else:
                        logger.error("关键词格式无效，使用原关键词")
                else:
                    logger.warning("未输入新关键词，使用原关键词")
        
        # 2. 验证收件人邮箱（必需）
        if not required_info.get('to_email'):
            print("\n⚠️  未找到收件人邮箱")
            email_input = input("请输入收件人邮箱: ").strip()
            if email_input and self._validate_email(email_input):
                required_info['to_email'] = email_input
                logger.info(f"用户输入的邮箱: {email_input}")
            else:
                logger.error("邮箱格式无效或未提供，任务终止")
                return None
        else:
            # 如果已有邮箱，让用户确认
            email = required_info.get('to_email')
            print(f"\n📧 解析到的收件人邮箱: {email}")
            confirm = input("确认使用此邮箱？(回车确认/y=确认, n=修改): ").strip().lower()
            
            if confirm in ['', 'y', 'yes', '是']:
                # 用户确认，继续使用
                logger.info(f"用户确认邮箱: {email}")
            else:
                # 用户要修改
                print("\n请输入新的收件人邮箱: ")
                email_input = input("> ").strip()
                if email_input and self._validate_email(email_input):
                    required_info['to_email'] = email_input
                    logger.info(f"用户修改后的邮箱: {email_input}")
                else:
                    logger.error("邮箱格式无效，使用原邮箱")
        
        # 3. 验证时间范围（必需）
        if not required_info.get('min_date') and not required_info.get('max_date') and not required_info.get('days_back'):
            print("\n⚠️  未找到时间范围")
            print("请选择时间范围：")
            print("1. 最近N天（例如：最近7天）")
            print("2. 具体年份（例如：2026年）")
            print("3. 日期范围（例如：2026-01-01 到 2026-12-31）")
            time_input = input("请输入时间范围: ").strip()
            
            if time_input:
                # 重新解析时间范围
                time_parsed = self._parse_date_range(time_input)
                if time_parsed:
                    required_info.update(time_parsed)
                    logger.info(f"用户输入的时间范围: {time_parsed}")
                else:
                    logger.error("时间范围格式无效，任务终止")
                    return None
            else:
                logger.error("未提供时间范围，任务终止")
                return None
        else:
            # 如果已有时间范围，让用户确认
            time_info = []
            if required_info.get('days_back'):
                time_info.append(f"最近{required_info['days_back']}天")
            elif required_info.get('min_date') and required_info.get('max_date'):
                time_info.append(f"{required_info['min_date']} 至 {required_info['max_date']}")
            elif required_info.get('min_date'):
                time_info.append(f"从 {required_info['min_date']} 至今")
            
            if time_info:
                print(f"\n📅 解析到的时间范围: {', '.join(time_info)}")
                confirm = input("确认使用此时间范围？(回车确认/y=确认, n=修改): ").strip().lower()
                
                if confirm in ['', 'y', 'yes', '是']:
                    # 用户确认，继续使用
                    logger.info(f"用户确认时间范围: {time_info}")
                else:
                    # 用户要修改
                    print("\n请选择时间范围：")
                    print("1. 最近N天（例如：最近7天）")
                    print("2. 具体年份（例如：2026年）")
                    print("3. 日期范围（例如：2026-01-01 到 2026-12-31）")
                    time_input = input("请输入时间范围: ").strip()
                    
                    if time_input:
                        # 重新解析时间范围
                        time_parsed = self._parse_date_range(time_input)
                        if time_parsed:
                            # 清除旧的时间范围
                            required_info.pop('days_back', None)
                            required_info.pop('min_date', None)
                            required_info.pop('max_date', None)
                            # 更新新的时间范围
                            required_info.update(time_parsed)
                            logger.info(f"用户修改后的时间范围: {time_parsed}")
                        else:
                            logger.error("时间范围格式无效，使用原时间范围")
                    else:
                        logger.warning("未输入新时间范围，使用原时间范围")
        
        return required_info
    
    def _expand_search_keywords_with_llm(self, keywords: List[str]) -> List[str]:
        """
        使用LLM将用户提供的关键词（中文/英文/混合）扩展为适合PubMed的英文检索词列表
        """
        if not self.use_llm or not keywords:
            return keywords
        
        # 构造提示词（从文件加载，否则使用默认）
        prompt_template = self._load_prompt("expand_keywords")
        if not prompt_template:
            prompt_template = """你是一个熟悉PubMed检索的生物医学文献专家。

现在有一组用户提供的检索关键词（可能是中文、英文或中英文混合），请将它们转换为适合在PubMed中使用的**英文检索词列表**。

要求：
1. 对每个关键词，给出1-3个常用的英文等价词或近义表达（适合用于Title/Abstract检索）
2. 保持短语简洁，例如：
   - "单细胞" → ["single cell", "single-cell", "scRNA-seq"]
   - "AI" → ["artificial intelligence", "AI", "deep learning"]
3. 不要包含中文，不要包含解释性句子，只要英文短语
4. 只返回一个JSON对象，格式如下：
{
  "expanded_keywords": ["英文短语1", "英文短语2", "..."]
}

用户提供的原始关键词列表：
{keywords}

请按照要求返回JSON："""
        
        # 注意：prompt_template 中包含JSON示例的大括号，不能直接用str.format
        # 这里只做一个简单的占位符替换，避免误解析其他花括号
        prompt = prompt_template.replace("{keywords}", ", ".join(keywords))
        
        try:
            response = self.llm_client.generate(prompt, max_tokens=400, temperature=0.2)
            import json, re
            # 尽量提取**完整**的JSON对象：
            # - 使用findall并选择最长的那一段，避免非贪婪匹配截断嵌套结构
            # - 仍然只能基于大括号启发式，但在LLM严格提示下通常足够
            json_candidates = re.findall(r'\{[\s\S]*\}', response)
            if not json_candidates:
                logger.warning(f"无法从LLM扩展关键词响应中提取JSON，原始响应: {response[:200]}...")
                return keywords
            # 选择最长的候选，尽可能包含完整mapping
            raw_json = max(json_candidates, key=len).strip()
            try:
                data = json.loads(raw_json)
            except Exception as je:
                # 尝试使用 ast.literal_eval 作为宽松解析（允许单引号、尾逗号等）
                try:
                    import ast
                    data = ast.literal_eval(raw_json)
                    logger.warning(f"JSON解析失败，但通过literal_eval成功解析关键词扩展结果: {raw_json[:200]}..., 原始错误: {je}")
                except Exception as je2:
                    logger.warning(
                        f"解析LLM扩展关键词JSON失败，将使用原始关键词。"
                        f"原始片段: {raw_json[:200]}..., 错误1: {je}; 错误2: {je2}"
                    )
                    return keywords
            # 优先使用 keyword_mapping（如果存在），否则使用 expanded_keywords
            keyword_mapping = data.get("keyword_mapping", {})
            expanded = data.get("expanded_keywords", [])
            
            # 如果有关键词映射，优先使用（保留每个原始关键词对应的扩展词）
            if keyword_mapping:
                # 为每个原始关键词选择最佳扩展词（第一个）
                primary_expanded = []
                for orig_kw in keywords:
                    orig_kw_str = str(orig_kw).strip()
                    if orig_kw_str in keyword_mapping:
                        mapped = keyword_mapping[orig_kw_str]
                        if mapped and len(mapped) > 0:
                            # 选择第一个（通常是最常用的）
                            primary_expanded.append(str(mapped[0]).strip())
                
                # 如果主要扩展词存在，使用它们；否则使用扁平列表
                if primary_expanded:
                    # 清理：去重、去空
                    cleaned = []
                    for kw in primary_expanded:
                        kw_str = str(kw).strip()
                        if kw_str and kw_str not in cleaned:
                            cleaned.append(kw_str)
                    
                    if cleaned:
                        # 进一步过滤：只保留英文关键词
                        english_cleaned = self._filter_english_keywords(cleaned)
                        if english_cleaned:
                            logger.info(f"✅ 使用用户关键词对应的主要扩展词: {english_cleaned}")
                            return english_cleaned
            
            # 回退到扁平列表
            if expanded:
                cleaned = []
                for kw in expanded:
                    kw_str = str(kw).strip()
                    if kw_str and kw_str not in cleaned:
                        cleaned.append(kw_str)
                
                if cleaned:
                    english_cleaned = self._filter_english_keywords(cleaned)
                    if english_cleaned:
                        return english_cleaned
            
            logger.warning("LLM扩展后的关键词列表为空或无效，使用原始关键词")
            return keywords
        except Exception as e:
            logger.warning(f"LLM扩展关键词失败: {e}，使用原始关键词")
            return keywords
    
    def _validate_email(self, email: str) -> bool:
        """验证邮箱格式"""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return bool(re.match(pattern, email))
    
    def _parse_time_with_llm(self, user_input: str) -> Dict[str, Any]:
        """
        使用LLM解析时间范围（兜底方案）
        返回:
          - {'days_back': int} 或 {'min_date': 'YYYY-MM-DD', 'max_date': 'YYYY-MM-DD'}
          - 解析失败返回 {}
        """
        if not self.use_llm:
            return {}

        prompt_template = self._load_prompt("parse_time_range")
        if not prompt_template:
            # 默认提示词（如果忘了创建 prompts/parse_time_range.txt）
            prompt_template = """你是一个时间范围解析助手。
用户需求：{user_input}

请从中解析出文献检索的时间范围，并只输出JSON：
- 如果是"最近N年/近N年/过去N年/最近N月/最近N天"等相对时间，用days_back字段（整数，天数）。
- 如果是"2020年到2023年"、"2015年至今"、"2010年-2012年"等具体年份或日期，返回min_date和max_date（YYYY-MM-DD）。

输出格式示例：
{{
  "days_back": 365,
  "min_date": null,
  "max_date": null
}}

或：

{{
  "days_back": null,
  "min_date": "2015-01-01",
  "max_date": "2024-12-31"
}}
"""
        import re, json
        prompt = prompt_template.format(user_input=user_input)
        try:
            resp = self.llm_client.generate(prompt, max_tokens=300, temperature=0.1)
            json_match = re.search(r'\{.*?\}', resp, re.DOTALL)
            if not json_match:
                logger.warning(f"LLM时间解析未返回JSON，原始响应: {resp[:200]}...")
                return {}
            data = json.loads(json_match.group(0))
            days_back = data.get("days_back")
            min_date = data.get("min_date")
            max_date = data.get("max_date")
            result: Dict[str, Any] = {}
            if isinstance(days_back, int) and days_back > 0:
                result["days_back"] = days_back
            if isinstance(min_date, str) and isinstance(max_date, str):
                result["min_date"] = min_date
                result["max_date"] = max_date
            logger.info(f"LLM解析时间范围结果: {result}")
            return result
        except Exception as e:
            logger.warning(f"LLM解析时间范围失败: {e}")
            return {}
    
    def _find_keyword_evidence(self, keyword: str, keyword_evidence: Dict[str, str]) -> Optional[str]:
        """
        在 keyword_evidence 字典中查找关键词对应的证据。
        查找顺序: 精确匹配 → 忽略大小写 → 子串匹配 → 取第一个可用值。
        """
        if not keyword_evidence:
            return None
        # 1. 精确匹配
        if keyword in keyword_evidence:
            return keyword_evidence[keyword]
        # 2. 忽略大小写
        kw_lower = keyword.lower()
        for key, value in keyword_evidence.items():
            if key.lower() == kw_lower:
                return value
        # 3. 子串匹配
        for key, value in keyword_evidence.items():
            if kw_lower in key.lower() or key.lower() in kw_lower:
                return value
        # 4. 第一个可用值
        return next(iter(keyword_evidence.values()), None)

    def _generate_validation_table(self, papers: List, keyword_validation_info: Dict) -> str:
        """
        生成关键词匹配验证表（优化可读性）

        Args:
            papers: 论文列表
            keyword_validation_info: 关键词验证信息

        Returns:
            验证表的Markdown格式文本
        """
        if not keyword_validation_info or 'validation_details' not in keyword_validation_info:
            return ""
        
        validation_details = keyword_validation_info.get('validation_details', {})
        strict_count = keyword_validation_info.get('strict_matched', 0)
        
        # 获取语义关键词列表（用于验证的关键词，与keyword_evidence的key对应）
        # keyword_evidence的key是基于semantic_keywords生成的，所以这里必须使用semantic_keywords
        semantic_keywords = self.base_agent.config['search'].get('semantic_keywords', [])
        if not semantic_keywords:
            # 如果没有semantic_keywords，回退到keywords（可能是英文）
            keywords = self.base_agent.config['search'].get('keywords', [])
            if isinstance(keywords, str):
                keywords = [keywords]
            # 如果keywords是查询字符串（包含AND/OR），尝试提取关键词
            if keywords and isinstance(keywords[0], str) and (' AND ' in keywords[0] or ' OR ' in keywords[0]):
                # 尝试从查询字符串中提取关键词
                import re
                query = keywords[0]
                # 提取括号中的关键词
                extracted = re.findall(r'\(([^)]+)\)', query)
                if extracted:
                    keywords = extracted
                else:
                    # 如果没有括号，按AND/OR分割
                    keywords = re.split(r'\s+(?:AND|OR)\s+', query)
            semantic_keywords = keywords
        keywords = semantic_keywords
        
        # 如果只有一个关键词，简化表格格式
        if len(keywords) == 1:
            table = "\n\n## 【关键词匹配验证表】\n\n"
            table += f"**统计**：严格匹配 {strict_count} 篇\n\n"
            table += "| 序号 | 标题 | 关键词证据 | 是否完全匹配 |\n"
            table += "|------|------|-------------|--------------|\n"
            
            row_index = 0
            for paper in papers:
                title = paper.title or '无标题'
                # 标题截断到70字符，保持可读性
                if len(title) > 70:
                    title = title[:67] + "..."
                
                paper_info = validation_details.get(paper.title or '未知', {})
                keyword_evidence = paper_info.get('keyword_evidence', {})
                all_match = paper_info.get('all_match', False)
                if not all_match:
                    continue
                
                row_index += 1
                
                # 获取第一个（也是唯一一个）关键词的证据
                kw = keywords[0] if keywords else ""
                evidence = self._find_keyword_evidence(kw, keyword_evidence)
                if not evidence:
                    evidence = "未提及"
                # 证据内容控制在40字符以内，保持简洁
                if len(evidence) > 40:
                    evidence = evidence[:37] + "..."
                
                match_status = "✅"
                table += f"| {row_index} | {title} | {evidence} | {match_status} |\n"
        else:
            # 多个关键词：合并显示所有证据，或只显示主要证据
            table = "\n\n## 【关键词匹配验证表】\n\n"
            table += f"**统计**：严格匹配 {strict_count} 篇\n\n"
            # 简化表头：只显示"关键词证据"（合并列）
            table += "| 序号 | 标题 | 关键词证据 | 是否完全匹配 |\n"
            table += "|------|------|-------------|--------------|\n"
            
            row_index = 0
            for paper in papers:
                title = paper.title or '无标题'
                # 标题截断到70字符
                if len(title) > 70:
                    title = title[:67] + "..."
                
                paper_info = validation_details.get(paper.title or '未知', {})
                keyword_evidence = paper_info.get('keyword_evidence', {})
                all_match = paper_info.get('all_match', False)
                if not all_match:
                    continue
                
                row_index += 1
                
                # 合并所有关键词的证据，用分号分隔
                evidence_parts = []
                for kw in keywords:
                    ev = self._find_keyword_evidence(kw, keyword_evidence)
                    if ev and ev not in ["未提及", "未检查", ""]:
                        # 简化证据文本，去掉冗余描述
                        ev_clean = ev.replace("摘要中提及", "").replace("方法中", "").replace("结果中", "").strip()
                        if ev_clean and ev_clean not in ["未提及", "未检查"]:
                            evidence_parts.append(ev_clean[:25])  # 每个关键词证据最多25字符
                
                # 合并证据，总长度控制在60字符以内
                if evidence_parts:
                    combined_evidence = "；".join(evidence_parts)
                    if len(combined_evidence) > 60:
                        combined_evidence = combined_evidence[:57] + "..."
                else:
                    # 如果所有关键词都没有证据，尝试获取所有可用的证据
                    all_evidence = [v for v in keyword_evidence.values() if v and v not in ["未提及", "未检查", ""]]
                    if all_evidence:
                        combined_evidence = "；".join([e[:20] for e in all_evidence[:3]])  # 最多显示3个证据
                        if len(combined_evidence) > 60:
                            combined_evidence = combined_evidence[:57] + "..."
                    else:
                        combined_evidence = "未找到实质性使用证据"
                
                match_status = "✅"
                table += f"| {row_index} | {title} | {combined_evidence} | {match_status} |\n"
        
        return table

    # ------------------------------------------------------------------
    # 智能交互处理
    # ------------------------------------------------------------------

    def handle_user_input(self, user_input: str, default_email: str = None) -> Dict[str, Any]:
        """
        智能处理用户输入，自动分类意图并执行相应操作。

        Args:
            user_input: 用户输入的自然语言
            default_email: 默认邮箱（从配置中获取）

        Returns:
            {"status": "success"|"error"|"chat", "message": str, "intent": str}
        """
        intent_result = _classify_user_intent(user_input)
        intent = intent_result["intent"]
        params = intent_result["params"]
        confidence = intent_result["confidence"]

        logger.info(f"意图识别: {intent} (置信度: {confidence:.2f})")

        # 1. 帮助意图
        if intent == UserIntent.HELP:
            return {
                "status": "chat",
                "message": self._get_help_message(),
                "intent": intent
            }

        # 2. 本地 PDF 解析
        if intent == UserIntent.LOCAL_PDF:
            pdf_path = params.get("pdf_path")
            email = params.get("email") or default_email
            title = params.get("title")

            if not email:
                return {
                    "status": "error",
                    "message": "请提供收件邮箱，例如：解析 /path/to/file.pdf 发送到 xxx@qq.com",
                    "intent": intent
                }

            result = self.analyze_local_pdf(pdf_path, email, title=title)
            return {**result, "intent": intent}

        # 3. 单篇文献解析
        if intent == UserIntent.SINGLE_PAPER:
            paper_id = params.get("paper_id")
            email = params.get("email") or default_email

            if not email:
                return {
                    "status": "error",
                    "message": "请提供收件邮箱，例如：解析 PMID 发送到 xxx@qq.com",
                    "intent": intent
                }

            result = self.analyze_paper(paper_id, email)
            return {**result, "intent": intent}

        # 4. 文献搜索
        if intent == UserIntent.LITERATURE_SEARCH:
            # 交给现有的搜索流程处理
            return {
                "status": "search",
                "message": "执行文献搜索",
                "intent": intent,
                "query": params.get("query")
            }

        # 5. 闲聊/无法识别 - 用 LLM 回复
        if intent == UserIntent.CHAT:
            response = self._llm_chat_response(user_input)
            return {
                "status": "chat",
                "message": response,
                "intent": intent
            }

        return {"status": "error", "message": "未知错误", "intent": intent}

    def _get_help_message(self) -> str:
        """返回帮助信息"""
        return """我是文献智能体，可以帮你：

1. **搜索文献**
   - "找最近7天关于单细胞和AI的文献，发送到 xxx@qq.com"
   - "搜索 CRISPR 相关的综述"

2. **解析单篇论文**（PMID/DOI/标题）
   - "解析 38096903 发到 xxx@qq.com"
   - "帮我分析 10.1038/s41586-023-06924-6"
   - "解读这篇论文 BiOmics: A Foundational Agent"

3. **解析本地 PDF**
   - "解析 ~/Downloads/paper.pdf 发到 xxx@qq.com"
   - "分析 /path/to/论文.pdf 标题是 XXX"

请告诉我你的需求，我会尽力帮助你！"""

    def _llm_chat_response(self, user_input: str) -> str:
        """用 LLM 回复无法识别的用户输入"""
        if not self.use_llm or not self.llm_client:
            return self._get_help_message()

        prompt = f"""你是一个文献检索智能助手。用户输入了以下内容，但我无法明确识别其意图：

用户输入：{user_input}

请你：
1. 如果用户似乎想搜索文献或解析论文，引导他们提供更明确的信息（如关键词、PMID、DOI、PDF路径、邮箱等）
2. 如果用户在闲聊，友好回复并引导他们使用文献功能
3. 回复要简洁，不超过100字

我支持的功能：
- 文献搜索（需要：关键词 + 邮箱）
- 单篇解析（需要：PMID/DOI/标题 + 邮箱）
- 本地PDF解析（需要：PDF路径 + 邮箱）"""

        try:
            response = self.llm_client.generate(prompt, max_tokens=300, temperature=0.7)
            return response.strip()
        except Exception as e:
            logger.warning(f"LLM 回复失败: {e}")
            return self._get_help_message()

    # ------------------------------------------------------------------
    # 单篇文献快速解析
    # ------------------------------------------------------------------

    def analyze_local_pdf(self, pdf_path: str, to_email: str, title: str = None) -> Dict[str, Any]:
        """
        解析本地 PDF 文件 → LLM 结构化解析 → 发送邮件

        Args:
            pdf_path: 本地 PDF 文件路径
            to_email: 收件人邮箱
            title: 可选的论文标题（若不提供则从 PDF 文件名推断）

        Returns:
            {"status": "success"|"error", "message": str, "title": str}
        """
        from literature.pdf_downloader import PDFDownloader
        from literature.base_client import PaperMetadata, PaperSource, Author

        pdf_file = Path(pdf_path)
        if not pdf_file.exists():
            return {"status": "error", "message": f"PDF 文件不存在: {pdf_path}", "title": ""}

        if not pdf_file.suffix.lower() == '.pdf':
            return {"status": "error", "message": f"不是 PDF 文件: {pdf_path}", "title": ""}

        # 推断标题（使用文件名或用户提供的标题）
        inferred_title = title or pdf_file.stem.replace('_', ' ').replace('-', ' ')
        logger.info(f"[本地PDF解析] 文件: {pdf_file.name}, 标题: {inferred_title}")

        # 提取 PDF 文本
        downloader = PDFDownloader()
        content = downloader._extract_text_from_pdf(str(pdf_file))

        if not content or len(content) < 100:
            return {"status": "error", "message": "PDF 内容提取失败或内容过少", "title": inferred_title}

        logger.info(f"[本地PDF解析] 提取文本 {len(content)} 字符")

        # 读取 PDF 字节（用于邮件附件）
        pdf_bytes = pdf_file.read_bytes()

        # 构造一个简化的 PaperMetadata 对象
        paper = PaperMetadata(
            id=f"local:{pdf_file.name}",
            source=PaperSource.MANUAL,
            title=inferred_title,
            abstract="",
            authors=[],
            journal="本地文件",
            year=None
        )

        # 调用 LLM 解析
        source_desc = "本地PDF全文"
        analysis = self._analyze_paper_with_llm(paper, content, source_desc)

        # 发送邮件
        ok = self._send_single_paper_email(paper, analysis, source_desc, to_email, pdf_bytes)
        if ok:
            return {"status": "success", "message": f"解析结果已发送至 {to_email}", "title": inferred_title}
        else:
            return {"status": "error", "message": "邮件发送失败，请检查 SMTP 配置", "title": inferred_title}

    def analyze_paper(self, query: str, to_email: str) -> Dict[str, Any]:
        """
        获取单篇文献 → LLM 结构化解析 → 发送邮件

        Args:
            query: PMID（6-8位纯数字）、DOI（10.xxx/...）或标题文本
            to_email: 收件人邮箱

        Returns:
            {"status": "success"|"error", "message": str, "title": str}
        """
        from literature.paper_fetcher import SinglePaperFetcher
        from literature.pdf_downloader import PDFDownloader
        from literature.bgpt_client import BGPTClient

        # Step 1: 获取文献元数据
        logger.info(f"[单篇解析] 获取文献: {query}")
        paper = SinglePaperFetcher().fetch(query)
        if not paper:
            return {"status": "error", "message": f"未找到文献: {query}", "title": ""}

        logger.info(f"[单篇解析] 找到文献: {paper.title[:60]}")

        # Step 2a: 优先尝试 BGPT（直接返回结构化全文数据，无需下载 PDF + LLM 解析）
        analysis = None
        pdf_bytes = None
        source_desc = None

        bgpt_api_key = self.base_agent.config.get('bgpt', {}).get('api_key') or None
        bgpt = BGPTClient(api_key=bgpt_api_key)
        bgpt_data = None
        if paper.doi:
            bgpt_data = bgpt.fetch_by_doi(paper.doi)
        if not bgpt_data and paper.title:
            bgpt_data = bgpt.fetch_by_title(paper.title)

        # 先尝试获取全文/PDF（无论 BGPT 是否命中都需要）
        content, pdf_source, pdf_bytes = PDFDownloader().get_fulltext(paper)
        if pdf_bytes:
            logger.warning(f"[单篇解析] PDF 附件已下载 ({len(pdf_bytes)//1024} KB) 来源: {pdf_source}")
        else:
            logger.warning(f"[单篇解析] 无可用 PDF 附件（来源: {pdf_source}）")

        # 检查 BGPT 数据是否有效（不能全是空或横杠）
        bgpt_valid = bgpt_data and self._is_analysis_valid(bgpt_data)

        if bgpt_valid:
            source_desc = "BGPT全文解析"
            logger.warning(f"[单篇解析] BGPT 命中且内容有效，使用结构化数据")
            # BGPT 返回英文，调 LLM 翻译成中文
            analysis = self._translate_bgpt_analysis(bgpt_data)
        else:
            # BGPT 未命中或内容无效，回退到 LLM 解析
            if bgpt_data:
                logger.warning(f"[单篇解析] BGPT 命中但内容无效，降级到 LLM 解析")
            else:
                logger.warning(f"[单篇解析] BGPT 未命中，使用 LLM 解析")

            if not content:
                return {"status": "error", "message": "文献内容为空", "title": paper.title}
            source_desc = pdf_source
            logger.warning(f"[单篇解析] 内容来源: {source_desc}，字符数: {len(content)}")
            analysis = self._analyze_paper_with_llm(paper, content, source_desc)

        # Step 3: 发送邮件（含 PDF 附件，如有）
        ok = self._send_single_paper_email(paper, analysis, source_desc, to_email, pdf_bytes)
        if ok:
            return {"status": "success", "message": f"解析结果已发送至 {to_email}", "title": paper.title}
        else:
            return {"status": "error", "message": "邮件发送失败，请检查 SMTP 配置", "title": paper.title}

    def _analyze_paper_with_llm(self, paper, content: str, source_desc: str) -> Dict[str, Any]:
        """调用 LLM 生成结构化解析结果，失败时自动用精简 prompt 重试"""
        import json

        if not self.use_llm or not self.llm_client:
            return {
                "研究背景": "LLM 未配置，无法解析",
                "研究目的": "", "方法": "",
                "主要发现": [paper.abstract[:200] if paper.abstract else ""],
                "结论与意义": "", "局限性": "",
                "解析级别": source_desc
            }

        authors = paper.authors or []
        authors_str = ", ".join(a.name for a in authors[:5])
        if len(authors) > 5:
            authors_str += " et al."

        prompt_vars = {
            "title": paper.title or "N/A",
            "authors": authors_str or "N/A",
            "journal": paper.journal or "N/A",
            "date": str(paper.year or paper.publication_date or "N/A"),
            "doi": paper.doi or "N/A",
            "source_type": source_desc,
            "content": content
        }

        # 第一次尝试：完整 prompt
        result = self._try_llm_analyze(prompt_vars, "analyze_single_paper", max_tokens=4000)
        if result:
            result.setdefault("解析级别", source_desc)
            return result

        # 第二次尝试：精简 prompt
        logger.warning("完整解析失败，使用精简 prompt 重试...")
        result = self._try_llm_analyze(prompt_vars, "analyze_single_paper_lite", max_tokens=2000)
        if result:
            result.setdefault("解析级别", f"{source_desc}(精简)")
            return result

        # 全部失败
        return {
            "研究背景": "解析失败，请查看原文",
            "研究目的": "", "方法": "",
            "主要发现": [],
            "结论与意义": "", "局限性": "",
            "解析级别": source_desc
        }

    def _try_llm_analyze(self, prompt_vars: Dict, prompt_name: str, max_tokens: int) -> Optional[Dict[str, Any]]:
        """尝试用指定 prompt 进行 LLM 解析，返回 None 表示失败"""
        import json

        prompt_file = self.prompts_dir / f"{prompt_name}.txt"
        if not prompt_file.exists():
            logger.warning(f"Prompt 文件不存在: {prompt_file}")
            return None

        template = prompt_file.read_text(encoding='utf-8')
        prompt = template.format(**prompt_vars)

        try:
            raw = self.llm_client.generate(prompt, max_tokens=max_tokens, temperature=0.1)
            json_str = raw.strip()

            # 提取 JSON（LLM 可能包裹在 ```json ... ``` 中）
            if "```" in json_str:
                import re as _re
                m = _re.search(r'```(?:json)?\s*([\s\S]+?)```', json_str)
                if m:
                    json_str = m.group(1).strip()

            # 尝试解析 JSON
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                # 尝试修复截断的 JSON
                json_str_fixed = self._try_fix_truncated_json(json_str)
                return json.loads(json_str_fixed)

        except Exception as e:
            logger.warning(f"LLM 解析失败 ({prompt_name}): {e}")
            return None

    def _try_fix_truncated_json(self, json_str: str) -> str:
        """尝试修复被截断的 JSON 字符串"""
        s = json_str.rstrip()

        # 统计未闭合的括号和引号
        in_string = False
        escape_next = False
        brace_count = 0
        bracket_count = 0

        for ch in s:
            if escape_next:
                escape_next = False
                continue
            if ch == '\\':
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                brace_count += 1
            elif ch == '}':
                brace_count -= 1
            elif ch == '[':
                bracket_count += 1
            elif ch == ']':
                bracket_count -= 1

        # 如果在字符串中截断，先闭合字符串
        if in_string:
            s += '"'

        # 闭合未闭合的括号
        s += ']' * bracket_count
        s += '}' * brace_count

        return s

    def _send_single_paper_email(self, paper, analysis: Dict, source_desc: str, to_email: str,
                                   pdf_bytes: Optional[bytes] = None) -> bool:
        """构建单篇解析 HTML 邮件并发送，如有 PDF 字节则作为附件"""
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.mime.base import MIMEBase
        from email import encoders as _encoders
        from datetime import datetime as _dt

        email_cfg = self.base_agent.config.get('email', {})
        smtp_server = email_cfg.get('smtp_server')
        smtp_port = email_cfg.get('smtp_port', 587)
        smtp_username = email_cfg.get('smtp_username')
        smtp_password = email_cfg.get('smtp_password')

        if not all([smtp_server, smtp_username, smtp_password]):
            logger.error("SMTP 配置不完整")
            return False

        authors = paper.authors or []
        authors_str = ", ".join(a.name for a in authors[:5])
        if len(authors) > 5:
            authors_str += " et al."

        doi_link = (
            f'<a href="https://doi.org/{paper.doi}">{paper.doi}</a>'
            if paper.doi else "N/A"
        )
        pubmed_link = (
            f'<a href="https://pubmed.ncbi.nlm.nih.gov/{paper.pubmed_id}/">[PubMed]</a>'
            if paper.pubmed_id else ""
        )
        parse_level = analysis.get("解析级别", source_desc)
        level_color = "#27ae60" if "全文" in parse_level else "#e67e22"

        # 新格式字段
        overview = analysis.get("全文概述", "")
        terms = analysis.get("术语解释", [])
        experiments = analysis.get("论文实验", "")
        figures = analysis.get("关键图表解读", [])
        conclusions = analysis.get("核心结论", [])
        limitations = analysis.get("局限性与展望", "")

        # 兼容旧格式（BGPT翻译结果）
        if not overview:
            overview = analysis.get("研究背景", "")
        if not conclusions:
            conclusions = analysis.get("主要发现", [])
        if not limitations:
            limitations = analysis.get("局限性", "")
        if not experiments:
            experiments = analysis.get("方法", "")

        # 构建术语解释 HTML
        terms_html = ""
        if terms and isinstance(terms, list):
            for t in terms:
                if isinstance(t, dict):
                    term_name = t.get("术语", "")
                    term_def = t.get("解释", "")
                    terms_html += f'<li><strong>{term_name}</strong>：{term_def}</li>'
                else:
                    terms_html += f'<li>{t}</li>'

        # 构建图表解读 HTML
        figures_html = ""
        if figures and isinstance(figures, list):
            for fig in figures:
                if isinstance(fig, dict):
                    fig_num = fig.get("图表", "")
                    fig_title = fig.get("标题", "")
                    fig_desc = fig.get("解读", "")
                    figures_html += f'''<div style="margin-bottom:12px;padding:10px;background:#f8f9fa;border-radius:6px;">
                      <div style="font-weight:bold;color:#2980b9;">{fig_num}：{fig_title}</div>
                      <div style="margin-top:6px;">{fig_desc}</div>
                    </div>'''
                else:
                    figures_html += f'<div style="margin-bottom:8px;">{fig}</div>'

        # 构建核心结论 HTML
        conclusions_html = ""
        if isinstance(conclusions, list):
            conclusions_html = "".join(f"<li>{c}</li>" for c in conclusions)
        else:
            conclusions_html = f"<li>{conclusions}</li>"

        def section(title, content, icon=""):
            if not content or content == "—":
                return ""
            return f'''<div style="margin-bottom:20px;">
              <h3 style="color:#2c3e50;font-size:1em;margin:0 0 10px 0;border-left:4px solid #3498db;padding-left:10px;">
                {icon} {title}
              </h3>
              <div style="padding-left:14px;line-height:1.8;">{content}</div>
            </div>'''

        html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
        line-height:1.6;color:#333;max-width:800px;margin:0 auto;padding:24px;}}
  .hdr{{border-bottom:3px solid #2980b9;padding-bottom:14px;margin-bottom:24px;}}
  .hdr h1{{font-size:1.1em;color:#2980b9;margin:0 0 6px 0;}}
  .title{{font-size:1.3em;font-weight:bold;color:#1a252f;margin:6px 0;}}
  .meta{{color:#7f8c8d;font-size:0.9em;margin:3px 0;}}
  .badge{{display:inline-block;padding:2px 10px;border-radius:12px;font-size:0.8em;
          font-weight:bold;color:#fff;background:{level_color};margin-left:6px;}}
  ul{{margin:0;padding-left:18px;}} ul li{{margin:6px 0;line-height:1.7;}}
  .content{{background:#fff;}}
  .footer{{text-align:center;color:#bdc3c7;font-size:0.8em;
           margin-top:28px;padding-top:14px;border-top:1px solid #ecf0f1;}}
</style></head><body>
<div class="hdr">
  <h1>论文深度解读</h1>
  <div class="title">{paper.title or 'N/A'}</div>
  <div class="meta">{authors_str or 'N/A'}</div>
  <div class="meta">{paper.journal or 'N/A'} | {paper.year or paper.publication_date or 'N/A'}</div>
  <div class="meta">DOI: {doi_link} {pubmed_link}
    <span class="badge">{parse_level}</span>
  </div>
</div>
<div class="content">
  {section('全文概述', overview, '')}
  {section('术语解释', f'<ul>{terms_html}</ul>' if terms_html else '', '')}
  {section('论文实验', experiments, '')}
  {section('关键图表解读', figures_html, '') if figures_html else ''}
  {section('核心结论', f'<ul>{conclusions_html}</ul>', '')}
  {section('局限性与展望', limitations, '')}
</div>
<p style="color:#95a5a6;font-size:0.85em;margin-top:16px;">
  全文来源：{source_desc} | 生成时间：{_dt.now().strftime('%Y-%m-%d %H:%M')}
</p>
<div class="footer">由 Bioinfo Literature Daily 自动生成</div>
</body></html>"""

        title_short = (paper.title or "未知标题")[:40]
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"论文深度解读：{title_short}"
        msg["From"] = email_cfg.get('from_email', smtp_username)
        msg["To"] = to_email
        msg.attach(MIMEText(html, "html", "utf-8"))

        # 附加 PDF 文件（仅当成功下载时）
        if pdf_bytes:
            safe_title = re.sub(r'[^\w\s-]', '', paper.title or 'paper')[:40].strip().replace(' ', '_')
            pdf_filename = f"{safe_title}.pdf"
            part = MIMEBase("application", "octet-stream")
            part.set_payload(pdf_bytes)
            _encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment",
                            filename=("utf-8", "", pdf_filename))
            msg.attach(part)
            logger.info(f"附加 PDF 附件: {pdf_filename} ({len(pdf_bytes)//1024} KB)")

        try:
            server = smtplib.SMTP(smtp_server, smtp_port, timeout=10)
            if email_cfg.get('use_tls', True):
                server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(msg)
            server.quit()
            logger.info(f"单篇解析邮件已发送至 {to_email}")
            return True
        except Exception as e:
            logger.error(f"邮件发送失败: {e}")
            return False

    def _is_analysis_valid(self, analysis: Dict) -> bool:
        """检查解析结果是否有效（不能全是空或横杠）"""
        # 新格式字段 + 旧格式字段
        key_fields = ["全文概述", "论文实验", "核心结论",
                      "研究背景", "研究目的", "方法", "结论与意义"]
        valid_count = 0
        for field in key_fields:
            value = analysis.get(field, "")
            # 列表类型检查非空
            if isinstance(value, list) and len(value) > 0:
                valid_count += 1
            # 字符串类型：非空、非横杠、长度 > 10
            elif value and value != "—" and len(str(value)) > 10:
                valid_count += 1
        # 至少 2 个关键字段有效才算通过
        return valid_count >= 2

    def _translate_bgpt_analysis(self, bgpt_data: Dict) -> Dict:
        """将 BGPT 英文解析结果翻译成中文"""
        import json

        if not self.use_llm or not self.llm_client:
            logger.warning("LLM 未配置，无法翻译 BGPT 结果，保持英文")
            return bgpt_data

        # 构建翻译 prompt
        content_to_translate = {
            "研究背景": bgpt_data.get("研究背景", ""),
            "研究目的": bgpt_data.get("研究目的", ""),
            "方法": bgpt_data.get("方法", ""),
            "主要发现": bgpt_data.get("主要发现", []),
            "结论与意义": bgpt_data.get("结论与意义", ""),
            "局限性": bgpt_data.get("局限性", ""),
        }

        prompt = f"""请将以下论文解析内容翻译成中文，保持原有结构，直接输出 JSON，不要有其他文字：

{json.dumps(content_to_translate, ensure_ascii=False, indent=2)}

要求：
1. 专业术语保留英文缩写并加中文解释，如 "GPCR (G蛋白偶联受体)"
2. 保持学术语言风格
3. 主要发现保持列表格式
4. 严格输出 JSON"""

        try:
            raw = self.llm_client.generate(prompt, max_tokens=2000, temperature=0.1)
            json_str = raw.strip()
            if "```" in json_str:
                import re as _re
                m = _re.search(r'```(?:json)?\s*([\s\S]+?)```', json_str)
                if m:
                    json_str = m.group(1).strip()
            translated = json.loads(json_str)
            translated["解析级别"] = "BGPT全文解析"
            logger.info("[单篇解析] BGPT 结果已翻译成中文")
            return translated
        except Exception as e:
            logger.warning(f"翻译失败，保持英文: {e}")
            return bgpt_data

    def _default_analyze_prompt(self) -> str:
        return """你是生物信息学领域专家。请根据以下论文内容进行结构化解析，严格输出 JSON，不要包含其他文字。

【论文信息】
标题：{title}  作者：{authors}  期刊：{journal}  时间：{date}  DOI：{doi}
内容来源：{source_type}

【论文内容】
{content}

请输出如下 JSON：
{{"研究背景":"...","研究目的":"...","方法":"...","主要发现":["发现1","发现2","发现3"],"结论与意义":"...","局限性":"...","解析级别":"全文解析"}}"""


class UserIntent:
    """用户意图类型"""
    LOCAL_PDF = "local_pdf"           # 本地 PDF 解析
    SINGLE_PAPER = "single_paper"     # 单篇文献解析（PMID/DOI/标题）
    LITERATURE_SEARCH = "search"      # 文献搜索
    HELP = "help"                     # 帮助/使用指导
    CHAT = "chat"                     # 闲聊/无法识别


def _llm_classify_intent(text: str) -> dict | None:
    """
    当规则全部未命中时，用 LLM 判断用户意图是文献搜索还是闲聊。
    返回 intent dict 或 None（LLM 不可用时）。
    """
    try:
        from core.config import get_config
        from core.llm.openai import OpenAIProvider
        config = get_config()
        if not config.llm.api_key:
            return None
        client = OpenAIProvider(config.llm)
    except Exception:
        return None

    prompt = (
        "你是一个文献检索智能助手的意图分类器。\n"
        "请判断以下用户输入是否包含文献搜索意图（想查找论文/文献/研究/科学进展等）。\n\n"
        f"用户输入：{text}\n\n"
        "只输出一个 JSON：\n"
        '{"intent": "search"} 如果用户想搜索/查找文献或科学信息\n'
        '{"intent": "chat"} 如果用户在闲聊、打招呼或询问非文献问题\n'
        "不要输出其他任何文字。"
    )
    try:
        import json as _json
        raw = client.generate(prompt, max_tokens=30, temperature=0.1)
        m = re.search(r'\{[^}]+\}', raw)
        if m:
            data = _json.loads(m.group(0))
            if data.get("intent") == "search":
                return {
                    "intent": UserIntent.LITERATURE_SEARCH,
                    "params": {"query": text},
                    "confidence": 0.7
                }
    except Exception as e:
        logger.warning("LLM 意图分类失败: %s", e)

    return None


def _classify_user_intent(text: str) -> dict:
    """
    分类用户意图，返回意图类型和提取的参数。

    Returns:
        {
            "intent": UserIntent.XXX,
            "params": {...},  # 根据意图类型不同
            "confidence": float  # 置信度 0-1
        }
    """
    text = text.strip()

    # 空输入
    if not text:
        return {"intent": UserIntent.HELP, "params": {}, "confidence": 1.0}

    # 1. 帮助意图
    help_patterns = [
        r'^(帮助|help|怎么用|如何使用|使用方法|用法|\?|？)$',
        r'(怎么|如何|怎样)(使用|操作|用)',
        r'(有什么|有哪些)(功能|命令)',
        r'(教我|告诉我)(怎么|如何)',
    ]
    for p in help_patterns:
        if re.search(p, text, re.IGNORECASE):
            return {"intent": UserIntent.HELP, "params": {}, "confidence": 0.9}

    # 2. 本地 PDF 解析
    pdf_path, pdf_email, pdf_title = _detect_local_pdf_intent(text)
    if pdf_path:
        return {
            "intent": UserIntent.LOCAL_PDF,
            "params": {"pdf_path": pdf_path, "email": pdf_email, "title": pdf_title},
            "confidence": 0.95
        }

    # 3. 单篇文献解析（PMID/DOI/标题）
    paper_id, paper_email = _detect_single_paper_intent(text)
    if paper_id:
        return {
            "intent": UserIntent.SINGLE_PAPER,
            "params": {"paper_id": paper_id, "email": paper_email},
            "confidence": 0.9
        }

    # 4. 文献搜索意图（扩大覆盖面）
    search_patterns = [
        r'(找|搜|搜索|查|检索|推荐|推送|看看|整理|汇总).*(文献|论文|文章|研究|paper|article|进展|动态)',
        r'(文献|论文|文章|paper|article).*(找|搜|推荐|推送|汇总)',
        r'(最近|近期|\d+天|\d+年|\d+月).*(文献|论文|文章|研究|进展)',
        r'(关于|有关|涉及|围绕).+的?(文献|论文|文章|研究|进展)',
        r'发送到.+@',
        r'(综述|review|meta.?analysis|survey)',
        r'(最新|最近|近期).*(进展|成果|发现|突破)',
        r'\b\w+@\w+\.\w+\b.*?(关键词|keyword|搜索|search)',
    ]
    for p in search_patterns:
        if re.search(p, text, re.IGNORECASE):
            return {
                "intent": UserIntent.LITERATURE_SEARCH,
                "params": {"query": text},
                "confidence": 0.8
            }

    # 5. 包含生物医学/AI 领域关键词 - 默认当搜索处理
    keyword_hints = [
        # 分子/基因
        '基因', '蛋白', 'DNA', 'RNA', 'mRNA', '转录', '表达', '突变', '变异', '基因组',
        'genome', 'transcriptome', 'proteome', 'epigenetic', '表观遗传', '甲基化',
        # 细胞/组织
        '细胞', 'cell', '类器官', 'organoid', '干细胞', 'stem cell', '免疫', 'immune',
        # 技术/方法
        'CRISPR', '测序', 'sequencing', 'single cell', 'single-cell', '空间转录',
        'spatial', '质谱', 'mass spec', 'flow cytometry', 'PCR', 'NGS', 'Hi-C',
        'ChIP', 'ATAC', 'scRNA', 'scATAC', 'long read', 'nanopore',
        # AI/计算
        'AI', '机器学习', 'deep learning', 'machine learning', 'transformer',
        'neural network', 'language model', 'LLM', 'AlphaFold', 'foundation model',
        'bioinformatics', '生信', '生物信息',
        # 疾病/临床
        '癌', 'cancer', 'tumor', '肿瘤', '糖尿病', 'diabetes', '阿尔茨海默',
        'Alzheimer', '帕金森', 'Parkinson', '心血管', 'cardiovascular',
        # 组学
        '组学', 'omics', '代谢组', 'metabolom', '微生物组', 'microbiome', '宏基因组',
        'metagenom', '蛋白质组', '脂质组', 'lipidom',
    ]
    text_lower = text.lower()
    for kw in keyword_hints:
        if kw.lower() in text_lower:
            return {
                "intent": UserIntent.LITERATURE_SEARCH,
                "params": {"query": text},
                "confidence": 0.6
            }

    # 6. LLM 兜底意图分类（规则全部未命中时）
    llm_intent = _llm_classify_intent(text)
    if llm_intent:
        return llm_intent

    return {"intent": UserIntent.CHAT, "params": {"query": text}, "confidence": 0.3}


def _detect_local_pdf_intent(text: str):
    """
    检测输入是否为本地 PDF 解析意图。
    返回 (pdf_path, email, title) 或 (None, None, None)。

    支持的路径格式：
    - 绝对路径：/Users/xxx/paper.pdf
    - 相对路径：./paper.pdf, ../docs/paper.pdf
    - Home 路径：~/Downloads/paper.pdf
    - Windows 路径：C:\\Users\\xxx\\paper.pdf
    """
    # 检测 PDF 文件路径的正则
    # 匹配：绝对路径、相对路径、~ 开头的路径、Windows 路径
    pdf_pattern = r'((?:[~.]?[/\\]|[A-Za-z]:[/\\])?(?:[\w\-.\u4e00-\u9fff]+[/\\])*[\w\-.\u4e00-\u9fff]+\.pdf)'

    pdf_match = re.search(pdf_pattern, text, re.IGNORECASE)
    if not pdf_match:
        return None, None, None

    pdf_path = pdf_match.group(1)

    # 展开 ~ 路径
    if pdf_path.startswith('~'):
        pdf_path = os.path.expanduser(pdf_path)

    # 提取邮箱
    email_match = re.search(r'[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}', text)
    email = email_match.group(0) if email_match else None

    # 提取标题（如果用户指定了）
    title = None
    title_patterns = [
        r'标题[是为：:]\s*[「「"\'"]?(.+?)[」」"\'"]?(?:\s|$|，|,)',
        r'题目[是为：:]\s*[「「"\'"]?(.+?)[」」"\'"]?(?:\s|$|，|,)',
        r'叫[做作]\s*[「「"\'"]?(.+?)[」」"\'"]?(?:\s|$|，|,)',
    ]
    for pattern in title_patterns:
        title_match = re.search(pattern, text)
        if title_match:
            title = title_match.group(1).strip()
            break

    return pdf_path, email, title


def _detect_single_paper_intent(text: str):
    """
    检测输入是否为单篇解析意图。
    返回 (paper_id, email) 或 (None, None)。
    """
    intent_pattern = r'(解析|分析|速读|解读|帮我看看|帮我读)'
    if not re.search(intent_pattern, text, re.IGNORECASE):
        return None, None

    email_match = re.search(r'[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}', text)
    email = email_match.group(0) if email_match else None

    pmid_match = re.search(r'\b(\d{6,8})\b', text)
    doi_match = re.search(r'(10\.\d{4,}/\S+)', text)

    if pmid_match:
        return pmid_match.group(1), email
    elif doi_match:
        return doi_match.group(1), email

    # 剩余文本当标题
    cleaned = re.sub(intent_pattern, '', text, flags=re.IGNORECASE)
    cleaned = re.sub(r'[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}', '', cleaned)
    cleaned = re.sub(r'[，,。发送到发至发给：:]', ' ', cleaned).strip()
    if len(cleaned) >= 8:
        return cleaned, email
    return None, None


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='文献智能体 - 根据自然语言需求搜索和推送文献',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 交互式模式
  python agent_main.py

  # 命令行模式
  python agent_main.py "帮我找最近7天关于单细胞和AI的文献，发送到example@qq.com"

  # 指定关键词
  python agent_main.py --keywords "single cell AND AI" --days 7 --email example@qq.com

  # 本地 PDF 文件解析
  python agent_main.py --pdf /path/to/paper.pdf --email example@qq.com
  python agent_main.py --pdf /path/to/paper.pdf --title "论文标题" --email example@qq.com
        """
    )
    
    parser.add_argument(
        'request',
        nargs='?',
        help='自然语言需求（如果不提供，将进入交互式模式）'
    )
    
    # 运行模式
    parser.add_argument(
        '--mode',
        choices=['scheduled', 'interactive'],
        default='interactive',
        help='运行模式: scheduled（定时触发）或 interactive（交互式）'
    )
    
    # 可选参数：命令行直接提供关键词/时间/邮箱（主要用于interactive模式）
    parser.add_argument(
        '--keywords',
        nargs='+',
        help='搜索关键词列表（支持PubMed查询语法）'
    )
    
    parser.add_argument(
        '--days',
        type=int,
        help='搜索最近N天的文献'
    )
    
    parser.add_argument(
        '--email',
        help='收件人邮箱地址'
    )
    
    parser.add_argument(
        '--max-papers',
        type=int,
        help='最多发送的文献数量（覆盖配置文件中的report.max_papers）'
    )
    
    parser.add_argument(
        '--translate-abstract',
        type=lambda x: x.lower() in ['true', '1', 'yes', 'on'],
        help='是否将英文摘要翻译成中文（true/false，覆盖配置文件中的report.translate_abstract）'
    )
    
    parser.add_argument(
        '--max-results-per-keyword',
        type=int,
        help='每个关键词最多返回的论文数（覆盖配置文件中的search.max_results_per_keyword，默认20）'
    )
    
    parser.add_argument(
        '--validation-strictness',
        choices=['normal', 'strict', 'very_strict'],
        help='验证严格度级别（覆盖配置文件中的search.validation_strictness）'
    )
    
    parser.add_argument(
        '--strict-validation',
        type=lambda x: x.lower() in ['true', '1', 'yes', 'on'],
        help='是否启用严格关键词验证（true/false，覆盖配置文件中的search.strict_keyword_validation）'
    )
    
    parser.add_argument(
        '--use-unified-search',
        type=lambda x: x.lower() in ['true', '1', 'yes', 'on'],
        help='是否使用统一检索（PubMed + arXiv + bioRxiv，true/false，覆盖配置文件中的search.use_unified_search）'
    )
    
    parser.add_argument(
        '--skip-sent-dedup',
        type=lambda x: x.lower() in ['true', '1', 'yes', 'on'],
        help='是否跳过已发送文献去重（true/false，覆盖配置文件中的search.skip_sent_dedup）'
    )
    
    parser.add_argument(
        '--min-date',
        help='最小日期（格式：YYYY-MM-DD，覆盖配置文件中的search.min_date）'
    )
    
    parser.add_argument(
        '--max-date',
        help='最大日期（格式：YYYY-MM-DD，覆盖配置文件中的search.max_date）'
    )
    
    parser.add_argument(
        '--max-core-keywords-for-and',
        type=int,
        help='默认仅对前N个核心关键词使用AND组合（覆盖配置文件中的search.max_core_keywords_for_and，默认2）'
    )
    
    parser.add_argument(
        '--enforce-all-keywords-and',
        type=lambda x: x.lower() in ['true', '1', 'yes', 'on'],
        help='强制所有关键词使用AND组合（true/false，覆盖配置文件中的search.enforce_all_keywords_and）'
    )
    
    parser.add_argument(
        '--config',
        help='配置文件路径（默认: config.yaml）'
    )
    
    parser.add_argument(
        '--min-impact-factor',
        type=float,
        help='最小影响因子（覆盖配置文件中的filter.min_impact_factor）'
    )
    
    parser.add_argument(
        '--max-impact-factor',
        type=float,
        help='最大影响因子（覆盖配置文件中的filter.max_impact_factor）'
    )
    
    parser.add_argument(
        '--journals',
        nargs='+',
        help='限定期刊列表（覆盖配置文件中的filter.allowed_journals）'
    )
    
    parser.add_argument(
        '--fields',
        nargs='+',
        help='限定领域列表（覆盖配置文件中的filter.allowed_fields）'
    )

    parser.add_argument(
        '--paper',
        help='单篇文献快速解析：输入 PMID（纯数字）、DOI（10.xxx/...）或标题，配合 --email 使用'
    )

    parser.add_argument(
        '--pdf',
        help='本地 PDF 文件解析：输入 PDF 文件路径，配合 --email 使用'
    )

    parser.add_argument(
        '--title',
        help='指定 PDF 文件的论文标题（可选，配合 --pdf 使用）'
    )

    args = parser.parse_args()
    
    config_path = args.config or "config.yaml"
    agent = LiteratureAgent(config_path=config_path, mode=args.mode)
    
    # 构建配置覆盖字典（从命令行参数）
    override_config = {}
    
    # 搜索配置覆盖
    if any([args.max_results_per_keyword, args.validation_strictness, args.strict_validation is not None,
            args.use_unified_search is not None, args.skip_sent_dedup is not None,
            args.min_date, args.max_date, args.max_core_keywords_for_and,
            args.enforce_all_keywords_and is not None, args.keywords, args.days is not None]):
        override_config['search'] = {}
        if args.max_results_per_keyword is not None:
            override_config['search']['max_results_per_keyword'] = args.max_results_per_keyword
        if args.validation_strictness:
            override_config['search']['validation_strictness'] = args.validation_strictness
        if args.strict_validation is not None:
            override_config['search']['strict_keyword_validation'] = args.strict_validation
        if args.use_unified_search is not None:
            override_config['search']['use_unified_search'] = args.use_unified_search
        if args.skip_sent_dedup is not None:
            override_config['search']['skip_sent_dedup'] = args.skip_sent_dedup
        if args.min_date:
            override_config['search']['min_date'] = args.min_date
        if args.max_date:
            override_config['search']['max_date'] = args.max_date
        if args.keywords:
            override_config['search']['keywords'] = args.keywords
        if args.days is not None:
            override_config['search']['days_back'] = args.days
        if args.max_core_keywords_for_and is not None:
            override_config['search']['max_core_keywords_for_and'] = args.max_core_keywords_for_and
        if args.enforce_all_keywords_and is not None:
            override_config['search']['enforce_all_keywords_and'] = args.enforce_all_keywords_and
    
    # 报告配置覆盖
    if args.max_papers is not None or args.translate_abstract is not None:
        if 'report' not in override_config:
            override_config['report'] = {}
        if args.max_papers is not None:
            override_config['report']['max_papers'] = args.max_papers
        if args.translate_abstract is not None:
            override_config['report']['translate_abstract'] = args.translate_abstract

    if args.email:
        if 'email' not in override_config:
            override_config['email'] = {}
        override_config['email']['to_email'] = args.email

    if any([
        args.min_impact_factor is not None,
        args.max_impact_factor is not None,
        args.journals,
        args.fields
    ]):
        if 'filter' not in override_config:
            override_config['filter'] = {}
        if args.min_impact_factor is not None:
            override_config['filter']['min_impact_factor'] = args.min_impact_factor
        if args.max_impact_factor is not None:
            override_config['filter']['max_impact_factor'] = args.max_impact_factor
        if args.journals:
            override_config['filter']['allowed_journals'] = args.journals
        if args.fields:
            override_config['filter']['allowed_fields'] = args.fields
    
    # 处理用户输入
    if args.mode == "scheduled":
        # 定时触发模式：直接运行，不使用用户输入
        user_input = None
    elif args.request:
        # 命令行模式：直接使用用户输入
        user_input = args.request
    elif args.keywords or args.days or args.email:
        # 参数模式：从命令行参数构建需求
        parts = []
        if args.keywords:
            parts.append(f"关键词: {', '.join(args.keywords)}")
        if args.days:
            parts.append(f"最近{args.days}天")
        if args.email:
            parts.append(f"发送到{args.email}")
        if args.max_papers:
            parts.append(f"最多{args.max_papers}篇")
        
        user_input = " ".join(parts)
    else:
        # 交互式模式：提示用户输入
        if args.mode == "interactive":
            print("=" * 80)
            print("文献智能体 - 交互式模式")
            print("=" * 80)
            print()
            print("请输入你的需求（例如：帮我找最近7天关于单细胞和AI的文献，发送到example@qq.com）")
            print("支持：文献搜索 | 单篇解析（PMID/DOI/标题）| 本地PDF解析")
            print("输入 'help' 获取帮助，'quit' 退出")
            print()

            user_input = input("> ").strip()

            if not user_input or user_input.lower() in ['quit', 'exit', 'q']:
                print("退出")
                return

            # 使用智能意图分类处理用户输入
            default_email = (agent.base_agent.config.get('email', {}) or {}).get('to_email', '')
            result = agent.handle_user_input(user_input, default_email=default_email)

            status = result.get("status")
            intent = result.get("intent")
            message = result.get("message", "")

            if status == "chat":
                # 帮助或闲聊回复
                print(f"\n{message}")
                return

            if status == "error":
                # 出错但给出引导
                print(f"\n⚠️ {message}")
                return

            if status == "success":
                # 成功执行
                print(f"\n✓ {message}")
                return

            if status == "search":
                # 文献搜索，继续走原有流程
                user_input = result.get("query", user_input)
                # 不 return，继续执行下面的搜索逻辑

        else:
            user_input = None
    
    # --pdf 模式：本地 PDF 文件解析
    if args.pdf:
        to_email = args.email or (agent.base_agent.config.get('email', {}) or {}).get('to_email', '')
        if not to_email:
            print("错误: 请通过 --email 指定收件人邮箱")
            return
        print(f"\n正在解析本地 PDF: {args.pdf}")
        result = agent.analyze_local_pdf(args.pdf, to_email, title=args.title)
        if result.get('status') == 'success':
            print(f"✓ {result.get('message')}")
        else:
            print(f"✗ 解析失败: {result.get('message')}")
        return

    # --paper 模式：单篇文献快速解析，跳过批量检索流程
    if args.paper:
        to_email = args.email or (agent.base_agent.config.get('email', {}) or {}).get('to_email', '')
        if not to_email:
            print("错误: 请通过 --email 指定收件人邮箱")
            return
        print(f"\n正在解析文献: {args.paper}")
        result = agent.analyze_paper(args.paper, to_email)
        if result.get('status') == 'success':
            print(f"✓ {result.get('message')}")
        else:
            print(f"✗ 解析失败: {result.get('message')}")
        return

    # 运行智能体（传入配置覆盖）
    try:
        agent.run_with_request(user_input, override_config=override_config if override_config else None)
    except KeyboardInterrupt:
        print("\n\n用户中断")
    except Exception as e:
        logger.error(f"运行失败: {e}", exc_info=True)


if __name__ == "__main__":
    main()
