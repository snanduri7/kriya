import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import reactHooks from 'eslint-plugin-react-hooks';
import globals from 'globals';
import { FORBIDDEN_IMPORT_REGEX } from '../scripts/forbidden-imports.mjs';

/** ui/shared is host-independent (gate A-2, P-R1): no Electron, no Node built-ins, no node-only packages. */
export default tseslint.config(
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['src/**/*.{ts,tsx}', 'test/**/*.{ts,tsx}'],
    plugins: { 'react-hooks': reactHooks },
    languageOptions: { globals: { ...globals.browser } },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'no-restricted-imports': ['error', { patterns: [{ regex: FORBIDDEN_IMPORT_REGEX, message: 'ui/shared must stay host-independent (P-R1): use the HostAdapter.' }] }],
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    },
  },
);
