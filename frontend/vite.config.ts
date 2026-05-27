import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [
    react(),
    {
      name: "remove-crossorigin",
      transformIndexHtml(html) {
        return html.replace(/\s*crossorigin\s*/g, " ");
      },
    },
  ],
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
  },
});
