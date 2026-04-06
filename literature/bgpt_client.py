"""
BGPT Paper Search 客户端

通过 MCP SSE 协议调用 BGPT 服务，获取论文结构化全文数据。
BGPT 返回 25+ 字段：方法、量化结果、样本量、质量评分、结论等。

官网: https://bgpt.pro/mcp
免费额度: 50 次/网络（无需 API key）
付费: $0.01/条结果（需要 API key）

依赖安装:
    pip install mcp
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

BGPT_SSE_URL = "https://bgpt.pro/mcp/sse"


class BGPTClient:
    """BGPT MCP 客户端，查询论文结构化数据"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key

    def fetch_by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        """按 DOI 查询 BGPT，返回结构化论文数据或 None"""
        # BGPT 不支持 DOI: 前缀语法，直接用 DOI 字符串搜索
        return self._run(doi)

    def fetch_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """按标题查询 BGPT，返回结构化论文数据或 None"""
        return self._run(title)

    def _run(self, query: str) -> Optional[Dict[str, Any]]:
        """同步入口，封装 asyncio.run"""
        try:
            return asyncio.run(self._async_search(query))
        except RuntimeError:
            # 已有事件循环（如 Jupyter）时的兼容处理
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(self._async_search(query))
            finally:
                loop.close()
        except Exception as e:
            logger.warning(f"BGPT 查询失败: {e}")
            return None

    async def _async_search(self, query: str) -> Optional[Dict[str, Any]]:
        try:
            from mcp import ClientSession
            from mcp.client.sse import sse_client
        except ImportError:
            logger.warning("mcp 未安装，跳过 BGPT。安装命令: pip install mcp")
            return None

        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            async with sse_client(BGPT_SSE_URL, headers=headers) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "search_papers",
                        {"query": query}
                    )
                    return self._parse_result(result)
        except Exception as e:
            logger.warning(f"BGPT MCP 调用失败: {e}")
            return None

    def _parse_result(self, result) -> Optional[Dict[str, Any]]:
        """
        把 BGPT MCP 返回结果转成我们的分析格式

        BGPT 返回字段（25+）示例：
          title, authors, journal, year, doi,
          methods, results, sample_size, quality_score,
          conclusions, background, limitations, ...
        """
        import json

        # MCP ToolResult 的内容在 content 列表里
        content = result.content
        if not content:
            return None

        # 收集所有 TextContent
        raw_text = ""
        for item in content:
            raw_text += item.text if hasattr(item, 'text') else str(item)

        logger.debug(f"BGPT 原始返回（前300字符）: {raw_text[:300]}")

        if not raw_text.strip():
            return None

        # 解析策略：依次尝试，直到成功
        data = self._try_parse_json(raw_text)
        if data is None:
            return None

        # 取第一条结果
        if isinstance(data, list):
            if not data:
                logger.info("BGPT 返回空列表，该论文不在数据库中")
                return None
            data = data[0]

        if not isinstance(data, dict):
            logger.warning(f"BGPT 返回非预期类型: {type(data)}")
            return None

        return self._map_to_analysis(data)

    def _try_parse_json(self, text: str) -> Optional[Any]:
        """
        多策略 JSON 解析，只返回 dict 或 list，跳过标量值。
        1. 直接 json.loads
        2. raw_decode（忽略尾部多余内容）
        3. NDJSON（逐行解析，收集所有 dict/list）
        4. 正则提取第一个 JSON 数组或对象
        """
        import json, re

        text = text.strip()

        def is_useful(obj):
            """只接受 dict 或非空 list，拒绝标量（int/str/None 等）"""
            return isinstance(obj, dict) or (isinstance(obj, list) and len(obj) > 0)

        # 策略 1：标准解析
        try:
            obj = json.loads(text)
            if is_useful(obj):
                return obj
        except json.JSONDecodeError:
            pass

        # 策略 2：raw_decode，允许尾部有多余内容
        try:
            obj, _ = json.JSONDecoder().raw_decode(text)
            if is_useful(obj):
                return obj
        except json.JSONDecodeError:
            pass

        # 策略 3：NDJSON（逐行解析，只收集 dict/list）
        objects = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if is_useful(obj):
                    objects.append(obj)
            except json.JSONDecodeError:
                pass
        if objects:
            return objects if len(objects) > 1 else objects[0]

        # 策略 4：正则提取第一个 JSON 数组或对象
        for pattern in (r'(\[[\s\S]*?\])', r'(\{[\s\S]*?\})'):
            m = re.search(pattern, text)
            if m:
                try:
                    obj = json.loads(m.group(1))
                    if is_useful(obj):
                        return obj
                except json.JSONDecodeError:
                    pass

        logger.warning(f"BGPT 所有 JSON 解析策略均失败，原始内容前200字符: {text[:200]}")
        return None

    def _map_to_analysis(self, data: Dict) -> Dict[str, Any]:
        """将 BGPT 字段映射到我们的结构化解析格式"""
        # 如果是错误响应，返回 None
        if "error" in data:
            logger.debug(f"BGPT 返回错误: {data.get('error')}")
            return None

        # BGPT 返回结构是 {results: [...], count: N, ...}，提取 results
        if "results" in data:
            results = data.get("results", [])
            if not results:
                logger.info(f"BGPT 查询成功但无结果 (count={data.get('count', 0)})")
                return None
            # 取第一条结果
            data = results[0] if isinstance(results, list) else results
            logger.info(f"BGPT 命中，剩余免费次数: {data.get('free_results_remaining', '?')}")

        logger.debug(f"BGPT 论文字段: {list(data.keys())}")

        # BGPT 实际字段映射（基于 2024 年返回格式）
        # problem_statement → 研究背景/目的
        # methods_and_experimental_techniques → 方法
        # results_and_conclusions → 主要发现 + 结论
        # paper_limitations_and_biases → 局限性
        # one_sentence_summary → 备用摘要

        # 研究背景：用 study_context 或 problem_statement
        background = (data.get("study_context") or data.get("problem_statement") or "").strip()

        # 研究目的：用 problem_statement
        objective = (data.get("problem_statement") or "").strip()

        # 方法
        methods = (data.get("methods_and_experimental_techniques") or "").strip()

        # 主要发现：从 results_and_conclusions 提取
        results_text = data.get("results_and_conclusions") or ""
        if results_text:
            # 按句号分割取前几条作为发现
            sentences = [s.strip() for s in results_text.replace(". ", ".|").split("|") if s.strip()]
            findings = sentences[:4] if sentences else [results_text]
        else:
            findings = []

        # 结论
        conclusion = (data.get("results_and_conclusions") or data.get("one_sentence_summary") or "").strip()

        # 局限性
        limitations = (data.get("paper_limitations_and_biases") or data.get("study_blindspots") or "").strip()
        sample_info = (data.get("sample_size_and_population_characteristics") or "").strip()
        quality_score = data.get("paper_scientific_quality_score") or ""

        limitations_parts = []
        if sample_info:
            limitations_parts.append(f"样本: {sample_info}")
        if limitations:
            limitations_parts.append(limitations)
        if quality_score and quality_score != "None":
            limitations_parts.append(f"（质量评分: {quality_score}/10）")
        limitations_text = " ".join(limitations_parts) if limitations_parts else "—"

        return {
            "研究背景": background or "—",
            "研究目的": objective or "—",
            "方法": methods or "—",
            "主要发现": findings if findings else ["—"],
            "结论与意义": conclusion or "—",
            "局限性": limitations_text,
            "解析级别": "BGPT全文解析",
            "_bgpt_raw": data,  # 保留原始数据供调试
        }
