export type ReviewStatus = "正确" | "误判" | "忽略" | "需后续确认";

export type Issue = {
  id: string;
  type: string;
  severity: string;
  bbox: { x: number; y: number; width: number; height: number };
  description: string;
  designObservation: string;
  developedObservation: string;
  suggestion: string;
  confidence: number;
  reviewStatus: ReviewStatus;
  note: string;
};

export type Audit = {
  id: string;
  createdAt: string;
  designImage: { filename: string; width: number; height: number; contentType: string };
  developedImage: { filename: string; width: number; height: number; contentType: string };
  preprocessing: string[];
  capabilities: {
    opencvEnabled: boolean;
    ocrEnabled: boolean;
    gptEnabled: boolean;
    notes: string[];
  };
  score: {
    total: number;
    dimensions: Record<string, number>;
    deductions: string[];
  };
  issues: Issue[];
};

const DEMO_WIDTH = 1000;
const DEMO_HEIGHT = 720;

export function createDemoAudit(designFile: File, developedFile: File): Audit {
  const issues: Issue[] = [
    {
      id: "DEMO-001",
      type: "结构布局问题",
      severity: "中",
      bbox: { x: 120, y: 92, width: 330, height: 86 },
      description: "顶部导航区域与设计稿存在明显位置差异，建议优先复核间距和对齐方式。",
      designObservation: "设计稿中导航内容更靠左，按钮与标题之间留白更紧凑。",
      developedObservation: "开发页面顶部区域整体偏右，局部高度也略高。",
      suggestion: "检查导航容器宽度、左右 padding、按钮高度和垂直居中规则。",
      confidence: 0.86,
      reviewStatus: "需后续确认",
      note: "",
    },
    {
      id: "DEMO-002",
      type: "颜色问题",
      severity: "低",
      bbox: { x: 570, y: 286, width: 230, height: 118 },
      description: "主操作按钮颜色与设计稿不完全一致，可能是状态色或透明度配置偏差。",
      designObservation: "设计稿中按钮背景色更深，对比更强。",
      developedObservation: "开发页面按钮颜色偏浅，边框也更明显。",
      suggestion: "核对按钮背景色、hover/disabled 状态色和边框 token。",
      confidence: 0.78,
      reviewStatus: "需后续确认",
      note: "",
    },
    {
      id: "DEMO-003",
      type: "文本问题",
      severity: "低",
      bbox: { x: 176, y: 508, width: 302, height: 58 },
      description: "卡片标题文案长度或换行与设计稿不一致，建议确认是否漏字或样式导致换行。",
      designObservation: "设计稿中标题保持单行展示。",
      developedObservation: "开发页面标题疑似提前换行，影响卡片高度。",
      suggestion: "核对文案内容、字号、字重、行高和卡片内容宽度。",
      confidence: 0.72,
      reviewStatus: "需后续确认",
      note: "",
    },
  ];

  return {
    id: "demo-audit",
    createdAt: new Date().toISOString(),
    designImage: {
      filename: designFile.name,
      width: DEMO_WIDTH,
      height: DEMO_HEIGHT,
      contentType: designFile.type || "image/png",
    },
    developedImage: {
      filename: developedFile.name,
      width: DEMO_WIDTH,
      height: DEMO_HEIGHT,
      contentType: developedFile.type || "image/png",
    },
    preprocessing: [
      "演示模式：读取本地截图文件名和预览图，不上传图片。",
      "演示模式：生成固定的示例差异区域，用来展示走查交互和报告结构。",
    ],
    capabilities: {
      opencvEnabled: false,
      ocrEnabled: false,
      gptEnabled: false,
      notes: [
        "演示模式不会运行真实 OpenCV 检测，只展示前端交互流程。",
        "演示模式不会启用 OCR 或 GPT-4o，也不会上传完整截图。",
      ],
    },
    score: {
      total: 84,
      dimensions: {
        文本一致性: 88,
        颜色一致性: 82,
        布局一致性: 79,
        间距一致性: 83,
        组件完整性: 92,
        图片图标一致性: 91,
      },
      deductions: issues.map((issue) => `${issue.id} ${issue.type}（${issue.severity}）示例扣分`),
    },
    issues,
  };
}

export function createDemoMarkdown(audit: Audit): string {
  const issueLines = audit.issues
    .map(
      (issue) =>
        `- ${issue.id}｜${issue.severity}｜${issue.type}：${issue.description}\n  - 建议：${issue.suggestion}\n  - 复核状态：${issue.reviewStatus}`,
    )
    .join("\n");

  return `# uidesign 演示报告

> 这是单 HTML 前端演示报告，用来展示走查流程和报告结构，不代表真实算法检测结果。

## 基本信息

- 设计稿截图：${audit.designImage.filename}
- 开发页面截图：${audit.developedImage.filename}
- 还原度示例分：${audit.score.total}
- 生成时间：${audit.createdAt}

## 能力说明

- 本 demo 不启动 Python 后端。
- 本 demo 不运行 OpenCV、PaddleOCR 或 GPT-4o。
- 上传的截图只在浏览器本地预览，不会被上传。

## 示例问题

${issueLines}
`;
}
