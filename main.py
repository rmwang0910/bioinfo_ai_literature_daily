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
import re
import yaml
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import smtplib

# 配置logging：默认只在控制台输出重要日志（WARNING及以上）
log_level_name = os.environ.get("BIOAI_LOG_LEVEL", "WARNING").upper()
log_level = getattr(logging, log_level_name, logging.WARNING)
logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 导入本地文献搜索模块（独立，不依赖 BioLitKG）
try:
    from literature.pubmed_client import PubMedClient
    from literature.base_client import PaperMetadata, PaperSource
    from literature.unified_search import UnifiedLiteratureSearch
except ImportError as e:
    logger.error(f"无法导入文献搜索模块: {e}")
    logger.error("请确保已安装所有依赖（biopython, arxiv 等）")
    sys.exit(1)

try:
    from core.rag.cwts_source_filter import CWTSSourceFilter
except ImportError:
    CWTSSourceFilter = None
try:
    from core.rag.scimago_metrics import ScimagoMetrics
except ImportError:
    ScimagoMetrics = None


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
        
        # 初始化搜索客户端（PubMed 为基础必需）
        self.pubmed_client = PubMedClient()
        # 统一检索客户端（按需懒加载）
        self._unified_search: Optional["UnifiedLiteratureSearch"] = None
        
        # 加载已发送的文献记录（用于去重）
        self.sent_papers_cache = self._load_sent_papers_cache()
        
        # 关键词验证信息（用于生成验证表）
        self._keyword_validation_info = {}
        self.last_ris_path = None
        self.cwts_source_filter = None
        if CWTSSourceFilter:
            try:
                self.cwts_source_filter = CWTSSourceFilter()
            except Exception as e:
                logger.warning(f"初始化 CWTS 来源知识库失败: {e}")
        self.scimago_metrics = None
        if ScimagoMetrics:
            metrics_cfg = self.config.get("journal_metrics", {}) or {}
            scimago_path = metrics_cfg.get("scimago_csv_path")
            if scimago_path:
                try:
                    self.scimago_metrics = ScimagoMetrics(scimago_path)
                except Exception as e:
                    logger.warning(f"初始化 Scimago 指标失败: {e}")
    
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
                'smtp_server': '',
                'smtp_port': 587,
                'smtp_username': '',
                'smtp_password': '',
                'from_email': '',
                'to_email': '',
                'use_tls': True
            },
            'filter': {
                'min_abstract_length': 100,  # 最小摘要长度
                'exclude_keywords': [],  # 排除关键词列表
                'include_keywords': [],  # 必须包含的关键词（如果设置）
                'allowed_journals': [],
                'allowed_fields': [],
                'min_impact_factor': None,
                'max_impact_factor': None
            },
            'report': {
                'max_papers': 50,  # 最多发送50篇文献
                'format': 'html',  # 邮件格式：html 或 text
                'translate_abstract': False  # 是否将英文摘要翻译成中文
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
    
    def search_literature(self, progress_callback=None) -> List[PaperMetadata]:
        """
        搜索最新的生信+AI相关文献

        Args:
            progress_callback: 可选的进度回调函数，签名 callback(step: str, message: str)

        Returns:
            论文列表
        """
        def _progress(step: str, message: str):
            if progress_callback:
                try:
                    progress_callback(step, message)
                except Exception:
                    pass

        _progress("init", "正在初始化搜索参数...")
        logger.info("开始搜索文献...")
        search_config = self.config['search']
        keywords = search_config.get('keywords', [])
        max_results = search_config.get('max_results_per_keyword', 20)
        # 关键词组合方式：'AND'（交集）或 'OR'（并集），默认为'AND'
        keyword_operator = search_config.get('keyword_operator', 'AND').upper()
        # 是否启用 BioLitKG 的统一检索（PubMed + arXiv 等）
        use_unified_search = bool(search_config.get('use_unified_search', False))
        
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
        
        all_papers: List[PaperMetadata] = []
        
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
        for qi, query_keywords in enumerate(queries, 1):
            _progress("searching", f"正在检索第 {qi}/{len(queries)} 组查询...")
            logger.info(f"搜索查询: {query_keywords}")
            try:
                # 确保年份是整数
                year_from = int(min_date.year) if min_date else None
                year_to = int(max_date.year) if max_date else None

                papers: List[PaperMetadata] = []

                # 1）优先尝试使用 BioLitKG 的统一检索（多源：PubMed + arXiv 等）
                if use_unified_search and UnifiedLiteratureSearch is not None:
                    # 懒加载统一检索客户端
                    if self._unified_search is None:
                        try:
                            logger.info("初始化统一检索（PubMed + arXiv + bioRxiv）")
                            # 这里不强依赖 BioLitKG 的独立安装，只使用当前仓库中的代码
                            self._unified_search = UnifiedLiteratureSearch(
                                arxiv_enabled=True,
                                semantic_scholar_enabled=False,  # 已禁用 Semantic Scholar
                                pubmed_enabled=True,
                                biorxiv_enabled=True,  # 启用 bioRxiv
                            )
                        except Exception as e:
                            logger.warning(f"初始化统一检索失败，将回退到仅使用 PubMed: {e}")
                            self._unified_search = None

                    if self._unified_search is not None:
                        # 统一检索接口不需要 PubMed 特有的 [Title/Abstract] 后缀
                        unified_query = query_keywords
                        logger.info("使用统一检索（PubMed + arXiv + bioRxiv）进行多源搜索")
                        papers = self._unified_search.search(
                            query=unified_query,
                            max_results_per_source=max_results,
                            year_from=year_from,
                            year_to=year_to,
                            deduplicate=True,
                        )

                # 2）如果未启用统一检索或初始化失败，则仅使用 PubMed
                if not papers:
                    # 构建 PubMed 特有的查询语法
                    query = f"{query_keywords}[Title/Abstract]"
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
                            
                            # 处理时区问题：确保 paper_date 和 min_date/max_date 都是 offset-naive
                            # 如果 paper_date 是 offset-aware（有时区信息），转换为 offset-naive
                            if paper_date.tzinfo is not None:
                                # 转换为本地时间（去掉时区信息）
                                paper_date = paper_date.replace(tzinfo=None)
                            
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
                logger.warning(f"搜索查询 '{query_keywords}' 时出错: {e}")
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
        _progress("dedup", f"去重后共 {len(unique_papers)} 篇，正在进行关键词验证...")

        # 应用过滤器（启用严格关键词验证）
        strict_validation = self.config.get('search', {}).get('strict_keyword_validation', True)  # 默认启用
        validation_strictness = self.config.get('search', {}).get('validation_strictness', 'very_strict')  # 默认最严格
        filtered_papers, keyword_validation_info = self._filter_papers(
            unique_papers, 
            strict_keyword_validation=strict_validation,
            validation_strictness=validation_strictness
        )
        # 使用WARNING级别，向用户展示严格验证后的论文数
        logger.warning(f"过滤后剩余 {len(filtered_papers)} 篇论文（严格度: {validation_strictness}）")
        _progress("filtered", f"关键词验证完成，匹配 {len(filtered_papers)} 篇")
        
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
            
            # 使用WARNING级别，向用户展示去重后的新论文数
            logger.warning(f"去除已发送文献后，剩余 {len(new_papers)} 篇新论文")
        
        # 限制数量
        max_papers = self.config['report'].get('max_papers', 50)
        new_papers = new_papers[:max_papers]
        
        # 按时间近到远排序（优先使用publication_date，其次使用year）
        def get_sort_date(paper: PaperMetadata) -> datetime:
            """获取用于排序的日期（统一为 offset-naive）"""
            if paper.publication_date:
                paper_date = paper.publication_date
                # 处理时区问题：确保返回的 datetime 是 offset-naive
                if paper_date.tzinfo is not None:
                    # 转换为本地时间（去掉时区信息）
                    paper_date = paper_date.replace(tzinfo=None)
                return paper_date
            elif paper.year:
                # 只有年份，使用该年的最后一天作为排序基准（确保同年论文排在前面）
                return datetime(paper.year, 12, 31)
            else:
                # 没有日期信息，排到最后
                return datetime(1900, 1, 1)
        
        new_papers.sort(key=get_sort_date, reverse=True)  # reverse=True 表示近到远

        _progress("done", f"搜索完成，共 {len(new_papers)} 篇文献")
        self.last_ris_path = self._export_ris(new_papers)
        if self.last_ris_path:
            logger.warning(f"已生成 EndNote 导入文件: {self.last_ris_path}")
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
        
        # 统一处理摘要长度阈值，防止 None 与 int 比较导致错误
        raw_min_len = filter_config.get('min_abstract_length', 100)
        min_abstract_length = raw_min_len if isinstance(raw_min_len, (int, float)) else 0
        exclude_keywords = [kw.lower() for kw in filter_config.get('exclude_keywords', [])]
        include_keywords = [kw.lower() for kw in filter_config.get('include_keywords', [])]
        allowed_journals = [str(j).strip().lower() for j in filter_config.get('allowed_journals', []) if str(j).strip()]
        allowed_fields = [str(f).strip().lower() for f in filter_config.get('allowed_fields', []) if str(f).strip()]
        min_impact_factor = filter_config.get('min_impact_factor')
        max_impact_factor = filter_config.get('max_impact_factor')
        
        # 获取用于严格验证的关键词（优先使用语义关键词，其次使用检索关键词）
        search_section = self.config.get('search', {})
        semantic_keywords = search_section.get('semantic_keywords') or search_section.get('keywords', [])
        # 如果启用严格验证且存在关键词，则使用LLM进行严格验证
        if strict_keyword_validation and semantic_keywords:
            keyword_operator = search_section.get('keyword_operator', 'AND').upper()
            # 多个关键词且为AND关系：要求所有关键词都实质性包含
            # 单个关键词：也进行严格验证，只检查该技术是否被实质性使用
            if keyword_operator == 'AND' or len(semantic_keywords) == 1:
                # 使用WARNING级别，让用户在精简日志模式下也能看到“已进入严格验证阶段”
                logger.warning(f"进行严格关键词验证，要求实质性包含以下关键词: {semantic_keywords}（严格度: {validation_strictness}）")
                return self._strict_validate_keywords(papers, semantic_keywords, filter_config, validation_strictness)
        
        filtered_count = 0
        filtered_reasons = {}
        
        for paper in papers:
            metadata_ok, metadata_reason = self._passes_metadata_filters(
                paper,
                allowed_journals=allowed_journals,
                allowed_fields=allowed_fields,
                min_impact_factor=min_impact_factor,
                max_impact_factor=max_impact_factor
            )
            if not metadata_ok:
                filtered_count += 1
                filtered_reasons[metadata_reason] = filtered_reasons.get(metadata_reason, 0) + 1
                continue

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

    def _passes_metadata_filters(
        self,
        paper: PaperMetadata,
        allowed_journals: List[str],
        allowed_fields: List[str],
        min_impact_factor: Optional[float],
        max_impact_factor: Optional[float]
    ) -> tuple[bool, str]:
        if allowed_journals:
            journal_name = (paper.journal or paper.venue or "").strip()
            if not journal_name and self.cwts_source_filter:
                journal_name = self.cwts_source_filter.get_source_name_for_paper(paper) or ""
            journal = journal_name.lower()
            if not journal:
                return False, '缺少期刊信息'
            if not any(j in journal or journal in j for j in allowed_journals):
                return False, '不在指定期刊范围'

        if allowed_fields:
            base_fields = [str(f).strip() for f in (paper.fields or []) if str(f).strip()]
            rag_fields = self.cwts_source_filter.get_fields_for_paper(paper) if self.cwts_source_filter else []
            rag_topics = self.cwts_source_filter.get_micro_topics_for_paper(paper) if self.cwts_source_filter else []
            merged_fields = []
            seen_fields = set()
            for item in (base_fields + rag_fields + rag_topics):
                val = str(item).strip()
                key = val.lower()
                if val and key not in seen_fields:
                    merged_fields.append(val)
                    seen_fields.add(key)
            if not base_fields and rag_fields:
                paper.fields = rag_fields
            field_candidates = [f.lower() for f in merged_fields]
            if not field_candidates:
                text = f"{paper.title or ''} {paper.abstract or ''}".lower()
                has_field_match = any(f in text for f in allowed_fields)
            else:
                has_field_match = any(
                    (allow in field_val or field_val in allow)
                    for allow in allowed_fields
                    for field_val in field_candidates
                )
            if not has_field_match:
                return False, '不在指定领域范围'

        if min_impact_factor is not None or max_impact_factor is not None:
            impact_factor = self._get_journal_impact_factor(paper.journal)
            if impact_factor is None:
                return False, '缺少影响因子信息'
            if min_impact_factor is not None and impact_factor < float(min_impact_factor):
                return False, '影响因子低于下限'
            if max_impact_factor is not None and impact_factor > float(max_impact_factor):
                return False, '影响因子高于上限'

        return True, ''
    
    def _strict_validate_keywords(self, papers: List[PaperMetadata], keywords: List[str], filter_config: Dict, validation_strictness: str = 'normal') -> tuple[List[PaperMetadata], Dict]:
        """
        严格验证关键词：使用LLM检查每篇论文是否实质性包含所有关键词
        
        Args:
            papers: 论文列表
            keywords: 主题关键词列表（用于第一层验证）
            filter_config: 过滤配置
            validation_strictness: 验证严格度级别（'normal', 'strict', 'very_strict'）
        
        Returns:
            (严格匹配的论文列表, 关键词匹配验证信息)
        """
        # 获取文章类型关键词（用于第二层过滤）
        search_cfg = self.config.get('search', {}) or {}
        article_type_keywords = search_cfg.get('article_type_keywords', [])
        if article_type_keywords:
            logger.info(f"启用两层验证：主题关键词={keywords}，文章类型关键词={article_type_keywords}")
        
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
        
        # 统一处理摘要长度阈值，防止 None 与 int 比较导致错误
        raw_min_len = filter_config.get('min_abstract_length', 100)
        min_abstract_length = raw_min_len if isinstance(raw_min_len, (int, float)) else 0
        allowed_journals = [str(j).strip().lower() for j in filter_config.get('allowed_journals', []) if str(j).strip()]
        allowed_fields = [str(f).strip().lower() for f in filter_config.get('allowed_fields', []) if str(f).strip()]
        min_impact_factor = filter_config.get('min_impact_factor')
        max_impact_factor = filter_config.get('max_impact_factor')
        
        total_papers = len(papers)
        if total_papers > 0:
            logger.warning(
                f"进入严格验证阶段，共 {total_papers} 篇候选论文，将使用LLM并行检查关键词匹配情况..."
            )

        # --- 第一轮：元数据/摘要预过滤（无 LLM，极快） ---
        llm_candidates = []  # (index, paper) 通过预过滤的论文
        for i, paper in enumerate(papers, 1):
            metadata_ok, metadata_reason = self._passes_metadata_filters(
                paper,
                allowed_journals=allowed_journals,
                allowed_fields=allowed_fields,
                min_impact_factor=min_impact_factor,
                max_impact_factor=max_impact_factor
            )
            if not metadata_ok:
                validation_info[paper.title or f"论文{i}"] = {
                    'all_match': False, 'reason': metadata_reason, 'keyword_evidence': {}
                }
                continue
            if min_abstract_length > 0 and (not paper.abstract or len(paper.abstract) < min_abstract_length):
                validation_info[paper.title or f"论文{i}"] = {
                    'all_match': False, 'reason': '摘要太短', 'keyword_evidence': {}
                }
                continue
            llm_candidates.append((i, paper))

        if not llm_candidates:
            return strict_matched, {
                'strict_matched': 0, 'partial_matched': 0,
                'validation_details': validation_info, 'partial_papers': []
            }

        logger.warning(f"预过滤后 {len(llm_candidates)} 篇需要 LLM 验证（并行 max_workers=5）")

        # --- 第二轮：LLM 并行验证 ---
        import json as _json
        import re as _re
        from concurrent.futures import ThreadPoolExecutor, as_completed

        article_types_str = ", ".join(article_type_keywords) if article_type_keywords else "null"
        logical_desc = search_cfg.get('logical_keywords_description') or ""

        def _validate_one(idx_paper):
            """单篇 LLM 验证（在子线程中执行）。返回 (index, paper, result_dict)。"""
            idx, paper = idx_paper
            abstract = paper.abstract[:1500] if paper.abstract else "无摘要"
            prompt = prompt_template.format(
                title=paper.title or '无标题',
                abstract=abstract,
                keywords=", ".join(keywords),
                article_type_keywords=article_types_str,
                logical_keywords_description=logical_desc
            )
            response = llm_client.generate(prompt, max_tokens=400, temperature=0.0)
            json_match = _re.search(r'\{.*\}', response, _re.DOTALL)
            if json_match:
                return idx, paper, _json.loads(json_match.group(0))
            return idx, paper, None

        completed_count = 0
        max_workers = min(5, len(llm_candidates))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_validate_one, item): item
                for item in llm_candidates
            }
            for future in as_completed(futures):
                i, paper = futures[future]
                completed_count += 1
                if completed_count == 1 or completed_count % 10 == 0 or completed_count == len(llm_candidates):
                    tp = paper.title[:50] if paper.title else "N/A"
                    logger.warning("严格验证进度: %d/%d (当前: %s)", completed_count, len(llm_candidates), tp)

                try:
                    _, _, result = future.result()
                except Exception as e:
                    logger.warning(f"验证论文关键词失败: {e}, 论文: {paper.title[:50] if paper.title else 'N/A'}")
                    if self._basic_keyword_check(paper, keywords):
                        strict_matched.append(paper)
                    else:
                        partial_matched.append(paper)
                    continue

                if result is None:
                    logger.warning(f"无法解析LLM验证结果，使用基础过滤: {paper.title[:50] if paper.title else 'N/A'}")
                    if self._basic_keyword_check(paper, keywords):
                        strict_matched.append(paper)
                    else:
                        partial_matched.append(paper)
                    continue

                all_match = result.get('all_keywords_match', False)
                keyword_evidence = result.get('keyword_evidence', {})
                reason = result.get('reason', '')
                match_level = result.get('match_level', 'none')
                matched_keywords = result.get('matched_keywords', [])

                # 文章类型匹配（第二层过滤）
                article_type_match = True
                matched_article_types = []
                if article_type_keywords:
                    article_type_match = result.get('article_type_match', False)
                    matched_article_types = result.get('matched_article_types', [])
                    if all_match and not article_type_match:
                        all_match = False
                        match_level = 'partial'
                        reason = f"主题匹配但文章类型不匹配（期望: {', '.join(article_type_keywords)}）"

                # 严格度额外检查
                if validation_strictness in ['strict', 'very_strict']:
                    evidence_count = len([ev for ev in keyword_evidence.values()
                                         if ev and '未' not in ev and '仅' not in ev
                                         and '背景' not in ev and '提及' not in ev
                                         and '展望' not in ev and '讨论' not in ev])
                    if evidence_count < len(keywords):
                        all_match = False
                        match_level = 'partial'
                        reason = f"证据不足：{evidence_count}/{len(keywords)}个有实质性证据"
                    if validation_strictness == 'very_strict' and len(matched_keywords) < len(keywords):
                        all_match = False
                        match_level = 'partial'
                        reason = f"非常严格：{len(matched_keywords)}/{len(keywords)}个关键词实质性使用"

                if match_level == 'none' and all_match:
                    all_match = False
                    match_level = 'partial'

                validation_info[paper.title or f"论文{i}"] = {
                    'all_match': all_match,
                    'keyword_evidence': keyword_evidence,
                    'reason': reason,
                    'match_level': match_level,
                    'matched_keywords': matched_keywords,
                    'article_type_match': article_type_match,
                    'matched_article_types': matched_article_types
                }

                is_strict_match = all_match and match_level == 'strict'
                if article_type_keywords:
                    is_strict_match = is_strict_match and article_type_match

                if is_strict_match:
                    strict_matched.append(paper)
                else:
                    partial_matched.append(paper)
        
        # 使用WARNING级别，向用户展示严格匹配与部分匹配的数量
        logger.warning(f"严格匹配: {len(strict_matched)} 篇，部分匹配: {len(partial_matched)} 篇")
        
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
        
        # 统一处理摘要长度阈值，防止 None 与 int 比较导致错误
        raw_min_len = filter_config.get('min_abstract_length', 100)
        min_abstract_length = raw_min_len if isinstance(raw_min_len, (int, float)) else 0
        allowed_journals = [str(j).strip().lower() for j in filter_config.get('allowed_journals', []) if str(j).strip()]
        allowed_fields = [str(f).strip().lower() for f in filter_config.get('allowed_fields', []) if str(f).strip()]
        min_impact_factor = filter_config.get('min_impact_factor')
        max_impact_factor = filter_config.get('max_impact_factor')
        
        for paper in papers:
            metadata_ok, _ = self._passes_metadata_filters(
                paper,
                allowed_journals=allowed_journals,
                allowed_fields=allowed_fields,
                min_impact_factor=min_impact_factor,
                max_impact_factor=max_impact_factor
            )
            if not metadata_ok:
                continue

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
    
    def _generate_source_statistics(self, papers: List[PaperMetadata]) -> Dict[str, Any]:
        """
        生成文献来源统计
        
        Args:
            papers: 论文列表
            
        Returns:
            包含统计信息的字典
        """
        from collections import Counter
        
        # 统计来源
        source_counts = Counter()
        source_names = {
            PaperSource.PUBMED: "PubMed",
            PaperSource.ARXIV: "arXiv",
            PaperSource.BIORXIV: "bioRxiv",
            PaperSource.SEMANTIC_SCHOLAR: "Semantic Scholar",
            PaperSource.UNKNOWN: "未知来源",
            PaperSource.MANUAL: "手动添加"
        }
        
        for paper in papers:
            source = paper.source if hasattr(paper, 'source') else PaperSource.UNKNOWN
            source_counts[source] += 1
        
        # 转换为列表格式，便于显示
        stats = []
        total = len(papers)
        for source, count in source_counts.most_common():
            name = source_names.get(source, source.value if hasattr(source, 'value') else str(source))
            percentage = (count / total * 100) if total > 0 else 0
            stats.append({
                'name': name,
                'count': count,
                'percentage': percentage,
                'source': source
            })
        
        return {
            'total': total,
            'sources': stats,
            'source_counts': dict(source_counts)
        }
    
    def _format_source_statistics_html(self, stats: Dict[str, Any]) -> str:
        """
        格式化来源统计为HTML（包含简单图表）
        
        Args:
            stats: 统计信息字典
            
        Returns:
            HTML格式的统计图表
        """
        if not stats['sources']:
            return ""
        
        # 颜色映射
        colors = {
            'PubMed': '#4CAF50',
            'arXiv': '#2196F3',
            'bioRxiv': '#9C27B0',
            'Semantic Scholar': '#FF9800',
            '未知来源': '#9E9E9E',
            '手动添加': '#607D8B'
        }
        
        html = """
        <div class="source-stats">
            <h3>📈 文献来源统计</h3>
            <div class="stats-container">
        """
        
        # 生成条形图
        max_count = max(s['count'] for s in stats['sources']) if stats['sources'] else 1
        
        for source_info in stats['sources']:
            name = source_info['name']
            count = source_info['count']
            percentage = source_info['percentage']
            color = colors.get(name, '#607D8B')
            bar_width = (count / max_count * 100) if max_count > 0 else 0
            
            html += f"""
                <div class="stat-item">
                    <div class="stat-label">
                        <span class="stat-name">{name}</span>
                        <span class="stat-count">{count} 篇 ({percentage:.1f}%)</span>
                    </div>
                    <div class="stat-bar-container">
                        <div class="stat-bar" style="width: {bar_width}%; background-color: {color};"></div>
                    </div>
                </div>
            """
        
        html += """
            </div>
        </div>
        """
        
        return html
    
    def _format_source_statistics_text(self, stats: Dict[str, Any]) -> str:
        """
        格式化来源统计为纯文本（ASCII图表）
        
        Args:
            stats: 统计信息字典
            
        Returns:
            文本格式的统计图表
        """
        if not stats['sources']:
            return ""
        
        text = "\n文献来源统计:\n"
        text += "-" * 50 + "\n"
        
        max_count = max(s['count'] for s in stats['sources']) if stats['sources'] else 1
        bar_length = 30  # 条形图最大长度
        
        for source_info in stats['sources']:
            name = source_info['name']
            count = source_info['count']
            percentage = source_info['percentage']
            bar_width = int((count / max_count * bar_length)) if max_count > 0 else 0
            bar = "█" * bar_width + "░" * (bar_length - bar_width)
            
            text += f"{name:20s} {bar} {count:3d} 篇 ({percentage:5.1f}%)\n"
        
        text += "-" * 50 + "\n"
        
        return text
    
    def _translate_abstract(self, abstract: str) -> Optional[str]:
        """
        使用LLM将英文摘要翻译成中文
        
        Args:
            abstract: 英文摘要
            
        Returns:
            中文翻译，如果翻译失败则返回None
        """
        if not abstract or len(abstract.strip()) == 0:
            return None
        
        try:
            from core.llm.openai import OpenAIProvider
            from core.config import get_config
            config = get_config()
            if not config.llm.api_key:
                logger.warning("LLM不可用，无法翻译摘要")
                return None
            
            llm_client = OpenAIProvider(config.llm)
            
            # 构建翻译提示词
            prompt = f"""请将以下英文摘要翻译成中文，要求：
1. 保持学术性和专业性
2. 准确传达原文意思
3. 语言流畅自然
4. 只返回翻译结果，不要添加任何解释或说明

英文摘要：
{abstract[:2000]}  # 限制长度避免token过多
"""
            
            translated = llm_client.generate(prompt, max_tokens=1000, temperature=0.3)
            
            # 清理翻译结果（移除可能的引号或多余内容）
            translated = translated.strip()
            if translated.startswith('"') and translated.endswith('"'):
                translated = translated[1:-1]
            if translated.startswith("'") and translated.endswith("'"):
                translated = translated[1:-1]
            
            return translated.strip()
            
        except Exception as e:
            logger.warning(f"翻译摘要失败: {e}")
            return None
    
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
    
    def _get_journal_impact_factor_info(self, journal_name: Optional[str]) -> tuple[Optional[float], Optional[str]]:
        """
        获取期刊影响因子
        
        Args:
            journal_name: 期刊名称
            
        Returns:
            (影响因子, 来源标签)
        """
        if not journal_name:
            return None, None

        metrics_cfg = self.config.get("journal_metrics", {}) or {}
        scimago_metric = metrics_cfg.get("scimago_metric", "cites_per_doc_2y")
        if self.scimago_metrics:
            value, label = self.scimago_metrics.get_impact_factor(journal_name, metric=scimago_metric)
            if value is not None:
                return value, f"Scimago {label}" if label else "Scimago"

        impact_factors = {
            # Nature 系列
            "Nature": 64.8,
            "Nature Biotechnology": 46.9,
            "Nature Methods": 48.0,
            "Nature Genetics": 30.8,
            "Nature Cell Biology": 21.3,
            "Nature Medicine": 82.9,
            "Nature Communications": 16.6,
            "Nature Machine Intelligence": 25.9,
            "Nature Computational Science": 11.2,
            "Nature Protocols": 14.3,
            "Nature Reviews Genetics": 42.7,
            "Nature Reviews Methods Primers": 20.0,
            
            # Science 系列
            "Science": 56.9,
            "Science Advances": 13.6,
            
            # Cell 系列
            "Cell": 64.5,
            "Cell Systems": 9.5,
            "Cell Reports": 8.1,
            
            # 生物信息学相关
            "Bioinformatics": 5.8,
            "Nucleic Acids Research": 14.9,
            "Genome Research": 11.0,
            "Genome Biology": 12.3,
            "PLOS Computational Biology": 4.3,
            "BMC Bioinformatics": 3.0,
            "Briefings in Bioinformatics": 9.5,
            "Database": 3.5,
            
            # 计算生物学
            "Journal of Computational Biology": 1.7,
            "Computational Biology and Chemistry": 2.9,
            
            # 其他高影响因子期刊
            "Cell": 64.5,
            "The Lancet": 168.9,
            "New England Journal of Medicine": 176.1,
            "JAMA": 120.7,
            "PNAS": 11.1,
            "eLife": 7.7,
            "PLOS Biology": 9.8,
            "Genome Biology": 12.3,
        }
        
        # 尝试精确匹配
        if journal_name in impact_factors:
            return impact_factors[journal_name], "JCR"
        
        # 尝试不区分大小写匹配
        journal_lower = journal_name.lower()
        for journal, if_value in impact_factors.items():
            if journal.lower() == journal_lower:
                return if_value, "JCR"
        
        # 尝试部分匹配（处理期刊名称变体）
        for journal, if_value in impact_factors.items():
            if journal.lower() in journal_lower or journal_lower in journal.lower():
                return if_value, "JCR"
        
        return None, None

    def _get_journal_impact_factor(self, journal_name: Optional[str]) -> Optional[float]:
        impact_factor, _ = self._get_journal_impact_factor_info(journal_name)
        return impact_factor
    
    def _format_publication_date(self, paper: PaperMetadata) -> str:
        """
        格式化发布日期，显示年月
        
        Args:
            paper: 论文元数据
            
        Returns:
            格式化的日期字符串，如 "2024年3月" 或 "2024年"
        """
        if paper.publication_date:
            # 有精确日期，显示年月
            paper_date = paper.publication_date
            # 处理时区问题：确保是 offset-naive
            if paper_date.tzinfo is not None:
                paper_date = paper_date.replace(tzinfo=None)
            return paper_date.strftime('%Y年%m月')
        elif paper.year:
            # 只有年份，显示年份
            return f"{paper.year}年"
        else:
            return "未知"

    def _sanitize_filename(self, text: str) -> str:
        cleaned = re.sub(r"[^\w\u4e00-\u9fa5]+", "_", text or "")
        cleaned = re.sub(r"_+", "_", cleaned).strip("_")
        return cleaned or "literature"

    def _format_ris_date(self, paper: PaperMetadata) -> str:
        if paper.publication_date:
            dt = paper.publication_date
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            return dt.strftime("%Y/%m/%d")
        if paper.year:
            return f"{paper.year}//"
        return ""

    def _format_ris_authors(self, paper: PaperMetadata) -> List[str]:
        names = []
        for author in (paper.authors or []):
            if hasattr(author, "name"):
                name = author.name
            else:
                name = str(author)
            name = (name or "").strip()
            if name:
                names.append(name)
        return names

    def _get_pubmed_field(self, paper: PaperMetadata, key: str) -> Optional[str]:
        if not paper.raw_data or not isinstance(paper.raw_data, dict):
            return None
        value = paper.raw_data.get(key)
        if isinstance(value, list):
            if not value:
                return None
            return " ".join(str(v).strip() for v in value if str(v).strip())
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    def _get_openalex_field(self, paper: PaperMetadata, key: str) -> Optional[str]:
        if not paper.raw_data or not isinstance(paper.raw_data, dict):
            return None
        openalex = paper.raw_data.get("openalex")
        if not isinstance(openalex, dict):
            return None
        value = openalex.get(key)
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    def _split_pages(self, pages: Optional[str]) -> tuple[Optional[str], Optional[str]]:
        if not pages:
            return None, None
        text = str(pages).strip()
        if "-" in text:
            parts = [p.strip() for p in text.split("-", 1)]
            if len(parts) == 2:
                return parts[0] or None, parts[1] or None
        return text, None

    def _select_issn(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        text = str(value)
        matches = []
        for match in re.finditer(r"(\d{4}-\d{3}[\dX])\s*(?:\(([^)]+)\))?", text, re.IGNORECASE):
            issn = match.group(1)
            label = (match.group(2) or "").lower()
            matches.append((issn, label))
        if not matches:
            return None
        for issn, label in matches:
            if "electronic" in label:
                return issn
        for issn, label in matches:
            if "print" in label:
                return issn
        return matches[0][0]

    def _get_ris_db_label(self, paper: PaperMetadata) -> Optional[str]:
        if not getattr(paper, "source", None):
            return None
        if paper.source == PaperSource.PUBMED:
            return "PubMed"
        if paper.source == PaperSource.ARXIV:
            return "arXiv"
        if paper.source == PaperSource.BIORXIV:
            return "bioRxiv"
        if paper.source == PaperSource.SEMANTIC_SCHOLAR:
            return "Semantic Scholar"
        return None

    def _get_ris_type(self, paper: PaperMetadata) -> tuple[str, str]:
        if paper.source in {PaperSource.ARXIV, PaperSource.BIORXIV}:
            return "ELEC", "Preprint"
        return "JOUR", "Journal Article"

    def _get_preprint_journal_name(self, paper: PaperMetadata) -> str:
        journal = (paper.journal or "").strip()
        if journal.lower().startswith("medrxiv"):
            return "medRxiv"
        return "bioRxiv"

    def _export_ris(self, papers: List[PaperMetadata]) -> Optional[str]:
        if not papers:
            return None
        theme = self._format_theme_display()
        safe_theme = self._sanitize_filename(theme)
        date_tag = datetime.now().strftime("%Y%m%d")
        filename = f"{date_tag}_{safe_theme}.ris"
        path = self.output_dir / filename
        lines = []
        for paper in papers:
            ris_ty, ris_m3 = self._get_ris_type(paper)
            lines.append(f"TY  - {ris_ty}")
            title = (paper.title or "").strip()
            if title:
                lines.append(f"T1  - {title}")
            for author in self._format_ris_authors(paper):
                lines.append(f"A1  - {author}")
            journal_full = None
            journal_abbrev = None
            if getattr(paper, "source", None) == PaperSource.PUBMED:
                journal_full = self._get_pubmed_field(paper, "JT")
                journal_abbrev = self._get_pubmed_field(paper, "TA")
            if paper.source in {PaperSource.ARXIV, PaperSource.BIORXIV}:
                journal_full = self._get_preprint_journal_name(paper)
                journal_abbrev = journal_full
            else:
                journal_full = journal_full or (paper.journal or paper.venue or "").strip() or None
                journal_abbrev = journal_abbrev or (paper.journal or "").strip() or None
            if journal_full:
                lines.append(f"JF  - {journal_full}")
            if journal_abbrev:
                lines.append(f"JO  - {journal_abbrev}")
            ris_date = self._format_ris_date(paper)
            if ris_date:
                lines.append(f"Y1  - {ris_date}")
            elif paper.year:
                lines.append(f"Y1  - {paper.year}//")
            if paper.pubmed_id:
                lines.append(f"ID  - PMID:{paper.pubmed_id}")
            elif paper.arxiv_id:
                lines.append(f"ID  - arXiv:{paper.arxiv_id}")
            db_label = self._get_ris_db_label(paper)
            if db_label:
                lines.append(f"DB  - {db_label}")
            volume = None
            issue = None
            pages = None
            issn = None
            if getattr(paper, "source", None) == PaperSource.PUBMED:
                volume = self._get_pubmed_field(paper, "VI")
                issue = self._get_pubmed_field(paper, "IP")
                pages = self._get_pubmed_field(paper, "PG")
                issn = self._get_pubmed_field(paper, "IS")
            if paper.source not in {PaperSource.ARXIV, PaperSource.BIORXIV}:
                if not volume:
                    volume = self._get_openalex_field(paper, "volume")
                if not issue:
                    issue = self._get_openalex_field(paper, "issue")
                if not pages:
                    first_page = self._get_openalex_field(paper, "first_page")
                    last_page = self._get_openalex_field(paper, "last_page")
                    if first_page and last_page:
                        pages = f"{first_page}-{last_page}"
                    elif first_page:
                        pages = first_page
                if not issn:
                    issn = self._get_openalex_field(paper, "issn")
            if volume:
                lines.append(f"VL  - {volume}")
            if issue:
                lines.append(f"IS  - {issue}")
            sp, ep = self._split_pages(pages)
            if sp:
                lines.append(f"SP  - {sp}")
            if ep:
                lines.append(f"EP  - {ep}")
            issn_value = self._select_issn(issn)
            if issn_value:
                lines.append(f"SN  - {issn_value}")
            if paper.doi:
                lines.append(f"DO  - {paper.doi}")
            if ris_m3:
                lines.append(f"M3  - {ris_m3}")
            url = paper.url
            if not url and paper.pubmed_id:
                url = f"https://pubmed.ncbi.nlm.nih.gov/{paper.pubmed_id}/"
            if url:
                lines.append(f"UR  - {url}")
            abstract = (paper.abstract or "").strip()
            if abstract:
                lines.append(f"N2  - {abstract}")
            keywords = paper.keywords or []
            for kw in keywords:
                kw = str(kw).strip()
                if kw:
                    lines.append(f"KW  - {kw}")
            if paper.source in {PaperSource.ARXIV, PaperSource.BIORXIV}:
                lines.append("N1  - Preprint")
            lines.append("ER  - ")
            lines.append("")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            return str(path)
        except Exception as e:
            logger.warning(f"写入 RIS 失败: {e}")
            return None
    
    def _format_theme_display(self) -> str:
        """
        格式化主题显示，从查询字符串中提取关键词并优化显示
        
        Returns:
            格式化的主题字符串，如 "bioinformatics + AI"
        """
        search_cfg = self.config.get('search', {})
        keywords = search_cfg.get('keywords') or []
        semantic_keywords = search_cfg.get('semantic_keywords') or []
        
        # 优先使用语义关键词（更友好）
        if semantic_keywords:
            if isinstance(semantic_keywords, str):
                semantic_keywords = [semantic_keywords]
            # 直接使用语义关键词，用 + 连接
            return " + ".join(semantic_keywords)
        
        # 如果没有语义关键词，从查询字符串中提取
        if isinstance(keywords, str):
            keywords = [keywords]
        
        if not keywords:
            return "未指定主题"
        
        # 从查询字符串中提取关键词（去掉括号、AND/OR等）
        extracted_keywords = []
        for kw in keywords:
            if isinstance(kw, str):
                # 如果包含 AND/OR，尝试提取括号中的关键词
                if ' AND ' in kw.upper() or ' OR ' in kw.upper():
                    # 提取括号中的内容
                    matches = re.findall(r'\(([^)]+)\)', kw)
                    if matches:
                        extracted_keywords.extend(matches)
                    else:
                        # 如果没有括号，按 AND/OR 分割
                        parts = re.split(r'\s+(?:AND|OR)\s+', kw, flags=re.IGNORECASE)
                        # 清理每个部分（去掉可能的括号）
                        cleaned = [re.sub(r'^\(|\)$', '', p.strip()) for p in parts]
                        extracted_keywords.extend(cleaned)
                else:
                    # 简单关键词，直接使用
                    cleaned = kw.strip()
                    # 去掉可能的括号
                    cleaned = re.sub(r'^\(|\)$', '', cleaned)
                    extracted_keywords.append(cleaned)
        
        # 去重并保持顺序
        seen = set()
        unique_keywords = []
        for kw in extracted_keywords:
            kw_clean = kw.strip()
            if kw_clean and kw_clean.lower() not in seen:
                seen.add(kw_clean.lower())
                unique_keywords.append(kw_clean)
        
        if unique_keywords:
            return " + ".join(unique_keywords)
        else:
            return "未指定主题"
    
    def _format_html_email(self, papers: List[PaperMetadata], summary: Optional[str] = None, paper_summaries: Optional[Dict[str, str]] = None) -> str:
        """格式化HTML邮件"""
        # 主题名称：优化显示格式
        theme = self._format_theme_display()
        
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
                .source-stats {{ background-color: #f5f5f5; padding: 20px; margin: 20px 0; border-radius: 5px; border-left: 4px solid #2196F3; }}
                .source-stats h3 {{ color: #1976D2; margin-top: 0; font-size: 18px; }}
                .stats-container {{ margin-top: 15px; }}
                .stat-item {{ margin-bottom: 15px; }}
                .stat-label {{ display: flex; justify-content: space-between; margin-bottom: 5px; font-size: 14px; }}
                .stat-name {{ font-weight: 600; color: #333; }}
                .stat-count {{ color: #666; }}
                .stat-bar-container {{ width: 100%; height: 25px; background-color: #e0e0e0; border-radius: 12px; overflow: hidden; }}
                .stat-bar {{ height: 100%; border-radius: 12px; transition: width 0.3s ease; }}
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

        if self.last_ris_path:
            ris_name = Path(self.last_ris_path).name
            html += f"""
                <div class="source-stats">
                    <strong>EndNote 导入文件:</strong> {ris_name}（已作为附件发送）
                </div>
            """
        
        # 生成并添加来源统计
        if len(papers) > 0:
            source_stats = self._generate_source_statistics(papers)
            html += self._format_source_statistics_html(source_stats)
        
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
            
            # 处理摘要：优先显示中文总结，如果没有则显示翻译后的摘要或英文摘要
            abstract_html = ""
            paper_id = paper.doi or paper.title or str(i)
            chinese_summary = None
            translated_abstract = None
            
            if paper_summaries:
                # 尝试通过DOI、标题或索引获取中文总结或翻译
                chinese_summary = (paper_summaries.get(paper.doi) or 
                                 paper_summaries.get(paper.title) or 
                                 paper_summaries.get(paper_id) or
                                 paper_summaries.get(str(i)))
            
            # 获取翻译后的摘要（使用 _translated 后缀的键）
            translate_enabled = self.config.get('report', {}).get('translate_abstract', False)
            translated_abstract = None
            if translate_enabled and paper.abstract:
                # 尝试获取翻译后的摘要（使用 _translated 后缀的键）
                translated_abstract = (paper_summaries.get(f"{paper.doi}_translated") or 
                                     paper_summaries.get(f"{paper.title}_translated") or 
                                     paper_summaries.get(f"{paper_id}_translated") or
                                     paper_summaries.get(f"{str(i)}_translated"))
            
            if chinese_summary:
                # 显示中文总结
                summary_text = chinese_summary.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                abstract_html = f'<div class="abstract"><strong>中文总结:</strong> {summary_text}</div>'
                
                # 如果启用了翻译摘要，也显示翻译的摘要
                if translated_abstract:
                    translated_text = translated_abstract.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    abstract_html += f'<div class="abstract" style="margin-top: 10px;"><strong>中文摘要:</strong> {translated_text}</div>'
                # 如果配置了显示英文摘要，也显示
                elif paper.abstract and self.config.get('report', {}).get('show_english_abstract', False):
                    abstract_text = paper.abstract[:300].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    abstract_html += f'<div class="abstract" style="margin-top: 10px; font-size: 0.9em; color: #666;"><strong>英文摘要:</strong> {abstract_text}...</div>'
            elif translated_abstract:
                # 显示翻译后的摘要
                translated_text = translated_abstract.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                abstract_html = f'<div class="abstract"><strong>中文摘要:</strong> {translated_text}</div>'
                # 如果配置了显示英文摘要，也显示
                if paper.abstract and self.config.get('report', {}).get('show_english_abstract', False):
                    abstract_text = paper.abstract[:300].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    abstract_html += f'<div class="abstract" style="margin-top: 10px; font-size: 0.9em; color: #666;"><strong>英文摘要:</strong> {abstract_text}...</div>'
            elif paper.abstract:
                # 没有中文总结或翻译，显示英文摘要
                abstract_text = paper.abstract[:500]
                abstract_text = abstract_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                abstract_html = f'<div class="abstract"><strong>摘要:</strong> {abstract_text}{"..." if len(paper.abstract) > 500 else ""}</div>'
            
            # 安全处理标题（转义HTML特殊字符）
            title = paper.title or '无标题'
            title = title.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            
            # 格式化发布日期（年月）
            pub_date_str = self._format_publication_date(paper)
            
            # 获取影响因子
            impact_factor, impact_source = self._get_journal_impact_factor_info(paper.journal)
            journal_display = paper.journal or '未知'
            if impact_factor:
                if impact_source:
                    journal_display += f" (IF: {impact_factor:.1f}, {impact_source})"
                else:
                    journal_display += f" (IF: {impact_factor:.1f})"
            
            html += f"""
                <div class="paper">
                    <div class="title">
                        <a href="{paper.url}" class="link" target="_blank">{title}</a>
                        {f'<span style="background-color: #ff9800; color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.7em; margin-left: 8px; vertical-align: middle;">OPEN ACCESS</span>' if getattr(paper, 'is_open_access', False) else ''}
                    </div>
                    <div class="meta">
                        <strong>作者:</strong> {authors_str}<br>
                        <strong>期刊:</strong> {journal_display}<br>
                        <strong>发表日期:</strong> {pub_date_str}
                        {f'<br><strong>被引:</strong> {paper.citation_count}' if getattr(paper, 'citation_count', 0) > 0 else ''}
                        {f'<br><strong>机构:</strong> {", ".join(paper.institutions[:2])}...' if getattr(paper, 'institutions', None) else ''}
                        {f'<br><strong>DOI:</strong> {paper.doi}' if paper.doi else ''}
                    </div>
                    {abstract_html}
                    <div style="margin-top: 10px;">
                        <a href="{paper.url}" class="link" target="_blank">查看原文</a>
                        {f' | <a href="{paper.pdf_url}" class="link" target="_blank">PDF下载</a>' if paper.pdf_url else ''}
                        {f' | <a href="{paper.open_access_url}" class="link" target="_blank">OA链接</a>' if getattr(paper, 'open_access_url', None) and paper.open_access_url != paper.pdf_url else ''}
                    </div>
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
        # 主题名称：优化显示格式
        theme = self._format_theme_display()
        
        text = f"""
主题文献推送（主题：{theme}）
========================

共找到 {len(papers)} 篇新文献
日期: {datetime.now().strftime('%Y年%m月%d日')}

"""
        # 添加检索说明
        text += self._build_search_description_text()

        if self.last_ris_path:
            ris_name = Path(self.last_ris_path).name
            text += f"\nEndNote 导入文件: {ris_name}（已作为附件发送）\n"
        
        # 生成并添加来源统计
        if len(papers) > 0:
            source_stats = self._generate_source_statistics(papers)
            text += self._format_source_statistics_text(source_stats)
        
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
            
            # 处理摘要：优先显示中文总结，如果没有则显示翻译后的摘要或英文摘要
            abstract_text = ""
            paper_id = paper.doi or paper.title or str(i)
            chinese_summary = None
            translated_abstract = None
            
            if paper_summaries:
                chinese_summary = (paper_summaries.get(paper.doi) or 
                                 paper_summaries.get(paper.title) or 
                                 paper_summaries.get(paper_id) or
                                 paper_summaries.get(str(i)))
            
            # 获取翻译后的摘要（使用 _translated 后缀的键）
            translate_enabled = self.config.get('report', {}).get('translate_abstract', False)
            translated_abstract = None
            if translate_enabled and paper.abstract:
                # 尝试获取翻译后的摘要（使用 _translated 后缀的键）
                translated_abstract = (paper_summaries.get(f"{paper.doi}_translated") or 
                                     paper_summaries.get(f"{paper.title}_translated") or 
                                     paper_summaries.get(f"{paper_id}_translated") or
                                     paper_summaries.get(f"{str(i)}_translated"))
            
            if chinese_summary:
                abstract_text = f'中文总结: {chinese_summary}'
                # 如果启用了翻译摘要，也显示翻译的摘要
                if translated_abstract:
                    abstract_text += f'\n   中文摘要: {translated_abstract}'
                # 如果配置了显示英文摘要，也显示
                elif paper.abstract and self.config.get('report', {}).get('show_english_abstract', False):
                    abstract_text += f'\n   英文摘要: {paper.abstract[:300]}...'
            elif translated_abstract:
                abstract_text = f'中文摘要: {translated_abstract}'
                if paper.abstract and self.config.get('report', {}).get('show_english_abstract', False):
                    abstract_text += f'\n   英文摘要: {paper.abstract[:300]}...'
            elif paper.abstract:
                abstract_text = f'摘要: {paper.abstract[:500]}{"..." if len(paper.abstract) > 500 else ""}'
            
            # 格式化发布日期（年月）
            pub_date_str = self._format_publication_date(paper)
            
            # 获取影响因子
            impact_factor, impact_source = self._get_journal_impact_factor_info(paper.journal)
            journal_display = paper.journal or '未知'
            if impact_factor:
                if impact_source:
                    journal_display += f" (IF: {impact_factor:.1f}, {impact_source})"
                else:
                    journal_display += f" (IF: {impact_factor:.1f})"
            
            # Open Access 标记
            oa_tag = "[OA] " if getattr(paper, 'is_open_access', False) else ""
            
            # 引用数
            citation_info = f" | 被引: {paper.citation_count}" if getattr(paper, 'citation_count', 0) > 0 else ""
            
            # 机构信息
            inst_info = ""
            if getattr(paper, 'institutions', None):
                inst_info = f"\n   机构: {', '.join(paper.institutions[:2])}"
            
            text += f"""
{i}. {oa_tag}{paper.title or '无标题'}
   作者: {authors_str}
   期刊: {journal_display}
   发表日期: {pub_date_str}{citation_info}{inst_info}
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
        # 如果启用了摘要翻译，翻译所有摘要
        if self.config.get('report', {}).get('translate_abstract', False):
            logger.info("开始翻译摘要(并行处理)...")
            if not paper_summaries:
                paper_summaries = {}

            from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed

            # 收集需要翻译的论文
            to_translate = [(i, p) for i, p in enumerate(papers) if p.abstract]

            def _translate_one(idx_paper):
                idx, paper = idx_paper
                translated = self._translate_abstract(paper.abstract)
                return idx, paper, translated

            max_workers = min(5, len(to_translate)) if to_translate else 1
            translated_count = 0
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futs = {executor.submit(_translate_one, item): item for item in to_translate}
                for fut in _as_completed(futs):
                    try:
                        i, paper, translated = fut.result()
                        if translated:
                            paper_id = paper.doi or paper.title or str(i)
                            if paper.doi:
                                paper_summaries[f"{paper.doi}_translated"] = translated
                            if paper.title:
                                paper_summaries[f"{paper.title}_translated"] = translated
                            paper_summaries[f"{paper_id}_translated"] = translated
                            paper_summaries[f"{str(i)}_translated"] = translated
                            translated_count += 1
                    except Exception as e:
                        idx, paper = futs[fut]
                        logger.warning(f"翻译失败: {paper.title[:50] if paper.title else 'N/A'}: {e}")

            logger.info(f"摘要翻译完成，共翻译 {translated_count} 篇")
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

        html_path = None
        if format_type == 'html':
            try:
                theme = self._format_theme_display()
                safe_theme = self._sanitize_filename(theme)
                date_tag = datetime.now().strftime('%Y%m%d')
                html_filename = f"{date_tag}_{safe_theme}.html"
                html_path = self.output_dir / html_filename
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(content)
                logger.warning(f"已保存 HTML 邮件内容: {html_path}")
            except Exception as e:
                logger.warning(f"保存 HTML 邮件内容失败: {e}")
        
        # 添加 RIS 附件（EndNote 导入）
        if self.last_ris_path:
            try:
                with open(self.last_ris_path, "rb") as f:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(f.read())
                encoders.encode_base64(part)
                filename = Path(self.last_ris_path).name
                part.add_header("Content-Disposition", "attachment", filename=("utf-8", "", filename))
                part.add_header("Content-Type", "application/octet-stream", name=("utf-8", "", filename))
                msg.attach(part)
            except Exception as e:
                logger.warning(f"添加 RIS 附件失败: {e}")

        if html_path:
            try:
                with open(html_path, "rb") as f:
                    part = MIMEBase("text", "html")
                    part.set_payload(f.read())
                encoders.encode_base64(part)
                filename = Path(html_path).name
                part.add_header("Content-Disposition", "attachment", filename=("utf-8", "", filename))
                part.add_header("Content-Type", "text/html", name=("utf-8", "", filename))
                msg.attach(part)
            except Exception as e:
                logger.warning(f"添加 HTML 附件失败: {e}")

        # 发送邮件
        try:
            smtp_server = email_config.get('smtp_server')
            smtp_port = email_config.get('smtp_port')
            smtp_username = email_config.get('smtp_username')
            smtp_password = email_config.get('smtp_password')
            use_tls = email_config.get('use_tls', True)
            
            # 验证配置
            if not all([smtp_server, smtp_port, smtp_username, smtp_password]):
                logger.error("邮件配置不完整，请检查配置文件中的email配置")
                return False
            
            if smtp_password == "请填写你的QQ邮箱授权码" or not smtp_password:
                logger.error("未配置SMTP密码（授权码），请检查配置文件")
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
            # 使用WARNING级别，明确告诉用户“没有新文献”
            logger.warning("未找到新文献")
            return
        
        # 2. 发送邮件
        success = self.send_email(papers)
        
        if success:
            # 基础模式也给出清晰完成提示
            logger.warning("=" * 80)
            logger.warning("推送完成！")
            logger.warning("=" * 80)
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
