# VACE 精准视频替换工作台部署手册

本项目只做精准替换：

- Florence-2 根据提示词和商品参考图定位原商品。
- SAM2 跟踪商品并生成逐帧蒙版。
- Wan2.1 VACE 只重绘蒙版区域，不重新生成整个人和背景。
- FaceFusion 只替换脸部。
- 最后恢复原视频的声音和准确时长。

网页端口为 `6006`，ComfyUI 内部端口为 `6008`。6008 不需要对外开放。

## 1. 在 Mac 上压缩项目

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
```

如果桌面已经存在 `comfyui-video-studio.tar.gz`，上面的命令会直接覆盖它。然后把压缩包上传到 AutoDL 的 `/root/autodl-tmp/`。

## 2. 在 AutoDL 解压

```bash
cd /root/autodl-tmp
tar -xzf comfyui-video-studio.tar.gz
cd /root/autodl-tmp/comfyui-video-studio
chmod +x scripts/*.sh
```

更新解压会覆盖压缩包内同名代码文件，不会覆盖压缩时排除的 `.env`、任务数据和日志。

## 3. 准备 ComfyUI

### 3.1 镜像已经包含 ComfyUI

先检查路径和 VACE 节点：

```bash
test -f /root/ComfyUI/main.py && echo '找到 ComfyUI'
grep -Rqs 'WanVaceToVideo' /root/ComfyUI/comfy /root/ComfyUI/comfy_extras \
  && echo '支持 Wan VACE' \
  || echo '当前 ComfyUI 太旧，不支持 Wan VACE'
```

如果显示支持 Wan VACE，只安装工作台依赖：

```bash
cd /root/autodl-tmp/comfyui-video-studio
cp -n .env.example .env

sed -i \
  -e 's#^H3_COMFYUI_DIR=.*#H3_COMFYUI_DIR=/root/ComfyUI#' \
  -e 's#^H3_COMFYUI_INPUT_DIR=.*#H3_COMFYUI_INPUT_DIR=/root/ComfyUI/input#' \
  -e 's#^H3_COMFYUI_OUTPUT_DIR=.*#H3_COMFYUI_OUTPUT_DIR=/root/ComfyUI/output#' \
  .env

scripts/install_client_only.sh
```

环境变量继续使用已有的 `H3_` 前缀，只是为了兼容服务器上原来的 `.env`；生成链路已经不再加载 MiniMax-H3 模型。

### 3.2 镜像没有 ComfyUI，或现有版本不支持 VACE

安装独立 ComfyUI 到数据盘，不修改镜像自带目录：

```bash
cd /root/autodl-tmp/comfyui-video-studio
cp -n .env.example .env
scripts/install_autodl.sh
```

安装完成后的目录是 `/root/autodl-tmp/ComfyUI`。在无 GPU 开机模式下可以完成依赖安装，安装结束后再切换到 GPU 模式启动。

## 4. 一次准备全部模型

### 4.1 Wan VACE 模型

需要以下三个文件：

| 文件 | ComfyUI 目录 |
| --- | --- |
| `wan2.1_vace_14B_fp16.safetensors` | `models/diffusion_models` |
| `umt5_xxl_fp8_e4m3fn_scaled.safetensors` | `models/text_encoders` |
| `wan_2.1_vae.safetensors` | `models/vae` |

已确认的 AutoDL 模型广场公共路径：

```text
wan2.1_vace_14B_fp16.safetensors：/.autodl/e8/9f/11/e89f11d81110d363b9fde6e921e802b7
umt5_xxl_fp8_e4m3fn_scaled.safetensors：/.autodl/4b/ad/50/4bad5031eb889beddcfb6e1b9b12cccc
wan_2.1_vae.safetensors：/.autodl/0d/d0/84/0dd084bcf4728fe874a9e3b18110af74
```

在 AutoDL 服务器上直接执行：

```bash
cd /root/autodl-tmp/comfyui-video-studio
scripts/link_autodl_models.sh
```

脚本会把上面的三个公共模型挂载到当前 `.env` 指定的 ComfyUI。它只建立软链接，不复制大模型，也不会覆盖公共目录里的原文件。需要使用其他路径时，也可以把三个路径依次传给脚本。

### 4.2 Florence-2、SAM2 和 FaceFusion

FaceFusion 3.9.0 的视频读取需要支持 `-fps_mode` 的 FFmpeg。项目会把 FFmpeg 7 安装到独立环境，不替换系统版本；`scripts/install_precision_tools.sh` 会自动完成这一步。已安装精准工具的服务器只需运行一次：

```bash
cd /root/autodl-tmp/comfyui-video-studio
scripts/install_ffmpeg.sh
scripts/stop_all.sh
scripts/start_all.sh
```

商品替换默认使用 50 Steps。旧草稿若保存了 4 Steps 等低步数，页面会保留数值并提示改为至少 30 Steps。换脸身份强度的新默认值是 0.85；如果服务器 `.env` 已有 `H3_FACEFUSION_SWAPPER_WEIGHT=0.70`，可改成 `0.85` 后重启服务。

已确认的 AutoDL 公共路径：

```text
Florence-2：/.autodl/53/68/2a/53682a80f2a8321a6133a95084a4f86d
SAM2.1 Small：/.autodl/51/71/3b/51713b3d1994696d27f35f9c6de6f5ef
```

写入配置并安装缺少的运行文件：

```bash
cd /root/autodl-tmp/comfyui-video-studio

sed -i \
  -e '/^H3_FLORENCE2_PUBLIC_PATH=/d' \
  -e '/^H3_SAM2_CHECKPOINT=/d' \
  .env

echo 'H3_FLORENCE2_PUBLIC_PATH=/.autodl/53/68/2a/53682a80f2a8321a6133a95084a4f86d' >> .env
echo 'H3_SAM2_CHECKPOINT=/.autodl/51/71/3b/51713b3d1994696d27f35f9c6de6f5ef' >> .env

scripts/install_precision_tools.sh
```

这个安装脚本会复用完整环境。只有文件缺失时才安装或下载；普通代码更新不需要重复运行。

换脸开始前会依次抽查视频的中段、前段和后段，执行真实换脸预检，并确认 `inswapper_128_fp16` 已创建 CUDA 会话。任一抽查画面成功后才处理整段视频；如果参考图或三个视频帧都没有检测到脸，任务会直接报错，不再把未换脸的原画面当成成功结果。换脸日志会保存在：

```text
/root/autodl-tmp/comfyui-video-studio/logs/facefusion/
```


## 5. 启动服务

切换到 GPU 开机模式后执行：

```bash
cd /root/autodl-tmp/comfyui-video-studio
scripts/stop_all.sh
scripts/start_all.sh
```

检查服务：

```bash
curl --max-time 5 http://127.0.0.1:6008/system_stats
curl --max-time 5 http://127.0.0.1:6006/health
```

查看日志：

```bash
tail -f /root/autodl-tmp/comfyui-video-studio/logs/comfyui.log
tail -f /root/autodl-tmp/comfyui-video-studio/logs/app.log
```

在 AutoDL 控制台为 `6006` 创建自定义服务，然后打开 AutoDL 提供的访问地址。无需开放 `6008`。

## 6. Mac 本地查看页面

Mac 本地只预览客户端页面，不执行 VACE：

```bash
cd /Users/linyongjia/Desktop/comfyui-video-studio
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

H3_STUDIO_DATA_DIR="$PWD/data" \
H3_COMFYUI_URL="http://127.0.0.1:6008" \
.venv/bin/uvicorn app.main:app \
  --host 127.0.0.1 \
  --port 6006
```

浏览器打开：

```text
http://127.0.0.1:6006
```

如果提示 `6006 address already in use`：

```bash
lsof -nP -iTCP:6006 -sTCP:LISTEN
kill <上一步显示的 PID>
```

## 7. 以后更新项目代码

先在 Mac 重新执行第 1 节压缩命令并上传新的压缩包，然后在服务器执行：

```bash
cd /root/autodl-tmp/comfyui-video-studio
scripts/stop_all.sh

cd /root/autodl-tmp
tar -xzf comfyui-video-studio.tar.gz

cd /root/autodl-tmp/comfyui-video-studio
chmod +x scripts/*.sh
scripts/install_client_only.sh
scripts/install_ffmpeg.sh
scripts/start_all.sh
```

VACE 公共模型仍然存在时只需运行 `scripts/link_autodl_models.sh`；只有精准工具环境缺失或损坏时运行 `scripts/install_precision_tools.sh`。
如果旧任务报 `Unrecognized option 'fps_mode'`，按上述步骤更新并重启服务，再重试任务。

## 8. 部署检查

```bash
cd /root/autodl-tmp/comfyui-video-studio
set -a
source .env
set +a

test -f workflows/wan_vace_inpaint_api.json
test -f "${H3_COMFYUI_DIR:-/root/ComfyUI}/models/diffusion_models/wan2.1_vace_14B_fp16.safetensors"
test -f "${H3_COMFYUI_DIR:-/root/ComfyUI}/models/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors"
test -f "${H3_COMFYUI_DIR:-/root/ComfyUI}/models/vae/wan_2.1_vae.safetensors"

${H3_SAM2_PYTHON:-/root/autodl-tmp/h3-precision-tools/sam2-env/bin/python} \
  -c "import cv2, sam2, transformers; print('Florence-2 与 SAM2 正常')"
```

如果网页显示 ComfyUI 未连接，先查看 `logs/comfyui.log`。如果提示找不到 `WanVaceToVideo`，当前 ComfyUI 版本不支持 VACE，需要按第 3.2 节安装独立的最新版 ComfyUI。
