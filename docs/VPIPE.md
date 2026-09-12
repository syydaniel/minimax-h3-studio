# vPipe 引擎（可选，更快）

[vPipe](https://github.com/tgo-app-dev/vpipe)（Apache-2.0）是另一套 Apple Silicon 上的
原生 Metal 推理实现。跑同一个 MiniMax-H3，同样的步数下比 h3.c 快 1.4 到 1.7 倍，
音轨也不丢。装不装都行，不装时界面里 vPipe 一档显示「未安装」且点不动。

## 实测（Apple M3 Max 128 GB）

同一首帧、同一 seed、864×480、73 帧（3.04 秒）、6 步，唯一变量是引擎：

| 方案 | 耗时 | 相对 | 锐度 | 音轨 |
|---|---:|---:|---:|:--:|
| h3.c 原版 6 步 | 10.7 分 | 1.00x | 55.0 | 有 |
| h3.c Turbo 6 步 | 10.9 分 | 0.98x | 64.3 | 有 |
| vPipe Turbo 6 步 | 7.7 分 | 1.39x | 105.1 | 有 |
| **vPipe Turbo + sol_attn** | **6.3 分** | **1.70x** | 99.5 | 有 |
| vPipe Turbo + i8_gemm | 7.5 分 | 1.43x | 105.1 | 有 |

锐度是 12 帧 Laplacian 方差均值，只在同画布同内容之间可比；数值高也可能包含更多高频噪点，
建议自己看片再下结论。h3.c 的 Turbo 在同样 6 步下不更快是正常的：蒸馏的价值是让你用 6 步
而不是 20 步，而不是让每一步更快。

## 和 h3.c 的能力差异

| | h3.c | vPipe |
|---|:--:|:--:|
| 文生视频、首帧生视频 | 有 | 有 |
| 首尾帧、参考生视频 | 有 | **无**（只装了 FL2VA 分区） |
| 原生音轨 | 有 | 有（需用官方权重，见下） |
| Turbo 加速 | 折进权重（见 [TURBO.md](TURBO.md)） | 运行时挂载 LoRA，不用折 |
| reuse / core-reuse / token 缩减 | 有 | 无 |
| sol_attn / i8_gemm | 无 | 有 |

## 一、编译 vPipe

**必须带子模块克隆**，否则 cmake 会在 `extern/nanobind` 处失败：

```bash
git clone --recurse-submodules https://github.com/tgo-app-dev/vpipe.git ~/h3-studio/third_party/vpipe
cd ~/h3-studio/third_party/vpipe
```

### macOS 27 上必须先打一个补丁

上游三个 `.metal` 文件里有 `typedef bfloat16 bfloat16_t;`。在 macOS 27 的 Metal 编译器里
裸名 `bfloat16` 已经有多个可见声明，解析失败，于是 `dense_gemm`、`dense_gemm_bf16`、
`affine_qmm_steel_bf16` 三个核心内核编译不出来。后果不是报错退出，而是视频 VAE、文本编码器、
音频 VAE 全部 `failed to load ... ; inert`，管线两百毫秒就 drain 退出、不出片，很难一眼看出根因。

源码注释说明这行的用意只是把 MLX steel 内核里未使用的包装类型别名到原生类型，而这份代码
别处用的原生名字就是 `bfloat`，所以改成：

```bash
grep -rl "typedef bfloat16 bfloat16_t;" gpu-kernels/metal/ \
  | xargs sed -i '' 's/typedef bfloat16 bfloat16_t;/typedef bfloat bfloat16_t;/'
```

涉及 `gpu-kernels/metal/gemm/dense_gemm.metal`、`gemm/dense_gemm_steel.metal`、
`quant/affine_qmm_steel.metal` 三处。metal 源码是以字符串内嵌进 `libvpipe.dylib`、运行时
编译的，所以改完必须重新编译。

### 编译

```bash
cmake -S . -B build && cmake --build build -j8
```

上面这套步骤（带子模块克隆 → 打补丁 → 编译）在 M3 Max 上从零验证过一次，
`cmake --build` 用时约 1 分 20 秒。产物是 `build/apps/vpipe/vpipe`。验证补丁进去了：

```bash
strings build/libvpipe.0.1.dylib | grep -c "typedef bfloat bfloat16_t"   # 应为 6
```

## 二、把权重注册给 vPipe（不下载）

vPipe 从它**启动时所在目录**读模型注册表，所以先建一个工作目录：

```bash
mkdir -p ~/h3-studio/models/vpipe-work
cd ~/h3-studio/models/vpipe-work
```

然后把本仓库已经下载好的官方权重注册进去。`model-register` 只是把路径记进注册表，
不复制也不下载，14 毫秒完成：

```bash
~/h3-studio/third_party/vpipe/build/apps/vpipe/vpipe --launch-stage model-register \
  --stage-cfg model_dir='"'"$HOME"/h3-studio/models/MiniMax-H3/FL2VA'"' \
  --stage-cfg key='"local/MiniMax-H3-FL2VA-official"'
```

成功时它会打印检测结果，应该是 `[minimax-h3-fl2va, by diffusers] text, image -> video, audio`。
界面在第一次跑 vPipe 任务时也会自动补做这一步，所以这条命令主要用来确认能注册成功。

> **别用 Comfy-Org 的重打包**。vPipe 文档里的流程默认下载 `Comfy-Org/MiniMax-H3`（115 GB），
> 但那份重打包的音频 VAE 张量命名和 vPipe 的加载器不一致，读不了，**出来的片子没有声音**。
> 用本仓库下载的 MiniMaxAI 官方权重就没有这个问题，而且不用多占一份磁盘。

## 三、在界面里用

编译好、注册好之后重启界面，「引擎」一档就会出现 vPipe。默认路径是：

| 变量 | 默认值 |
|---|---|
| `H3_VPIPE_BIN` | `third_party/vpipe/build/apps/vpipe/vpipe` |
| `H3_VPIPE_WORK` | `models/vpipe-work` |
| `H3_VPIPE_MODEL_DIR` | `models/MiniMax-H3/FL2VA` |
| `H3_VPIPE_MODEL_KEY` | `local/MiniMax-H3-FL2VA-official` |

「高级参数」里多出两个只对 vPipe 生效的开关：

- **sol_attn**：实测再快约 1.2 倍，是目前最快的组合，画质没有明显损失。
- **i8_gemm**：int8 矩阵乘，实测比不开还略慢一点，留着备查。

选了 vPipe 之后，首尾帧和参考生视频会自动禁用，reuse / core-reuse / token 缩减也会置灰。

## 出处与许可

- vPipe：<https://github.com/tgo-app-dev/vpipe>，Apache-2.0
- 本仓库不分发 vPipe 源码或二进制，只提供上面的编译与接入说明
- Turbo LoRA 的出处与许可见 [TURBO.md](TURBO.md)
