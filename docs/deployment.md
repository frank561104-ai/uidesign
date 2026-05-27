# uidesign 云部署步骤

目标：前端部署到 Vercel，后端部署到 Python 服务平台，拿到网址的人可以直接访问。

## 1. 后端部署

推荐先用 Render / Railway / Fly.io / 国内云服务器中的一个部署 `backend`。

后端启动命令：

```bash
python3 -m uvicorn backend.main:app --host 0.0.0.0 --port $PORT
```

后端环境变量：

```bash
UIDESIGN_CORS_ORIGINS=https://your-frontend-domain.vercel.app
UIDESIGN_ENABLE_OCR=false
UIDESIGN_ENABLE_GPT=false
OPENAI_API_KEY=your_api_key_here
OPENAI_MODEL=gpt-4o
```

如果暂时不用 GPT-4o，可以不配置真实 `OPENAI_API_KEY`，并保持 `UIDESIGN_ENABLE_GPT=false`。

部署完成后，用这个地址检查后端是否可用：

```text
https://your-backend-domain/api/health
```

看到 `{"status":"ok"}` 表示后端已启动。

## 2. 前端部署到 Vercel

Vercel 项目设置：

```text
Root Directory: frontend
Build Command: npm run build
Output Directory: dist
```

前端环境变量：

```bash
VITE_API_BASE_URL=https://your-backend-domain
```

注意：这里不要带 `/api`，代码里会自动请求 `/api/audits`。

## 3. 回填 CORS

前端部署完成后，把 Vercel 给你的正式域名填回后端：

```bash
UIDESIGN_CORS_ORIGINS=https://your-project.vercel.app
```

如果同时有自定义域名，可以用英文逗号分隔：

```bash
UIDESIGN_CORS_ORIGINS=https://your-project.vercel.app,https://www.your-domain.com
```

## 4. 验证

打开前端网址，上传设计稿截图和开发页面截图。

如果上传失败，优先检查：

- 前端 `VITE_API_BASE_URL` 是否是后端根地址。
- 后端 `UIDESIGN_CORS_ORIGINS` 是否包含当前前端网址。
- 后端日志里是否有图片格式、大小、OpenCV 或 GPT-4o 调用错误。

