#!/usr/bin/env python
"""
文献每日推送智能体

从PubMed等文献数据库中获取最新的相关文献，并发送到预设的邮箱
"""
from __future__ import annotations

import os
import sys
import logging
import json
import yaml
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib

# 添加 BioLitKG 路径
biolitkg_path = Path(__file__).parent.parent.parent / "AI" / "BioLitKG"
if str(biolitkg_path) not in sys.path:
    sys.path.insert(0, str(biolitkg_path))

# 配置logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 导入 BioLitKG 模块
try:
    from literature.pubmed_client import PubMedClient
    from literature.base_client import PaperMetadata
except ImportError as e:
    logger.error(f"无法导入 BioLitKG 模块: {e}")
    logger.error("请确保 BioLitKG 已正确安装")
    sys.exit(1)


class BioinfoAILiteratureDaily:
    """文献每日推送智能体"""
    
    def __init__(self, config_path: Optional[str] = None):
        """
        初始化智能体
        
        Args:
            config_path: 配置文件路径（可选）
        """
        self.tool_dir = Path(__file__).parent
        self.output_dir = self.tool_dir / "outputs"
        self.output_dir.mkdir(exist_ok=True)
        self.cache_dir = self.tool_dir / "cache"
        self.cache_dir.mkdir(exist_ok=True)
        
        # 加载配置文件
        self.config = self._load_config(config_path)
        
        # 初始化搜索客户端
        self.pubmed_client = PubMedClient()
        
        # 加载已发送的文献记录（用于去重）
        self.sent_papers_cache = self._load_sent_papers_cache()
        
        # 关键词验证信息（用于生成验证表）
        self._keyword_validation_info = {}
    
    def _load_config(self, config_path: Optional[str] = None) -> Dict[str, Any]:
        """加载配置文件"""
        if config_path is None:
            config_path = self.tool_dir / "config.yaml"
        else:
            config_path = Path(config_path)
        
        if config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f)
                logger.info(f"已加载配置文件: {config_path}")
                return config
            except Exception as e:
                logger.warning(f"加载配置文件失败: {e}，使用默认配置")
        else:
            logger.warning(f"配置文件不存在: {config_path}，使用默认配置")
        
        # 返回默认配置
        return {
            'search': {
                'keywords': [
                    'bioinformatics AND artificial intelligence',
                    'computational biology AND machine learning',
                    'genomics AND deep learning',
                    'single cell AND AI',
                    'sequencing AND neural network'
                ],
                'max_results_per_keyword': 20,
                'keyword_operator': 'AND',  # 关键词组合方式：AND（交集）或OR（并集）
                'days_back': 7,  # 搜索最近7天的文献
                'min_date': None  # 如果设置，将覆盖days_back
            },
            'email': {
                'smtp_server': 'smtp.example.com',
                'smtp_port': 587,
                'smtp_username': 'your-email@example.com',
                'smtp_password': 'your-password',
                'from_email': 'your-email@example.com',
                'to_email': 'recipient@example.com',
                'use_tls': True
            },
            'filter': {
                'min_abstract_length': 100,  # 最小摘要长度
                'exclude_keywords': [],  # 排除关键词列表
                'include_keywords': []  # 必须包含的关键词（如果设置）
            },
            'report': {
                'max_papers': 50,  # 最多发送50篇文献
                'format': 'html'  # 邮件格式：html 或 text
            }
        }
    
    def _load_sent_papers_cache(self) -> set:
        """加载已发送的文献缓存"""
        cache_file = self.cache_dir / "sent_papers.json"
        if cache_file.exists():
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # 返回DOI和标题的集合
                    sent_papers = set()
                    for paper in data.get('papers', []):
                        if paper.get('doi'):
                            sent_papers.add(paper['doi'].lower())
                        elif paper.get('title'):
                            sent_papers.add(paper['title'].lower())
                    logger.info(f"已加载 {len(sent_papers)} 条已发送文献记录")
                    return sent_papers
            except Exception as e:
                logger.warning(f"加载已发送文献缓存失败: {e}")
        
        return set()
    
    def _save_sent_papers_cache(self, new_papers: List[PaperMetadata]):
        """保存已发送的文献到缓存"""
        cache_file = self.cache_dir / "sent_papers.json"
        
        # 读取现有缓存
        existing_data = {'papers': [], 'last_update': None}
        if cache_file.exists():
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)
            except:
                pass
        
        # 添加新论文
        for paper in new_papers:
            paper_dict = {
                'title': paper.title,
                'doi': paper.doi,
                'journal': paper.journal,
                'year': paper.year,
                'url': paper.url,
                'sent_date': datetime.now().isoformat()
            }
            existing_data['papers'].append(paper_dict)
        
        # 限制缓存大小（保留最近1000条）
        if len(existing_data['papers']) > 1000:
            existing_data['papers'] = existing_data['papers'][-1000:]
        
        existing_data['last_update'] = datetime.now().isoformat()
        
        # 保存
        try:
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(existing_data, f, ensure_ascii=False, indent=2)
            logger.info(f"已保存 {len(new_papers)} 条新文献记录到缓存")
        except Exception as e:
            logger.warning(f"保存已发送文献缓存失败: {e}")
    
    def search_literature(self) -> List[PaperMetadata]:
        """
        搜索最新的生信+AI相关文献
        
        Returns:
            论文列表
        """
        logger.info("开始搜索文献...")
        
        search_config = self.config['search']
        keywords = search_config.get('keywords', [])
        max_results = search_config.get('max_results_per_keyword', 20)
        # 关键词组合方式：'AND'（交集）或 'OR'（并集），默认为'AND'
        keyword_operator = search_config.get('keyword_operator', 'AND').upper()
        
        # 计算日期范围
        if search_config.get('min_date') and search_config.get('max_date'):
            # 如果同时设置了min_date和max_date，使用指定的日期范围
            min_date = datetime.strptime(search_config['min_date'], '%Y-%m-%d')
            max_date = datetime.strptime(search_config['max_date'], '%Y-%m-%d')
            logger.info(f"使用指定的日期范围: {min_date.strftime('%Y-%m-%d')} 至 {max_date.strftime('%Y-%m-%d')}")
        elif search_config.get('min_date'):
            # 如果只设置了min_date，使用从min_date到现在
            min_date = datetime.strptime(search_config['min_date'], '%Y-%m-%d')
            max_date = datetime.now()
            logger.info(f"使用从指定日期到现在: {min_date.strftime('%Y-%m-%d')} 至 {max_date.strftime('%Y-%m-%d')}")
        else:
            # 使用days_back
            days_back = search_config.get('days_back', 7)
            min_date = datetime.now() - timedelta(days=days_back)
            max_date = datetime.now()
            logger.info(f"使用最近{days_back}天: {min_date.strftime('%Y-%m-%d')} 至 {max_date.strftime('%Y-%m-%d')}")
        
        logger.info(f"搜索时间范围: {min_date.strftime('%Y-%m-%d')} 至 {max_date.strftime('%Y-%m-%d')}")
        
        all_papers = []
        
        # 检查关键词是否已经包含逻辑运算符
        has_operator = any(' AND ' in kw.upper() or ' OR ' in kw.upper() or ' NOT ' in kw.upper() for kw in keywords)
        
        if has_operator:
            # 如果关键词已经包含逻辑运算符，分别搜索每个查询
            logger.info("检测到关键词中包含逻辑运算符，将分别搜索每个查询")
            queries = keywords
        else:
            # 如果关键词是简单列表，根据配置组合
            # 注意：当用户提供多个关键词时，默认使用AND（逻辑"与"关系）
            if keyword_operator == 'AND' or len(keywords) > 1:
                # 交集：所有关键词都必须包含
                # 将多个关键词用AND连接
                combined_keywords = f" AND ".join([f"({kw})" for kw in keywords])
                queries = [combined_keywords]
                logger.info(f"使用AND组合关键词（交集，要求同时包含所有关键词）: {combined_keywords}")
                # 确保配置中的keyword_operator是AND
                self.config['search']['keyword_operator'] = 'AND'
            else:
                # 并集：任一关键词包含即可
                combined_keywords = f" {keyword_operator} ".join([f"({kw})" for kw in keywords])
                queries = [combined_keywords]
                logger.info(f"使用{keyword_operator}组合关键词（并集）: {combined_keywords}")
        
        # 为每个查询搜索
        for query_keywords in queries:
            logger.info(f"搜索查询: {query_keywords}")
            try:
                # 构建查询，限制日期范围
                query = f"{query_keywords}[Title/Abstract]"
                
                # 确保年份是整数
                year_from = int(min_date.year) if min_date else None
                year_to = int(max_date.year) if max_date else None
                
                papers = self.pubmed_client.search(
                    query=query,
                    max_results=max_results,
                    year_from=year_from,
                    year_to=year_to,
                    sort="pub_date"
                )
                
                # 进一步过滤日期
                filtered_papers = []
                for paper in papers:
                    try:
                        # 优先使用精确的发布日期
                        if paper.publication_date:
                            paper_date = paper.publication_date
                            # 精确日期：必须在时间范围内
                            if min_date <= paper_date <= max_date:
                                filtered_papers.append(paper)
                        elif paper.year is not None:
                            # 只有年份信息：如果年份在范围内，就接受
                            # 对于只有年份的论文，我们更宽松地处理
                            paper_year = int(paper.year)  # 确保是整数
                            min_year = int(min_date.year)
                            max_year = int(max_date.year)
                            
                            # 如果论文年份在搜索年份范围内，就接受
                            # 这样可以避免因为缺少具体日期而过滤掉所有论文
                            if min_year <= paper_year <= max_year:
                                filtered_papers.append(paper)
                        else:
                            # 没有日期信息：如果配置允许，可以接受
                            # 默认情况下，如果没有日期信息，也接受（可能是新论文）
                            filtered_papers.append(paper)
                    except (TypeError, ValueError, AttributeError) as e:
                        # 如果日期处理出错，记录警告但接受论文（避免丢失数据）
                        logger.warning(f"处理论文日期时出错: {e}, 论文标题: {paper.title[:50] if paper.title else '未知'}")
                        filtered_papers.append(paper)  # 出错时也接受，避免丢失数据
                
                all_papers.extend(filtered_papers)
                logger.info(f"找到 {len(filtered_papers)} 篇论文（日期过滤后）")
            except Exception as e:
                logger.warning(f"搜索关键词 '{keyword}' 时出错: {e}")
                continue
        
        # 去重（基于DOI和标题）
        seen_papers = set()
        unique_papers = []
        for paper in all_papers:
            paper_id = (
                paper.doi.lower() if paper.doi else "",
                paper.title.lower() if paper.title else ""
            )
            if paper_id not in seen_papers and paper_id[0] or paper_id[1]:
                seen_papers.add(paper_id)
                unique_papers.append(paper)
        
        logger.info(f"去重后共找到 {len(unique_papers)} 篇唯一论文")
        
        # 应用过滤器（启用严格关键词验证）
        strict_validation = self.config.get('search', {}).get('strict_keyword_validation', True)  # 默认启用
        validation_strictness = self.config.get('search', {}).get('validation_strictness', 'very_strict')  # 默认最严格
        filtered_papers, keyword_validation_info = self._filter_papers(
            unique_papers, 
            strict_keyword_validation=strict_validation,
            validation_strictness=validation_strictness
        )
        logger.info(f"过滤后剩余 {len(filtered_papers)} 篇论文（严格度: {validation_strictness}）")
        
        # 保存关键词验证信息（用于生成验证表）
        self._keyword_validation_info = keyword_validation_info
        
        # 是否跳过“已发送文献”的去重（测试阶段可开启）
        skip_sent_dedup = self.config.get('search', {}).get('skip_sent_dedup', False)
        
        if skip_sent_dedup:
            logger.info("测试模式：跳过已发送文献去重（可能会看到重复论文）")
            new_papers = filtered_papers
        else:
            # 去除已发送的文献
            new_papers = []
            for paper in filtered_papers:
                is_sent = False
                if paper.doi and paper.doi.lower() in self.sent_papers_cache:
                    is_sent = True
                elif paper.title and paper.title.lower() in self.sent_papers_cache:
                    is_sent = True
                
                if not is_sent:
                    new_papers.append(paper)
            
            logger.info(f"去除已发送文献后，剩余 {len(new_papers)} 篇新论文")
        
        # 限制数量
        max_papers = self.config['report'].get('max_papers', 50)
        new_papers = new_papers[:max_papers]
        
        return new_papers
    
    def _filter_papers(self, papers: List[PaperMetadata], strict_keyword_validation: bool = False, validation_strictness: str = 'normal') -> tuple[List[PaperMetadata], Dict]:
        """
        过滤论文
        
        Args:
            papers: 论文列表
            strict_keyword_validation: 是否进行严格的关键词验证（使用LLM）
            validation_strictness: 验证严格度级别（'normal', 'strict', 'very_strict'）
        
        Returns:
            (过滤后的论文列表, 关键词匹配验证信息字典)
        """
        filter_config = self.config.get('filter', {})
        filtered = []
        keyword_validation_info = {}  # 存储关键词验证信息
        
        min_abstract_length = filter_config.get('min_abstract_length', 100)
        exclude_keywords = [kw.lower() for kw in filter_config.get('exclude_keywords', [])]
        include_keywords = [kw.lower() for kw in filter_config.get('include_keywords', [])]
        
        # 获取用于严格验证的关键词（优先使用语义关键词，其次使用检索关键词）
        search_section = self.config.get('search', {})
        semantic_keywords = search_section.get('semantic_keywords') or search_section.get('keywords', [])
        # 如果启用严格验证且存在关键词，则使用LLM进行严格验证
        if strict_keyword_validation and semantic_keywords:
            keyword_operator = search_section.get('keyword_operator', 'AND').upper()
            # 多个关键词且为AND关系：要求所有关键词都实质性包含
            # 单个关键词：也进行严格验证，只检查该技术是否被实质性使用
            if keyword_operator == 'AND' or len(semantic_keywords) == 1:
                logger.info(f"进行严格关键词验证，要求实质性包含以下关键词: {semantic_keywords}（严格度: {validation_strictness}）")
                return self._strict_validate_keywords(papers, semantic_keywords, filter_config, validation_strictness)
        
        filtered_count = 0
        filtered_reasons = {}
        
        for paper in papers:
            # 检查摘要长度
            if min_abstract_length > 0:
                if not paper.abstract or len(paper.abstract) < min_abstract_length:
                    filtered_count += 1
                    filtered_reasons['摘要太短'] = filtered_reasons.get('摘要太短', 0) + 1
                    continue
            
            # 检查排除关键词
            if exclude_keywords:
                text = f"{paper.title or ''} {paper.abstract or ''}".lower()
                if any(kw in text for kw in exclude_keywords):
                    filtered_count += 1
                    filtered_reasons['包含排除关键词'] = filtered_reasons.get('包含排除关键词', 0) + 1
                    continue
            
            # 检查必须包含的关键词
            if include_keywords:
                text = f"{paper.title or ''} {paper.abstract or ''}".lower()
                if not any(kw in text for kw in include_keywords):
                    filtered_count += 1
                    filtered_reasons['缺少必需关键词'] = filtered_reasons.get('缺少必需关键词', 0) + 1
                    continue
            
            filtered.append(paper)
        
        # 记录过滤统计
        if filtered_count > 0:
            reasons_str = ", ".join([f"{k}: {v}" for k, v in filtered_reasons.items()])
            logger.info(f"过滤掉 {filtered_count} 篇论文（原因: {reasons_str}）")
        
        return filtered, keyword_validation_info
    
    def _strict_validate_keywords(self, papers: List[PaperMetadata], keywords: List[str], filter_config: Dict, validation_strictness: str = 'normal') -> tuple[List[PaperMetadata], Dict]:
        """
        严格验证关键词：使用LLM检查每篇论文是否实质性包含所有关键词
        
        Args:
            papers: 论文列表
            keywords: 关键词列表
            filter_config: 过滤配置
            validation_strictness: 验证严格度级别（'normal', 'strict', 'very_strict'）
        
        Returns:
            (严格匹配的论文列表, 关键词匹配验证信息)
        """
        # 检查是否有LLM可用
        try:
            from core.llm.openai import OpenAIProvider
            from core.config import get_config
            config = get_config()
            if not config.llm.api_key:
                logger.warning("LLM不可用，无法进行严格关键词验证，使用基础过滤")
                return self._basic_keyword_filter(papers, keywords, filter_config)
            
            llm_client = OpenAIProvider(config.llm)
        except Exception as e:
            logger.warning(f"无法初始化LLM进行严格验证: {e}，使用基础过滤")
            return self._basic_keyword_filter(papers, keywords, filter_config)
        
        # 加载验证提示词
        prompts_dir = Path(__file__).parent / "prompts"
        prompt_file = prompts_dir / "validate_keywords.txt"
        if not prompt_file.exists():
            logger.warning("验证提示词文件不存在，使用基础过滤")
            return self._basic_keyword_filter(papers, keywords, filter_config)
        
        with open(prompt_file, 'r', encoding='utf-8') as f:
            prompt_template = f.read()
        
        strict_matched = []
        partial_matched = []
        validation_info = {}
        
        min_abstract_length = filter_config.get('min_abstract_length', 100)
        
        for i, paper in enumerate(papers, 1):
            # 基础过滤：摘要长度
            if min_abstract_length > 0:
                if not paper.abstract or len(paper.abstract) < min_abstract_length:
                    validation_info[paper.title or f"论文{i}"] = {
                        'all_match': False,
                        'reason': '摘要太短',
                        'keyword_evidence': {}
                    }
                    continue
            
            # 使用LLM验证关键词（采用更严格的验证策略）
            try:
                abstract = paper.abstract[:1500] if paper.abstract else "无摘要"  # 增加摘要长度限制，获取更多上下文
                prompt = prompt_template.format(
                    title=paper.title or '无标题',
                    abstract=abstract,
                    keywords=", ".join(keywords)
                )
                
                # 使用更低的temperature和更多的token，确保判断更严格
                response = llm_client.generate(prompt, max_tokens=400, temperature=0.0)  # 温度设为0，更确定性
                
                # 解析JSON响应
                import json
                import re
                json_match = re.search(r'\{.*\}', response, re.DOTALL)
                if json_match:
                    result = json.loads(json_match.group(0))
                    all_match = result.get('all_keywords_match', False)
                    keyword_evidence = result.get('keyword_evidence', {})
                    reason = result.get('reason', '')
                    match_level = result.get('match_level', 'none')
                    matched_keywords = result.get('matched_keywords', [])
                    
                    # 根据严格度级别进行额外检查
                    if validation_strictness in ['strict', 'very_strict']:
                        # 检查是否所有关键词都有实质性证据
                        evidence_count = len([ev for ev in keyword_evidence.values() 
                                             if ev and '未' not in ev and '仅' not in ev 
                                             and '背景' not in ev and '提及' not in ev 
                                             and '展望' not in ev and '讨论' not in ev])
                        
                        if evidence_count < len(keywords):
                            # 如果证据不足，降级为部分匹配
                            logger.warning(f"⚠️  证据不足，降级为部分匹配: {paper.title[:50] if paper.title else '无标题'}")
                            all_match = False
                            match_level = 'partial'
                            reason = f"证据不足：仅{evidence_count}/{len(keywords)}个关键词有实质性使用证据"
                        
                        # 非常严格模式：要求matched_keywords必须包含所有关键词
                        if validation_strictness == 'very_strict':
                            if len(matched_keywords) < len(keywords):
                                logger.warning(f"⚠️  非常严格模式：匹配关键词不足，拒绝: {paper.title[:50] if paper.title else '无标题'}")
                                all_match = False
                                match_level = 'partial'
                                reason = f"非常严格模式：仅{len(matched_keywords)}/{len(keywords)}个关键词被判定为实质性使用"
                    
                    # 额外检查：如果match_level是"none"，即使all_match为true也拒绝
                    if match_level == 'none' and all_match:
                        logger.warning(f"⚠️  LLM判定为不匹配但all_match为true，拒绝: {paper.title[:50] if paper.title else '无标题'}")
                        all_match = False
                        match_level = 'partial'
                    
                    validation_info[paper.title or f"论文{i}"] = {
                        'all_match': all_match,
                        'keyword_evidence': keyword_evidence,
                        'reason': reason,
                        'match_level': match_level,
                        'matched_keywords': matched_keywords
                    }
                    
                    if all_match and match_level == 'strict':
                        strict_matched.append(paper)
                        logger.info(f"✅ 严格匹配: {paper.title[:50] if paper.title else '无标题'}")
                    else:
                        partial_matched.append(paper)
                        logger.info(f"⚠️  部分匹配: {paper.title[:50] if paper.title else '无标题'} - {reason}")
                else:
                    # 如果无法解析，使用基础过滤
                    logger.warning(f"无法解析LLM验证结果，使用基础过滤: {paper.title[:50] if paper.title else '无标题'}")
                    if self._basic_keyword_check(paper, keywords):
                        strict_matched.append(paper)
                    else:
                        partial_matched.append(paper)
            except Exception as e:
                logger.warning(f"验证论文关键词失败: {e}, 论文: {paper.title[:50] if paper.title else '无标题'}")
                # 出错时使用基础过滤
                if self._basic_keyword_check(paper, keywords):
                    strict_matched.append(paper)
                else:
                    partial_matched.append(paper)
        
        logger.info(f"严格匹配: {len(strict_matched)} 篇，部分匹配: {len(partial_matched)} 篇")
        
        # 返回严格匹配的论文和验证信息
        return strict_matched, {
            'strict_matched': len(strict_matched),
            'partial_matched': len(partial_matched),
            'validation_details': validation_info,
            'partial_papers': partial_matched[:10]  # 保留前10篇部分匹配的论文信息
        }
    
    def _basic_keyword_filter(self, papers: List[PaperMetadata], keywords: List[str], filter_config: Dict) -> tuple[List[PaperMetadata], Dict]:
        """基础关键词过滤（不使用LLM）"""
        filtered = []
        validation_info = {}
        
        min_abstract_length = filter_config.get('min_abstract_length', 100)
        
        for paper in papers:
            if min_abstract_length > 0:
                if not paper.abstract or len(paper.abstract) < min_abstract_length:
                    continue
            
            # 检查所有关键词是否都在标题或摘要中
            text = f"{paper.title or ''} {paper.abstract or ''}".lower()
            all_found = all(kw.lower() in text for kw in keywords)
            
            if all_found:
                filtered.append(paper)
                validation_info[paper.title or '未知'] = {
                    'all_match': True,
                    'keyword_evidence': {kw: '在标题或摘要中找到' for kw in keywords},
                    'reason': ''
                }
        
        return filtered, {'validation_details': validation_info}
    
    def _basic_keyword_check(self, paper: PaperMetadata, keywords: List[str]) -> bool:
        """基础关键词检查（不使用LLM）"""
        text = f"{paper.title or ''} {paper.abstract or ''}".lower()
        return all(kw.lower() in text for kw in keywords)
    
    def _build_search_description_html(self) -> str:
        """构建HTML格式的检索说明块"""
        search_cfg = self.config.get('search', {})
        expanded_keywords = search_cfg.get('keywords') or []
        
        if isinstance(expanded_keywords, str):
            expanded_keywords = [expanded_keywords]
        
        if not expanded_keywords:
            return ""
        
        # 构造逻辑关系说明
        if len(expanded_keywords) > 1:
            if len(expanded_keywords) == 2:
                logic_line = f"我将寻找同时包含【{expanded_keywords[0]}】和【{expanded_keywords[1]}】的文献，并在报告中标注每篇文献的匹配情况。"
            else:
                head = "、".join([f"【{kw}】" for kw in expanded_keywords[:-1]])
                tail = f"和【{expanded_keywords[-1]}】"
                logic_line = f"我将寻找同时包含{head}{tail}的文献，并在报告中标注每篇文献的匹配情况。"
        elif len(expanded_keywords) == 1:
            logic_line = f"我将寻找包含【{expanded_keywords[0]}】的文献，并在报告中标注每篇文献的匹配情况。"
        else:
            logic_line = "我将根据配置中的关键词进行文献检索，并在报告中标注每篇文献的匹配情况。"
        
        # 统一只展示英文检索关键词
        keywords_line = "； ".join(expanded_keywords) if expanded_keywords else "未指定"
        
        html = f"""
                <div class="summary">
                    <h2>🔍 检索说明</h2>
                    <p>{logic_line}</p>
                    <p><strong>检索关键词（英文）：</strong>{keywords_line}</p>
                </div>
        """
        return html
    
    def _build_search_description_text(self) -> str:
        """构建纯文本格式的检索说明块"""
        search_cfg = self.config.get('search', {})
        expanded_keywords = search_cfg.get('keywords') or []
        
        if isinstance(expanded_keywords, str):
            expanded_keywords = [expanded_keywords]
        
        if not expanded_keywords:
            return ""
        
        if len(expanded_keywords) > 1:
            if len(expanded_keywords) == 2:
                logic_line = f"我将寻找同时包含【{expanded_keywords[0]}】和【{expanded_keywords[1]}】的文献，并在报告中标注每篇文献的匹配情况。"
            else:
                head = "、".join([f"【{kw}】" for kw in expanded_keywords[:-1]])
                tail = f"和【{expanded_keywords[-1]}】"
                logic_line = f"我将寻找同时包含{head}{tail}的文献，并在报告中标注每篇文献的匹配情况。"
        elif len(expanded_keywords) == 1:
            logic_line = f"我将寻找包含【{expanded_keywords[0]}】的文献，并在报告中标注每篇文献的匹配情况。"
        else:
            logic_line = "我将根据配置中的关键词进行文献检索，并在报告中标注每篇文献的匹配情况。"
        
        keywords_line = "； ".join(expanded_keywords) if expanded_keywords else "未指定"
        
        text = f"""
检索说明
----------------
{logic_line}
检索关键词（英文）：{keywords_line}

"""
        return text
    
    def _format_summary_html(self, summary: str) -> str:
        """
        将Markdown格式的总结转换为格式化的HTML
        
        Args:
            summary: Markdown格式的总结文本（可能包含<br>标签）
        
        Returns:
            HTML格式的总结
        """
        import re
        
        # 先转义HTML特殊字符（但保留已有的HTML标签）
        # 临时标记已有的HTML标签
        html = summary
        
        # 处理标题（**一、标题** 或 **标题**）
        # 先处理带编号的标题
        html = re.sub(r'\*\*([一二三四五六七八九十]+[、.].+?)\*\*', r'<h3>\1</h3>', html)
        # 再处理其他加粗文本（但排除已经在h3中的）
        html = re.sub(r'\*\*([^<*]+?)\*\*', r'<strong>\1</strong>', html)
        
        # 处理段落：将<br><br>或双换行转换为段落分隔
        html = html.replace('\r\n', '\n').replace('\r', '\n')
        # 将连续的<br>或换行转换为段落分隔
        html = re.sub(r'(<br>\s*){2,}', '</p><p>', html)
        html = re.sub(r'\n\n+', '</p><p>', html)
        html = re.sub(r'(<br>\s*)?\n(<br>\s*)?', '<br>', html)
        
        # 处理列表项
        lines = html.split('<br>')
        result_parts = []
        current_paragraph = []
        in_list = False
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # 检查是否是标题
            if line.startswith('<h3>'):
                # 如果当前有段落，先输出
                if current_paragraph:
                    result_parts.append('<p>' + ' '.join(current_paragraph) + '</p>')
                    current_paragraph = []
                if in_list:
                    result_parts.append('</ul>')
                    in_list = False
                result_parts.append(line)
                continue
            
            # 有序列表（数字. 开头）
            list_match = re.match(r'^(\d+)\.\s+(.+)$', line)
            if list_match:
                if current_paragraph:
                    result_parts.append('<p>' + ' '.join(current_paragraph) + '</p>')
                    current_paragraph = []
                if not in_list:
                    result_parts.append('<ul>')
                    in_list = True
                result_parts.append(f'<li>{list_match.group(2)}</li>')
                continue
            
            # 无序列表（- 或 • 开头）
            ulist_match = re.match(r'^[-•]\s+(.+)$', line)
            if ulist_match:
                if current_paragraph:
                    result_parts.append('<p>' + ' '.join(current_paragraph) + '</p>')
                    current_paragraph = []
                if not in_list:
                    result_parts.append('<ul>')
                    in_list = True
                result_parts.append(f'<li>{ulist_match.group(1)}</li>')
                continue
            
            # 普通文本
            if in_list:
                result_parts.append('</ul>')
                in_list = False
            
            current_paragraph.append(line)
        
        # 处理剩余的段落
        if current_paragraph:
            result_parts.append('<p>' + ' '.join(current_paragraph) + '</p>')
        if in_list:
            result_parts.append('</ul>')
        
        html = '\n'.join(result_parts)
        
        # 清理和优化
        html = re.sub(r'</p>\s*<p>', '</p><p>', html)  # 合并相邻段落
        html = re.sub(r'<p>\s*</p>', '', html)  # 删除空段落
        html = re.sub(r'</ul>\s*<ul>', '', html)  # 合并相邻列表
        html = re.sub(r'<p>\s*<h3>', '<h3>', html)  # 段落中的标题
        html = re.sub(r'</h3>\s*<p>', '</h3>', html)
        
        # 确保有内容
        if not html.strip():
            html = '<p>' + summary.replace('<br>', '<br>').replace('\n', '<br>') + '</p>'
        
        return html
    
    def format_email_content(self, papers: List[PaperMetadata], format_type: str = 'html', summary: Optional[str] = None, paper_summaries: Optional[Dict[str, str]] = None) -> str:
        """
        格式化邮件内容
        
        Args:
            papers: 论文列表
            format_type: 格式类型（html 或 text）
            summary: 文献总结（可选）
            paper_summaries: 每篇论文的中文总结字典，key为论文ID或标题（可选）
        
        Returns:
            格式化后的邮件内容
        """
        if format_type == 'html':
            return self._format_html_email(papers, summary, paper_summaries)
        else:
            return self._format_text_email(papers, summary, paper_summaries)
    
    def _format_html_email(self, papers: List[PaperMetadata], summary: Optional[str] = None, paper_summaries: Optional[Dict[str, str]] = None) -> str:
        """格式化HTML邮件"""
        # 主题名称：使用英文检索关键词（期刊为英文）
        search_cfg = self.config.get('search', {})
        expanded_keywords = search_cfg.get('keywords') or []
        if isinstance(expanded_keywords, str):
            expanded_keywords = [expanded_keywords]
        theme = " + ".join(expanded_keywords) if expanded_keywords else "未指定主题"
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .header {{ background-color: #4CAF50; color: white; padding: 20px; text-align: center; }}
                .content {{ padding: 20px; }}
                .summary {{ background-color: #e8f5e9; padding: 20px; margin-bottom: 20px; border-radius: 5px; border-left: 4px solid #4CAF50; }}
                .summary h2 {{ color: #2e7d32; margin-top: 0; font-size: 20px; }}
                .summary h3 {{ color: #388e3c; font-size: 16px; margin-top: 20px; margin-bottom: 10px; }}
                .summary p {{ line-height: 1.8; color: #333; margin: 10px 0; }}
                .summary ul, .summary ol {{ margin: 10px 0; padding-left: 25px; }}
                .summary li {{ margin: 8px 0; line-height: 1.8; }}
                .summary strong {{ color: #2e7d32; font-weight: 600; }}
                .paper {{ margin-bottom: 30px; padding: 15px; border-left: 4px solid #4CAF50; background-color: #f9f9f9; }}
                .title {{ font-size: 18px; font-weight: bold; color: #2c3e50; margin-bottom: 10px; }}
                .meta {{ color: #7f8c8d; font-size: 14px; margin-bottom: 10px; }}
                .abstract {{ color: #555; margin-top: 10px; line-height: 1.8; }}
                .link {{ color: #3498db; text-decoration: none; }}
                .footer {{ text-align: center; padding: 20px; color: #7f8c8d; font-size: 12px; }}
            </style>
        </head>
        <body>
            <div class="header">
                <h1>📚 主题文献推送（主题：{theme}）</h1>
                <p>共找到 {len(papers)} 篇新文献</p>
                <p>日期: {datetime.now().strftime('%Y年%m月%d日')}</p>
            </div>
            <div class="content">
        """
        # 构建检索说明块
        html += self._build_search_description_html()
        
        # 添加总结（如果有）
        if summary:
            # 优化总结格式：将Markdown格式转换为HTML
            summary_html = self._format_summary_html(summary)
            html += f"""
                <div class="summary">
                    <h2>📊 文献总结</h2>
                    {summary_html}
                </div>
            """
        
        for i, paper in enumerate(papers, 1):
            # 安全处理作者信息
            authors_str = "未知"
            if paper.authors:
                try:
                    # 尝试提取作者名称
                    author_names = []
                    for author in paper.authors[:5]:
                        if hasattr(author, 'name'):
                            author_names.append(author.name)
                        elif isinstance(author, str):
                            author_names.append(author)
                        elif hasattr(author, '__str__'):
                            author_names.append(str(author))
                    
                    if author_names:
                        authors_str = ", ".join(author_names)
                        if len(paper.authors) > 5:
                            authors_str += " et al."
                except Exception:
                    authors_str = "未知"
            
            # 处理摘要：优先显示中文总结，如果没有则显示英文摘要
            abstract_html = ""
            paper_id = paper.doi or paper.title or str(i)
            chinese_summary = None
            if paper_summaries:
                # 尝试通过DOI、标题或索引获取中文总结
                chinese_summary = (paper_summaries.get(paper.doi) or 
                                 paper_summaries.get(paper.title) or 
                                 paper_summaries.get(paper_id) or
                                 paper_summaries.get(str(i)))
            
            if chinese_summary:
                # 显示中文总结
                summary_text = chinese_summary.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                abstract_html = f'<div class="abstract"><strong>中文总结:</strong> {summary_text}</div>'
                # 如果配置了显示英文摘要，也显示
                if paper.abstract and self.config.get('report', {}).get('show_english_abstract', False):
                    abstract_text = paper.abstract[:300].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    abstract_html += f'<div class="abstract" style="margin-top: 10px; font-size: 0.9em; color: #666;"><strong>英文摘要:</strong> {abstract_text}...</div>'
            elif paper.abstract:
                # 没有中文总结，显示英文摘要
                abstract_text = paper.abstract[:500]
                abstract_text = abstract_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                abstract_html = f'<div class="abstract"><strong>摘要:</strong> {abstract_text}{"..." if len(paper.abstract) > 500 else ""}</div>'
            
            # 安全处理标题（转义HTML特殊字符）
            title = paper.title or '无标题'
            title = title.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            
            html += f"""
                <div class="paper">
                    <div class="title">{i}. {title}</div>
                    <div class="meta">
                        <strong>作者:</strong> {authors_str}<br>
                        <strong>期刊:</strong> {paper.journal or '未知'}<br>
                        <strong>年份:</strong> {paper.year or '未知'}
                        {f'<br><strong>DOI:</strong> {paper.doi}' if paper.doi else ''}
                    </div>
                    {abstract_html}
                    {f'<div style="margin-top: 10px;"><a href="{paper.url}" class="link">查看原文</a></div>' if paper.url else ''}
                </div>
            """
        
        html += """
            </div>
            <div class="footer">
                <p>本邮件由生信+AI文献每日推送智能体自动生成</p>
            </div>
        </body>
        </html>
        """
        
        return html
    
    def _format_text_email(self, papers: List[PaperMetadata], summary: Optional[str] = None, paper_summaries: Optional[Dict[str, str]] = None) -> str:
        """格式化纯文本邮件"""
        # 主题名称：使用英文检索关键词（期刊为英文）
        search_cfg = self.config.get('search', {})
        expanded_keywords = search_cfg.get('keywords') or []
        if isinstance(expanded_keywords, str):
            expanded_keywords = [expanded_keywords]
        theme = " + ".join(expanded_keywords) if expanded_keywords else "未指定主题"
        
        text = f"""
主题文献推送（主题：{theme}）
========================

共找到 {len(papers)} 篇新文献
日期: {datetime.now().strftime('%Y年%m月%d日')}

"""
        # 添加检索说明
        text += self._build_search_description_text()
        
        # 添加总结（如果有）
        if summary:
            text += f"""
{'='*60}
文献总结
{'='*60}

{summary}

{'='*60}
文献列表
{'='*60}

"""
        
        for i, paper in enumerate(papers, 1):
            # 安全处理作者信息
            authors_str = "未知"
            if paper.authors:
                try:
                    # 尝试提取作者名称
                    author_names = []
                    for author in paper.authors[:5]:
                        if hasattr(author, 'name'):
                            author_names.append(author.name)
                        elif isinstance(author, str):
                            author_names.append(author)
                        elif hasattr(author, '__str__'):
                            author_names.append(str(author))
                    
                    if author_names:
                        authors_str = ", ".join(author_names)
                        if len(paper.authors) > 5:
                            authors_str += " et al."
                except Exception:
                    authors_str = "未知"
            
            # 处理摘要：优先显示中文总结
            abstract_text = ""
            paper_id = paper.doi or paper.title or str(i)
            chinese_summary = None
            if paper_summaries:
                chinese_summary = (paper_summaries.get(paper.doi) or 
                                 paper_summaries.get(paper.title) or 
                                 paper_summaries.get(paper_id) or
                                 paper_summaries.get(str(i)))
            
            if chinese_summary:
                abstract_text = f'中文总结: {chinese_summary}'
                if paper.abstract and self.config.get('report', {}).get('show_english_abstract', False):
                    abstract_text += f'\n   英文摘要: {paper.abstract[:300]}...'
            elif paper.abstract:
                abstract_text = f'摘要: {paper.abstract[:500]}{"..." if len(paper.abstract) > 500 else ""}'
            
            text += f"""
{i}. {paper.title or '无标题'}
   作者: {authors_str}
   期刊: {paper.journal or '未知'}
   年份: {paper.year or '未知'}
   {f'DOI: {paper.doi}' if paper.doi else ''}
   {f'链接: {paper.url}' if paper.url else ''}
   {abstract_text}
   
"""
        
        text += "\n本邮件由文献每日推送智能体自动生成\n"
        
        return text
    
    def send_email(self, papers: List[PaperMetadata], summary: Optional[str] = None, paper_summaries: Optional[Dict[str, str]] = None) -> bool:
        """
        发送邮件
        
        Args:
            papers: 论文列表
            summary: 文献总结（可选）
            paper_summaries: 每篇论文的中文总结字典（可选）
        
        Returns:
            是否发送成功
        """
        if not papers:
            logger.info("没有新文献，跳过邮件发送")
            return True
        
        email_config = self.config['email']
        format_type = self.config['report'].get('format', 'html')
        
        # 创建邮件
        msg = MIMEMultipart('alternative')
        msg['From'] = email_config['from_email']
        msg['To'] = email_config['to_email']
        msg['Subject'] = f"文献每日推送 - {datetime.now().strftime('%Y年%m月%d日')} ({len(papers)}篇)"
        
        # 添加邮件内容（包含总结和论文中文总结）
        content = self.format_email_content(papers, format_type, summary, paper_summaries)
        if format_type == 'html':
            msg.attach(MIMEText(content, 'html', 'utf-8'))
        else:
            msg.attach(MIMEText(content, 'plain', 'utf-8'))
        
        # 发送邮件
        try:
            smtp_server = email_config.get('smtp_server')
            smtp_port = email_config.get('smtp_port')
            smtp_username = email_config.get('smtp_username')
            smtp_password = email_config.get('smtp_password')
            use_tls = email_config.get('use_tls', True)
            
            # 验证配置
            if not all([smtp_server, smtp_port, smtp_username, smtp_password]):
                logger.error("邮件配置不完整，请检查config.yaml中的email配置")
                return False
            
            if smtp_password == "请填写你的QQ邮箱授权码" or not smtp_password:
                logger.error("未配置SMTP密码（授权码），请检查config.yaml")
                return False
            
            logger.info(f"连接SMTP服务器: {smtp_server}:{smtp_port}")
            
            # 连接SMTP服务器
            try:
                server = smtplib.SMTP(smtp_server, smtp_port, timeout=10)
            except (smtplib.SMTPConnectError, OSError) as e:
                logger.error(f"无法连接到SMTP服务器: {e}")
                logger.error("请检查：1. SMTP服务器地址和端口是否正确 2. 网络连接是否正常")
                return False
            
            # 启用TLS
            if use_tls:
                try:
                    server.starttls()
                    logger.info("TLS加密已启用")
                except Exception as e:
                    logger.warning(f"启用TLS失败: {e}，尝试继续...")
            
            # 登录
            try:
                server.login(smtp_username, smtp_password)
                logger.info("SMTP登录成功")
            except smtplib.SMTPAuthenticationError as e:
                logger.error(f"SMTP认证失败: {e}")
                logger.error("请检查：1. 邮箱地址是否正确 2. 授权码是否正确（不是QQ密码） 3. 是否已开启SMTP服务")
                server.quit()
                return False
            
            # 发送邮件
            try:
                server.send_message(msg)
                logger.info(f"邮件发送成功！已发送 {len(papers)} 篇文献到 {email_config['to_email']}")
            except Exception as e:
                logger.error(f"发送邮件时出错: {e}")
                server.quit()
                return False
            
            server.quit()
            
            # 保存已发送的文献记录
            self._save_sent_papers_cache(papers)
            # 更新缓存集合
            for paper in papers:
                if paper.doi:
                    self.sent_papers_cache.add(paper.doi.lower())
                elif paper.title:
                    self.sent_papers_cache.add(paper.title.lower())
            
            return True
        except Exception as e:
            logger.error(f"发送邮件失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return False
    
    def run(self):
        """运行完整流程"""
        logger.info("=" * 80)
        logger.info("文献每日推送智能体")
        logger.info("=" * 80)
        
        # 1. 搜索文献
        papers = self.search_literature()
        
        if not papers:
            logger.info("未找到新文献")
            return
        
        # 2. 发送邮件
        success = self.send_email(papers)
        
        if success:
            logger.info("=" * 80)
            logger.info("推送完成！")
            logger.info("=" * 80)
        else:
            logger.error("推送失败，请检查配置和网络连接")


def main():
    """主函数"""
    # 创建智能体
    agent = BioinfoAILiteratureDaily()
    
    # 运行
    agent.run()


if __name__ == "__main__":
    main()
