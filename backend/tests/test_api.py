from io import BytesIO
import sys
from types import SimpleNamespace

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

import backend.main as backend_main
from backend.main import app


client = TestClient(app)


def make_png(color=(255, 255, 255), size=(220, 160), mark=False):
    image = Image.new("RGB", size, color)
    if mark:
        draw = ImageDraw.Draw(image)
        draw.rectangle((70, 45, 150, 105), fill=(20, 120, 230))
        draw.text((82, 72), "CTA", fill=(255, 255, 255))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def upload_pair(design=None, developed=None):
    design = design or make_png(mark=False)
    developed = developed or make_png(mark=True)
    return client.post(
        "/api/audits",
        files={
            "design_image": ("design.png", design, "image/png"),
            "developed_image": ("developed.png", developed, "image/png"),
        },
    )


def test_rejects_missing_developed_image():
    response = client.post(
        "/api/audits",
        files={"design_image": ("design.png", make_png(), "image/png")},
    )

    assert response.status_code == 422


def test_rejects_unsupported_image_type():
    response = client.post(
        "/api/audits",
        files={
            "design_image": ("design.gif", BytesIO(b"GIF89a"), "image/gif"),
            "developed_image": ("developed.png", make_png(), "image/png"),
        },
    )

    assert response.status_code == 400
    assert "仅支持 PNG、JPG、JPEG" in response.json()["detail"]


def test_creates_audit_with_visual_issue_and_degraded_capabilities():
    response = upload_pair()

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"]
    assert payload["capabilities"]["opencvEnabled"] is True
    assert payload["capabilities"]["ocrEnabled"] is False
    assert payload["capabilities"]["gptEnabled"] is False
    assert payload["score"]["total"] < 100
    assert payload["issues"]
    issue = payload["issues"][0]
    assert {
        "id",
        "type",
        "severity",
        "bbox",
        "description",
        "designObservation",
        "developedObservation",
        "suggestion",
        "confidence",
        "reviewStatus",
        "note",
    }.issubset(issue.keys())


def test_updates_issue_review_status_and_note():
    audit = upload_pair().json()
    issue_id = audit["issues"][0]["id"]

    response = client.patch(
        f"/api/audits/{audit['id']}/issues/{issue_id}",
        json={"reviewStatus": "误判", "note": "设计稿状态不同"},
    )

    assert response.status_code == 200
    issue = response.json()
    assert issue["reviewStatus"] == "误判"
    assert issue["note"] == "设计稿状态不同"


def test_returns_annotated_image_and_markdown_report():
    audit = upload_pair().json()

    image_response = client.get(f"/api/audits/{audit['id']}/annotated-image")
    assert image_response.status_code == 200
    assert image_response.headers["content-type"] == "image/png"
    assert image_response.content.startswith(b"\x89PNG")

    report_response = client.get(f"/api/audits/{audit['id']}/report.md")
    assert report_response.status_code == 200
    markdown = report_response.text
    assert "# uidesign 设计还原度走查报告" in markdown
    assert "还原度总分" in markdown
    assert audit["issues"][0]["id"] in markdown


def test_enabled_ocr_uses_paddle_without_unsupported_show_log(monkeypatch):
    class FakePaddleOCR:
        def __init__(self, use_angle_cls=True, lang="ch"):
            self.use_angle_cls = use_angle_cls
            self.lang = lang

        def ocr(self, image):
            return [
                [
                    [
                        [[10, 10], [90, 10], [90, 30], [10, 30]],
                        ("测试文本", 0.93),
                    ]
                ]
            ]

    monkeypatch.setenv("UIDESIGN_ENABLE_OCR", "true")
    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=FakePaddleOCR))
    monkeypatch.setattr(backend_main, "OCR_ENGINE", None)

    response = upload_pair(design=make_png(mark=False), developed=make_png(mark=False))

    assert response.status_code == 200
    payload = response.json()
    assert payload["capabilities"]["ocrEnabled"] is True
    assert any("OCR 已识别" in note for note in payload["capabilities"]["notes"])


def test_enabled_deepseek_enhances_issue_description(monkeypatch):
    class FakeMessage:
        content = '[{"id":"VIS-001","description":"DeepSeek 改写后的走查问题","suggestion":"按结构化结果复核该区域"}]'

    class FakeChoice:
        message = FakeMessage()

    class FakeChatCompletions:
        @staticmethod
        def create(**kwargs):
            assert kwargs["model"] == "deepseek-v4-flash"
            assert kwargs["response_format"] == {"type": "json_object"}
            return SimpleNamespace(choices=[FakeChoice()])

    class FakeOpenAI:
        def __init__(self, api_key=None, base_url=None):
            assert api_key == "test-key"
            assert base_url == "https://api.deepseek.com"
            self.chat = SimpleNamespace(completions=FakeChatCompletions())

    monkeypatch.setenv("UIDESIGN_ENABLE_AI", "true")
    monkeypatch.setenv("AI_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("UIDESIGN_ENABLE_GPT", raising=False)
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))

    response = upload_pair()

    assert response.status_code == 200
    payload = response.json()
    assert payload["capabilities"]["gptEnabled"] is True
    assert payload["capabilities"]["aiProvider"] == "deepseek"
    assert payload["capabilities"]["aiModel"] == "deepseek-v4-flash"
    assert payload["issues"][0]["description"] == "DeepSeek 改写后的走查问题"
