# MiniMax H3 视频替换工作台

这是一个部署在 AutoDL ComfyUI 实例上的 MiniMax-H3 Ref2VA 客户端。浏览器负责上传视频、参考图片和 Excel，FastAPI 保存任务并按顺序提交给本机 ComfyUI。网页不设置登录或访问令牌，访问地址后即可使用。

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

## AutoDL 实例要求

创建实例时选择带有新版 ComfyUI 的应用镜像，ComfyUI 版本需要为 0.30.0 或更高。建议至少使用 24GB 显存 GPU，并准备足够的主机内存；显存和内存越小，生成越慢。

本项目使用 AutoDL 公共模型，不需要重新下载完整的 Hugging Face 模型。需要在 AutoDL“公共模型”中搜索以下四个文件：

| 类型 | 公共模型文件名 | ComfyUI 目录 |
| --- | --- | --- |
| Ref2VA 扩散模型 | `minimax_h3_ref2va_pruned_int8_convrot.safetensors` | `models/diffusion_models` |
| 文本编码器 | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | `models/text_encoders` |
| 视频 VAE | `minimax_h3_video_vae_int8_convrot.safetensors` | `models/vae` |
| 音频 VAE | `minimax_h3_audio_vae_fp32.safetensors` | `models/vae` |

公共模型页面会给出一个以 `/.autodl/` 开头的源路径。应用通过软链接直接读取这些文件，不复制模型权重。

## 上传项目

可以通过 AutoDL JupyterLab 上传项目目录，也可以在 Mac 终端执行：

```bash
rsync -av --exclude '.venv' --exclude 'data' --exclude '.git' \
  -e 'ssh -p 你的SSH端口' \
  /Users/linyongjia/Desktop/comfyui-video-studio/ \
  root@你的AutoDL主机:/root/autodl-tmp/comfyui-video-studio/
```

## 挂载公共模型

从公共模型页面复制上述四个文件的源路径，然后在 AutoDL 终端执行：

```bash
cd /root/autodl-tmp/comfyui-video-studio
chmod +x scripts/*.sh

scripts/link_autodl_models.sh \
  '/.autodl/Ref2VA文件对应路径' \
  '/.autodl/文本编码器对应路径' \
  '/.autodl/视频VAE对应路径' \
  '/.autodl/音频VAE对应路径'
```

脚本只创建软链接。如果 AutoDL 页面已经给出了 `ln -s` 命令，也可以直接执行页面提供的命令，但目标文件名和目录必须与上表一致。

## 安装并启动

```bash
cd /root/autodl-tmp/comfyui-video-studio
scripts/install_autodl.sh
cp .env.example .env
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

返回 `engine: ok` 表示网页已连接 ComfyUI。网页监听 6006，ComfyUI 仅在服务器内部监听 8188，不需要开放 8188。

在 AutoDL 控制台打开 6006 端口的“自定义服务”地址。访问者打开该地址即可上传素材和创建任务，不需要输入令牌。所有访问者共用同一个任务队列。

停止服务：

```bash
scripts/stop_all.sh
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
H3_COMFYUI_URL="http://127.0.0.1:8188" \
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
- ComfyUI 内部接口：`127.0.0.1:8188`
- 队列数据库：`$H3_STUDIO_DATA_DIR/studio.db`
- 生成结果：`$H3_STUDIO_DATA_DIR/outputs`
- ComfyUI 工作流：`workflows/minimax_h3_ref2va_api.json`

应用保持一个队列 worker，Uvicorn 必须使用 `--workers 1`。任务素材提交给 ComfyUI 后会从 ComfyUI input 临时目录清理，原始上传文件仍保留在工作台数据目录中，以便失败后重试。
