# h3.c 上游版本与本地改动

- 上游仓库：https://github.com/antirez/h3.c
- 基线提交：`8974cc0`（2026-08-11，"Clarify SSD streaming memory and speed tradeoff"）
- 许可：MIT（见同目录 `LICENSE`）

## 本地改动

只有一处，来自上游尚未合并的 PR #1：https://github.com/antirez/h3.c/pull/1

- 改动文件：`h3_video_vae.c`、`README.md`
- 内容：视频 VAE 解码固定使用 256 像素分块（与官方 checkpoint 配置 `vae_tile_size: 256` 一致），
  不再在 256 到 320 像素之间按画布几何自动挑选
- 原因：未打补丁时，所有官方 768p 画布（1344×768、1024×768、768×768 等）都会被自动选成
  304 或 320 像素分块，画面平坦区域出现 16 像素网格状条纹。实测横向 16 像素边界跳变比从 1.33 降到 1.24
  （1.0 为无网格），条纹明显减轻但未完全消失。排查过程见 `docs/NOTES.md`
- 补丁原文：`third_party/h3.c-pr1-vae-tiles.patch`
- 仍可用环境变量 `H3_VAE_TILE_PIXELS` 覆盖（256 到 512，16 的倍数）

## 更新到新的上游版本

```bash
cd third_party/h3.c
# 用上游最新源码覆盖后，检查 PR #1 是否已合并；未合并则重新打补丁
git apply --check ../h3.c-pr1-vae-tiles.patch && git apply ../h3.c-pr1-vae-tiles.patch
make -j"$(sysctl -n hw.ncpu)" h3
```
