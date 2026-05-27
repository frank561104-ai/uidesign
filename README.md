# uidesign

`uidesign` 是一个本地优先的设计还原度走查工具。第一版支持上传设计稿截图和开发页面截图，用 OpenCV 做基础视觉差异检测，生成差异标注图、问题列表、还原度评分和 Markdown 报告。

## 运行方式

安装后端依赖：

```bash
python3 -m pip install -r backend/requirements.txt
```

启动后端：

```bash
python3 -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

安装前端依赖：

```bash
cd frontend
npm install
```

启动前端：

```bash
npm run dev
```

打开 Vite 输出的本地地址，通常是 `http://127.0.0.1:5173`。

## 配置

复制 `.env.example` 为 `.env` 后按需修改。不要把真实密钥提交到仓库。

OCR 默认关闭。需要启用时安装 PaddleOCR，并设置：

```bash
UIDESIGN_ENABLE_OCR=true
```

GPT-4o 默认关闭，且不会接收完整截图。需要启用时安装 OpenAI Python SDK，并设置：

```bash
python3 -m pip install openai
UIDESIGN_ENABLE_GPT=true
OPENAI_API_KEY=your_api_key_here
OPENAI_MODEL=gpt-4o
```

启用后，后端只会把 OpenCV / OCR 产生的结构化问题发送给模型，用来优化问题描述和修复建议，不会发送原始设计稿或页面截图。

## 当前能力

- 支持 PNG、JPG、JPEG，单张图片限制 20MB。
- 自动把设计稿截图缩放到开发页面截图尺寸后对比。
- 使用 OpenCV 找出主要视觉差异区域，并在开发页面截图上画框。
- 输出结构化问题：问题类型、严重程度、位置、描述、建议、置信度、复核状态。
- 支持在前端标记问题为“正确”。
- 支持下载 Markdown 报告；PDF 可通过报告页或浏览器打印保存。

## 当前限制

- 第一版不做账号、历史记录、团队空间、URL 自动截图、Figma 文件解析。
- OpenCV 检测主要能发现明显的颜色、位置、尺寸和结构差异。
- OCR 和 GPT-4o 是可选能力，未配置时主流程仍可完成。
- 评分是基于问题数量和严重程度的基础规则，不代表最终上线验收结论。

## 可能误判

- 两张截图不是同一页面状态。
- 滚动位置不同。
- 图片尺寸或裁切差异过大。
- 字体抗锯齿、阴影、渐变、透明度造成轻微像素差。
- 动态内容、时间、头像、测试数据不一致。
- OCR 低置信度识别错误。

遇到这些情况时，建议先复核截图状态，再根据问题列表逐条确认。

## 测试

后端：

```bash
python3 -m pytest backend/tests/test_api.py -q
```

前端：

```bash
cd frontend
npm test
```
