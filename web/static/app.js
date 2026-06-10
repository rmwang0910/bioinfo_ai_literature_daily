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
  analysisArchives: [],       // from index.json
  savedDateFilter: { range: "all", from: "", to: "" },
  archiveDateFilter: { range: "all", from: "", to: "" },
  savedQuery: "",
  archiveQuery: "",
  selectedSavedPapers: new Set(),
  searchStartTime: null,      // for timing
  timings: {},                // step timing info
  currentAbortController: null  // for cancelling operations
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
  return dateInputValue(dt);
}

function formatDateTime(d) {
  if (!d) return "";
  const dt = new Date(d);
  if (isNaN(dt.getTime())) return String(d).replace("T", " ").slice(0, 16);
  const date = dateInputValue(dt);
  const time = dt.toTimeString().slice(0, 5);
  return `${date} ${time}`;
}

function dateInputValue(date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function getDateFilterBounds(filter) {
  const range = filter.range || "all";
  if (range === "all") return { from: "", to: "" };
  if (range === "custom") return { from: filter.from || "", to: filter.to || "" };

  const days = parseInt(range, 10);
  if (!days) return { from: "", to: "" };
  const to = new Date();
  const from = new Date();
  from.setDate(to.getDate() - days + 1);
  return { from: dateInputValue(from), to: dateInputValue(to) };
}

function isWithinDateFilter(value, filter) {
  if (!value) return (filter.range || "all") === "all";
  const itemDate = formatDate(value);
  const { from, to } = getDateFilterBounds(filter);
  if (from && itemDate < from) return false;
  if (to && itemDate > to) return false;
  return true;
}

function timestampOf(value) {
  const t = new Date(value || 0).getTime();
  return isNaN(t) ? 0 : t;
}

function monthKeyOf(value) {
  if (!value) return "unknown";
  const dt = new Date(value);
  if (isNaN(dt.getTime())) {
    const raw = String(value);
    return raw.length >= 7 ? raw.slice(0, 7) : "unknown";
  }
  return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}`;
}

function formatMonthLabel(key) {
  if (!key || key === "unknown") return "时间未知";
  const [year, month] = key.split("-");
  return `${year}年${month}月`;
}

function groupByMonth(items, dateGetter) {
  const grouped = {};
  for (const item of items) {
    const key = monthKeyOf(dateGetter(item));
    if (!grouped[key]) grouped[key] = [];
    grouped[key].push(item);
  }
  return Object.entries(grouped).sort(([a], [b]) => {
    if (a === "unknown") return 1;
    if (b === "unknown") return -1;
    return b.localeCompare(a);
  });
}

function sourceBadgeClass(source) {
  const s = String(source || "").toLowerCase();
  if (s.includes("pubmed")) return "source-badge--pubmed";
  if (s.includes("arxiv") && !s.includes("bio")) return "source-badge--arxiv";
  if (s.includes("biorxiv")) return "source-badge--biorxiv";
  return "source-badge--unknown";
}

const TOPIC_COLOR_PALETTE = [
  { name: "蓝", fg: "#2563eb", bg: "rgba(37,99,235,0.12)", border: "rgba(37,99,235,0.30)" },
  { name: "绿", fg: "#16a34a", bg: "rgba(22,163,74,0.12)", border: "rgba(22,163,74,0.30)" },
  { name: "橙", fg: "#d97706", bg: "rgba(217,119,6,0.13)", border: "rgba(217,119,6,0.32)" },
  { name: "红", fg: "#dc2626", bg: "rgba(220,38,38,0.11)", border: "rgba(220,38,38,0.28)" },
  { name: "紫", fg: "#7c3aed", bg: "rgba(124,58,237,0.12)", border: "rgba(124,58,237,0.30)" },
  { name: "青", fg: "#0891b2", bg: "rgba(8,145,178,0.12)", border: "rgba(8,145,178,0.30)" },
];
const TOPIC_COLOR_STORAGE_KEY = "biolit-topic-colors";

function hashString(s) {
  let h = 0;
  const text = String(s || "");
  for (let i = 0; i < text.length; i++) {
    h = ((h << 5) - h + text.charCodeAt(i)) | 0;
  }
  return Math.abs(h);
}

function loadTopicColorOverrides() {
  try {
    const raw = localStorage.getItem(TOPIC_COLOR_STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch (e) {
    return {};
  }
}

function saveTopicColorOverrides(colors) {
  try {
    localStorage.setItem(TOPIC_COLOR_STORAGE_KEY, JSON.stringify(colors || {}));
  } catch (e) {
    console.warn("Failed to save topic colors:", e);
  }
}

function topicPaletteColor(hex) {
  return TOPIC_COLOR_PALETTE.find(color => color.fg.toLowerCase() === String(hex || "").toLowerCase()) || null;
}

function topicColorValue(topic) {
  const overrides = loadTopicColorOverrides();
  const custom = overrides[String(topic || "")];
  if (topicPaletteColor(custom)) return custom;
  return TOPIC_COLOR_PALETTE[hashString(topic) % TOPIC_COLOR_PALETTE.length].fg;
}

function topicColorStyle(topic) {
  const overrides = loadTopicColorOverrides();
  const custom = topicPaletteColor(overrides[String(topic || "")]);
  const c = custom || TOPIC_COLOR_PALETTE[hashString(topic) % TOPIC_COLOR_PALETTE.length];
  return `--topic-color:${c.fg};--topic-bg:${c.bg};--topic-border:${c.border};`;
}

// ---------------------------------------------------------------------------
// LocalStorage config persistence
// ---------------------------------------------------------------------------
const CONFIG_STORAGE_KEY = "biolit-config";
const SEARCH_PANEL_STORAGE_KEY = "biolit-search-panel-collapsed";

function saveConfigToStorage(cfg) {
  try {
    localStorage.setItem(CONFIG_STORAGE_KEY, JSON.stringify(cfg));
  } catch (e) {
    console.warn("Failed to save config to localStorage:", e);
  }
}

function loadConfigFromStorage() {
  try {
    const raw = localStorage.getItem(CONFIG_STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch (e) {
    console.warn("Failed to load config from localStorage:", e);
    return {};
  }
}

function applySearchPanelCollapsed(collapsed) {
  const main = document.querySelector(".main");
  const btn = el("searchPanelToggle");
  const content = el("searchPanelContent");
  if (!main || !btn || !content) return;

  main.classList.toggle("search-panel-collapsed", collapsed);
  btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
  btn.title = collapsed ? "展开搜索面板" : "隐藏搜索面板";
  btn.querySelector(".search-panel-toggle__icon").textContent = collapsed ? "›" : "‹";
  content.setAttribute("aria-hidden", collapsed ? "true" : "false");

  try {
    localStorage.setItem(SEARCH_PANEL_STORAGE_KEY, collapsed ? "1" : "0");
  } catch (e) {
    console.warn("Failed to save search panel state:", e);
  }
}

function loadSearchPanelCollapsed() {
  try {
    return localStorage.getItem(SEARCH_PANEL_STORAGE_KEY) === "1";
  } catch (e) {
    return false;
  }
}

function toggleSearchPanel() {
  const main = document.querySelector(".main");
  applySearchPanelCollapsed(!main?.classList.contains("search-panel-collapsed"));
}

function collectFullConfig() {
  // 收集完整配置（包含敏感信息），用于 localStorage 和下载
  const patch = collectConfigPatch();
  return patch;
}

async function saveConfig() {
  if (!state.sessionId) return;

  const cfg = collectFullConfig();

  // 保存到 localStorage
  saveConfigToStorage(cfg);

  // 同步到服务器 session
  try {
    const data = await apiJson("/api/config/update", { session_id: state.sessionId, config: cfg });
    state.config = data.config || state.config;
    addProgress("配置已保存到浏览器", "ok");
  } catch (e) {
    addProgress("配置同步失败: " + e.message, "err");
  }
}

async function downloadConfigYaml() {
  if (!state.sessionId) return;

  // 先保存最新配置到 session
  const cfg = collectFullConfig();
  try {
    await apiJson("/api/config/update", { session_id: state.sessionId, config: cfg });
  } catch (e) {
    addProgress("配置同步失败: " + e.message, "err");
    return;
  }

  // 下载 yaml
  try {
    const res = await fetch("/api/config/download-yaml", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.sessionId }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${res.status}`);
    }

    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "config.yaml";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    addProgress("config.yaml 已下载", "ok");
  } catch (e) {
    addProgress("下载失败: " + e.message, "err");
  }
}

async function restoreConfigFromStorage() {
  const saved = loadConfigFromStorage();
  if (!saved || Object.keys(saved).length === 0) return;

  // 如果有保存的 LLM 或 email 配置，同步到服务器
  if (saved.llm?.api_key || saved.email?.smtp_server) {
    try {
      await apiJson("/api/config/update", { session_id: state.sessionId, config: saved });
      addProgress("已从浏览器恢复配置", "ok");
    } catch (e) {
      console.warn("Failed to restore config to server:", e);
    }
  }
}

function clearConfig() {
  if (!confirm("确定要清除保存的配置吗？包括 API Key 和邮箱设置。")) return;

  // 清除 localStorage
  try {
    localStorage.removeItem(CONFIG_STORAGE_KEY);
  } catch (e) {
    console.warn("Failed to clear localStorage:", e);
  }

  // 重置表单字段
  el("cfgLlmApiKey").value = "";
  el("cfgLlmBaseUrl").value = "https://dashscope.aliyuncs.com/compatible-mode/v1";
  el("cfgLlmModel").value = "qwen-plus";
  el("cfgSmtpServer").value = "";
  el("cfgSmtpPort").value = "587";
  el("cfgSmtpUsername").value = "";
  el("cfgSmtpPassword").value = "";
  el("cfgFromEmail").value = "";
  el("cfgSmtpTls").checked = true;

  addProgress("配置已清除", "ok");
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

async function apiStreamNdjson(path, body, onEvent, signal) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
    signal: signal,  // AbortSignal for cancellation
  });
  if (!res.ok) {
    const errJson = await res.json().catch(() => ({}));
    throw new Error(errJson?.error || `HTTP ${res.status}`);
  }
  if (!res.body) throw new Error("Streaming body unavailable");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
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
  } finally {
    // Ensure reader is released on abort or completion
    reader.releaseLock();
  }
}

async function apiStreamFormData(path, formData, onEvent, signal) {
  const res = await fetch(path, {
    method: "POST",
    body: formData,
    signal: signal,
  });
  if (!res.ok) {
    const errJson = await res.json().catch(() => ({}));
    throw new Error(errJson?.error || `HTTP ${res.status}`);
  }
  if (!res.body) throw new Error("Streaming body unavailable");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
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
    buffer += decoder.decode();
    const tail = buffer.trim();
    if (tail) {
      try { onEvent(JSON.parse(tail)); } catch (_) {}
    }
  } finally {
    reader.releaseLock();
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
  const l = cfg.llm || {};

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

  // LLM 配置（从 localStorage 恢复，不从服务器，因为服务器不保存敏感信息）
  const saved = loadConfigFromStorage();
  if (saved.llm) {
    el("cfgLlmApiKey").value = saved.llm.api_key || "";
    el("cfgLlmBaseUrl").value = saved.llm.base_url || l.base_url || "https://dashscope.aliyuncs.com/compatible-mode/v1";
    el("cfgLlmModel").value = saved.llm.model || l.model || "qwen-plus";
  } else {
    el("cfgLlmApiKey").value = "";
    el("cfgLlmBaseUrl").value = l.base_url || "https://dashscope.aliyuncs.com/compatible-mode/v1";
    el("cfgLlmModel").value = l.model || "qwen-plus";
  }

  // SMTP 配置（优先 localStorage，其次服务器配置）
  if (saved.email) {
    el("cfgSmtpServer").value = saved.email.smtp_server || e.smtp_server || "";
    el("cfgSmtpPort").value = saved.email.smtp_port || e.smtp_port || 587;
    el("cfgSmtpUsername").value = saved.email.smtp_username || e.smtp_username || "";
    el("cfgSmtpPassword").value = saved.email.smtp_password || "";
    el("cfgFromEmail").value = saved.email.from_email || e.from_email || "";
    el("cfgSmtpTls").checked = saved.email.use_tls !== false;
  } else {
    el("cfgSmtpServer").value = e.smtp_server || "";
    el("cfgSmtpPort").value = e.smtp_port || 587;
    el("cfgSmtpUsername").value = e.smtp_username || "";
    el("cfgSmtpPassword").value = "";
    el("cfgFromEmail").value = e.from_email || "";
    el("cfgSmtpTls").checked = e.use_tls !== false;
  }
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
      smtp_server: el("cfgSmtpServer").value.trim(),
      smtp_port: parseInt(el("cfgSmtpPort").value) || 587,
      smtp_username: el("cfgSmtpUsername").value.trim(),
      smtp_password: el("cfgSmtpPassword").value,
      from_email: el("cfgFromEmail").value.trim(),
      use_tls: el("cfgSmtpTls").checked,
    },
    llm: {
      api_key: el("cfgLlmApiKey").value.trim(),
      base_url: el("cfgLlmBaseUrl").value.trim() || "https://dashscope.aliyuncs.com/compatible-mode/v1",
      model: el("cfgLlmModel").value.trim() || "qwen-plus",
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
function showProgress(showCancel = false) {
  el("progressCard").style.display = "";
  el("progressArea").innerHTML = "";
  const cancelBtn = el("cancelBtn");
  if (cancelBtn) {
    cancelBtn.style.display = showCancel ? "" : "none";
  }
}

function hideCancel() {
  const cancelBtn = el("cancelBtn");
  if (cancelBtn) {
    cancelBtn.style.display = "none";
  }
}

function cancelOperation() {
  if (state.currentAbortController) {
    state.currentAbortController.abort();
    state.currentAbortController = null;
    addProgress("操作已取消", "err");
    hideCancel();
  }
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
  if (tab === "archives") loadAnalysisArchives();
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

  // Create AbortController for cancellation
  state.currentAbortController = new AbortController();

  el("searchBtn").disabled = true;
  el("searchBtn").innerHTML = '<span class="spinner"></span>搜索中...';
  showProgress(true);  // show cancel button
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
    }, state.currentAbortController.signal);
  } catch (e) {
    if (e.name === "AbortError") {
      // User cancelled - already handled in cancelOperation()
      el("papersList").innerHTML = '<div class="empty-state">搜索已取消</div>';
    } else {
      addProgress("请求失败: " + e.message, "err");
    }
  } finally {
    state.searching = false;
    state.currentAbortController = null;
    el("searchBtn").disabled = false;
    el("searchBtn").textContent = "开始搜索";
    hideProgressBar();
    hideCancel();
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
          ${citations ? `<span class="paper-card__meta-item">${citations}</span>` : ""}
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
function getCurrentAnalysisTopic(useSearchTopic) {
  if (!useSearchTopic) return "";
  const cfgKeywordsInput = el("cfgKeywords").value.trim();
  const stateKeywords = (state.config.search || {}).keywords || [];
  return cfgKeywordsInput || stateKeywords.join(", ") || el("nlInput").value.trim() || "";
}

// useSearchTopic: true = 从搜索关键词取主题（搜索结果中点"深度分析"）
//                 false = 用论文标题做主题（直接输入 PMID/DOI/标题）
async function runAnalyze(paperQuery, useSearchTopic = true) {
  if (state.analyzing || !state.sessionId) return;

  state.analyzing = true;
  switchTab("analysis");

  // Create AbortController for cancellation
  state.currentAbortController = new AbortController();

  const c = el("analysisContent");
  c.innerHTML = '<div class="empty-state"><span class="spinner"></span> 正在分析文献...</div>';

  showProgress(true);  // show cancel button
  addProgress("开始深度分析: " + truncate(paperQuery, 60));
  setProgressBar(0, true);

  try {
    // useSearchTopic=true: 从搜索关键词取主题（搜索结果触发）
    // useSearchTopic=false: 传空字符串，后端用论文标题做主题（直接查询触发）
    const topic = getCurrentAnalysisTopic(useSearchTopic);

    await apiStreamNdjson("/api/analyze", {
      session_id: state.sessionId,
      query: paperQuery,
      topic: topic,
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
        loadAnalysisArchives();  // 刷新 Archives 计数
      }

      if (t === "error") {
        c.innerHTML = `<div class="empty-state" style="color:var(--err);">分析失败: ${escapeHtml(ev.message)}</div>`;
        addProgress("分析失败: " + ev.message, "err");
      }
    }, state.currentAbortController.signal);
  } catch (e) {
    if (e.name === "AbortError") {
      // User cancelled - already handled in cancelOperation()
      c.innerHTML = '<div class="empty-state">分析已取消</div>';
    } else {
      c.innerHTML = `<div class="empty-state" style="color:var(--err);">请求失败: ${escapeHtml(e.message)}</div>`;
      addProgress("请求失败: " + e.message, "err");
    }
  } finally {
    state.analyzing = false;
    state.currentAbortController = null;
    hideProgressBar();
    hideCancel();
  }
}

async function runAnalyzeUpload() {
  if (state.analyzing || !state.sessionId) return;

  const fileInput = el("paperUploadInput");
  const file = fileInput.files && fileInput.files[0];
  if (!file) {
    addProgress("请先选择 PDF 文件", "err");
    return;
  }
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    addProgress("目前仅支持 PDF 文件", "err");
    return;
  }

  state.analyzing = true;
  switchTab("analysis");
  state.currentAbortController = new AbortController();

  const c = el("analysisContent");
  c.innerHTML = '<div class="empty-state"><span class="spinner"></span> 正在上传并分析 PDF...</div>';

  const uploadBtn = el("paperUploadBtn");
  uploadBtn.disabled = true;
  uploadBtn.innerHTML = '<span class="spinner"></span>解读中...';

  showProgress(true);
  addProgress("开始上传本地文献: " + truncate(file.name, 60));
  setProgressBar(0, true);

  const formData = new FormData();
  formData.append("session_id", state.sessionId);
  formData.append("paper", file);
  formData.append("title", el("paperUploadTitleInput").value.trim());
  formData.append("topic", getCurrentAnalysisTopic(true));

  try {
    await apiStreamFormData("/api/analyze/upload", formData, (ev) => {
      const t = String(ev.type || "");

      if (t === "status") {
        addProgress(ev.message || ev.step);
      }

      if (t === "paper_meta") {
        const p = ev.data || {};
        c.innerHTML = `
          <div class="analysis-meta">
            <strong>${escapeHtml(p.title || file.name)}</strong>
            <span style="color:var(--text-muted);">${escapeHtml(p.journal || "本地文件")}</span>
          </div>
          <div id="analysisBody"><div class="empty-state"><span class="spinner"></span> LLM 正在分析...</div></div>
        `;
      }

      if (t === "analysis") {
        renderAnalysisResult(ev.data, ev.source, ev.paper);
        addProgress("本地文献解读完成", "ok");
      }

      if (t === "done") {
        setProgressBar(100, false);
        loadAnalysisArchives();
      }

      if (t === "error") {
        c.innerHTML = `<div class="empty-state" style="color:var(--err);">分析失败: ${escapeHtml(ev.message)}</div>`;
        addProgress("分析失败: " + ev.message, "err");
      }
    }, state.currentAbortController.signal);
  } catch (e) {
    if (e.name === "AbortError") {
      c.innerHTML = '<div class="empty-state">分析已取消</div>';
    } else {
      c.innerHTML = `<div class="empty-state" style="color:var(--err);">请求失败: ${escapeHtml(e.message)}</div>`;
      addProgress("请求失败: " + e.message, "err");
    }
  } finally {
    state.analyzing = false;
    state.currentAbortController = null;
    uploadBtn.disabled = !(fileInput.files && fileInput.files.length);
    uploadBtn.textContent = "上传解读";
    hideProgressBar();
    hideCancel();
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
    state.selectedSavedPapers.delete(String(id));
    addProgress(`已删除 ${data.deleted} 篇存档`, "ok");
    await loadSavedPapers();
  } catch (e) {
    addProgress("删除失败: " + e.message, "err");
  }
}

function renderListFilterToolbar(prefix, filter, visibleCount, totalCount, query, placeholder) {
  const range = filter.range || "all";
  const customStyle = range === "custom" ? "" : 'style="display:none;"';
  return `
    <div class="list-filter-bar" data-filter-prefix="${prefix}">
      <div class="list-filter-bar__search">
        <input id="${prefix}SearchInput" class="input list-filter-bar__search-input" type="search"
          placeholder="${escapeHtml(placeholder)}" value="${escapeHtml(query || "")}">
      </div>
      <div class="list-filter-bar__controls">
        <select id="${prefix}DateRange" class="select list-filter-bar__select" title="时间范围">
          <option value="all" ${range === "all" ? "selected" : ""}>全部</option>
          <option value="7" ${range === "7" ? "selected" : ""}>最近 7 天</option>
          <option value="30" ${range === "30" ? "selected" : ""}>最近 30 天</option>
          <option value="90" ${range === "90" ? "selected" : ""}>最近 90 天</option>
          <option value="custom" ${range === "custom" ? "selected" : ""}>自定义</option>
        </select>
        <div id="${prefix}CustomRange" class="list-filter-bar__custom" ${customStyle}>
          <input id="${prefix}DateFrom" class="input list-filter-bar__date" type="date" value="${escapeHtml(filter.from || "")}" title="开始日期">
          <span class="list-filter-bar__dash">-</span>
          <input id="${prefix}DateTo" class="input list-filter-bar__date" type="date" value="${escapeHtml(filter.to || "")}" title="结束日期">
        </div>
        <span class="list-filter-bar__count">${visibleCount} / ${totalCount}</span>
      </div>
    </div>
  `;
}

function bindListFilterToolbar(prefix, filter, getQuery, setQuery, renderFn) {
  const searchEl = document.getElementById(`${prefix}SearchInput`);
  const rangeEl = document.getElementById(`${prefix}DateRange`);
  const customEl = document.getElementById(`${prefix}CustomRange`);
  const fromEl = document.getElementById(`${prefix}DateFrom`);
  const toEl = document.getElementById(`${prefix}DateTo`);

  if (searchEl) {
    searchEl.oninput = () => {
      const pos = searchEl.selectionStart || searchEl.value.length;
      setQuery(searchEl.value);
      renderFn();
      const next = document.getElementById(`${prefix}SearchInput`);
      if (next) {
        next.focus();
        next.setSelectionRange(pos, pos);
      }
    };
  }
  if (rangeEl) {
    rangeEl.onchange = () => {
      filter.range = rangeEl.value;
      if (customEl) customEl.style.display = filter.range === "custom" ? "" : "none";
      renderFn();
    };
  }
  if (fromEl) {
    fromEl.onchange = () => {
      filter.from = fromEl.value;
      filter.range = "custom";
      renderFn();
    };
  }
  if (toEl) {
    toEl.onchange = () => {
      filter.to = toEl.value;
      filter.range = "custom";
      renderFn();
    };
  }
}

function renderSavedPapers() {
  const list = el("savedList");
  if (!state.savedPapers.length) {
    state.selectedSavedPapers.clear();
    list.innerHTML = '<div class="empty-state">暂无存档文献</div>';
    el("savedCount").textContent = "0";
    return;
  }
  const savedQuery = state.savedQuery.trim().toLowerCase();
  const filtered = state.savedPapers
    .filter(p => isWithinDateFilter(p.saved_at, state.savedDateFilter))
    .filter(p => {
      if (!savedQuery) return true;
      return [p.title, p.journal, p.venue, p.doi, p.pubmed_id, p.source]
        .some(value => String(value || "").toLowerCase().includes(savedQuery));
    })
    .sort((a, b) => timestampOf(b.saved_at) - timestampOf(a.saved_at));

  el("savedCount").textContent = String(state.savedPapers.length);
  const filterHtml = renderListFilterToolbar(
    "saved",
    state.savedDateFilter,
    filtered.length,
    state.savedPapers.length,
    state.savedQuery,
    "搜索 Saved 文献、期刊、DOI"
  );

  if (!filtered.length) {
    state.selectedSavedPapers.clear();
    list.innerHTML = filterHtml + '<div class="empty-state">当前时间范围内没有存档文献</div>';
    bindListFilterToolbar(
      "saved",
      state.savedDateFilter,
      () => state.savedQuery,
      value => { state.savedQuery = value; },
      renderSavedPapers
    );
    return;
  }

  const visibleKeys = new Set(filtered.map(savedPaperKey));
  for (const key of Array.from(state.selectedSavedPapers)) {
    if (!visibleKeys.has(key)) state.selectedSavedPapers.delete(key);
  }
  const selectedCount = state.selectedSavedPapers.size;
  const toolbarHtml = `
    <div class="papers-toolbar saved-toolbar">
      <label class="papers-toolbar__select-all">
        <input type="checkbox" id="savedSelectAllCheckbox" ${selectedCount === filtered.length ? "checked" : ""}>
        <span>${selectedCount === filtered.length ? "取消全选" : "全选"}</span>
      </label>
      <span class="papers-toolbar__count">已选 ${selectedCount} 篇</span>
      <button class="btn btn--small btn--ghost" data-action="email-saved-selected" ${selectedCount ? "" : "disabled"}>发送邮件</button>
      <button class="btn btn--small btn--ghost" data-action="export-saved-ris" ${selectedCount ? "" : "disabled"}>导出 RIS</button>
    </div>
  `;

  const savedTimelineHtml = groupByMonth(filtered, p => p.saved_at).map(([monthKey, items]) => {
    const itemsHtml = items.map((p, i) => {
    const key = savedPaperKey(p);
    const title = escapeHtml(p.title || "Untitled");
    const authors = (p.authors || []).slice(0, 3).map(a => escapeHtml(typeof a === "string" ? a : a.name || "")).join(", ");
    const hasMore = (p.authors || []).length > 3;
    const journal = escapeHtml(p.journal || p.venue || "");
    const year = p.year || "";
    const source = String(p.source || "unknown").toLowerCase();
    const badgeCls = sourceBadgeClass(source);
    const doi = p.doi ? escapeHtml(p.doi) : "";
    const savedAt = formatDateTime(p.saved_at);
    const deleteId = p.doi || p.title || "";

    let link = "#";
    if (p.doi) link = `https://doi.org/${encodeURIComponent(p.doi)}`;
    else if (p.url) link = escapeHtml(p.url);
    else if (p.pubmed_id) link = `https://pubmed.ncbi.nlm.nih.gov/${p.pubmed_id}/`;

    return `
      <div class="timeline-item">
        <div class="timeline-item__marker"></div>
        <div class="timeline-item__body">
          <div class="timeline-item__time">${savedAt ? `保存于 ${escapeHtml(savedAt)}` : "保存时间未知"}</div>
          <div class="paper-card${state.selectedSavedPapers.has(key) ? " paper-card--selected" : ""}">
            <div class="paper-card__header">
              <input type="checkbox" class="paper-card__checkbox" data-saved-key="${escapeHtml(key)}" ${state.selectedSavedPapers.has(key) ? "checked" : ""}>
              <div class="paper-card__title" style="flex:1;">
                <a href="${link}" target="_blank" rel="noopener">${title}</a>
              </div>
            </div>
            <div class="paper-card__meta">
              <span class="source-badge ${badgeCls}">${escapeHtml(source)}</span>
              ${journal ? `<span class="paper-card__meta-item">${journal}</span>` : ""}
              ${year ? `<span class="paper-card__meta-item">${year}</span>` : ""}
              ${doi ? `<span class="paper-card__meta-item" style="font-family:var(--mono);font-size:11px;">${doi}</span>` : ""}
            </div>
            <div class="paper-card__meta" style="color:var(--text-muted);">
              ${authors}${hasMore ? " et al." : ""}
            </div>
            <div class="paper-card__actions">
              <button class="btn btn--small btn--danger" data-action="delete-saved" data-delete-id="${escapeHtml(deleteId)}">删除</button>
              <button class="btn btn--small btn--ghost" data-action="analyze" data-query="${doi || escapeHtml(truncate(p.title, 100))}">深度分析</button>
            </div>
          </div>
        </div>
      </div>
    `;
    }).join("");
    return `
      <div class="timeline-month">
        <div class="timeline-month__header">
          <span>${formatMonthLabel(monthKey)}</span>
          <span>${items.length} 篇</span>
        </div>
        <div class="timeline-list">${itemsHtml}</div>
      </div>
    `;
  }).join("");

  const html = filterHtml + toolbarHtml + savedTimelineHtml;

  list.innerHTML = html;
  bindListFilterToolbar(
    "saved",
    state.savedDateFilter,
    () => state.savedQuery,
    value => { state.savedQuery = value; },
    renderSavedPapers
  );
  const selectAll = document.getElementById("savedSelectAllCheckbox");
  if (selectAll) selectAll.indeterminate = selectedCount > 0 && selectedCount < filtered.length;
}

function savedPaperKey(p) {
  return String(p.doi || p.pubmed_id || p.arxiv_id || p.title || p.id || "");
}

function selectedSavedPapers() {
  return state.savedPapers.filter(p => state.selectedSavedPapers.has(savedPaperKey(p)));
}

function toggleSavedPaperSelect(key) {
  if (state.selectedSavedPapers.has(key)) state.selectedSavedPapers.delete(key);
  else state.selectedSavedPapers.add(key);
  renderSavedPapers();
}

function toggleSavedSelectAll() {
  const savedQuery = state.savedQuery.trim().toLowerCase();
  const visible = state.savedPapers
    .filter(p => isWithinDateFilter(p.saved_at, state.savedDateFilter))
    .filter(p => {
      if (!savedQuery) return true;
      return [p.title, p.journal, p.venue, p.doi, p.pubmed_id, p.source]
        .some(value => String(value || "").toLowerCase().includes(savedQuery));
    })
    .sort((a, b) => timestampOf(b.saved_at) - timestampOf(a.saved_at));
  const visibleKeys = visible.map(savedPaperKey);
  const allSelected = visibleKeys.length > 0 && visibleKeys.every(key => state.selectedSavedPapers.has(key));
  for (const key of visibleKeys) {
    if (allSelected) state.selectedSavedPapers.delete(key);
    else state.selectedSavedPapers.add(key);
  }
  renderSavedPapers();
}

async function sendSavedEmail() {
  const papers = selectedSavedPapers();
  if (!papers.length) {
    addProgress("请先勾选 Saved 里的文献", "err");
    return;
  }
  const toEmail = el("cfgEmail").value.trim();
  addProgress(`正在发送 ${papers.length} 篇 Saved 文献的邮件...`);
  try {
    const data = await apiJson("/api/saved/email", {
      session_id: state.sessionId,
      papers,
      to_email: toEmail || undefined,
    });
    addProgress(data.message || "邮件已发送", "ok");
  } catch (e) {
    addProgress("Saved 邮件发送失败: " + e.message, "err");
  }
}

async function exportSavedRIS() {
  const papers = selectedSavedPapers();
  if (!papers.length) {
    addProgress("请先勾选 Saved 里的文献", "err");
    return;
  }
  addProgress(`正在导出 ${papers.length} 篇 Saved 文献的 RIS...`);
  try {
    const res = await fetch("/api/saved/export-ris", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.sessionId, papers }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${res.status}`);
    }
    const disposition = res.headers.get("Content-Disposition") || "";
    const fnameMatch = disposition.match(/filename="?([^"]+)"?/);
    const filename = fnameMatch ? fnameMatch[1] : "saved_literature.ris";
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
    addProgress("Saved RIS 导出失败: " + e.message, "err");
  }
}

// ---------------------------------------------------------------------------
// Analysis Archives (grouped by topic)
// ---------------------------------------------------------------------------
async function loadAnalysisArchives() {
  try {
    const data = await apiJson("/api/analyses/list", {});
    state.analysisArchives = data.entries || [];
    el("archivesCount").textContent = String(state.analysisArchives.length);
    if (state.activeTab === "archives") renderAnalysisArchives();
  } catch (_) {}
}

function renderAnalysisArchives() {
  const list = el("archivesList");
  const entries = state.analysisArchives;

  if (!entries.length) {
    list.innerHTML = '<div class="empty-state">暂无分析存档，在文献上点击「深度分析」后结果会自动保存</div>';
    el("archivesCount").textContent = "0";
    return;
  }
  el("archivesCount").textContent = String(entries.length);

  const timeFiltered = entries
    .filter(e => isWithinDateFilter(e.analyzed_at, state.archiveDateFilter))
    .sort((a, b) => timestampOf(b.analyzed_at) - timestampOf(a.analyzed_at));

  const query = state.archiveQuery.trim().toLowerCase();
  const filtered = query
    ? timeFiltered.filter(e => [e.topic, e.title, e.journal, e.doi, e.source_desc]
        .some(value => String(value || "").toLowerCase().includes(query)))
    : timeFiltered;

  const filterHtml = renderListFilterToolbar(
    "archive",
    state.archiveDateFilter,
    filtered.length,
    entries.length,
    state.archiveQuery,
    "搜索 Archives 标题、标签、期刊、DOI"
  );

  if (!filtered.length) {
    list.innerHTML = filterHtml + `<div class="empty-state">无匹配的存档</div>`;
    _bindArchiveSearch();
    return;
  }

  // Group by topic
  const grouped = {};
  for (const e of filtered) {
    const topic = e.topic || "未分类";
    if (!grouped[topic]) grouped[topic] = [];
    grouped[topic].push(e);
  }

  const topicNames = Object.keys(grouped).sort();
  const html = filterHtml + topicNames.map(topic => {
    const items = grouped[topic];
    const monthGroups = groupByMonth(
      items.sort((a, b) => timestampOf(b.analyzed_at) - timestampOf(a.analyzed_at)),
      e => e.analyzed_at
    );
    const itemsHtml = monthGroups.map(([monthKey, monthItems]) => {
      const monthItemsHtml = monthItems.map(e => {
      const title = escapeHtml(e.title || "Untitled");
      const date = formatDateTime(e.analyzed_at);
      const source = escapeHtml(e.source_desc || "");
      const journal = escapeHtml(e.journal || "");
      const citations = Number(e.citation_count || 0);
      const impactFactor = e.impact_factor != null ? Number(e.impact_factor) : null;
      const summarySentence = escapeHtml(e.summary_sentence || "");
      const doi = e.doi ? escapeHtml(e.doi) : "";
      const htmlFile = escapeHtml(e.html_file || "");

      return `
        <div class="timeline-item">
          <div class="timeline-item__marker"></div>
          <div class="timeline-item__body">
            <div class="timeline-item__time">${date ? `解读于 ${escapeHtml(date)}` : "解读时间未知"}</div>
            <div class="paper-card paper-card--topic">
              <div class="archive-card__topline">
                <div class="archive-card__source">
                  ${journal ? `<span>${journal}</span>` : ""}
                  ${journal && source ? `<span class="archive-card__dot">·</span>` : ""}
                  ${source ? `<span>${source}</span>` : ""}
                </div>
                <div class="archive-card__badges">
                  ${citations > 0 ? `<span class="archive-card__badge archive-card__badge--cite">${citations}</span>` : ""}
                </div>
              </div>
              <div class="paper-card__header archive-card__header">
                <div class="paper-card__title archive-card__title" style="flex:1;">${title}</div>
              </div>
              <div class="paper-card__meta">
                ${journal ? `<span class="archive-card__tag">${journal}</span>` : ""}
                ${impactFactor ? `<span class="archive-card__tag archive-card__tag--if">IF ${impactFactor.toFixed(1)}</span>` : ""}
                ${source ? `<span class="archive-card__tag">${source}</span>` : ""}
                ${doi ? `<span class="paper-card__meta-item" style="font-family:var(--mono);font-size:11px;">${doi}</span>` : ""}
              </div>
              ${summarySentence ? `<div class="archive-card__summary">推荐理由：${summarySentence}</div>` : ""}
              <div class="paper-card__actions">
                ${htmlFile ? `<button class="btn btn--small btn--ghost" data-action="view-archive-html" data-html-file="${htmlFile}">查看报告</button>` : ""}
                <button class="btn btn--small btn--ghost" data-action="delete-archive" data-archive-id="${escapeHtml(e.id || "")}" style="color:var(--err);">删除</button>
              </div>
            </div>
          </div>
        </div>`;
      }).join("");
      return `
        <div class="timeline-month timeline-month--nested">
          <div class="timeline-month__header">
            <span>${formatMonthLabel(monthKey)}</span>
            <span>${monthItems.length} 篇</span>
          </div>
          <div class="timeline-list">${monthItemsHtml}</div>
        </div>
      `;
    }).join("");

    const topicEsc = escapeHtml(topic);
    const colorStyle = topicColorStyle(topic);
    return `
      <div class="archive-topic-group" style="${colorStyle}">
        <div class="archive-topic-header">
          <span class="archive-topic-tag">${topicEsc}</span>
          <span class="archive-topic-count">${items.length} 篇</span>
          <button class="archive-topic-edit" data-action="edit-topic" data-topic="${topicEsc}" title="编辑标签">✎</button>
        </div>
        ${itemsHtml}
      </div>`;
  }).join("");

  list.innerHTML = html;
  _bindArchiveSearch();
}

function _bindArchiveSearch() {
  bindListFilterToolbar(
    "archive",
    state.archiveDateFilter,
    () => state.archiveQuery,
    value => { state.archiveQuery = value; },
    renderAnalysisArchives
  );
}

async function renameTopic(oldTopic) {
  const newTopic = prompt("输入新的主题名：", oldTopic);
  if (!newTopic || newTopic.trim() === oldTopic) return;

  try {
    const data = await apiJson("/api/analyses/rename-topic", {
      old_topic: oldTopic,
      new_topic: newTopic.trim()
    });
    if (data.ok) {
      const colors = loadTopicColorOverrides();
      if (colors[oldTopic] && !colors[newTopic.trim()]) {
        colors[newTopic.trim()] = colors[oldTopic];
        delete colors[oldTopic];
        saveTopicColorOverrides(colors);
      }
      addProgress(`已将「${oldTopic}」改名为「${newTopic.trim()}」(${data.renamed} 条)`, "ok");
      await loadAnalysisArchives();
    } else {
      addProgress("改名失败: " + (data.error || ""), "err");
    }
  } catch (e) {
    addProgress("改名失败: " + e.message, "err");
  }
}

function editTopic(topic) {
  const existing = document.querySelector(".topic-edit-dialog");
  if (existing) existing.remove();

  let selectedColor = topicColorValue(topic);
  const colorButtons = TOPIC_COLOR_PALETTE.map(color => `
    <button class="topic-edit-dialog__swatch${color.fg === selectedColor ? " is-selected" : ""}"
      type="button"
      data-color="${color.fg}"
      title="${color.name}"
      style="--swatch:${color.fg};"></button>
  `).join("");

  const dialog = document.createElement("div");
  dialog.className = "topic-edit-dialog";
  dialog.innerHTML = `
    <div class="topic-edit-dialog__panel">
      <label class="topic-edit-dialog__label">标签名</label>
      <input class="input topic-edit-dialog__input" type="text" value="${escapeHtml(topic)}">
      <div class="topic-edit-dialog__label">颜色</div>
      <div class="topic-edit-dialog__swatches">${colorButtons}</div>
      <div class="topic-edit-dialog__actions">
        <button class="btn btn--small btn--ghost" type="button" data-topic-edit-cancel>取消</button>
        <button class="btn btn--small btn--primary" type="button" data-topic-edit-save>保存</button>
      </div>
    </div>
  `;
  document.body.appendChild(dialog);

  const input = dialog.querySelector(".topic-edit-dialog__input");
  const close = () => dialog.remove();
  input.focus();
  input.select();

  dialog.addEventListener("click", async (ev) => {
    const swatch = ev.target.closest("[data-color]");
    if (swatch) {
      selectedColor = swatch.getAttribute("data-color");
      dialog.querySelectorAll(".topic-edit-dialog__swatch").forEach(btn => btn.classList.remove("is-selected"));
      swatch.classList.add("is-selected");
      return;
    }
    if (ev.target.closest("[data-topic-edit-cancel]") || ev.target === dialog) {
      close();
      return;
    }
    if (!ev.target.closest("[data-topic-edit-save]")) return;

    const newTopic = input.value.trim();
    if (!newTopic) return;

    try {
      const colors = loadTopicColorOverrides();
      const topicChanged = newTopic !== topic;
      if (topicChanged) {
        const data = await apiJson("/api/analyses/rename-topic", {
          old_topic: topic,
          new_topic: newTopic
        });
        if (!data.ok) {
          addProgress("标签保存失败: " + (data.error || ""), "err");
          return;
        }
        delete colors[topic];
        addProgress(`已将「${topic}」改为「${newTopic}」(${data.renamed} 条)`, "ok");
      }
      colors[newTopic] = selectedColor;
      saveTopicColorOverrides(colors);
      if (!topicChanged) addProgress(`已更新「${newTopic}」的标签颜色`, "ok");
      close();
      await loadAnalysisArchives();
    } catch (e) {
      addProgress("标签保存失败: " + e.message, "err");
    }
  });

  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") close();
    if (ev.key === "Enter") dialog.querySelector("[data-topic-edit-save]")?.click();
  });
}

async function viewArchiveHtml(htmlFile) {
  try {
    const data = await apiJson("/api/analyses/html", { html_file: htmlFile });
    if (data.html) {
      const w = window.open("", "_blank");
      w.document.write(data.html);
      w.document.close();
    }
  } catch (e) {
    addProgress("查看报告失败: " + e.message, "err");
  }
}

async function deleteArchiveEntry(archiveId) {
  if (!confirm("确定删除此条分析存档？本地文件也会一并删除。")) return;
  try {
    const data = await apiJson("/api/analyses/delete", { id: archiveId });
    if (data.ok) {
      addProgress("已删除存档", "ok");
      await loadAnalysisArchives();
    } else {
      addProgress("删除失败: " + (data.error || ""), "err");
    }
  } catch (e) {
    addProgress("删除失败: " + e.message, "err");
  }
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
  el("searchPanelToggle").addEventListener("click", toggleSearchPanel);

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

  // Single paper analysis (by PMID/DOI/title) — topic = 论文标题
  el("analyzeQueryBtn").addEventListener("click", () => {
    const q = el("analyzeQueryInput").value.trim();
    if (q) runAnalyze(q, false);
  });
  el("analyzeQueryInput").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      const q = el("analyzeQueryInput").value.trim();
      if (q) runAnalyze(q, false);
    }
  });
  el("paperUploadInput").addEventListener("change", () => {
    const file = el("paperUploadInput").files?.[0];
    el("paperUploadBtn").disabled = !file;
    if (file && !el("paperUploadTitleInput").value.trim()) {
      el("paperUploadTitleInput").value = file.name.replace(/\.pdf$/i, "").replaceAll("_", " ").replaceAll("-", " ");
    }
  });
  el("paperUploadBtn").addEventListener("click", runAnalyzeUpload);

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

  // System config buttons
  el("saveConfigBtn").addEventListener("click", saveConfig);
  el("downloadConfigBtn").addEventListener("click", downloadConfigYaml);
  el("clearConfigBtn").addEventListener("click", clearConfig);

  // Cancel button for aborting operations
  el("cancelBtn").addEventListener("click", cancelOperation);

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
    const action = btn?.getAttribute("data-action");
    if (action === "email-saved-selected") {
      sendSavedEmail();
      return;
    }
    if (action === "export-saved-ris") {
      exportSavedRIS();
      return;
    }
    if (!btn) return;
    if (action === "delete-saved") {
      const id = btn.getAttribute("data-delete-id");
      if (id) deleteSavedPaper(id);
    }
    if (action === "analyze") {
      const query = btn.getAttribute("data-query");
      if (query) runAnalyze(query);
    }
  });
  el("savedList").addEventListener("change", (ev) => {
    const selectAll = ev.target.closest("#savedSelectAllCheckbox");
    if (selectAll) {
      toggleSavedSelectAll();
      return;
    }
    const cb = ev.target.closest("[data-saved-key]");
    if (cb) toggleSavedPaperSelect(cb.getAttribute("data-saved-key"));
  });

  // Archives list delegation (rename-topic + view-archive-html)
  el("archivesList").addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-action]");
    if (!btn) return;
    const action = btn.getAttribute("data-action");
    if (action === "edit-topic") {
      const topic = btn.getAttribute("data-topic");
      if (topic) editTopic(topic);
    }
    if (action === "view-archive-html") {
      const htmlFile = btn.getAttribute("data-html-file");
      if (htmlFile) viewArchiveHtml(htmlFile);
    }
    if (action === "delete-archive") {
      const archiveId = btn.getAttribute("data-archive-id");
      if (archiveId) deleteArchiveEntry(archiveId);
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
  applySearchPanelCollapsed(loadSearchPanelCollapsed());
  el("themeToggle").addEventListener("click", toggleTheme);
  bind();
  await createSession();
  await restoreConfigFromStorage();
  loadSavedPapers();
  loadAnalysisArchives();
});
