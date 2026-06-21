import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import prettier from 'eslint-config-prettier'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
    },
  },
  // Test files run under Vitest globals (describe/it/expect/vi).
  {
    files: ['**/*.{test,spec}.{ts,tsx}', 'src/test/**'],
    languageOptions: {
      globals: globals.vitest,
    },
  },
  // Design-system primitives (slice 23) intentionally co-export their cva variant
  // helpers (buttonVariants, badgeVariants) alongside the component, per the shadcn
  // convention. cva() results are not constants, so react-refresh's allowConstantExport
  // does not exempt them; the rule is off for this folder only.
  {
    files: ['src/components/ui/**'],
    rules: {
      'react-refresh/only-export-components': 'off',
    },
  },
  // Disable stylistic rules that conflict with Prettier. Must come last.
  prettier,
])
