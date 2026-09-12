"""H3 Studio: local web UI for antirez/h3.c (MiniMax-H3 on Apple Silicon).

Runs h3 as a subprocess per job, one at a time, parses its stderr progress
(`\r%-25s %4d/%-4d`) and `h3 profile:` lines, and streams job state plus
CPU/GPU/memory telemetry to the browser over Server-Sent Events.
Binds to 127.0.0.1 only.
"""
import asyncio, functools, json, os, re, shutil, signal, subprocess, time, uuid
from contextlib import asynccontextmanager
from pathlib import Path

import psutil
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import claude_opt

# Paths default to this repository's layout; override with environment
# variables to point at an existing h3 build, model directory or data folder.
REPO = Path(__file__).resolve().parent.parent
H3_BIN = Path(os.environ.get("H3_BIN", REPO / "third_party" / "h3.c" / "h3"))
MODEL = Path(os.environ.get("H3_MODEL_DIR", REPO / "models" / "MiniMax-H3"))
# Optional Turbo variant: the same checkpoint with a step-distillation LoRA
# folded into its transformers. Absent unless the user builds it (docs/TURBO.md);
# the UI then shows Turbo as 未安装 and refuses to select it.
TURBO_MODEL = Path(os.environ.get("H3_TURBO_MODEL_DIR", REPO / "models" / "MiniMax-H3-turbo"))
DATA = Path(os.environ.get("H3_DATA_DIR", REPO / "webui" / "data"))
CLI_OUTPUTS = H3_BIN.parent / "outputs"   # clips made with the h3 CLI get imported


def _device_name():
    try:
        return subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                              capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception:
        return ""


DEVICE = _device_name()
JOBS, UPLOADS, OUTPUTS, THUMBS = (DATA / d for d in ("jobs", "uploads", "outputs", "thumbs"))
for d in (JOBS, UPLOADS, OUTPUTS, THUMBS):
    d.mkdir(parents=True, exist_ok=True)

PROGRESS_RE = re.compile(r"^(?P<phase>[A-Za-z][A-Za-z0-9 ]*?)\s+(?P<done>\d+)/(?P<total>\d+)\s*$")
PROFILE_RE = re.compile(r"^h3 profile:\s+(?P<comp>.+?)\s{2,}(?P<phase>\S.*?)\s+wall=\s*(?P<wall>[\d.]+)s.*?peak=\s*(?P<peak>[\d.]+)GiB")
MAX_PIXELS = 768 * 1344

# Sexualised depictions of minors or childlike characters are refused outright,
# for generation and for the Claude optimizer. A prompt is blocked when it has
# both a sexual term and a minor/childlike term.
_SEXUAL = re.compile(r"脱衣|裸|色情|情色|性爱|性交|性感|露点|挑逗|诱惑|内衣|成人向|\bnsfw\b|\bnude|\bnaked"
                     r"|\bstrip|\bsex|\bporn|\berotic|\blingerie|\bseductive", re.I)
_MINOR = re.compile(r"萝莉|幼女|幼童|女童|男童|小女孩|小男孩|儿童|孩子|未成年|少女|少年|娇小|稚嫩|初中|小学|中学生|女学生"
                    r"|\bloli|\bshota|\bchild|\bkids?\b|\bminors?\b|\bunderage|\bteens?\b|\bschool ?girl|\byoung girl", re.I)
SAFETY_MSG = "内容把未成年人或儿童形象性化，H3 Studio 不会生成，也不会送去优化。"


def violates_minor_safety(*texts):
    t = " ".join(x or "" for x in texts)
    return bool(_SEXUAL.search(t) and _MINOR.search(t))


jobs: dict[str, dict] = {}
queue: asyncio.Queue = asyncio.Queue()
subscribers: set[asyncio.Queue] = set()
current_proc: asyncio.subprocess.Process | None = None


# ---------- persistence ----------
def save(job):
    (JOBS / f"{job['id']}.json").write_text(json.dumps(job, ensure_ascii=False, indent=1))


def load_jobs():
    for f in sorted(JOBS.glob("*.json")):
        try:
            j = json.loads(f.read_text())
        except Exception:
            continue
        if j.get("status") in ("queued", "running"):
            j["status"], j["error"] = "failed", "服务重启时任务中断"
            save(j)
        jobs[j["id"]] = j


def import_legacy():
    """Register clips generated from the command line (outside the UI).
    Skips files still being written (modified in the last 10 s)."""
    known = {j.get("output") for j in jobs.values()}
    added = []
    for mp4 in sorted(CLI_OUTPUTS.glob("*.mp4")):
        if str(mp4) in known or time.time() - mp4.stat().st_mtime < 10:
            continue
        jid = "cli-" + mp4.stem
        jobs[jid] = {"id": jid, "status": "done", "mode": "cli", "title": mp4.stem,
                     "prompt": "", "params": {}, "output": str(mp4),
                     "created": mp4.stat().st_mtime, "finished": mp4.stat().st_mtime,
                     "stages": {}, "profile": [], "imported": True}
        save(jobs[jid])
        added.append(jobs[jid])
    return added


async def rescan_loop():
    while True:
        await asyncio.sleep(30)
        for job in import_legacy():
            await asyncio.to_thread(make_thumb, job)
            save(job)
            broadcast("job", public(job))


# ---------- events ----------
def broadcast(kind, payload):
    msg = f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
    for q in list(subscribers):
        if q.qsize() < 200:
            q.put_nowait(msg)


def public(job):
    j = {k: v for k, v in job.items() if k != "log"}
    j["log_tail"] = job.get("log", [])[-40:]
    return j


# ---------- telemetry ----------
def gpu_stats():
    try:
        out = subprocess.run(["ioreg", "-r", "-d", "1", "-w", "0", "-c", "IOAccelerator"],
                             capture_output=True, text=True, timeout=2).stdout
    except Exception:
        return {}
    def num(key):
        m = re.search(rf'"{re.escape(key)}"=(\d+)', out)
        return int(m.group(1)) if m else None
    return {"util": num("Device Utilization %"), "renderer": num("Renderer Utilization %"),
            "mem_used": num("In use system memory"), "mem_alloc": num("Alloc system memory")}


def external_h3():
    mine = current_proc.pid if current_proc else None
    for p in psutil.process_iter(["pid", "name", "exe"]):
        try:
            if p.info["name"] == "h3" and p.info["pid"] != mine:
                return p.info["pid"]
        except psutil.Error:
            pass
    return None


async def telemetry_loop():
    psutil.cpu_percent(None)
    while True:
        await asyncio.sleep(1)
        vm = psutil.virtual_memory()
        g = await asyncio.to_thread(gpu_stats)
        broadcast("telemetry", {"t": time.time(), "cpu": psutil.cpu_percent(None),
                                "mem_used": vm.total - vm.available, "mem_total": vm.total,
                                "gpu": g, "external_h3": external_h3()})


# ---------- job building ----------
OFFICIAL_MIN_FRAMES, OFFICIAL_MAX_FRAMES = 107, 362   # 4-15 s aligned to 5+17k


def align_frames(n):
    """Snap up to the next 5 + 17k, like diffusers align_num_frames."""
    return 5 + 17 * max(0, -(-(n - 5) // 17))


def official_canvas(aw, ah, short_edge=768):
    """diffusers MiniMax-H3 resolve_canvas_size for the released checkpoint:
    short edge 768 (480 for the faster tier), area capped at 768*1344, axes
    rounded to the nearest 32.
    Rounding can push the area slightly over the cap (2.39:1 gives 1568x672),
    which h3.c rejects, so the long axis then steps down by 32 until it fits.
    Mirrored by resolveCanvas() in static/create.js."""
    ratio = min(4.0, max(0.25, aw / ah))
    w, h = (short_edge * ratio, float(short_edge)) if ratio >= 1 else (float(short_edge), short_edge / ratio)
    if w * h > MAX_PIXELS:
        s = (MAX_PIXELS / (w * h)) ** 0.5
        w, h = w * s, h * s
    w, h = max(32, round(w / 32) * 32), max(32, round(h / 32) * 32)
    while w * h > MAX_PIXELS:
        if w >= h:
            w -= 32
        else:
            h -= 32
    return w, h


# Every canvas the resolver can produce for a ratio in [1/4, 4]. A job's size
# must be one of these, so arbitrary sizes cannot slip in through the API.
OFFICIAL_CANVASES = {official_canvas(4 ** (i / 20000 * 2 - 1), 1, s)
                     for i in range(20001) for s in (768, 480)}


# Turbo is a distilled schedule: it runs few steps and has none of the
# redundancy that reuse / core-reuse / token reduction exploit, so those are
# refused rather than silently ignored. lightx2v's 768p Ref2VA adapter is
# trained for video shift 6 (h3.c reads H3_VIDEO_SHIFT; see docs/TURBO.md).
TURBO_STEPS = {"ref": (8, 8), "other": (5, 8)}


def model_dir_for(job):
    return TURBO_MODEL if job["params"].get("variant") == "turbo" else MODEL


def build_argv(job):
    p = job["params"]
    # the browser sends the short names (create.js params()), the API and older
    # job records the long ones; accept both so no flag is silently dropped
    core_reuse = int(p.get("core_reuse", p.get("core", 1)) or 1)
    token_reduction = bool(p.get("token_reduction", p.get("token", False)))
    try:
        w, h = int(p["width"]), int(p["height"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("缺少画布尺寸：params 里需要 width 和 height")
    variant = p.get("variant", "base")
    if variant not in ("base", "turbo"):
        raise ValueError("未知模型：只有 base 和 turbo")
    model = model_dir_for(job)
    job["env"] = {}
    if variant == "turbo":
        if not turbo_ready(job["mode"]):
            raise ValueError("Turbo 权重未安装，构建方法见 docs/TURBO.md")
        lo, hi = TURBO_STEPS["ref" if job["mode"] == "ref" else "other"]
        if not lo <= int(p.get("steps", 6)) <= hi:
            raise ValueError(f"Turbo 在这个模式下只支持 {lo} 到 {hi} 步")
        if int(p.get("reuse", 1)) > 1 or core_reuse > 1 or token_reduction:
            raise ValueError("Turbo 不能与 reuse、core-reuse 或 token 缩减同时使用")
        if job["mode"] == "ref":
            job["env"]["H3_VIDEO_SHIFT"] = "6"
    if (w, h) not in OFFICIAL_CANVASES:
        raise ValueError(f"{w}×{h} 不是 MiniMax-H3 官方 768p 画布（例如 16:9 为 1344×768）")
    frames = align_frames(int(p.get("frames", 124)))
    # Official duration is 4-15 s. This also satisfies h3's own floors (22
    # frames per decoder chunk, 56 when a reference video/audio is attached).
    if not OFFICIAL_MIN_FRAMES <= frames <= OFFICIAL_MAX_FRAMES:
        raise ValueError(f"时长需在官方的 4 到 15 秒之间（{OFFICIAL_MIN_FRAMES} 到 {OFFICIAL_MAX_FRAMES} 帧），当前 {frames} 帧")
    steps = int(p.get("steps", 20))
    if not 2 <= steps <= 100:
        raise ValueError("步数需在 2 到 100 之间")
    argv = [str(H3_BIN), "--profile", "-d", str(model), "-p", job["prompt"],
            "--width", str(w), "--height", str(h), "--frames", str(frames),
            "--steps", str(steps), "--layers", str(int(p.get("layers", 50))),
            "--seed", str(int(p.get("seed", 42))), "-o", job["output"]]
    if core_reuse > 1:
        argv += ["--core-reuse", str(core_reuse)]
    else:
        argv += ["--reuse", str(int(p.get("reuse", 1)))]
    if token_reduction:
        argv.append("--token-reduction")
    if p.get("render_width") and p.get("render_height"):
        argv += ["--render-width", str(int(p["render_width"])),
                 "--render-height", str(int(p["render_height"]))]
    refs = job.get("refs", {})
    if job["mode"] in ("i2v", "fl2v"):
        if not refs.get("first"):
            raise ValueError("缺少首帧图片")
        argv += ["--first-frame", refs["first"]]
        if job["mode"] == "fl2v":
            if not refs.get("last"):
                raise ValueError("缺少尾帧图片")
            argv += ["--last-frame", refs["last"]]
    elif job["mode"] == "ref":
        items = refs.get("items", [])
        if not any(i["kind"] in ("image", "video", "silent_video") for i in items):
            raise ValueError("参考生视频至少需要一张图片或一段视频")
        flag = {"image": "--ref-image", "video": "--ref-video",
                "silent_video": "--ref-silent-video", "audio": "--ref-audio"}
        for i in items:
            argv += [flag[i["kind"]], i["path"]]
        if p.get("ref_image_size") == "max":
            argv += ["--ref-image-size", "max"]
    return argv, frames


# ---------- runner ----------
def handle_line(job, line):
    line = line.strip()
    if not line:
        return False
    m = PROGRESS_RE.match(line)
    now = time.time()
    if m:
        ph, done, total = m["phase"].strip(), int(m["done"]), int(m["total"])
        st = job["stages"].setdefault(ph, {"done": 0, "total": total, "start": now, "order": len(job["stages"])})
        if ph == "denoise" and done > st["done"]:
            job.setdefault("step_times", []).append([done, now])
        st.update(done=done, total=total)
        if done >= total:
            st.setdefault("end", now)
        job["phase"] = ph
        return True
    job.setdefault("log", []).append(line)
    pm = PROFILE_RE.match(line)
    if pm:
        job["profile"].append({"component": pm["comp"].strip(), "phase": pm["phase"].strip(),
                               "wall": float(pm["wall"]), "peak_gib": float(pm["peak"])})
    elif line.startswith("h3:") and not line.startswith("h3: wrote"):
        # h3 also prints informational "h3:" lines (tile plan, reuse schedule),
        # so this only becomes the error if the process exits non-zero
        job["last_h3_msg"] = line[3:].strip()
    return True


async def run_job(job):
    global current_proc
    while external_h3():
        job["phase"] = "等待外部 h3 进程结束"
        broadcast("job", public(job))
        await asyncio.sleep(5)
    try:
        argv, frames = build_argv(job)
    except ValueError as e:
        job.update(status="failed", error=str(e), finished=time.time())
        return
    job["params"]["frames"] = frames
    job.update(status="running", started=time.time(), argv=argv[:])
    broadcast("job", public(job))
    # Decode with the checkpoint's own vae_tile_size (256). Unpatched h3.c picks
    # 304-320 px tiles for every 768p canvas, which leaves a 16 px ViT patch
    # grid in flat areas (antirez/h3.c PR #1).
    env = {**os.environ, "H3_VAE_TILE_PIXELS": "256", **job.get("env", {})}
    current_proc = await asyncio.create_subprocess_exec(
        *argv, cwd=str(H3_BIN.parent), env=env, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT)
    buf, last_push = b"", 0.0
    while True:
        chunk = await current_proc.stdout.read(4096)
        if not chunk:
            break
        buf += chunk
        parts = re.split(rb"[\r\n]", buf)
        buf = parts.pop()
        changed = False
        for part in parts:
            changed |= handle_line(job, part.decode("utf-8", "replace"))
        if changed and time.time() - last_push > 0.3:
            broadcast("job", public(job)); last_push = time.time()
    if buf:
        handle_line(job, buf.decode("utf-8", "replace"))
    rc = await current_proc.wait()
    current_proc = None
    job["finished"] = time.time()
    if job.get("cancel_requested"):
        job["status"] = "cancelled"
    elif rc == 0 and Path(job["output"]).exists():
        job["status"] = "done"
        await asyncio.to_thread(make_thumb, job)
    else:
        job["status"] = "failed"
        job["error"] = job.get("last_h3_msg") or f"h3 退出码 {rc}"


def make_thumb(job):
    out = THUMBS / f"{job['id']}.jpg"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-ss", "0.4", "-i", job["output"],
                    "-frames:v", "1", "-vf", "scale=640:-2", str(out)], timeout=60)
    if out.exists():
        job["thumb"] = out.name


async def worker():
    while True:
        jid = await queue.get()
        job = jobs.get(jid)
        if not job or job["status"] != "queued":
            continue
        try:
            await run_job(job)
        except Exception as e:
            job.update(status="failed", error=f"内部错误: {e}", finished=time.time())
        save(job)
        broadcast("job", public(job))


# ---------- app ----------
@asynccontextmanager
async def lifespan(_app):
    load_jobs()
    import_legacy()
    for j in jobs.values():
        if j.get("status") == "done" and not j.get("thumb") and Path(j.get("output", "")).exists():
            await asyncio.to_thread(make_thumb, j); save(j)
    tasks = [asyncio.create_task(worker()), asyncio.create_task(telemetry_loop()),
             asyncio.create_task(rescan_loop())]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="H3 Studio", lifespan=lifespan)


# h3.c only inventories Ref2VA/transformer at startup, but a Ref2VA job reads
# the whole Ref2VA partition (h3.c:921-930): tokenizer, text encoder, DiT and
# both VAEs. Report Ref2VA as ready only when all of them are present.
REF2VA_REQUIRED = ("transformer/model.safetensors.index.json", "tokenizer/tokenizer.json",
                   "text_encoder/config.json", "video_vae/source/config.json", "audio_vae/config.json")


def ref2va_ready():
    return all((MODEL / "Ref2VA" / p).exists() for p in REF2VA_REQUIRED)


@functools.cache
def engine_has_shift_override():
    """Upstream h3.c hardcodes the video sigma shift (12). lightx2v's 768p
    Ref2VA adapter is trained for shift 6, so H3_VIDEO_SHIFT only means
    something on a build that reads it (see docs/TURBO.md). Probe the binary
    instead of assuming: on a stock build it would be set and silently ignored."""
    try:
        out = subprocess.run(["strings", str(H3_BIN)], capture_output=True,
                             text=True, timeout=30).stdout
    except Exception:
        return False
    return "H3_VIDEO_SHIFT" in out


def turbo_ready(mode=None):
    if not (TURBO_MODEL / "FL2VA/transformer/config.json").exists():
        return False
    if mode == "ref":
        return (engine_has_shift_override()
                and all((TURBO_MODEL / "Ref2VA" / p).exists() for p in REF2VA_REQUIRED))
    return True


@app.get("/api/status")
def status():
    return {"h3": H3_BIN.exists(), "fl2va": (MODEL / "FL2VA/transformer/config.json").exists(),
            "ref2va": ref2va_ready(), "external_h3": external_h3(), "device": DEVICE,
            "turbo": turbo_ready(), "turbo_ref": turbo_ready("ref")}


@app.get("/api/jobs")
def list_jobs():
    return sorted((public(j) for j in jobs.values()), key=lambda j: j.get("created", 0), reverse=True)


@app.get("/api/jobs/{jid}/log")
def job_log(jid: str):
    if jid not in jobs:
        raise HTTPException(404)
    return {"log": jobs[jid].get("log", [])}


REF_AUDIO_VIDEO_MIN_S = 56 / 24


def probe_duration(path):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", path], capture_output=True, text=True, timeout=20)
        return float(out.stdout.strip())
    except Exception:
        return None


def store_upload(f: UploadFile) -> str:
    ext = Path(f.filename or "").suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".mp4", ".mov", ".m4v",
                   ".webm", ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}:
        raise HTTPException(400, f"不支持的文件类型 {ext}")
    dest = UPLOADS / f"{uuid.uuid4().hex[:12]}{ext}"
    with dest.open("wb") as out:
        shutil.copyfileobj(f.file, out)
    return str(dest)


@app.post("/api/jobs")
async def create_job(spec: str = Form(...), files: list[UploadFile] = File(default=[])):
    try:
        s = json.loads(spec)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"spec 不是合法 JSON：{e.msg}（第 {e.colno} 列）")
    if not isinstance(s, dict):
        raise HTTPException(400, "spec 必须是 JSON 对象")
    mode = s.get("mode")
    if mode not in ("t2v", "i2v", "fl2v", "ref"):
        raise HTTPException(400, "未知模式")
    if not s.get("prompt", "").strip():
        raise HTTPException(400, "提示词为空")
    if violates_minor_safety(s.get("prompt"), s.get("title")):
        raise HTTPException(400, SAFETY_MSG)
    stored = []
    try:
        for f in files:
            stored.append(store_upload(f))
        return await _create_job(s, mode, stored)
    except HTTPException:
        # a rejected request must not leave its uploads behind
        for path in stored:
            Path(path).unlink(missing_ok=True)
        raise


async def _create_job(s, mode, stored):
    refs = {}
    slots = s.get("slots", [])   # parallel to files: "first" | "last" | "ref:<kind>"
    for slot, path in zip(slots, stored):
        if slot in ("first", "last"):
            refs[slot] = path
        elif slot.startswith("ref:"):
            refs.setdefault("items", []).append({"kind": slot[4:], "path": path})
    if mode == "ref" and not ref2va_ready():
        missing = [p.split("/")[0] for p in REF2VA_REQUIRED if not (MODEL / "Ref2VA" / p).exists()]
        raise HTTPException(400, f"Ref2VA 权重不完整，缺少：{', '.join(missing)}")
    for n, item in enumerate(refs.get("items", []), 1):
        if item["kind"] == "video":
            # h3 resamples a reference video to 24 fps and then rounds the frame
            # count DOWN to 5+17k (h3_ffmpeg.c); its soundtrack needs >= 2 s, so
            # the video must give >= 56 frames, i.e. about 2.34 s.
            dur = probe_duration(item["path"])
            if dur is not None and dur < REF_AUDIO_VIDEO_MIN_S:
                raise HTTPException(400, f"第 {n} 个参考素材是带音轨的视频，只有 {dur:.2f} 秒；"
                                         f"h3 要求至少 {REF_AUDIO_VIDEO_MIN_S:.2f} 秒。换更长的片段，或改选「静音」")
    jid = time.strftime("%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    job = {"id": jid, "status": "queued", "mode": mode, "title": s.get("title") or "",
           "prompt": s["prompt"], "params": s.get("params", {}), "refs": refs,
           "output": str(OUTPUTS / f"{jid}.mp4"), "created": time.time(),
           "stages": {}, "profile": [], "log": []}
    try:
        build_argv(job)
    except ValueError as e:
        raise HTTPException(400, str(e))
    jobs[jid] = job
    save(job)
    await queue.put(jid)
    broadcast("job", public(job))
    return public(job)


@app.delete("/api/jobs/{jid}")
async def cancel_job(jid: str):
    job = jobs.get(jid)
    if not job:
        raise HTTPException(404)
    if job["status"] == "queued":
        job.update(status="cancelled", finished=time.time())
    elif job["status"] == "running" and current_proc:
        job["cancel_requested"] = True
        current_proc.send_signal(signal.SIGTERM)
    save(job)
    broadcast("job", public(job))
    return public(job)


@app.get("/api/events")
async def events():
    q: asyncio.Queue = asyncio.Queue()
    subscribers.add(q)

    async def stream():
        try:
            yield "retry: 2000\n\n"
            while True:
                try:
                    yield await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            subscribers.discard(q)
    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@app.get("/media/{jid}")
def media(jid: str):
    job = jobs.get(jid)
    if not job or not Path(job.get("output", "")).exists():
        raise HTTPException(404)
    return FileResponse(job["output"], media_type="video/mp4")


@app.get("/thumb/{name}")
def thumb(name: str):
    p = THUMBS / Path(name).name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/api/claude/status")
async def claude_status(recheck: bool = False):
    return await asyncio.to_thread(claude_opt.status, recheck)


@app.post("/api/claude/optimize")
async def claude_optimize(body: dict):
    if violates_minor_safety(body.get("idea"), body.get("draft")):
        raise HTTPException(400, SAFETY_MSG)
    try:
        prompt = await claude_opt.optimize(
            body.get("mode", "t2v"), body.get("idea", ""), float(body.get("duration", 5)),
            list(body.get("labels", [])), body.get("draft", ""))
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))
    return {"prompt": prompt, "engine": claude_opt.status()["engine"]}


@app.post("/api/jobs/{jid}/reveal")
def reveal(jid: str):
    job = jobs.get(jid)
    if not job or not Path(job.get("output", "")).exists():
        raise HTTPException(404)
    subprocess.run(["open", "-R", job["output"]])
    return {"ok": True}


class NoCacheStatic(StaticFiles):
    """Make browsers revalidate the UI files on every load. Without this a
    reload kept serving a stale app.js after an update."""
    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app.mount("/", NoCacheStatic(directory=str(Path(__file__).parent / "static"), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    # 127.0.0.1 by default: the UI can start processes and read local files, so
    # only expose it on a network (H3_HOST=0.0.0.0) behind something you trust.
    uvicorn.run(app, host=os.environ.get("H3_HOST", "127.0.0.1"),
                port=int(os.environ.get("PORT", 7870)), log_level="warning")
