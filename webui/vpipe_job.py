"""Render a vPipe pipeline graph for a H3 Studio job.

vPipe is driven by a stage graph, not a CLI, so a job becomes a .vpipeline
JSON that `vpipe --launch` runs. Unlike h3.c it applies the Turbo LoRA at
runtime, so nothing has to be folded into the weights beforehand.
"""
import functools
import json
import os
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
TEMPLATE = HERE / "vpipe_template.json"
# vPipe is not vendored: build it yourself (docs/VPIPE.md) and either put it at
# these defaults or point the environment at your own checkout.
VPIPE_BIN = Path(os.environ.get("H3_VPIPE_BIN", REPO / "third_party" / "vpipe" / "build" / "apps" / "vpipe" / "vpipe"))
VPIPE_WORK = Path(os.environ.get("H3_VPIPE_WORK", REPO / "models" / "vpipe-work"))
TURBO_LORA = "larryvrh/MiniMax-H3-Turbo-Lora-v4-600-ema"

# vPipe runs against the released MiniMaxAI weights this box already has, not a
# repack: the Comfy-Org repack's audio VAE uses tensor names vPipe's loader
# cannot read, and pointing at it costs the soundtrack. The released tree loads
# fully, so vPipe jobs carry native audio like h3.c's.
MODEL_DIR = Path(os.environ.get("H3_VPIPE_MODEL_DIR",
                                Path(os.environ.get("H3_MODEL_DIR", REPO / "models" / "MiniMax-H3")) / "FL2VA"))
MODEL_KEY = os.environ.get("H3_VPIPE_MODEL_KEY", "local/MiniMax-H3-FL2VA-official")
HAS_AUDIO = True


def ready():
    return (VPIPE_BIN.exists() and (VPIPE_WORK / "data.mdb").exists()
            and TEMPLATE.exists() and (MODEL_DIR / "transformer").is_dir())


@functools.cache
def ensure_registered():
    """Point vPipe's registry at the released weights. Registering the same
    directory again is a no-op for vPipe, so this is safe to repeat and costs
    milliseconds; it keeps a fresh work directory from failing its first job."""
    try:
        subprocess.run([str(VPIPE_BIN), "--launch-stage", "model-register",
                        "--stage-cfg", f'model_dir="{MODEL_DIR}"',
                        "--stage-cfg", f'key="{MODEL_KEY}"',
                        "--stage-cfg", "overwrite_existing=true"],
                       cwd=str(VPIPE_WORK), capture_output=True, timeout=60)
    except Exception:
        pass   # a stale registration still works; the job itself reports failures


def render(job, pipeline_path):
    """Write the graph for `job` and return it. Mirrors build_argv's checks."""
    p = job["params"]
    ensure_registered()
    g = json.loads(TEMPLATE.read_text())
    g["id"] = job["id"]
    by = {s["id"]: s for s in g["stages"]}
    by["model-select"]["config"]["hf_dir"] = MODEL_KEY
    by["text-prompt"]["config"]["text"] = job["prompt"]
    by["save-video"]["config"]["output_url"] = job["output"]

    gv = by["generate-video"]["config"]
    gv.update(width=int(p["width"]), height=int(p["height"]),
              frames=int(p["frames"]), steps=int(p.get("steps", 6)),
              seed=int(p.get("seed", 42)),
              sol_attn=bool(p.get("sol_attn")), i8_gemm=bool(p.get("i8_gemm")))

    mc = by["minimax-h3-model-config"]["config"]
    if p.get("variant") == "turbo":
        mc.update(lora=TURBO_LORA, lora_scale=1.0)
    else:
        mc.pop("lora", None)
        mc.pop("lora_scale", None)

    if not HAS_AUDIO:
        # save-video validates the edge COUNT, so the audio edge is removed
        # rather than left dangling
        g["stages"] = [s for s in g["stages"] if s["id"] != "audio-vae-decode"]
        sv = by["save-video"]
        sv["config"]["enable_audio"] = False
        sv["iports"] = [q for q in sv["iports"] if q.get("src") != "audio-vae-decode"]

    first = (job.get("refs") or {}).get("first")
    if first:
        by["load-image"]["config"]["url"] = [first]
        by["first-frame"]["config"].update(width=int(p["width"]), height=int(p["height"]))
    else:
        for sid in ("load-image", "first-frame", "vae-encode-first"):
            g["stages"] = [s for s in g["stages"] if s["id"] != sid]
        for q in by["generate-video"]["iports"]:
            if q.get("src") == "vae-encode-first":
                q["src"] = ""

    Path(pipeline_path).write_text(json.dumps(g, indent=1))
    return g
