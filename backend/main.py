import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from uuid import uuid4

import cv2
import numpy as np
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


MAX_IMAGE_BYTES = 20 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/jpg"}
ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg"}
DATA_DIR = Path(__file__).resolve().parent / ".data" / "audits"

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _cors_origins() -> List[str]:
    configured = os.getenv("UIDESIGN_CORS_ORIGINS", "")
    origins = [origin.strip() for origin in configured.split(",") if origin.strip()]
    return origins or ["http://localhost:5173", "http://127.0.0.1:5173"]


class BBox(BaseModel):
    x: int
    y: int
    width: int
    height: int


class ImageInfo(BaseModel):
    filename: str
    width: int
    height: int
    contentType: str


class Capabilities(BaseModel):
    opencvEnabled: bool = True
    ocrEnabled: bool = False
    gptEnabled: bool = False
    aiProvider: str = "local"
    aiModel: str = "local-rules"
    notes: List[str] = Field(default_factory=list)


class Score(BaseModel):
    total: int
    dimensions: Dict[str, int]
    deductions: List[str]


class Issue(BaseModel):
    id: str
    type: str
    severity: str
    bbox: BBox
    description: str
    designObservation: str
    developedObservation: str
    suggestion: str
    confidence: float
    reviewStatus: str = "需后续确认"
    note: str = ""


class AuditResult(BaseModel):
    id: str
    createdAt: str
    designImage: ImageInfo
    developedImage: ImageInfo
    preprocessing: List[str]
    capabilities: Capabilities
    score: Score
    issues: List[Issue]


class IssuePatch(BaseModel):
    reviewStatus: str
    note: Optional[str] = ""


app = FastAPI(title="uidesign API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

AUDITS: Dict[str, AuditResult] = {}
OCR_ENGINE = None


@app.get("/api/health")
def health_check():
    return {"status": "ok"}


def _capabilities() -> Capabilities:
    notes = []
    ocr_enabled = False
    ai_enabled = False
    ai_provider = os.getenv("AI_PROVIDER", "openai").strip().lower()
    if ai_provider not in {"openai", "deepseek"}:
        ai_provider = "openai"
    ai_model = os.getenv("AI_MODEL", "deepseek-v4-flash" if ai_provider == "deepseek" else "gpt-4o")

    if os.getenv("UIDESIGN_ENABLE_OCR") == "true":
        try:
            import pytesseract  # noqa: F401
            from PIL import Image  # noqa: F401

            ocr_enabled = True
        except Exception:
            notes.append("pytesseract 未安装或不可用，已跳过文字识别。")
    else:
        notes.append("OCR 未启用：设置 UIDESIGN_ENABLE_OCR=true 并安装 pytesseract 后可使用。")

    wants_ai = os.getenv("UIDESIGN_ENABLE_AI") == "true" or os.getenv("UIDESIGN_ENABLE_GPT") == "true"
    api_key = _api_key_for_provider(ai_provider)
    if wants_ai and api_key:
        try:
            import openai  # noqa: F401

            ai_enabled = True
        except Exception:
            notes.append("OpenAI SDK 未安装，已使用本地规则生成问题描述。")
    else:
        notes.append("AI 描述未启用：默认只用本地结构化结果生成描述，不上传原始截图。")

    return Capabilities(
        ocrEnabled=ocr_enabled,
        gptEnabled=ai_enabled,
        aiProvider=ai_provider if ai_enabled else "local",
        aiModel=ai_model if ai_enabled else "local-rules",
        notes=notes,
    )


def _api_key_for_provider(provider: str) -> Optional[str]:
    if provider == "deepseek":
        return os.getenv("DEEPSEEK_API_KEY") or os.getenv("AI_API_KEY")
    return os.getenv("OPENAI_API_KEY") or os.getenv("AI_API_KEY")


def _base_url_for_provider(provider: str) -> Optional[str]:
    if os.getenv("AI_BASE_URL"):
        return os.getenv("AI_BASE_URL")
    if provider == "deepseek":
        return "https://api.deepseek.com"
    return None


async def _read_image(upload: UploadFile) -> bytes:
    suffix = Path(upload.filename or "").suffix.lower()
    if upload.content_type not in ALLOWED_CONTENT_TYPES or suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail="仅支持 PNG、JPG、JPEG 格式的截图。")

    content = await upload.read()
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="单张图片不能超过 20MB。")
    if not content:
        raise HTTPException(status_code=400, detail="图片内容为空，请重新上传。")
    return content


def _decode_image(content: bytes) -> np.ndarray:
    buffer = np.frombuffer(content, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="图片损坏或无法读取，请重新上传。")
    return image


def _image_info(upload: UploadFile, image: np.ndarray) -> ImageInfo:
    height, width = image.shape[:2]
    return ImageInfo(
        filename=upload.filename or "image",
        width=width,
        height=height,
        contentType=upload.content_type or "application/octet-stream",
    )


def _detect_visual_issues(design: np.ndarray, developed: np.ndarray) -> tuple:
    dev_height, dev_width = developed.shape[:2]
    preprocessing = [
        "读取两张截图并记录原始尺寸。",
        "将设计稿截图缩放到开发页面截图尺寸后进行视觉对比。",
    ]
    aligned_design = cv2.resize(design, (dev_width, dev_height), interpolation=cv2.INTER_AREA)
    diff = cv2.absdiff(aligned_design, developed)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    _, threshold = cv2.threshold(blurred, 28, 255, cv2.THRESH_BINARY)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    closed = cv2.morphologyEx(threshold, cv2.MORPH_CLOSE, kernel, iterations=2)
    closed = cv2.dilate(closed, kernel, iterations=1)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    page_area = dev_width * dev_height
    min_area = max(80, int(page_area * 0.001))
    issues: List[Issue] = []
    for index, contour in enumerate(sorted(contours, key=cv2.contourArea, reverse=True)[:30], start=1):
        x, y, width, height = cv2.boundingRect(contour)
        area = width * height
        if area < min_area:
            continue

        severity = _severity_for_area(area, page_area)
        issue_type = _type_for_region(width, height, dev_width, dev_height)
        confidence = min(0.96, max(0.58, float(area) / float(page_area) * 12))
        region_diff = float(np.mean(gray[y : y + height, x : x + width]))
        issues.append(
            Issue(
                id=f"VIS-{len(issues) + 1:03d}",
                type=issue_type,
                severity=severity,
                bbox=BBox(x=x, y=y, width=width, height=height),
                description=_description_for_issue(issue_type, severity, region_diff),
                designObservation="该区域在设计稿中与开发页面存在可见差异。",
                developedObservation=f"开发页面在 x={x}, y={y}, 宽 {width}px, 高 {height}px 的区域变化明显。",
                suggestion=_suggestion_for_type(issue_type),
                confidence=round(confidence, 2),
            )
        )

    if not issues:
        preprocessing.append("未检测到超过阈值的主要视觉差异。")
    else:
        preprocessing.append(f"过滤轻微噪点后，检测到 {len(issues)} 个主要视觉差异区域。")
    return aligned_design, issues, preprocessing


def _severity_for_area(area: int, page_area: int) -> str:
    ratio = area / page_area
    if ratio >= 0.12:
        return "高"
    if ratio >= 0.035:
        return "中"
    return "低"


def _type_for_region(width: int, height: int, page_width: int, page_height: int) -> str:
    if width > page_width * 0.65 or height > page_height * 0.35:
        return "结构布局问题"
    if abs(width - height) < 18 and max(width, height) < 120:
        return "图片 / 图标问题"
    return "颜色问题"


def _description_for_issue(issue_type: str, severity: str, diff_strength: float) -> str:
    certainty = "明显" if diff_strength >= 40 else "疑似"
    return f"{certainty}的{issue_type}，建议按{severity}优先级复核。"


def _suggestion_for_type(issue_type: str) -> str:
    suggestions = {
        "结构布局问题": "检查模块是否缺失、多出、错位，优先确认页面状态是否一致。",
        "图片 / 图标问题": "检查图片、图标或按钮形态是否与设计稿一致。",
        "颜色问题": "检查背景色、文字色、边框色或组件状态色是否还原。",
    }
    return suggestions.get(issue_type, "结合标注区域复核视觉还原细节。")


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "").strip().lower()


def _ocr_items(image: np.ndarray) -> List[dict]:
    import pytesseract
    from PIL import Image

    image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    pil_image = Image.fromarray(image_rgb)
    data = pytesseract.image_to_data(pil_image, output_type=pytesseract.Output.DICT, lang="chi_sim+eng")
    
    items = []
    num_boxes = len(data["text"])
    for i in range(num_boxes):
        text = data["text"][i].strip()
        if not text:
            continue
        
        confidence = float(data["confidence"][i])
        if confidence < 0.1:
            continue
            
        x = data["left"][i]
        y = data["top"][i]
        width = data["width"][i]
        height = data["height"][i]
        
        items.append(
            {
                "text": text,
                "normalized": _normalize_text(text),
                "confidence": confidence,
                "bbox": BBox(x=x, y=y, width=width, height=height),
            }
        )
    return items


def _detect_text_issues(design: np.ndarray, developed: np.ndarray, start_index: int) -> tuple:
    design_items = _ocr_items(design)
    developed_items = _ocr_items(developed)
    design_by_text = {item["normalized"]: item for item in design_items}
    developed_by_text = {item["normalized"]: item for item in developed_items}
    issues: List[Issue] = []

    for normalized, item in design_by_text.items():
        if normalized not in developed_by_text:
            box = item["bbox"]
            issues.append(
                Issue(
                    id=f"TXT-{start_index + len(issues):03d}",
                    type="文本问题",
                    severity="中",
                    bbox=box,
                    description=f"开发页面疑似缺少文本“{item['text']}”。",
                    designObservation=f"设计稿包含文本“{item['text']}”。",
                    developedObservation="开发页面 OCR 结果中未找到对应文本。",
                    suggestion="检查文案是否漏开发、被遮挡，或截图状态是否一致。",
                    confidence=round(item["confidence"], 2),
                )
            )

    for normalized, item in developed_by_text.items():
        if normalized not in design_by_text:
            box = item["bbox"]
            issues.append(
                Issue(
                    id=f"TXT-{start_index + len(issues):03d}",
                    type="多余元素",
                    severity="低",
                    bbox=box,
                    description=f"开发页面疑似多出文本“{item['text']}”。",
                    designObservation="设计稿 OCR 结果中未找到对应文本。",
                    developedObservation=f"开发页面包含文本“{item['text']}”。",
                    suggestion="检查是否多开发了文案，或确认设计稿与开发页面是否为同一状态。",
                    confidence=round(item["confidence"], 2),
                )
            )

    for normalized, design_item in design_by_text.items():
        developed_item = developed_by_text.get(normalized)
        if not developed_item:
            continue
        design_box = design_item["bbox"]
        developed_box = developed_item["bbox"]
        offset = abs(design_box.x - developed_box.x) + abs(design_box.y - developed_box.y)
        if offset > 24:
            issues.append(
                Issue(
                    id=f"TXT-{start_index + len(issues):03d}",
                    type="文本问题",
                    severity="低",
                    bbox=developed_box,
                    description=f"文本“{developed_item['text']}”位置疑似偏移。",
                    designObservation=f"设计稿文本位置约为 x={design_box.x}, y={design_box.y}。",
                    developedObservation=f"开发页面文本位置约为 x={developed_box.x}, y={developed_box.y}。",
                    suggestion="检查文字所在组件的间距、对齐和换行设置。",
                    confidence=round(min(design_item["confidence"], developed_item["confidence"]), 2),
                )
            )

    return issues, f"OCR 已识别设计稿 {len(design_items)} 处文本、开发页面 {len(developed_items)} 处文本。"


def _score_for_issues(issues: List[Issue]) -> Score:
    weights = {"高": 18, "中": 9, "低": 4}
    deductions = [f"{issue.id} {issue.type}（{issue.severity}）扣 {weights[issue.severity]} 分" for issue in issues]
    total = max(0, 100 - sum(weights[issue.severity] for issue in issues))
    dimensions = {
        "文本一致性": max(0, 100 - sum(10 for issue in issues if issue.type in {"文本问题", "多余元素"})),
        "颜色一致性": max(0, 100 - sum(6 for issue in issues if issue.type == "颜色问题")),
        "布局一致性": max(0, 100 - sum(8 for issue in issues if issue.type == "结构布局问题")),
        "间距一致性": max(0, 100 - sum(5 for issue in issues if issue.type in {"结构布局问题", "颜色问题"})),
        "组件完整性": max(0, 100 - sum(8 for issue in issues if issue.type == "图片 / 图标问题")),
        "图片图标一致性": max(0, 100 - sum(8 for issue in issues if issue.type == "图片 / 图标问题")),
    }
    return Score(total=total, dimensions=dimensions, deductions=deductions)


def _enhance_issues_with_ai(issues: List[Issue], capabilities: Capabilities) -> tuple:
    if not issues:
        return issues, "没有检测到问题，未调用 AI 描述模型。"
    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=_api_key_for_provider(capabilities.aiProvider),
            base_url=_base_url_for_provider(capabilities.aiProvider),
        )
        model = capabilities.aiModel
        issue_payload = [
            {
                "id": issue.id,
                "type": issue.type,
                "severity": issue.severity,
                "bbox": issue.bbox.model_dump(),
                "description": issue.description,
                "designObservation": issue.designObservation,
                "developedObservation": issue.developedObservation,
                "suggestion": issue.suggestion,
                "confidence": issue.confidence,
            }
            for issue in issues
        ]
        response = client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是设计还原度走查助手。只根据用户输入的结构化检测结果生成更清楚的问题描述，"
                        "不要声称你看过原始截图。必须输出 JSON 数组，每项包含 id、description、suggestion。"
                    ),
                },
                {"role": "user", "content": json.dumps(issue_payload, ensure_ascii=False)},
            ],
        )
        raw_text = response.choices[0].message.content or ""
        enhanced = json.loads(raw_text)
        enhanced_by_id = {item["id"]: item for item in enhanced if "id" in item}
        for issue in issues:
            item = enhanced_by_id.get(issue.id)
            if not item:
                continue
            issue.description = item.get("description") or issue.description
            issue.suggestion = item.get("suggestion") or issue.suggestion
        return issues, f"{capabilities.aiProvider} 已基于结构化检测结果优化 {len(enhanced_by_id)} 条问题描述。"
    except Exception as exc:
        return issues, f"AI 描述模型调用失败，已保留本地规则描述：{exc}"


def _annotate_image(developed: np.ndarray, issues: List[Issue]) -> bytes:
    output = developed.copy()
    colors = {"高": (34, 34, 220), "中": (25, 138, 245), "低": (40, 180, 100)}
    for issue in issues:
        color = colors.get(issue.severity, (25, 138, 245))
        box = issue.bbox
        cv2.rectangle(output, (box.x, box.y), (box.x + box.width, box.y + box.height), color, 2)
        cv2.putText(
            output,
            issue.id,
            (box.x, max(18, box.y - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            color,
            2,
            cv2.LINE_AA,
        )
    success, encoded = cv2.imencode(".png", output)
    if not success:
        raise HTTPException(status_code=500, detail="标注图生成失败。")
    return encoded.tobytes()


def _report_for_audit(audit: AuditResult) -> str:
    lines = [
        "# uidesign 设计还原度走查报告",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 任务编号：{audit.id}",
        f"- 设计稿：{audit.designImage.filename}（{audit.designImage.width}x{audit.designImage.height}）",
        f"- 开发页面：{audit.developedImage.filename}（{audit.developedImage.width}x{audit.developedImage.height}）",
        f"- 还原度总分：{audit.score.total}/100",
        "",
        "## 能力状态",
        "",
        f"- OpenCV：{'已启用' if audit.capabilities.opencvEnabled else '未启用'}",
        f"- OCR：{'已启用' if audit.capabilities.ocrEnabled else '未启用'}",
        f"- AI 描述：{'已启用' if audit.capabilities.gptEnabled else '未启用'}",
        f"- AI 提供商：{audit.capabilities.aiProvider}",
        f"- AI 模型：{audit.capabilities.aiModel}",
        "",
        "## 预处理说明",
        "",
    ]
    lines.extend([f"- {item}" for item in audit.preprocessing])
    lines.extend(["", "## 评分维度", ""])
    lines.extend([f"- {name}：{score}" for name, score in audit.score.dimensions.items()])
    lines.extend(["", "## 问题列表", ""])
    if not audit.issues:
        lines.append("- 未检测到主要差异。")
    for issue in audit.issues:
        box = issue.bbox
        lines.extend(
            [
                f"### {issue.id} {issue.type}",
                "",
                f"- 严重程度：{issue.severity}",
                f"- 置信度：{issue.confidence}",
                f"- 位置：x={box.x}, y={box.y}, width={box.width}, height={box.height}",
                f"- 问题描述：{issue.description}",
                f"- 设计稿表现：{issue.designObservation}",
                f"- 开发页面表现：{issue.developedObservation}",
                f"- 建议修复方向：{issue.suggestion}",
                f"- 复核状态：{issue.reviewStatus}",
                f"- 备注：{issue.note or '无'}",
                "",
            ]
        )
    return "\n".join(lines)


@app.post("/api/audits", response_model=AuditResult)
async def create_audit(design_image: UploadFile = File(...), developed_image: UploadFile = File(...)):
    design_content = await _read_image(design_image)
    developed_content = await _read_image(developed_image)
    design = _decode_image(design_content)
    developed = _decode_image(developed_content)

    _, issues, preprocessing = _detect_visual_issues(design, developed)
    capabilities = _capabilities()
    if capabilities.ocrEnabled:
        try:
            text_issues, ocr_note = _detect_text_issues(design, developed, len(issues) + 1)
            issues.extend(text_issues)
            capabilities.notes.append(ocr_note)
        except Exception as exc:
            capabilities.ocrEnabled = False
            capabilities.notes.append(f"OCR 执行失败，已跳过文字差异检测：{exc}")
    if capabilities.gptEnabled:
        issues, ai_note = _enhance_issues_with_ai(issues, capabilities)
        capabilities.notes.append(ai_note)
    audit_id = uuid4().hex
    audit = AuditResult(
        id=audit_id,
        createdAt=datetime.now().isoformat(timespec="seconds"),
        designImage=_image_info(design_image, design),
        developedImage=_image_info(developed_image, developed),
        preprocessing=preprocessing,
        capabilities=capabilities,
        score=_score_for_issues(issues),
        issues=issues,
    )
    AUDITS[audit_id] = audit
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / f"{audit_id}.png").write_bytes(_annotate_image(developed, issues))
    return audit


@app.get("/api/audits/{audit_id}/annotated-image")
def get_annotated_image(audit_id: str):
    image_path = DATA_DIR / f"{audit_id}.png"
    if audit_id not in AUDITS or not image_path.exists():
        raise HTTPException(status_code=404, detail="未找到该走查任务。")
    return Response(content=image_path.read_bytes(), media_type="image/png")


@app.patch("/api/audits/{audit_id}/issues/{issue_id}", response_model=Issue)
def update_issue(audit_id: str, issue_id: str, patch: IssuePatch):
    audit = AUDITS.get(audit_id)
    if audit is None:
        raise HTTPException(status_code=404, detail="未找到该走查任务。")
    for issue in audit.issues:
        if issue.id == issue_id:
            issue.reviewStatus = patch.reviewStatus
            issue.note = patch.note or ""
            return issue
    raise HTTPException(status_code=404, detail="未找到该问题。")


@app.get("/api/audits/{audit_id}/report.md")
def get_markdown_report(audit_id: str):
    audit = AUDITS.get(audit_id)
    if audit is None:
        raise HTTPException(status_code=404, detail="未找到该走查任务。")
    return Response(content=_report_for_audit(audit), media_type="text/markdown; charset=utf-8")


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"


@app.get("/{full_path:path}")
async def serve_frontend(full_path: str):
    file_path = FRONTEND_DIR / full_path
    if file_path.exists() and file_path.is_file():
        return FileResponse(file_path)
    return FileResponse(FRONTEND_DIR / "index.html")
