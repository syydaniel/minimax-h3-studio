/* H3 Studio: shared helpers + creation panel (modes, assets, size, duration,
   quality, ETA, Claude optimize, submit). Loaded before app.js. */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const store = {
  get(k, d) { try { const v = localStorage.getItem("h3s." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("h3s." + k, JSON.stringify(v)); } catch {} },
};
async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  let body = null;
  try { body = await r.json(); } catch {}
  if (!r.ok) throw new Error((body && body.detail) || `HTTP ${r.status}`);
  return body;
}
function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast" + (err ? " err" : ""); t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), err ? 6000 : 3000);
}
function fmtDur(sec) {
  if (sec == null || !isFinite(sec)) return "--";
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h} 小时 ${m} 分` : m ? `${m} 分 ${String(s).padStart(2, "0")} 秒` : `${s} 秒`;
}

/* ---------- model limits ---------- */
const MAX_PIXELS = 768 * 1344;
/* The six ratios MiniMax-H3 lists as supported. Pixels come from resolveCanvas. */
const ASPECTS = { "21:9": [21, 9], "16:9": [16, 9], "4:3": [4, 3], "1:1": [1, 1], "3:4": [3, 4], "9:16": [9, 16] };
/* 768 is the canvas H3 was released for; 480 is the same official geometry at a
   smaller short edge, about 40% of the pixels and correspondingly faster. */
const SHORT_EDGES = [[768, "768p", "官方画布"], [480, "480p", "约快 2 倍"]];
const VARIANTS = [["base", "原版", "官方权重"], ["turbo", "Turbo 加速", "蒸馏，步数少"]];
/* h3.c is the native C engine and the only one here with a working audio VAE.
   vPipe runs the same model from a stage graph and applies the Turbo LoRA at
   runtime, but its checkpoint repack has an audio VAE this loader cannot read. */
const ENGINES = [["h3", "h3.c", "带原生音轨"], ["vpipe", "vPipe", "更快，同样带音轨"]];

/* Port of diffusers MiniMax-H3 `resolve_canvas_size` (released checkpoint:
   short edge 768, area capped at 768*1344, both axes rounded to the nearest
   32 with Python's round-half-even). Gives 21:9 1536x672, 16:9 1344x768,
   4:3 1024x768, 1:1 768x768, 3:4 768x1024, 9:16 768x1344. */
function roundHalfEven(x) {
  const f = Math.floor(x), d = x - f;
  if (Math.abs(d - 0.5) < 1e-9) return f % 2 === 0 ? f : f + 1;
  return Math.round(x);
}
/* Rounding can push the area just over the cap (2.39:1 -> 1568x672), which
   h3.c rejects, so the long axis then steps down by 32 until it fits. The
   third element says whether that happened. Mirrors official_canvas() in
   server.py. */
function resolveCanvas(aw, ah, shortEdge = 768) {
  const ratio = Math.min(4, Math.max(0.25, aw / ah));
  let w = ratio >= 1 ? shortEdge * ratio : shortEdge, h = ratio >= 1 ? shortEdge : shortEdge / ratio;
  if (w * h > MAX_PIXELS) { const s = Math.sqrt(MAX_PIXELS / (w * h)); w *= s; h *= s; }
  const r32 = v => Math.max(32, roundHalfEven(v / 32) * 32);
  w = r32(w); h = r32(h);
  let clamped = false;
  while (w * h > MAX_PIXELS) { if (w >= h) w -= 32; else h -= 32; clamped = true; }
  return [w, h, clamped];
}
const QUALITY = {
  preview: { label: "预览", sub: "4 步", steps: 4, reuse: 1, layers: 50, core: 1, token: false },
  draft:   { label: "草稿", sub: "8 步", steps: 8, reuse: 1, layers: 50, core: 1, token: false },
  balanced:{ label: "标准", sub: "20 步 · 复用", steps: 20, reuse: 2, layers: 50, core: 1, token: true },
  fine:    { label: "精品", sub: "20 步", steps: 20, reuse: 1, layers: 50, core: 1, token: false },
  ref:     { label: "参考级", sub: "50 步", steps: 50, reuse: 1, layers: 50, core: 1, token: false },
};
/* Turbo runs a distilled schedule: few steps, and none of the redundancy that
   reuse / core-reuse / token reduction rely on, so those stay off. larryvrh's
   FL2VA adapter is validated from 5 steps (4 smears fast motion); lightx2v's
   Ref2VA adapter is an 8-step one. */
const TURBO_QUALITY = {
  t5: { label: "最快", sub: "5 步", steps: 5, reuse: 1, layers: 50, core: 1, token: false },
  t6: { label: "推荐", sub: "6 步", steps: 6, reuse: 1, layers: 50, core: 1, token: false },
  t8: { label: "更稳", sub: "8 步", steps: 8, reuse: 1, layers: 50, core: 1, token: false },
};
const TURBO_REF_QUALITY = {
  t8: { label: "参考模式", sub: "8 步 · shift 6", steps: 8, reuse: 1, layers: 50, core: 1, token: false },
};
function presets() {
  if (S.variant !== "turbo") return QUALITY;
  return S.mode === "ref" ? TURBO_REF_QUALITY : TURBO_QUALITY;
}
const CAMERA = [
  ["推近", "The camera pushes in with small amplitude at slow speed toward "],
  ["拉远", "The camera pulls out with large amplitude at slow speed, revealing "],
  ["左摇", "The camera pans left at slow speed across "],
  ["右摇", "The camera pans right at slow speed across "],
  ["横移", "The camera trucks right with small amplitude at slow speed alongside "],
  ["上摇", "The camera tilts up with large amplitude at slow speed following "],
  ["升降", "The camera pedestals up at slow speed over "],
  ["环绕", "The camera moves in an arc shot around "],
  ["跟拍", "A tracking shot follows "],
  ["固定", "The camera holds a static shot as "],
  ["轻晃", "The camera shakes slightly as "],
  ["切镜", "[Shot 2] At 00:05.000, the camera cuts to "],
];

/* frames are 5 + 17k. Official duration is 4-15 s (model card, SGLang API),
   i.e. k 6..21 = 107..362 frames = 4.46..15.08 s. This also covers h3's own
   floors: 22 frames per decoder chunk, 56 when a reference carries audio. */
const K_MIN = 6, K_MAX = 21;
const framesOf = k => 5 + 17 * k;

/* "auto" follows the first keyframe's own aspect, like the official pipeline. */
function canvasFor() {
  if (S.aspect === "auto" && S.first && S.first.w) return resolveCanvas(S.first.w, S.first.h, S.short);
  const [a, b] = ASPECTS[S.aspect === "auto" ? "16:9" : S.aspect];
  return resolveCanvas(a, b, S.short);
}

/* ETA model, calibrated on this M3 Max:
   256x256x22f = 1.99 s / DiT eval, 1344x768x39f = 89.4 s, 1344x768x243f ~ 1440 s.
   Interpolated in log-log space over (pixels * frames). */
const CAL = [[65536 * 22, 1.99], [1032192 * 39, 89.4], [1032192 * 243, 1440]];
function secPerEval(x) {
  const L = CAL.map(([u, v]) => [Math.log(u), Math.log(v)]);
  const lx = Math.log(x);
  let i = lx <= L[1][0] ? 0 : 1;
  const [x0, y0] = L[i], [x1, y1] = L[i + 1];
  return Math.exp(y0 + ((lx - x0) * (y1 - y0)) / (x1 - x0));
}
function evalsOf(p) {
  if (p.variant === "turbo") return p.steps;          // distilled: every step is a full forward
  if (p.core > 1) return p.steps * (0.3 + 0.7 / p.core);
  return p.reuse === 1 ? p.steps : Math.ceil(p.steps / p.reuse) + 1;
}
function estimate(p) {
  const x = p.width * p.height * p.frames;
  let denoise = secPerEval(x) * evalsOf(p) * (p.layers / 50) * (p.token ? 0.75 : 1);
  /* measured on this M3 Max at 864x480x73f, 6 steps: h3.c 641 s, vPipe 460 s,
     vPipe+sol_attn 376 s. The ETA curve is calibrated on h3.c, so scale it. */
  if (p.engine === "vpipe") denoise /= p.sol_attn ? 1.70 : 1.39;
  const decode = 2.1e-6 * x + 5;
  return { denoise, total: 20 + denoise + decode };
}

/* ---------- state ---------- */
const S = {
  mode: "t2v",
  aspect: store.get("aspect", "16:9"),
  short: SHORT_EDGES.some(([s]) => s === store.get("short", 768)) ? store.get("short", 768) : 768,
  variant: store.get("variant", "base"),
  engine: store.get("engine", "h3"),
  k: Math.min(K_MAX, Math.max(K_MIN, store.get("k", 7))),
  quality: store.get("quality", "balanced"),
  first: null, last: null,        // {file, url}
  refs: [],                        // {file, url, kind: image|video|silent_video|audio}
  status: {},
};

function params() {
  const [width, height] = canvasFor();
  return {
    width, height, frames: framesOf(S.k),
    steps: +$("#steps").value || 20, reuse: +$("#reuse").value, layers: +$("#layers").value,
    core: +$("#core").value, token: $("#token-red").checked, seed: +$("#seed").value || 0,
    variant: S.variant, engine: S.engine,
    sol_attn: $("#sol-attn").checked, i8_gemm: $("#i8-gemm").checked,
  };
}

/* ---------- reference labels, same rule as h3_multimodal.c ---------- */
function refLabels() {
  let pic = 0, vid = 0, aud = 0;
  return S.refs.map(r => {
    const L = [];
    if (r.kind === "video" || r.kind === "audio") L.push(`<Audio ${++aud}>`);
    if (r.kind === "image") L.push(`<Picture ${++pic}>`);
    if (r.kind === "video" || r.kind === "silent_video") L.push(`<Video ${++vid}>`);
    return L;
  });
}

/* ---------- rendering ---------- */
function seg(el, items, current, onPick) {
  el.innerHTML = items.map(([v, label, sub]) =>
    `<button data-v="${esc(v)}" class="${String(v) === String(current) ? "on" : ""}">${esc(label)}${sub ? `<small>${esc(sub)}</small>` : ""}</button>`).join("");
  el.onclick = e => { const b = e.target.closest("button"); if (b) onPick(b.dataset.v); };
}

function dropZone(slot, title, item) {
  return `<div class="drop" data-slot="${slot}">
    <div class="thumb" style="${item ? `background-image:url('${item.url}')` : ""}">${item ? "" : "＋"}</div>
    <div class="meta"><b>${esc(title)}</b>${item ? esc(item.file.name) : "点击或拖入图片"}</div>
    ${item ? `<button class="x" data-clear="${slot}" title="移除">✕</button>` : ""}</div>`;
}

function renderAssets() {
  const el = $("#assets");
  if (S.mode === "t2v") { el.innerHTML = ""; return; }
  if (S.mode === "i2v") { el.innerHTML = dropZone("first", "首帧", S.first); }
  else if (S.mode === "fl2v") { el.innerHTML = dropZone("first", "首帧", S.first) + dropZone("last", "尾帧", S.last); }
  else {
    const labels = refLabels();
    el.innerHTML = `<div class="ref-list">${S.refs.map((r, i) => `
      <div class="ref-item">
        <div class="thumb" style="${r.kind === "image" ? `background-image:url('${r.url}')` : ""}">${r.kind === "image" ? "" : r.kind === "audio" ? "♪" : "▶"}</div>
        <span class="grow">${esc(r.file.name)}</span>
        ${r.kind === "video" || r.kind === "silent_video" ? `<select data-kind="${i}"><option value="video" ${r.kind === "video" ? "selected" : ""}>带音轨</option><option value="silent_video" ${r.kind === "silent_video" ? "selected" : ""}>静音</option></select>` : ""}
        ${labels[i].map(l => `<code data-ins="${esc(l)}" title="插入到提示词">${esc(l)}</code>`).join("")}
        <button data-del="${i}" title="移除">✕</button>
      </div>`).join("")}</div>
      <div class="drop" data-slot="ref"><div class="thumb">＋</div>
      <div class="meta"><b>添加参考素材</b>图片最多 9 张，视频、音频各最多 3 段，合计 12 个；带音轨的视频至少 2.34 秒，否则请选「静音」</div></div>`;
  }
}

function renderSize() {
  const keyframed = S.mode === "i2v" || S.mode === "fl2v";
  if (!keyframed && S.aspect === "auto") S.aspect = "16:9";
  const opts = Object.keys(ASPECTS).map(a => { const [w, h] = resolveCanvas(...ASPECTS[a], S.short); return [a, a, `${w}×${h}`]; });
  if (keyframed) opts.unshift(["auto", "跟随首帧", "官方做法"]);
  seg($("#aspect"), opts, S.aspect, v => { S.aspect = v; store.set("aspect", v); update(); });
  seg($("#short-edge"), SHORT_EDGES.map(([s, l, sub]) => [s, l, sub]), S.short,
      v => { S.short = +v; store.set("short", +v); update(); });
  const vpipeOff = !S.status.vpipe;
  if (vpipeOff && S.engine === "vpipe") { S.engine = "h3"; store.set("engine", "h3"); }
  seg($("#engine"), ENGINES.map(([v, l, sub]) => [v, l, v === "vpipe" && vpipeOff ? "未安装" : sub]), S.engine,
      v => { if (v === "vpipe" && vpipeOff) return toast("vPipe 未安装", true);
             S.engine = v; store.set("engine", v); update(); });
  const turboOff = !S.status.turbo || (S.mode === "ref" && !S.status.turbo_ref);
  // a stored "turbo" choice must not survive into a deployment that lacks the
  // weights, or the job is only refused later by the server
  if (turboOff && S.variant === "turbo") { S.variant = "base"; store.set("variant", "base"); fixPreset(); }
  seg($("#variant"), VARIANTS.map(([v, l, sub]) => [v, l, v === "turbo" && turboOff ? "未安装" : sub]), S.variant,
      v => { if (v === "turbo" && turboOff) return toast("Turbo 权重未安装", true);
             S.variant = v; store.set("variant", v); fixPreset(); update(); });
  seg($("#quality"), Object.entries(presets()).map(([k, q]) => [k, q.label, q.sub]), S.quality,
      v => { S.quality = v; store.set("quality", v); applyQuality(); update(); });
}

/* keep the selected preset valid when the model or the mode changes */
function fixPreset() {
  const p = presets();
  if (!p[S.quality]) { S.quality = Object.keys(p)[S.variant === "turbo" ? 1 % Object.keys(p).length : 0]; store.set("quality", S.quality); }
  applyQuality();
}

function applyQuality() {
  const q = presets()[S.quality] || Object.values(presets())[0];
  $("#steps").value = q.steps; $("#reuse").value = q.reuse; $("#layers").value = q.layers;
  $("#core").value = q.core; $("#token-red").checked = q.token;
}

function update() {
  renderSize();
  const p = params();
  const secs = p.frames / 24;
  $("#dur-readout").textContent = `${secs.toFixed(2)} 秒 · ${p.frames} 帧（官方 4 到 15 秒，帧数对齐到 5 + 17k）`;
  const clamped = S.aspect === "auto" && S.first && S.first.w && resolveCanvas(S.first.w, S.first.h)[2];
  // Turbo's distilled schedule has no redundancy for these to exploit
  const vp = S.engine === "vpipe";
  $$("#modes button").forEach(b => { b.disabled = vp && (b.dataset.mode === "fl2v" || b.dataset.mode === "ref"); });
  if (vp && (S.mode === "fl2v" || S.mode === "ref")) { setMode("t2v"); return; }
  $("#engine-hint").textContent = vp ? "vPipe 用官方权重，带音轨；只有 FL2VA，做不了首尾帧和参考生视频" : "";
  ["#reuse", "#core", "#token-red"].forEach(s => { const el = $(s); el.disabled = S.variant === "turbo" || vp; });
  ["#sol-attn", "#i8-gemm"].forEach(s => { const el = $(s); el.disabled = !vp; if (!vp) el.checked = false; });
  if (S.variant === "turbo" || vp) { $("#reuse").value = 1; $("#core").value = 1; $("#token-red").checked = false; }
  $("#size-readout").textContent = `官方 ${S.short}p 画布 ${p.width} × ${p.height}` +
    (S.aspect === "auto" ? (S.first && S.first.w ? `（按首帧 ${S.first.w}×${S.first.h} 的比例）` : "（还没有首帧，暂按 16:9）") : "") +
    (clamped ? "，官方取整后超出 h3.c 的 768×1344 像素上限，长边已缩 32 的倍数" : "");
  const e = estimate(p);
  $("#eta").innerHTML = `预计 <b>${fmtDur(e.total)}</b><br><span class="muted">DiT ${Math.round(evalsOf(p))} 次前向 · 按本机实测外推</span>`;
  const ok = S.status.fl2va && $("#prompt").value.trim() &&
    (S.mode !== "i2v" || S.first) && (S.mode !== "fl2v" || (S.first && S.last)) &&
    (S.mode !== "ref" || S.refs.some(r => r.kind !== "audio"));
  $("#submit").disabled = !ok;
  store.set("k", S.k);
}

function setMode(m) {
  S.mode = m;
  $$("#modes button").forEach(b => b.classList.toggle("on", b.dataset.mode === m));
  renderAssets(); update();
}

/* ---------- file handling ---------- */
function kindOf(file) {
  if (file.type.startsWith("image/")) return "image";
  if (file.type.startsWith("video/")) return "video";
  if (file.type.startsWith("audio/")) return "audio";
  return null;
}
function addFiles(slot, files) {
  for (const file of files) {
    const kind = kindOf(file);
    const item = { file, url: URL.createObjectURL(file), kind };
    if (slot === "first" || slot === "last") {
      if (kind !== "image") { toast("首帧/尾帧只接受图片", true); continue; }
      S[slot] = item;
      const img = new Image();
      img.onload = () => { item.w = img.naturalWidth; item.h = img.naturalHeight; renderAssets(); update(); };
      img.src = item.url;
    } else {
      if (!kind) { toast(`不支持的文件：${file.name}`, true); continue; }
      const count = k => S.refs.filter(r => (k === "video" ? r.kind.includes("video") : r.kind === k)).length;
      const cap = { image: 9, video: 3, audio: 3 }[kind];
      if (count(kind) >= cap || S.refs.length >= 12) { toast("超出参考素材数量上限", true); continue; }
      S.refs.push(item);
    }
  }
  renderAssets(); update();
}
function pickFiles(slot) {
  const inp = document.createElement("input");
  inp.type = "file"; inp.multiple = slot === "ref";
  inp.accept = slot === "ref" ? "image/*,video/*,audio/*" : "image/*";
  inp.onchange = () => addFiles(slot, inp.files);
  inp.click();
}

function insertAtCursor(ta, text) {
  const { selectionStart: a, selectionEnd: b, value } = ta;
  ta.value = value.slice(0, a) + text + value.slice(b);
  ta.selectionStart = ta.selectionEnd = a + text.length;
  ta.focus(); store.set("prompt", ta.value); update();
}

/* ---------- Claude ---------- */
async function refreshClaude(recheck = false) {
  const el = $("#claude-state");
  try {
    const st = await api("/api/claude/status" + (recheck ? "?recheck=true" : ""));
    S.claude = st;
    el.innerHTML = st.ok ? esc(st.detail) : `<span class="error">${esc(st.detail)}</span> <button class="ghost" id="claude-recheck">重新检测</button>`;
    const rb = $("#claude-recheck"); if (rb) rb.onclick = () => refreshClaude(true);
    if (typeof schedule === "function") schedule();   // repaint the top-bar chip now
  } catch (e) { el.textContent = "无法获取 Claude 状态"; }
}
async function optimize() {
  const btn = $("#optimize");
  const idea = $("#idea").value, draft = $("#prompt").value;
  if (!idea.trim() && !draft.trim()) { toast("先在「你的想法」里写几句", true); return; }
  btn.disabled = true; btn.classList.add("busy");
  $("#claude-state").textContent = "Claude 正在按官方格式改写，通常 20 到 60 秒…";
  try {
    const r = await api("/api/claude/optimize", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: S.mode, idea, draft, duration: framesOf(S.k) / 24, labels: refLabels().flat() }),
    });
    $("#prompt").value = r.prompt; store.set("prompt", r.prompt);
    $("#claude-state").textContent = "已改写，可以继续手动微调";
    update();
  } catch (e) {
    // the server has just learned the login state; pull it so the chip and
    // the re-check button reflect it immediately, then keep the real error
    await refreshClaude();
    $("#claude-state").innerHTML = `<span class="error">${esc(e.message)}</span>`;
  } finally { btn.disabled = false; btn.classList.remove("busy"); }
}

/* ---------- submit ---------- */
async function submit() {
  const p = params();
  const fd = new FormData(), slots = [];
  if (S.mode === "i2v" || S.mode === "fl2v") { fd.append("files", S.first.file); slots.push("first"); }
  if (S.mode === "fl2v") { fd.append("files", S.last.file); slots.push("last"); }
  if (S.mode === "ref") S.refs.forEach(r => { fd.append("files", r.file); slots.push("ref:" + r.kind); });
  const idea = $("#idea").value.trim();
  fd.append("spec", JSON.stringify({
    mode: S.mode, prompt: $("#prompt").value, slots,
    title: idea ? idea.slice(0, 40) : $("#prompt").value.replace(/^.*?\] ?/s, "").slice(0, 40),
    params: { width: p.width, height: p.height, frames: p.frames, steps: p.steps, reuse: p.reuse,
              layers: p.layers, core_reuse: p.core, token_reduction: p.token, seed: p.seed,
              quality: S.quality },
  }));
  $("#submit").disabled = true;
  try {
    const job = await api("/api/jobs", { method: "POST", body: fd });
    $("#form-error").hidden = true;
    toast("已加入队列");
    window.App && App.select(job.id);
  } catch (e) {
    $("#form-error").textContent = e.message; $("#form-error").hidden = false;
  } finally { update(); }
}

/* reuse settings from a finished job */
function applyJob(job) {
  const m = ["t2v", "i2v", "fl2v", "ref"].includes(job.mode) ? job.mode : "t2v";
  setMode(m);
  if (job.prompt) { $("#prompt").value = job.prompt; store.set("prompt", job.prompt); }
  const p = job.params || {};
  if (p.frames) S.k = Math.min(K_MAX, Math.max(K_MIN, Math.ceil((p.frames - 5) / 17)));
  if (p.variant) S.variant = p.variant;
  if (p.width && p.height) {
    S.short = Math.min(p.width, p.height) <= 544 ? 480 : 768;
    const hit = Object.keys(ASPECTS).find(k => { const [w, h] = resolveCanvas(...ASPECTS[k], S.short); return w === p.width && h === p.height; });
    S.aspect = hit || (m === "i2v" || m === "fl2v" ? "auto" : "16:9");
  }
  if (p.quality && QUALITY[p.quality]) S.quality = p.quality;
  $("#duration").value = S.k;
  if (p.steps) $("#steps").value = p.steps;
  if (p.reuse) $("#reuse").value = p.reuse;
  if (p.layers) $("#layers").value = p.layers;
  $("#core").value = p.core_reuse || 1;
  $("#token-red").checked = !!p.token_reduction;
  if (p.seed != null) $("#seed").value = p.seed;
  update();
  if (m !== "t2v") toast("参数已载入；参考素材需要重新添加");
}

/* ---------- init ---------- */
function initCreate(status) {
  S.status = status;
  $$("#modes button").forEach(b => {
    if (b.dataset.mode === "ref" && !status.ref2va) { b.disabled = true; b.title = "Ref2VA 权重未安装"; }
    b.onclick = () => !b.disabled && setMode(b.dataset.mode);
  });
  $("#camera-chips").innerHTML = CAMERA.map(([zh, en], i) => `<button data-i="${i}" title="${esc(en)}">${zh}</button>`).join("");
  $("#camera-chips").onclick = e => { const b = e.target.closest("button"); if (b) insertAtCursor($("#prompt"), CAMERA[+b.dataset.i][1]); };

  const assets = $("#assets");
  assets.onclick = e => {
    const clr = e.target.closest("[data-clear]"); if (clr) { S[clr.dataset.clear] = null; renderAssets(); update(); return; }
    const del = e.target.closest("[data-del]"); if (del) { S.refs.splice(+del.dataset.del, 1); renderAssets(); update(); return; }
    const ins = e.target.closest("[data-ins]"); if (ins) { insertAtCursor($("#prompt"), ins.dataset.ins); return; }
    const d = e.target.closest(".drop"); if (d) pickFiles(d.dataset.slot);
  };
  assets.onchange = e => { const s = e.target.closest("[data-kind]"); if (s) { S.refs[+s.dataset.kind].kind = s.value; renderAssets(); update(); } };
  assets.ondragover = e => { const d = e.target.closest(".drop"); if (d) { e.preventDefault(); d.classList.add("over"); } };
  assets.ondragleave = e => { const d = e.target.closest(".drop"); if (d) d.classList.remove("over"); };
  assets.ondrop = e => { const d = e.target.closest(".drop"); if (d) { e.preventDefault(); addFiles(d.dataset.slot, e.dataTransfer.files); } };

  $("#duration").value = S.k;
  $("#duration").oninput = e => { S.k = +e.target.value; update(); };
  $("#idea").value = store.get("idea", ""); $("#prompt").value = store.get("prompt", "");
  $("#idea").oninput = e => store.set("idea", e.target.value);
  $("#prompt").oninput = e => { store.set("prompt", e.target.value); update(); };
  ["#steps", "#reuse", "#layers", "#core", "#token-red", "#seed"].forEach(s => $(s).addEventListener("input", update));
  $("#optimize").onclick = optimize;
  $("#submit").onclick = submit;
  applyQuality(); renderAssets(); update(); refreshClaude();
}
