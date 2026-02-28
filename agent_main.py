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


class LiteratureAgent:
    """文献智能体"""
    
    def __init__(self, config_path: Optional[str] = None, mode: str = "interactive"):
        """
        初始化智能体
        
        Args:
            config_path: 配置文件路径（可选）
            mode: 运行模式
                - "scheduled": 定时触发模式，完全使用config.yaml配置
                - "interactive": 交互式模式，完全依赖用户输入，不使用config.yaml默认值
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
    
    def _parse_simple_request(self, user_input: str) -> Dict[str, Any]:
        """
        简单模式：使用正则表达式和规则解析用户需求
        
        Args:
            user_input: 用户输入的自然语言需求
        
        Returns:
            解析后的参数字典
        """
        from datetime import datetime, timedelta
        
        parsed = {}
        current_year = datetime.now().year
        current_date = datetime.now()
        
        # 解析年份范围（如"2023-2024年"、"2022年到2024年"）
        year_range_match = re.search(r'(\d{4})[年-](\d{4})年?', user_input)
        if year_range_match:
            year_start = int(year_range_match.group(1))
            year_end = int(year_range_match.group(2))
            parsed['min_date'] = f"{year_start}-01-01"
            parsed['max_date'] = f"{year_end}-12-31"
            logger.info(f"简单解析：检测到年份范围 {year_start}-{year_end}，设置日期范围 {parsed['min_date']} 至 {parsed['max_date']}")
        
        # 解析具体日期范围（如"2023年1月1日到2024年12月31日"）
        full_date_range_match = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日[到至-](\d{4})年(\d{1,2})月(\d{1,2})日', user_input)
        if full_date_range_match:
            year1, month1, day1 = int(full_date_range_match.group(1)), int(full_date_range_match.group(2)), int(full_date_range_match.group(3))
            year2, month2, day2 = int(full_date_range_match.group(4)), int(full_date_range_match.group(5)), int(full_date_range_match.group(6))
            parsed['min_date'] = f"{year1}-{month1:02d}-{day1:02d}"
            parsed['max_date'] = f"{year2}-{month2:02d}-{day2:02d}"
            logger.info(f"简单解析：检测到具体日期范围，设置 {parsed['min_date']} 至 {parsed['max_date']}")
        
        # 解析"X年到Y年"（如"2022年到2024年"）
        year_to_year_match = re.search(r'(\d{4})年[到至](\d{4})年', user_input)
        if year_to_year_match and 'min_date' not in parsed:
            year_start = int(year_to_year_match.group(1))
            year_end = int(year_to_year_match.group(2))
            parsed['min_date'] = f"{year_start}-01-01"
            parsed['max_date'] = f"{year_end}-12-31"
            logger.info(f"简单解析：检测到年份范围 {year_start}-{year_end}")
        
        # 解析"X年至今"、"X年到现在"（如"2023年至今"）
        year_to_now_match = re.search(r'(\d{4})年[到至](?:今|现在|当前)', user_input)
        if year_to_now_match and 'min_date' not in parsed:
            year = int(year_to_now_match.group(1))
            parsed['min_date'] = f"{year}-01-01"
            parsed['max_date'] = current_date.strftime('%Y-%m-%d')
            logger.info(f"简单解析：检测到从{year}年至今")
        
        # 解析"X年初"、"X年底"（如"2024年初"、"2025年底"）
        year_start_match = re.search(r'(\d{4})年初', user_input)
        if year_start_match and 'min_date' not in parsed:
            year = int(year_start_match.group(1))
            parsed['min_date'] = f"{year}-01-01"
            parsed['max_date'] = f"{year}-03-31"  # 年初通常指第一季度
            logger.info(f"简单解析：检测到{year}年初")
        
        year_end_match = re.search(r'(\d{4})年底', user_input)
        if year_end_match and 'min_date' not in parsed:
            year = int(year_end_match.group(1))
            parsed['min_date'] = f"{year}-10-01"  # 年底通常指第四季度
            parsed['max_date'] = f"{year}-12-31"
            logger.info(f"简单解析：检测到{year}年底")
        
        # 解析相对时间（"去年"、"今年"、"明年"）
        if '去年' in user_input and 'min_date' not in parsed:
            last_year = current_year - 1
            parsed['min_date'] = f"{last_year}-01-01"
            parsed['max_date'] = f"{last_year}-12-31"
            logger.info(f"简单解析：检测到去年（{last_year}年）")
        
        if '今年' in user_input and 'min_date' not in parsed:
            parsed['min_date'] = f"{current_year}-01-01"
            parsed['max_date'] = current_date.strftime('%Y-%m-%d')
            logger.info(f"简单解析：检测到今年（{current_year}年）")
        
        if '明年' in user_input and 'min_date' not in parsed:
            next_year = current_year + 1
            parsed['min_date'] = f"{next_year}-01-01"
            parsed['max_date'] = f"{next_year}-12-31"
            logger.info(f"简单解析：检测到明年（{next_year}年）")
        
        # 解析年份（如"2026年"、"2025年"）- 放在后面，避免覆盖更具体的范围
        year_match = re.search(r'(\d{4})年(?![到至-]|\d)', user_input)
        if year_match and 'min_date' not in parsed:
            year = int(year_match.group(1))
            parsed['min_date'] = f"{year}-01-01"
            parsed['max_date'] = f"{year}-12-31"
            logger.info(f"简单解析：检测到年份 {year}，设置日期范围 {parsed['min_date']} 至 {parsed['max_date']}")
        
        # 解析日期范围（如"2024年1月到3月"、"2025年1月-3月"）
        date_range_match = re.search(r'(\d{4})年(\d{1,2})月[到至-](\d{1,2})月', user_input)
        if date_range_match and 'min_date' not in parsed:
            year = int(date_range_match.group(1))
            month_start = int(date_range_match.group(2))
            month_end = int(date_range_match.group(3))
            parsed['min_date'] = f"{year}-{month_start:02d}-01"
            # 计算结束月份的最后一天
            if month_end == 12:
                parsed['max_date'] = f"{year}-12-31"
            else:
                next_month = datetime(year, month_end + 1, 1)
                last_day = (next_month - timedelta(days=1)).day
                parsed['max_date'] = f"{year}-{month_end:02d}-{last_day}"
            logger.info(f"简单解析：检测到日期范围，设置 {parsed['min_date']} 至 {parsed['max_date']}")
        
        # 解析单个月份（如"2024年1月"）
        month_match = re.search(r'(\d{4})年(\d{1,2})月(?![到至-]|\d)', user_input)
        if month_match and 'min_date' not in parsed:
            year = int(month_match.group(1))
            month = int(month_match.group(2))
            parsed['min_date'] = f"{year}-{month:02d}-01"
            if month == 12:
                parsed['max_date'] = f"{year}-12-31"
            else:
                next_month = datetime(year, month + 1, 1)
                last_day = (next_month - timedelta(days=1)).day
                parsed['max_date'] = f"{year}-{month:02d}-{last_day}"
            logger.info(f"简单解析：检测到月份，设置 {parsed['min_date']} 至 {parsed['max_date']}")
        
        # 解析"最近N天"
        days_match = re.search(r'最近(\d+)天', user_input)
        if days_match and 'min_date' not in parsed:
            days = int(days_match.group(1))
            parsed['days_back'] = days
            logger.info(f"简单解析：检测到最近{days}天")
        
        # 解析"最近N周"
        weeks_match = re.search(r'最近(\d+)周', user_input)
        if weeks_match and 'min_date' not in parsed:
            weeks = int(weeks_match.group(1))
            parsed['days_back'] = weeks * 7
            logger.info(f"简单解析：检测到最近{weeks}周，转换为{weeks * 7}天")
        
        # 解析"最近N月"
        months_match = re.search(r'最近(\d+)月', user_input)
        if months_match and 'min_date' not in parsed:
            months = int(months_match.group(1))
            parsed['days_back'] = months * 30  # 近似值
            logger.info(f"简单解析：检测到最近{months}月，转换为{months * 30}天")
        
        # 解析"近N年/最近N年/过去N年"
        years_match = re.search(r'(最近|近|过去)(\d+)年', user_input)
        if years_match and 'min_date' not in parsed and 'days_back' not in parsed:
            years = int(years_match.group(2))
            days = years * 365  # 按年近似为365天
            parsed['days_back'] = days
            logger.info(f"简单解析：检测到{years_match.group(1)}{years}年，转换为最近{days}天")
        
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
                    if self._is_valid_keyword(kw):
                        keywords.append(kw)
        
        # 清理和去重关键词
        if keywords:
            # 去重
            keywords = list(set(keywords))
            # 过滤无效关键词
            keywords = [kw for kw in keywords if self._is_valid_keyword(kw)]
            # 移除包含"或者"、"或"的关键词（这些应该被拆分）
            keywords = [kw for kw in keywords if '或者' not in kw and '或' not in kw]
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
1. 搜索关键词（多个关键词用列表形式，支持PubMed查询语法）
2. 时间范围（非常重要，请仔细解析）：
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
3. 收件人邮箱（如果用户提供了）
4. 关键词组合方式（keyword_operator: "AND"表示交集，需要同时包含所有关键词；"OR"表示并集，包含任一关键词即可。根据用户语义判断）
5. 其他要求（如过滤条件、文献数量等）

请以JSON格式输出，格式如下：
{{
    "keywords": ["keyword1", "keyword2"],
    "keyword_operator": "AND",
    "days_back": null,
    "min_date": "2026-01-01",
    "max_date": "2026-12-31",
    "to_email": null,
    "max_papers": 50,
    "filter": {{
        "min_abstract_length": 100,
        "exclude_keywords": [],
        "include_keywords": []
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
            response = self.llm_client.generate(prompt, max_tokens=500, temperature=0.3)
            
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
                        elif key == 'keyword_operator' and value:
                            merged['keyword_operator'] = value
                        elif key == 'to_email' and value:
                            merged['to_email'] = value
                        elif key == 'max_papers' and value:
                            merged['max_papers'] = value
                        elif key == 'filter' and value:
                            merged['filter'] = value
                
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
        # 使用WARNING级别，让用户在精简日志模式下也能看到“进入总结阶段”
        logger.warning(f"开始为 {total} 篇论文生成中文总结（可能稍有耗时，请耐心等待）...")
        
        # 逐篇处理，更可靠
        for i, paper in enumerate(papers, 1):
            try:
                # 定期打印进度，避免用户误以为卡住
                if i == 1 or i % 5 == 0 or i == total:
                    title_preview = paper.title[:50] if paper.title else "无标题"
                    logger.warning(f"中文总结进度: 第 {i}/{total} 篇论文（当前: {title_preview}）")
                
                summary = self._summarize_single_paper(paper, i)
                if summary:
                    # 使用多种key，确保能匹配到
                    if paper.doi:
                        paper_summaries[paper.doi] = summary
                    if paper.title:
                        paper_summaries[paper.title] = summary
                    # 也使用索引作为key
                    paper_summaries[str(i)] = summary
                    logger.info(f"✓ 论文 {i}/{len(papers)}: {paper.title[:50] if paper.title else '无标题'}...")
                else:
                    logger.warning(f"✗ 论文 {i}/{len(papers)}: 生成总结失败")
            except Exception as e:
                logger.warning(f"✗ 论文 {i}/{len(papers)}: 生成总结时出错: {e}")
        
        # 使用WARNING级别，总结生成完毕
        logger.warning(f"已为 {len(set(paper_summaries.values()))} 篇论文生成中文总结")
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
            # 简单模式：返回基本信息
            summary = f"共找到 {len(papers)} 篇相关文献。\n\n"
            for i, paper in enumerate(papers[:5], 1):
                summary += f"{i}. {paper.title or '无标题'}\n"
                summary += f"   期刊: {paper.journal or '未知'}, 年份: {paper.year or '未知'}\n\n"
            
            # 添加验证表（如果有）
            if keyword_validation_info:
                summary += self._generate_validation_table(papers, keyword_validation_info)
            
            return summary
        
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
            return self.summarize_literature(papers, keyword_validation_info)  # 递归调用简单模式
    
    def update_config_from_request(self, parsed_request: Dict[str, Any], user_input: str = ""):
        """
        根据解析的需求更新配置
        
        Args:
            parsed_request: 解析后的需求字典
            user_input: 用户原始输入（用于LLM判断关键词运算符）
        """
        # 更新搜索配置
        # 优先使用新的两层结构：topic_keywords（主题关键词）和 article_type_keywords（文章类型关键词）
        topic_keywords = None
        article_type_keywords = None
        
        if 'topic_keywords' in parsed_request and parsed_request['topic_keywords']:
            topic_keywords = parsed_request['topic_keywords']
            logger.info(f"✅ 检测到主题关键词: {topic_keywords}")
        
        if 'article_type_keywords' in parsed_request and parsed_request.get('article_type_keywords'):
            article_type_keywords = parsed_request['article_type_keywords']
            logger.info(f"✅ 检测到文章类型关键词: {article_type_keywords}")
        
        # 向后兼容：如果没有新的两层结构，使用旧的 keywords 字段
        if not topic_keywords and 'keywords' in parsed_request and parsed_request['keywords']:
            topic_keywords = parsed_request['keywords']
            logger.info(f"使用向后兼容模式，将keywords作为主题关键词: {topic_keywords}")
        
        # 处理主题关键词（用于第一层检索）
        if topic_keywords:
            keywords = topic_keywords
            logger.info(f"准备更新主题关键词，原始关键词: {keywords}")
            # 清理和验证关键词
            cleaned_keywords = []
            for kw in keywords:
                kw_str = str(kw).strip()
                if self._is_valid_keyword(kw_str):
                    cleaned_keywords.append(kw_str)
                else:
                    logger.warning(f"过滤无效关键词: {kw_str}")
            
            if cleaned_keywords:
                # 使用LLM将中文/混合关键词扩展为适合PubMed的英文检索词（用于参考和验证）
                expanded_keywords = cleaned_keywords
                force_expand = getattr(self, "force_llm_expand_keywords", False)
                
                if self.use_llm:
                    try:
                        expanded_keywords = self._expand_search_keywords_with_llm(cleaned_keywords)
                        logger.info(f"✅ 关键词扩展结果（用于检索参考）: {expanded_keywords}")
                    except Exception as e:
                        if force_expand:
                            # 强制模式下，LLM扩展失败视为致命错误
                            msg = f"强制LLM关键词扩展开启，但扩展失败: {e}。请检查LLM配置或修改关键词后重试。"
                            logger.error(msg)
                            raise RuntimeError(msg)
                        else:
                            logger.warning(f"使用LLM扩展关键词失败，暂时仅使用原始关键词进行检索: {e}")
                            expanded_keywords = cleaned_keywords
                
                # 从配置中读取AND核心关键词个数与强制全AND开关
                search_cfg = self.base_agent.config.get('search', {})
                try:
                    max_core = int(search_cfg.get('max_core_keywords_for_and', 2))
                except Exception:
                    max_core = 2
                enforce_all = bool(search_cfg.get('enforce_all_keywords_and', False))

                # 关键修复：使用扩展后的英文关键词构建查询，而不是原始中文关键词
                # 如果LLM扩展成功，使用扩展后的英文关键词；否则使用原始关键词（可能是英文）
                query_keywords = expanded_keywords if expanded_keywords != cleaned_keywords else cleaned_keywords
                
                # 默认只对少数核心关键词使用AND，其余交给LLM严格验证
                core_keywords = query_keywords
                if len(query_keywords) > max_core and not enforce_all:
                    core_keywords = query_keywords[:max_core]
                    logger.info(
                        f"解析到关键词较多，默认仅对以下核心关键词使用AND组合: {core_keywords}；"
                        f"其余关键词将在严格验证阶段由LLM判断相关性。"
                    )
                elif enforce_all:
                    logger.info(f"已启用强制全AND模式，将对所有关键词使用AND组合: {query_keywords}")

                # 构建查询字符串：只对 core_keywords 使用 AND
                # 这个查询会用于所有搜索源（PubMed、arXiv、bioRxiv），所以必须使用英文
                if len(core_keywords) > 1:
                    query = " AND ".join([f"({kw})" for kw in core_keywords])
                else:
                    query = core_keywords[0] if core_keywords else ""

                # 将单个查询字符串作为keywords列表的唯一元素
                # main.py 中会识别出其中的AND，不再二次组合
                self.base_agent.config['search']['keywords'] = [query]
                logger.info(f"✅ 更新搜索查询（用于所有检索源，已转换为英文）: {query}")
                
                # 同时保留语义层面的原始关键词，供严格验证和验证表使用
                self.base_agent.config['search']['semantic_keywords'] = cleaned_keywords
                logger.info(f"✅ 保留语义关键词（用于验证）: {cleaned_keywords}")
                
                # 保存文章类型关键词（用于第二层过滤）
                if article_type_keywords:
                    # 清理文章类型关键词
                    cleaned_article_types = []
                    for at_kw in article_type_keywords:
                        at_kw_str = str(at_kw).strip()
                        if at_kw_str:
                            cleaned_article_types.append(at_kw_str)
                    
                    if cleaned_article_types:
                        self.base_agent.config['search']['article_type_keywords'] = cleaned_article_types
                        logger.info(f"✅ 保存文章类型关键词（用于第二层过滤）: {cleaned_article_types}")
                    else:
                        # 清除文章类型关键词
                        self.base_agent.config['search'].pop('article_type_keywords', None)
                else:
                    # 清除文章类型关键词
                    self.base_agent.config['search'].pop('article_type_keywords', None)
            else:
                logger.warning("所有关键词都被过滤，使用配置文件中的默认关键词")
        else:
            logger.warning(f"解析结果中没有关键词字段，parsed_request keys: {list(parsed_request.keys()) if parsed_request else 'None'}")
            if parsed_request:
                logger.warning(f"parsed_request 内容: {parsed_request}")
        
        # 如果LLM没有提供keyword_operator，使用LLM智能判断（需要在有keywords的情况下）
        # 优先使用topic_keywords，向后兼容使用keywords
        keywords_for_operator = topic_keywords or parsed_request.get('keywords', [])
        if keywords_for_operator:
            keywords = keywords_for_operator
            if 'keyword_operator' not in parsed_request or not parsed_request['keyword_operator']:
                if self.use_llm and len(keywords) > 1:
                    # 检查是否已包含逻辑运算符
                    has_operator = any(' AND ' in kw.upper() or ' OR ' in kw.upper() or ' NOT ' in kw.upper() for kw in keywords)
                    if not has_operator:
                        operator = self.determine_keyword_operator(keywords, user_input)
                        self.base_agent.config['search']['keyword_operator'] = operator
                        logger.info(f"LLM智能判断关键词组合方式: {operator}")
            else:
                self.base_agent.config['search']['keyword_operator'] = parsed_request['keyword_operator']
                logger.info(f"使用解析的keyword_operator: {parsed_request['keyword_operator']}")
        
        if 'days_back' in parsed_request and parsed_request['days_back']:
            self.base_agent.config['search']['days_back'] = parsed_request['days_back']
            logger.info(f"更新搜索时间范围: 最近{parsed_request['days_back']}天")
        
        if 'min_date' in parsed_request and parsed_request['min_date']:
            self.base_agent.config['search']['min_date'] = parsed_request['min_date']
            # 如果设置了min_date，清除days_back（避免冲突）
            if 'days_back' in self.base_agent.config['search']:
                self.base_agent.config['search']['days_back'] = None
            logger.info(f"更新最小日期: {parsed_request['min_date']}")
        
        if 'max_date' in parsed_request and parsed_request['max_date']:
            self.base_agent.config['search']['max_date'] = parsed_request['max_date']
            # 如果设置了max_date，清除days_back（避免冲突）
            if 'days_back' in self.base_agent.config['search']:
                self.base_agent.config['search']['days_back'] = None
            logger.info(f"更新最大日期: {parsed_request['max_date']}")
        
        # 更新邮件配置
        if 'to_email' in parsed_request and parsed_request['to_email']:
            self.base_agent.config['email']['to_email'] = parsed_request['to_email']
            logger.info(f"更新收件人邮箱: {parsed_request['to_email']}")
        
        # 更新报告配置
        if 'max_papers' in parsed_request and parsed_request['max_papers']:
            self.base_agent.config['report']['max_papers'] = parsed_request['max_papers']
            logger.info(f"更新最大文献数: {parsed_request['max_papers']}")
        
        # 更新过滤配置
        if 'filter' in parsed_request and parsed_request['filter']:
            filter_config = parsed_request['filter']
            if 'min_abstract_length' in filter_config:
                self.base_agent.config['filter']['min_abstract_length'] = filter_config['min_abstract_length']
            if 'exclude_keywords' in filter_config:
                self.base_agent.config['filter']['exclude_keywords'] = filter_config['exclude_keywords']
            if 'include_keywords' in filter_config:
                self.base_agent.config['filter']['include_keywords'] = filter_config['include_keywords']
    
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
        if override_config:
            self._apply_config_overrides(override_config)
        
        if self.mode == "scheduled":
            # 定时触发模式：直接使用config.yaml配置
            logger.info("模式: 定时触发（使用config.yaml配置）")
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
        """定时触发模式：直接使用config.yaml配置"""
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
        """交互式模式：完全依赖用户输入，不使用config.yaml默认值"""
        # 1. 解析用户需求
        logger.info("步骤1: 解析用户需求...")
        parsed_request = self.parse_user_request(user_input)
        
        # 2. 验证必需信息（交互式模式下，不使用config.yaml的默认值）
        logger.info("步骤2: 验证必需信息...")
        required_info = self._validate_and_prompt_required_info(parsed_request)
        
        if not required_info:
            logger.error("缺少必需信息，任务终止")
            return
        
        # 3. 更新配置（使用用户输入的信息，不使用config.yaml默认值）
        logger.info("步骤3: 更新配置...")
        logger.info(f"解析结果详情: {required_info}")
        self.update_config_from_request(required_info, user_input)

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
                time_parsed = self._parse_time_range(time_input)
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
                        time_parsed = self._parse_time_range(time_input)
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
    
    def _parse_time_range(self, time_input: str) -> Dict[str, Any]:
        """解析时间范围输入"""
        from datetime import datetime, timedelta
        
        # 解析"最近N天"
        days_match = re.search(r'最近(\d+)天', time_input)
        if days_match:
            return {'days_back': int(days_match.group(1))}
        
        # 解析"近N年/最近N年/过去N年"
        years_match = re.search(r'(最近|近|过去)(\d+)年', time_input)
        if years_match:
            years = int(years_match.group(2))
            days = years * 365  # 按年近似为365天
            return {'days_back': days}
        
        # 解析年份
        year_match = re.search(r'(\d{4})年', time_input)
        if year_match:
            year = int(year_match.group(1))
            return {
                'min_date': f"{year}-01-01",
                'max_date': f"{year}-12-31"
            }
        
        # 解析日期范围（YYYY-MM-DD 到 YYYY-MM-DD）
        date_range_match = re.search(r'(\d{4}-\d{2}-\d{2})\s*到\s*(\d{4}-\d{2}-\d{2})', time_input)
        if date_range_match:
            return {
                'min_date': date_range_match.group(1),
                'max_date': date_range_match.group(2)
            }
        
        # 简单规则没命中时，尝试使用LLM兜底解析时间范围
        if self.use_llm:
            logger.info(f"简单时间解析失败，尝试使用LLM解析时间范围: {time_input}")
            llm_result = self._parse_time_with_llm(time_input)
            if llm_result:
                return llm_result
        
        return None

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
- 如果是“最近N年/近N年/过去N年/最近N月/最近N天”等相对时间，用days_back字段（整数，天数）。
- 如果是“2020年到2023年”、“2015年至今”、“2010年-2012年”等具体年份或日期，返回min_date和max_date（YYYY-MM-DD）。

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
                # 尝试多种可能的key格式匹配
                evidence = None
                # 1. 直接匹配
                if kw in keyword_evidence:
                    evidence = keyword_evidence[kw]
                else:
                    # 2. 尝试不区分大小写匹配
                    for key, value in keyword_evidence.items():
                        if key.lower() == kw.lower():
                            evidence = value
                            break
                    # 3. 如果还是找不到，尝试部分匹配
                    if not evidence:
                        kw_lower = kw.lower()
                        for key, value in keyword_evidence.items():
                            if kw_lower in key.lower() or key.lower() in kw_lower:
                                evidence = value
                                break
                
                if not evidence:
                    # 如果还是找不到，尝试获取第一个可用的证据
                    if keyword_evidence:
                        evidence = list(keyword_evidence.values())[0]
                    else:
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
                    # 尝试多种可能的key格式匹配
                    ev = None
                    # 1. 直接匹配
                    if kw in keyword_evidence:
                        ev = keyword_evidence[kw]
                    else:
                        # 2. 尝试不区分大小写匹配
                        for key, value in keyword_evidence.items():
                            if key.lower() == kw.lower():
                                ev = value
                                break
                        # 3. 如果还是找不到，尝试部分匹配（用于处理查询字符串中的关键词）
                        if not ev:
                            kw_lower = kw.lower()
                            for key, value in keyword_evidence.items():
                                if kw_lower in key.lower() or key.lower() in kw_lower:
                                    ev = value
                                    break
                    
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
        help='运行模式: scheduled（定时触发，使用config.yaml）或 interactive（交互式，完全依赖用户输入）'
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
        help='最多发送的文献数量（覆盖config.yaml中的report.max_papers）'
    )
    
    parser.add_argument(
        '--translate-abstract',
        type=lambda x: x.lower() in ['true', '1', 'yes', 'on'],
        help='是否将英文摘要翻译成中文（true/false，覆盖config.yaml中的report.translate_abstract）'
    )
    
    parser.add_argument(
        '--max-results-per-keyword',
        type=int,
        help='每个关键词最多返回的论文数（覆盖config.yaml中的search.max_results_per_keyword，默认20）'
    )
    
    parser.add_argument(
        '--validation-strictness',
        choices=['normal', 'strict', 'very_strict'],
        help='验证严格度级别（覆盖config.yaml中的search.validation_strictness）'
    )
    
    parser.add_argument(
        '--strict-validation',
        type=lambda x: x.lower() in ['true', '1', 'yes', 'on'],
        help='是否启用严格关键词验证（true/false，覆盖config.yaml中的search.strict_keyword_validation）'
    )
    
    parser.add_argument(
        '--use-unified-search',
        type=lambda x: x.lower() in ['true', '1', 'yes', 'on'],
        help='是否使用统一检索（PubMed + arXiv + bioRxiv，true/false，覆盖config.yaml中的search.use_unified_search）'
    )
    
    parser.add_argument(
        '--skip-sent-dedup',
        type=lambda x: x.lower() in ['true', '1', 'yes', 'on'],
        help='是否跳过已发送文献去重（true/false，覆盖config.yaml中的search.skip_sent_dedup）'
    )
    
    parser.add_argument(
        '--min-date',
        help='最小日期（格式：YYYY-MM-DD，覆盖config.yaml中的search.min_date）'
    )
    
    parser.add_argument(
        '--max-date',
        help='最大日期（格式：YYYY-MM-DD，覆盖config.yaml中的search.max_date）'
    )
    
    parser.add_argument(
        '--max-core-keywords-for-and',
        type=int,
        help='默认仅对前N个核心关键词使用AND组合（覆盖config.yaml中的search.max_core_keywords_for_and，默认2）'
    )
    
    parser.add_argument(
        '--enforce-all-keywords-and',
        type=lambda x: x.lower() in ['true', '1', 'yes', 'on'],
        help='强制所有关键词使用AND组合（true/false，覆盖config.yaml中的search.enforce_all_keywords_and）'
    )
    
    parser.add_argument(
        '--config',
        help='配置文件路径（默认: config.yaml）'
    )
    
    args = parser.parse_args()
    
    # 创建智能体（根据模式）
    agent = LiteratureAgent(config_path=args.config, mode=args.mode)
    
    # 构建配置覆盖字典（从命令行参数）
    override_config = {}
    
    # 搜索配置覆盖
    if any([args.max_results_per_keyword, args.validation_strictness, args.strict_validation is not None,
            args.use_unified_search is not None, args.skip_sent_dedup is not None,
            args.min_date, args.max_date, args.max_core_keywords_for_and,
            args.enforce_all_keywords_and is not None]):
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
            print("或输入 'quit' 退出")
            print()
            
            user_input = input("> ").strip()
            
            if not user_input or user_input.lower() in ['quit', 'exit', 'q']:
                print("退出")
                return
        else:
            user_input = None
    
    # 运行智能体（传入配置覆盖）
    try:
        agent.run_with_request(user_input, override_config=override_config if override_config else None)
    except KeyboardInterrupt:
        print("\n\n用户中断")
    except Exception as e:
        logger.error(f"运行失败: {e}", exc_info=True)


if __name__ == "__main__":
    main()
