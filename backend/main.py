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

            # Verify tesseract binary is reachable
            tesseract_path = os.getenv("TESSERACT_CMD") or "tesseract"
            pytesseract.pytesseract.tesseract_cmd = tesseract_path
            version = pytesseract.get_tesseract_version()
            langs = pytesseract.get_languages()
            notes.append(f"Tesseract {version} 已就绪，可用语言：{', '.join(langs)}")
            ocr_enabled = True
        except Exception as exc:
            notes.append(f"OCR 初始化失败：{exc}")
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
        
        # 过滤空文本和极短文本（单字符或两个字符的文本容易误识别）
        if not text or len(text) <= 2:
            continue
        
        # 提高置信度阈值，过滤低置信度结果
        confidence = float(data["conf"][i])
        if confidence < 0.5:
            continue
            
        # 过滤纯数字或特殊符号（可能是误识别的噪声）
        if re.match(r"^[\d\s\-_+=\/*<>~`!@#$%^&()[\]{}|\\;:,.?]*$", text):
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
    from difflib import SequenceMatcher

    design_items = _ocr_items(design)
    developed_items = _ocr_items(developed)
    issues: List[Issue] = []

    def _fuzzy_ratio(a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio()

    def _center_dist(a: BBox, b: BBox) -> float:
        """Mahattan distance between box centers."""
        ax = a.x + a.width / 2
        ay = a.y + a.height / 2
        bx = b.x + b.width / 2
        by = b.y + b.height / 2
        return abs(ax - bx) + abs(ay - by)

    # Position-first matching: for each design item, find the closest developed item by position
    matched_dev_indices: set = set()
    for d_item in design_items:
        d_box = d_item["bbox"]
        best_dist = float("inf")
        best_dev_idx = -1
        for idx, dev_item in enumerate(developed_items):
            if idx in matched_dev_indices:
                continue
            dist = _center_dist(d_box, dev_item["bbox"])
            if dist < best_dist:
                best_dist = dist
                best_dev_idx = idx

        # Max spatial distance: 2x the larger text height (same-line tolerance) or 80px
        font_h = max(d_box.height, 14)
        max_dist = max(font_h * 3, 60)

        if best_dev_idx < 0 or best_dist > max_dist:
            # No position match — truly missing
            if len(d_item["normalized"]) >= 3:
                issues.append(
                    Issue(
                        id=f"TXT-{start_index + len(issues):03d}",
                        type="文本问题",
                        severity="中",
                        bbox=d_box,
                        description=f"开发页面疑似缺少文本“{d_item['text']}”。",
                        designObservation=f"设计稿包含文本“{d_item['text']}”（附近未找到对应的开发文本）。",
                        developedObservation="开发页面该位置附近无文本匹配。",
                        suggestion="检查文案是否漏开发、被遮挡，或截图状态是否一致。",
                        confidence=round(d_item["confidence"], 2),
                    )
                )
            continue

        matched_dev_indices.add(best_dev_idx)
        dev_item = developed_items[best_dev_idx]
        dev_box = dev_item["bbox"]
        text_similarity = _fuzzy_ratio(d_item["normalized"], dev_item["normalized"])

        # Only report text mismatch if content really differs (not just OCR noise)
        if text_similarity < 0.55 and len(d_item["normalized"]) >= 3 and len(dev_item["normalized"]) >= 3:
            issues.append(
                Issue(
                    id=f"TXT-{start_index + len(issues):03d}",
                    type="文本问题",
                    severity="低",
                    bbox=dev_box,
                    description=f"同位置文本内容不一致：设计稿“{d_item['text']}” vs 开发“{dev_item['text']}”。",
                    designObservation=f"设计稿此位置文本为“{d_item['text']}”。",
                    developedObservation=f"开发页面同位置文本为“{dev_item['text']}”。",
                    suggestion="检查文案内容是否写错或 OCR 识别偏差。",
                    confidence=round(1 - text_similarity, 2),
                )
            )

    # Report developed items with no spatial match in design (extra elements)
    for idx, dev_item in enumerate(developed_items):
        if idx in matched_dev_indices:
            continue
        dev_box = dev_item["bbox"]
        # Check if any design item is spatially close
        too_close = any(
            _center_dist(dev_box, d["bbox"]) < max(d["bbox"].height * 3, 60)
            for d in design_items
        )
        if too_close or len(dev_item["normalized"]) < 3:
            continue
        issues.append(
            Issue(
                id=f"TXT-{start_index + len(issues):03d}",
                type="多余元素",
                severity="低",
                bbox=dev_box,
                description=f"开发页面疑似多出文本“{dev_item['text']}”。",
                designObservation="设计稿该位置附近未找到对应文本。",
                developedObservation=f"开发页面包含文本“{dev_item['text']}”。",
                suggestion="检查是否多开发了文案，或确认设计稿与开发页面是否为同一状态。",
                confidence=round(dev_item["confidence"], 2),
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


@app.post("/api/audits")
async def create_audit(design_image: UploadFile = File(...), developed_image: UploadFile = File(...)):
    import traceback

    try:
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
                capabilities.notes.append(traceback.format_exc()[-300:])
        if capabilities.gptEnabled:
            try:
                issues, ai_note = _enhance_issues_with_ai(issues, capabilities)
                capabilities.notes.append(ai_note)
            except Exception as exc:
                capabilities.notes.append(f"AI 增强失败：{exc}")
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
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")


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


@app.get("/")
async def serve_root():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/{full_path:path}")
async def serve_frontend(full_path: str):
    file_path = FRONTEND_DIR / full_path
    if file_path.exists() and file_path.is_file():
        return FileResponse(file_path)
    return FileResponse(FRONTEND_DIR / "index.html")