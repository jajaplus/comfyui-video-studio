# MiniMax H3 视频替换工作台部署手册

本文只保留 Mac 打包上传、AutoDL 首次部署、模型加载、服务启动、代码更新、本地查看和部署故障处理。

## 部署结构

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

这一步只安装工作台自身依赖，不下载、复制或更新 ComfyUI。不要运行 `scripts/install_autodl.sh`、`git pull` 或镜像根目录的 `/root/start.sh`。安装完成后继续第 3 节。

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

如果日志中的启动文件是 `/root/autodl-tmp/ComfyUI/main.py`，但当前镜像已经自带 `/root/ComfyUI/main.py`，说明数据盘保留了旧 `.env`。切换到镜像自带版本，并重新挂载四个 H3 模型：

```bash
cd /root/autodl-tmp/comfyui-video-studio
scripts/stop_all.sh
test -f /root/ComfyUI/main.py

sed -i \
  -e '/^H3_COMFYUI_DIR=/d' \
  -e '/^H3_COMFYUI_INPUT_DIR=/d' \
  -e '/^H3_COMFYUI_OUTPUT_DIR=/d' \
  -e '/^H3_COMFYUI_URL=/d' \
  -e '/^H3_COMFYUI_PYTHON=/d' \
  .env
echo 'H3_COMFYUI_DIR=/root/ComfyUI' >> .env
echo 'H3_COMFYUI_INPUT_DIR=/root/ComfyUI/input' >> .env
echo 'H3_COMFYUI_OUTPUT_DIR=/root/ComfyUI/output' >> .env
echo 'H3_COMFYUI_URL=http://127.0.0.1:6008' >> .env

if [ ! -d /root/ComfyUI/models ]; then
  if [ -e /root/ComfyUI/models ] || [ -L /root/ComfyUI/models ]; then
    mv /root/ComfyUI/models "/root/ComfyUI/models.backup.$(date +%s)"
  fi
  mkdir -p /root/ComfyUI/models
fi

scripts/link_autodl_models.sh \
  '/.autodl/Comfy-Org/MiniMax-H3/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors' \
  '/.autodl/Comfy-Org/MiniMax-H3/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors' \
  '/.autodl/15/22/fc/1522fc49e094bb75c704ee519582252d' \
  '/.autodl/Comfy-Org/MiniMax-H3/vae/minimax_h3_audio_vae_fp32.safetensors'

unset OMP_NUM_THREADS
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
/root/autodl-tmp/h3-precision-tools/sam2-env/bin/python -c "import cv2, numpy, sam2; print('SAM2 与 OpenCV 正常')"
/root/autodl-tmp/h3-precision-tools/sam2-env/bin/python -c "from transformers import AutoProcessor; AutoProcessor.from_pretrained('/root/autodl-tmp/h3-precision-tools/models/Florence-2-base-ft', trust_remote_code=True, local_files_only=True); print('Florence-2 正常')"
/root/autodl-tmp/h3-precision-tools/facefusion-env/bin/python -c "import onnxruntime as ort; print(ort.get_available_providers()); assert 'CUDAExecutionProvider' in ort.get_available_providers()"
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
