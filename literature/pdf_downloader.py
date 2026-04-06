"""
PDF 下载与全文提取工具

获取优先级：
  1. OpenAlex 已提供的 OA URL (open_access_url)
  2. Unpaywall API（按 DOI 查询）
  3. PMC 全文 XML（按 PMID 查询 PMC ID）
  4. 降级：使用摘要

返回 (text, source_desc, pdf_bytes) 三元组。
pdf_bytes 仅在成功下载 PDF 时有值，PMC XML 和摘要降级时为 None。
"""
from __future__ import annotations

import logging
import tempfile
import requests
from pathlib import Path
from typing import Optional, Tuple

from .base_client import PaperMetadata

logger = logging.getLogger(__name__)

# Unpaywall 要求提供联系邮箱（免费，无需注册）
UNPAYWALL_EMAIL = "bioinfo.daily@example.com"

# 全文最大字符数，避免超出 LLM context
MAX_FULLTEXT_CHARS = 20000


class PDFDownloader:
    """PDF 下载与全文文本提取"""

    def get_fulltext(self, paper: PaperMetadata) -> Tuple[str, str, Optional[bytes]]:
        """
        尝试获取论文全文文本

        Returns:
            (text, source_desc, pdf_bytes)
            source_desc: "OA全文" / "Unpaywall PDF" / "PMC全文" / "仅摘要"
            pdf_bytes: 原始 PDF 字节（仅 PDF 来源时有值，其余为 None）
        """
        # 1. PMC 全文 XML（NCBI 官方 API，对自动化完全开放，优先使用）
        if paper.pubmed_id:
            text = self._try_pmc_fulltext(paper.pubmed_id)
            if text:
                return text, "PMC全文", None

        # 2. Unpaywall（按 DOI 查找 OA PDF，出版商站点可能有反爬）
        if paper.doi:
            pdf_url = self._query_unpaywall(paper.doi)
            if pdf_url:
                text, pdf_bytes = self._try_download_pdf(pdf_url)
                if text:
                    return text, "Unpaywall PDF", pdf_bytes

        # 3. bioRxiv 直链（格式固定，无反爬，成功率高）
        if paper.doi and ("biorxiv" in (paper.journal or "").lower()
                          or "biorxiv" in (getattr(paper, 'open_access_url', '') or "").lower()):
            biorxiv_pdf = f"https://www.biorxiv.org/content/{paper.doi}.full.pdf"
            text, pdf_bytes = self._try_download_pdf(biorxiv_pdf)
            if text:
                return text, "bioRxiv PDF", pdf_bytes

        # 4. OpenAlex OA URL（备用，同样可能受出版商限制）
        oa_url = getattr(paper, 'open_access_url', None)
        if oa_url:
            text, pdf_bytes = self._try_download_pdf(oa_url)
            if text:
                return text, "OA全文", pdf_bytes

        # 4. 降级：摘要
        abstract = paper.abstract or ""
        return abstract, "仅摘要", None

    # ------------------------------------------------------------------
    # Unpaywall
    # ------------------------------------------------------------------

    def _query_unpaywall(self, doi: str) -> Optional[str]:
        """查询 Unpaywall，返回 PDF 直链（无则返回 None）"""
        try:
            url = f"https://api.unpaywall.org/v2/{doi}?email={UNPAYWALL_EMAIL}"
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                return None
            data = resp.json()
            best_oa = data.get('best_oa_location') or {}
            pdf_url = best_oa.get('url_for_pdf')
            if pdf_url:
                logger.info(f"Unpaywall 找到 PDF: {pdf_url[:80]}")
            return pdf_url
        except Exception as e:
            logger.warning(f"Unpaywall 查询失败: {e}")
            return None

    # ------------------------------------------------------------------
    # PDF 下载与文本提取
    # ------------------------------------------------------------------

    def _try_download_pdf(self, pdf_url: str) -> Tuple[Optional[str], Optional[bytes]]:
        """
        下载 PDF，提取纯文本，同时保留原始字节。
        Returns: (text, pdf_bytes)，失败返回 (None, None)
        """
        try:
            import fitz  # PyMuPDF
        except ImportError:
            logger.warning("PyMuPDF 未安装，跳过 PDF 解析。安装: pip install PyMuPDF")
            return None, None

        try:
            resp = requests.get(
                pdf_url,
                timeout=15,  # 出版商站点经常超时，快速失败避免阻塞
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/124.0.0.0 Safari/537.36",
                    "Accept": "application/pdf,*/*",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": "https://pubmed.ncbi.nlm.nih.gov/",
                }
            )
            if resp.status_code != 200:
                return None, None

            pdf_bytes = resp.content  # 保留原始字节用于附件

            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
                f.write(pdf_bytes)
                tmp_path = f.name

            text = self._extract_text_from_pdf(tmp_path)
            Path(tmp_path).unlink(missing_ok=True)

            if len(text) < 200:
                return None, None

            logger.info(f"PDF 提取成功，字符数: {len(text)}，字节数: {len(pdf_bytes)}")
            return text, pdf_bytes

        except Exception as e:
            logger.warning(f"PDF 下载/解析失败: {e}")
            return None, None

    def _extract_text_from_pdf(self, pdf_path: str) -> str:
        """
        从 PDF 提取纯文本，截断参考文献。
        优先用 pdfplumber（双栏论文文本顺序更准确），
        失败时回退到 PyMuPDF。
        """
        full_text = ""

        # 优先：pdfplumber（处理双栏排版更好）
        try:
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                pages_text = []
                for page in pdf.pages:
                    t = page.extract_text()
                    if t:
                        pages_text.append(t)
            full_text = "\n".join(pages_text)
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"pdfplumber 提取失败，尝试 PyMuPDF: {e}")

        # 回退：PyMuPDF
        if not full_text:
            try:
                import fitz
                doc = fitz.open(pdf_path)
                full_text = "\n".join(page.get_text() for page in doc)
                doc.close()
            except ImportError:
                logger.warning("pdfplumber 和 PyMuPDF 均未安装，请安装其中一个: pip install pdfplumber 或 pip install PyMuPDF")
            except Exception as e:
                logger.warning(f"PyMuPDF 提取失败: {e}")

        # 截断参考文献（只截断后半段出现的 References 标题）
        for marker in ["References\n", "REFERENCES\n", "参考文献\n", "Bibliography\n"]:
            idx = full_text.rfind(marker)
            if idx > len(full_text) * 0.5:
                full_text = full_text[:idx]
                break

        return full_text[:MAX_FULLTEXT_CHARS]

    # ------------------------------------------------------------------
    # PMC 全文 XML
    # ------------------------------------------------------------------

    def _try_pmc_fulltext(self, pubmed_id: str) -> Optional[str]:
        """通过 PMID → PMC ID → 全文 XML 提取文本"""
        try:
            from Bio import Entrez

            handle = Entrez.elink(dbfrom="pubmed", db="pmc", id=pubmed_id)
            record = Entrez.read(handle)
            handle.close()

            pmc_ids = []
            if record and record[0].get("LinkSetDb"):
                links = record[0]["LinkSetDb"][0].get("Link", [])
                pmc_ids = [link["Id"] for link in links]

            if not pmc_ids:
                logger.debug(f"PMID {pubmed_id} 无对应 PMC 全文")
                return None

            pmc_id = pmc_ids[0]
            logger.info(f"找到 PMC ID: {pmc_id}，正在获取全文 XML")

            handle = Entrez.efetch(db="pmc", id=pmc_id, rettype="xml", retmode="xml")
            xml_content = handle.read()
            handle.close()

            return self._parse_pmc_xml(xml_content)

        except Exception as e:
            logger.warning(f"PMC 全文获取失败: {e}")
            return None

    def _parse_pmc_xml(self, xml_content: bytes) -> Optional[str]:
        """解析 PMC XML，拼接正文段落"""
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(xml_content)

            paragraphs = []
            for p in root.iter('p'):
                text = ''.join(p.itertext()).strip()
                if len(text) > 30:
                    paragraphs.append(text)

            full_text = "\n\n".join(paragraphs)
            if len(full_text) < 200:
                return None

            logger.info(f"PMC XML 解析成功，段落数: {len(paragraphs)}")
            return full_text[:MAX_FULLTEXT_CHARS]

        except Exception as e:
            logger.warning(f"PMC XML 解析失败: {e}")
            return None
