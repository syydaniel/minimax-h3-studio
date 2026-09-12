# 部署方案

本文按顺序写明从一台全新的 Mac 到打开界面生成第一条视频的全部步骤。
每个需要安装的东西都给出官方链接。命令都在"终端"里运行。

## 0. 先确认硬件

| 项目 | 要求 | 怎么查 |
|---|---|---|
| 芯片 | Apple Silicon（M1 及以后） | 左上角苹果菜单 → 关于本机 |
| 内存 | 建议 64 GB 以上，作者用的是 128 GB | 同上 |
| 磁盘 | 约 215 GB 可用空间 | 系统设置 → 通用 → 储存空间 |
| 系统 | macOS 26 或更新 | 同上 |

为什么要这么多：MiniMax-H3 的生成主干（DiT）有 33B 参数，常驻内存约 37 到 45 GiB；
文本编码器（Qwen3-VL）约 62 GiB，但它和 DiT 分阶段加载，不会同时占满。
内存小于 64 GB 时，h3.c 有一个 `--ssd-streaming` 模式可以边算边从硬盘读权重，但本界面目前没有接入，
需要的话请直接用命令行，说明见 https://github.com/antirez/h3.c 。

Intel Mac、Windows、Linux 都不行：h3.c 只有 Apple Metal 后端。
那些平台请参考官方推荐的 SGLang / vLLM-Omni 部署：https://platform.minimax.io/docs/guides/local-deploy

## 1. 安装基础工具

### 1.1 Xcode Command Line Tools（编译 h3.c 用）

```bash
xcode-select --install
```

弹窗里点"安装"。说明：https://developer.apple.com/xcode/resources/

### 1.2 Homebrew（安装 ffmpeg 和 uv 用）

按官网首页的一行命令安装：https://brew.sh

装完后按提示把 brew 加进 PATH，然后检查：

```bash
brew --version
```

### 1.3 ffmpeg 和 uv

`install.sh` 会自动用 Homebrew 安装这两个，也可以提前手动装：

```bash
brew install ffmpeg uv
```

- ffmpeg：h3.c 用它读图片、视频，并把生成结果封装成 MP4。https://formulae.brew.sh/formula/ffmpeg
- uv：Python 环境管理工具，用来建界面的运行环境。https://docs.astral.sh/uv/

### 1.4 Claude Code（可选，Claude 优化提示词功能要用）

不装也能用界面，只是"Claude 优化"按钮不可用。安装与登录见 https://code.claude.com/docs

装好后登录：

```bash
claude auth login
```

也可以不用 Claude Code，改为设置 Anthropic API Key（从 https://console.anthropic.com 获取），
界面会自动改用 API（模型 `claude-opus-5`）：

```bash
export ANTHROPIC_API_KEY=你的key
```

## 2. 获取本仓库

**不要**放在"桌面""文稿"或 iCloud 同步目录下。这些目录受 macOS 隐私保护或会自动同步，
既可能让服务在访问文件时卡住，也可能把 210 GB 的权重同步上云。建议放在用户目录下：

```bash
git clone https://github.com/syydaniel/minimax-h3-studio.git ~/h3-studio
cd ~/h3-studio
```

## 3. 运行安装脚本

```bash
./install.sh
```

它会依次：

1. 检查是不是 Apple Silicon、有没有 Xcode Command Line Tools 和 Homebrew
2. 缺 ffmpeg 或 uv 就用 Homebrew 装上
3. 编译 `third_party/h3.c`，得到 `third_party/h3.c/h3`，几秒钟
4. 在 `webui/.venv` 建 Python 3.13 环境，按 `webui/requirements.txt` 装依赖
5. 从模型仓库下载 MiniMax 官方提示词规范到 `webui/guides/`

脚本可以重复运行。

## 4. 下载模型权重（约 210 GB）

使用前请先阅读并接受 MiniMax H3 Community License：
https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE

```bash
./scripts/download-weights.sh
```

- 默认下载到 `models/MiniMax-H3/`。想放到别的盘，先设置 `export H3_MODEL_DIR=/Volumes/某盘/MiniMax-H3`，
  之后运行 `start.sh` 时也要设置同样的变量
- 可以随时 Ctrl-C，重跑会从断点继续
- 网速参考：作者这里约 5 到 8 MB/s，全部下完约 8 到 12 小时
- 下载期间想让电脑不休眠又能关屏幕：`caffeinate -ims ./scripts/download-weights.sh`，盖子别合上

脚本内置了下面这些处理，都是实际踩过的坑：

| 做法 | 原因 |
|---|---|
| 设置 `HF_HUB_DISABLE_XET=1` | Hugging Face 新的 Xet 传输后端在作者的网络下只有 0.15 MB/s，经典 CDN 有 5 到 8 MB/s。环境变量说明：https://huggingface.co/docs/huggingface_hub/package_reference/environment_variables |
| 只下 `FL2VA/*` 和 `Ref2VA/transformer` | 模型仓库有 498 GB，其中约 288 GB 是另一套 diffusers 布局，h3.c 不读 |
| 每个 `--include` 单独调用一次 | `hf download` 的 `--include` 只接受一个模式，多写的会被当成文件名，导致 404。CLI 文档：https://huggingface.co/docs/huggingface_hub/guides/cli |
| Ref2VA 的 transformer 先下到暂存目录 | 它的 `index.json` 会先于 66 GB 权重落地。h3.c 只要看到这个文件就认定 Ref2VA 已安装，结果连文生视频都会报错 |
| Ref2VA 其余文件用硬链接 | Ref2VA 的 text_encoder、video_vae、audio_vae 等与 FL2VA 逐字节相同（按 HF 清单的 sha256 核对），硬链接不重复下载、不多占空间 |
| 最后按 HF 清单逐文件核对大小 | 确保 FL2VA 和 Ref2VA 各 81 个文件都完整 |

## 5. 冒烟测试（可选，约 1 分钟）

```bash
./scripts/smoke-test.sh
```

生成一条 256×256、不到 1 秒的小视频到 `outputs/smoke.mp4`，只用来确认引擎和权重能端到端出片。
能看到画面、听到声音就说明部署成功。

## 6. 启动界面

```bash
./start.sh
```

浏览器会自动打开 http://127.0.0.1:7870 。服务默认只监听本机。
换端口：`PORT=7880 ./start.sh`。关闭：在终端按 Ctrl-C。

### 界面使用要点

- **清晰度**分 768p 和 480p 两档。480p 用同一个官方算法，只是把短边换成 480
  （16:9 为 864×480，4:3 为 640×480），耗时约为 768p 的一半，适合试镜头和试提示词
- **模型**分原版和 Turbo。Turbo 需要自己构建，见 [TURBO.md](TURBO.md)；没构建时显示"未安装"
- **引擎**分 h3.c 和 vPipe。vPipe 需要自己编译，见 [VPIPE.md](VPIPE.md)，实测快 1.4 到 1.7 倍，
  音轨一样有；选它之后首尾帧和参考生视频会禁用，因为只装了 FL2VA 分区
- **删除**在作品详情里，会把成片、缩略图和它用到的上传素材一起删掉，不可恢复；
  排队中或生成中的任务要先取消才能删
- **画幅**只有官方 768p 画布：21:9 为 1536×672，16:9 为 1344×768，4:3 为 1024×768，1:1 为 768×768，
  3:4 为 768×1024，9:16 为 768×1344。首帧类模式可以选"跟随首帧"，按图片比例用官方算法计算画布。
  算法移植自 diffusers 的 `resolve_canvas_size`：
  https://github.com/huggingface/diffusers/tree/main/src/diffusers/modular_pipelines/minimax_h3
- **时长**只允许官方的 4 到 15 秒。帧数必须是 5 + 17k，所以实际是 107 帧（4.46 秒）到 362 帧（15.08 秒）
- **质量**：预览 4 步适合试构图；标准 20 步配合复用和 token 缩减，是速度与质量的折中；精品 20 步不做近似。
  选了 Turbo 之后档位换成 5 / 6 / 8 步，复用、core-reuse、token 缩减会被禁用（蒸馏后的采样计划没有它们要利用的冗余）
- **提示词**要用官方的英文结构化格式。不熟的话先在"你的想法"里写中文，再点"Claude 优化"
- **参考生视频**里带音轨的视频至少要 2.34 秒，更短的请选"静音"
- 同一时间只跑一个任务，其余排队；命令行里另有 h3 在跑时，队列会等它结束

## 7. 高级配置（环境变量）

| 变量 | 默认值 | 作用 |
|---|---|---|
| `H3_MODEL_DIR` | `models/MiniMax-H3` | 权重目录 |
| `H3_BIN` | `third_party/h3.c/h3` | h3 可执行文件 |
| `H3_TURBO_MODEL_DIR` | `models/MiniMax-H3-turbo` | 可选的 Turbo 权重目录，构建方法见 [TURBO.md](TURBO.md) |
| `H3_VPIPE_BIN` | `third_party/vpipe/build/apps/vpipe/vpipe` | 可选的 vPipe 可执行文件，见 [VPIPE.md](VPIPE.md) |
| `H3_VPIPE_WORK` | `models/vpipe-work` | vPipe 的工作目录（模型注册表在这里） |
| `H3_VPIPE_MODEL_DIR` | `models/MiniMax-H3/FL2VA` | vPipe 用的权重分区 |
| `H3_DATA_DIR` | `webui/data` | 成片、任务记录、上传文件 |
| `PORT` | `7870` | 界面端口 |
| `H3_HOST` | `127.0.0.1` | 监听地址。界面能启动进程、读取本机文件，不要随意改成 `0.0.0.0` 暴露到网络 |
| `H3_VAE_TILE_PIXELS` | `256` | VAE 解码分块大小，一般不用改 |
| `ANTHROPIC_API_KEY` | 无 | 设置后 Claude 优化改用 Anthropic API |

## 8. 常见问题

**Claude 优化提示"登录失效"**
在终端运行 `claude auth login`，然后在界面点"重新检测"。注意 `claude auth status` 有时会显示已登录，
但令牌实际已失效，以界面上真实调用的结果为准。

**界面打开是空白，或一直连接不上**
确认仓库不在桌面、文稿等受保护目录下。作者遇到过：在 `~/Desktop` 作为工作目录启动时，
Python 会卡在一个 `open()` 系统调用上，一直不监听端口。`start.sh` 已经先切换到 `webui/` 目录再启动。

**更新代码后界面还是旧的**
静态文件已设置 `no-cache`；如果改了前端，把 `webui/static/index.html` 里的 `?v=` 数字加一。

**画面平坦区域（天空、雾）有一格一格的细网格**
来自 h3.c 的 VAE 解码实现，不是视频编码造成的。本仓库的 h3.c 已打 PR #1，改用官方 256 像素分块，
网格明显减轻但没有完全消失。可用 `tools/grid.py` 量化，排查过程见 `docs/NOTES.md`。

**Turbo 建好了，但切到参考生视频就变成"未安装"**
这是有意的。参考生视频用的 lightx2v 适配器按 video sigma shift = 6 训练，而上游 h3.c 把这个值
写死成 12，没有开关。界面会用 `strings` 探测 h3 二进制里有没有 `H3_VIDEO_SHIFT`，探测不到就
不放开参考模式，而不是设一个不起作用的环境变量后照跑。要用的话得自己给 h3.c 打补丁，
让 `h3_video_sigma_shift()` 读这个环境变量，重新 `make` 后界面会自动放开。详见 [TURBO.md](TURBO.md)。

**参考生视频报 "video soundtrack N requires at least 2 seconds"**
带音轨的参考视频太短。h3 会按 24 fps 取帧后向下对齐到 5 + 17k，音轨又要求至少 2 秒，
所以视频至少要 2.34 秒。界面提交时已会拦下并提示。

**报 "canvas exceeds the released 768*1344 pixel limit" 或尺寸不合法**
界面只会给出合法的官方画布。用 API 或命令行时，宽高须为 32 的倍数且面积不超过 768×1344。

**生成比预计慢很多**
预计耗时按作者 M3 Max 的实测数据外推，其他芯片会不同；参考生视频还会因为参考素材拉长序列而更慢。
另外同时跑两个 h3 进程会互相拖慢。

**任务失败**
在界面里点"查看日志"。任务记录保存在 `webui/data/jobs/`，成片在 `webui/data/outputs/`。
