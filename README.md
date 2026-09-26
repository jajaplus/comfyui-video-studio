# MiniMax H3 视频替换工作台

这是一个部署在 AutoDL 上的视频精准替换客户端。浏览器负责上传视频、参考图片和 Excel，FastAPI 保存任务并按顺序执行 MiniMax-H3、Florence-2、SAM2 与 FaceFusion。网页不设置登录或访问令牌，打开地址即可使用。

优先使用已经包含 `/root/ComfyUI` 的 AutoDL 应用镜像，项目会直接复用它且不主动更新。服务器没有 ComfyUI，或者镜像自带版本缺少 H3 节点但又不希望修改原目录时，可以按第 2.2 节另装一份到 `/root/autodl-tmp/ComfyUI`；两种方式只选择一种。

## 功能

- 视频和参考图片可多次选择、累计上传、预览和逐项删除。
- 一次最多选择 3 个视频，每个视频建立一个独立队列任务。
- 每个任务使用 1 个上传视频和最多 9 张参考图，每张图明确选择“脸部”或“商品”。
- 使用商品参考图时，Florence-2 根据提示词和参考图自动识别视频中的原商品；首帧手动框选仅作为识别不准时的可选修正。
- 商品替换由 MiniMax-H3 生成候选画面，SAM2 跟踪自动识别或手动修正的商品蒙版，并只把候选画面的商品区域合成回原视频。
- 脸部替换在商品合成后由 FaceFusion 完成，并组合方框、遮挡和面部区域蒙版；人物的身体、服装、动作与背景继续使用原视频像素。
- 最终成片恢复上传视频的原声音轨。
- 视频时长自动读取并显示，视频不能短于 4 秒。
- 超过 15 秒的视频会切成多个长度接近的 4–15 秒片段，依次生成后自动合并成一个视频。
- 生成总时长跟随上传视频，画面比例默认跟随视频方向。
- 视频清晰度支持低清、标清和高清三档，默认低清以缩短生成时间。
- Scheduler、Sampler、Steps 和 Denoise 可在客户端逐任务设置，任务列表显示实际参数。
- Excel 一次最多导入 3 个任务，素材 URL 由用户自行填写。
- SQLite 持久化队列，支持查看进度、下载、失败重试和删除任务。
- 客户端每 3 秒刷新 GPU 利用率、显存、温度和功耗。
- 当前任务显示准备素材、模型加载、参考编码、采样、视频解码和保存等环节。
- 生成中的任务按秒显示已耗时，结束后保留包含切割、生成和合并过程的总耗时。
- ComfyUI 原生 `MiniMaxH3ReferenceToVideo` 只负责商品候选画面；仅换脸的任务不会启动 H3。

精准模式的处理顺序如下：

```text
上传的原视频
  → MiniMax-H3 生成商品候选画面（仅有商品参考图时）
  → Florence-2 根据提示词和商品参考图识别首帧原商品
  → SAM2 跟踪商品区域并局部合成
  → FaceFusion 替换脸部（仅有脸部参考图时）
  → 恢复原视频声音
  → 输出成片
```

这条链路避免让 H3 直接重画整个人物。商品区域之外始终来自原视频；换脸也在最后单独执行。自动识别或 SAM2 跟踪仍可能在目标描述不清、强遮挡、商品完全离开画面、严重运动模糊或镜头切换时出错。遇到识别不准时，可点击视频预览下方的“修正商品区域”手动框选，正式生成前建议先用短视频测试。

为了提高局部替换成功率：

1. 脸部参考图尽量使用清晰、无遮挡、接近正面的单人照片，不要把参考图的服装、姿势或背景作为替换目标。
2. 商品参考图尽量完整、清晰、背景简单；原视频中必须已经存在需要替换的商品。
3. 在每张参考图下方选择正确类型；提示词写清原视频中的目标，例如“人物身上的上衣换成参考图2”。
4. 默认不需要框选。自动识别不准时，再用“修正商品区域”完整框住原商品并略留边缘。
5. 低清适合测试速度；正式商品生成建议选择 `standard` 或 `high`。FaceFusion 的换脸质量不受 H3 清晰度选项影响。

长视频的每个片段由模型独立生成，因此片段连接处可能出现轻微的画面、人物细节或声音跳变。任务列表会显示总片段数、当前片段和整体进度；任意片段失败时可直接重试整个任务。

清晰度控制的是 H3 商品候选画布大小。最终成片保持上传视频的原始分辨率和时长；模型仍然使用 24fps，采样参数默认是 `simple`、`res_multistep`、20 Steps 和 Denoise 1.0：

| 清晰度 | 16:9 | 9:16 | 1:1 | 4:3 | 3:4 | 21:9 |
| --- | --- | --- | --- | --- | --- | --- |
| `low`（默认、最快） | 672×384 | 384×672 | 512×512 | 576×448 | 448×576 | 768×320 |
| `standard`（均衡） | 832×480 | 480×832 | 640×640 | 736×544 | 544×736 | 960×416 |
| `high`（最慢） | 1344×768 | 768×1344 | 1024×1024 | 1024×768 | 768×1024 | 1568×672 |

生成耗时主要随总帧数、画面像素量、采样步数和参考图片数量增长。高清横屏的像素量约为低清的 4 倍，因此同一视频可能明显更慢；具体倍数还取决于显卡、显存卸载和模型加载状态。

客户端“生成参数”区域支持以下设置：

| 参数 | 默认值 | 可选范围 | 作用 |
| --- | --- | --- | --- |
| Scheduler | `simple` | `simple`、`normal`、`karras`、`exponential`、`sgm_uniform` | 安排每一步的噪声强度 |
| Sampler | `res_multistep` | `res_multistep`、`euler`、`euler_ancestral`、`heun`、`dpmpp_2m`、`dpmpp_2m_sde` | 计算每一步如何更新视频潜空间 |
| Steps | `20` | 1–100 | 采样步数；通常越高越慢 |
| Denoise | `1.0` | 0.01–1.00 | 生成变化强度；越低越接近输入 |

`simple + res_multistep + 20 + 1.0` 是本项目原始 MiniMax-H3 工作流参数。需要更保守的商品变化时，可先测试 Denoise `0.85`；修改 Sampler 或 Scheduler 后应先用短视频检查运动稳定性。

## 模型清单

当前精准链路实际使用以下权重。大小按官方发布文件计算，页面显示值可能因 GB/GiB 换算略有差异。

| 环节 | 模型文件 | 约占用 |
| --- | --- | ---: |
| H3 视频扩散 | `minimax_h3_ref2va_pruned_int8_convrot.safetensors` | 21.0 GB |
| H3 文本编码 | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | 15.7 GB |
| H3 视频 VAE | `minimax_h3_video_vae_int8_convrot.safetensors` | 2.81 GB |
| H3 音频 VAE | `minimax_h3_audio_vae_fp32.safetensors` | 605 MB |
| 商品蒙版跟踪 | `sam2.1_hiera_small.pt` | 184 MB |
| 商品首帧识别 | `Florence-2-base-ft/model.safetensors` | 463 MB |
| FaceFusion 换脸 | `inswapper_128_fp16.onnx` | 264.8 MiB |
| FaceFusion 人脸检测 | `yoloface_8n.onnx` | 12.1 MiB |
| FaceFusion 关键点 | `2dfan4.onnx`、`fan_68_5.onnx` | 94.3 MiB |
| FaceFusion 人脸识别 | `arcface_w600k_r50.onnx` | 166.3 MiB |
| FaceFusion 遮挡与区域蒙版 | `xseg_1.onnx`、`bisenet_resnet_34.onnx` | 156.4 MiB |
| FaceFusion 公共检查模型 | `nsfw_1/2/3.onnx`、`fairface.onnx`、`kim_vocal_2.onnx` | 584.6 MiB |

H3 四个文件合计约 40.1 GB；SAM2 约 184 MB；Florence-2 权重约 463 MB；本项目指定的 FaceFusion 必需模型合计约 1.25 GiB。PyTorch、CUDA 和 ONNX Runtime 属于运行环境，不在上表的模型大小内。已确认的 AutoDL 模型广场路径和全部加载命令统一放在第 3 节。

商品自动定位使用微软发布的 [`microsoft/Florence-2-base-ft`](https://huggingface.co/microsoft/Florence-2-base-ft)。安装脚本只下载 safetensors 权重及运行所需的配置、处理器和分词器文件，不会同时下载重复的 `pytorch_model.bin`。

## 部署结构（先看）

服务器上包含三个相互配合的部分：

| 部分 | 是否已有 | 目录 | 端口 | 作用 |
| --- | --- | --- | --- | --- |
| ComfyUI | 镜像已有则复用；没有则安装 | `/root/ComfyUI` 或 `/root/autodl-tmp/ComfyUI` | `6008` | 加载 MiniMax-H3，并执行商品候选视频生成 |
| 视频替换工作台 | 本项目需要安装 | `/root/autodl-tmp/comfyui-video-studio` | `6006` | 提供网页、任务队列、Excel 导入和调用流程 |
| 精准替换工具 | 本项目需要安装 | `/root/autodl-tmp/h3-precision-tools` | 无独立端口 | 使用 Florence-2 定位商品、SAM2 跟踪蒙版、FaceFusion 换脸 |

镜像已有 ComfyUI 时，`scripts/install_client_only.sh` 只安装视频工作台。没有 ComfyUI 时，`scripts/install_autodl.sh` 才会把它安装到数据盘。`scripts/install_precision_tools.sh` 安装 Florence-2、SAM2 与 FaceFusion。启动时，`scripts/start_all.sh` 根据 `.env` 找到所选的 ComfyUI，同时启动工作台网页。

```text
浏览器
  → 6006：视频替换工作台和任务队列
      → 6008：ComfyUI + MiniMax-H3
      → Florence-2 + SAM2：商品定位、跟踪与局部合成
      → FaceFusion：脸部替换
```

## 部署流程总览

首次部署按下面顺序操作：

1. 在 Mac 压缩项目并上传到 AutoDL。
2. 检查服务器是否已有 `/root/ComfyUI`，然后在第 2.1 和第 2.2 节中只选择一种安装方式。
3. 完整执行第 3 节的一组命令，检查并加载公共模型，同时安装精准替换工具。
4. 切换到 GPU 模式，启动 6008 和 6006 两个服务。
5. 在 AutoDL 控制台开放 6006 自定义服务。

后续更新代码直接查看“项目更新部署”章节，不需要重新安装 ComfyUI、重新挂载模型或删除任务数据。

## 1. 在 Mac 压缩并上传项目

这一组命令同时用于首次部署和后续更新，README 其他章节不再重复压缩和上传步骤。

### 1.1 压缩项目

在 Mac 终端执行：

```bash
cd /Users/linyongjia/Desktop

tar \
  --exclude='comfyui-video-studio/.venv' \
  --exclude='comfyui-video-studio/.env' \
  --exclude='comfyui-video-studio/data' \
  --exclude='comfyui-video-studio/logs' \
  --exclude='comfyui-video-studio/.git' \
  --exclude='*/__pycache__' \
  --exclude='*.pyc' \
  --exclude='.DS_Store' \
  -czf comfyui-video-studio.tar.gz \
  comfyui-video-studio

ls -lh /Users/linyongjia/Desktop/comfyui-video-studio.tar.gz
```

生成的文件是：

```text
/Users/linyongjia/Desktop/comfyui-video-studio.tar.gz
```

这条命令不会修改或删除 `comfyui-video-studio` 项目目录中的任何文件。如果桌面上已经有同名的 `comfyui-video-studio.tar.gz`，`-czf` 会直接用新压缩包覆盖旧压缩包。

压缩包不会包含本地虚拟环境、服务器配置、任务数据、生成结果、日志和 Git 历史。`__pycache__` 的正确写法是 `--exclude='*/__pycache__'`；请直接复制上面的代码块，避免聊天软件把两个下划线转换成粗体标记。

### 1.2 上传到 AutoDL

把 SSH 主机和端口替换成 AutoDL 控制台显示的值：

```bash
scp -P 你的SSH端口 \
  /Users/linyongjia/Desktop/comfyui-video-studio.tar.gz \
  root@你的AutoDL主机:/root/autodl-tmp/
```

例如 AutoDL 登录命令是：

```text
ssh -p 12345 root@region-1.autodl.com
```

对应的上传命令是：

```bash
scp -P 12345 \
  /Users/linyongjia/Desktop/comfyui-video-studio.tar.gz \
  root@region-1.autodl.com:/root/autodl-tmp/
```

服务器上如果已经有 `/root/autodl-tmp/comfyui-video-studio.tar.gz`，上传会用新压缩包覆盖它，不会在服务器上生成多个副本。

### 1.3 首次部署时解压

登录服务器：

```bash
ssh -p 你的SSH端口 root@你的AutoDL主机
```

然后在 AutoDL 终端执行：

```bash
cd /root/autodl-tmp
tar -xzf comfyui-video-studio.tar.gz
cd /root/autodl-tmp/comfyui-video-studio
chmod +x scripts/*.sh
ls
```

看到 `app`、`scripts`、`static`、`workflows` 和 `README.md`，说明解压成功。

## 2. 准备 ComfyUI 和工作台（二选一）

先执行：

```bash
if [ -f /root/ComfyUI/main.py ]; then
  echo '使用第 2.1 节：镜像已有 ComfyUI'
else
  echo '使用第 2.2 节：需要安装 ComfyUI'
fi
```

只执行检测结果对应的小节，不要把第 2.1 和第 2.2 节都执行。

### 2.1 镜像已有 `/root/ComfyUI`（推荐）

先确认镜像自带的 ComfyUI：

```bash
ls -lh /root/ComfyUI/main.py
```

能看到 `main.py` 后，再执行下面的命令。它会把工作台固定连接到 `/root/ComfyUI` 和 `6008` 端口，然后安装工作台自身的 Python 依赖：

```bash
cd /root/autodl-tmp/comfyui-video-studio
cp -n .env.autodl-comfyui.example .env

sed -i \
  -e 's#^H3_COMFYUI_DIR=.*#H3_COMFYUI_DIR=/root/ComfyUI#' \
  -e 's#^H3_COMFYUI_INPUT_DIR=.*#H3_COMFYUI_INPUT_DIR=/root/ComfyUI/input#' \
  -e 's#^H3_COMFYUI_OUTPUT_DIR=.*#H3_COMFYUI_OUTPUT_DIR=/root/ComfyUI/output#' \
  -e 's#^H3_COMFYUI_URL=.*#H3_COMFYUI_URL=http://127.0.0.1:6008#' \
  .env

grep '^H3_COMFYUI' .env
scripts/install_client_only.sh
```

这一步不会下载、复制或重新安装 ComfyUI。`install_client_only.sh` 只创建本项目的 `.venv`、安装 FastAPI 等客户端依赖，并检查现有 ComfyUI 是否包含 MiniMax-H3 节点。不要运行 `scripts/install_autodl.sh`，也不要运行镜像根目录的 `/root/start.sh`。

检查镜像是否包含 MiniMax-H3 原生节点：

```bash
grep -Rqs --exclude-dir=.git --exclude-dir=.venv \
  'MiniMaxH3ReferenceToVideo' \
  /root/ComfyUI/comfy /root/ComfyUI/comfy_extras \
  && echo 'MiniMax-H3 节点正常：保持原版本，继续第 3 节' \
  || echo '缺少 H3 节点：不要修改 /root/ComfyUI，改用第 2.2 节'
```

检测正常就直接跳到第 3 节，不要执行 `git pull`。如果缺少 H3 节点，执行第 2.2 节，在数据盘安装独立的 ComfyUI，原来的 `/root/ComfyUI` 保持不变。

### 2.2 没有 ComfyUI，或不修改镜像自带版本

本节把独立的 ComfyUI 安装到 `/root/autodl-tmp/ComfyUI`。服务器完全没有 ComfyUI 时，需要使用带 CUDA 版 PyTorch 2.7 或更高版本的基础镜像；服务器已有 `/root/ComfyUI` 时，脚本会优先复用它的 Python 环境，但不会修改它的代码和文件。可以先用无 GPU 模式安装依赖，完成后再切换到 GPU 模式。

先选择可用的 PyTorch 环境并确认它是 CUDA 构建：

```bash
if [ -x /root/miniconda3/envs/comfyui/bin/python ]; then
  BASE_PYTHON=/root/miniconda3/envs/comfyui/bin/python
else
  BASE_PYTHON=python3
fi

"$BASE_PYTHON" -c "import torch; print('PyTorch:', torch.__version__, 'CUDA构建:', torch.version.cuda, '当前GPU可用:', torch.cuda.is_available())"
```

无 GPU 模式下 `当前GPU可用` 显示 `False` 是正常的；`CUDA构建` 必须显示具体版本，不能是 `None`。然后执行：

```bash
cd /root/autodl-tmp/comfyui-video-studio
cp -n .env.example .env

if [ -x /root/miniconda3/envs/comfyui/bin/python ]; then
  BASE_PYTHON=/root/miniconda3/envs/comfyui/bin/python
else
  BASE_PYTHON=python3
fi

sed -i \
  -e 's#^H3_COMFYUI_DIR=.*#H3_COMFYUI_DIR=/root/autodl-tmp/ComfyUI#' \
  -e 's#^H3_COMFYUI_INPUT_DIR=.*#H3_COMFYUI_INPUT_DIR=/root/autodl-tmp/ComfyUI/input#' \
  -e 's#^H3_COMFYUI_OUTPUT_DIR=.*#H3_COMFYUI_OUTPUT_DIR=/root/autodl-tmp/ComfyUI/output#' \
  -e 's#^H3_COMFYUI_URL=.*#H3_COMFYUI_URL=http://127.0.0.1:6008#' \
  .env

sed -i '/^H3_BASE_PYTHON=/d' .env
echo "H3_BASE_PYTHON=$BASE_PYTHON" >> .env

scripts/install_autodl.sh
```

该脚本安装工作台客户端，并把独立的 ComfyUI 安装到 `/root/autodl-tmp/ComfyUI`。它会复用选定 Python 环境中的 PyTorch 和 CUDA，不会再下载一套 PyTorch，也不会修改 `/root/ComfyUI`。安装结束后继续执行第 3 节。

## 3. 一次性加载 AutoDL 模型广场文件

无论第 2 节选择哪一种方式，本节命令都会读取 `.env` 中的 `H3_COMFYUI_DIR`，把四个 MiniMax-H3 公共权重挂载到对应的 ComfyUI `models` 目录，并让工作台直接读取 SAM2 与 Florence-2 公共权重。当前已确认的路径如下：

| 类型 | 模型广场名称 | AutoDL 公共路径 | 加载位置 |
| --- | --- | --- | --- |
| Ref2VA 扩散模型 | `minimax_h3_ref2va_pruned_int8_convrot` | `/.autodl/Comfy-Org/MiniMax-H3/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors` | `models/diffusion_models` |
| 文本编码器 | `qwen3vl_32b_minimax_h3_nvfp4_awq` | `/.autodl/Comfy-Org/MiniMax-H3/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | `models/text_encoders` |
| 视频 VAE | `minimax_h3_video_vae_int8_convrot` | `/.autodl/15/22/fc/1522fc49e094bb75c704ee519582252d` | `models/vae` |
| 音频 VAE | `minimax_h3_audio_vae_fp32` | `/.autodl/Comfy-Org/MiniMax-H3/vae/minimax_h3_audio_vae_fp32.safetensors` | `models/vae` |
| 商品蒙版跟踪 | `sam2.1_hiera_small.pt` | `/.autodl/51/71/3b/51713b3d1994696d27f35f9c6de6f5ef` | `.env` 直接读取 |
| 商品首帧识别 | `Florence-2-base-ft` | `/.autodl/53/68/2a/53682a80f2a8321a6133a95084a4f86d` | 安装脚本挂载到精准工具目录 |

公共目录只能读取。下面这一整块命令按顺序完成路径检查、把四个 H3 权重软链接到第 2 节准备好的 ComfyUI、写入 SAM2 公共路径，并安装 Florence-2、SAM2 与 FaceFusion。它不会再次安装 ComfyUI。直接完整复制到 AutoDL 终端执行：

```bash
(
set -e
cd /root/autodl-tmp/comfyui-video-studio

H3_DIFFUSION='/.autodl/Comfy-Org/MiniMax-H3/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors'
H3_TEXT_ENCODER='/.autodl/Comfy-Org/MiniMax-H3/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors'
H3_VIDEO_VAE='/.autodl/15/22/fc/1522fc49e094bb75c704ee519582252d'
H3_AUDIO_VAE='/.autodl/Comfy-Org/MiniMax-H3/vae/minimax_h3_audio_vae_fp32.safetensors'
SAM2_MODEL='/.autodl/51/71/3b/51713b3d1994696d27f35f9c6de6f5ef'
FLORENCE2_MODEL='/.autodl/53/68/2a/53682a80f2a8321a6133a95084a4f86d'

# 1. 检查六个模型广场路径
ls -ldh \
  "$H3_DIFFUSION" \
  "$H3_TEXT_ENCODER" \
  "$H3_VIDEO_VAE" \
  "$H3_AUDIO_VAE" \
  "$SAM2_MODEL" \
  "$FLORENCE2_MODEL"

# 2. 把四个 MiniMax-H3 权重挂载到 ComfyUI
scripts/link_autodl_models.sh \
  "$H3_DIFFUSION" \
  "$H3_TEXT_ENCODER" \
  "$H3_VIDEO_VAE" \
  "$H3_AUDIO_VAE"

# 3. 让工作台复用 SAM2 和 Florence-2 公共模型
sed -i '/^H3_SAM2_CHECKPOINT=/d' .env
echo "H3_SAM2_CHECKPOINT=$SAM2_MODEL" >> .env
sed -i '/^H3_FLORENCE2_PUBLIC_PATH=/d' .env
echo "H3_FLORENCE2_PUBLIC_PATH=$FLORENCE2_MODEL" >> .env

# 4. 安装 Florence-2、SAM2、FaceFusion 运行环境及缺少的模型
scripts/install_precision_tools.sh
)
```

这组命令在独立子进程中执行；任意路径不存在或某一步失败时会立即停止，但不会退出当前 SSH 终端。四个 H3 权重会以软链接加载到 ComfyUI，SAM2 直接读取公共文件。Florence-2 公共路径如果是完整模型目录，安装脚本会挂载其中的文件；如果是单个 safetensors 权重文件，脚本会挂载权重，并只下载体积很小的配置、处理器和分词器文件。463 MB 的 Florence-2 权重不会再重复下载。

FaceFusion 的模型广场路径尚未提供，因此最后一步会下载项目需要的约 1.25 GiB ONNX 文件。如果以后找到了包含这些 ONNX 文件的公共目录，可以先把 `H3_FACEFUSION_MODELS_DIR=/.autodl/公共模型实际目录` 写入 `.env`，安装脚本就会优先复用。不要手工运行 FaceFusion 的 `force-download`，否则会下载当前版本提供的全部模型。

## 4. 切换到 GPU 模式并启动

如果当前是无 GPU 模式，先在 AutoDL 控制台关机，再选择 GPU 并重新开机。进入终端后检查 GPU：

```bash
nvidia-smi
python3 -c "import torch; print('PyTorch:', torch.__version__, 'CUDA:', torch.version.cuda, 'GPU可用:', torch.cuda.is_available(), 'GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else '无')"
```

`GPU可用` 必须显示 `True`。下面的命令会根据 `.env` 启动 `/root/ComfyUI/main.py` 或 `/root/autodl-tmp/ComfyUI/main.py`，并在 6006 启动工作台客户端：

```bash
cd /root/autodl-tmp/comfyui-video-studio
scripts/start_all.sh
```

检查两个服务：

```bash
curl --max-time 5 http://127.0.0.1:6008/system_stats
curl --max-time 5 http://127.0.0.1:6006/health
```

第二个命令返回 `"engine":"ok"` 后，在 AutoDL 控制台创建 **6006 端口**的自定义服务。访问者打开该地址即可上传素材和创建任务，所有访问者共用一个任务队列。

如果需要查看 ComfyUI 节点页面，可以再创建 **6008 端口**的自定义服务。平时使用视频工作台只需开放 6006。

查看日志：

```bash
tail -f /root/autodl-tmp/comfyui-video-studio/logs/comfyui.log
tail -f /root/autodl-tmp/comfyui-video-studio/logs/app.log
```

停止服务：

```bash
cd /root/autodl-tmp/comfyui-video-studio
scripts/stop_all.sh
```

服务器重启后不需要重新安装或重新挂载模型，只需再次运行 `scripts/start_all.sh`。

## 5. 项目更新部署（上传压缩包）

本节只更新工作台代码，不会删除服务器现有的 `.env`、虚拟环境、任务数据库、上传素材、生成结果和日志。

### 5.1 在 Mac 重新打包并上传

再次执行“1.1 压缩项目”和“1.2 上传到 AutoDL”中的命令。每次打包都会覆盖 Mac 桌面上的旧压缩包，每次上传都会覆盖服务器上的旧压缩包。

### 5.2 在服务器停止服务并覆盖代码

在 AutoDL 终端执行：

```bash
cd /root/autodl-tmp/comfyui-video-studio
scripts/stop_all.sh

cd /root/autodl-tmp
tar -xzf comfyui-video-studio.tar.gz

cd /root/autodl-tmp/comfyui-video-studio
chmod +x scripts/*.sh
```

解压时，压缩包中存在的程序代码和说明文件会覆盖服务器上的同名文件。压缩包已经排除了 `.env`、`.venv`、`data`、`logs` 和 `.git`，所以这些服务器文件夹不会被覆盖或删除，任务记录、上传素材、生成结果和服务器配置会保留。

`tar -xzf` 只覆盖压缩包中包含的同名文件，不会自动删除服务器上多余的旧文件。如果新版明确删除了某个旧程序文件，需要手工删除该旧文件。

### 5.3 直接重启

普通代码和页面更新不需要重新安装 ComfyUI、SAM2、Florence-2 或 FaceFusion，直接启动即可：

```bash
cd /root/autodl-tmp/comfyui-video-studio
scripts/start_all.sh
```

只有 README 明确说明“本次更新增加依赖”时，才需要更新客户端依赖：

```bash
cd /root/autodl-tmp/comfyui-video-studio
.venv/bin/python -m pip install -r requirements.txt
```

`scripts/install_precision_tools.sh` 只用于首次部署、加入新的精准工具依赖或修复损坏环境。脚本现在会先检查现有环境：完整时直接显示“复用”，不会执行 `pip install`、`git pull` 或 FaceFusion 安装。需要强制重装时使用 `scripts/install_precision_tools.sh --force`；需要主动更新 SAM2 和 FaceFusion 源码时使用 `scripts/install_precision_tools.sh --update-sources`。

确认新版本已经正常运行：

```bash
curl --max-time 5 http://127.0.0.1:6008/system_stats
curl --max-time 5 http://127.0.0.1:6006/health
tail -n 50 logs/app.log
```

确认无误后可以删除服务器上的压缩包：

```bash
rm /root/autodl-tmp/comfyui-video-studio.tar.gz
```

更新代码后，浏览器如果仍显示旧页面，按 `Command + Shift + R` 强制刷新。

## 6. 常见问题

### 页面显示“ComfyUI 未连接（6008）”

客户端已经启动，但无法访问 ComfyUI。依次执行：

```bash
cd /root/autodl-tmp/comfyui-video-studio
grep '^H3_COMFYUI' .env
curl --max-time 5 http://127.0.0.1:6008/system_stats
tail -n 100 logs/comfyui.log
```

`.env` 中的地址应为：

```text
H3_COMFYUI_URL=http://127.0.0.1:6008
```

如果仍是 8188，修改后重启：

```bash
cd /root/autodl-tmp/comfyui-video-studio
sed -i 's#http://127.0.0.1:8188#http://127.0.0.1:6008#g' .env
scripts/stop_all.sh
scripts/start_all.sh
```

Mac 本地预览只启动客户端，没有运行 MiniMax-H3 ComfyUI，因此显示未连接是正常现象。实际生成视频时应打开 AutoDL 的 6006 地址。

### 提示找不到 `comfyui-workflow-templates` 指定版本

例如 `requirements.txt` 要求 `comfyui-workflow-templates==0.11.70`，但错误列表最高只有 `0.11.68`，说明 AutoDL 当前 pip 镜像尚未同步新版本。不要修改 ComfyUI 的 `requirements.txt`，直接从官方 PyPI 继续安装：

```bash
cd /root/ComfyUI

if [ -x /root/miniconda3/envs/comfyui/bin/python ]; then
  COMFY_PYTHON=/root/miniconda3/envs/comfyui/bin/python
elif [ -x .venv/bin/python ]; then
  COMFY_PYTHON=.venv/bin/python
else
  COMFY_PYTHON=python3
fi

"$COMFY_PYTHON" -m pip install \
  --index-url https://pypi.org/simple \
  -r requirements.txt
```

安装成功后回到项目目录，继续执行第 3 节；不需要重新运行 `git pull`。

### 提示 Florence-2、SAM2 或 FaceFusion 未安装

这是精准模式需要的三个独立工具。首次安装或页面明确提示缺失时，在 AutoDL 终端执行：

```bash
cd /root/autodl-tmp/comfyui-video-studio
scripts/install_precision_tools.sh
scripts/stop_all.sh
scripts/start_all.sh
```

检查安装结果：

```bash
/root/autodl-tmp/h3-precision-tools/sam2-env/bin/python -c "import sam2; print('SAM2 正常')"
/root/autodl-tmp/h3-precision-tools/sam2-env/bin/python -c "from transformers import AutoProcessor; AutoProcessor.from_pretrained('/root/autodl-tmp/h3-precision-tools/models/Florence-2-base-ft', trust_remote_code=True, local_files_only=True); print('Florence-2 正常')"
/root/autodl-tmp/h3-precision-tools/facefusion-env/bin/python /root/autodl-tmp/h3-precision-tools/facefusion/facefusion.py --version
```

没有配置公共路径时，首次安装会下载约 463 MB 的 Florence-2、约 184 MB 的 SAM2.1 Small 和约 1.25 GiB 的 FaceFusion 必需模型；按第 3 节配置后会直接复用 Florence-2 和 SAM2 公共权重。生成时系统会在 H3 完成后释放 ComfyUI 模型，再启动 Florence-2、SAM2 和 FaceFusion，降低同时占用显存的概率。

重复执行安装脚本时，它会检查 Python 模块、模型文件和 FaceFusion 命令。全部存在就直接复用。看到 `Installing build dependencies` 说明检测到环境缺少模块、版本不匹配，或者使用了 `--force`，这时才会执行安装。

### 查看 GPU 使用情况

```bash
nvidia-smi
watch -n 1 nvidia-smi
```

客户端页面也会显示 GPU 利用率、显存、温度和功耗。

## 7. Mac 本地查看客户端

不要直接双击 `static/index.html`，页面需要通过 FastAPI 打开。首次运行：

```bash
cd /Users/linyongjia/Desktop/comfyui-video-studio
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

启动本地网页：

```bash
cd /Users/linyongjia/Desktop/comfyui-video-studio
H3_STUDIO_DATA_DIR="$PWD/data" \
H3_COMFYUI_URL="http://127.0.0.1:6008" \
.venv/bin/uvicorn app.main:app \
  --host 127.0.0.1 \
  --port 16006
```

另开一个终端：

```bash
open http://127.0.0.1:16006
```

如果完整服务运行在 AutoDL，也可以通过 SSH 隧道访问：

```bash
ssh -CNg -L 6006:127.0.0.1:6006 root@你的AutoDL主机 -p SSH端口
open http://127.0.0.1:6006
```

## 8. Excel 批量格式

在网页中点击“下载 Excel 模板”。`任务`工作表每行一个任务：

| 列 | 必填 | 含义 |
| --- | --- | --- |
| `upload_video_url` | 是 | 用户自行提供的公开视频 URL；不能短于 4 秒，超过 15 秒会自动分段生成并合并 |
| `reference_image_urls` | 否 | 用户自行提供的参考图片 URL，每行一个，最多 9 个 |
| `reference_roles` | 有参考图时是 | 与图片 URL 逐行对应；脸部图填 `face`，商品图填 `product` |
| `product_box` | 否 | AI 默认自动定位；识别不准时可填写首帧原商品框的归一化坐标 `x1,y1,x2,y2` |
| `prompt` | 否 | 商品外观或局部合成要求；写清“人物身上的上衣”等目标可提高自动定位准确率 |
| `aspect_ratio` | 否 | `auto`、`16:9`、`9:16`、`1:1`、`4:3`、`3:4` 或 `21:9` |
| `quality` | 否 | `low`、`standard` 或 `high`，默认 `low` |
| `seed` | 否 | 随机种子，留空时自动生成 |
| `scheduler` | 否 | `simple`、`normal`、`karras`、`exponential` 或 `sgm_uniform`，默认 `simple` |
| `sampler` | 否 | `res_multistep`、`euler`、`euler_ancestral`、`heun`、`dpmpp_2m` 或 `dpmpp_2m_sde` |
| `steps` | 否 | 1–100，默认 `20` |
| `denoise` | 否 | 0.01–1.00，默认 `1.0` |

URL 必须以 `http://` 或 `https://` 开头，并允许 AutoDL 服务器直接访问。临时过期、需要登录或禁止外链的 URL 无法使用。

`product_box` 可以留空。系统会先从提示词判断上衣、包、鞋等目标；提示词没有写明时，再从商品参考图判断，并自动定位视频首帧中的原商品。识别不准时才填写该列，例如 `0.42,0.46,0.72,0.88` 表示左上角在画面的 42%/46%，右下角在 72%/88%。网页上传可点击视频预览下方的“修正商品区域”拖框。

## 9. 目录与端口

- 客户端和任务 API：`6006`
- ComfyUI 节点和 API：`6008`
- GPU 与运行状态接口：`/api/system/status`
- 项目目录：`/root/autodl-tmp/comfyui-video-studio`
- 工作台数据：`/root/autodl-tmp/h3-studio-data`
- 镜像自带的 ComfyUI：`/root/ComfyUI`
- 第 2.2 节自行安装的 ComfyUI：`/root/autodl-tmp/ComfyUI`
- 安装下载缓存：`/root/autodl-tmp/.cache/uv`
- Florence-2、SAM2 与 FaceFusion：`/root/autodl-tmp/h3-precision-tools`
- 队列数据库：`$H3_STUDIO_DATA_DIR/studio.db`
- 生成结果：`$H3_STUDIO_DATA_DIR/outputs`
- ComfyUI 工作流：`workflows/minimax_h3_ref2va_api.json`

应用保持一个队列 worker，Uvicorn 必须使用 `--workers 1`。任务素材提交给 ComfyUI 后会从 ComfyUI input 临时目录清理，原始上传文件仍保留在工作台数据目录中，以便失败后重试。
