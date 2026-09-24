# MiniMax H3 视频替换工作台

这是一个可部署到 AutoDL 的单机应用：浏览器负责单任务或 Excel 批量上传，FastAPI 将任务持久化到 SQLite 并按顺序执行，SGLang 在本机运行 MiniMax-H3 Ref2VA。页面支持查看进度、下载结果、重试以及删除任务。

## 能做什么

- 使用源视频作为动作、镜头、场景和音频参考。
- 使用人物图替换主要人物，使用商品图替换主要商品；两种参考可同时提供。
- Excel 一次创建多个任务，并同时上传表格引用的视频和图片。
- 任务在 SQLite 中持久化。网页刷新或应用重启后，未完成任务会重新排队。
- 删除排队任务；删除生成中任务时会请求 SGLang 取消，并立即从列表隐藏。
- 可设置访问令牌，适合通过 AutoDL 6006 自定义服务访问。

MiniMax-H3 Ref2VA 是参考驱动的重生成模型。它可能重新组织动作与镜头，不能保证逐帧保留原视频，也不是传统意义上的像素级换脸或商品贴片。源素材应短而明确，建议先测试 4–5 秒。

## AutoDL 机器选择

官方模型是 33B，并含 Qwen3-VL-32B 文本/视觉编码器。可按预算选择：

| 配置 | 适用情况 | 说明 |
| --- | --- | --- |
| 4 × H100 80GB | 稳定生产与较高吞吐 | 官方验证的常驻权重拓扑；本项目脚本使用 TP2 + Ulysses2。 |
| 2 × RTX 5090 32GB，约 384GB 内存 | 较低成本、仍需较好速度 | 官方验证过双卡分层卸载。 |
| 1 × RTX 4090/5090，24GB 以上显存，建议 192–256GB 内存 | 功能验证 | 使用 INT8 与 CPU 分层卸载，能跑但会明显慢，主机内存不足会失败。 |

数据盘建议至少 350GB；模型缓存、上传源视频和生成结果都会占空间。选择 CUDA 12.4 或更新的 PyTorch 镜像。若 AutoDL 实例的内存较小，优先升级内存或改用多卡高显存机器。

## 部署

将本项目上传到实例的数据盘，例如 `/root/autodl-tmp/comfyui-video-studio`，然后执行：

```bash
cd /root/autodl-tmp/comfyui-video-studio
chmod +x scripts/*.sh
scripts/install_autodl.sh
cp .env.example .env
```

编辑 `.env`，至少修改 `H3_STUDIO_TOKEN`。建议使用随机长字符串：

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

启动模型与网页：

```bash
scripts/start_all.sh
```

查看启动状态：

```bash
tail -f logs/h3.log
tail -f logs/app.log
```

H3 首次启动会从 Hugging Face 下载模型。模型服务正常后，访问本机 `http://127.0.0.1:6006/health` 应看到 `engine: ok`。

在 AutoDL 控制台打开“自定义服务”，选择 6006 端口对应的 HTTP 地址。个人账号无法直接开放端口时，可在自己的电脑上建立 SSH 隧道：

```bash
ssh -CNg -L 6006:127.0.0.1:6006 root@你的AutoDL主机 -p SSH端口
```

然后打开 `http://127.0.0.1:6006`。

停止服务：

```bash
scripts/stop_all.sh
```

## Excel 批量格式

在页面点击“下载 Excel 模板”。`任务`工作表每行一个任务，字段如下：

| 列 | 必填 | 含义 |
| --- | --- | --- |
| `task_name` | 否 | 任务名称 |
| `source_video` | 是 | 源视频文件名 |
| `character_image` | 否 | 人物参考图文件名 |
| `product_image` | 否 | 商品参考图文件名 |
| `prompt` | 否 | 补充生成要求 |
| `duration` | 否 | 4–15 秒，默认 5 |
| `aspect_ratio` | 否 | `auto`、`16:9`、`9:16`、`1:1`、`4:3`、`3:4` 或 `21:9` |
| `source_start` | 否 | 从源视频第几秒开始，默认 0 |
| `seed` | 否 | 固定随机种子；空白时自动生成 |

导入时同时选择表格中提到的素材文件，服务端按文件名匹配。文件名不能重复。若素材已提前放到服务器，可在 `.env` 设置 `H3_SERVER_ASSET_ROOT`，Excel 则可引用该目录内的相对路径。

## 接口与数据

- 网页和 API：6006
- SGLang Ref2VA：仅监听 `127.0.0.1:30011`
- 数据目录：`H3_STUDIO_DATA_DIR`
- 队列数据库：`$H3_STUDIO_DATA_DIR/studio.db`
- 成片：`$H3_STUDIO_DATA_DIR/outputs`

应用只启动一个队列 worker。请保持 Uvicorn 的 `--workers 1`，否则多进程会同时抢占同一 GPU。任务删除使用软删除记录，输出文件会删除；原始上传素材暂时保留，便于数据库审计和批量任务共享。可按需要定期清理已删除任务对应的上传目录。

## 使用要求

部署和商用前请阅读 MiniMax-H3 Community License。只处理你有权使用的人物、商品、音频和视频素材，并取得人物肖像及品牌素材的必要授权。AutoDL 自定义服务条款将其定位为科研用途，并要求链接仅供账户本人使用；若用于面向客户的正式产品，应选择允许相应用途的云部署方案。

