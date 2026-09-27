# ComfyUI 工作流

`wan_vace_inpaint_api.json` 是供 ComfyUI `/prompt` 接口使用的 API 工作流。应用会把原视频、SAM2 逐帧商品蒙版和商品参考图传入 Wan2.1 VACE；蒙版内先填中性灰，再生成商品并合成回原视频。默认使用 50 步采样和 VACE 的 16.0 采样偏移。提交前会设置提示词、尺寸、帧数和种子。

参考官方工作流：https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_wan_vace_inpainting.json
