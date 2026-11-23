"use strict";
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));
const paths = {
  eye: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
  eyeOff: '<path d="m3 3 18 18M9 5.5a12 12 0 0 1 3-.5c6 0 10 7 10 7a18 18 0 0 1-3.3 4.2M6 6.6A20 20 0 0 0 2 12s4 7 10 7a12 12 0 0 0 4.4-.9"/>',
  overview:
    '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  queue:
    '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9h1m3 0h6M7 15h1m3 0h6"/>',
  settings:
    '<path d="m9 3 6 0 .8 2.4 2.4 1.3 2.2-.4 2 3.6-1.8 1.8v2.6l1.3 1.8-2.2 3.5-2.4-.6-2 1.2-.7 2.1H9.6l-.8-2.2-2.3-1.3-2.4.4-2-3.6L4 13.7v-2.5L2.5 9.4l2.2-3.5 2.3.5 2-1.2z"/><circle cx="12" cy="12" r="3"/>',
  code: '<path d="m8 7-5 5 5 5m8-10 5 5-5 5m-3-13-2 16"/>',
  external: '<path d="M13 4h7v7m0-7L10 14M9 5H5v15h15v-4"/>',
  panel: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
  calendar:
    '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 10h18m-13 4h2m3 0h2"/>',
  chart: '<path d="M3 3v18h18M7 16v-5m5 5V6m5 10v-8"/>',
  pie: '<path d="M20.5 14A9 9 0 1 1 10 3.5V14z"/><path d="M14 2.5V10h7.5A9 9 0 0 0 14 2.5z"/>',
  wallet:
    '<path d="M19 7V4L5 6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h15V8H5m15 4h-5v4h5"/>',
  trend: '<path d="m3 16 6-6 4 4 8-9m-6 0h6v6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  arrow: '<path d="M4 12h15m-5-5 5 5-5 5"/>',
  x: '<path d="m6 6 12 12M6 18 18 6"/>',
  search: '<circle cx="10" cy="10" r="6.5"/><path d="m15 15 5 5"/>',
  grip: '<path stroke-width="3" d="M9 6h.1M15 6h.1M9 12h.1M15 12h.1M9 18h.1M15 18h.1"/>',
  down: '<path d="M12 3v14m-5-5 5 5 5-5M4 21h16"/>',
  edit: '<path d="m4 16 12-12 4 4L8 20l-5 1zm10-10 4 4"/>',
  image:
    '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8" cy="8" r="1"/><path d="m3 17 6-6 4 4 3-3 5 5"/>',
  contact:
    '<rect x="3" y="5" width="18" height="15" rx="2"/><circle cx="9" cy="11" r="2"/><path d="M6 16c1-3 5-3 6 0m2-6h4m-4 4h3"/>',
  backup: '<path d="M12 3v12m-4-4 4 4 4-4M4 16v4h16v-4"/>',
  file: '<path d="M5 3h9l5 5v13H5zm9 0v5h5M8 12h8m-8 4h6"/>',
};
function icon(name) {
  return `<svg class="icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">${paths[name] || ""}</svg>`;
}
function esc(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
}
const money = (n) =>
  Number(n).toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
const localDay = () =>
  new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
const state = {
  page: "overview",
  records: [],
  settings: { contacts: [], platforms: [] },
  stats: null,
  months: 6,
  incomePeriod: { total: 0, average: 0 },
  year: null,
  lane: "normal",
  receipts: [],
  receiptQuery: "",
  query: "",
  showUnmatched: false,
};
let loadId = 0,
  toastTimer,
  editing = null,
  editingRevision = null,
  insertBefore = null,
  attachments = [],
  suggestTimer,
  editorGeneration = 0,
  readingFiles = 0,
  busy = false;
const queueNames = {
  normal: "排队中",
  deferred: "延期等待",
  archived: "已结稿",
};
function queueRecords(lane = "normal") {
  return state.records.filter((r) =>
    lane === "archived"
      ? r.status === "已结稿"
      : r.status !== "已结稿" && r.queue === lane,
  );
}
function commissionTags(record) {
  const sample = record.sample ? '<span class="sample-tag">示例</span>' : "";
  const priority = record.is_priority ? '<span class="priority-tag">插队</span>' : "";
  return [sample, priority].filter(Boolean).join(" ");
}

function toast(msg) {
  $("#toast").textContent = msg;
  $("#toast").classList.add("toast-show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(
    () => $("#toast").classList.remove("toast-show"),
    3000,
  );
}
async function api(path, method = "GET", data) {
  const response = await fetch("/api" + path, {
    method,
    headers: data === undefined ? {} : { "Content-Type": "application/json" },
    body: data instanceof File ? data : data === undefined ? undefined : JSON.stringify(data),
  });
  if (!response.ok) {
    let msg = "请求失败，请重试";
    try {
      const { detail } = await response.json();
      msg = Array.isArray(detail)
        ? detail.map((e) => `${e.loc.slice(1).join(".")} ${e.msg}`).join("，")
        : detail || msg;
    } catch {}
    throw Object.assign(Error(msg), {status: response.status});
  }
  return response.status === 204 ? null : response.json();
}
async function refresh() {
  const id = ++loadId;
  const requested = location.hash.slice(1);
  const page = ["overview", "queue", "receipts", "settings"].includes(requested) ? requested : "overview";
  const query = new URLSearchParams({page});
  if (page === "overview") {
    if (state.year) query.set("year", state.year);
    else if (state.months === "all") query.set("all_time", "true");
    else query.set("months", state.months);
  }
  const snapshot = await api("/workspace?" + query);
  if (id !== loadId) return;
  $("#main").removeAttribute("aria-busy");
  Object.assign(state, {stats: null}, snapshot);
  render();
}
function button(label, action, ico = "", primary = false) {
  return `<button type="button" class="btn ${primary ? "primary" : ""}" data-action="${action}">${ico ? icon(ico) : ""}${label}</button>`;
}
function statusTag(s) {
  return `<span class="status ${s === "进行中" ? "progress" : s === "已结稿" ? "done" : ""}">${esc(s)}</span>`;
}
function title(text, action = "") {
  return `<div class="page-head"><h1>${text}</h1>${action}</div>`;
}
function art() {
  return `<div class="art" aria-hidden="true"><i class="sheet back"></i><i class="sheet"></i><div class="paper"><div class="rings">${"<i></i>".repeat(4)}</div><div class="squares">${"<i></i>".repeat(12)}</div></div><i class="pencil"></i></div>`;
}
const incomeWindows = [0, 3, 6, 12];
function metricContent(kind) {
  const months = state.incomePeriod[kind];
  const period = { 3: "近三月", 6: "近六月", 12: "近一年" }[months] || "";
  const label = kind === "average" ? period + "每月平均收入" : (period || "过去总") + "收入";
  const values = months ? state.stats.periods[months] : state.stats;
  return `<span class="metric-label">${label}</span><span class="number"><em>¥</em>${money(values[kind])}</span>`;
}
function metric(kind) {
  const average = kind === "average";
  return `<button class="metric" data-action="cycle-income" data-kind="${kind}" title="点击切换统计周期">
    <span class="tile ${average ? "teal" : ""}">${icon(average ? "trend" : "wallet")}</span>
    <span class="metric-value" aria-live="polite">${metricContent(kind)}</span>
  </button>`;
}
function monthlyChart(values) {
  const entries = Object.entries(values),
    max = entries.reduce((peak, [, value]) => Math.max(peak, value), 100);
  const unit = 10 ** Math.floor(Math.log10(max)),
    top = Math.ceil(max / unit) * unit;
  const left = 49,
    right = Math.max(512, left + 28 * entries.length),
    bottom = 145,
    w = (right - left) / entries.length;
  let html = "";
  for (let i = 0; i <= 4; i++) {
    const y = bottom - 32 * i,
      v = (top * i) / 4;
    html += `<line x1="${left}" x2="${right}" y1="${y}" y2="${y}" stroke="#dce5f0" stroke-dasharray="3 5"/><text x="${left - 7}" y="${y + 4}" text-anchor="end">${v >= 10000 ? +(v / 10000).toFixed(1) + "万" : +v.toFixed(1)}</text>`;
  }
  entries.forEach(([key, n], i) => {
    const x = left + w * (i + 0.5),
      h = (n / top) * 128,
      bw = Math.min(35, w * 0.56),
      current = i === entries.length - 1;
    const label = entries.length > 12
      ? (i % 3 === 0 ? key : "")
      : Number(key.slice(5)) + "月";
    html += `<g data-tip="${key} · 收入 ¥${money(n)}"><rect x="${x - w / 2}" y="12" width="${w}" height="155" fill="transparent"/><rect class="bar ${current ? "current" : ""}" x="${x - bw / 2}" y="${bottom - Math.max(h, 1.5)}" width="${bw}" height="${Math.max(h, 1.5)}" rx="3"/><text x="${x}" y="166" text-anchor="middle">${label}</text></g>`;
  });
  return `<div class="plot-scroll"><svg class="plot" style="min-width:${right > 512 ? right + 8 : 0}px" viewBox="0 0 ${right + 8} 175" role="img" aria-label="月收入柱状图">${html}</svg></div><details class="chart-data"><summary>查看月收入明细</summary>${entries.map(([m, n]) => `<div><span>${m}</span><b>¥${money(n)}</b></div>`).join("")}</details>`;
}
const colors = [
  "#5480de",
  "#72bdb9",
  "#ecb96e",
  "#a397d6",
  "#d395af",
  "#89a4ba",
];
function platforms() {
  const { platforms, total } = state.stats,
    names = [
      ...new Set([...Object.keys(platforms), ...state.settings.platforms]),
    ];
  let end = 0;
  const segments = names.map((name, i) => {
    const amount = Object.hasOwn(platforms, name) ? platforms[name] : 0;
    const share = total ? 100 * amount / total : 0;
    const start = end;
    end += share;
    const percent = +share.toFixed(1) + "%";
    return {
      name, amount, start, end, percent, color: colors[i % colors.length],
      tip: `${name} · ¥${money(amount)} · 占比 ${percent}`,
    };
  });
  const gradient = segments.map(s => `${s.color} ${s.start}% ${s.end}%`).join(",");
  return `<div class="donut-content"><div class="donut" ${total ? `style="background:conic-gradient(${gradient})"` : ""}><div class="donut-inner"><strong>${Object.keys(platforms).length}</strong><small>收入平台</small></div></div><div class="legend">${segments.map(s => `<div data-tip="${esc(s.tip)}" data-end="${s.end}"><i style="background:${s.color}"></i><span>${esc(s.name)}<small>¥${money(s.amount)}</small></span><b>${s.percent}</b></div>`).join("")}</div></div>`;
}

function hideChartTip() {
  $("#chartTip").hidden = true;
}
document.addEventListener("pointermove", event => {
  let item = event.target.closest("[data-tip]");
  if (event.target.matches(".donut") && state.stats.total > 0) {
    const ring = event.target;
    const rect = ring.getBoundingClientRect();
    const dx = event.clientX - rect.left - rect.width / 2;
    const dy = event.clientY - rect.top - rect.height / 2;
    const fraction = (Math.atan2(dx, -dy) / (2 * Math.PI) + 1) % 1;
    item = Array.from(ring.parentNode.querySelectorAll("[data-end]"))
      .find(row => fraction * 100 < Number(row.dataset.end));
  }
  if (!item || event.pointerType === "touch") return hideChartTip();
  const tip = $("#chartTip");
  tip.textContent = item.dataset.tip;
  tip.hidden = false;
  tip.style.left = Math.max(4, Math.min(event.clientX + 12, innerWidth - tip.offsetWidth - 6)) + "px";
  tip.style.top = (event.clientY + 14 + tip.offsetHeight < innerHeight
    ? event.clientY + 14 : Math.max(4, event.clientY - tip.offsetHeight - 8)) + "px";
});
document.addEventListener("pointerout", e => { if (!e.relatedTarget) hideChartTip(); });
document.addEventListener("scroll", hideChartTip, true);
window.addEventListener("blur", hideChartTip);

function empty(text, create = false) {
  return `<div class="empty"><span class="tile">${icon("queue")}</span><strong>${text}</strong>${create ? button("新建约稿", "new", "plus") : ""}</div>`;
}
function queuePreview(lane) {
  const deferred = lane === "deferred";
  const records = queueRecords(lane);
  const heading = deferred ? "延期预览" : "排队预览";
  const columns = deferred ? ["甲方昵称", "约稿内容"] : ["顺位", "甲方", "约稿内容", "预计开始", "结稿状态"];
  const rows = records.map((record, index) => {
    const nickname = `<button class="namebtn" data-action="detail" aria-haspopup="dialog">${esc(record.nickname)}</button>`;
    return `<tr data-action="detail" data-key="${esc(record.id)}">
      ${deferred ? "" : `<td>${String(index + 1).padStart(2, "0")}</td>`}
      <td><div class="preview-client">${nickname}${deferred ? "" : commissionTags(record)}</div></td><td>${esc(record.title || "—")}</td>
      ${deferred ? "" : `<td class="preview-date">${esc(record.planned || "待定")}</td><td>${statusTag(record.status)}</td>`}</tr>`;
  }).join("");
  return `<section class="card preview-${lane}"><div class="card-head"><h2>${icon(deferred ? "calendar" : "queue")}${heading}</h2>
    <a href="#queue" data-action="open-lane" data-lane="${lane}" class="linkbtn" aria-label="查看全部${queueNames[lane]}约稿">查看全部 ${icon("arrow")}</a></div>
    ${rows ? `<div class="preview-scroll"><table class="compact-table" aria-label="${heading}"><thead><tr>${columns.map(label => `<th scope="col">${label}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>` : empty(deferred ? "暂无延期约稿" : "暂无排队中的约稿", !deferred)}</section>`;
}
function overview() {
  const s = state.stats;
  return (
    title("工作台", button("新建约稿", "new", "plus", true)) +
    `<div class="summary-grid"><section class="hero"><h2 class="hero-title">${icon("calendar")}当前排期</h2><div class="hero-stats"><div><span>排队中的甲方</span><strong>${s.count}<small>位</small></strong><span class="deferred-count">延期中的甲方 <b>${s.deferred_count}</b> 位</span></div><div><span>预计排期至</span><strong class="scheduled" title="${esc(s.until || "")}">${s.until ? s.until.slice(5).replace("-", " / ") : "—"}</strong>${s.until ? `<span>${s.until.slice(0, 4)} 年</span>` : ""}</div></div>${art()}</section><div class="metric-stack">${metric("total")}${metric("average")}</div></div>
 <div class="charts"><section class="card"><div class="card-head"><h2>${icon("chart")}每月收入</h2><select class="select-small" id="months" aria-label="图表月份">${[3, 6, 12].map((n) => `<option value="${n}" ${!state.year && n === state.months ? "selected" : ""}>近 ${n} 个月</option>`).join("")}<option value="all" ${!state.year && state.months === "all" ? "selected" : ""}>全部时间</option>${s.years.map((year) => `<option value="y${year}" ${year === state.year ? "selected" : ""}>${year} 年</option>`).join("")}</select></div><small class="chart-unit">金额（元）</small>${monthlyChart(s.monthly)}</section><section class="card"><div class="card-head"><h2>${icon("pie")}收入来源</h2><span class="chart-unit">累计收入占比</span></div>${platforms()}</section></div>
 <div class="queue-previews">${queuePreview("normal")}${queuePreview("deferred")}</div>`
  );
}
function briefDetails(record) {
  const title = record.title?.trim();
  if (!title && !record.requirements && !record.attachments.length) return "";
  return `<div id="brief-${esc(record.id)}" class="brief-detail" hidden>${record.requirements ? `<p>${esc(record.requirements)}</p>` : record.attachments.length ? "" : '<p class="helper">暂无详细需求</p>'}${record.attachments.length ? `<div class="thumbs">${record.attachments.map((a, i) => `<div class="thumb">${thumbnailButton(a, i)}</div>`).join("")}</div>` : ""}</div>`;
}
function matchesQuery(record, query) {
  return [record.nickname, record.contact, record.title].some(value =>
    (value || "").toLowerCase().includes(query)
  );
}

function queueTabs() {
  const q = state.query.trim().toLowerCase();
  return Object.entries(queueNames).map(([key, label]) => {
    const selected = key === state.lane;
    const records = queueRecords(key);
    const hits = q ? records.filter(r => matchesQuery(r, q)).length : 0;
    const highlight = hits > 0 && !selected;
    return `<button data-action="lane" data-lane="${key}" aria-pressed="${selected}" class="${selected ? "selected" : highlight ? "lane-match" : ""}">${label} ${q ? hits + "/" : ""}${records.length}</button>`;
  }).join("");
}

function queueList() {
  const list = queueRecords(state.lane);
  const archived = state.lane === "archived";
  const q = state.query.trim().toLowerCase();
  const matches = list.map((r, i) => ({r, i, hit: !!q && matchesQuery(r, q)}))
    .filter(({hit}) => !q || state.showUnmatched || hit);
  if (!matches.length)
    return `<div class="card">${empty(q ? "没有匹配的约稿" : archived ? "暂无已结稿记录" : state.lane === "deferred" ? "暂无延期约稿" : "暂无排队中的约稿", !q && !archived)}</div>`;
  return matches
    .map(({r, i, hit}, index) => {
      const drag = !archived && !q;
      const detail = briefDetails(r);
      const contact = r.contact
        ? `${esc(r.contact_type)} · ${esc(r.contact)}`
        : "未填写联系方式";
      return `<article class="queue-card${state.showUnmatched && hit ? " search-match" : ""}" data-key="${r.id}">
        <div class="queue-rank">
          ${drag ? `<button class="grip" title="拖动排序，或用上下方向键移动" aria-label="移动${esc(r.nickname)}">${icon("grip")}</button>` : ""}
          <span class="pos">${String(i + 1).padStart(2, "0")}</span>
        </div>
        <div class="queue-project">
          <div class="queue-client">
            <button class="namebtn" data-action="edit">${esc(r.nickname)}</button>
            ${commissionTags(r)}
          </div>
          ${detail ? `<button class="project-title" data-action="brief" aria-expanded="false" aria-controls="brief-${esc(r.id)}">${esc(r.title?.trim() || "查看约稿需求")}</button>` : ""}
          <span class="queue-contact">${contact}</span>
        </div>
        <div class="queue-schedule">
          <div class="queue-value"><span>预计开始</span><b>${esc(r.planned || "待定")}</b></div>
          <div class="queue-progress">${statusTag(r.status)}<span>${r.brief_received ? "已交需求" : "待交需求"}</span></div>
        </div>
        <div class="client-history">
          <div class="queue-value"><span>累计收款</span><b>¥${money(r.history.total)}</b></div>
          <div class="history-dates"><span>已完成 ${r.history.completed} 单</span><span>最近收款 ${esc(r.history.latest || "—")}</span></div>
        </div>
        <div class="queue-actions">
          ${archived ? "" : `<button class="btn advance" data-action="status" data-status="${r.status === "进行中" ? "已结稿" : "进行中"}">${r.status === "进行中" ? "结稿" : "开始"}</button>`}
          ${archived ? "" : state.lane === "deferred" ? `<button class="btn" data-action="resume">${icon("arrow")}恢复排队</button>` : `<button class="btn" data-action="defer">${icon("down")}延期</button>`}
          <button class="iconbtn" data-action="edit" aria-label="编辑${esc(r.nickname)}">${icon("edit")}</button>
        </div>
        ${detail}
      </article>${drag && index < matches.length - 1 ? `<div class="insert"><button data-action="insert" data-before="${list[i + 1].id}" aria-label="在此插入新约稿">＋ 插队</button></div>` : '<div class="list-end"></div>'}`;
    })
    .join("");
}
function queue() {
  return (
    title("排队列表", button("新建约稿", "new", "plus", true)) +
    `<div class="toolbar"><div class="tabs">${queueTabs()}</div><div class="search">${icon("search")}<input id="query" value="${esc(state.query)}" placeholder="搜索昵称、账号或约稿" aria-label="搜索昵称、账号或约稿"><button class="search-eye" data-action="search-mode" aria-label="显示未匹配项" aria-pressed="${state.showUnmatched}" title="${state.showUnmatched ? "当前显示全部，点击隐藏未匹配项" : "当前隐藏未匹配项，点击显示全部"}">${icon(state.showUnmatched ? "eye" : "eyeOff")}</button></div></div><div id="queueList">${queueList()}</div>`
  );
}
let detailRequest = 0;
async function openCommissionDetail(key) {
  const sequence = ++detailRequest;
  const generation = loadId;
  let r = state.records.find(record => record.id === key);
  if (!r) {
    r = await api("/commissions/" + encodeURIComponent(key));
    if (generation !== loadId || sequence !== detailRequest) return;
    state.records.push(r);
  }
  const dialog = $("#commissionDetail");
  const fields = [
    ["联系方式", [r.contact_type, r.contact].filter(Boolean).join(" · ")],
    ["队列", r.status === "已结稿" ? "已结稿" : queueNames[r.queue]],
    ["预计开始", r.planned], ["实际开始", r.started],
    ["需求状态", r.brief_received ? "已交需求" : "待交需求"],
  ];
  dialog.dataset.key = key;
  dialog.innerHTML = `<div class="dialog-head"><h2 id="detailTitle">约稿详情</h2><button class="iconbtn" data-dismiss="commissionDetail" aria-label="关闭">${icon("x")}</button></div>
    <div class="dialog-scroll"><section><h3>${esc(r.nickname)} ${commissionTags(r)} ${statusTag(r.status)}</h3>
    <dl class="detail-fields">${fields.map(([label, value]) => `<div><dt>${label}</dt><dd>${esc(value || "—")}</dd></div>`).join("")}</dl></section>
    <section><h3>${esc(r.title || "约稿需求")}</h3><p class="detail-brief">${esc(r.requirements || "暂无详细需求")}</p>
    ${r.attachments.length ? `<div class="thumbs">${r.attachments.map((attachment, i) => `<div class="thumb">${thumbnailButton(attachment, i)}</div>`).join("")}</div>` : ""}</section>
    <section><h3>本单收款</h3>${r.payments.length ? r.payments.map(p => `<div class="detail-payment"><time>${esc(p.date)}</time><span>${esc(p.stage)} · ${esc(p.platform)}</span><b>¥${money(p.amount)}</b></div>`).join("") : "暂无收款记录"}</section></div>
    <div class="dialog-foot"><button class="btn" data-action="edit">编辑约稿</button></div>`;
  hideChartTip();
  dialog.showModal();
}

function receiptList() {
  const query = state.receiptQuery.trim().toLowerCase();
  const entries = state.receipts.filter((p) =>
    [p.nickname, p.title || "", p.stage, p.platform, p.date].some((v) =>
      v.toLowerCase().includes(query),
    ),
  );
  if (!entries.length) return empty(query ? "没有匹配的收款" : "暂无收款记录");
  const cutoff = localDay();
  return `<div class="ledger-wrap"><table class="compact-table ledger"><caption>${entries.length} 笔记录</caption><thead><tr><th scope="col">支付日期</th><th scope="col">甲方</th><th scope="col">约稿内容</th><th scope="col">稿期阶段</th><th scope="col">支付方式</th><th scope="col">金额（元）</th></tr></thead><tbody>${entries.map((p) => `<tr data-action="detail" data-key="${esc(p.commission_id)}"><td><time>${esc(p.date)}</time>${p.date > cutoff ? " <small>待入账</small>" : ""}</td><td><button class="namebtn" data-action="detail" aria-haspopup="dialog">${esc(p.nickname)}</button> ${commissionTags(p)}</td><td class="receipt-title">${esc(p.title || "—")}</td><td>${esc(p.stage)}</td><td>${esc(p.platform)}</td><td class="receipt-amount">¥${money(p.amount)}</td></tr>`).join("")}</tbody></table></div>`;
}
function receiptsPage() {
  return (
    title("收款记录") +
    `<section class="card"><div class="card-head"><h2>${icon("wallet")}收款明细</h2><label class="search">${icon("search")}<input id="receiptQuery" aria-label="搜索收款记录" placeholder="搜索甲方、约稿或收款" value="${esc(state.receiptQuery)}"></label></div><div id="receiptList">${receiptList()}</div></section>`
  );
}
function optionCard(key, label, ico) {
  return `<section class="card"><div class="card-head"><h2>${icon(ico)}${label}</h2></div><div class="chips">${state.settings[key].map((v, i) => `<span class="chip">${esc(v)}<button data-action="remove-option" data-list="${key}" data-index="${i}" aria-label="移除${esc(v)}">×</button></span>`).join("")}</div><form class="inline-form" data-list="${key}"><input class="control" name="value" aria-label="新${label}" placeholder="添加${label}" required><button class="btn">添加</button></form></section>`;
}
function settingsPage() {
  return (
    title("设置") +
    `<div class="settings-grid">${optionCard("platforms", "支付方式", "wallet")}${optionCard("contacts", "联系方式", "contact")}<section class="card"><div class="card-head"><h2>${icon("backup")}数据备份</h2></div><p>JSON 备份包含设置和原图，CSV 导出约稿与收款明细，Beancount 导出账本。注意仅支持json格式导入。导入会替换当前数据。</p><div class="buttons"><a class="btn" href="/api/export" download>${icon("backup")}导出 JSON</a><a class="btn" href="/api/export/csv" download>导出 CSV</a><a class="btn" href="/api/export/beancount" download>导出 Beancount</a><label class="btn attach-label">导入备份<input id="importFile" type="file" accept=".json,application/json"></label></div></section><section class="card"><div class="card-head"><h2>${icon("queue")}示例数据</h2></div><p>包含正常排队、延期、结稿和分期收款记录。</p><div class="buttons">${state.has_samples ? button("清除示例", "clear-examples") : button("加载示例", "load-examples", "plus")}</div></section><section class="card"><div class="card-head"><h2>${icon("code")}API 文档</h2></div><p>在线查看并调试接口，或下载 OpenAPI 定义。</p><div class="buttons"><a class="btn" href="/docs" target="_blank" rel="noopener">交互文档 ${icon("external")}</a><a class="linkbtn" href="/openapi.json" download="openapi.json">下载 OpenAPI</a><a class="linkbtn" href="/llms.txt" target="_blank" rel="noopener">智能体调用指南</a></div></section></div>`
  );
}
function render() {
  hideChartTip();
  const pages = {
      overview: "工作台",
      queue: "排队列表",
      receipts: "收款记录",
      settings: "设置",
    };
  $("#crumb").textContent = pages[state.page];
  document.title = pages[state.page] + " · 稿序";
  $$("nav [data-page]").forEach((a) => {
    a.classList.toggle("active", a.dataset.page === state.page);
    if (a.dataset.page === state.page) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  const count = state.count;
  $("#navCount").textContent = count;
  $("#navCount").hidden = !count;
  const views = {
    overview,
    queue,
    settings: settingsPage,
    receipts: receiptsPage,
  };
  $("#main").innerHTML = views[state.page]();
}

function choices(options, value) {
  return [
    ...new Set([
      ...(value && !options.includes(value) ? [value] : []),
      ...options,
    ]),
  ]
    .map(
      (v) =>
        `<option ${v === value ? "selected" : ""} value="${esc(v)}">${esc(v)}</option>`,
    )
    .join("");
}
function normalizeDay(raw) {
  let s = raw.trim();
  if (!s) return "";
  if (/^\d{6}$/.test(s)) s = "20" + s;
  if (/^\d{8}$/.test(s))
    s = s.slice(0, 4) + "-" + s.slice(4, 6) + "-" + s.slice(6);
  const match = s.match(/^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?$/);
  if (!match) return null;
  const [, year, month, day] = match;
  const iso = `${year}-${month.padStart(2, "0")}-${day.padStart(2, "0")}`;
  const check = new Date(iso + "T00:00:00Z");
  return Number.isFinite(+check) && check.toISOString().slice(0, 10) === iso
    ? iso
    : null;
}
function dateField(name, label, value) {
  return `<div class="field-label"><label>${label}<span class="datewrap"><input class="control day" name="${name}" aria-label="${label}" placeholder="如 260228" value="${esc(value)}" autocomplete="off"></span></label><span class="datepick"><input type="date" aria-label="选择${label}" value="${esc(value)}">${icon("calendar")}</span><small class="datehint"></small></div>`;
}
let stageMenuId = 0;
function setupStageMenu(row) {
  const input = row.querySelector('[name="stage"]');
  const menu = row.querySelector(".stage-menu");
  menu.id = "stage-options-" + ++stageMenuId;
  input.setAttribute("aria-controls", menu.id);
  let active = -1;
  function close() {
    menu.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    active = -1;
  }
  function open() {
    close();
    const options = ["稿费", "定金", "阶段款", "尾款"].filter(stage => stage.includes(input.value.trim()));
    if (!options.length) return;
    menu.innerHTML = options.map((stage, i) =>
      `<div role="option" id="${menu.id}-${i}" aria-selected="false">${stage}</div>`).join("");
    menu.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }
  function select(option) {
    input.value = option.textContent;
    close();
  }
  input.addEventListener("focus", open);
  input.addEventListener("click", open);
  input.addEventListener("input", open);
  input.addEventListener("blur", close);
  menu.addEventListener("pointerdown", e => e.preventDefault());
  menu.addEventListener("click", e => {
    if (e.target.matches('[role="option"]')) select(e.target);
  });
  input.addEventListener("keydown", e => {
    if (e.isComposing) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (menu.hidden) open();
      if (menu.hidden) return;
      e.preventDefault();
      const options = Array.from(menu.children);
      active = e.key === "ArrowDown" ? (active + 1) % options.length
        : (active <= 0 ? options.length - 1 : active - 1);
      options.forEach((option, i) => option.setAttribute("aria-selected", String(i === active)));
      input.setAttribute("aria-activedescendant", options[active].id);
    } else if (!menu.hidden && e.key === "Enter" && active >= 0) {
      e.preventDefault();
      select(menu.children[active]);
    } else if (!menu.hidden && e.key === "Escape") {
      e.preventDefault();
      close();
    }
  });
}
function paymentRow(p = {}) {
  const el = document.createElement("div");
  el.className = "payment";
  el.innerHTML = `<label class="field-label">金额（元）<input class="control" name="amount" type="number" min="0.01" step="0.01" placeholder="0.00" value="${esc(p.amount)}" required></label><div class="field-label stage-field"><label>稿期阶段<input class="control" name="stage" role="combobox" aria-expanded="false" aria-autocomplete="list" autocomplete="off" placeholder="稿费" value="${esc(p.stage)}"></label><div class="stage-menu" role="listbox" aria-label="稿期阶段推荐" hidden></div></div>${dateField("paydate", "支付日期", p.date || localDay())}<label class="field-label">支付方式<select class="control" name="platform">${choices(state.settings.platforms, p.platform)}</select></label><button class="iconbtn" type="button" data-action="remove-payment" aria-label="移除此笔稿费">${icon("x")}</button>`;
  setupStageMenu(el);
  return el;
}
function openEditor(key = null, before = null) {
  const r = key ? state.records.find((x) => x.id === key) : {};
  if (!r) return;
  editorGeneration++;
  clearTimeout(suggestTimer);
  editing = key;
  editingRevision = r.revision;
  insertBefore = before;
  attachments = (r.attachments || []).map((a) => ({ ...a }));
  readingFiles = 0;
  busy = false;
  $("#commissionForm").innerHTML =
    `<div class="dialog-head"><h2>${key ? "编辑约稿" : before ? "插入约稿" : "新建约稿"}</h2><button class="iconbtn" type="button" data-dismiss="editor" aria-label="关闭">${icon("x")}</button></div>
 <div class="dialog-scroll"><section><div class="formgrid"><div class="field-label span2 suggest-wrap"><label for="nickname">甲方昵称</label><input class="control" id="nickname" name="nickname" value="${esc(r.nickname)}" autocomplete="off" placeholder="输入昵称，或留空自动编号"><div class="suggestions" id="suggestions"></div></div><label class="field-label">联系方式<select class="control" name="contact_type">${choices(state.settings.contacts, r.contact_type)}</select></label><label class="field-label">联系账号<input class="control" name="contact" placeholder="账号或主页链接" value="${esc(r.contact)}"></label>${dateField("planned", "预计开始", r.planned)}${dateField("started", "实际开始", r.started)}<label class="field-label">结稿状态<select class="control" name="status">${choices(["未开始", "进行中", "已结稿"], r.status)}</select></label><label class="checkbox"><input type="checkbox" name="brief_received" ${r.brief_received ? "checked" : ""}>已交付需求</label>${key ? `<label class="checkbox"><input type="checkbox" name="is_priority" ${r.is_priority ? "checked" : ""}>插队</label>` : ""}</div></section>
 <section><div class="section-head"><h3>稿费</h3><button class="linkbtn" type="button" data-action="add-payment">${icon("plus")}添加稿费</button></div><div class="payments" id="payments"></div></section>
 <section><label class="field-label brief-field">约稿内容<input class="control" name="brief_title" placeholder="简短的需求标题" value="${esc(r.title)}"></label><div class="section-head"><h3>需求与参考图</h3><label class="linkbtn attach-label">${icon("image")}添加图片<input id="files" type="file" accept="image/png,image/jpeg,image/webp,image/gif" multiple></label></div><textarea aria-label="需求说明" name="requirements" placeholder="填写约稿需求">${esc(r.requirements)}</textarea><div class="thumbs" id="thumbs"></div></section></div>
 <div class="form-error" role="alert" id="formError"></div><div class="dialog-foot">${key ? '<button class="btn danger" type="button" data-action="delete">删除</button>' : ""}<button class="btn" type="button" data-dismiss="editor">取消</button><button id="save" class="btn primary">保存约稿</button></div>`;
  (r.payments || []).forEach((p) => $("#payments").append(paymentRow(p)));
  drawThumbs();
  $("#editor").showModal();
  $("#nickname").focus();
}
const imgSrc = (a) => a.data || "/api/images/" + encodeURIComponent(a.id);
function thumbnailButton(a, i) {
  const src = a.data || imgSrc(a) + "?thumbnail=true";
  return `<button type="button" data-action="preview" data-index="${i}"><img loading="lazy" src="${esc(src)}" alt="${esc(a.name)}"></button>`;
}
function drawThumbs() {
  $("#thumbs").innerHTML = attachments
    .map(
      (a, i) =>
        `<div class="thumb">${thumbnailButton(a, i)}<button type="button" class="remove" data-action="remove-image" data-index="${i}" aria-label="移除${esc(a.name)}">×</button></div>`,
    )
    .join("");
}
function fileData(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(Error("图片读取失败"));
    reader.readAsDataURL(file);
  });
}
async function addFiles(input) {
  const generation = editorGeneration,
    files = [...input.files];
  input.value = "";
  readingFiles++;
  $("#save").disabled = true;
  try {
    if (files.some((f) => f.size > 12 * 1024 * 1024))
      throw Error("单张图片最多 12 MB");
    const added = await Promise.all(
      files.map(async (f) => ({ name: f.name, data: await fileData(f) })),
    );
    if (generation === editorGeneration) {
      attachments.push(...added);
      drawThumbs();
      $("#formError").textContent = "";
    }
  } catch (error) {
    if (generation === editorGeneration)
      $("#formError").textContent = error.message;
  } finally {
    if (generation === editorGeneration) {
      readingFiles--;
      $("#save").disabled = busy || readingFiles > 0;
    }
  }
}
async function submitRecord(e) {
  e.preventDefault();
  if (busy || readingFiles) return;
  const form = e.target,
    f = form.elements;
  const invalid = $$(".day").find((el) => normalizeDay(el.value) === null);
  if (invalid) {
    invalid.setCustomValidity("请输入有效日期，例如 260228");
    invalid.reportValidity();
    return;
  }
  const payments = $$(".payment").map((row) => {
    const val = (n) => row.querySelector(`[name="${n}"]`).value;
    return {
      amount: val("amount"),
      stage: val("stage") || "稿费",
      date: normalizeDay(val("paydate")) || localDay(),
      platform: val("platform"),
    };
  });
  const data = {
    ...(!editing && state.page === "queue" && state.lane === "deferred"
      ? { queue: "deferred" }
      : {}),
    ...(editing ? { revision: editingRevision, is_priority: f.is_priority.checked } : {}),
    nickname: f.nickname.value,
    contact_type: f.contact_type.value,
    contact: f.contact.value,
    planned: normalizeDay(f.planned.value) || null,
    started: normalizeDay(f.started.value) || null,
    status: f.status.value,
    title: f.brief_title.value.trim(),
    requirements: f.requirements.value,
    brief_received: f.brief_received.checked,
    payments,
    attachments,
  };
  busy = true;
  $("#save").disabled = true;
  $("#formError").textContent = "";
  try {
    await api(
      editing
        ? "/commissions/" + editing
        : "/commissions" +
            (insertBefore ? "?before=" + encodeURIComponent(insertBefore) : ""),
      editing ? "PUT" : "POST",
      data,
    );
    $("#editor").close();
    await refresh();
    toast("约稿已保存");
  } catch (error) {
    if (error.status === 409) await refresh();
    if ($("#editor").open) $("#formError").textContent = error.message;
    else toast(error.message);
  } finally {
    busy = false;
    $("#save").disabled = false;
  }
}
let suggested = [];
async function showSuggestions() {
  const input = $("#nickname");
  if (!input || !$("#editor").open) return;
  const query = input.value.trim(),
    generation = editorGeneration;
  if (!query) {
    $("#suggestions").replaceChildren();
    return;
  }
  const found = await api("/clients?q=" + encodeURIComponent(query));
  if (
    generation !== editorGeneration ||
    query !== input.value.trim() ||
    !$("#editor").open ||
    document.activeElement !== input
  )
    return;
  suggested = found;
  $("#suggestions").innerHTML = found
    .map(
      (r, i) =>
        `<button type="button" data-action="suggest" data-index="${i}">${esc(r.nickname)}<small>${esc(r.contact_type)} ${esc(r.contact)}</small></button>`,
    )
    .join("");
}
$("#confirm").addEventListener("close", () => { $("#confirmYes").onclick = null; });
function confirmAction(title, message, action) {
  $("#confirmTitle").textContent = title;
  $("#confirmText").textContent = message;
  $("#confirmYes").onclick = async () => {
    const b = $("#confirmYes");
    b.disabled = true;
    try {
      await action();
      $("#confirm").close();
    } catch (error) {
      $("#confirmText").textContent = error.message;
    } finally {
      b.disabled = false;
    }
  };
  $("#confirm").showModal();
}
async function setOption(key, values) {
  await api("/settings", "PUT", { ...state.settings, [key]: values });
  await refresh();
  toast("设置已保存");
}
async function changePosition(key, before) {
  await api("/commissions/" + key + "/move", "POST", { before });
  await refresh();
  toast("顺序已更新");
}
async function onClick(event) {
  const dismiss = event.target.closest("[data-dismiss]");
  if (dismiss) {
    if (!(busy && dismiss.dataset.dismiss === "editor"))
      $("#" + dismiss.dataset.dismiss).close();
    return;
  }
  const b = event.target.closest("[data-action]");
  if (!b) return;
  const key = b.closest("[data-key]")?.dataset.key,
    action = b.dataset.action;
  switch (action) {
    case "cycle-income": {
      const kind = b.dataset.kind;
      const index = incomeWindows.indexOf(state.incomePeriod[kind]);
      state.incomePeriod[kind] = incomeWindows[(index + 1) % incomeWindows.length];
      const value = b.querySelector(".metric-value");
      value.innerHTML = metricContent(kind);
      if (!matchMedia("(prefers-reduced-motion: reduce)").matches) {
        value.animate([{ opacity: 0.35, transform: "translateY(5px)" }, { opacity: 1, transform: "none" }],
          { duration: 200, easing: "ease-out" });
      }
      break;
    }
    case "refresh":
      await refresh();
      break;
    case "new":
      openEditor();
      break;
    case "insert":
      openEditor(null, b.dataset.before);
      break;
    case "brief": {
      const panel = document.getElementById(b.getAttribute("aria-controls"));
      panel.hidden = !panel.hidden;
      b.setAttribute("aria-expanded", String(!panel.hidden));
      break;
    }
    case "detail":
      await openCommissionDetail(key);
      break;
    case "edit":
      if ($("#commissionDetail").open) $("#commissionDetail").close();
      openEditor(key);
      break;
    case "resume":
    case "defer":
      b.disabled = true;
      try {
        await api("/commissions/" + key + "/" + action, "POST");
        await refresh();
        toast(action === "defer" ? "已移入延期等待" : "已恢复至正常队列");
      } finally {
        b.disabled = false;
      }
      break;
    case "status":
      b.disabled = true;
      try {
        await api("/commissions/" + key + "/status", "PATCH", { status: b.dataset.status });
        await refresh();
        toast(b.dataset.status);
      } finally {
        b.disabled = false;
      }
      break;
    case "search-mode":
      state.showUnmatched = !state.showUnmatched;
      render();
      $('[data-action="search-mode"]').focus();
      break;
    case "open-lane":
      state.lane = b.dataset.lane;
      state.query = "";
      break;
    case "lane":
      state.lane = b.dataset.lane;
      render();
      break;
    case "load-examples":
      await api("/examples", "POST");
      await refresh();
      toast("示例已加载");
      break;
    case "clear-examples":
      confirmAction(
        "清除示例数据",
        "删除所有标有「示例」的约稿及其收款、附件，包括对示例的编辑。",
        async () => {
          await api("/examples", "DELETE");
          await refresh();
          toast("示例已清除");
        },
      );
      break;
    case "add-payment": {
      const row = paymentRow();
      $("#payments").append(row);
      row.querySelector("input").focus();
      break;
    }
    case "remove-payment":
      b.closest(".payment").remove();
      break;
    case "preview": {
      const source = key
        ? state.records.find((r) => r.id === key).attachments
        : attachments;
      const a = source[+b.dataset.index];
      $("#largeImage").src = imgSrc(a);
      $("#imageCaption").textContent = a.name;
      $("#lightbox").showModal();
      break;
    }
    case "remove-image":
      attachments.splice(+b.dataset.index, 1);
      drawThumbs();
      break;
    case "suggest": {
      const r = suggested[+b.dataset.index],
        f = $("#commissionForm").elements;
      f.nickname.value = r.nickname;
      f.contact.value = r.contact;
      f.contact_type.innerHTML = choices(
        state.settings.contacts,
        r.contact_type,
      );
      $("#suggestions").replaceChildren();
      break;
    }
    case "delete":
      confirmAction(
        "删除约稿",
        "该约稿的收款和图片也将一并删除。",
        async () => {
          await api("/commissions/" + editing, "DELETE");
          $("#editor").close();
          await refresh();
          toast("约稿已删除");
        },
      );
      break;
    case "remove-option": {
      const key = b.dataset.list;
      const values = state.settings[key].filter(
        (_, i) => i !== +b.dataset.index,
      );
      if (!values.length) {
        toast("请至少保留一个选项");
        return;
      }
      await setOption(key, values);
      break;
    }
  }
}
document.addEventListener("click", (e) => {
  if (!e.target.closest(".suggest-wrap")) $("#suggestions")?.replaceChildren();
  onClick(e).catch((error) => toast(error.message));
});
document.addEventListener("submit", async (e) => {
  if (e.target.id === "commissionForm") {
    submitRecord(e);
    return;
  }
  if (!e.target.matches(".inline-form")) return;
  e.preventDefault();
  const key = e.target.dataset.list,
    value = e.target.elements.value.value.trim();
  if (!value) return;
  try {
    await setOption(key, [...new Set([...state.settings[key], value])]);
  } catch (error) {
    toast(error.message);
  }
});
document.addEventListener("input", (e) => {
  const el = e.target;
  if (el.id === "receiptQuery") {
    state.receiptQuery = el.value;
    $("#receiptList").innerHTML = receiptList();
  }
  if (el.id === "query") {
    state.query = el.value;
    $(".toolbar .tabs").innerHTML = queueTabs();
    $("#queueList").innerHTML = queueList();
  }
  if (el.id === "nickname") {
    clearTimeout(suggestTimer);
    suggestTimer = setTimeout(
      () => showSuggestions().catch((err) => toast(err.message)),
      140,
    );
  }
  if (el.matches(".day")) {
    el.setCustomValidity("");
    const value = normalizeDay(el.value);
    el.closest(".field-label").querySelector(".datehint").textContent =
      value && value !== el.value ? value : "";
  }
});
document.addEventListener("change", async (e) => {
  const el = e.target;
  try {
    if (el.id === "months") {
      state.year = el.value.startsWith("y") ? Number(el.value.slice(1)) : null;
      if (!state.year) state.months = el.value === "all" ? "all" : Number(el.value);
      await refresh();
    }
    if (el.id === "files") await addFiles(el);
    if (el.closest(".datepick")) {
      const text = el.closest(".field-label").querySelector(".day");
      text.value = el.value;
      text.dispatchEvent(new Event("input", { bubbles: true }));
    }
    if (el.id === "importFile" && el.files[0]) {
      const file = el.files[0];
      el.value = "";
      confirmAction(
        "导入备份",
        `使用「${file.name}」中的数据替换当前全部约稿、收款、设置和图片？`,
        async () => {
          await api("/import", "POST", file);
          await refresh();
          toast("备份已恢复");
        },
      );
    }
  } catch (error) {
    toast(error.message);
  }
});
document.addEventListener("focusout", (e) => {
  if (e.target.matches(".day")) {
    const s = normalizeDay(e.target.value);
    if (s) {
      e.target.value = s;
      const wrap = e.target.closest(".field-label");
      wrap.querySelector("input[type=date]").value = s;
      wrap.querySelector(".datehint").textContent = "";
    }
  }
  if (
    e.target.closest(".suggest-wrap") &&
    !e.relatedTarget?.closest(".suggest-wrap")
  )
    setTimeout(() => {
      if (!document.activeElement.closest(".suggest-wrap"))
        $("#suggestions")?.replaceChildren();
    }, 0);
});
let drag = null;

function positionDrag() {
  const { rows, source, startY, pointerY, scrollStart } = drag;
  const offset = pointerY - startY + window.scrollY - scrollStart;
  const center = source.top + source.height / 2 + offset;
  const others = rows.filter(row => row !== source);
  const before = others.find(row => center < row.top + row.height / 2);
  drag.before = before?.card.dataset.key || null;
  const destination = before ? others.indexOf(before) : others.length;
  const origin = rows.indexOf(source);
  for (const [i, row] of rows.entries()) {
    let shift = 0;
    if (row === source) shift = offset;
    else if (origin < i && i <= destination) shift = -drag.step;
    else if (destination <= i && i < origin) shift = drag.step;
    row.card.style.transform = `translateY(${shift}px)`;
  }
}

function scrollDrag() {
  if (!drag) return;
  if (!drag.grip.isConnected) return clearDrag();
  const distance = drag.pointerY < 64 ? -10 : drag.pointerY > innerHeight - 64 ? 10 : 0;
  if (distance) {
    window.scrollBy(0, distance);
    positionDrag();
  }
  drag.frame = requestAnimationFrame(scrollDrag);
}

function clearDrag() {
  if (!drag) return;
  const { rows, source, grip, pointer, frame } = drag;
  drag = null;
  cancelAnimationFrame(frame);
  source.card.classList.remove("dragging");
  for (const { card } of rows) card.style.removeProperty("transform");
  if (grip.hasPointerCapture(pointer)) grip.releasePointerCapture(pointer);
}

document.addEventListener("pointerdown", e => {
  const grip = e.target.closest(".grip");
  if (!grip || e.button !== 0 || !e.isPrimary || drag) return;
  const rows = $$(".queue-card").map(card => {
    const { top, height } = card.getBoundingClientRect();
    return { card, top, height };
  });
  const source = rows.find(row => row.card.contains(grip));
  if (!source || rows.length < 2) return;
  drag = {
    rows, source, grip, pointer: e.pointerId, startY: e.clientY, pointerY: e.clientY,
    scrollStart: window.scrollY, step: source.height + rows[1].top - rows[0].top - rows[0].height,
    before: null, frame: 0
  };
  grip.setPointerCapture(e.pointerId);
  source.card.classList.add("dragging");
  positionDrag();
  drag.frame = requestAnimationFrame(scrollDrag);
});

document.addEventListener("pointermove", e => {
  if (drag?.pointer !== e.pointerId) return;
  drag.pointerY = e.clientY;
  positionDrag();
});
document.addEventListener("pointerup", async e => {
  if (drag?.pointer !== e.pointerId) return;
  drag.pointerY = e.clientY;
  positionDrag();
  const { rows, source, before, step } = drag;
  const originalNext = rows[rows.indexOf(source) + 1]?.card.dataset.key || null;
  // Hold the preview in its dropped position until the saved order is rendered.
  const next = rows.find(row => row.card.dataset.key === before);
  const last = rows[rows.length - 1];
  const landingTop = next
    ? next.top - (next.top > source.top ? step : 0)
    : last.top + last.height - source.height;
  const preview = rows.map(({card}) => card.style.transform);
  clearDrag();
  if (before === originalNext) return;
  rows.forEach(({card}, i) => { card.style.transform = preview[i]; });
  source.card.style.transform = `translateY(${landingTop - source.top}px)`;
  try {
    await changePosition(source.card.dataset.key, before);
  } catch (error) {
    toast(error.message);
  } finally {
    rows.forEach(({card}) => card.style.removeProperty("transform"));
  }
});
for (const event of ["pointercancel", "lostpointercapture"]) {
  document.addEventListener(event, e => {
    if (drag?.pointer === e.pointerId) clearDrag();
  });
}
window.addEventListener("blur", clearDrag);
document.addEventListener("keydown", e => {
  if (e.key === "Escape") clearDrag();
});
document.addEventListener("keydown", async (e) => {
  const grip = e.target.closest(".grip");
  if (!grip || !["ArrowUp", "ArrowDown"].includes(e.key)) return;
  e.preventDefault();
  const key = grip.closest(".queue-card").dataset.key,
    keys = queueRecords(state.lane).map((x) => x.id),
    i = keys.indexOf(key);
  if (
    (e.key === "ArrowUp" && i === 0) ||
    (e.key === "ArrowDown" && i === keys.length - 1)
  )
    return;
  try {
    await changePosition(
      key,
      e.key === "ArrowUp" ? keys[i - 1] : keys[i + 2] || null,
    );
    document.querySelector(`[data-key="${key}"] .grip`)?.focus();
  } catch (error) {
    toast(error.message);
  }
});
$("#editor").addEventListener("cancel", (e) => {
  if (busy) e.preventDefault();
});
$("#editor").addEventListener("close", () => {
  editorGeneration++;
  clearTimeout(suggestTimer);
});
$$("dialog").forEach((d) =>
  d.addEventListener("click", (e) => {
    if (e.target !== d || busy) return;
    const r = d.getBoundingClientRect();
    if (
      e.clientX < r.left ||
      e.clientX > r.right ||
      e.clientY < r.top ||
      e.clientY > r.bottom
    )
      d.close();
  }),
);
$$("[data-icon]").forEach((el) => (el.outerHTML = icon(el.dataset.icon)));
$("#clock").innerHTML =
  icon("calendar") +
  new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "long",
    day: "numeric",
    weekday: "short",
  }).format(new Date());
window.addEventListener("hashchange", () => {
  $("#main").textContent = "加载中…";
  $("#main").setAttribute("aria-busy", "true");
  refresh().catch(showLoadError);
  window.scrollTo(0, 0);
});
function showLoadError(err) {
  $("#main").removeAttribute("aria-busy");
  $("#main").innerHTML =
    `<div class="card"><p>${esc(err.message)}</p>${button("重新加载", "refresh")}</div>`;
}
refresh().catch(showLoadError);
