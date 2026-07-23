# Frontend Third-Party Notice Input

Status: generated audit input; not a final release notice or legal approval
Source: `hikari-frontend/package-lock.json`
Lock SHA-256: `c4ce5af84589839b8375a41aa86d4dd380269351e71b9e11ba9d2d1c91ac8b75`
Package entries: 406

Regenerate with `python scripts/frontend_third_party.py`. Verify with `python scripts/frontend_third_party.py --check`.

## Review boundaries

- Registry license fields and archive URLs are evidence pointers, not substitutes for the license and notice files inside each resolved artifact.
- The `@img/sharp-libvips-*` archives report LGPL-3.0-or-later or combined terms, while upstream libvips reports LGPL-2.1-or-later. Exact binary composition, corresponding-source, relinking, and notice obligations remain release blockers.
- MPL packages require license-file and modified-file review. The resolved `axe-core` artifact also contains its own third-party notice file.
- CC-BY content requires attribution review. Python-2.0 and CC0 entries still require their artifact license evidence to be represented in the final notice.
- Permissive families are included in the complete index and still require normal copyright, license-text, and notice retention review before distribution.

Upstream evidence: [sharp-libvips](https://github.com/lovell/sharp-libvips), [libvips](https://github.com/libvips/libvips), [axe-core](https://github.com/dequelabs/axe-core), [Lightning CSS](https://github.com/parcel-bundler/lightningcss), [caniuse-lite](https://github.com/browserslist/caniuse-lite), [argparse](https://github.com/nodeca/argparse), and [language-subtag-registry](https://github.com/mattcg/language-subtag-registry).

## License-family summary

| Entries | Lock license expression |
|---:|---|
| 314 | MIT |
| 32 | Apache-2.0 |
| 17 | ISC |
| 12 | MPL-2.0 |
| 10 | LGPL-3.0-or-later |
| 7 | BSD-2-Clause |
| 3 | Apache-2.0 AND LGPL-3.0-or-later |
| 3 | BlueOak-1.0.0 |
| 2 | 0BSD |
| 2 | BSD-3-Clause |
| 1 | Apache-2.0 AND LGPL-3.0-or-later AND MIT |
| 1 | CC-BY-4.0 |
| 1 | CC0-1.0 |
| 1 | Python-2.0 |

## Focused review set

| Package path | Version | Lock license expression | Registry archive |
|---|---:|---|---|
| `node_modules/@img/sharp-libvips-darwin-arm64` | 1.3.2 | LGPL-3.0-or-later | [archive](https://registry.npmjs.org/@img/sharp-libvips-darwin-arm64/-/sharp-libvips-darwin-arm64-1.3.2.tgz) |
| `node_modules/@img/sharp-libvips-darwin-x64` | 1.3.2 | LGPL-3.0-or-later | [archive](https://registry.npmjs.org/@img/sharp-libvips-darwin-x64/-/sharp-libvips-darwin-x64-1.3.2.tgz) |
| `node_modules/@img/sharp-libvips-linux-arm` | 1.3.2 | LGPL-3.0-or-later | [archive](https://registry.npmjs.org/@img/sharp-libvips-linux-arm/-/sharp-libvips-linux-arm-1.3.2.tgz) |
| `node_modules/@img/sharp-libvips-linux-arm64` | 1.3.2 | LGPL-3.0-or-later | [archive](https://registry.npmjs.org/@img/sharp-libvips-linux-arm64/-/sharp-libvips-linux-arm64-1.3.2.tgz) |
| `node_modules/@img/sharp-libvips-linux-ppc64` | 1.3.2 | LGPL-3.0-or-later | [archive](https://registry.npmjs.org/@img/sharp-libvips-linux-ppc64/-/sharp-libvips-linux-ppc64-1.3.2.tgz) |
| `node_modules/@img/sharp-libvips-linux-riscv64` | 1.3.2 | LGPL-3.0-or-later | [archive](https://registry.npmjs.org/@img/sharp-libvips-linux-riscv64/-/sharp-libvips-linux-riscv64-1.3.2.tgz) |
| `node_modules/@img/sharp-libvips-linux-s390x` | 1.3.2 | LGPL-3.0-or-later | [archive](https://registry.npmjs.org/@img/sharp-libvips-linux-s390x/-/sharp-libvips-linux-s390x-1.3.2.tgz) |
| `node_modules/@img/sharp-libvips-linux-x64` | 1.3.2 | LGPL-3.0-or-later | [archive](https://registry.npmjs.org/@img/sharp-libvips-linux-x64/-/sharp-libvips-linux-x64-1.3.2.tgz) |
| `node_modules/@img/sharp-libvips-linuxmusl-arm64` | 1.3.2 | LGPL-3.0-or-later | [archive](https://registry.npmjs.org/@img/sharp-libvips-linuxmusl-arm64/-/sharp-libvips-linuxmusl-arm64-1.3.2.tgz) |
| `node_modules/@img/sharp-libvips-linuxmusl-x64` | 1.3.2 | LGPL-3.0-or-later | [archive](https://registry.npmjs.org/@img/sharp-libvips-linuxmusl-x64/-/sharp-libvips-linuxmusl-x64-1.3.2.tgz) |
| `node_modules/@img/sharp-wasm32` | 0.35.3 | Apache-2.0 AND LGPL-3.0-or-later AND MIT | [archive](https://registry.npmjs.org/@img/sharp-wasm32/-/sharp-wasm32-0.35.3.tgz) |
| `node_modules/@img/sharp-win32-arm64` | 0.35.3 | Apache-2.0 AND LGPL-3.0-or-later | [archive](https://registry.npmjs.org/@img/sharp-win32-arm64/-/sharp-win32-arm64-0.35.3.tgz) |
| `node_modules/@img/sharp-win32-ia32` | 0.35.3 | Apache-2.0 AND LGPL-3.0-or-later | [archive](https://registry.npmjs.org/@img/sharp-win32-ia32/-/sharp-win32-ia32-0.35.3.tgz) |
| `node_modules/@img/sharp-win32-x64` | 0.35.3 | Apache-2.0 AND LGPL-3.0-or-later | [archive](https://registry.npmjs.org/@img/sharp-win32-x64/-/sharp-win32-x64-0.35.3.tgz) |
| `node_modules/argparse` | 2.0.1 | Python-2.0 | [archive](https://registry.npmjs.org/argparse/-/argparse-2.0.1.tgz) |
| `node_modules/axe-core` | 4.10.3 | MPL-2.0 | [archive](https://registry.npmjs.org/axe-core/-/axe-core-4.10.3.tgz) |
| `node_modules/caniuse-lite` | 1.0.30001733 | CC-BY-4.0 | [archive](https://registry.npmjs.org/caniuse-lite/-/caniuse-lite-1.0.30001733.tgz) |
| `node_modules/language-subtag-registry` | 0.3.23 | CC0-1.0 | [archive](https://registry.npmjs.org/language-subtag-registry/-/language-subtag-registry-0.3.23.tgz) |
| `node_modules/lightningcss` | 1.30.1 | MPL-2.0 | [archive](https://registry.npmjs.org/lightningcss/-/lightningcss-1.30.1.tgz) |
| `node_modules/lightningcss-darwin-arm64` | 1.30.1 | MPL-2.0 | [archive](https://registry.npmjs.org/lightningcss-darwin-arm64/-/lightningcss-darwin-arm64-1.30.1.tgz) |
| `node_modules/lightningcss-darwin-x64` | 1.30.1 | MPL-2.0 | [archive](https://registry.npmjs.org/lightningcss-darwin-x64/-/lightningcss-darwin-x64-1.30.1.tgz) |
| `node_modules/lightningcss-freebsd-x64` | 1.30.1 | MPL-2.0 | [archive](https://registry.npmjs.org/lightningcss-freebsd-x64/-/lightningcss-freebsd-x64-1.30.1.tgz) |
| `node_modules/lightningcss-linux-arm-gnueabihf` | 1.30.1 | MPL-2.0 | [archive](https://registry.npmjs.org/lightningcss-linux-arm-gnueabihf/-/lightningcss-linux-arm-gnueabihf-1.30.1.tgz) |
| `node_modules/lightningcss-linux-arm64-gnu` | 1.30.1 | MPL-2.0 | [archive](https://registry.npmjs.org/lightningcss-linux-arm64-gnu/-/lightningcss-linux-arm64-gnu-1.30.1.tgz) |
| `node_modules/lightningcss-linux-arm64-musl` | 1.30.1 | MPL-2.0 | [archive](https://registry.npmjs.org/lightningcss-linux-arm64-musl/-/lightningcss-linux-arm64-musl-1.30.1.tgz) |
| `node_modules/lightningcss-linux-x64-gnu` | 1.30.1 | MPL-2.0 | [archive](https://registry.npmjs.org/lightningcss-linux-x64-gnu/-/lightningcss-linux-x64-gnu-1.30.1.tgz) |
| `node_modules/lightningcss-linux-x64-musl` | 1.30.1 | MPL-2.0 | [archive](https://registry.npmjs.org/lightningcss-linux-x64-musl/-/lightningcss-linux-x64-musl-1.30.1.tgz) |
| `node_modules/lightningcss-win32-arm64-msvc` | 1.30.1 | MPL-2.0 | [archive](https://registry.npmjs.org/lightningcss-win32-arm64-msvc/-/lightningcss-win32-arm64-msvc-1.30.1.tgz) |
| `node_modules/lightningcss-win32-x64-msvc` | 1.30.1 | MPL-2.0 | [archive](https://registry.npmjs.org/lightningcss-win32-x64-msvc/-/lightningcss-win32-x64-msvc-1.30.1.tgz) |

## Complete lock index

| Package path | Package | Version | Lock license expression |
|---|---|---:|---|
| `node_modules/@alloc/quick-lru` | `@alloc/quick-lru` | 5.2.0 | MIT |
| `node_modules/@ampproject/remapping` | `@ampproject/remapping` | 2.3.0 | Apache-2.0 |
| `node_modules/@emnapi/core` | `@emnapi/core` | 1.4.5 | MIT |
| `node_modules/@emnapi/runtime` | `@emnapi/runtime` | 1.11.2 | MIT |
| `node_modules/@emnapi/wasi-threads` | `@emnapi/wasi-threads` | 1.0.4 | MIT |
| `node_modules/@eslint-community/eslint-utils` | `@eslint-community/eslint-utils` | 4.7.0 | MIT |
| `node_modules/@eslint-community/eslint-utils/node_modules/eslint-visitor-keys` | `eslint-visitor-keys` | 3.4.3 | Apache-2.0 |
| `node_modules/@eslint-community/regexpp` | `@eslint-community/regexpp` | 4.12.1 | MIT |
| `node_modules/@eslint/config-array` | `@eslint/config-array` | 0.21.0 | Apache-2.0 |
| `node_modules/@eslint/config-helpers` | `@eslint/config-helpers` | 0.3.1 | Apache-2.0 |
| `node_modules/@eslint/core` | `@eslint/core` | 0.15.2 | Apache-2.0 |
| `node_modules/@eslint/eslintrc` | `@eslint/eslintrc` | 3.3.1 | MIT |
| `node_modules/@eslint/js` | `@eslint/js` | 9.33.0 | MIT |
| `node_modules/@eslint/object-schema` | `@eslint/object-schema` | 2.1.6 | Apache-2.0 |
| `node_modules/@eslint/plugin-kit` | `@eslint/plugin-kit` | 0.3.5 | Apache-2.0 |
| `node_modules/@humanfs/core` | `@humanfs/core` | 0.19.1 | Apache-2.0 |
| `node_modules/@humanfs/node` | `@humanfs/node` | 0.16.6 | Apache-2.0 |
| `node_modules/@humanfs/node/node_modules/@humanwhocodes/retry` | `@humanwhocodes/retry` | 0.3.1 | Apache-2.0 |
| `node_modules/@humanwhocodes/module-importer` | `@humanwhocodes/module-importer` | 1.0.1 | Apache-2.0 |
| `node_modules/@humanwhocodes/retry` | `@humanwhocodes/retry` | 0.4.3 | Apache-2.0 |
| `node_modules/@img/colour` | `@img/colour` | 1.1.0 | MIT |
| `node_modules/@img/sharp-darwin-arm64` | `@img/sharp-darwin-arm64` | 0.35.3 | Apache-2.0 |
| `node_modules/@img/sharp-darwin-x64` | `@img/sharp-darwin-x64` | 0.35.3 | Apache-2.0 |
| `node_modules/@img/sharp-freebsd-wasm32` | `@img/sharp-freebsd-wasm32` | 0.35.3 | Apache-2.0 |
| `node_modules/@img/sharp-libvips-darwin-arm64` | `@img/sharp-libvips-darwin-arm64` | 1.3.2 | LGPL-3.0-or-later |
| `node_modules/@img/sharp-libvips-darwin-x64` | `@img/sharp-libvips-darwin-x64` | 1.3.2 | LGPL-3.0-or-later |
| `node_modules/@img/sharp-libvips-linux-arm` | `@img/sharp-libvips-linux-arm` | 1.3.2 | LGPL-3.0-or-later |
| `node_modules/@img/sharp-libvips-linux-arm64` | `@img/sharp-libvips-linux-arm64` | 1.3.2 | LGPL-3.0-or-later |
| `node_modules/@img/sharp-libvips-linux-ppc64` | `@img/sharp-libvips-linux-ppc64` | 1.3.2 | LGPL-3.0-or-later |
| `node_modules/@img/sharp-libvips-linux-riscv64` | `@img/sharp-libvips-linux-riscv64` | 1.3.2 | LGPL-3.0-or-later |
| `node_modules/@img/sharp-libvips-linux-s390x` | `@img/sharp-libvips-linux-s390x` | 1.3.2 | LGPL-3.0-or-later |
| `node_modules/@img/sharp-libvips-linux-x64` | `@img/sharp-libvips-linux-x64` | 1.3.2 | LGPL-3.0-or-later |
| `node_modules/@img/sharp-libvips-linuxmusl-arm64` | `@img/sharp-libvips-linuxmusl-arm64` | 1.3.2 | LGPL-3.0-or-later |
| `node_modules/@img/sharp-libvips-linuxmusl-x64` | `@img/sharp-libvips-linuxmusl-x64` | 1.3.2 | LGPL-3.0-or-later |
| `node_modules/@img/sharp-linux-arm` | `@img/sharp-linux-arm` | 0.35.3 | Apache-2.0 |
| `node_modules/@img/sharp-linux-arm64` | `@img/sharp-linux-arm64` | 0.35.3 | Apache-2.0 |
| `node_modules/@img/sharp-linux-ppc64` | `@img/sharp-linux-ppc64` | 0.35.3 | Apache-2.0 |
| `node_modules/@img/sharp-linux-riscv64` | `@img/sharp-linux-riscv64` | 0.35.3 | Apache-2.0 |
| `node_modules/@img/sharp-linux-s390x` | `@img/sharp-linux-s390x` | 0.35.3 | Apache-2.0 |
| `node_modules/@img/sharp-linux-x64` | `@img/sharp-linux-x64` | 0.35.3 | Apache-2.0 |
| `node_modules/@img/sharp-linuxmusl-arm64` | `@img/sharp-linuxmusl-arm64` | 0.35.3 | Apache-2.0 |
| `node_modules/@img/sharp-linuxmusl-x64` | `@img/sharp-linuxmusl-x64` | 0.35.3 | Apache-2.0 |
| `node_modules/@img/sharp-wasm32` | `@img/sharp-wasm32` | 0.35.3 | Apache-2.0 AND LGPL-3.0-or-later AND MIT |
| `node_modules/@img/sharp-webcontainers-wasm32` | `@img/sharp-webcontainers-wasm32` | 0.35.3 | Apache-2.0 |
| `node_modules/@img/sharp-win32-arm64` | `@img/sharp-win32-arm64` | 0.35.3 | Apache-2.0 AND LGPL-3.0-or-later |
| `node_modules/@img/sharp-win32-ia32` | `@img/sharp-win32-ia32` | 0.35.3 | Apache-2.0 AND LGPL-3.0-or-later |
| `node_modules/@img/sharp-win32-x64` | `@img/sharp-win32-x64` | 0.35.3 | Apache-2.0 AND LGPL-3.0-or-later |
| `node_modules/@isaacs/fs-minipass` | `@isaacs/fs-minipass` | 4.0.1 | ISC |
| `node_modules/@jridgewell/gen-mapping` | `@jridgewell/gen-mapping` | 0.3.12 | MIT |
| `node_modules/@jridgewell/resolve-uri` | `@jridgewell/resolve-uri` | 3.1.2 | MIT |
| `node_modules/@jridgewell/sourcemap-codec` | `@jridgewell/sourcemap-codec` | 1.5.4 | MIT |
| `node_modules/@jridgewell/trace-mapping` | `@jridgewell/trace-mapping` | 0.3.29 | MIT |
| `node_modules/@napi-rs/wasm-runtime` | `@napi-rs/wasm-runtime` | 0.2.12 | MIT |
| `node_modules/@next/env` | `@next/env` | 15.5.21 | MIT |
| `node_modules/@next/eslint-plugin-next` | `@next/eslint-plugin-next` | 15.5.21 | MIT |
| `node_modules/@next/swc-darwin-arm64` | `@next/swc-darwin-arm64` | 15.5.21 | MIT |
| `node_modules/@next/swc-darwin-x64` | `@next/swc-darwin-x64` | 15.5.21 | MIT |
| `node_modules/@next/swc-linux-arm64-gnu` | `@next/swc-linux-arm64-gnu` | 15.5.21 | MIT |
| `node_modules/@next/swc-linux-arm64-musl` | `@next/swc-linux-arm64-musl` | 15.5.21 | MIT |
| `node_modules/@next/swc-linux-x64-gnu` | `@next/swc-linux-x64-gnu` | 15.5.21 | MIT |
| `node_modules/@next/swc-linux-x64-musl` | `@next/swc-linux-x64-musl` | 15.5.21 | MIT |
| `node_modules/@next/swc-win32-arm64-msvc` | `@next/swc-win32-arm64-msvc` | 15.5.21 | MIT |
| `node_modules/@next/swc-win32-x64-msvc` | `@next/swc-win32-x64-msvc` | 15.5.21 | MIT |
| `node_modules/@nodelib/fs.scandir` | `@nodelib/fs.scandir` | 2.1.5 | MIT |
| `node_modules/@nodelib/fs.stat` | `@nodelib/fs.stat` | 2.0.5 | MIT |
| `node_modules/@nodelib/fs.walk` | `@nodelib/fs.walk` | 1.2.8 | MIT |
| `node_modules/@nolyfill/is-core-module` | `@nolyfill/is-core-module` | 1.0.39 | MIT |
| `node_modules/@rtsao/scc` | `@rtsao/scc` | 1.1.0 | MIT |
| `node_modules/@rushstack/eslint-patch` | `@rushstack/eslint-patch` | 1.12.0 | MIT |
| `node_modules/@swc/helpers` | `@swc/helpers` | 0.5.15 | Apache-2.0 |
| `node_modules/@tailwindcss/node` | `@tailwindcss/node` | 4.1.11 | MIT |
| `node_modules/@tailwindcss/oxide` | `@tailwindcss/oxide` | 4.1.11 | MIT |
| `node_modules/@tailwindcss/oxide-android-arm64` | `@tailwindcss/oxide-android-arm64` | 4.1.11 | MIT |
| `node_modules/@tailwindcss/oxide-darwin-arm64` | `@tailwindcss/oxide-darwin-arm64` | 4.1.11 | MIT |
| `node_modules/@tailwindcss/oxide-darwin-x64` | `@tailwindcss/oxide-darwin-x64` | 4.1.11 | MIT |
| `node_modules/@tailwindcss/oxide-freebsd-x64` | `@tailwindcss/oxide-freebsd-x64` | 4.1.11 | MIT |
| `node_modules/@tailwindcss/oxide-linux-arm-gnueabihf` | `@tailwindcss/oxide-linux-arm-gnueabihf` | 4.1.11 | MIT |
| `node_modules/@tailwindcss/oxide-linux-arm64-gnu` | `@tailwindcss/oxide-linux-arm64-gnu` | 4.1.11 | MIT |
| `node_modules/@tailwindcss/oxide-linux-arm64-musl` | `@tailwindcss/oxide-linux-arm64-musl` | 4.1.11 | MIT |
| `node_modules/@tailwindcss/oxide-linux-x64-gnu` | `@tailwindcss/oxide-linux-x64-gnu` | 4.1.11 | MIT |
| `node_modules/@tailwindcss/oxide-linux-x64-musl` | `@tailwindcss/oxide-linux-x64-musl` | 4.1.11 | MIT |
| `node_modules/@tailwindcss/oxide-wasm32-wasi` | `@tailwindcss/oxide-wasm32-wasi` | 4.1.11 | MIT |
| `node_modules/@tailwindcss/oxide-wasm32-wasi/node_modules/@emnapi/core` | `@emnapi/core` | 1.4.3 | MIT |
| `node_modules/@tailwindcss/oxide-wasm32-wasi/node_modules/@emnapi/runtime` | `@emnapi/runtime` | 1.4.3 | MIT |
| `node_modules/@tailwindcss/oxide-wasm32-wasi/node_modules/@emnapi/wasi-threads` | `@emnapi/wasi-threads` | 1.0.2 | MIT |
| `node_modules/@tailwindcss/oxide-wasm32-wasi/node_modules/@napi-rs/wasm-runtime` | `@napi-rs/wasm-runtime` | 0.2.11 | MIT |
| `node_modules/@tailwindcss/oxide-wasm32-wasi/node_modules/@tybys/wasm-util` | `@tybys/wasm-util` | 0.9.0 | MIT |
| `node_modules/@tailwindcss/oxide-wasm32-wasi/node_modules/tslib` | `tslib` | 2.8.0 | 0BSD |
| `node_modules/@tailwindcss/oxide-win32-arm64-msvc` | `@tailwindcss/oxide-win32-arm64-msvc` | 4.1.11 | MIT |
| `node_modules/@tailwindcss/oxide-win32-x64-msvc` | `@tailwindcss/oxide-win32-x64-msvc` | 4.1.11 | MIT |
| `node_modules/@tailwindcss/postcss` | `@tailwindcss/postcss` | 4.1.11 | MIT |
| `node_modules/@tybys/wasm-util` | `@tybys/wasm-util` | 0.10.0 | MIT |
| `node_modules/@types/estree` | `@types/estree` | 1.0.8 | MIT |
| `node_modules/@types/json-schema` | `@types/json-schema` | 7.0.15 | MIT |
| `node_modules/@types/json5` | `@types/json5` | 0.0.29 | MIT |
| `node_modules/@types/node` | `@types/node` | 20.19.10 | MIT |
| `node_modules/@types/react` | `@types/react` | 19.1.9 | MIT |
| `node_modules/@types/react-dom` | `@types/react-dom` | 19.1.7 | MIT |
| `node_modules/@typescript-eslint/eslint-plugin` | `@typescript-eslint/eslint-plugin` | 8.39.0 | MIT |
| `node_modules/@typescript-eslint/eslint-plugin/node_modules/ignore` | `ignore` | 7.0.5 | MIT |
| `node_modules/@typescript-eslint/parser` | `@typescript-eslint/parser` | 8.39.0 | MIT |
| `node_modules/@typescript-eslint/project-service` | `@typescript-eslint/project-service` | 8.39.0 | MIT |
| `node_modules/@typescript-eslint/scope-manager` | `@typescript-eslint/scope-manager` | 8.39.0 | MIT |
| `node_modules/@typescript-eslint/tsconfig-utils` | `@typescript-eslint/tsconfig-utils` | 8.39.0 | MIT |
| `node_modules/@typescript-eslint/type-utils` | `@typescript-eslint/type-utils` | 8.39.0 | MIT |
| `node_modules/@typescript-eslint/types` | `@typescript-eslint/types` | 8.39.0 | MIT |
| `node_modules/@typescript-eslint/typescript-estree` | `@typescript-eslint/typescript-estree` | 8.39.0 | MIT |
| `node_modules/@typescript-eslint/typescript-estree/node_modules/brace-expansion` | `brace-expansion` | 2.1.2 | MIT |
| `node_modules/@typescript-eslint/typescript-estree/node_modules/fast-glob` | `fast-glob` | 3.3.3 | MIT |
| `node_modules/@typescript-eslint/typescript-estree/node_modules/glob-parent` | `glob-parent` | 5.1.2 | ISC |
| `node_modules/@typescript-eslint/typescript-estree/node_modules/minimatch` | `minimatch` | 9.0.9 | ISC |
| `node_modules/@typescript-eslint/utils` | `@typescript-eslint/utils` | 8.39.0 | MIT |
| `node_modules/@typescript-eslint/visitor-keys` | `@typescript-eslint/visitor-keys` | 8.39.0 | MIT |
| `node_modules/@unrs/resolver-binding-android-arm-eabi` | `@unrs/resolver-binding-android-arm-eabi` | 1.11.1 | MIT |
| `node_modules/@unrs/resolver-binding-android-arm64` | `@unrs/resolver-binding-android-arm64` | 1.11.1 | MIT |
| `node_modules/@unrs/resolver-binding-darwin-arm64` | `@unrs/resolver-binding-darwin-arm64` | 1.11.1 | MIT |
| `node_modules/@unrs/resolver-binding-darwin-x64` | `@unrs/resolver-binding-darwin-x64` | 1.11.1 | MIT |
| `node_modules/@unrs/resolver-binding-freebsd-x64` | `@unrs/resolver-binding-freebsd-x64` | 1.11.1 | MIT |
| `node_modules/@unrs/resolver-binding-linux-arm-gnueabihf` | `@unrs/resolver-binding-linux-arm-gnueabihf` | 1.11.1 | MIT |
| `node_modules/@unrs/resolver-binding-linux-arm-musleabihf` | `@unrs/resolver-binding-linux-arm-musleabihf` | 1.11.1 | MIT |
| `node_modules/@unrs/resolver-binding-linux-arm64-gnu` | `@unrs/resolver-binding-linux-arm64-gnu` | 1.11.1 | MIT |
| `node_modules/@unrs/resolver-binding-linux-arm64-musl` | `@unrs/resolver-binding-linux-arm64-musl` | 1.11.1 | MIT |
| `node_modules/@unrs/resolver-binding-linux-ppc64-gnu` | `@unrs/resolver-binding-linux-ppc64-gnu` | 1.11.1 | MIT |
| `node_modules/@unrs/resolver-binding-linux-riscv64-gnu` | `@unrs/resolver-binding-linux-riscv64-gnu` | 1.11.1 | MIT |
| `node_modules/@unrs/resolver-binding-linux-riscv64-musl` | `@unrs/resolver-binding-linux-riscv64-musl` | 1.11.1 | MIT |
| `node_modules/@unrs/resolver-binding-linux-s390x-gnu` | `@unrs/resolver-binding-linux-s390x-gnu` | 1.11.1 | MIT |
| `node_modules/@unrs/resolver-binding-linux-x64-gnu` | `@unrs/resolver-binding-linux-x64-gnu` | 1.11.1 | MIT |
| `node_modules/@unrs/resolver-binding-linux-x64-musl` | `@unrs/resolver-binding-linux-x64-musl` | 1.11.1 | MIT |
| `node_modules/@unrs/resolver-binding-wasm32-wasi` | `@unrs/resolver-binding-wasm32-wasi` | 1.11.1 | MIT |
| `node_modules/@unrs/resolver-binding-win32-arm64-msvc` | `@unrs/resolver-binding-win32-arm64-msvc` | 1.11.1 | MIT |
| `node_modules/@unrs/resolver-binding-win32-ia32-msvc` | `@unrs/resolver-binding-win32-ia32-msvc` | 1.11.1 | MIT |
| `node_modules/@unrs/resolver-binding-win32-x64-msvc` | `@unrs/resolver-binding-win32-x64-msvc` | 1.11.1 | MIT |
| `node_modules/acorn` | `acorn` | 8.15.0 | MIT |
| `node_modules/acorn-jsx` | `acorn-jsx` | 5.3.2 | MIT |
| `node_modules/ajv` | `ajv` | 6.15.0 | MIT |
| `node_modules/ansi-styles` | `ansi-styles` | 4.3.0 | MIT |
| `node_modules/argparse` | `argparse` | 2.0.1 | Python-2.0 |
| `node_modules/aria-query` | `aria-query` | 5.3.2 | Apache-2.0 |
| `node_modules/array-buffer-byte-length` | `array-buffer-byte-length` | 1.0.2 | MIT |
| `node_modules/array-includes` | `array-includes` | 3.1.9 | MIT |
| `node_modules/array.prototype.findlast` | `array.prototype.findlast` | 1.2.5 | MIT |
| `node_modules/array.prototype.findlastindex` | `array.prototype.findlastindex` | 1.2.6 | MIT |
| `node_modules/array.prototype.flat` | `array.prototype.flat` | 1.3.3 | MIT |
| `node_modules/array.prototype.flatmap` | `array.prototype.flatmap` | 1.3.3 | MIT |
| `node_modules/array.prototype.tosorted` | `array.prototype.tosorted` | 1.1.4 | MIT |
| `node_modules/arraybuffer.prototype.slice` | `arraybuffer.prototype.slice` | 1.0.4 | MIT |
| `node_modules/ast-types-flow` | `ast-types-flow` | 0.0.8 | MIT |
| `node_modules/async-function` | `async-function` | 1.0.0 | MIT |
| `node_modules/available-typed-arrays` | `available-typed-arrays` | 1.0.7 | MIT |
| `node_modules/axe-core` | `axe-core` | 4.10.3 | MPL-2.0 |
| `node_modules/axobject-query` | `axobject-query` | 4.1.0 | Apache-2.0 |
| `node_modules/balanced-match` | `balanced-match` | 1.0.2 | MIT |
| `node_modules/brace-expansion` | `brace-expansion` | 1.1.16 | MIT |
| `node_modules/braces` | `braces` | 3.0.3 | MIT |
| `node_modules/call-bind` | `call-bind` | 1.0.8 | MIT |
| `node_modules/call-bind-apply-helpers` | `call-bind-apply-helpers` | 1.0.2 | MIT |
| `node_modules/call-bound` | `call-bound` | 1.0.4 | MIT |
| `node_modules/callsites` | `callsites` | 3.1.0 | MIT |
| `node_modules/caniuse-lite` | `caniuse-lite` | 1.0.30001733 | CC-BY-4.0 |
| `node_modules/chalk` | `chalk` | 4.1.2 | MIT |
| `node_modules/chownr` | `chownr` | 3.0.0 | BlueOak-1.0.0 |
| `node_modules/client-only` | `client-only` | 0.0.1 | MIT |
| `node_modules/color-convert` | `color-convert` | 2.0.1 | MIT |
| `node_modules/color-name` | `color-name` | 1.1.4 | MIT |
| `node_modules/concat-map` | `concat-map` | 0.0.1 | MIT |
| `node_modules/cross-spawn` | `cross-spawn` | 7.0.6 | MIT |
| `node_modules/csstype` | `csstype` | 3.1.3 | MIT |
| `node_modules/damerau-levenshtein` | `damerau-levenshtein` | 1.0.8 | BSD-2-Clause |
| `node_modules/data-view-buffer` | `data-view-buffer` | 1.0.2 | MIT |
| `node_modules/data-view-byte-length` | `data-view-byte-length` | 1.0.2 | MIT |
| `node_modules/data-view-byte-offset` | `data-view-byte-offset` | 1.0.1 | MIT |
| `node_modules/debug` | `debug` | 4.4.1 | MIT |
| `node_modules/deep-is` | `deep-is` | 0.1.4 | MIT |
| `node_modules/define-data-property` | `define-data-property` | 1.1.4 | MIT |
| `node_modules/define-properties` | `define-properties` | 1.2.1 | MIT |
| `node_modules/detect-libc` | `detect-libc` | 2.1.2 | Apache-2.0 |
| `node_modules/doctrine` | `doctrine` | 2.1.0 | Apache-2.0 |
| `node_modules/dunder-proto` | `dunder-proto` | 1.0.1 | MIT |
| `node_modules/emoji-regex` | `emoji-regex` | 9.2.2 | MIT |
| `node_modules/enhanced-resolve` | `enhanced-resolve` | 5.18.3 | MIT |
| `node_modules/es-abstract` | `es-abstract` | 1.24.0 | MIT |
| `node_modules/es-define-property` | `es-define-property` | 1.0.1 | MIT |
| `node_modules/es-errors` | `es-errors` | 1.3.0 | MIT |
| `node_modules/es-iterator-helpers` | `es-iterator-helpers` | 1.2.1 | MIT |
| `node_modules/es-object-atoms` | `es-object-atoms` | 1.1.1 | MIT |
| `node_modules/es-set-tostringtag` | `es-set-tostringtag` | 2.1.0 | MIT |
| `node_modules/es-shim-unscopables` | `es-shim-unscopables` | 1.1.0 | MIT |
| `node_modules/es-to-primitive` | `es-to-primitive` | 1.3.0 | MIT |
| `node_modules/escape-string-regexp` | `escape-string-regexp` | 4.0.0 | MIT |
| `node_modules/eslint` | `eslint` | 9.33.0 | MIT |
| `node_modules/eslint-config-next` | `eslint-config-next` | 15.5.21 | MIT |
| `node_modules/eslint-import-resolver-node` | `eslint-import-resolver-node` | 0.3.9 | MIT |
| `node_modules/eslint-import-resolver-node/node_modules/debug` | `debug` | 3.2.7 | MIT |
| `node_modules/eslint-import-resolver-typescript` | `eslint-import-resolver-typescript` | 3.10.1 | ISC |
| `node_modules/eslint-module-utils` | `eslint-module-utils` | 2.12.1 | MIT |
| `node_modules/eslint-module-utils/node_modules/debug` | `debug` | 3.2.7 | MIT |
| `node_modules/eslint-plugin-import` | `eslint-plugin-import` | 2.32.0 | MIT |
| `node_modules/eslint-plugin-import/node_modules/debug` | `debug` | 3.2.7 | MIT |
| `node_modules/eslint-plugin-import/node_modules/semver` | `semver` | 6.3.1 | ISC |
| `node_modules/eslint-plugin-jsx-a11y` | `eslint-plugin-jsx-a11y` | 6.10.2 | MIT |
| `node_modules/eslint-plugin-react` | `eslint-plugin-react` | 7.37.5 | MIT |
| `node_modules/eslint-plugin-react-hooks` | `eslint-plugin-react-hooks` | 5.2.0 | MIT |
| `node_modules/eslint-plugin-react/node_modules/resolve` | `resolve` | 2.0.0-next.5 | MIT |
| `node_modules/eslint-plugin-react/node_modules/semver` | `semver` | 6.3.1 | ISC |
| `node_modules/eslint-scope` | `eslint-scope` | 8.4.0 | BSD-2-Clause |
| `node_modules/eslint-visitor-keys` | `eslint-visitor-keys` | 4.2.1 | Apache-2.0 |
| `node_modules/espree` | `espree` | 10.4.0 | BSD-2-Clause |
| `node_modules/esquery` | `esquery` | 1.6.0 | BSD-3-Clause |
| `node_modules/esrecurse` | `esrecurse` | 4.3.0 | BSD-2-Clause |
| `node_modules/estraverse` | `estraverse` | 5.3.0 | BSD-2-Clause |
| `node_modules/esutils` | `esutils` | 2.0.3 | BSD-2-Clause |
| `node_modules/fast-deep-equal` | `fast-deep-equal` | 3.1.3 | MIT |
| `node_modules/fast-glob` | `fast-glob` | 3.3.1 | MIT |
| `node_modules/fast-glob/node_modules/glob-parent` | `glob-parent` | 5.1.2 | ISC |
| `node_modules/fast-json-stable-stringify` | `fast-json-stable-stringify` | 2.1.0 | MIT |
| `node_modules/fast-levenshtein` | `fast-levenshtein` | 2.0.6 | MIT |
| `node_modules/fastq` | `fastq` | 1.19.1 | ISC |
| `node_modules/file-entry-cache` | `file-entry-cache` | 8.0.0 | MIT |
| `node_modules/fill-range` | `fill-range` | 7.1.1 | MIT |
| `node_modules/find-up` | `find-up` | 5.0.0 | MIT |
| `node_modules/flat-cache` | `flat-cache` | 4.0.1 | MIT |
| `node_modules/flatted` | `flatted` | 3.4.2 | ISC |
| `node_modules/for-each` | `for-each` | 0.3.5 | MIT |
| `node_modules/function-bind` | `function-bind` | 1.1.2 | MIT |
| `node_modules/function.prototype.name` | `function.prototype.name` | 1.1.8 | MIT |
| `node_modules/functions-have-names` | `functions-have-names` | 1.2.3 | MIT |
| `node_modules/get-intrinsic` | `get-intrinsic` | 1.3.0 | MIT |
| `node_modules/get-proto` | `get-proto` | 1.0.1 | MIT |
| `node_modules/get-symbol-description` | `get-symbol-description` | 1.1.0 | MIT |
| `node_modules/get-tsconfig` | `get-tsconfig` | 4.10.1 | MIT |
| `node_modules/glob-parent` | `glob-parent` | 6.0.2 | ISC |
| `node_modules/globals` | `globals` | 14.0.0 | MIT |
| `node_modules/globalthis` | `globalthis` | 1.0.4 | MIT |
| `node_modules/gopd` | `gopd` | 1.2.0 | MIT |
| `node_modules/graceful-fs` | `graceful-fs` | 4.2.11 | ISC |
| `node_modules/graphemer` | `graphemer` | 1.4.0 | MIT |
| `node_modules/has-bigints` | `has-bigints` | 1.1.0 | MIT |
| `node_modules/has-flag` | `has-flag` | 4.0.0 | MIT |
| `node_modules/has-property-descriptors` | `has-property-descriptors` | 1.0.2 | MIT |
| `node_modules/has-proto` | `has-proto` | 1.2.0 | MIT |
| `node_modules/has-symbols` | `has-symbols` | 1.1.0 | MIT |
| `node_modules/has-tostringtag` | `has-tostringtag` | 1.0.2 | MIT |
| `node_modules/hasown` | `hasown` | 2.0.2 | MIT |
| `node_modules/ignore` | `ignore` | 5.3.2 | MIT |
| `node_modules/import-fresh` | `import-fresh` | 3.3.1 | MIT |
| `node_modules/imurmurhash` | `imurmurhash` | 0.1.4 | MIT |
| `node_modules/internal-slot` | `internal-slot` | 1.1.0 | MIT |
| `node_modules/is-array-buffer` | `is-array-buffer` | 3.0.5 | MIT |
| `node_modules/is-async-function` | `is-async-function` | 2.1.1 | MIT |
| `node_modules/is-bigint` | `is-bigint` | 1.1.0 | MIT |
| `node_modules/is-boolean-object` | `is-boolean-object` | 1.2.2 | MIT |
| `node_modules/is-bun-module` | `is-bun-module` | 2.0.0 | MIT |
| `node_modules/is-callable` | `is-callable` | 1.2.7 | MIT |
| `node_modules/is-core-module` | `is-core-module` | 2.16.1 | MIT |
| `node_modules/is-data-view` | `is-data-view` | 1.0.2 | MIT |
| `node_modules/is-date-object` | `is-date-object` | 1.1.0 | MIT |
| `node_modules/is-extglob` | `is-extglob` | 2.1.1 | MIT |
| `node_modules/is-finalizationregistry` | `is-finalizationregistry` | 1.1.1 | MIT |
| `node_modules/is-generator-function` | `is-generator-function` | 1.1.0 | MIT |
| `node_modules/is-glob` | `is-glob` | 4.0.3 | MIT |
| `node_modules/is-map` | `is-map` | 2.0.3 | MIT |
| `node_modules/is-negative-zero` | `is-negative-zero` | 2.0.3 | MIT |
| `node_modules/is-number` | `is-number` | 7.0.0 | MIT |
| `node_modules/is-number-object` | `is-number-object` | 1.1.1 | MIT |
| `node_modules/is-regex` | `is-regex` | 1.2.1 | MIT |
| `node_modules/is-set` | `is-set` | 2.0.3 | MIT |
| `node_modules/is-shared-array-buffer` | `is-shared-array-buffer` | 1.0.4 | MIT |
| `node_modules/is-string` | `is-string` | 1.1.1 | MIT |
| `node_modules/is-symbol` | `is-symbol` | 1.1.1 | MIT |
| `node_modules/is-typed-array` | `is-typed-array` | 1.1.15 | MIT |
| `node_modules/is-weakmap` | `is-weakmap` | 2.0.2 | MIT |
| `node_modules/is-weakref` | `is-weakref` | 1.1.1 | MIT |
| `node_modules/is-weakset` | `is-weakset` | 2.0.4 | MIT |
| `node_modules/isarray` | `isarray` | 2.0.5 | MIT |
| `node_modules/isexe` | `isexe` | 2.0.0 | ISC |
| `node_modules/iterator.prototype` | `iterator.prototype` | 1.1.5 | MIT |
| `node_modules/jiti` | `jiti` | 2.5.1 | MIT |
| `node_modules/js-tokens` | `js-tokens` | 4.0.0 | MIT |
| `node_modules/js-yaml` | `js-yaml` | 4.3.0 | MIT |
| `node_modules/json-buffer` | `json-buffer` | 3.0.1 | MIT |
| `node_modules/json-schema-traverse` | `json-schema-traverse` | 0.4.1 | MIT |
| `node_modules/json-stable-stringify-without-jsonify` | `json-stable-stringify-without-jsonify` | 1.0.1 | MIT |
| `node_modules/json5` | `json5` | 1.0.2 | MIT |
| `node_modules/jsx-ast-utils` | `jsx-ast-utils` | 3.3.5 | MIT |
| `node_modules/keyv` | `keyv` | 4.5.4 | MIT |
| `node_modules/language-subtag-registry` | `language-subtag-registry` | 0.3.23 | CC0-1.0 |
| `node_modules/language-tags` | `language-tags` | 1.0.9 | MIT |
| `node_modules/levn` | `levn` | 0.4.1 | MIT |
| `node_modules/lightningcss` | `lightningcss` | 1.30.1 | MPL-2.0 |
| `node_modules/lightningcss-darwin-arm64` | `lightningcss-darwin-arm64` | 1.30.1 | MPL-2.0 |
| `node_modules/lightningcss-darwin-x64` | `lightningcss-darwin-x64` | 1.30.1 | MPL-2.0 |
| `node_modules/lightningcss-freebsd-x64` | `lightningcss-freebsd-x64` | 1.30.1 | MPL-2.0 |
| `node_modules/lightningcss-linux-arm-gnueabihf` | `lightningcss-linux-arm-gnueabihf` | 1.30.1 | MPL-2.0 |
| `node_modules/lightningcss-linux-arm64-gnu` | `lightningcss-linux-arm64-gnu` | 1.30.1 | MPL-2.0 |
| `node_modules/lightningcss-linux-arm64-musl` | `lightningcss-linux-arm64-musl` | 1.30.1 | MPL-2.0 |
| `node_modules/lightningcss-linux-x64-gnu` | `lightningcss-linux-x64-gnu` | 1.30.1 | MPL-2.0 |
| `node_modules/lightningcss-linux-x64-musl` | `lightningcss-linux-x64-musl` | 1.30.1 | MPL-2.0 |
| `node_modules/lightningcss-win32-arm64-msvc` | `lightningcss-win32-arm64-msvc` | 1.30.1 | MPL-2.0 |
| `node_modules/lightningcss-win32-x64-msvc` | `lightningcss-win32-x64-msvc` | 1.30.1 | MPL-2.0 |
| `node_modules/locate-path` | `locate-path` | 6.0.0 | MIT |
| `node_modules/lodash.merge` | `lodash.merge` | 4.6.2 | MIT |
| `node_modules/loose-envify` | `loose-envify` | 1.4.0 | MIT |
| `node_modules/magic-string` | `magic-string` | 0.30.17 | MIT |
| `node_modules/math-intrinsics` | `math-intrinsics` | 1.1.0 | MIT |
| `node_modules/merge2` | `merge2` | 1.4.1 | MIT |
| `node_modules/micromatch` | `micromatch` | 4.0.8 | MIT |
| `node_modules/minimatch` | `minimatch` | 3.1.5 | ISC |
| `node_modules/minimist` | `minimist` | 1.2.8 | MIT |
| `node_modules/minipass` | `minipass` | 7.1.2 | ISC |
| `node_modules/minizlib` | `minizlib` | 3.1.0 | MIT |
| `node_modules/ms` | `ms` | 2.1.3 | MIT |
| `node_modules/nanoid` | `nanoid` | 3.3.15 | MIT |
| `node_modules/napi-postinstall` | `napi-postinstall` | 0.3.2 | MIT |
| `node_modules/natural-compare` | `natural-compare` | 1.4.0 | MIT |
| `node_modules/next` | `next` | 15.5.21 | MIT |
| `node_modules/object-assign` | `object-assign` | 4.1.1 | MIT |
| `node_modules/object-inspect` | `object-inspect` | 1.13.4 | MIT |
| `node_modules/object-keys` | `object-keys` | 1.1.1 | MIT |
| `node_modules/object.assign` | `object.assign` | 4.1.7 | MIT |
| `node_modules/object.entries` | `object.entries` | 1.1.9 | MIT |
| `node_modules/object.fromentries` | `object.fromentries` | 2.0.8 | MIT |
| `node_modules/object.groupby` | `object.groupby` | 1.0.3 | MIT |
| `node_modules/object.values` | `object.values` | 1.2.1 | MIT |
| `node_modules/optionator` | `optionator` | 0.9.4 | MIT |
| `node_modules/own-keys` | `own-keys` | 1.0.1 | MIT |
| `node_modules/p-limit` | `p-limit` | 3.1.0 | MIT |
| `node_modules/p-locate` | `p-locate` | 5.0.0 | MIT |
| `node_modules/parent-module` | `parent-module` | 1.0.1 | MIT |
| `node_modules/path-exists` | `path-exists` | 4.0.0 | MIT |
| `node_modules/path-key` | `path-key` | 3.1.1 | MIT |
| `node_modules/path-parse` | `path-parse` | 1.0.7 | MIT |
| `node_modules/picocolors` | `picocolors` | 1.1.1 | ISC |
| `node_modules/picomatch` | `picomatch` | 2.3.2 | MIT |
| `node_modules/possible-typed-array-names` | `possible-typed-array-names` | 1.1.0 | MIT |
| `node_modules/postcss` | `postcss` | 8.5.16 | MIT |
| `node_modules/prelude-ls` | `prelude-ls` | 1.2.1 | MIT |
| `node_modules/prop-types` | `prop-types` | 15.8.1 | MIT |
| `node_modules/punycode` | `punycode` | 2.3.1 | MIT |
| `node_modules/queue-microtask` | `queue-microtask` | 1.2.3 | MIT |
| `node_modules/react` | `react` | 19.1.0 | MIT |
| `node_modules/react-dom` | `react-dom` | 19.1.0 | MIT |
| `node_modules/react-is` | `react-is` | 16.13.1 | MIT |
| `node_modules/reflect.getprototypeof` | `reflect.getprototypeof` | 1.0.10 | MIT |
| `node_modules/regexp.prototype.flags` | `regexp.prototype.flags` | 1.5.4 | MIT |
| `node_modules/resolve` | `resolve` | 1.22.10 | MIT |
| `node_modules/resolve-from` | `resolve-from` | 4.0.0 | MIT |
| `node_modules/resolve-pkg-maps` | `resolve-pkg-maps` | 1.0.0 | MIT |
| `node_modules/reusify` | `reusify` | 1.1.0 | MIT |
| `node_modules/run-parallel` | `run-parallel` | 1.2.0 | MIT |
| `node_modules/safe-array-concat` | `safe-array-concat` | 1.1.3 | MIT |
| `node_modules/safe-push-apply` | `safe-push-apply` | 1.0.0 | MIT |
| `node_modules/safe-regex-test` | `safe-regex-test` | 1.1.0 | MIT |
| `node_modules/scheduler` | `scheduler` | 0.26.0 | MIT |
| `node_modules/semver` | `semver` | 7.8.5 | ISC |
| `node_modules/set-function-length` | `set-function-length` | 1.2.2 | MIT |
| `node_modules/set-function-name` | `set-function-name` | 2.0.2 | MIT |
| `node_modules/set-proto` | `set-proto` | 1.0.0 | MIT |
| `node_modules/sharp` | `sharp` | 0.35.3 | Apache-2.0 |
| `node_modules/shebang-command` | `shebang-command` | 2.0.0 | MIT |
| `node_modules/shebang-regex` | `shebang-regex` | 3.0.0 | MIT |
| `node_modules/side-channel` | `side-channel` | 1.1.0 | MIT |
| `node_modules/side-channel-list` | `side-channel-list` | 1.0.0 | MIT |
| `node_modules/side-channel-map` | `side-channel-map` | 1.0.1 | MIT |
| `node_modules/side-channel-weakmap` | `side-channel-weakmap` | 1.0.2 | MIT |
| `node_modules/source-map-js` | `source-map-js` | 1.2.1 | BSD-3-Clause |
| `node_modules/stable-hash` | `stable-hash` | 0.0.5 | MIT |
| `node_modules/stop-iteration-iterator` | `stop-iteration-iterator` | 1.1.0 | MIT |
| `node_modules/string.prototype.includes` | `string.prototype.includes` | 2.0.1 | MIT |
| `node_modules/string.prototype.matchall` | `string.prototype.matchall` | 4.0.12 | MIT |
| `node_modules/string.prototype.repeat` | `string.prototype.repeat` | 1.0.0 | MIT |
| `node_modules/string.prototype.trim` | `string.prototype.trim` | 1.2.10 | MIT |
| `node_modules/string.prototype.trimend` | `string.prototype.trimend` | 1.0.9 | MIT |
| `node_modules/string.prototype.trimstart` | `string.prototype.trimstart` | 1.0.8 | MIT |
| `node_modules/strip-bom` | `strip-bom` | 3.0.0 | MIT |
| `node_modules/strip-json-comments` | `strip-json-comments` | 3.1.1 | MIT |
| `node_modules/styled-jsx` | `styled-jsx` | 5.1.6 | MIT |
| `node_modules/supports-color` | `supports-color` | 7.2.0 | MIT |
| `node_modules/supports-preserve-symlinks-flag` | `supports-preserve-symlinks-flag` | 1.0.0 | MIT |
| `node_modules/tailwindcss` | `tailwindcss` | 4.1.11 | MIT |
| `node_modules/tapable` | `tapable` | 2.2.2 | MIT |
| `node_modules/tar` | `tar` | 7.5.19 | BlueOak-1.0.0 |
| `node_modules/tinyglobby` | `tinyglobby` | 0.2.14 | MIT |
| `node_modules/tinyglobby/node_modules/fdir` | `fdir` | 6.4.6 | MIT |
| `node_modules/tinyglobby/node_modules/picomatch` | `picomatch` | 4.0.4 | MIT |
| `node_modules/to-regex-range` | `to-regex-range` | 5.0.1 | MIT |
| `node_modules/ts-api-utils` | `ts-api-utils` | 2.1.0 | MIT |
| `node_modules/tsconfig-paths` | `tsconfig-paths` | 3.15.0 | MIT |
| `node_modules/tslib` | `tslib` | 2.8.1 | 0BSD |
| `node_modules/type-check` | `type-check` | 0.4.0 | MIT |
| `node_modules/typed-array-buffer` | `typed-array-buffer` | 1.0.3 | MIT |
| `node_modules/typed-array-byte-length` | `typed-array-byte-length` | 1.0.3 | MIT |
| `node_modules/typed-array-byte-offset` | `typed-array-byte-offset` | 1.0.4 | MIT |
| `node_modules/typed-array-length` | `typed-array-length` | 1.0.7 | MIT |
| `node_modules/typescript` | `typescript` | 5.9.2 | Apache-2.0 |
| `node_modules/unbox-primitive` | `unbox-primitive` | 1.1.0 | MIT |
| `node_modules/undici-types` | `undici-types` | 6.21.0 | MIT |
| `node_modules/unrs-resolver` | `unrs-resolver` | 1.11.1 | MIT |
| `node_modules/uri-js` | `uri-js` | 4.4.1 | BSD-2-Clause |
| `node_modules/which` | `which` | 2.0.2 | ISC |
| `node_modules/which-boxed-primitive` | `which-boxed-primitive` | 1.1.1 | MIT |
| `node_modules/which-builtin-type` | `which-builtin-type` | 1.2.1 | MIT |
| `node_modules/which-collection` | `which-collection` | 1.0.2 | MIT |
| `node_modules/which-typed-array` | `which-typed-array` | 1.1.19 | MIT |
| `node_modules/word-wrap` | `word-wrap` | 1.2.5 | MIT |
| `node_modules/yallist` | `yallist` | 5.0.0 | BlueOak-1.0.0 |
| `node_modules/yocto-queue` | `yocto-queue` | 0.1.0 | MIT |
