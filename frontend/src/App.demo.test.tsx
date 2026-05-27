import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

function pngFile(name: string) {
  return new File(["fake"], name, { type: "image/png" });
}

async function renderDemoApp() {
  vi.stubEnv("VITE_DEMO_MODE", "true");
  vi.resetModules();
  const { default: App } = await import("./App");
  render(<App />);
}

describe("App demo mode", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  test("creates a local simulated audit without calling the backend", async () => {
    await renderDemoApp();

    await userEvent.upload(screen.getByLabelText("设计稿截图"), pngFile("design.png"));
    await userEvent.upload(screen.getByLabelText("开发页面截图"), pngFile("developed.png"));
    await userEvent.click(screen.getByRole("button", { name: "开始对比" }));

    expect(await screen.findByText("演示模式")).toBeInTheDocument();
    expect(screen.getByText("DEMO-001")).toBeInTheDocument();
    expect(screen.getByText("Markdown")).toBeInTheDocument();
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  test("updates review state locally and keeps the selected overlay visible", async () => {
    await renderDemoApp();

    await userEvent.upload(screen.getByLabelText("设计稿截图"), pngFile("design.png"));
    await userEvent.upload(screen.getByLabelText("开发页面截图"), pngFile("developed.png"));
    await userEvent.click(screen.getByRole("button", { name: "开始对比" }));

    await screen.findByText("DEMO-001");
    expect(document.querySelector(".selected-box")).toBeInTheDocument();

    await userEvent.click(screen.getAllByRole("article")[1]);
    const selectedBox = document.querySelector(".selected-box");
    expect(selectedBox).toHaveStyle({ left: "57%" });

    await userEvent.click(screen.getByRole("button", { name: "标记正确" }));

    await waitFor(() => {
      expect(screen.getAllByText("正确").length).toBeGreaterThan(1);
    });
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });
});
