/* H3 Studio: stage (live job), pipeline, gallery, queue, telemetry, SSE. */

const STAGE_NAMES = {
  "tokenizer": "分词", "text encoder": "文本编码", "refine text": "文本精炼",
  "Qwen vision": "视觉编码", "video VAE encoder": "参考视频编码", "audio VAE encoder": "参考音频编码",
  "precompute AdaLN": "AdaLN 预计算", "load transformer core": "载入 DiT", "denoise": "去噪",
  "preview VAE load": "预览 VAE", "audio VAE": "音频解码", "video VAE load": "视频解码", "FFmpeg": "封装 MP4",
};
const POST = new Set(["audio VAE", "video VAE load", "FFmpeg"]);
const MODE_NAMES = { t2v: "文生视频", i2v: "首帧生视频", fl2v: "首尾帧", ref: "参考生视频", cli: "命令行" };
const STATUS_NAMES = { queued: "排队中", running: "生成中", done: "完成", failed: "失败", cancelled: "已取消" };

const App = {
  jobs: new Map(), selected: null, status: {}, sse: "connecting",
  tele: [], teleMax: 300,
};
window.App = App;

/* ---------- progress math ---------- */
function progressOf(job) {
  const st = job.stages || {};
  const names = Object.keys(st);
  if (job.status === "done") return 1;
  if (!names.length) return 0;
  const pre = names.filter(n => n !== "denoise" && !POST.has(n));
  const post = names.filter(n => POST.has(n));
  const frac = n => Math.min(1, st[n].done / Math.max(1, st[n].total));
  const preF = pre.length ? pre.reduce((a, n) => a + frac(n), 0) / pre.length : 0;
  const den = st.denoise ? frac("denoise") : 0;
  const postF = post.length ? post.reduce((a, n) => a + frac(n), 0) / 3 : 0;
  return Math.min(0.999, 0.03 * (st.denoise ? 1 : preF) + 0.85 * den + 0.12 * postF);
}
function etaOf(job) {
  if (job.status !== "running") return null;
  const p = job.params || {};
  const x = (p.width || 0) * (p.height || 0) * (p.frames || 0);
  const decode = 2.1e-6 * x + 5;
  const d = job.stages && job.stages.denoise;
  const times = job.step_times || [];
  if (d && d.done >= d.total) return decode * 0.5;
  if (d && times.length >= 2) {
    const [s0, t0] = times[0], [s1, t1] = times[times.length - 1];
    const per = (t1 - t0) / Math.max(1, s1 - s0);
    const sinceLast = Date.now() / 1000 - t1;
    return Math.max(0, (d.total - d.done) * per - Math.min(sinceLast, per)) + decode;
  }
  const est = estimate({ width: p.width, height: p.height, frames: p.frames, steps: p.steps || 20,
    reuse: p.reuse || 1, layers: p.layers || 50, core: p.core_reuse || 1, token: !!p.token_reduction });
  return Math.max(0, est.total - (Date.now() / 1000 - (job.started || Date.now() / 1000)));
}

/* ---------- stage ---------- */
function pipelineHTML(job) {
  const st = job.stages || {};
  const names = Object.keys(st).sort((a, b) => (st[a].order ?? 0) - (st[b].order ?? 0));
  return `<div class="pipeline">${names.map(n => {
    const s = st[n], f = Math.min(1, s.done / Math.max(1, s.total));
    const cls = f >= 1 ? "done" : job.phase === n && job.status === "running" ? "active" : "";
    const dur = s.end && s.start ? ` · ${fmtDur(s.end - s.start)}` : "";
    return `<div class="pstep ${cls}"><div class="n"><span>${esc(STAGE_NAMES[n] || n)}</span><span>${f >= 1 ? "✓" : ""}</span></div>
      <div class="v">${s.done}/${s.total}${dur}</div><div class="mini"><div style="width:${f * 100}%"></div></div></div>`;
  }).join("")}</div>`;
}

function paramLine(job) {
  const p = job.params || {};
  if (!p.width) return MODE_NAMES[job.mode] || "";
  const bits = [MODE_NAMES[job.mode], p.variant === "turbo" ? "Turbo" : "原版",
                `${p.width}×${p.height}`, `${(p.frames / 24).toFixed(2)} 秒`, `${p.steps} 步`];
  if ((p.core_reuse || 1) > 1) bits.push(`core-reuse ${p.core_reuse}`); else if (p.reuse > 1) bits.push(`reuse ${p.reuse}`);
  if (p.token_reduction) bits.push("token 缩减");
  if (p.layers && p.layers < 50) bits.push(`${p.layers} 层`);
  bits.push(`seed ${p.seed}`);
  return bits.join(" · ");
}

function renderStage() {
  const el = $("#stage");
  const job = App.jobs.get(App.selected);
  if (!job) {
    el.innerHTML = `<div class="stage-empty"><div><div class="big">▶</div>
      <p>在左侧写下想法，点「Claude 优化」生成官方格式提示词，<br>再点「生成」。进度、每个阶段的耗时和 GPU 占用会实时显示在这里。</p></div></div>`;
    return;
  }
  const title = job.title || job.id;
  const head = `<div class="stage-top"><div><div class="stage-title">${esc(title)}</div>
    <div class="stage-sub">${esc(paramLine(job))}</div></div>
    <span class="badge ${job.status}">${STATUS_NAMES[job.status] || job.status}</span></div>`;

  if (job.status === "running" || job.status === "queued") {
    const pr = progressOf(job), eta = etaOf(job);
    const d = job.stages && job.stages.denoise;
    const elapsed = job.started ? Date.now() / 1000 - job.started : 0;
    el.innerHTML = head + `
      <div class="hero"><div class="pct">${Math.floor(pr * 100)}%</div>
        <div class="facts">
          <span>阶段 <b>${esc(STAGE_NAMES[job.phase] || job.phase || "等待开始")}</b></span>
          ${d ? `<span>去噪 <b>${d.done}/${d.total}</b></span>` : ""}
          <span>已用 <b>${fmtDur(elapsed)}</b></span>
          <span>剩余约 <b>${fmtDur(eta)}</b></span>
        </div></div>
      <div class="bar"><div style="width:${pr * 100}%"></div></div>
      ${pipelineHTML(job)}
      <div class="stage-actions"><button class="ghost" data-act="cancel">取消任务</button>
        <button class="ghost" data-act="log">${App.logFor === job.id ? "收起日志" : "查看日志"}</button></div>
      ${logHTML(job)}
      <div class="prompt-box">${esc(job.prompt)}</div>`;
  } else if (job.status === "done") {
    const prof = (job.profile || []).map(r => `<tr><td>${esc(r.component)}</td><td>${esc(r.phase)}</td>
      <td class="num">${fmtDur(r.wall)}</td><td class="num">${r.peak_gib.toFixed(1)} GiB</td></tr>`).join("");
    const took = job.started && job.finished ? fmtDur(job.finished - job.started) : null;
    el.innerHTML = head + `
      <video class="player" src="/media/${encodeURIComponent(job.id)}" controls loop playsinline preload="metadata"
        ${job.params && job.params.width ? `style="aspect-ratio:${job.params.width}/${job.params.height}"` : `style="aspect-ratio:16/9"`}
        ${job.thumb ? `poster="/thumb/${encodeURIComponent(job.thumb)}"` : ""}></video>
      <div class="stage-actions">
        ${took ? `<span class="muted small" style="align-self:center">总耗时 ${took}</span>` : ""}
        ${job.prompt ? `<button class="ghost" data-act="reuse">复用参数</button>` : ""}
        <button class="ghost" data-act="reveal">在访达中显示</button>
        <a class="ghost" href="/media/${encodeURIComponent(job.id)}" download="${esc(job.id)}.mp4">下载</a>
        ${job.log_tail && job.log_tail.length ? `<button class="ghost" data-act="log">${App.logFor === job.id ? "收起日志" : "查看日志"}</button>` : ""}
      </div>
      ${logHTML(job)}
      ${prof ? `<table class="profile-table"><tr><td class="muted">组件</td><td class="muted">阶段</td><td class="num muted">耗时</td><td class="num muted">峰值</td></tr>${prof}</table>` : ""}
      ${job.prompt ? `<div class="prompt-box">${esc(job.prompt)}</div>` : ""}`;
  } else {
    el.innerHTML = head + `<p class="error">${esc(job.error || "任务未完成")}</p>
      ${pipelineHTML(job)}
      <div class="stage-actions"><button class="ghost" data-act="reuse">复用参数重试</button>
      <button class="ghost" data-act="log">查看日志</button></div>
      <div class="logbox">${esc((job.log_tail || []).join("\n"))}</div>`;
  }
}

async function stageAction(act) {
  const job = App.jobs.get(App.selected);
  if (!job) return;
  if (act === "cancel" && confirm("确定取消这个任务？已经算过的部分不会保留。")) {
    try { await api(`/api/jobs/${job.id}`, { method: "DELETE" }); } catch (e) { toast(e.message, true); }
  } else if (act === "reuse") applyJob(job);
  else if (act === "reveal") api(`/api/jobs/${job.id}/reveal`, { method: "POST" }).catch(e => toast(e.message, true));
  else if (act === "log") {
    // The stage re-renders every second while a job runs, so the log box is
    // part of renderStage (see logHTML) instead of a one-off appended node.
    // a finished job's playing <video> is normally left alone by schedule();
    // clear its marker so the stage re-renders with or without the log box
    const v = $("#stage video"); if (v) v.dataset.id = "";
    if (App.logFor === job.id) { App.logFor = null; schedule(); return; }
    App.logFor = job.id; App.logText = "读取中…";
    await refreshLog(); schedule();
  }
}

async function refreshLog() {
  if (!App.logFor) return;
  try { App.logText = (await api(`/api/jobs/${App.logFor}/log`)).log.join("\n") || "（暂无日志）"; }
  catch (e) { App.logText = "读取日志失败：" + e.message; }
}
function logHTML(job) {
  return App.logFor === job.id ? `<div class="logbox" id="logbox">${esc(App.logText)}</div>` : "";
}

/* ---------- gallery + queue ---------- */
function sortedJobs() { return [...App.jobs.values()].sort((a, b) => (b.created || 0) - (a.created || 0)); }

function renderGallery() {
  const list = sortedJobs().filter(j => j.status !== "cancelled");
  $("#gallery-count").textContent = `${list.filter(j => j.status === "done").length} 条`;
  $("#gallery").innerHTML = list.map(j => {
    const done = j.status === "done";
    const p = j.params || {};
    const meta = p.width ? `${p.width}×${p.height} · ${(p.frames / 24).toFixed(1)} 秒` : MODE_NAMES[j.mode] || "";
    const media = done
      ? `<div class="media" style="${j.thumb ? `background-image:url('/thumb/${encodeURIComponent(j.thumb)}')` : ""}"><span class="tag">${esc(MODE_NAMES[j.mode] || "")}</span></div>`
      : `<div class="media">${j.status === "running" ? `${Math.floor(progressOf(j) * 100)}%` : STATUS_NAMES[j.status]}</div>`;
    return `<div class="card ${done ? "" : "pending"} ${j.id === App.selected ? "sel" : ""}" data-id="${esc(j.id)}">${media}
      <div class="body"><div class="t">${esc(j.title || j.id)}</div><div class="m">${esc(meta)} · ${esc(STATUS_NAMES[j.status])}</div></div></div>`;
  }).join("") || `<p class="muted small">还没有作品。</p>`;
}

function renderQueue() {
  const q = sortedJobs().filter(j => j.status === "queued" || j.status === "running").reverse();
  const ext = App.status.external_h3;
  $("#queue").innerHTML = (ext ? `<div class="qitem"><span class="grow">外部 h3 进程（PID ${ext}）占用 GPU，队列会等它结束</span></div>` : "") +
    (q.map(j => `<div class="qitem"><span class="badge ${j.status}">${STATUS_NAMES[j.status]}</span>
      <span class="grow" title="${esc(j.title)}">${esc(j.title || j.id)}</span>
      <button data-cancel="${esc(j.id)}">取消</button></div>`).join("") || (ext ? "" : `<p class="muted small">队列为空</p>`));
}

function renderChips() {
  const s = App.status, c = S.claude;
  const chip = (cls, text, title = "") => `<span class="chip ${cls}" title="${esc(title)}"><i></i>${esc(text)}</span>`;
  $("#chips").innerHTML = [
    chip(s.fl2va ? "good" : "bad", s.fl2va ? "FL2VA 就绪" : "FL2VA 缺失"),
    chip(s.ref2va ? "good" : "warn", s.ref2va ? "Ref2VA 就绪" : "Ref2VA 未安装"),
    c ? chip(!c.ok ? "bad" : c.verified === false ? "warn" : "good",
             !c.ok ? "Claude 需登录" : c.verified === false ? "Claude 待验证" : "Claude 可用", c.detail) : "",
    chip(App.sse === "open" ? "good" : "warn", App.sse === "open" ? "实时连接" : "连接中…"),
  ].join("");
}

/* ---------- telemetry charts ---------- */
const METRICS = [
  { key: "gpu", name: "GPU 利用率", color: "--series-gpu", unit: "%", max: () => 100, val: t => t.gpu && t.gpu.util },
  { key: "gmem", name: "GPU 占用内存", color: "--series-gpu", unit: "GB", max: t => t.mem_total / 1e9, val: t => t.gpu && t.gpu.mem_used != null ? t.gpu.mem_used / 1e9 : null },
  { key: "cpu", name: "CPU", color: "--series-cpu", unit: "%", max: () => 100, val: t => t.cpu },
  { key: "mem", name: "系统内存", color: "--series-mem", unit: "GB", max: t => t.mem_total / 1e9, val: t => t.mem_used / 1e9 },
];

function buildMetrics() {
  $("#metrics").innerHTML = METRICS.map(m => `<div class="metric" data-k="${m.key}">
    <div class="top"><span class="name"><i style="background:var(${m.color})"></i>${m.name}</span><span class="val" data-v>--</span></div>
    <canvas></canvas><div class="foot" data-f></div></div>`).join("");
  $$("#metrics .metric").forEach(box => {
    const cv = $("canvas", box), m = METRICS.find(x => x.key === box.dataset.k);
    cv.addEventListener("mousemove", e => { box._hover = e.offsetX; drawMetric(box, m); });
    cv.addEventListener("mouseleave", () => { box._hover = null; drawMetric(box, m); });
  });
}

function drawMetric(box, m) {
  const cv = $("canvas", box), data = App.tele;
  const W = cv.clientWidth, H = cv.clientHeight, dpr = window.devicePixelRatio || 1;
  if (cv.width !== W * dpr) { cv.width = W * dpr; cv.height = H * dpr; }
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  const css = getComputedStyle(document.documentElement);
  const color = css.getPropertyValue(m.color).trim(), grid = css.getPropertyValue("--line").trim();
  const last = data[data.length - 1];
  const max = last ? m.max(last) || 100 : 100;
  ctx.strokeStyle = grid; ctx.lineWidth = 1;
  [0.5, 1].forEach(f => { const y = Math.round(H - f * (H - 4)) + 0.5; ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke(); });
  const n = App.teleMax, step = W / (n - 1), off = n - data.length;
  const pts = data.map((t, i) => { const v = m.val(t); return v == null ? null : [(i + off) * step, H - 2 - (Math.min(v, max) / max) * (H - 6)]; });
  const valid = pts.filter(Boolean);
  if (valid.length > 1) {
    ctx.beginPath(); valid.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    ctx.lineTo(valid[valid.length - 1][0], H); ctx.lineTo(valid[0][0], H); ctx.closePath();
    ctx.globalAlpha = 0.1; ctx.fillStyle = color; ctx.fill(); ctx.globalAlpha = 1;
    ctx.beginPath(); valid.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.lineJoin = "round"; ctx.stroke();
  }
  let tip = $(".tip", box);
  if (box._hover != null && data.length) {
    const i = Math.round(box._hover / step) - off;
    const t = data[Math.max(0, Math.min(data.length - 1, i))], p = pts[Math.max(0, Math.min(pts.length - 1, i))];
    if (t && p) {
      ctx.strokeStyle = css.getPropertyValue("--text-2").trim(); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(p[0] + 0.5, 0); ctx.lineTo(p[0] + 0.5, H); ctx.stroke();
      ctx.fillStyle = color; ctx.beginPath(); ctx.arc(p[0], p[1], 4, 0, 7); ctx.fill();
      ctx.strokeStyle = css.getPropertyValue("--card").trim(); ctx.lineWidth = 2; ctx.stroke();
      if (!tip) { tip = document.createElement("div"); tip.className = "tip"; box.appendChild(tip); }
      const v = m.val(t), ago = Math.round(Date.now() / 1000 - t.t);
      tip.textContent = `${v == null ? "--" : v.toFixed(m.unit === "%" ? 0 : 1)}${m.unit} · ${ago} 秒前`;
      tip.style.left = `${cv.offsetLeft + p[0]}px`; tip.style.top = `${cv.offsetTop + p[1]}px`;
    }
  } else if (tip) tip.remove();
  if (last) {
    const v = m.val(last), vals = data.map(m.val).filter(x => x != null), peak = vals.length ? Math.max(...vals) : null;
    $("[data-v]", box).innerHTML = v == null ? "--" : `${v.toFixed(m.unit === "%" ? 0 : 1)}<small>${m.unit}</small>`;
    $("[data-f]", box).textContent = peak == null ? "" : `窗口峰值 ${peak.toFixed(m.unit === "%" ? 0 : 1)}${m.unit}` +
      (m.unit === "GB" ? ` · 总量 ${max.toFixed(0)} GB` : "");
  }
}
function drawMetrics() { $$("#metrics .metric").forEach(box => drawMetric(box, METRICS.find(m => m.key === box.dataset.k))); }

/* ---------- live updates ---------- */
let raf = 0;
function schedule() {
  if (raf) return;
  raf = requestAnimationFrame(() => {
    raf = 0;
    const playing = $("#stage video");
    const job = App.jobs.get(App.selected);
    // don't rebuild a playing video on every progress tick
    if (!(playing && job && job.status === "done" && playing.dataset.id === job.id)) {
      renderStage();
      const v = $("#stage video"); if (v && job) v.dataset.id = job.id;
      const lb = $("#logbox"); if (lb) lb.scrollTop = lb.scrollHeight;
    }
    renderGallery(); renderQueue(); renderChips();
  });
}
App.select = id => { App.selected = id; App.logFor = null; const v = $("#stage video"); if (v) v.dataset.id = ""; schedule(); window.scrollTo({ top: 0, behavior: "smooth" }); };

function upsert(job) {
  const prev = App.jobs.get(job.id);
  App.jobs.set(job.id, job);
  const running = sortedJobs().find(j => j.status === "running");
  if (!App.selected || (running && running.id === job.id && (!prev || prev.status !== "running"))) App.selected = job.id;
  if (prev && prev.status === "running" && job.status === "done") toast(`完成：${job.title || job.id}`);
  if (prev && prev.status === "running" && job.status === "failed") toast(`失败：${job.error || job.title}`, true);
  schedule();
}

function connect() {
  const es = new EventSource("/api/events");
  es.onopen = () => { App.sse = "open"; schedule(); };
  es.onerror = () => { App.sse = "retry"; schedule(); };
  es.addEventListener("job", e => upsert(JSON.parse(e.data)));
  es.addEventListener("telemetry", e => {
    const t = JSON.parse(e.data);
    App.tele.push(t); if (App.tele.length > App.teleMax) App.tele.shift();
    if (t.external_h3 !== App.status.external_h3) { App.status.external_h3 = t.external_h3; schedule(); }
    drawMetrics();
    const j = App.jobs.get(App.selected);
    if (j && j.status === "running") schedule();   // tick elapsed / ETA
    // keep an open log box current while its job runs (every 3 s)
    if (j && App.logFor === j.id && j.status === "running" && (App.logTick = (App.logTick || 0) + 1) % 3 === 0)
      refreshLog().then(schedule);
  });
}

async function boot() {
  buildMetrics();
  try {
    App.status = await api("/api/status");
    initCreate(App.status);
    const list = await api("/api/jobs");
    list.forEach(j => App.jobs.set(j.id, j));
    const running = list.find(j => j.status === "running");
    App.selected = running ? running.id : (list.find(j => j.status === "done") || {}).id || null;
  } catch (e) { toast("连接后端失败：" + e.message, true); }
  schedule(); connect();
  setInterval(async () => { try { App.status = { ...App.status, ...(await api("/api/status")) }; S.status = App.status; schedule(); } catch {} }, 30000);
  setInterval(() => refreshClaude(), 60000);
  window.addEventListener("resize", drawMetrics);
  $("#stage").onclick = e => { const b = e.target.closest("[data-act]"); if (b) stageAction(b.dataset.act); };
  $("#gallery").onclick = e => { const c = e.target.closest(".card"); if (c) App.select(c.dataset.id); };
  $("#gallery").addEventListener("mouseover", e => {
    const c = e.target.closest(".card:not(.pending)"); if (!c || $("video", c)) return;
    const m = $(".media", c); const v = document.createElement("video");
    v.src = `/media/${encodeURIComponent(c.dataset.id)}`; v.muted = true; v.loop = true; v.autoplay = true; v.playsInline = true;
    m.appendChild(v);
    c.addEventListener("mouseleave", () => v.remove(), { once: true });
  });
  $("#queue").onclick = async e => {
    const b = e.target.closest("[data-cancel]"); if (!b) return;
    try { await api(`/api/jobs/${b.dataset.cancel}`, { method: "DELETE" }); } catch (err) { toast(err.message, true); }
  };
}
boot();
