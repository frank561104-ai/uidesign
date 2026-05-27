import {
  AlertTriangle,
  CheckCircle2,
  Download,
  FileText,
  Filter,
  ImageIcon,
  Loader2,
  Printer,
  Upload,
} from "lucide-react";
import { ChangeEvent, useMemo, useState } from "react";

import { Audit, Issue, ReviewStatus, createDemoAudit, createDemoMarkdown } from "./demoAudit";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";
const IS_DEMO_MODE = String(import.meta.env.VITE_DEMO_MODE) === "true";

function toPercent(value: number) {
  return `${Number(value.toFixed(2))}%`;
}

type UploadSlotProps = {
  label: string;
  file: File | null;
  preview: string;
  onChange: (file: File | null) => void;
};

function UploadSlot({ label, file, preview, onChange }: UploadSlotProps) {
  function handleChange(event: ChangeEvent<HTMLInputElement>) {
    onChange(event.target.files?.[0] ?? null);
  }

  return (
    <section className="upload-slot">
      <div className="slot-header">
        <ImageIcon size={18} aria-hidden="true" />
        <h2>{label}</h2>
      </div>
      <label className={`drop-zone ${preview ? "has-preview" : ""}`}>
        <input aria-label={label} type="file" accept="image/png,image/jpeg" onChange={handleChange} />
        {preview ? (
          <img src={preview} alt={`${label}预览`} />
        ) : (
          <span>
            <Upload size={22} aria-hidden="true" />
            选择截图
          </span>
        )}
      </label>
      {file ? <p className="file-name">{file.name}</p> : <p className="file-name muted">PNG / JPG / JPEG</p>}
    </section>
  );
}

function ScorePanel({ audit }: { audit: Audit }) {
  return (
    <section className="score-panel">
      <div>
        <p className="section-label">还原度总分</p>
        <strong className="score-number">{audit.score.total}</strong>
      </div>
      <div className="dimension-grid">
        {Object.entries(audit.score.dimensions).map(([name, value]) => (
          <div key={name} className="dimension-item">
            <span>{name}</span>
            <b>{value}</b>
          </div>
        ))}
      </div>
    </section>
  );
}

function CapabilityNotes({ audit }: { audit: Audit }) {
  if (IS_DEMO_MODE) {
    return (
      <section className="capability-panel demo-panel">
        <div className="capability-row">
          <span className="status-off">演示模式</span>
          <span className="status-off">OpenCV 未运行</span>
          <span className="status-off">OCR 未启用</span>
          <span className="status-off">GPT-4o 未启用</span>
        </div>
        <p>这是单 HTML 前端演示：用于快速展示上传、差异标注、问题列表和报告导出的交互。</p>
        <p>演示模式不会运行真实检测，也不会上传截图；图片只在当前浏览器本地预览。</p>
      </section>
    );
  }

  const explanations = [
    audit.capabilities.ocrEnabled
      ? "OCR 已启用：会检查截图中的文案内容、缺失文本和文本位置。"
      : "OCR 未启用：暂时不会检查截图里的文案是否缺失、写错或位置偏移。",
    audit.capabilities.gptEnabled
      ? "GPT-4o 已启用：只会基于结构化检测结果优化问题描述，不发送完整截图。"
      : "GPT-4o 未启用：问题说明由本地规则生成，不会把完整截图发送给外部模型。",
  ];

  return (
    <section className="capability-panel">
      <div className="capability-row">
        <span className="status-on">OpenCV 已启用</span>
        <span className={audit.capabilities.ocrEnabled ? "status-on" : "status-off"}>
          OCR {audit.capabilities.ocrEnabled ? "已启用" : "未启用"}
        </span>
        <span className={audit.capabilities.gptEnabled ? "status-on" : "status-off"}>
          GPT-4o {audit.capabilities.gptEnabled ? "已启用" : "未启用"}
        </span>
      </div>
      {explanations.map((note) => (
        <p key={note}>{note}</p>
      ))}
    </section>
  );
}

function IssueList({
  audit,
  selectedId,
  onSelect,
  onReview,
}: {
  audit: Audit;
  selectedId: string;
  onSelect: (id: string) => void;
  onReview: (issue: Issue) => void;
}) {
  const [typeFilter, setTypeFilter] = useState("全部类型");
  const [severityFilter, setSeverityFilter] = useState("全部严重程度");
  const [reviewFilter, setReviewFilter] = useState("全部状态");

  const issueTypes = useMemo(() => Array.from(new Set(audit.issues.map((issue) => issue.type))), [audit.issues]);
  const filtered = audit.issues.filter((issue) => {
    return (
      (typeFilter === "全部类型" || issue.type === typeFilter) &&
      (severityFilter === "全部严重程度" || issue.severity === severityFilter) &&
      (reviewFilter === "全部状态" || issue.reviewStatus === reviewFilter)
    );
  });

  return (
    <section className="issue-panel">
      <div className="panel-title">
        <Filter size={18} aria-hidden="true" />
        <h2>问题列表</h2>
      </div>
      <div className="filters">
        <label>
          问题类型
          <select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
            <option>全部类型</option>
            <option>文本问题</option>
            {issueTypes.map((type) => (
              <option key={type}>{type}</option>
            ))}
          </select>
        </label>
        <label>
          严重程度
          <select value={severityFilter} onChange={(event) => setSeverityFilter(event.target.value)}>
            <option>全部严重程度</option>
            <option>高</option>
            <option>中</option>
            <option>低</option>
          </select>
        </label>
        <label>
          复核状态
          <select value={reviewFilter} onChange={(event) => setReviewFilter(event.target.value)}>
            <option>全部状态</option>
            <option>正确</option>
            <option>误判</option>
            <option>忽略</option>
            <option>需后续确认</option>
          </select>
        </label>
      </div>
      <div className="issue-list">
        {filtered.length === 0 ? (
          <p className="empty-state">没有符合筛选条件的问题</p>
        ) : (
          filtered.map((issue) => (
            <article
              key={issue.id}
              className={`issue-card ${selectedId === issue.id ? "is-selected" : ""}`}
              onClick={() => onSelect(issue.id)}
            >
              <header>
                <span className="issue-id">{issue.id}</span>
                <span className={`severity severity-${issue.severity}`}>{issue.severity}</span>
                <span className="review-status">{issue.reviewStatus}</span>
              </header>
              <h3>{issue.type}</h3>
              <p>{issue.description}</p>
              <dl>
                <div>
                  <dt>开发表现</dt>
                  <dd>{issue.developedObservation}</dd>
                </div>
                <div>
                  <dt>建议</dt>
                  <dd>{issue.suggestion}</dd>
                </div>
              </dl>
              {selectedId === issue.id ? (
                <button
                  className="ghost-button"
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    onReview(issue);
                  }}
                >
                  <CheckCircle2 size={16} aria-hidden="true" />
                  标记正确
                </button>
              ) : null}
            </article>
          ))
        )}
      </div>
    </section>
  );
}

export default function App() {
  const [designFile, setDesignFile] = useState<File | null>(null);
  const [developedFile, setDevelopedFile] = useState<File | null>(null);
  const [designPreview, setDesignPreview] = useState("");
  const [developedPreview, setDevelopedPreview] = useState("");
  const [audit, setAudit] = useState<Audit | null>(null);
  const [selectedIssueId, setSelectedIssueId] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState("");
  const reportUrl = useMemo(() => {
    if (!audit || !IS_DEMO_MODE) return "";
    return URL.createObjectURL(new Blob([createDemoMarkdown(audit)], { type: "text/markdown;charset=utf-8" }));
  }, [audit]);

  function handleDesign(file: File | null) {
    setDesignFile(file);
    setDesignPreview(file ? URL.createObjectURL(file) : "");
  }

  function handleDeveloped(file: File | null) {
    setDevelopedFile(file);
    setDevelopedPreview(file ? URL.createObjectURL(file) : "");
  }

  async function submitAudit() {
    if (!designFile || !developedFile) return;
    setIsSubmitting(true);
    setError("");

    if (IS_DEMO_MODE) {
      window.setTimeout(() => {
        const result = createDemoAudit(designFile, developedFile);
        setAudit(result);
        setSelectedIssueId(result.issues[0]?.id ?? "");
        setIsSubmitting(false);
      }, 250);
      return;
    }

    const formData = new FormData();
    formData.append("design_image", designFile);
    formData.append("developed_image", developedFile);
    try {
      const response = await fetch(`${API_BASE}/api/audits`, { method: "POST", body: formData });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || "走查任务创建失败");
      }
      const result = (await response.json()) as Audit;
      setAudit(result);
      setSelectedIssueId(result.issues[0]?.id ?? "");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "走查任务创建失败");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function markCorrect(issue: Issue) {
    if (!audit) return;
    if (IS_DEMO_MODE) {
      const updated = { ...issue, reviewStatus: "正确" as ReviewStatus, note: "演示模式：已在本地标记为正确" };
      setAudit((current) =>
        current
          ? {
              ...current,
              issues: current.issues.map((item) => (item.id === updated.id ? updated : item)),
            }
          : current,
      );
      return;
    }

    const response = await fetch(`${API_BASE}/api/audits/${audit.id}/issues/${issue.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reviewStatus: "正确", note: "确认需要修复" }),
    });
    if (!response.ok) return;
    const updated = (await response.json()) as Issue;
    setAudit((current) =>
      current
        ? {
            ...current,
            issues: current.issues.map((item) => (item.id === updated.id ? updated : item)),
          }
        : current,
    );
  }

  const selectedIssue = audit?.issues.find((issue) => issue.id === selectedIssueId);
  const canSubmit = Boolean(designFile && developedFile) && !isSubmitting;

  return (
    <main className="app-shell">
      <header className="top-bar">
        <div>
          <h1>uidesign</h1>
          <p>设计还原度走查</p>
        </div>
        <div className="toolbar">
          <button className="primary-button toolbar-primary" type="button" disabled={!canSubmit} onClick={submitAudit}>
            {isSubmitting ? <Loader2 className="spin" size={18} aria-hidden="true" /> : <AlertTriangle size={18} aria-hidden="true" />}
            开始对比
          </button>
          {audit ? (
            <>
            <a
              className="icon-button"
              href={IS_DEMO_MODE ? reportUrl : `${API_BASE}/api/audits/${audit.id}/report.md`}
              download={IS_DEMO_MODE ? "uidesign-demo-report.md" : undefined}
            >
              <Download size={17} aria-hidden="true" />
              Markdown
            </a>
            <button className="icon-button" type="button" onClick={() => window.print()}>
              <Printer size={17} aria-hidden="true" />
              打印 PDF
            </button>
            </>
          ) : null}
        </div>
      </header>

      <section className="workspace">
        {error ? <p className="error-text top-error">{error}</p> : null}
        <div className="upload-grid">
          <UploadSlot label="设计稿截图" file={designFile} preview={designPreview} onChange={handleDesign} />
          <UploadSlot label="开发页面截图" file={developedFile} preview={developedPreview} onChange={handleDeveloped} />
        </div>

        {audit ? (
          <>
            <div className="result-grid">
              <ScorePanel audit={audit} />
              <CapabilityNotes audit={audit} />
            </div>
            <section className="comparison-grid">
              <div className="image-panel">
                <div className="panel-title">
                  <ImageIcon size={18} aria-hidden="true" />
                  <h2>设计稿</h2>
                </div>
                {designPreview ? <img src={designPreview} alt="设计稿截图预览" /> : null}
              </div>
              <div className="image-panel annotated-panel">
                <div className="panel-title">
                  <FileText size={18} aria-hidden="true" />
                  <h2>差异标注</h2>
                </div>
                <div className="annotated-wrap">
                  <img
                    src={IS_DEMO_MODE ? developedPreview : `${API_BASE}/api/audits/${audit.id}/annotated-image`}
                    alt="差异标注图"
                  />
                  {selectedIssue ? (
                    <span
                      className="selected-box"
                      style={{
                        left: toPercent((selectedIssue.bbox.x / audit.developedImage.width) * 100),
                        top: toPercent((selectedIssue.bbox.y / audit.developedImage.height) * 100),
                        width: toPercent((selectedIssue.bbox.width / audit.developedImage.width) * 100),
                        height: toPercent((selectedIssue.bbox.height / audit.developedImage.height) * 100),
                      }}
                    />
                  ) : null}
                </div>
              </div>
              <IssueList
                audit={audit}
                selectedId={selectedIssueId}
                onSelect={setSelectedIssueId}
                onReview={markCorrect}
              />
            </section>
          </>
        ) : null}
      </section>
    </main>
  );
}
