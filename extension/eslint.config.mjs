import js from "@eslint/js"
import { defineConfig } from "eslint/config"
import globals from "globals"
import tseslint from "typescript-eslint"

export default defineConfig(
  { ignores: ["dist/", "artifacts/", "node_modules/"] },
  js.configs.recommended,
  tseslint.configs.recommended,
  {
    files: ["src/**/*.ts"],
    languageOptions: { globals: { ...globals.browser, ...globals.webextensions } },
    rules: {
      "no-console": ["error", { allow: ["warn", "error"] }],
    },
  },
  {
    files: ["scripts/**/*", "*.mjs", "*.ts"],
    languageOptions: { globals: globals.node },
  }
)
