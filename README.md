# MiniMax H3 视频替换工作台

这是一个部署在 AutoDL 上的 MiniMax-H3 Ref2VA 客户端。浏览器负责上传视频、参考图片和 Excel，FastAPI 保存任务并按顺序提交给本机 ComfyUI。网页不设置登录或访问令牌，打开地址即可使用。

推荐租用已经包含 `/root/ComfyUI` 的 AutoDL 应用镜像。只有服务器上没有 ComfyUI 时，才需要使用 PyTorch 基础镜像安装完整环境。

## 功能

- 视频和参考图片可多次选择、累计上传、预览和逐项删除。
- 一次最多选择 3 个视频，每个视频建立一个独立队列任务。
- 每个任务使用 1 个上传视频和最多 9 张人物或商品参考图。
- 视频时长自动读取并显示，视频不能短于 4 秒。
- 超过 15 秒的视频会切成多个长度接近的 4–15 秒片段，依次生成后自动合并成一个视频。
- 生成总时长跟随上传视频，画面比例默认跟随视频方向。
- 视频清晰度支持低清、标清和高清三档，默认低清以缩短生成时间。
- Excel 一次最多导入 3 个任务，素材 URL 由用户自行填写。
- SQLite 持久化队列，支持查看进度、下载、失败重试和删除任务。
- 客户端每 3 秒刷新 GPU 利用率、显存、温度和功耗。
- 当前任务显示准备素材、模型加载、参考编码、采样、视频解码和保存等环节。
- 生成中的任务按秒显示已耗时，结束后保留包含切割、生成和合并过程的总耗时。
- 通过 ComfyUI 原生 `MiniMaxH3ReferenceToVideo` 工作流生成带声音的视频。

MiniMax-H3 Ref2VA 会重新生成画面与声音，不能保证逐帧复制原视频，也不是传统的像素级换脸或商品贴片。快速动作、遮挡、手部接触和包装小字可能发生变化。

长视频的每个片段由模型独立生成，因此片段连接处可能出现轻微的画面、人物细节或声音跳变。任务列表会显示总片段数、当前片段和整体进度；任意片段失败时可直接重试整个任务。

清晰度控制的是模型生成画布大小。模型仍然使用 24fps 和 20 个采样步骤：

| 清晰度 | 16:9 | 9:16 | 1:1 | 4:3 | 3:4 | 21:9 |
| --- | --- | --- | --- | --- | --- | --- |
| `low`（默认、最快） | 672×384 | 384×672 | 512×512 | 576×448 | 448×576 | 768×320 |
| `standard`（均衡） | 832×480 | 480×832 | 640×640 | 736×544 | 544×736 | 960×416 |
| `high`（最慢） | 1344×768 | 768×1344 | 1024×1024 | 1024×768 | 768×1024 | 1568×672 |

生成耗时主要随总帧数、画面像素量、采样步数和参考图片数量增长。高清横屏的像素量约为低清的 4 倍，因此同一视频可能明显更慢；具体倍数还取决于显卡、显存卸载和模型加载状态。

## 部署流程总览

首次部署按下面顺序操作：

1. 在 Mac 压缩项目并上传到 AutoDL。
2. 根据服务器镜像选择一种安装方式：
   - 已有 `/root/ComfyUI`：只安装客户端依赖。
   - 没有 ComfyUI：在无 GPU 模式安装 ComfyUI 和客户端。
3. 挂载 AutoDL 公共模型。
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

压缩包不会包含本地虚拟环境、服务器配置、任务数据、生成结果、日志和 Git 历史。

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

## 2. 安装项目

下面两种安装方式只选择一种。

### 2.1 AutoDL 已有 ComfyUI 镜像（推荐）

适用于服务器根目录已经存在 `/root/ComfyUI` 和 `/root/start.sh` 的镜像。不要运行 `scripts/install_autodl.sh`，也不要运行镜像根目录的 `/root/start.sh`。

复制配置并只安装客户端依赖：

```bash
cd /root/autodl-tmp/comfyui-video-studio
cp -n .env.autodl-comfyui.example .env
scripts/install_client_only.sh
```

该脚本复用 `/root/ComfyUI`，不会重新下载或复制 ComfyUI。

检查镜像是否包含 MiniMax-H3 原生节点：

```bash
grep -Rqs --exclude-dir=.git --exclude-dir=.venv \
  'MiniMaxH3ReferenceToVideo' \
  /root/ComfyUI/comfy /root/ComfyUI/comfy_extras \
  && echo 'MiniMax-H3 节点正常' \
  || echo '需要更新 ComfyUI'
```

如果显示“需要更新 ComfyUI”，执行：

```bash
cd /root/ComfyUI
git pull --ff-only

if [ -x /root/miniconda3/envs/comfyui/bin/python ]; then
  COMFY_PYTHON=/root/miniconda3/envs/comfyui/bin/python
else
  COMFY_PYTHON=python3
fi

"$COMFY_PYTHON" -m pip install -r requirements.txt
```

### 2.2 PyTorch 基础镜像

只有服务器上没有 `/root/ComfyUI` 时才使用本节。建议配置：

- NVIDIA GPU，建议 48GB 显存；24GB 显存可以尝试模型卸载，但速度更慢，并需要充足内存。
- PyTorch 2.7 或更高版本，且 CUDA 与所租显卡兼容。
- 建议 64GB 或更多内存，数据盘至少预留 30GB。

先使用无 GPU 模式开机，以减少 GPU 租用时间。确认镜像中的 PyTorch 是 CUDA 构建：

```bash
python3 -c "import torch; print('PyTorch:', torch.__version__, 'CUDA构建:', torch.version.cuda, '当前GPU可用:', torch.cuda.is_available())"
```

无 GPU 模式下 `当前GPU可用` 显示 `False` 是正常现象；`CUDA构建` 必须是具体版本，不能是 `None`。

安装 ComfyUI 和客户端：

```bash
cd /root/autodl-tmp/comfyui-video-studio
cp -n .env.example .env
scripts/install_autodl.sh
```

ComfyUI 会安装到 `/root/autodl-tmp/ComfyUI`。脚本复用基础镜像中的 PyTorch，不会重复下载整套 CUDA。安装结束后先不要启动服务，继续挂载公共模型。

## 3. 挂载 AutoDL 公共模型

需要在 AutoDL 公共模型中找到以下四个文件：

| 类型 | 公共模型文件名 | ComfyUI 目录 |
| --- | --- | --- |
| Ref2VA 扩散模型 | `minimax_h3_ref2va_pruned_int8_convrot.safetensors` | `models/diffusion_models` |
| 文本编码器 | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | `models/text_encoders` |
| 视频 VAE | `minimax_h3_video_vae_int8_convrot.safetensors` | `models/vae` |
| 音频 VAE | `minimax_h3_audio_vae_fp32.safetensors` | `models/vae` |

四个量化权重合计约 40GB。软链接会直接读取 AutoDL 公共模型，不会再复制一份到自己的数据盘。

先确认第三个哈希路径存在：

```bash
ls -lh '/.autodl/15/22/fc/1522fc49e094bb75c704ee519582252d'
```

然后执行：

```bash
cd /root/autodl-tmp/comfyui-video-studio

scripts/link_autodl_models.sh \
  '/.autodl/Comfy-Org/MiniMax-H3/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors' \
  '/.autodl/Comfy-Org/MiniMax-H3/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors' \
  '/.autodl/15/22/fc/1522fc49e094bb75c704ee519582252d' \
  '/.autodl/Comfy-Org/MiniMax-H3/vae/minimax_h3_audio_vae_fp32.safetensors'
```

脚本只创建软链接。如果公共模型页面提供的源路径不同，以页面显示的实际路径为准。

## 4. 切换到 GPU 模式并启动

如果当前是无 GPU 模式，先在 AutoDL 控制台关机，再选择 GPU 并重新开机。进入终端后检查 GPU：

```bash
nvidia-smi
python3 -c "import torch; print('PyTorch:', torch.__version__, 'CUDA:', torch.version.cuda, 'GPU可用:', torch.cuda.is_available(), 'GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else '无')"
```

`GPU可用` 必须显示 `True`。启动 ComfyUI 和客户端：

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

压缩包已经排除了 `.env`、`.venv`、`data` 和 `logs`，解压时只覆盖程序代码和说明文件。

### 5.3 更新客户端依赖并重启

无论使用哪一种镜像，都可以执行下面的通用更新命令：

```bash
cd /root/autodl-tmp/comfyui-video-studio

if [ -x .venv/bin/uv ]; then
  .venv/bin/uv pip install --python .venv/bin/python -r requirements.txt
else
  .venv/bin/python -m pip install -r requirements.txt
fi

scripts/start_all.sh
```

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

### 提示找不到 MiniMaxH3ReferenceToVideo

已有 ComfyUI 应用镜像：

```bash
cd /root/ComfyUI
git pull --ff-only
```

PyTorch 基础镜像安装的 ComfyUI：

```bash
cd /root/autodl-tmp/ComfyUI
git pull --ff-only
.venv/bin/uv pip install --python .venv/bin/python -r requirements.txt
```

更新后重启服务。

### 基础镜像安装很慢

新版安装脚本会复用基础镜像中的 `torch` 和 `torchvision`。如果终端正在下载以 `torch-` 或 `nvidia-` 开头的数 GB 文件，说明服务器使用的是旧安装脚本。停止安装、上传最新压缩包，然后重新执行：

```bash
cd /root/autodl-tmp/comfyui-video-studio
scripts/install_autodl.sh
```

不需要删除 `/root/autodl-tmp/ComfyUI`，脚本会复用已经下载的文件。

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
| `prompt` | 否 | 视频生成提示词 |
| `aspect_ratio` | 否 | `auto`、`16:9`、`9:16`、`1:1`、`4:3`、`3:4` 或 `21:9` |
| `quality` | 否 | `low`、`standard` 或 `high`，默认 `low` |
| `seed` | 否 | 随机种子，留空时自动生成 |

URL 必须以 `http://` 或 `https://` 开头，并允许 AutoDL 服务器直接访问。临时过期、需要登录或禁止外链的 URL 无法使用。

## 9. 目录与端口

- 客户端和任务 API：`6006`
- ComfyUI 节点和 API：`6008`
- GPU 与运行状态接口：`/api/system/status`
- 项目目录：`/root/autodl-tmp/comfyui-video-studio`
- 工作台数据：`/root/autodl-tmp/h3-studio-data`
- 基础镜像的 ComfyUI：`/root/autodl-tmp/ComfyUI`
- 应用镜像自带的 ComfyUI：`/root/ComfyUI`
- 安装下载缓存：`/root/autodl-tmp/.cache/uv`
- 队列数据库：`$H3_STUDIO_DATA_DIR/studio.db`
- 生成结果：`$H3_STUDIO_DATA_DIR/outputs`
- ComfyUI 工作流：`workflows/minimax_h3_ref2va_api.json`

应用保持一个队列 worker，Uvicorn 必须使用 `--workers 1`。任务素材提交给 ComfyUI 后会从 ComfyUI input 临时目录清理，原始上传文件仍保留在工作台数据目录中，以便失败后重试。
