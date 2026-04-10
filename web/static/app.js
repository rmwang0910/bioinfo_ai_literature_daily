/* ===== BioLit Daily — Frontend Logic ===== */
"use strict";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const state = {
  sessionId: null,
  config: {},
  papers: [],
  paperSummaries: {},   // paper_id -> summary text
  overallSummary: null,
  searching: false,
  analyzing: false,
  activeTab: "papers",
  selectedPapers: new Set(),  // set of indices
  savedPapers: [],            // from server
  searchStartTime: null,      // for timing
  timings: {}                 // step timing info
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function el(id) { return document.getElementById(id); }

function escapeHtml(s) {
  return String(s || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function truncate(s, n) {
  s = String(s || "");
  return s.length > n ? s.slice(0, n) + "..." : s;
}

function formatDate(d) {
  if (!d) return "";
  const dt = new Date(d);
  if (isNaN(dt.getTime())) return String(d).slice(0, 10);
  return dt.toISOString().slice(0, 10);
}

function sourceBadgeClass(source) {
  const s = String(source || "").toLowerCase();
  if (s.includes("pubmed")) return "source-badge--pubmed";
  if (s.includes("arxiv") && !s.includes("bio")) return "source-badge--arxiv";
  if (s.includes("biorxiv")) return "source-badge--biorxiv";
  return "source-badge--unknown";
}

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------
async function apiJson(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(json?.error || `HTTP ${res.status}`);
  return json;
}

async function apiStreamNdjson(path, body, onEvent) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!res.ok) {
    const errJson = await res.json().catch(() => ({}));
    throw new Error(errJson?.error || `HTTP ${res.status}`);
  }
  if (!res.body) throw new Error("Streaming body unavailable");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    while (true) {
      const idx = buffer.indexOf("\n");
      if (idx < 0) break;
      const line = buffer.slice(0, idx).trim();
      buffer = buffer.slice(idx + 1);
      if (!line) continue;
      try {
        const obj = JSON.parse(line);
        if (obj && typeof onEvent === "function") onEvent(obj);
      } catch (_) { /* skip invalid */ }
    }
  }
  // tail
  buffer += decoder.decode();
  const tail = buffer.trim();
  if (tail) {
    try { onEvent(JSON.parse(tail)); } catch (_) {}
  }
}

// ---------------------------------------------------------------------------
// Session
// ---------------------------------------------------------------------------
async function createSession() {
  try {
    const data = await apiJson("/api/session/create", {});
    state.sessionId = data.session_id;
    state.config = data.config || {};
    populateConfigForm(state.config);
    el("statusBadge").textContent = "connected";
    el("statusBadge").className = "badge badge--ok";
  } catch (e) {
    el("statusBadge").textContent = "disconnected";
    el("statusBadge").className = "badge badge--err";
    console.error("Session create failed:", e);
  }
}

// ---------------------------------------------------------------------------
// Config form <-> state
// ---------------------------------------------------------------------------
function populateConfigForm(cfg) {
  const s = cfg.search || {};
  const f = cfg.filter || {};
  const r = cfg.report || {};
  const e = cfg.email || {};

  const kws = s.keywords;
  if (Array.isArray(kws)) el("cfgKeywords").value = kws.join(", ");
  else if (kws) el("cfgKeywords").value = String(kws);

  // Time range: decide mode based on what's set
  const hasDateRange = s.min_date && s.max_date;
  if (hasDateRange) {
    setTimeMode("range");
    el("cfgMinDate").value = s.min_date;
    el("cfgMaxDate").value = s.max_date;
    el("cfgDaysBack").value = "";
    el("cfgOperator2").value = s.keyword_operator || "AND";
  } else {
    setTimeMode("days");
    el("cfgDaysBack").value = s.days_back || 7;
    el("cfgMinDate").value = "";
    el("cfgMaxDate").value = "";
  }

  el("cfgOperator").value = s.keyword_operator || "AND";
  el("cfgMaxResults").value = s.max_results_per_keyword || 20;
  el("cfgMaxPapers").value = r.max_papers || 50;
  el("cfgUnifiedSearch").checked = !!s.use_unified_search;
  el("cfgStrictValidation").checked = s.strict_keyword_validation !== false;
  el("cfgStrictness").value = s.validation_strictness || "strict";
  el("cfgMinIF").value = f.min_impact_factor || "";
  el("cfgMaxIF").value = f.max_impact_factor || "";
  el("cfgFields").value = (f.allowed_fields || []).join(", ");
  el("cfgJournals").value = (f.allowed_journals || []).join(", ");
  el("cfgEmail").value = e.to_email || "";
}

function setTimeMode(mode) {
  el("cfgTimeMode").value = mode;
  el("timeDaysGroup").style.display = mode === "days" ? "" : "none";
  el("timeRangeGroup").style.display = mode === "range" ? "" : "none";
  el("timeRangeOperatorGroup").style.display = mode === "range" ? "" : "none";
}

function collectConfigPatch() {
  const parseList = (v) => v ? v.split(",").map(x => x.trim()).filter(Boolean) : [];
  const parseNum = (v) => { const n = parseFloat(v); return isNaN(n) ? null : n; };

  const timeMode = el("cfgTimeMode").value;
  const operator = timeMode === "range" ? el("cfgOperator2").value : el("cfgOperator").value;

  const searchPatch = {
    keywords: parseList(el("cfgKeywords").value),
    keyword_operator: operator,
    max_results_per_keyword: parseInt(el("cfgMaxResults").value) || 20,
    use_unified_search: el("cfgUnifiedSearch").checked,
    strict_keyword_validation: el("cfgStrictValidation").checked,
    validation_strictness: el("cfgStrictness").value,
  };

  if (timeMode === "range") {
    searchPatch.min_date = el("cfgMinDate").value || null;
    searchPatch.max_date = el("cfgMaxDate").value || null;
    searchPatch.days_back = null;  // clear days_back when using date range
  } else {
    searchPatch.days_back = parseInt(el("cfgDaysBack").value) || 7;
    searchPatch.min_date = null;
    searchPatch.max_date = null;
  }

  return {
    search: searchPatch,
    filter: {
      min_impact_factor: parseNum(el("cfgMinIF").value),
      max_impact_factor: parseNum(el("cfgMaxIF").value),
      allowed_fields: parseList(el("cfgFields").value),
      allowed_journals: parseList(el("cfgJournals").value),
    },
    report: {
      max_papers: parseInt(el("cfgMaxPapers").value) || 50,
    },
    email: {
      to_email: el("cfgEmail").value.trim(),
    },
  };
}

async function applyConfig() {
  if (!state.sessionId) return;
  const patch = collectConfigPatch();
  try {
    const data = await apiJson("/api/config/update", { session_id: state.sessionId, config: patch });
    state.config = data.config || state.config;
    addProgress("配置已更新", "ok");
  } catch (e) {
    addProgress("配置更新失败: " + e.message, "err");
  }
}

// ---------------------------------------------------------------------------
// Progress
// ---------------------------------------------------------------------------
function showProgress() {
  el("progressCard").style.display = "";
  el("progressArea").innerHTML = "";
}

function addProgress(msg, type) {
  el("progressCard").style.display = "";
  const cls = type === "ok" ? "progress-area__line--ok" : type === "err" ? "progress-area__line--err" : "";
  el("progressArea").innerHTML += `<div class="progress-area__line ${cls}">${escapeHtml(msg)}</div>`;
  const area = el("progressArea");
  area.scrollTop = area.scrollHeight;
}

function setProgressBar(pct, indeterminate) {
  const wrap = el("progressBarWrap");
  const fill = el("progressBarFill");
  wrap.style.display = "";
  if (indeterminate) {
    fill.className = "progress-bar__fill progress-bar__fill--indeterminate";
    fill.style.width = "";
  } else {
    fill.className = "progress-bar__fill";
    fill.style.width = Math.min(100, Math.max(0, pct)) + "%";
  }
}

function hideProgressBar() {
  el("progressBarWrap").style.display = "none";
  el("progressBarFill").className = "progress-bar__fill";
  el("progressBarFill").style.width = "0%";
}

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------
function switchTab(tab) {
  state.activeTab = tab;
  document.querySelectorAll(".tab").forEach(t => {
    t.classList.toggle("tab--active", t.dataset.tab === tab);
  });
  document.querySelectorAll(".tab-content").forEach(tc => {
    tc.classList.toggle("tab-content--active", tc.id === tab + "Tab");
  });
  if (tab === "saved") loadSavedPapers();
}

// ---------------------------------------------------------------------------
// Parse (step 1: NL → config)
// ---------------------------------------------------------------------------
async function runParse() {
  const userInput = el("nlInput").value.trim();
  if (!userInput || !state.sessionId) return;

  el("parseBtn").disabled = true;
  el("parseBtn").innerHTML = '<span class="spinner"></span>解析中...';
  showProgress();

  try {
    const data = await apiJson("/api/parse", {
      session_id: state.sessionId,
      user_input: userInput,
    });

    if (data.config) {
      state.config = data.config;
      populateConfigForm(state.config);
    }

    // Auto-expand advanced params so user can review
    el("advancedDetails").open = true;

    addProgress("需求解析完成，请确认参数后点击「开始搜索」", "ok");
  } catch (e) {
    addProgress("解析失败: " + e.message, "err");
  } finally {
    el("parseBtn").disabled = false;
    el("parseBtn").textContent = "解析需求";
  }
}

// ---------------------------------------------------------------------------
// Search (step 2: use current config to search)
// ---------------------------------------------------------------------------
async function runSearch() {
  if (state.searching || !state.sessionId) return;

  // Apply form values to session config first
  try {
    const patch = collectConfigPatch();
    const cfgRes = await apiJson("/api/config/update", { session_id: state.sessionId, config: patch });
    state.config = cfgRes.config || state.config;
  } catch (e) {
    addProgress("配置同步失败: " + e.message, "err");
    return;
  }

  state.searching = true;
  state.papers = [];
  state.paperSummaries = {};
  state.overallSummary = null;
  state.selectedPapers.clear();
  state.searchStartTime = Date.now();
  state.timings = {};

  el("searchBtn").disabled = true;
  el("searchBtn").innerHTML = '<span class="spinner"></span>搜索中...';
  showProgress();
  setProgressBar(0, true);
  switchTab("papers");
  el("papersList").innerHTML = '<div class="empty-state"><span class="spinner"></span> 正在搜索...</div>';
  el("summaryContent").innerHTML = '<div class="empty-state">等待搜索完成...</div>';
  el("papersCount").textContent = "0";

  try {
    await apiStreamNdjson("/api/search", {
      session_id: state.sessionId,
    }, (ev) => {
      const t = String(ev.type || "");

      if (t === "status") {
        const step = ev.step || "";
        const msg = ev.message || ev.step;
        const progress = ev.progress || null;

        // 根据阶段显示不同进度
        if (step === "searching") {
          // 搜索阶段：0-40%
          setProgressBar(progress ? (progress.current / progress.total) * 40 : 20, false);
        } else if (step === "summarizing") {
          // 综述阶段：40-50%
          setProgressBar(45, false);
        } else if (step === "paper_summaries") {
          // 单篇总结阶段：50-100%
          if (progress && progress.total > 0) {
            const pct = 50 + (progress.current / progress.total) * 50;
            setProgressBar(pct, false);
          } else {
            setProgressBar(75, false);
          }
        } else if (step === "paper_summaries_done") {
          setProgressBar(90, false);
          addProgress(msg, "ok");
        } else if (step === "translating") {
          if (progress && progress.total > 0) {
            const pct = 90 + (progress.current / progress.total) * 10;
            setProgressBar(pct, false);
          }
          addProgress(msg);
        } else {
          // 未知阶段：不定状态
          setProgressBar(0, true);
        }

        // 普通 status 消息才添加（paper_summaries_done 已在上处理）
        if (step !== "paper_summaries_done") {
          addProgress(msg);
        }
      }

      if (t === "papers") {
        state.papers = ev.data || [];
        addProgress(`找到 ${ev.total || 0} 篇文献`, "ok");
        setProgressBar(50, false);
        renderPapers();
      }

      if (t === "summary") {
        state.overallSummary = ev.data;
        addProgress("文献综述生成完成", "ok");
        renderSummary();
      }

      if (t === "paper_summary") {
        const key = ev.paper_id || String(ev.paper_index);
        state.paperSummaries[key] = ev.summary;
        updatePaperSummary(ev.paper_index, ev.summary);
      }

      if (t === "paper_abstract_translated") {
        const idx = ev.paper_index;
        const absEl = document.getElementById(`abstract-${idx}`);
        if (absEl && ev.translated) {
          // 在英文摘要下方追加中文翻译
          let transEl = document.getElementById(`abstract-translated-${idx}`);
          if (!transEl) {
            transEl = document.createElement("div");
            transEl.id = `abstract-translated-${idx}`;
            transEl.className = "paper-card__abstract";
            transEl.style.cssText = "margin-top:6px;padding-top:6px;border-top:1px dashed var(--border);color:var(--text);";
            absEl.parentNode.insertBefore(transEl, absEl.nextSibling);
          }
          transEl.innerHTML = `<strong style="color:var(--text-muted);font-size:11px;">中文翻译:</strong> ${escapeHtml(ev.translated)}`;
        }
      }

      if (t === "done") {
        const totalElapsed = ((Date.now() - state.searchStartTime) / 1000).toFixed(1);
        addProgress(`搜索完成，共 ${ev.total_papers || 0} 篇文献，总耗时：${totalElapsed} 秒`, "ok");

        // 显示各阶段耗时统计
        if (state.timings && Object.keys(state.timings).length > 0) {
          let timingSummary = "耗时统计：";
          const parts = [];
          for (const [step, info] of Object.entries(state.timings)) {
            if (info.elapsed_sec) {
              parts.push(`${step}: ${info.elapsed_sec.toFixed(1)}s`);
            }
          }
          if (parts.length > 0) {
            addProgress(timingSummary + " | " + parts.join(" | "), "ok");
          }
        }

        setProgressBar(100, false);
      }

      // Handle timing events
      if (ev.timing) {
        const t = ev.timing;
        if (t.step) {
          state.timings[t.step] = t;
        }
      }

      if (t === "error") {
        addProgress("错误: " + ev.message, "err");
      }
    });
  } catch (e) {
    addProgress("请求失败: " + e.message, "err");
  } finally {
    state.searching = false;
    el("searchBtn").disabled = false;
    el("searchBtn").textContent = "开始搜索";
    hideProgressBar();
  }
}

// ---------------------------------------------------------------------------
// Render papers
// ---------------------------------------------------------------------------
function renderPapers() {
  const list = el("papersList");
  const toolbar = el("papersToolbar");
  if (!state.papers.length) {
    list.innerHTML = '<div class="empty-state">未找到文献</div>';
    el("papersCount").textContent = "0";
    toolbar.style.display = "none";
    return;
  }

  el("papersCount").textContent = String(state.papers.length);
  toolbar.style.display = "";
  updateSelectionUI();

  const html = state.papers.map((p, i) => {
    const checked = state.selectedPapers.has(i) ? "checked" : "";
    const selectedCls = state.selectedPapers.has(i) ? " paper-card--selected" : "";
    const title = escapeHtml(p.title || "Untitled");
    const authorsList = (p.authors || []).slice(0, 5);
    const authors = authorsList.map(a => escapeHtml(typeof a === "string" ? a : a.name || "")).join(", ");
    const hasMore = (p.authors || []).length > 5;
    // 机构信息（从前两位作者的 affiliation 中提取）
    const institutions = authorsList
      .map(a => (typeof a === "object" && a.affiliation) ? a.affiliation : "")
      .filter(v => v)
      .slice(0, 2);
    const journal = escapeHtml(p.journal || p.venue || "");
    // 优先使用完整日期，fallback 到年份
    const pubDate = p.publication_date ? formatDate(p.publication_date) : "";
    const year = pubDate || (p.year ? String(p.year) : "");
    const source = String(p.source || "unknown").toLowerCase();
    const badgeCls = sourceBadgeClass(source);
    const abstract = escapeHtml(truncate(p.abstract, 300));
    const doi = p.doi ? escapeHtml(p.doi) : "";
    const citations = p.citation_count || 0;
    const pmid = p.pubmed_id || "";
    const paperKey = p.doi || p.title || String(i);
    const summary = state.paperSummaries[paperKey] || "";
    const isOA = p.is_open_access;
    const pdfUrl = p.pdf_url || p.open_access_url || "";

    // Build link
    let link = "#";
    if (p.doi) link = `https://doi.org/${encodeURIComponent(p.doi)}`;
    else if (p.url) link = escapeHtml(p.url);
    else if (pmid) link = `https://pubmed.ncbi.nlm.nih.gov/${pmid}/`;

    return `
      <div class="paper-card${selectedCls}" data-index="${i}">
        <div class="paper-card__header">
          <input type="checkbox" class="paper-card__checkbox" data-select-index="${i}" ${checked}>
          <div class="paper-card__title">
            <a href="${link}" target="_blank" rel="noopener">${title}</a>
            ${isOA ? `<span style="display:inline-block;background:#27ae60;color:#fff;font-size:10px;padding:1px 6px;border-radius:8px;margin-left:6px;vertical-align:middle;">OA</span>` : ""}
          </div>
        </div>
        <div class="paper-card__meta">
          <span class="source-badge ${badgeCls}">${escapeHtml(source)}</span>
          ${journal ? `<span class="paper-card__meta-item">${journal}</span>` : ""}
          ${p.impact_factor != null ? `<span class="paper-card__meta-item" style="color:#e67e22;font-weight:600;">IF: ${p.impact_factor}</span>` : ""}
          ${year ? `<span class="paper-card__meta-item">${year}</span>` : ""}
          ${citations ? `<span class="paper-card__meta-item">Cited: ${citations}</span>` : ""}
          ${doi ? `<span class="paper-card__meta-item" style="font-family:var(--mono);font-size:11px;">${doi}</span>` : ""}
        </div>
        <div class="paper-card__meta" style="color:var(--text-muted);">
          ${authors}${hasMore ? " et al." : ""}
          ${institutions.length ? `<span style="margin-left:8px;font-size:11px;color:var(--text-muted);">${escapeHtml(institutions.join("; "))}</span>` : ""}
        </div>
        ${abstract ? `<div class="paper-card__abstract" id="abstract-${i}">${abstract}</div>` : ""}
        ${summary ? `<div class="paper-card__summary" id="summary-${i}">${escapeHtml(summary)}</div>` : `<div class="paper-card__summary" id="summary-${i}" style="display:none;"></div>`}
        <div class="paper-card__actions">
          <button class="btn btn--small btn--ghost" data-action="analyze" data-query="${doi || pmid || escapeHtml(truncate(p.title, 100))}">深度分析</button>
          ${pdfUrl ? `<button class="btn btn--small btn--ghost" onclick="window.open('${escapeHtml(pdfUrl)}','_blank')">PDF</button>` : ""}
          ${doi ? `<button class="btn btn--small btn--ghost" onclick="window.open('https://doi.org/${encodeURIComponent(p.doi)}','_blank')">DOI</button>` : ""}
          ${pmid ? `<button class="btn btn--small btn--ghost" onclick="window.open('https://pubmed.ncbi.nlm.nih.gov/${pmid}/','_blank')">PubMed</button>` : ""}
        </div>
      </div>
    `;
  }).join("");

  list.innerHTML = html;
}

// ---------------------------------------------------------------------------
// Paper selection
// ---------------------------------------------------------------------------
function togglePaperSelect(index) {
  if (state.selectedPapers.has(index)) {
    state.selectedPapers.delete(index);
  } else {
    state.selectedPapers.add(index);
  }
  // Update card visual without full re-render
  const card = document.querySelector(`.paper-card[data-index="${index}"]`);
  if (card) {
    card.classList.toggle("paper-card--selected", state.selectedPapers.has(index));
    const cb = card.querySelector(".paper-card__checkbox");
    if (cb) cb.checked = state.selectedPapers.has(index);
  }
  updateSelectionUI();
}

function toggleSelectAll() {
  const allSelected = state.selectedPapers.size === state.papers.length;
  state.selectedPapers.clear();
  if (!allSelected) {
    for (let i = 0; i < state.papers.length; i++) state.selectedPapers.add(i);
  }
  // Update all checkboxes
  document.querySelectorAll(".paper-card__checkbox").forEach(cb => {
    const idx = parseInt(cb.dataset.selectIndex, 10);
    cb.checked = state.selectedPapers.has(idx);
    const card = cb.closest(".paper-card");
    if (card) card.classList.toggle("paper-card--selected", cb.checked);
  });
  updateSelectionUI();
}

function updateSelectionUI() {
  const count = state.selectedPapers.size;
  const total = state.papers.length;
  el("selectedCount").textContent = `已选 ${count} 篇`;
  el("selectAllCheckbox").checked = total > 0 && count === total;
  el("selectAllCheckbox").indeterminate = count > 0 && count < total;
  el("selectAllLabel").textContent = (count === total && total > 0) ? "取消全选" : "全选";
  el("exportSelectedBtn").disabled = count === 0;
  el("saveSelectedBtn").disabled = count === 0;
  el("emailSelectedBtn").disabled = count === 0;
}

function updatePaperSummary(index, summary) {
  const el_summary = document.getElementById(`summary-${index}`);
  if (el_summary) {
    el_summary.style.display = "";
    el_summary.textContent = summary;
  }
}

// ---------------------------------------------------------------------------
// Render summary
// ---------------------------------------------------------------------------
function renderSummary() {
  const c = el("summaryContent");
  if (!state.overallSummary) {
    c.innerHTML = '<div class="empty-state">暂无综述</div>';
    return;
  }
  // Simple markdown-ish rendering (bold, headers, lists)
  let html = escapeHtml(state.overallSummary);
  // Headers
  html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
  html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');
  // Bold
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  // Lists
  html = html.replace(/^- (.+)$/gm, '<li>$1</li>');
  // Paragraphs
  html = html.replace(/\n\n/g, '</p><p>');
  html = '<p>' + html + '</p>';

  c.innerHTML = html;
}

// ---------------------------------------------------------------------------
// Single paper analysis
// ---------------------------------------------------------------------------
async function runAnalyze(paperQuery) {
  if (state.analyzing || !state.sessionId) return;

  state.analyzing = true;
  switchTab("analysis");

  const c = el("analysisContent");
  c.innerHTML = '<div class="empty-state"><span class="spinner"></span> 正在分析文献...</div>';

  showProgress();
  addProgress("开始深度分析: " + truncate(paperQuery, 60));
  setProgressBar(0, true);

  try {
    await apiStreamNdjson("/api/analyze", {
      session_id: state.sessionId,
      query: paperQuery,
    }, (ev) => {
      const t = String(ev.type || "");

      if (t === "status") {
        addProgress(ev.message || ev.step);
      }

      if (t === "paper_meta") {
        const p = ev.data || {};
        c.innerHTML = `
          <div class="analysis-meta">
            <strong>${escapeHtml(p.title || "")}</strong>
            <span style="color:var(--text-secondary);">${escapeHtml((p.authors || []).slice(0, 5).map(a => typeof a === "string" ? a : a.name).join(", "))}</span>
            <span style="color:var(--text-muted);">${escapeHtml(p.journal || "")} ${p.year || ""}</span>
          </div>
          <div id="analysisBody"><div class="empty-state"><span class="spinner"></span> LLM 正在分析...</div></div>
        `;
      }

      if (t === "analysis") {
        renderAnalysisResult(ev.data, ev.source, ev.paper);
        addProgress("分析完成", "ok");
      }

      if (t === "done") {
        setProgressBar(100, false);
      }

      if (t === "error") {
        c.innerHTML = `<div class="empty-state" style="color:var(--err);">分析失败: ${escapeHtml(ev.message)}</div>`;
        addProgress("分析失败: " + ev.message, "err");
      }
    });
  } catch (e) {
    c.innerHTML = `<div class="empty-state" style="color:var(--err);">请求失败: ${escapeHtml(e.message)}</div>`;
    addProgress("请求失败: " + e.message, "err");
  } finally {
    state.analyzing = false;
    hideProgressBar();
  }
}

function renderAnalysisResult(analysis, source, paper) {
  const body = document.getElementById("analysisBody") || el("analysisContent");

  if (!analysis || typeof analysis !== "object") {
    body.innerHTML = `<div class="analysis-section"><div class="analysis-section__body">${escapeHtml(String(analysis || "无分析结果"))}</div></div>`;
    return;
  }

  // analysis is a dict like {"研究背景": "...", "主要发现": [...], ...}
  const sections = Object.entries(analysis).map(([key, val]) => {
    let content;
    if (Array.isArray(val)) {
      content = val.map(v => {
        if (typeof v === "object" && v !== null) {
          // 对象元素：渲染为 key-value 块（术语解释、图表解读等）
          const entries = Object.entries(v);
          const first = entries[0] ? `<strong>${escapeHtml(String(entries[0][1]))}</strong>` : "";
          const rest = entries.slice(1).map(([k2, v2]) => escapeHtml(String(v2 || ""))).join(" — ");
          return `<li>${first}${rest ? "：" + rest : ""}</li>`;
        }
        return `<li>${escapeHtml(String(v))}</li>`;
      }).join("");
      content = `<ul>${content}</ul>`;
    } else if (typeof val === "object" && val !== null) {
      content = Object.entries(val).map(([k, v]) => `<div><strong>${escapeHtml(k)}:</strong> ${escapeHtml(String(v))}</div>`).join("");
    } else {
      content = escapeHtml(String(val || ""));
      // Basic markdown bold
      content = content.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
      content = content.replace(/\n/g, '<br>');
    }

    return `
      <div class="analysis-section">
        <div class="analysis-section__title">${escapeHtml(key)}</div>
        <div class="analysis-section__body">${content}</div>
      </div>
    `;
  }).join("");

  const sourceHtml = source ? `<div style="font-size:12px;color:var(--text-muted);margin-bottom:12px;">分析来源: ${escapeHtml(source)}</div>` : "";

  body.innerHTML = sourceHtml + sections;
}

// ---------------------------------------------------------------------------
// Send email
// ---------------------------------------------------------------------------
async function sendEmail() {
  if (!state.sessionId || !state.papers.length) return;
  const toEmail = el("cfgEmail").value.trim();
  const indices = state.selectedPapers.size > 0
    ? Array.from(state.selectedPapers).sort((a, b) => a - b)
    : null;

  if (indices && indices.length === 0) {
    addProgress("请先勾选要发送的文献", "err");
    return;
  }

  const count = indices ? indices.length : state.papers.length;
  el("emailSelectedBtn").disabled = true;
  el("emailSelectedBtn").innerHTML = '<span class="spinner"></span>发送中...';
  addProgress(`正在发送 ${count} 篇文献的邮件...`);

  try {
    const data = await apiJson("/api/email/send", {
      session_id: state.sessionId,
      to_email: toEmail || undefined,
      indices,
    });
    addProgress(data.message || "邮件已发送", "ok");
  } catch (e) {
    addProgress("邮件发送失败: " + e.message, "err");
  } finally {
    el("emailSelectedBtn").disabled = false;
    el("emailSelectedBtn").textContent = "发送邮件";
  }
}

// ---------------------------------------------------------------------------
// Saved papers
// ---------------------------------------------------------------------------
async function loadSavedPapers() {
  try {
    const data = await apiJson("/api/saved/list", {});
    state.savedPapers = data.papers || [];
    el("savedCount").textContent = String(state.savedPapers.length);
    if (state.activeTab === "saved") renderSavedPapers();
  } catch (_) {}
}

async function saveSelectedPapers() {
  if (!state.papers.length || state.selectedPapers.size === 0) return;
  const items = Array.from(state.selectedPapers)
    .sort((a, b) => a - b)
    .map(i => state.papers[i])
    .filter(Boolean);
  if (!items.length) return;

  try {
    const data = await apiJson("/api/saved/add", { papers: items });
    addProgress(`已存档 ${data.added} 篇文献（共 ${data.total} 篇）`, "ok");
    await loadSavedPapers();
  } catch (e) {
    addProgress("存档失败: " + e.message, "err");
  }
}

async function deleteSavedPaper(id) {
  try {
    const data = await apiJson("/api/saved/delete", { ids: [id] });
    addProgress(`已删除 ${data.deleted} 篇存档`, "ok");
    await loadSavedPapers();
  } catch (e) {
    addProgress("删除失败: " + e.message, "err");
  }
}

function renderSavedPapers() {
  const list = el("savedList");
  if (!state.savedPapers.length) {
    list.innerHTML = '<div class="empty-state">暂无存档文献</div>';
    el("savedCount").textContent = "0";
    return;
  }
  el("savedCount").textContent = String(state.savedPapers.length);

  const html = state.savedPapers.map((p, i) => {
    const title = escapeHtml(p.title || "Untitled");
    const authors = (p.authors || []).slice(0, 3).map(a => escapeHtml(typeof a === "string" ? a : a.name || "")).join(", ");
    const hasMore = (p.authors || []).length > 3;
    const journal = escapeHtml(p.journal || p.venue || "");
    const year = p.year || "";
    const source = String(p.source || "unknown").toLowerCase();
    const badgeCls = sourceBadgeClass(source);
    const doi = p.doi ? escapeHtml(p.doi) : "";
    const savedAt = p.saved_at ? p.saved_at.slice(0, 10) : "";
    const deleteId = p.doi || p.title || "";

    let link = "#";
    if (p.doi) link = `https://doi.org/${encodeURIComponent(p.doi)}`;
    else if (p.url) link = escapeHtml(p.url);
    else if (p.pubmed_id) link = `https://pubmed.ncbi.nlm.nih.gov/${p.pubmed_id}/`;

    return `
      <div class="paper-card">
        <div class="paper-card__header">
          <div class="paper-card__title" style="flex:1;">
            <a href="${link}" target="_blank" rel="noopener">${title}</a>
          </div>
        </div>
        <div class="paper-card__meta">
          <span class="source-badge ${badgeCls}">${escapeHtml(source)}</span>
          ${journal ? `<span class="paper-card__meta-item">${journal}</span>` : ""}
          ${year ? `<span class="paper-card__meta-item">${year}</span>` : ""}
          ${doi ? `<span class="paper-card__meta-item" style="font-family:var(--mono);font-size:11px;">${doi}</span>` : ""}
          ${savedAt ? `<span class="paper-card__meta-item">存档于 ${savedAt}</span>` : ""}
        </div>
        <div class="paper-card__meta" style="color:var(--text-muted);">
          ${authors}${hasMore ? " et al." : ""}
        </div>
        <div class="paper-card__actions">
          <button class="btn btn--small btn--danger" data-action="delete-saved" data-delete-id="${escapeHtml(deleteId)}">删除</button>
          <button class="btn btn--small btn--ghost" data-action="analyze" data-query="${doi || escapeHtml(truncate(p.title, 100))}">深度分析</button>
        </div>
      </div>
    `;
  }).join("");

  list.innerHTML = html;
}

// ---------------------------------------------------------------------------
// Export RIS
// ---------------------------------------------------------------------------
async function exportRIS() {
  if (!state.sessionId || !state.papers.length) return;
  const indices = state.selectedPapers.size > 0
    ? Array.from(state.selectedPapers).sort((a, b) => a - b)
    : null;  // null = all papers

  if (indices && indices.length === 0) {
    addProgress("请先勾选要导出的文献", "err");
    return;
  }

  addProgress(`正在导出 ${indices ? indices.length : state.papers.length} 篇文献的 RIS...`);

  try {
    const res = await fetch("/api/export/ris", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.sessionId, indices }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${res.status}`);
    }

    // Get filename from Content-Disposition header or use default
    const disposition = res.headers.get("Content-Disposition") || "";
    const fnameMatch = disposition.match(/filename="?([^"]+)"?/);
    const filename = fnameMatch ? fnameMatch[1] : "literature.ris";

    // Download as blob
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    addProgress(`RIS 文件已下载: ${filename}`, "ok");
  } catch (e) {
    addProgress("RIS 导出失败: " + e.message, "err");
  }
}

// ---------------------------------------------------------------------------
// Event binding
// ---------------------------------------------------------------------------
function bind() {
  // Parse (step 1)
  el("parseBtn").addEventListener("click", runParse);
  el("nlInput").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      runParse();
    }
  });

  // Time mode toggle
  el("cfgTimeMode").addEventListener("change", () => setTimeMode(el("cfgTimeMode").value));

  // Search (step 2)
  el("searchBtn").addEventListener("click", runSearch);


  // Tabs
  document.querySelectorAll(".tab").forEach(tab => {
    tab.addEventListener("click", () => switchTab(tab.dataset.tab));
  });

  // Paper card actions (event delegation)
  el("papersList").addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-action]");
    if (!btn) return;
    const action = btn.getAttribute("data-action");
    if (action === "analyze") {
      const query = btn.getAttribute("data-query");
      if (query) runAnalyze(query);
    }
  });

  // Export RIS
  el("exportSelectedBtn").addEventListener("click", exportRIS);
  el("emailSelectedBtn").addEventListener("click", sendEmail);
  el("saveSelectedBtn").addEventListener("click", saveSelectedPapers);

  // Select all checkbox
  el("selectAllCheckbox").addEventListener("change", toggleSelectAll);

  // Paper checkbox delegation
  el("papersList").addEventListener("change", (ev) => {
    const cb = ev.target.closest("[data-select-index]");
    if (!cb) return;
    const idx = parseInt(cb.dataset.selectIndex, 10);
    if (!isNaN(idx)) togglePaperSelect(idx);
  });

  // Saved list delegation (delete + analyze)
  el("savedList").addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-action]");
    if (!btn) return;
    const action = btn.getAttribute("data-action");
    if (action === "delete-saved") {
      const id = btn.getAttribute("data-delete-id");
      if (id) deleteSavedPaper(id);
    }
    if (action === "analyze") {
      const query = btn.getAttribute("data-query");
      if (query) runAnalyze(query);
    }
  });
}

// ---------------------------------------------------------------------------
// Theme
// ---------------------------------------------------------------------------
function getTheme() {
  return localStorage.getItem("biolit-theme") || "light";
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  el("themeToggle").textContent = theme === "dark" ? "\u2600\uFE0F" : "\uD83C\uDF19";
  el("themeToggle").title = theme === "dark" ? "切换到浅色主题" : "切换到深色主题";
  localStorage.setItem("biolit-theme", theme);
}

function toggleTheme() {
  applyTheme(getTheme() === "dark" ? "light" : "dark");
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
window.addEventListener("load", async () => {
  applyTheme(getTheme());
  el("themeToggle").addEventListener("click", toggleTheme);
  bind();
  await createSession();
  loadSavedPapers();
});
