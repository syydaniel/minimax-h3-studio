"""Claude prompt optimizer for H3 Studio.

Turns a rough idea (often Chinese) into a final MiniMax-H3 prompt that follows
the official writing guides in ./guides. Two engines:
  sdk: Anthropic Python SDK, used when ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN is set
  cli: the local Claude Code CLI (`claude -p`), using its existing login
"""
import asyncio, json, os, shutil, time
from pathlib import Path

# `anthropic` is imported lazily in _via_sdk: importing it pulls in truststore,
# which touches the macOS keychain and can block server startup in sandboxed
# launchers. The CLI engine never needs it.

HERE = Path(__file__).parent


def _guide(name: str) -> str:
    """Official MiniMax-H3 prompt guides, fetched by install.sh from the model
    repo (not redistributed here). Loaded lazily so the UI still starts
    without them; only the optimizer needs them."""
    path = HERE / "guides" / name
    if not path.exists():
        raise RuntimeError(f"缺少官方提示词规范 webui/guides/{name}，请先运行 ./install.sh")
    return path.read_text()
MODEL = "claude-opus-5"
CLI = shutil.which("claude") or str(Path.home() / ".local/bin/claude")

_status_cache: dict = {"t": 0.0, "value": None}
# `claude auth status` can report loggedIn while the stored OAuth token is
# already rejected (401). Once a real call fails auth, remember it until a
# forced recheck succeeds, instead of trusting auth status again.
_cli_auth_failed = False
_cli_verified = False

INSTRUCTIONS = """You write final prompts for MiniMax-H3, an audio+video generation model.
The user gives a rough idea, often in Chinese, plus the task mode, the clip
duration, and the reference assets attached. Rewrite it into one final prompt
that follows the writing guide below exactly.

Rules:
- Output only the final prompt text. No commentary, no headings, no code fences.
- Write everything in English except spoken dialogue or lyrics inside <d>...</d>
  and visible on-screen text, which keep the user's original language.
- Every cut time must be strictly inside the clip duration. Fit the number of
  shots to the duration (roughly one shot per 3 to 6 seconds unless the user
  asks otherwise). Keep camera motion as natural sentences using the guide's
  motion type + amplitude + speed vocabulary.
- Enrich the idea with concrete, visible and audible detail (composition,
  lighting, subject appearance, motion, ambient sound, score instrumentation),
  but never contradict what the user asked for.
- non_diegetic_music describes instrumentation, tempo and dynamics only, no
  mood words. Use N/A only if the user wants no music.
"""

MODE_RULES = {
    "t2v": "Task: T2VA (text only). Output the three core fields only: "
           "integrated_multimodal_description, overall_soundscape, non_diegetic_music.",
    "i2v": "Task: I2VA (first frame). The first line must be exactly:\n"
           "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.\n"
           "Then one blank line, then the three core fields. Shot 1 starts from the "
           "subject, composition and scene of <Picture 1> and develops forward.",
    "fl2v": "Task: FL2VA (first and last frame). The first line must be exactly:\n"
            "How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot {last_shot}) aligns with the {dur}-second mark of the target video.\n"
            "Then one blank line, then the three core fields. Prefer a single shot "
            "describing the continuous path from Picture 1 to Picture 2.",
    "ref": "Task: full-reference generation (Ref2VA). Output the six sections in order: "
           "subject_definitions, summary, retention_analysis, detailed_description, "
           "overall_soundscape, non_diegetic_music. Use exactly these reference labels "
           "for the attached assets and no others: {labels}.",
}


def status(force: bool = False) -> dict:
    now = time.time()
    if not force and _status_cache["value"] and now - _status_cache["t"] < 60:
        return _status_cache["value"]
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        value = {"engine": "sdk", "ok": True, "detail": f"Anthropic API · {MODEL}"}
    elif Path(CLI).exists():
        try:
            import subprocess
            out = subprocess.run([CLI, "auth", "status"], capture_output=True, text=True, timeout=10).stdout
            logged = json.loads(out).get("loggedIn", False)
        except Exception:
            logged = False
        if _cli_auth_failed and not force:
            logged = False
        if not logged:
            detail = "Claude Code 登录失效，请在终端运行 claude auth login"
        elif _cli_verified:
            detail = "Claude Code 已连接"
        else:
            detail = "Claude Code 已登录，首次优化时验证"
        value = {"engine": "cli", "ok": logged, "verified": logged and _cli_verified, "detail": detail}
    else:
        value = {"engine": None, "ok": False, "detail": "未找到 claude 命令，也没有 ANTHROPIC_API_KEY"}
    _status_cache.update(t=now, value=value)
    return value


def build_messages(mode: str, idea: str, duration: float, labels: list[str], draft: str):
    dur = f"{duration:.2f}"
    rule = MODE_RULES[mode].format(dur=dur, last_shot=1, labels=", ".join(labels) or "none")
    guide = _guide("base.md") if mode != "ref" else _guide("base.md") + "\n\n---\n\n" + _guide("ref.md")
    system = f"{INSTRUCTIONS}\n{rule}\n\n# Writing guide\n\n{guide}"
    brief = [f"Clip duration: {dur} seconds.", f"User idea:\n{idea.strip()}"]
    if draft.strip():
        brief.append(f"Existing draft prompt to improve (keep what works):\n{draft.strip()}")
    return system, "\n\n".join(brief)


async def _via_sdk(system: str, user: str) -> str:
    import anthropic
    client = anthropic.AsyncAnthropic()
    try:
        resp = await client.beta.messages.create(
            model=MODEL, max_tokens=16000,
            thinking={"type": "adaptive"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            system=system, messages=[{"role": "user", "content": user}])
    except anthropic.AuthenticationError:
        raise RuntimeError("API key 无效")
    except anthropic.RateLimitError:
        raise RuntimeError("触发限流，稍后再试")
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"API 错误 {e.status_code}: {e.message}")
    except anthropic.APIConnectionError:
        raise RuntimeError("连接 Anthropic API 失败")
    if resp.stop_reason == "refusal":
        raise RuntimeError("Claude 拒绝了这个请求")
    return "".join(b.text for b in resp.content if b.type == "text").strip()


async def _via_cli(system: str, user: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        CLI, "-p", "--model", "opus", "--tools", "", "--no-session-persistence",
        "--output-format", "json", "--system-prompt", system,
        cwd=str(HERE), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(user.encode()), timeout=300)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("Claude 响应超时")
    try:
        data = json.loads(out.decode())
    except Exception:
        raise RuntimeError((err or out).decode()[:300] or "claude 命令没有返回结果")
    global _cli_auth_failed, _cli_verified
    if data.get("is_error"):
        msg = str(data.get("result", ""))
        if "401" in msg or "authenticat" in msg.lower():
            _cli_auth_failed = True
            _status_cache["value"] = None
            raise RuntimeError("Claude Code 登录已失效，请在终端运行 claude auth login 重新登录")
        raise RuntimeError(msg[:300])
    _cli_auth_failed = False
    _cli_verified = True
    _status_cache["value"] = None
    return str(data.get("result", "")).strip()


async def optimize(mode: str, idea: str, duration: float, labels: list[str], draft: str = "") -> str:
    if mode not in MODE_RULES:
        raise ValueError("未知模式")
    if not idea.strip() and not draft.strip():
        raise ValueError("先写一点想法")
    system, user = build_messages(mode, idea, duration, labels, draft)
    st = status()
    if st["engine"] == "sdk":
        return await _via_sdk(system, user)
    if st["engine"] == "cli":
        return await _via_cli(system, user)
    raise RuntimeError(st["detail"])
