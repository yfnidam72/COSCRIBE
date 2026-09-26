"use strict";

/* ================================================================ helpers */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

async function api(path, opts = {}) {
  const init = { ...opts };
  if (opts.json !== undefined) {
    init.method = init.method || "POST";
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(opts.json);
  }
  const r = await fetch(path, init);
  let data = null;
  try { data = await r.json(); } catch { /* empty body */ }
  if (!r.ok) throw new Error((data && data.detail) || `Request failed (${r.status})`);
  return data;
}

function fmt(t, withTenths = false) {
  t = Math.max(0, t || 0);
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = Math.floor(t % 60);
  const base = h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
  return withTenths ? `${base}.${Math.floor((t % 1) * 10)}` : base;
}

function debounce(fn, ms) {
  let tm;
  return (...a) => { clearTimeout(tm); tm = setTimeout(() => fn(...a), ms); };
}

function hexToRgba(hex, a) {
  const n = parseInt(hex.replace("#", ""), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ================================================================ i18n */
let UI = "en";
function t(key, vars = {}) {
  let s = (I18N[UI] && I18N[UI][key]) ?? I18N.en[key] ?? key;
  for (const [k, v] of Object.entries(vars)) s = s.replaceAll(`{${k}}`, v);
  return s;
}
const langName = (code) => (LANG_NAMES[UI] && LANG_NAMES[UI][code]) || (LANG_NAMES.en[code]) || code || "—";

function applyI18n() {
  document.documentElement.lang = UI;
  document.documentElement.dir = UI === "ar" ? "rtl" : "ltr";
  $$("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  $$("[data-i18n-html]").forEach((el) => { el.innerHTML = t(el.dataset.i18nHtml); });
  $$("[data-i18n-ph]").forEach((el) => { el.placeholder = t(el.dataset.i18nPh); });
  $$("[data-i18n-title]").forEach((el) => { el.title = t(el.dataset.i18nTitle); });
}

let toastTimer;
function toast(msg, err = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.toggle("err", err);
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), err ? 6500 : 3200);
}

/* ================================================================ state */
const DEFAULT_STYLE = {
  font: "Cairo", size: 6, bold: true, uppercase: false, color: "#FFFFFF",
  bg: "box", bgColor: "#000000", bgOpacity: 70, pad: 0.3, outline: 0.08,
  position: "bottom", offset: 8, sideMargin: 8,
  show: "translation", secondarySize: 70, secondaryColor: "#FFD84D",
};
const STYLE_KEYS = Object.keys(DEFAULT_STYLE).filter((k) => k !== "show");
const TEXT_COLORS = ["#FFFFFF", "#FFD84D", "#111111", "#7CF2B4", "#7FD4FF", "#FF7A8A"];
const BG_COLORS = ["#000000", "#FFFFFF", "#5B45F0", "#E63946", "#0F2A24", "#FFD84D"];
const SEC_COLORS = ["#FFD84D", "#FFFFFF", "#CFC9FF", "#7FD4FF", "#BBBBBB"];

const S = {
  meta: null,
  settings: null,
  userThemes: [],
  projects: [],
  project: null,
  style: { ...DEFAULT_STYLE },
  engine: "gemma",
  exportRes: "source",
  subs: new Set(["srt"]),
  job: null,
  active: -1,
  focusIdx: -1,
  ratios: {},
  undo: [],
  redo: [],
  lib: { q: "", filter: "all", sort: "updated" },
};
const video = $("#video");

/* ================================================================ boot */
async function boot() {
  [S.meta, S.settings, S.userThemes] = await Promise.all([api("/api/meta"), api("/api/settings"), api("/api/themes")]);
  S.ratios = S.meta.ratios || {};
  S.engine = S.settings.defaultEngine || "gemma";
  S.exportRes = S.settings.exportResolution || "source";
  UI = S.settings.uiLang || "en";
  applyAppearance();
  buildStaticUi();
  wireGlobal();
  wireLibrary();
  wireEditor();
  wireSettings();
  applyI18n();
  refreshLangSelects();
  window.addEventListener("resize", layoutFrame);
  window.addEventListener("hashchange", route);
  await document.fonts.ready;
  route();
}

function route() {
  closeMenu();
  const h = location.hash;
  const m = h.match(/^#\/p\/([a-f0-9]{12})(?:\/(captions|style|export))?$/);
  if (m) { showView("editor"); openProject(m[1]).then(() => m[2] && switchTab(m[2])); return; }
  leaveEditor();
  if (h === "#/themes") {
    showView("themes");
    // Tiles use a real project frame as backdrop when one exists.
    (S.projects.length ? Promise.resolve() : api("/api/projects").then((l) => { S.projects = l; }))
      .catch(() => {}).then(renderThemeGallery);
    return;
  }
  showView("library");
  loadLibrary();
}

function showView(name) {
  for (const v of ["library", "themes", "editor"]) $(`#${v}`).hidden = v !== name;
  $$(".rail-btn[data-nav]").forEach((b) => b.classList.toggle("on", b.dataset.nav === name));
}

function leaveEditor() {
  if (!S.project) return;
  $("#railEditor").hidden = false;
  $("#railEditor").href = `#/p/${S.project.id}`;
  video.pause();
  stopPolling();
}

/* ================================================================ appearance + settings */
function applyAppearance() {
  const pref = S.settings.appearance || "dark";
  const dark = pref === "system" ? matchMedia("(prefers-color-scheme: dark)").matches : pref === "dark";
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}

async function saveSettings(patch) {
  S.settings = await api("/api/settings", { method: "PUT", json: patch });
}

function wireSettings() {
  $("#settingsBtn").addEventListener("click", openSettings);
  segWire("#uiLangSeg", async (v) => {
    UI = v;
    await saveSettings({ uiLang: v });
    applyI18n();
    refreshLangSelects();
    rebuildAfterLang();
    fillSettings();
  });
  segWire("#appearanceSeg", async (v) => { await saveSettings({ appearance: v }); applyAppearance(); });
  segWire("#setEngine", async (v) => { await saveSettings({ defaultEngine: v }); });
  $("#setTarget").addEventListener("change", (e) => saveSettings({ defaultTarget: e.target.value }));
  $("#setTheme").addEventListener("change", (e) => saveSettings({ defaultTheme: e.target.value || null }));
  $("#openOut").addEventListener("click", () => api("/api/reveal-output", { json: {} }).catch((e) => toast(e.message, true)));
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyAppearance);
}

function openSettings() {
  fillSettings();
  openModal("#settingsModal");
}

function fillSettings() {
  setSeg("#uiLangSeg", UI);
  setSeg("#appearanceSeg", S.settings.appearance || "dark");
  setSeg("#setEngine", S.settings.defaultEngine || "gemma");
  fillLangSelect($("#setTarget"), { selected: S.settings.defaultTarget || "ar" });
  const sel = $("#setTheme");
  sel.innerHTML = "";
  sel.add(new Option(t("noneTheme"), ""));
  for (const th of [...S.userThemes, ...BUILTIN_THEMES]) sel.add(new Option(th.name, th.id));
  sel.value = S.settings.defaultTheme || "";
  $("#outDir2").textContent = S.meta.output;
}

function rebuildAfterLang() {
  buildKeysList();
  buildFontGrid();
  if (!$("#library").hidden) renderLibrary();
  if (!$("#themes").hidden) renderThemeGallery();
  if (S.project) { renderAll(); showEngine(); }
  renderThemeStrip();
}

/* ================================================================ modals + menus */
function openModal(sel) {
  const m = $(sel);
  m.hidden = false;
  const first = $("input, select, button:not(.x)", m);
  if (first) setTimeout(() => first.focus(), 30);
}
function closeModals() { $$(".modal").forEach((m) => { m.hidden = true; }); }

let askResolve = null;
function ask({ title, text = "", value = null, okLabel = t("ok"), danger = false }) {
  $("#askTitle").textContent = title;
  $("#askText").textContent = text;
  $("#askText").hidden = !text;
  const inp = $("#askInput");
  inp.hidden = value === null;
  inp.value = value ?? "";
  $("#askOk").textContent = okLabel;
  $("#askOk").classList.toggle("danger", danger);
  openModal("#askModal");
  if (value !== null) setTimeout(() => { inp.focus(); inp.select(); }, 40);
  else setTimeout(() => $("#askOk").focus(), 40);
  return new Promise((res) => { askResolve = res; });
}
function finishAsk(v) {
  $("#askModal").hidden = true;
  if (askResolve) { const r = askResolve; askResolve = null; r(v); }
}

function openMenu(anchor, items) {
  const menu = $("#menu");
  menu.innerHTML = "";
  for (const it of items) {
    if (!it) continue;
    const b = document.createElement("button");
    b.textContent = it.label;
    if (it.danger) b.className = "danger";
    b.addEventListener("click", (e) => { e.stopPropagation(); closeMenu(); it.run(); });
    menu.append(b);
  }
  menu.hidden = false;
  const r = anchor.getBoundingClientRect();
  const w = menu.offsetWidth, h = menu.offsetHeight;
  const rtl = document.documentElement.dir === "rtl";
  let x = rtl ? r.left : r.right - w;
  let y = r.bottom + 6;
  if (y + h > innerHeight - 8) y = r.top - h - 6;
  menu.style.left = `${Math.max(8, Math.min(x, innerWidth - w - 8))}px`;
  menu.style.top = `${Math.max(8, y)}px`;
}
function closeMenu() { $("#menu").hidden = true; }

/* ================================================================ global wiring */
function wireGlobal() {
  document.addEventListener("click", (e) => {
    if (!e.target.closest("#menu") && !e.target.closest("[data-menu]")) closeMenu();
    if (e.target.closest("[data-close]")) {
      const modal = e.target.closest(".modal");
      if (modal && modal.id === "askModal") finishAsk(null);
      else if (modal) modal.hidden = true;
    }
    if (e.target.classList.contains("modal")) {
      if (e.target.id === "askModal") finishAsk(null); else e.target.hidden = true;
    }
  });
  $("#askForm").addEventListener("submit", (e) => {
    e.preventDefault();
    finishAsk($("#askInput").hidden ? true : $("#askInput").value.trim());
  });
  $("#keysBtn").addEventListener("click", () => openModal("#keysModal"));

  // Drop a video anywhere in the app to start a new project.
  let depth = 0;
  const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
  document.addEventListener("dragenter", (e) => { if (!hasFiles(e)) return; e.preventDefault(); depth++; $("#dropover").hidden = false; });
  document.addEventListener("dragleave", (e) => { if (!hasFiles(e)) return; depth = Math.max(0, depth - 1); if (!depth) $("#dropover").hidden = true; });
  document.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
  document.addEventListener("drop", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth = 0;
    $("#dropover").hidden = true;
    const f = e.dataTransfer.files[0];
    if (f) upload(f);
  });

  document.addEventListener("keydown", onKey);
}

function onKey(e) {
  if (e.key === "Escape") { closeMenu(); if (!$("#askModal").hidden) finishAsk(null); closeModals(); return; }
  const tag = document.activeElement && document.activeElement.tagName;
  const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(tag);
  const inEditor = !$("#editor").hidden;
  const mod = e.ctrlKey || e.metaKey;
  if (mod && e.key.toLowerCase() === "f" && inEditor) { e.preventDefault(); switchTab("captions"); $("#findInput").focus(); return; }
  if (mod && e.key.toLowerCase() === "e" && inEditor) { e.preventDefault(); switchTab("export"); return; }
  if (typing) return;
  if ($(".modal:not([hidden])")) return;
  if (e.key === "?") { openModal("#keysModal"); return; }
  if (!inEditor) {
    if (e.key.toLowerCase() === "n" && !$("#library").hidden) $("#fileInput").click();
    return;
  }
  if (mod && e.key.toLowerCase() === "z") { e.preventDefault(); e.shiftKey ? doRedo() : doUndo(); return; }
  if (mod && e.key.toLowerCase() === "y") { e.preventDefault(); doRedo(); return; }
  if (e.code === "Space") { e.preventDefault(); togglePlay(); }
  if (e.code === "ArrowLeft") video.currentTime -= 2;
  if (e.code === "ArrowRight") video.currentTime += 2;
  if (e.key === "[") setEdge("start");
  if (e.key === "]") setEdge("end");
}

function buildKeysList() {
  const rows = [
    ["Space", t("kPlay")], ["← →", t("kSeek")], ["Ctrl Z · Ctrl ⇧ Z", t("kUndo")], ["[  ]", t("kStartEnd")],
    ["Ctrl F", t("kFind")], ["Ctrl E", t("kExport")], ["N", t("kNew")], ["?", t("kHelp")], ["Esc", t("kEsc")],
  ];
  $("#keysList").innerHTML = rows.map(([k, d]) =>
    `<div class="key-row"><span>${esc(d)}</span><span class="kbds">${k.split(" · ").map((x) => `<kbd dir="ltr">${esc(x)}</kbd>`).join(" ")}</span></div>`).join("");
}

/* ================================================================ static UI builders */
function buildStaticUi() {
  $("#outDir").textContent = S.meta.output;
  buildKeysList();
  buildStyleControls();
  setSeg("#resSeg", S.exportRes);
  $$("[data-sub]").forEach((b) => b.classList.toggle("on", S.subs.has(b.dataset.sub)));
}

function fillLangSelect(sel, { asrOnly = false, exclude = null, selected = null, auto = false } = {}) {
  sel.innerHTML = "";
  if (auto) sel.add(new Option(t("detectAuto"), ""));
  const langs = S.meta.languages.filter((l) => (!asrOnly || l.asr) && l.code !== exclude);
  const main = S.meta.main.filter((c) => langs.some((l) => l.code === c));
  const label = (c) => langName(c) + (S.meta.experimental.includes(c) ? ` (${t("experimental")})` : "");
  const g1 = document.createElement("optgroup");
  g1.label = t("mainLanguages");
  for (const c of main) g1.append(new Option(label(c), c));
  const g2 = document.createElement("optgroup");
  g2.label = t("moreLanguages");
  langs.filter((l) => !main.includes(l.code))
    .sort((a, b) => label(a.code).localeCompare(label(b.code), UI))
    .forEach((l) => g2.append(new Option(label(l.code), l.code)));
  sel.append(g1, g2);
  if (selected !== null && [...sel.options].some((o) => o.value === selected)) sel.value = selected;
}

function refreshLangSelects() {
  const spoken = $("#spokenLang").value;
  fillLangSelect($("#spokenLang"), { asrOnly: true, auto: true, selected: spoken || "" });
  fillLangSelect($("#reLang"), { asrOnly: true, selected: (S.project && S.project.language) || "en" });
  if (S.project) fillTargets($("#targetLang").value || null);
}

/* ================================================================ library */
function wireLibrary() {
  const input = $("#fileInput");
  $("#newBtn").addEventListener("click", () => input.click());
  input.addEventListener("change", () => { if (input.files[0]) upload(input.files[0]); input.value = ""; });
  $("#libSearch").addEventListener("input", (e) => { S.lib.q = e.target.value.trim().toLowerCase(); renderLibrary(); });
  $("#libSort").addEventListener("change", (e) => { S.lib.sort = e.target.value; renderLibrary(); });
  $$("#libFilter button").forEach((b) => b.addEventListener("click", () => {
    S.lib.filter = b.dataset.v;
    $$("#libFilter button").forEach((x) => x.classList.toggle("on", x === b));
    renderLibrary();
  }));
  $("#grid").addEventListener("click", onGridClick);
}

let libTimer = null;
async function loadLibrary() {
  clearTimeout(libTimer);
  try { S.projects = await api("/api/projects"); } catch (e) { toast(e.message, true); return; }
  renderLibrary();
  // Keep progress bars on busy cards moving.
  if (S.projects.some((p) => p.job) && !$("#library").hidden) libTimer = setTimeout(loadLibrary, 1500);
}

function statusOf(p) {
  if (p.job) return ["busy", t("statusNew")];
  if (p.exported) return ["ok", t("statusExported")];
  if (p.target) return ["tr", t("statusTranslated")];
  if (p.captions) return ["", t("statusCaptioned")];
  return ["", t("statusNew")];
}

function ago(ts) {
  const s = Date.now() / 1000 - (ts || 0);
  if (s < 60) return t("justNow");
  if (s < 3600) return t("minutesAgo", { n: Math.floor(s / 60) });
  if (s < 86400) return t("hoursAgo", { n: Math.floor(s / 3600) });
  return t("daysAgo", { n: Math.floor(s / 86400) });
}

function renderLibrary() {
  const all = S.projects;
  $("#libCount").textContent = all.length === 1 ? t("projectCount") : t("projectsCount", { n: all.length });
  const empty = !all.length && !uploading;
  $("#emptyHero").hidden = !empty;
  $("#libTools").hidden = empty;
  let list = all.filter((p) => {
    if (S.lib.q && !p.name.toLowerCase().includes(S.lib.q)) return false;
    if (S.lib.filter === "progress") return !p.target && !p.exported;
    if (S.lib.filter === "translated") return !!p.target;
    if (S.lib.filter === "exported") return !!p.exported;
    return true;
  });
  const key = S.lib.sort;
  list = [...list].sort((a, b) => key === "name" ? a.name.localeCompare(b.name, UI)
    : key === "duration" ? (b.duration || 0) - (a.duration || 0) : (b[key] || 0) - (a[key] || 0));
  $("#noneFound").hidden = !(all.length && !list.length);

  const grid = $("#grid");
  grid.innerHTML = "";
  if (uploading) grid.append(uploadCard());
  for (const p of list) {
    const [cls, label] = statusOf(p);
    const langs = p.language ? `${langName(p.language)}${p.target ? " → " + langName(p.target) : ""}` : "—";
    const card = document.createElement("article");
    card.className = "card-p";
    card.dataset.id = p.id;
    card.tabIndex = 0;
    card.innerHTML = `
      <div class="thumb">
        <img loading="lazy" alt="" src="/thumb/${p.id}?v=${Math.floor(p.created || 0)}" onerror="this.remove()">
        <span class="dur" dir="ltr">${fmt(p.duration)}</span>
        ${p.job ? `<div class="thumb-job"><div class="bar"><div style="width:${Math.round(p.job.progress * 100)}%"></div></div><span>${esc(p.job.message || "")}</span></div>` : ""}
      </div>
      <div class="card-body">
        <div class="card-title"><strong title="${esc(p.name)}">${esc(p.name)}</strong>
          <button class="kebab" data-menu aria-label="More">
            <svg viewBox="0 0 24 24" class="ico"><circle cx="12" cy="5.5" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="12" cy="18.5" r="1.3"/></svg>
          </button></div>
        <div class="card-meta"><span>${esc(langs)}</span><span>·</span><span>${t("captionsN", { n: p.captions })}</span></div>
        <div class="card-foot"><span class="pill ${cls}">${esc(label)}</span><span class="muted">${ago(p.updated)}</span></div>
      </div>`;
    grid.append(card);
  }
}

function onGridClick(e) {
  const card = e.target.closest(".card-p");
  if (!card || !card.dataset.id) return;
  const p = S.projects.find((x) => x.id === card.dataset.id);
  if (e.target.closest(".kebab")) {
    e.stopPropagation();
    openMenu(e.target.closest(".kebab"), [
      { label: t("open"), run: () => { location.hash = `#/p/${p.id}`; } },
      { label: t("rename"), run: () => renameProject(p) },
      { label: t("duplicate"), run: async () => { await api(`/api/projects/${p.id}/duplicate`, { json: {} }); loadLibrary(); } },
      p.exported && { label: t("showExport"), run: () => api(`/api/projects/${p.id}/reveal`, { json: {} }) },
      { label: t("delete"), danger: true, run: () => deleteProject(p) },
    ]);
    return;
  }
  location.hash = `#/p/${p.id}`;
}

async function renameProject(p) {
  const name = await ask({ title: t("renameTitle"), value: p.name, okLabel: t("save") });
  if (!name) return;
  await api(`/api/projects/${p.id}`, { method: "PUT", json: { name } });
  if (S.project && S.project.id === p.id) { S.project.name = name; $("#projName").value = name; }
  loadLibrary();
}

async function deleteProject(p) {
  const ok = await ask({ title: t("delete"), text: t("confirmDelete", { name: p.name }), okLabel: t("delete"), danger: true });
  if (!ok) return;
  await api(`/api/projects/${p.id}`, { method: "DELETE" });
  if (S.project && S.project.id === p.id) { S.project = null; $("#railEditor").hidden = true; }
  if (location.hash.startsWith("#/p/")) location.hash = "#/"; else loadLibrary();
}

let uploading = null;
function uploadCard() {
  const c = document.createElement("article");
  c.className = "card-p uploading";
  c.innerHTML = `<div class="thumb"><div class="thumb-job"><div class="bar"><div style="width:${uploading.pct}%"></div></div>
    <span>${esc(t("importing", { name: uploading.name }))}</span></div></div>
    <div class="card-body"><div class="card-title"><strong>${esc(uploading.name)}</strong></div></div>`;
  return c;
}

function upload(file) {
  if (!/^video\//.test(file.type) && !/\.(mp4|mov|mkv|webm|m4v|avi)$/i.test(file.name)) { toast(t("notVideo"), true); return; }
  if (location.hash !== "#/" && location.hash !== "") location.hash = "#/";
  uploading = { name: file.name, pct: 0 };
  showView("library");
  renderLibrary();
  const fd = new FormData();
  fd.append("file", file);
  const xhr = new XMLHttpRequest();
  xhr.open("POST", `/api/projects?language=${encodeURIComponent($("#spokenLang").value)}`);
  xhr.upload.onprogress = (e) => {
    if (!e.lengthComputable) return;
    uploading.pct = Math.round((e.loaded / e.total) * 100);
    const bar = $(".card-p.uploading .bar > div");
    if (bar) bar.style.width = `${uploading.pct}%`;
  };
  xhr.onload = () => {
    let data = {};
    try { data = JSON.parse(xhr.responseText); } catch { /* ignore */ }
    uploading = null;
    if (xhr.status !== 200) { toast(data.detail || t("importFailed"), true); renderLibrary(); return; }
    location.hash = `#/p/${data.project.id}`;
  };
  xhr.onerror = () => { uploading = null; renderLibrary(); toast(t("importFailed"), true); };
  xhr.send(fd);
}

/* ================================================================ caption renderer (shared) */
function linesFor(c, style) {
  const orig = (c.text || "").trim(), tr = (c.tr || "").trim();
  if (style.show === "original" || (style.show === "translation" && !tr)) return [orig];
  if (style.show === "translation") return [tr];
  return tr ? [tr, orig] : [orig];
}

/* Builds the caption block exactly as engine.to_ass lays it out (sizes are % of frame height). */
function buildCap(style, lines, fw, fh, zoom = 1) {
  const st = { ...DEFAULT_STYLE, ...style };
  // zoom only enlarges the text (theme thumbnails); layout stays in frame proportions.
  const em = (st.size / 100) * fh * zoom;
  const ratio = S.ratios[st.font] || 1.2;
  const pad = st.pad * em;
  const box = document.createElement("div");
  box.className = "cap";
  const side = (st.sideMargin / 100) * fw;
  box.style.left = `${side}px`;
  box.style.right = `${side}px`;
  let mv = (st.offset / 100) * fh;
  if (st.bg === "box") mv = Math.max(mv, pad);
  if (st.position === "bottom") box.style.bottom = `${mv}px`;
  else if (st.position === "top") box.style.top = `${mv}px`;
  else { box.style.top = "50%"; box.style.transform = "translateY(-50%)"; }
  lines.forEach((text, n) => {
    if (!text) return;
    const wrap = document.createElement("div");
    wrap.style.fontSize = `${n === 0 ? em : em * (st.secondarySize / 100)}px`;
    wrap.style.lineHeight = String(ratio);
    const span = document.createElement("span");
    span.className = "ln";
    span.dir = "auto";
    span.textContent = st.uppercase ? text.toUpperCase() : text;
    span.style.fontFamily = `"${st.font}", "Noto Sans Tifinagh", "Segoe UI", sans-serif`;
    span.style.fontWeight = st.bold ? "700" : "400";
    span.style.color = n === 0 ? st.color : st.secondaryColor;
    if (st.bg === "box") {
      span.style.background = hexToRgba(st.bgColor, st.bgOpacity / 100);
      span.style.padding = `${pad}px`;
    } else if (st.bg === "outline") {
      span.style.webkitTextStroke = `${st.outline * em * 2}px ${st.bgColor}`;
      span.style.paintOrder = "stroke fill";
    } else if (st.bg === "shadow") {
      const d = Math.max(1, em * 0.06);
      span.style.textShadow = `${d}px ${d}px 0 ${hexToRgba(st.bgColor, st.bgOpacity / 100)}`;
    }
    wrap.append(span);
    box.append(wrap);
  });
  return box;
}

/* ================================================================ themes */
const allThemes = () => [...S.userThemes, ...BUILTIN_THEMES];
const themeStyle = (th) => ({ ...DEFAULT_STYLE, ...th.s, ...(th.style || {}) });

function themeTile(th, { big = false } = {}) {
  const tile = document.createElement("div");
  tile.className = "theme-tile" + (big ? " big" : "");
  tile.dataset.id = th.id;
  const st = themeStyle(th);
  const matches = S.project && STYLE_KEYS.every((k) => S.style[k] === st[k]);
  if (matches) tile.classList.add("on");
  const isDefault = S.settings.defaultTheme === th.id;
  const bgUrl = S.project ? `/thumb/${S.project.id}` : (S.projects[0] ? `/thumb/${S.projects[0].id}` : null);
  tile.innerHTML = `<div class="tt-frame" ${bgUrl ? `style="background-image:url('${bgUrl}')"` : ""}></div>
    <div class="tt-foot"><span class="tt-name">${esc(th.name)}</span>
      ${isDefault ? `<span class="tt-badge" title="${esc(t("isDefault"))}">★</span>` : ""}
      ${big ? `<button class="kebab" data-menu aria-label="More"><svg viewBox="0 0 24 24" class="ico"><circle cx="12" cy="5.5" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="12" cy="18.5" r="1.3"/></svg></button>` : ""}
    </div>`;
  const frame = $(".tt-frame", tile);
  requestAnimationFrame(() => {
    const fw = frame.clientWidth, fh = frame.clientHeight;
    if (!fw) return;
    frame.append(buildCap({ ...st, show: "both" }, [t("sampleLine1"), t("sampleLine2")], fw, fh, big ? 2.1 : 1.9));
  });
  return tile;
}

function applyTheme(th) {
  if (!S.project) { toast(t("openProjectFirst")); return false; }
  const st = themeStyle(th);
  for (const k of STYLE_KEYS) S.style[k] = st[k];
  styleChanged();
  pushTo("style");
  toast(t("themeApplied", { name: th.name }));
  return true;
}

function renderThemeStrip() {
  const strip = $("#themeStrip");
  strip.innerHTML = "";
  for (const th of allThemes().slice(0, 9)) strip.append(themeTile(th));
}

function renderThemeGallery() {
  const g = $("#themeGallery");
  g.innerHTML = "";
  const section = (title, themes, emptyText) => {
    const sec = document.createElement("section");
    sec.className = "theme-sec";
    sec.innerHTML = `<h2>${esc(title)}</h2><div class="theme-grid"></div>`;
    const grid = $(".theme-grid", sec);
    if (!themes.length && emptyText) grid.outerHTML = `<p class="muted">${esc(emptyText)}</p>`;
    themes.forEach((th) => grid.append(themeTile(th, { big: true })));
    g.append(sec);
  };
  section(t("myThemes"), S.userThemes, t("noMyThemes"));
  for (const cat of THEME_CATS) section(t(cat), BUILTIN_THEMES.filter((x) => x.cat === cat));
}

function wireThemes() {
  const onTile = (e) => {
    const tile = e.target.closest(".theme-tile");
    if (!tile) return;
    const th = allThemes().find((x) => x.id === tile.dataset.id);
    if (!th) return;
    if (e.target.closest(".kebab")) {
      e.stopPropagation();
      const mine = S.userThemes.some((x) => x.id === th.id);
      openMenu(e.target.closest(".kebab"), [
        { label: t("applyTheme"), run: () => { if (applyTheme(th)) location.hash = `#/p/${S.project.id}/style`; } },
        { label: t("setDefault"), run: async () => { await saveSettings({ defaultTheme: th.id }); renderThemeGallery(); } },
        mine && { label: t("rename"), run: () => renameTheme(th) },
        mine && { label: t("delete"), danger: true, run: () => deleteTheme(th) },
      ]);
      return;
    }
    if (!S.project) {
      // Nothing open to style: the most useful meaning of the click is "use this from now on".
      saveSettings({ defaultTheme: th.id }).then(() => { renderThemeGallery(); toast(`${th.name} — ${t("isDefault")}`); });
      return;
    }
    if (applyTheme(th) && !$("#themes").hidden) location.hash = `#/p/${S.project.id}/style`;
    else renderThemeStrip();
  };
  $("#themeGallery").addEventListener("click", onTile);
  $("#themeStrip").addEventListener("click", onTile);
  $("#saveThemeBtn").addEventListener("click", async () => {
    const name = await ask({ title: t("saveThemeTitle"), text: t("themeName"), value: "", okLabel: t("save") });
    if (!name) return;
    const style = Object.fromEntries(STYLE_KEYS.map((k) => [k, S.style[k]]));
    const th = await api("/api/themes", { json: { name, style } });
    S.userThemes.unshift(th);
    renderThemeStrip();
    toast(t("themeSaved", { name }));
  });
}

async function renameTheme(th) {
  const name = await ask({ title: t("renameTheme"), value: th.name, okLabel: t("save") });
  if (!name) return;
  const upd = await api(`/api/themes/${th.id}`, { method: "PUT", json: { name } });
  Object.assign(th, upd);
  renderThemeGallery();
}

async function deleteTheme(th) {
  if (!await ask({ title: t("delete"), text: t("deleteTheme", { name: th.name }), okLabel: t("delete"), danger: true })) return;
  await api(`/api/themes/${th.id}`, { method: "DELETE" });
  S.userThemes = S.userThemes.filter((x) => x.id !== th.id);
  if (S.settings.defaultTheme === th.id) await saveSettings({ defaultTheme: null });
  renderThemeGallery();
}

/* ================================================================ editor: open */
async function openProject(id) {
  let p;
  try { p = await api(`/api/projects/${id}`); } catch (e) { toast(e.message, true); location.hash = "#/"; return; }
  const first = !S.project || S.project.id !== id;
  S.project = p;
  $("#railEditor").hidden = false;
  $("#railEditor").href = `#/p/${id}`;
  if (first) {
    S.undo = []; S.redo = [];
    S.style = { ...DEFAULT_STYLE, ...(p.style || {}) };
    if (!p.style && S.settings.defaultTheme) {
      const th = allThemes().find((x) => x.id === S.settings.defaultTheme);
      if (th) { const st = themeStyle(th); for (const k of STYLE_KEYS) S.style[k] = st[k]; }
    }
    video.src = `/media/${p.id}?v=${encodeURIComponent(p.preview)}`;
    switchTab("captions");
    $("#findInput").value = "";
    $("#doneBox").hidden = true;
  }
  $("#projName").value = p.name;
  document.title = `${p.name} — Coscribe`;
  fillTargets(p.target || S.settings.defaultTarget || "ar");
  syncStyleControls();
  renderThemeStrip();
  renderAll();
  requestAnimationFrame(layoutFrame);
  const jobs = await api(`/api/projects/${id}/jobs`);
  if (jobs.length) track(jobs[0]); else showJob(null);
}

async function reloadProject() {
  const p = await api(`/api/projects/${S.project.id}`);
  if (p.preview !== S.project.preview) {
    const tm = video.currentTime;
    video.src = `/media/${p.id}?v=${encodeURIComponent(p.preview)}`;
    video.currentTime = tm;
  }
  S.project = p;
  renderAll();
}

function fillTargets(selected) {
  fillLangSelect($("#targetLang"), { exclude: S.project.language, selected });
  if (!$("#targetLang").value) $("#targetLang").value = S.project.language === "ar" ? "en" : "ar";
  showEngine();
}

function renderAll() {
  const p = S.project;
  $("#fromLang").textContent = p.language ? langName(p.language) : "—";
  if (p.language) $("#reLang").value = p.language;
  const hasTr = !!p.target;
  $("#translateBtn").textContent = hasTr ? t("translateAgain") : t("translateCaptions");
  $("#translateBtn").disabled = !p.captions.length || !!S.job;
  setSeg("#lengthSeg", p.length || "normal");
  setSeg("#showSeg", hasTr ? S.style.show : "original");
  $$("#showSeg button").forEach((b) => { b.disabled = b.dataset.v !== "original" && !hasTr; });
  renderList();
  renderSegs();
  renderSteps();
  renderSummary();
  renderCaption(true);
  updateUndoButtons();
}

function renderSteps() {
  const p = S.project;
  const done = { transcribe: p.captions.length > 0, translate: !!p.target, style: !!p.style, export: !!p.exported };
  const order = ["transcribe", "translate", "style", "export"];
  const current = order.find((k) => !done[k]) || "export";
  for (const li of $$("#steps li")) {
    const k = li.dataset.step;
    li.classList.toggle("done", done[k]);
    $("i", li).textContent = done[k] ? "✓" : order.indexOf(k) + 1;
    li.classList.toggle("current", k === current && !done[k]);
  }
}

/* ================================================================ caption list + editing */
const ICONS = {
  split: '<path d="M6 4v16M18 4v16M6 12h12"/>',
  merge: '<path d="M8 4v6l4 4 4-4V4M12 14v6"/>',
  start: '<path d="M5 4v16M9 12h10M13 8l-4 4 4 4"/>',
  end: '<path d="M19 4v16M15 12H5M11 8l4 4-4 4"/>',
  del: '<path d="M5 7h14M10 7V5h4v2M7 7l1 13h8l1-13"/>',
};
const ico = (k) => `<svg viewBox="0 0 24 24" class="ico">${ICONS[k]}</svg>`;

function renderList() {
  const list = $("#capList");
  const caps = S.project.captions;
  const trLang = S.meta.languages.find((l) => l.code === S.project.target);
  list.classList.toggle("has-tr", !!S.project.target);
  list.innerHTML = "";
  if (!caps.length) { list.innerHTML = `<div class="empty">${esc(t("emptyCaptions"))}</div>`; return; }
  const frag = document.createDocumentFragment();
  caps.forEach((c, i) => {
    const el = document.createElement("div");
    el.className = "item";
    el.dataset.i = i;
    el.innerHTML = `<button class="ts" dir="ltr" title="${esc(t("open"))}">${fmt(c.start, true)}<span>${fmt(c.end, true)}</span></button>
      <textarea class="orig" rows="1" spellcheck="false" dir="auto"></textarea>
      <textarea class="tr" rows="1" spellcheck="false" placeholder="${trLang ? esc(t("addTr", { lang: langName(trLang.code) })) : ""}"></textarea>
      <div class="acts">
        <button data-act="split" title="${esc(t("split"))}">${ico("split")}</button>
        <button data-act="merge" title="${esc(t("merge"))}">${ico("merge")}</button>
        <button data-act="start" title="${esc(t("setStart"))}">${ico("start")}</button>
        <button data-act="end" title="${esc(t("setEnd"))}">${ico("end")}</button>
        <button data-act="del" class="danger" title="${esc(t("removeCap"))}">${ico("del")}</button>
      </div>`;
    const [o, tr] = $$("textarea", el);
    o.value = c.text;
    tr.value = c.tr || "";
    tr.dir = trLang && trLang.rtl ? "rtl" : "auto";
    if (S.project.target === "zgh") tr.classList.add("tfng");
    frag.append(el);
  });
  list.append(frag);
  S.active = -1;
  applyFind();
}

let caretInfo = null;
let editSnapshot = null;
function wireList() {
  const list = $("#capList");
  list.addEventListener("click", (e) => {
    const item = e.target.closest(".item");
    if (!item) return;
    const i = +item.dataset.i;
    if (e.target.closest(".ts")) { video.currentTime = S.project.captions[i].start + 0.01; video.play(); return; }
    const act = e.target.closest("[data-act]");
    if (act) { e.preventDefault(); captionAction(act.dataset.act, i); }
  });
  // Keep the caret position so "split" works after the click moves focus to the button.
  list.addEventListener("mousedown", (e) => {
    if (e.target.closest("[data-act]")) {
      const ta = document.activeElement;
      if (ta && ta.tagName === "TEXTAREA" && ta.closest(".item")) {
        caretInfo = { i: +ta.closest(".item").dataset.i, field: ta.classList.contains("tr") ? "tr" : "text", pos: ta.selectionStart };
      }
      e.preventDefault();
    }
  });
  list.addEventListener("focusin", (e) => {
    if (e.target.tagName !== "TEXTAREA") return;
    const i = +e.target.closest(".item").dataset.i;
    S.focusIdx = i;
    editSnapshot = snapshot();
    video.pause();
    video.currentTime = S.project.captions[i].start + 0.01;
  });
  list.addEventListener("input", (e) => {
    if (e.target.tagName !== "TEXTAREA") return;
    if (editSnapshot) { pushUndo(editSnapshot); editSnapshot = null; }
    const i = +e.target.closest(".item").dataset.i;
    S.project.captions[i][e.target.classList.contains("tr") ? "tr" : "text"] = e.target.value;
    renderCaption(true);
    saveCaptions();
  });
}

function captionAction(act, i) {
  const caps = S.project.captions;
  const c = caps[i];
  if (!c) return;
  if (act === "del") {
    pushUndo();
    caps.splice(i, 1);
  } else if (act === "merge") {
    const n = caps[i + 1];
    if (!n) return;
    pushUndo();
    c.text = `${c.text} ${n.text}`.trim();
    c.tr = `${c.tr || ""} ${n.tr || ""}`.trim();
    c.end = n.end;
    caps.splice(i + 1, 1);
  } else if (act === "split") {
    const info = caretInfo && caretInfo.i === i ? caretInfo : { field: "text", pos: Math.floor(c.text.length / 2) };
    const src = c[info.field] || "";
    const pos = Math.max(1, Math.min(src.length - 1, info.pos));
    if (src.length < 2) return;
    pushUndo();
    const frac = pos / src.length;
    const other = info.field === "text" ? "tr" : "text";
    const words = (c[other] || "").split(/\s+/).filter(Boolean);
    const cut = Math.round(words.length * frac);
    const mid = +(c.start + (c.end - c.start) * frac).toFixed(3);
    const second = { start: mid, end: c.end, text: "", tr: "" };
    second[info.field] = src.slice(pos).trim();
    second[other] = words.slice(cut).join(" ");
    c[info.field] = src.slice(0, pos).trim();
    c[other] = words.slice(0, cut).join(" ");
    c.end = mid;
    caps.splice(i + 1, 0, second);
  } else if (act === "start" || act === "end") {
    setEdge(act, i);
    return;
  }
  caretInfo = null;
  afterStructuralEdit();
}

function setEdge(which, idx = null) {
  const caps = S.project && S.project.captions;
  if (!caps || !caps.length) return;
  const i = idx ?? (S.focusIdx >= 0 ? S.focusIdx : S.active);
  const c = caps[i];
  if (!c) return;
  const now = +video.currentTime.toFixed(3);
  const prev = caps[i - 1], next = caps[i + 1];
  if (which === "start") {
    if (now >= c.end - 0.2 || (prev && now < prev.start + 0.2)) return;
    pushUndo();
    c.start = now;
    if (prev && prev.end > now) prev.end = now;
  } else {
    if (now <= c.start + 0.2 || (next && now > next.end - 0.2)) return;
    pushUndo();
    c.end = now;
    if (next && next.start < now) next.start = now;
  }
  afterStructuralEdit();
}

function addCaptionAtPlayhead() {
  const caps = S.project.captions;
  const now = +video.currentTime.toFixed(3);
  if (caps.some((c) => now >= c.start && now < c.end)) { toast(t("addCaption")); return; }
  const next = caps.find((c) => c.start > now);
  pushUndo();
  const c = { start: now, end: +Math.min(now + 2, next ? next.start : S.project.duration).toFixed(3), text: "", tr: "" };
  caps.push(c);
  caps.sort((a, b) => a.start - b.start);
  afterStructuralEdit();
  const i = caps.indexOf(c);
  const ta = $(`.item[data-i="${i}"] textarea.orig`);
  if (ta) ta.focus();
}

function afterStructuralEdit() {
  S.project.captions.forEach((c, i) => { c.id = i; });
  renderList();
  renderSegs();
  renderCaption(true);
  updateUndoButtons();
  saveCaptions();
}

/* ---------- undo / redo (whole-caption snapshots; cheap at subtitle scale) */
const snapshot = () => JSON.stringify(S.project.captions);
function pushUndo(snap = null) {
  S.undo.push(snap || snapshot());
  if (S.undo.length > 150) S.undo.shift();
  S.redo = [];
  updateUndoButtons();
}
function restore(snap) {
  S.project.captions = JSON.parse(snap);
  afterStructuralEdit();
}
function doUndo() {
  if (!S.undo.length) { toast(t("nothingToUndo")); return; }
  S.redo.push(snapshot());
  restore(S.undo.pop());
}
function doRedo() {
  if (!S.redo.length) { toast(t("nothingToRedo")); return; }
  S.undo.push(snapshot());
  restore(S.redo.pop());
}
function updateUndoButtons() {
  $("#undoBtn").disabled = !S.undo.length;
  $("#redoBtn").disabled = !S.redo.length;
}

/* ---------- find & replace */
function applyFind() {
  const q = $("#findInput").value.trim().toLowerCase();
  let n = 0;
  $$("#capList .item").forEach((el) => {
    const c = S.project.captions[+el.dataset.i];
    const hit = !q || c.text.toLowerCase().includes(q) || (c.tr || "").toLowerCase().includes(q);
    el.hidden = !hit;
    if (q && hit) n++;
  });
  $("#findCount").hidden = !q;
  $("#findCount").textContent = t("matchesN", { n });
}

function replaceAll() {
  const q = $("#findInput").value;
  if (!q) return;
  const rep = $("#replaceInput").value;
  const re = new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
  let count = 0;
  const snap = snapshot();
  for (const c of S.project.captions) {
    for (const f of ["text", "tr"]) {
      if (!c[f]) continue;
      c[f] = c[f].replace(re, () => { count++; return rep; });
    }
  }
  if (count) { pushUndo(snap); afterStructuralEdit(); }
  toast(t("replacedN", { n: count }));
}

const saveCaptions = debounce(async () => {
  if (!S.project) return;
  $("#saved").textContent = t("saving");
  try {
    await api(`/api/projects/${S.project.id}`, { method: "PUT", json: { captions: S.project.captions } });
    $("#saved").textContent = t("allSaved");
  } catch (e) { toast(e.message, true); }
}, 600);

const saveStyle = debounce(async () => {
  if (!S.project) return;
  $("#saved").textContent = t("saving");
  try {
    await api(`/api/projects/${S.project.id}`, { method: "PUT", json: { style: S.style } });
    S.project.style = { ...S.style };
    renderSteps();
    $("#saved").textContent = t("allSaved");
  } catch (e) { toast(e.message, true); }
}, 500);

/* ================================================================ timeline + preview */
function renderSegs() {
  const d = S.project.duration || 1;
  $("#segs").innerHTML = S.project.captions.map((c) =>
    `<i class="${c.tr ? "tr" : ""}" style="left:${(c.start / d) * 100}%;width:${Math.max(0.15, ((c.end - c.start) / d) * 100)}%"></i>`).join("");
}

function seekFromTimeline(e) {
  const r = $("#timeline").getBoundingClientRect();
  const f = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
  video.currentTime = f * (S.project.duration || video.duration || 0);
}

function layoutFrame() {
  if (!S.project || $("#editor").hidden) return;
  const stage = $("#stage");
  const aw = stage.clientWidth - 40, ah = stage.clientHeight - 40;
  const ar = S.project.width / S.project.height;
  let w = aw, h = aw / ar;
  if (h > ah) { h = ah; w = ah * ar; }
  const fr = $("#frame");
  fr.style.width = `${Math.floor(w)}px`;
  fr.style.height = `${Math.floor(h)}px`;
  renderCaption(true);
}

function captionAt(tm) {
  const caps = S.project.captions;
  if (S.active >= 0 && caps[S.active] && tm >= caps[S.active].start && tm < caps[S.active].end) return S.active;
  let lo = 0, hi = caps.length - 1, idx = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (caps[mid].start <= tm) { idx = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return idx >= 0 && tm < caps[idx].end ? idx : -1;
}

let lastKey = "";
function renderCaption(force = false) {
  if (!S.project || $("#editor").hidden) return;
  const i = captionAt(video.currentTime);
  if (i !== S.active) setActive(i);
  const fr = $("#frame");
  const key = `${i}|${fr.clientWidth}x${fr.clientHeight}`;
  if (!force && key === lastKey) return;
  lastKey = key;
  const layer = $("#capLayer");
  layer.innerHTML = "";
  if (i < 0) return;
  const style = { ...S.style, show: S.project.target ? S.style.show : "original" };
  layer.append(buildCap(style, linesFor(S.project.captions[i], style), fr.clientWidth, fr.clientHeight));
}

function setActive(i) {
  const list = $("#capList");
  const prev = list.querySelector(".item.active");
  if (prev) prev.classList.remove("active");
  $$("#segs i.active").forEach((x) => x.classList.remove("active"));
  S.active = i;
  if (i < 0) return;
  const el = list.querySelector(`.item[data-i="${i}"]`);
  if (el) {
    el.classList.add("active");
    const typing = document.activeElement && document.activeElement.tagName === "TEXTAREA";
    if (!typing && !$("[data-body=captions]").hidden && !video.paused) el.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
  const seg = $("#segs").children[i];
  if (seg) seg.classList.add("active");
}

function tick() {
  if (S.project && !$("#editor").hidden) {
    const d = S.project.duration || video.duration || 1;
    $("#playhead").style.left = `${(video.currentTime / d) * 100}%`;
    $("#time").textContent = `${fmt(video.currentTime)} / ${fmt(d)}`;
    renderCaption();
  }
  requestAnimationFrame(tick);
}

/* ================================================================ editor wiring */
function wireEditor() {
  wireList();
  wireThemes();
  $("#playBtn").addEventListener("click", togglePlay);
  video.addEventListener("click", togglePlay);
  video.addEventListener("play", () => { $("#playIcon").innerHTML = '<path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z" fill="currentColor"/>'; });
  video.addEventListener("pause", () => { $("#playIcon").innerHTML = '<path d="M8 5.5v13l10.5-6.5z" fill="currentColor"/>'; });
  video.addEventListener("error", () => { if (S.project && !$("#editor").hidden) toast(t("previewError"), true); });

  const tl = $("#timeline");
  let dragging = false;
  tl.addEventListener("pointerdown", (e) => { dragging = true; tl.setPointerCapture(e.pointerId); seekFromTimeline(e); });
  tl.addEventListener("pointermove", (e) => { if (dragging) seekFromTimeline(e); });
  tl.addEventListener("pointerup", () => { dragging = false; });

  $$(".tab").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));
  $$("#steps li").forEach((li) => li.addEventListener("click", () => {
    const k = li.dataset.step;
    switchTab(k === "style" ? "style" : k === "export" ? "export" : "captions");
  }));
  $("#topExport").addEventListener("click", () => switchTab("export"));

  $("#projName").addEventListener("change", async (e) => {
    const name = e.target.value.trim() || S.project.name;
    e.target.value = name;
    S.project.name = name;
    await api(`/api/projects/${S.project.id}`, { method: "PUT", json: { name } });
    document.title = `${name} — Coscribe`;
  });
  $("#projName").addEventListener("keydown", (e) => { if (e.key === "Enter") e.target.blur(); });

  $("#targetLang").addEventListener("change", showEngine);
  segWire("#engineSeg", (v) => { S.engine = v; showEngine(); });
  $("#translateBtn").addEventListener("click", translateNow);

  segWire("#showSeg", (v) => { S.style.show = v; styleChanged(); });
  segWire("#lengthSeg", async (v) => {
    const p = S.project;
    if (v === (p.length || "normal")) return;
    const ok = await ask({ title: t("captionLength"), text: p.target ? t("confirmRechunkTr") : t("confirmRechunk") });
    if (!ok) { setSeg("#lengthSeg", p.length || "normal"); return; }
    const had = p.target;
    try {
      S.project = await api(`/api/projects/${p.id}/rechunk`, { json: { length: v } });
      S.undo = []; S.redo = [];
      renderAll();
      if (had) { $("#targetLang").value = had; translateNow(); }
    } catch (e) { toast(e.message, true); }
  });

  $("#reTranscribe").addEventListener("click", async () => {
    if (!await ask({ title: t("transcribeAgain"), text: t("confirmRetranscribe") })) return;
    try { track(await api(`/api/projects/${S.project.id}/transcribe`, { json: { language: $("#reLang").value } })); } catch (e) { toast(e.message, true); }
  });

  $("#findInput").addEventListener("input", applyFind);
  $("#replaceToggle").addEventListener("click", () => {
    $("#replaceRow").hidden = !$("#replaceRow").hidden;
    $("#replaceToggle").classList.toggle("on", !$("#replaceRow").hidden);
    if (!$("#replaceRow").hidden) $("#replaceInput").focus();
  });
  $("#replaceAll").addEventListener("click", replaceAll);
  $("#undoBtn").addEventListener("click", doUndo);
  $("#redoBtn").addEventListener("click", doRedo);
  $("#addCapBtn").addEventListener("click", addCaptionAtPlayhead);

  segWire("#resSeg", (v) => { S.exportRes = v; saveSettings({ exportResolution: v }); });
  $$("[data-sub]").forEach((b) => b.addEventListener("click", () => {
    const f = b.dataset.sub;
    if (S.subs.has(f)) S.subs.delete(f); else S.subs.add(f);
    b.classList.toggle("on", S.subs.has(f));
  }));
  $("#exportBtn").addEventListener("click", async () => {
    try {
      track(await api(`/api/projects/${S.project.id}/export`, { json: { style: S.style, resolution: S.exportRes, subtitles: [...S.subs] } }));
    } catch (e) { toast(e.message, true); }
  });
  $("#revealBtn").addEventListener("click", () => api(`/api/projects/${S.project.id}/reveal`, { json: {} }));
  $$("[data-save]").forEach((b) => b.addEventListener("click", async () => {
    const [field, format] = b.dataset.save.split(":");
    try { const r = await api(`/api/projects/${S.project.id}/save-srt`, { json: { field, format } }); toast(t("savedFile", { name: r.name })); } catch (e) { toast(e.message, true); }
  }));
  $("#deleteBtn").addEventListener("click", () => deleteProject(S.project));
  $("#cancelJob").addEventListener("click", () => { if (S.job) api(`/api/jobs/${S.job.id}/cancel`, { json: {} }); });
  requestAnimationFrame(tick);
}

function showEngine() {
  setSeg("#engineSeg", S.engine);
  const tgt = $("#targetLang").value;
  const key = tgt === "zgh" ? "hintZgh" : tgt === "ary" ? "hintAry" : S.engine === "gemma" ? "hintBest" : "hintFast";
  $("#engineHint").textContent = t(key);
  const gemma = S.meta.engines.find((e) => e.id === "gemma");
  $('#engineSeg [data-v="gemma"]').disabled = !(gemma && gemma.available);
}

async function translateNow() {
  const target = $("#targetLang").value;
  try {
    track(await api(`/api/projects/${S.project.id}/translate`, { json: { target, engine: S.engine } }));
    if (S.style.show === "original") { S.style.show = "translation"; styleChanged(); }
  } catch (e) { toast(e.message, true); }
}

function togglePlay() {
  if (!S.project) return;
  if (video.paused) video.play(); else video.pause();
}

function switchTab(name) {
  $$(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab-body").forEach((b) => { b.hidden = b.dataset.body !== name; });
  if (name === "export") renderSummary();
  if (name === "style") renderThemeStrip();
}

function renderSummary() {
  const p = S.project;
  if (!p) return;
  const show = { original: t("showOrig"), translation: t("showTr"), both: t("showBoth") }[S.style.show];
  $("#summary").innerHTML = `
    <span>${t("sumVideo")}</span><b dir="ltr">${p.width}×${p.height} · ${fmt(p.duration)}</b>
    <span>${t("sumCaptions")}</span><b>${p.captions.length}</b>
    <span>${t("sumLanguage")}</span><b>${esc(langName(p.language))}${p.target ? " → " + esc(langName(p.target)) : ""}</b>
    <span>${t("sumOnVideo")}</span><b>${p.target ? show : t("showOrig")}</b>
    <span>${t("sumFont")}</span><b>${esc(S.style.font)}</b>`;
  $$('[data-save^="tr:"]').forEach((b) => { b.disabled = !p.target; });
  $("#doneBox").hidden = !p.exported;
  if (p.exported) $("#doneName").textContent = p.exported;
}

/* ================================================================ jobs */
let pollTimer = null;
function stopPolling() { clearTimeout(pollTimer); pollTimer = null; }

function track(job) {
  S.job = job;
  showJob(job);
  stopPolling();
  const poll = async () => {
    let j;
    try { j = await api(`/api/jobs/${job.id}`); } catch { pollTimer = setTimeout(poll, 1000); return; }
    if (!S.project || j.project !== S.project.id) return;
    S.job = j;
    showJob(j);
    if (j.state === "done") {
      S.job = null; showJob(null);
      await reloadProject();
      if (j.kind === "transcribe") { S.undo = []; S.redo = []; toast(t("doneTranscribe", { n: S.project.captions.length })); }
      if (j.kind === "translate") toast(t("doneTranslate"));
      if (j.kind === "export") { switchTab("export"); toast(t("doneExport")); }
    } else if (j.state === "error" || j.state === "cancelled") {
      S.job = null; showJob(null);
      toast(j.state === "cancelled" ? t("cancelled") : j.error, j.state === "error");
      await reloadProject();
    } else {
      pollTimer = setTimeout(poll, 500);
    }
  };
  poll();
}

function dur(s) {
  s = Math.max(0, Math.round(s));
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s`;
}

function showJob(j) {
  const busy = !!j;
  $("#job").hidden = !busy;
  $$("#translateBtn, #exportBtn, #reTranscribe, #topExport").forEach((b) => { b.disabled = busy; });
  $$("#lengthSeg button, #capList .acts button").forEach((b) => { b.disabled = busy; });
  $$("#capList textarea").forEach((x) => { x.readOnly = busy; });
  if (!busy) { if (S.project) $("#translateBtn").disabled = !S.project.captions.length; return; }
  video.pause();
  const pct = Math.round((j.progress || 0) * 100);
  $("#jobMsg").textContent = j.message || t("working");
  $("#jobBar").style.width = `${pct}%`;
  $("#jobPct").textContent = `${pct}%`;
  let eta = "";
  if (j.started) {
    const el = Date.now() / 1000 - j.started;
    eta = j.progress > 0.08 && el > 5 ? t("etaLeft", { t: dur(el / j.progress - el) }) : t("elapsed", { t: dur(el) });
  }
  $("#jobEta").textContent = eta;
}

/* ================================================================ style controls */
function buildFontGrid() {
  const fonts = $("#fonts");
  fonts.innerHTML = "";
  for (const f of S.meta.fonts) {
    const b = document.createElement("button");
    b.className = "font";
    b.dataset.f = f.family;
    b.title = f.arabic ? f.family : `${f.family} — ${t("latinOnly")}`;
    b.innerHTML = `<span class="n"></span>${f.arabic ? '<span class="ar">عربي</span>' : ""}`;
    $(".n", b).textContent = f.family;
    b.style.fontFamily = `"${f.family}"`;
    b.addEventListener("click", () => { S.style.font = f.family; styleChanged(); });
    fonts.append(b);
  }
  if (S.project) syncStyleControls();
}

function buildStyleControls() {
  buildFontGrid();
  const ranges = [
    ["size", "sizeOut", (v) => `${(+v).toFixed(1)}`],
    ["bgOpacity", "opOut", (v) => `${v}%`],
    ["pad", "padOut", (v) => `${Math.round(v * 100)}`],
    ["outline", "olOut", (v) => `${Math.round(v * 100)}`],
    ["offset", "offOut", (v) => `${v}%`],
    ["sideMargin", "sideOut", (v) => `${v}%`],
    ["secondarySize", "secOut", (v) => `${v}%`],
  ];
  for (const [id, out, f] of ranges) {
    const el = $(`#${id}`);
    el.addEventListener("input", () => { S.style[id] = +el.value; $(`#${out}`).textContent = f(el.value); styleChanged(false); });
    el._fmt = () => { $(`#${out}`).textContent = f(el.value); };
  }
  $("#boldT").addEventListener("click", () => { S.style.bold = !S.style.bold; styleChanged(); });
  $("#upperT").addEventListener("click", () => { S.style.uppercase = !S.style.uppercase; styleChanged(); });
  segWire("#bgSeg", (v) => { S.style.bg = v; styleChanged(); });
  segWire("#posSeg", (v) => { S.style.position = v; styleChanged(); });
  swatches("#colorSw", TEXT_COLORS, "color");
  swatches("#bgSw", BG_COLORS, "bgColor");
  swatches("#secSw", SEC_COLORS, "secondaryColor");
}

function swatches(sel, colors, key) {
  const box = $(sel);
  for (const c of colors) {
    const b = document.createElement("button");
    b.className = "sw";
    b.style.background = c;
    b.dataset.c = c;
    b.title = c;
    b.addEventListener("click", () => { S.style[key] = c; styleChanged(); });
    box.append(b);
  }
  const custom = document.createElement("label");
  custom.className = "sw-custom";
  custom.dataset.i18nTitle = "customColor";
  const inp = document.createElement("input");
  inp.type = "color";
  inp.addEventListener("input", () => { S.style[key] = inp.value.toUpperCase(); styleChanged(false); });
  custom.append(inp);
  box.append(custom);
  box._key = key;
}

function syncStyleControls() {
  const st = S.style;
  for (const id of ["size", "bgOpacity", "pad", "outline", "offset", "sideMargin", "secondarySize"]) {
    const el = $(`#${id}`);
    el.value = st[id];
    el._fmt();
  }
  $$(".font").forEach((b) => b.classList.toggle("on", b.dataset.f === st.font));
  $("#boldT").classList.toggle("on", !!st.bold);
  $("#upperT").classList.toggle("on", !!st.uppercase);
  setSeg("#bgSeg", st.bg);
  setSeg("#posSeg", st.position);
  setSeg("#showSeg", S.project && !S.project.target ? "original" : st.show);
  for (const sel of ["#colorSw", "#bgSw", "#secSw"]) {
    const box = $(sel);
    const val = (st[box._key] || "").toUpperCase();
    $$(".sw", box).forEach((b) => b.classList.toggle("on", b.dataset.c.toUpperCase() === val));
    $("input", box).value = val.length === 7 ? val.toLowerCase() : "#ffffff";
  }
  $("#bgColorCtl").hidden = st.bg === "none";
  $("#opCtl").hidden = !(st.bg === "box" || st.bg === "shadow");
  $("#padCtl").hidden = st.bg !== "box";
  $("#olCtl").hidden = st.bg !== "outline";
  $("#offCtl").hidden = st.position === "middle";
  $("#secGroup").hidden = st.show !== "both";
  $$("#themeStrip .theme-tile").forEach((tile) => {
    const th = allThemes().find((x) => x.id === tile.dataset.id);
    const ts = th && themeStyle(th);
    tile.classList.toggle("on", !!ts && STYLE_KEYS.every((k) => st[k] === ts[k]));
  });
}

function styleChanged(resync = true) {
  if (resync) syncStyleControls();
  renderCaption(true);
  renderSummary();
  saveStyle();
}

function pushTo(tab) { if (S.project && !$("#editor").hidden) switchTab(tab); }

function segWire(sel, cb) {
  $$(`${sel} button`).forEach((b) => b.addEventListener("click", () => { setSeg(sel, b.dataset.v); cb(b.dataset.v); }));
}
function setSeg(sel, v) {
  $$(`${sel} button`).forEach((b) => b.classList.toggle("on", b.dataset.v === v));
}

boot().catch((e) => toast(e.message, true));
