import js from '@eslint/js';
import globals from 'globals';
import tseslint from 'typescript-eslint';
import reactHooks from 'eslint-plugin-react-hooks';

export default [
  { ignores: ['node_modules/**', 'frontend/dist/**', 'assets/**', 'electron/automation/**'] },
  {
    ...js.configs.recommended,
    files: ['electron/**/*.cjs'],
    languageOptions: { globals: globals.node, sourceType: 'commonjs' },
    rules: {
      ...js.configs.recommended.rules,
      'no-unused-vars': 'off',
      // 既有可选系统集成允许失败；保持异常包装和候选回退的运行语义。
      'no-empty': ['error', { allowEmptyCatch: true }],
      'no-useless-assignment': 'off',
      'preserve-caught-error': 'off',
    },
  },
  {
    files: ['frontend/src/**/*.{ts,tsx}', 'frontend/vite.config.ts'],
    languageOptions: {
      parser: tseslint.parser,
      globals: { ...globals.browser, ...globals.node },
      parserOptions: { ecmaVersion: 'latest', sourceType: 'module', ecmaFeatures: { jsx: true } },
    },
    plugins: { '@typescript-eslint': tseslint.plugin, 'react-hooks': reactHooks },
    rules: {
      ...js.configs.recommended.rules,
      'no-undef': 'off', // TypeScript checks names and types, including DOM declarations.
      'no-unused-vars': 'off',
      'no-empty': ['error', { allowEmptyCatch: true }],
      'react-hooks/rules-of-hooks': 'error',
      // Existing stable native-event subscriptions use refs for current session state.
      // Review their closures explicitly; do not change effect registration via autofix.
      'react-hooks/exhaustive-deps': 'warn',
    },
  },
  {
    files: ['electron/main.cjs'],
    rules: { 'no-control-regex': 'off' }, // 日志必须过滤控制字符。
  },
  {
    files: ['frontend/src/ResizeSessionController.ts'],
    rules: { 'no-self-assign': 'off' }, // 既有滚动写回留待独立行为整改。
  },
];
