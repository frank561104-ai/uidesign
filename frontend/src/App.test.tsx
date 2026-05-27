import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import App from "./App";

const auditPayload = {
  id: "audit-1",
  createdAt: "2026-05-27T10:00:00",
  designImage: { filename: "design.png", width: 220, height: 160, contentType: "image/png" },
  developedImage: { filename: "dev.png", width: 220, height: 160, contentType: "image/png" },
  preprocessing: ["将设计稿截图缩放到开发页面截图尺寸后进行视觉对比。"],
  capabilities: {
    opencvEnabled: true,
    ocrEnabled: false,
    gptEnabled: false,
    notes: ["GPT-4o 未启用：默认只用本地结构化结果生成描述，不上传原始截图。"],
  },
  score: {
    total: 87,
    dimensions: {
      文本一致性: 100,
      颜色一致性: 92,
      布局一致性: 100,
      间距一致性: 95,
      组件完整性: 100,
      图片图标一致性: 100,
    },
    deductions: ["VIS-001 颜色问题（中）扣 9 分"],
  },
  issues: [
    {
      id: "VIS-001",
      type: "颜色问题",
      severity: "中",
      bbox: { x: 70, y: 45, width: 80, height: 60 },
      description: "明显的颜色问题，建议按中优先级复核。",
      designObservation: "该区域在设计稿中与开发页面存在可见差异。",
      developedObservation: "开发页面在按钮区域变化明显。",
      suggestion: "检查背景色、文字色、边框色或组件状态色是否还原。",
      confidence: 0.82,
      reviewStatus: "需后续确认",
      note: "",
    },
  ],
};

function pngFile(name: string) {
  return new File(["fake"], name, { type: "image/png" });
}

describe("App", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = input.toString();
        if (url.endsWith("/api/audits") && init?.method === "POST") {
          return Response.json(auditPayload);
        }
        if (url.endsWith("/api/audits/audit-1/issues/VIS-001") && init?.method === "PATCH") {
          return Response.json({ ...auditPayload.issues[0], reviewStatus: "正确", note: "确认需要修复" });
        }
        return new Response("not found", { status: 404 });
      }),
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  test("keeps compare action disabled until both screenshots are selected", async () => {
    render(<App />);

    expect(screen.queryByText("当前任务")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始对比" })).toBeDisabled();

    await userEvent.upload(screen.getByLabelText("设计稿截图"), pngFile("design.png"));
    expect(screen.getByRole("button", { name: "开始对比" })).toBeDisabled();

    await userEvent.upload(screen.getByLabelText("开发页面截图"), pngFile("dev.png"));
    expect(screen.getByRole("button", { name: "开始对比" })).toBeEnabled();
    expect(screen.getByText("design.png")).toBeInTheDocument();
    expect(screen.getByText("dev.png")).toBeInTheDocument();
  });

  test("submits screenshots and renders score, issue list, filters and review update", async () => {
    render(<App />);

    await userEvent.upload(screen.getByLabelText("设计稿截图"), pngFile("design.png"));
    await userEvent.upload(screen.getByLabelText("开发页面截图"), pngFile("dev.png"));
    await userEvent.click(screen.getByRole("button", { name: "开始对比" }));

    expect(await screen.findByText("87")).toBeInTheDocument();
    expect(screen.getByText("VIS-001")).toBeInTheDocument();
    expect(screen.getByText("明显的颜色问题，建议按中优先级复核。")).toBeInTheDocument();
    expect(screen.getByText("OCR 未启用：暂时不会检查截图里的文案是否缺失、写错或位置偏移。")).toBeInTheDocument();
    expect(screen.getByText("GPT-4o 未启用：问题说明由本地规则生成，不会把完整截图发送给外部模型。")).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("问题类型"), "文本问题");
    expect(screen.queryByText("VIS-001")).not.toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("问题类型"), "全部类型");
    await userEvent.click(screen.getByRole("button", { name: "标记正确" }));

    await waitFor(() => {
      expect(screen.getAllByText("正确").length).toBeGreaterThan(1);
    });
  });
});
