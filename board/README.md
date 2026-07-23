# Research Market Board Delivery

Final delivery folder for the daily research market board. All user-facing artifacts for this task are kept under this `board` directory.

## Delivered artifacts

- `数据看板UI与日更SOP.docx`: UI plan, charting rules, daily update SOP, deployment/quality checklist, and 7 dashboard screenshots.
- `数据看板指标变量表.docx`: 367-field variable dictionary with module, submodule, API/source, frequency, unit, meaning, validation grade, fallback source, quality rule, and chart role.
- `research_market_board_release.zip`: clean source package for the deployable dashboard project.
- `public_dashboard/`: deployable Flask dashboard source, notebook, tests, deployment scripts, catalog, static assets, and formal evidence.

## Production URL

- https://desktop-i22b489.tailf9d7ac.ts.net:10007/
- Remote production root: `F:\apps\research_market_board`
- Local service binding on remote host: `127.0.0.1:8070`

## Latest verification summary

- Public HTTP gate: root, security headers, livez, healthz, snapshot, catalog, coverage, series, and stock APIs all passed.
- Local pytest: 66 passed.
- Remote release validation: passed.
- Playwright UI regression: six modules, five views, multi-indicator controls, CSV export, saved-view create/delete, stock K-line, stock lookup, news table, and console checks passed.
- Current public snapshot: 367 catalog rows, 467 runtime series, 33 charts, 7 tables; generated at `2026-07-12T09:08:05Z`.

## Evidence

- `public_dashboard/deployment_evidence/deployment-validation.json`
- `public_dashboard/deployment_evidence/playwright-validation.json`
- `public_dashboard/deployment_evidence/free_api_validation.json`
- 7 formal UI screenshots in `public_dashboard/deployment_evidence/`

## QA note

LibreOffice was not installed on this machine, and Word COM export hung during DOCX rasterization. The DOCX files were therefore verified structurally with `python-docx`: openability, section geometry, table/image counts, 367 variable rows, and sensitive-literal scans. This limitation is recorded in the final handoff rather than hidden.
