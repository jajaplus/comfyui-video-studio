# MiniMax H3 视频替换工作台

这是一个部署在 AutoDL 上的 MiniMax-H3 Ref2VA 客户端。推荐直接使用已经包含 `/root/ComfyUI` 的应用镜像；基础 PyTorch 镜像也可以按照后面的章节手动安装。浏览器负责上传视频、参考图片和 Excel，FastAPI 保存任务并按顺序提交给本机 ComfyUI。网页不设置登录或访问令牌，访问地址后即可使用。

## AutoDL 已有 ComfyUI 镜像快速部署

如果服务器根目录已经存在 `/root/ComfyUI` 和 `/root/start.sh`，使用本节即可，不要再运行 `scripts/install_autodl.sh`。后面的“基础镜像安装 ComfyUI”章节只用于没有 `/root/ComfyUI` 的服务器。

### 1. 上传并解压项目

在 Mac 上传已经生成的压缩包：

```bash
scp -P 你的SSH端口 \
  /Users/linyongjia/Desktop/comfyui-video-studio.tar.gz \
  root@你的AutoDL主机:/root/autodl-tmp/
```

在 AutoDL 终端解压：

```bash
cd /root/autodl-tmp
tar -xzf comfyui-video-studio.tar.gz
cd /root/autodl-tmp/comfyui-video-studio
chmod +x scripts/*.sh
```

### 2. 使用镜像自带的 ComfyUI

复制应用镜像专用配置，然后只安装客户端依赖：

```bash
cd /root/autodl-tmp/comfyui-video-studio
cp .env.autodl-comfyui.example .env
scripts/install_client_only.sh
```

该脚本不会安装或复制 ComfyUI，只会复用 `/root/ComfyUI`，因此安装很快。

检查镜像是否已经包含 MiniMax-H3 原生节点：

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

### 3. 挂载四个公共模型

先确认第三个哈希路径确实存在：

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

### 4. 启动

不要运行镜像根目录的 `/root/start.sh`；本项目会把 ComfyUI 启动在 6008，把客户端启动在 6006：

```bash
cd /root/autodl-tmp/comfyui-video-studio
scripts/start_all.sh
```

检查两个服务：

```bash
curl --max-time 5 http://127.0.0.1:6008/system_stats
curl --max-time 5 http://127.0.0.1:6006/health
```

第二个命令返回 `"engine":"ok"` 后，在 AutoDL 控制台创建 6006 端口的自定义服务并打开。需要查看 ComfyUI 节点页面时，再创建 6008 端口的自定义服务。

## 功能

- 视频和参考图片可多次点击、累计选择、预览和逐项删除。
- 一次最多选择 3 个视频，每个视频建立一个独立队列任务。
- 每个任务使用 1 个上传视频和最多 9 张人物或商品参考图。
- 视频时长自动读取并显示，支持 4–15 秒。
- 生成时长跟随上传视频，画面比例默认跟随视频方向。
- Excel 一次最多导入 3 个任务，素材 URL 由用户自行填写。
- SQLite 持久化队列，支持查看进度、下载、失败重试和删除任务。
- 通过 ComfyUI 原生 `MiniMaxH3ReferenceToVideo` 工作流生成带声音的视频。

MiniMax-H3 Ref2VA 会重新生成画面与声音，不能保证逐帧复制原视频，也不是传统的像素级换脸或商品贴片。快速动作、遮挡、手部接触和包装小字可能发生变化。

## 1. 租用 AutoDL 实例

创建实例时选择 **PyTorch 基础镜像**，不要选择 ComfyUI 应用镜像。基础镜像需要满足：

- NVIDIA GPU，建议 48GB 显存；24GB 显存可尝试依靠模型卸载运行，但会更慢，并且需要充足的内存。
- PyTorch 2.7 或更高版本，并且镜像详情中的 CUDA 与所租显卡兼容。
- 建议 64GB 或更多内存，数据盘至少预留 30GB 给环境、任务素材和输出。

四个量化权重合计约 40GB。使用 AutoDL 公共模型软链接时不会把这 40GB 再复制到自己的数据盘，但运行时仍然需要显存和内存装载模型。

创建实例后先选择 **无 GPU 模式开机**，完成上传和安装，减少 GPU 租用时间。无 GPU 模式下执行：

```bash
python3 -c "import torch; print('PyTorch:', torch.__version__, 'CUDA构建:', torch.version.cuda, '当前GPU可用:', torch.cuda.is_available())"
```

此时 `当前GPU可用` 显示 `False` 是正常现象，因为无卡模式没有挂载 GPU；`CUDA构建` 必须显示具体版本，不能是 `None`。如果是 `None`，说明基础镜像安装了 CPU 版 PyTorch，需要更换带 CUDA 的 PyTorch 基础镜像。

## 2. 使用 AutoDL 公共模型

本项目使用 AutoDL 公共模型，不需要重新下载完整的 Hugging Face 模型。需要在 AutoDL“公共模型”中搜索以下四个文件：

| 类型 | 公共模型文件名 | ComfyUI 目录 |
| --- | --- | --- |
| Ref2VA 扩散模型 | `minimax_h3_ref2va_pruned_int8_convrot.safetensors` | `models/diffusion_models` |
| 文本编码器 | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | `models/text_encoders` |
| 视频 VAE | `minimax_h3_video_vae_int8_convrot.safetensors` | `models/vae` |
| 音频 VAE | `minimax_h3_audio_vae_fp32.safetensors` | `models/vae` |

公共模型页面会给出一个以 `/.autodl/` 开头的源路径。应用通过软链接直接读取这些文件，不复制模型权重。

## 3. 上传项目

### 3.1 在 Mac 压缩项目

打开 Mac 终端执行：

```bash
cd /Users/linyongjia/Desktop

tar \
  --exclude='comfyui-video-studio/.venv' \
  --exclude='comfyui-video-studio/data' \
  --exclude='comfyui-video-studio/logs' \
  --exclude='comfyui-video-studio/.git' \
  --exclude='comfyui-video-studio/__pycache__' \
  --exclude='comfyui-video-studio/.DS_Store' \
  -czf comfyui-video-studio.tar.gz \
  comfyui-video-studio

ls -lh /Users/linyongjia/Desktop/comfyui-video-studio.tar.gz
```

压缩包会生成在：

```text
/Users/linyongjia/Desktop/comfyui-video-studio.tar.gz
```

压缩包不包含本地虚拟环境、已经上传的任务素材、生成结果、运行日志和 Git 历史，这些内容不需要传到服务器。

### 3.2 上传到 AutoDL

在 Mac 终端执行，把主机地址和 SSH 端口替换成 AutoDL 控制台显示的值：

```bash
scp -P 你的SSH端口 \
  /Users/linyongjia/Desktop/comfyui-video-studio.tar.gz \
  root@你的AutoDL主机:/root/autodl-tmp/
```

例如 AutoDL 给出的登录命令是：

```text
ssh -p 12345 root@region-1.autodl.com
```

对应的上传命令就是：

```bash
scp -P 12345 \
  /Users/linyongjia/Desktop/comfyui-video-studio.tar.gz \
  root@region-1.autodl.com:/root/autodl-tmp/
```

### 3.3 在 AutoDL 解压

先通过 SSH 登录服务器：

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

看到 `app`、`scripts`、`static`、`workflows` 和 `README.md` 等文件，说明解压成功。

确认解压成功后，可以删除服务器上的压缩包，释放空间：

```bash
rm /root/autodl-tmp/comfyui-video-studio.tar.gz
```

后续只更新少量代码时，也可以直接用 `rsync` 同步：

```bash
rsync -av --exclude '.venv' --exclude 'data' --exclude '.git' \
  -e 'ssh -p 你的SSH端口' \
  /Users/linyongjia/Desktop/comfyui-video-studio/ \
  root@你的AutoDL主机:/root/autodl-tmp/comfyui-video-studio/
```

## 4. 在无 GPU 模式安装 ComfyUI 和客户端

在 AutoDL 终端执行：

```bash
cd /root/autodl-tmp/comfyui-video-studio
chmod +x scripts/*.sh
cp -n .env.example .env
scripts/install_autodl.sh
```

该命令会完成以下工作：

1. 检查 PyTorch 版本以及是否为 CUDA 构建；无 GPU 模式不会因为 `torch.cuda.is_available()` 为 `False` 而停止。
2. 将最新版 ComfyUI 安装到 `/root/autodl-tmp/ComfyUI`。
3. 为 ComfyUI 建立独立环境，并复用基础镜像中已经适配 GPU 的 PyTorch。
4. 检查 ComfyUI 是否包含原生 `MiniMaxH3ReferenceToVideo` 节点。
5. 安装视频工作台依赖。

ComfyUI 和任务数据都位于 `/root/autodl-tmp` 数据盘。实例释放前只要保留数据盘，环境、任务数据库和生成结果就不会随系统盘消失。

安装脚本使用快速模式：ComfyUI 虚拟环境直接复用基础镜像中的 `torch` 和 `torchvision`，只安装其余依赖，不会重复下载整套 PyTorch/CUDA。`uv` 下载缓存保存在 `/root/autodl-tmp/.cache/uv`；网络中断后重新执行 `scripts/install_autodl.sh` 会复用已经下载的文件。

安装结束后先不要执行 `scripts/start_all.sh`。无 GPU 模式可以完成依赖安装和模型挂载，但不能运行 MiniMax-H3 推理。

## 5. 在无 GPU 模式挂载公共模型

从公共模型页面复制上述四个文件的源路径，然后在 AutoDL 终端执行：

```bash
cd /root/autodl-tmp/comfyui-video-studio

scripts/link_autodl_models.sh \
  '/.autodl/Comfy-Org/MiniMax-H3/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors' \
  '/.autodl/Comfy-Org/MiniMax-H3/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors ' \
  '/.autodl/15/22/fc/1522fc49e094bb75c704ee519582252d' \
  '/.autodl/Comfy-Org/MiniMax-H3/vae/minimax_h3_audio_vae_fp32.safetensors'
```

脚本只创建软链接。如果 AutoDL 页面已经给出了 `ln -s` 命令，也可以直接执行页面提供的命令，但目标文件名和目录必须与上表一致。

## 6. 切换到 GPU 模式并启动服务

依赖和公共模型准备完成后：

1. 在 AutoDL 控制台关闭当前无 GPU 实例。
2. 选择需要使用的 GPU，以 GPU 模式重新开机。
3. 重新进入终端，检查 GPU 和 PyTorch。

```bash
nvidia-smi
python3 -c "import torch; print('PyTorch:', torch.__version__, 'CUDA:', torch.version.cuda, 'GPU可用:', torch.cuda.is_available(), 'GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else '无')"
```

这里的 `GPU可用` 必须显示 `True`。然后启动 ComfyUI 和客户端：

```bash
cd /root/autodl-tmp/comfyui-video-studio
scripts/start_all.sh
```

如果服务器上已经部署过旧版本，并且 `.env` 中仍然是 8188，可执行下面的命令改为 6008 后重启：

```bash
cd /root/autodl-tmp/comfyui-video-studio
sed -i 's#http://127.0.0.1:8188#http://127.0.0.1:6008#g' .env
scripts/stop_all.sh
scripts/start_all.sh
```

查看日志：

```bash
tail -f logs/comfyui.log
tail -f logs/app.log
```

检查状态：

```bash
curl http://127.0.0.1:6006/health
```

返回 `engine: ok` 表示网页已连接 ComfyUI。客户端监听 6006，ComfyUI 监听 6008。

在 AutoDL 控制台打开 6006 端口的“自定义服务”地址。访问者打开该地址即可上传素材和创建任务，不需要输入令牌。所有访问者共用同一个任务队列。

如果要打开 ComfyUI 自己的节点页面，可以在 AutoDL 控制台再创建一个 **6008 端口**的“自定义服务”，然后打开 AutoDL 生成的访问地址。

也可以不公开 6008，在 Mac 建立 SSH 隧道访问：

```bash
ssh -CNg -L 16008:127.0.0.1:6008 root@你的AutoDL主机 -p SSH端口
open http://127.0.0.1:16008
```

平时使用视频客户端只需开放 6006；6008 只用于查看 ComfyUI 节点页面和接收本机后端提交的任务。

停止服务：

```bash
scripts/stop_all.sh
```

重启实例后不需要重新安装或重新挂载模型，只需执行：

```bash
cd /root/autodl-tmp/comfyui-video-studio
scripts/start_all.sh
```

## 常见安装问题

### ComfyUI 安装很慢

新版脚本正常安装时会出现：

```text
正在快速安装 ComfyUI 依赖；复用基础镜像的 torch/torchvision，不重复下载 CUDA 大包。
```

如果终端正在下载文件名以 `torch-` 或 `nvidia-` 开头的数 GB 大包，说明服务器使用的还是旧安装脚本。可以按 `Ctrl+C` 停止，上传最新项目后重新执行：

```bash
cd /root/autodl-tmp/comfyui-video-studio
chmod +x scripts/*.sh
scripts/install_autodl.sh
```

不需要删除 `/root/autodl-tmp/ComfyUI`；脚本会保留已经克隆的源码和下载缓存，从未完成的位置继续安装。如果只是网络暂时中断，直接重复执行同一条安装命令即可。

### 页面显示“ComfyUI 未连接（6008）”

这个状态表示客户端本身已经启动，但无法访问 ComfyUI 的 `6008` 端口。在 AutoDL 终端依次执行：

```bash
cd /root/autodl-tmp/comfyui-video-studio
grep '^H3_COMFYUI' .env
curl --max-time 5 http://127.0.0.1:6008/system_stats
tail -n 100 logs/comfyui.log
```

正常情况下，`.env` 中应显示：

```text
H3_COMFYUI_URL=http://127.0.0.1:6008
```

如果仍显示 8188，修改并重启：

```bash
cd /root/autodl-tmp/comfyui-video-studio
sed -i 's#http://127.0.0.1:8188#http://127.0.0.1:6008#g' .env
scripts/stop_all.sh
scripts/start_all.sh
```

重新检查：

```bash
curl --max-time 5 http://127.0.0.1:6008/system_stats
curl --max-time 5 http://127.0.0.1:6006/health
```

第一个命令需要返回 ComfyUI 系统信息，第二个命令需要返回 `"engine":"ok"`。如果第一个命令仍然连接失败，具体启动错误就在 `logs/comfyui.log` 中。

Mac 本地预览只启动客户端，没有运行 MiniMax-H3 ComfyUI，因此会显示未连接。实际生成视频时，应打开 AutoDL 的 6006 自定义服务地址。

如果安装脚本提示 PyTorch 低于 2.7，直接更换较新的 PyTorch 基础镜像。不要在旧镜像上混装另一套 CUDA。

如果无 GPU 安装时提示“当前安装的是 CPU 版 PyTorch”，说明 `torch.version.cuda` 为 `None`。这种镜像以后即使挂载 GPU 也不能直接使用，需要更换带 CUDA 版 PyTorch 的基础镜像。

如果启动时提示“当前没有可用 GPU”，说明实例仍处于无 GPU 模式，或者 GPU 模式下的 PyTorch 没有识别到显卡。先执行 `nvidia-smi` 和上面的 PyTorch GPU 检查命令。

如果提示找不到 `MiniMaxH3ReferenceToVideo`，更新 ComfyUI 后重新安装依赖：

```bash
cd /root/autodl-tmp/ComfyUI
git pull --ff-only
.venv/bin/uv pip install --python .venv/bin/python -r requirements.txt
```

如果服务未启动，先查看两份日志：

```bash
tail -n 100 /root/autodl-tmp/comfyui-video-studio/logs/comfyui.log
tail -n 100 /root/autodl-tmp/comfyui-video-studio/logs/app.log
```

## Mac 本地查看客户端

不要直接双击 `static/index.html`，页面需要通过 FastAPI 打开。首次运行：

```bash
cd /Users/linyongjia/Desktop/comfyui-video-studio
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

启动网页预览：

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

Mac 没有运行 ComfyUI 时可以查看页面和预览素材，但不能生成视频。

如果完整服务运行在 AutoDL，可通过 SSH 隧道访问：

```bash
ssh -CNg -L 6006:127.0.0.1:6006 root@你的AutoDL主机 -p SSH端口
open http://127.0.0.1:6006
```

## Excel 批量格式

在网页中点击“下载 Excel 模板”。`任务`工作表每行一个任务：

| 列 | 必填 | 含义 |
| --- | --- | --- |
| `upload_video_url` | 是 | 用户自行提供的公开视频 URL，时长必须为 4–15 秒 |
| `reference_image_urls` | 否 | 用户自行提供的参考图片 URL，每行一个，最多 9 个 |
| `prompt` | 否 | 视频生成提示词 |
| `aspect_ratio` | 否 | `auto`、`16:9`、`9:16`、`1:1`、`4:3`、`3:4` 或 `21:9` |
| `seed` | 否 | 随机种子，留空时自动生成 |

URL 必须以 `http://` 或 `https://` 开头，并允许 AutoDL 服务器直接访问。临时过期、需要登录或禁止外链的 URL 无法使用。

## 目录与端口

- 客户端和任务 API：`6006`
- ComfyUI 节点和 API：`6008`
- 安装下载缓存：`/root/autodl-tmp/.cache/uv`
- 队列数据库：`$H3_STUDIO_DATA_DIR/studio.db`
- 生成结果：`$H3_STUDIO_DATA_DIR/outputs`
- ComfyUI 工作流：`workflows/minimax_h3_ref2va_api.json`

应用保持一个队列 worker，Uvicorn 必须使用 `--workers 1`。任务素材提交给 ComfyUI 后会从 ComfyUI input 临时目录清理，原始上传文件仍保留在工作台数据目录中，以便失败后重试。
