#!/usr/bin/env python3
"""Convert a lightx2v MiniMax-H3 Turbo LoRA (diffusers/PEFT key space) into
h3.c's raw checkpoint key space, so tools/fold_turbo_lora.py can fold it.

Layout facts (diffusers scripts/convert_minimax_h3_to_diffusers.py, verified
byte-for-byte against the local Ref2VA shards):
  * raw `attn.qkv_proj` stores rows per head: [h0: q(128) k(128) v(128), h1: ...]
    for 56 heads; diffusers has separate to_q / to_k / to_v, each [q_all] etc.
  * raw `mlp.fc1` is [gate; value]; diffusers `ff.net.0.proj` is [value; gate].
  * `to_out.0` -> `attn.out_proj`, `ff.net.2` -> `mlp.fc2`,
    `transformer_blocks.N` -> `blocks.N`, `token_refiner.refiner_blocks.N` ->
    `token_refiner.blocks.N`.
PEFT applies delta = (alpha / rank) * B @ A. The output LoRA stores B already
multiplied by that factor, so the fold tool uses scale 1.0.

q/k/v become one rank-3r adapter on qkv_proj: A = [Aq; Ak; Av] and B places
each of Bq, Bk, Bv's rows at their per-head interleaved raw rows (zeros
elsewhere), so B @ A reproduces the three separate deltas exactly.

Usage:
  convert_lightx2v_lora.py IN.safetensors OUT.safetensors [--check]
"""
import json, struct, sys
import numpy as np

HEADS, HEAD_DIM = 56, 128
INNER = HEADS * HEAD_DIM          # 7168


def read(path):
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        hdr = json.loads(f.read(n))
        meta = hdr.pop("__metadata__", {})
        blob = f.read()
    def tensor(key):
        info = hdr[key]
        a, b = info["data_offsets"]
        assert info["dtype"] == "BF16", (key, info["dtype"])
        u16 = np.frombuffer(blob[a:b], dtype=np.uint16).astype(np.uint32) << 16
        return u16.view(np.float32).reshape(info["shape"])
    return hdr, meta, tensor


def to_bf16(x):
    u = np.ascontiguousarray(x, dtype=np.float32).view(np.uint32)
    u = (u + 0x7FFF + ((u >> 16) & 1)) >> 16          # round to nearest even
    return u.astype(np.uint16)


def write(path, tensors, meta):
    hdr, off, blobs = {"__metadata__": meta}, 0, []
    for k in sorted(tensors):
        b = to_bf16(tensors[k]).tobytes()
        hdr[k] = {"dtype": "BF16", "shape": list(tensors[k].shape), "data_offsets": [off, off + len(b)]}
        blobs.append(b); off += len(b)
    h = json.dumps(hdr).encode()
    h += b" " * ((8 - len(h) % 8) % 8)                   # keep data 8-byte aligned (h3.c requires it)
    with open(path, "wb") as f:
        f.write(struct.pack("<Q", len(h))); f.write(h)
        for b in blobs: f.write(b)


def raw_rows(part):
    """Raw qkv row indices holding diffusers rows 0..INNER-1 of q/k/v."""
    o = {"q": 0, "k": HEAD_DIM, "v": 2 * HEAD_DIM}[part]
    h = np.arange(INNER) // HEAD_DIM
    d = np.arange(INNER) % HEAD_DIM
    return h * 3 * HEAD_DIM + o + d


def raw_prefix(diff_prefix):
    if diff_prefix.startswith("token_refiner.refiner_blocks."):
        return diff_prefix.replace("token_refiner.refiner_blocks.", "token_refiner.blocks.", 1)
    if diff_prefix.startswith("transformer_blocks."):
        return diff_prefix.replace("transformer_blocks.", "blocks.", 1)
    raise ValueError(diff_prefix)


def main():
    src, dst = sys.argv[1], sys.argv[2]
    check = "--check" in sys.argv
    hdr, meta, T = read(src)
    alpha = float(meta.get("alpha", "nan"))
    A = lambda t: f"{t}.lora_A.default.weight"
    B = lambda t: f"{t}.lora_B.default.weight"
    targets = sorted({k.rsplit(".lora_", 1)[0] for k in hdr})
    blocks = sorted({t.rsplit(".attn.", 1)[0] if ".attn." in t else t.rsplit(".ff.", 1)[0] for t in targets})
    out = {}
    for blk in blocks:
        raw = raw_prefix(blk)
        # q/k/v -> one adapter on the interleaved fused projection
        As, Bs = [], []
        rank = None
        for part in ("q", "k", "v"):
            a, b = T(A(f"{blk}.attn.to_{part}")), T(B(f"{blk}.attn.to_{part}"))
            rank = a.shape[0]
            Braw = np.zeros((3 * INNER, rank), np.float32)
            Braw[raw_rows(part)] = b * (alpha / rank)
            As.append(a); Bs.append(Braw)
        out[f"{raw}.attn.qkv_proj.lora_A.weight"] = np.concatenate(As, 0)
        out[f"{raw}.attn.qkv_proj.lora_B.weight"] = np.concatenate(Bs, 1)
        # out_proj: direct
        a, b = T(A(f"{blk}.attn.to_out.0")), T(B(f"{blk}.attn.to_out.0"))
        out[f"{raw}.attn.out_proj.lora_A.weight"] = a
        out[f"{raw}.attn.out_proj.lora_B.weight"] = b * (alpha / a.shape[0])
        # fc1: diffusers [value; gate] -> raw [gate; value]
        a, b = T(A(f"{blk}.ff.net.0.proj")), T(B(f"{blk}.ff.net.0.proj"))
        half = b.shape[0] // 2
        out[f"{raw}.mlp.fc1.lora_A.weight"] = a
        out[f"{raw}.mlp.fc1.lora_B.weight"] = np.concatenate([b[half:], b[:half]], 0) * (alpha / a.shape[0])
        # fc2: direct
        a, b = T(A(f"{blk}.ff.net.2")), T(B(f"{blk}.ff.net.2"))
        out[f"{raw}.mlp.fc2.lora_A.weight"] = a
        out[f"{raw}.mlp.fc2.lora_B.weight"] = b * (alpha / a.shape[0])

    if check:
        # Rebuild each diffusers-space delta from the raw adapter via the forward
        # transform (de-interleave qkv, swap fc1 halves) and compare with PEFT's.
        rng = np.random.default_rng(0)
        worst = 0.0
        for blk in (blocks[0], blocks[len(blocks) // 2], blocks[-1]):
            raw = raw_prefix(blk)
            x = rng.standard_normal(T(A(f"{blk}.attn.to_q")).shape[1]).astype(np.float32)
            y = out[f"{raw}.attn.qkv_proj.lora_B.weight"] @ (out[f"{raw}.attn.qkv_proj.lora_A.weight"] @ x)
            for part in ("q", "k", "v"):
                a, b = T(A(f"{blk}.attn.to_{part}")), T(B(f"{blk}.attn.to_{part}"))
                ref = (alpha / a.shape[0]) * (b @ (a @ x))
                worst = max(worst, np.max(np.abs(y[raw_rows(part)] - ref)) / (np.max(np.abs(ref)) + 1e-12))
            a, b = T(A(f"{blk}.ff.net.0.proj")), T(B(f"{blk}.ff.net.0.proj"))
            ref = (alpha / a.shape[0]) * (b @ (a @ x)); half = ref.shape[0] // 2
            y = out[f"{raw}.mlp.fc1.lora_B.weight"] @ (out[f"{raw}.mlp.fc1.lora_A.weight"] @ x)
            worst = max(worst, np.max(np.abs(np.concatenate([y[half:], y[:half]]) - ref)) / (np.max(np.abs(ref)) + 1e-12))
        print(f"self-check: worst relative error {worst:.2e} over q/k/v and fc1 of 3 blocks")
        if worst > 1e-5:
            sys.exit("self-check failed")

    meta_out = {"source": src.rsplit("/", 1)[-1], "converted_to": "h3.c raw key space",
                "note": "B pre-multiplied by alpha/rank; fold with scale 1.0"}
    write(dst, out, meta_out)
    print(f"{len(blocks)} blocks, {len(out) // 2} adapter pairs -> {dst}")


if __name__ == "__main__":
    main()
