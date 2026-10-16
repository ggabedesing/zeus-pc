# Increment 015 — Real dashboard

## Goal
Make every screen of the local interface show real machine data with honest
states, working charts and no dead ends. Scope set directly by the owner:
"melhorar o app ao máximo — layout, gráficos, funções, tudo real e funcional".

The planned 015 (transaction crash recovery) was postponed to 016 by owner
request; this increment took its number.

## Backend (read-only only)
- `ZeusDatabase.get_scan(scan_id)` retrieves one stored analysis;
  non-numeric or missing ids return `None`.
- `GET /api/scan/<id>` serves a stored analysis; 404 when absent or the id is
  not numeric.
- `GET /api/report` renders the latest (or `?scan_id=`) analysis as a
  standalone HTML report — `src/zeus_pc/report.py`, pure function
  `build_report_html(scan, version, build)`. All dynamic values are escaped;
  statuses use the same honest labels as the UI; the footer states the report
  is generated from read-only reads. Delivered as an attachment download.
- No new write path, no new system mutation.

## Interface
- Observer is always live: sampling starts on load at 1s, backfilled from
  `/api/observer/history` so charts open full of real data. The old
  start/stop button became a frequency control (2s / 1s / Pausar).
- Charts rewritten: devicePixelRatio-aware canvas, grid lines with scale
  labels, gradient area fill, last-point dot, peak label. CPU, GPU, RAM and a
  new GPU temperature chart.
- Process tables (Top CPU / Top memória) from `/api/observer/processes`,
  refreshed every ≥4s, with total/sampled/protected counts and the sample
  time. The first-sample empty state says it is waiting for the second
  sample instead of showing a fake table.
- Meu PC: hardware hero plus 30 domain cards, each with its honest status
  pill and a scrollable metric/source table — raw JSON stays in the
  technical drawer.
- Home: live metric rows, domain status summary chips
  (OK / parciais / sem leitura / falhas) and the top recommendations.
- Otimizar: a "Gerar plano do motor" button calls
  `/api/optimization/plan` and renders the formal findings with evidence,
  confidence, risk, reversibility and blockers — previously API-only.
- Histórico (new page): the last 25 analyses from SQLite with failure and
  recommendation counts; clicking one loads its full snapshot through
  `/api/scan/<id>`.
- "Exportar relatório" on Início, Histórico and Mais downloads the HTML
  report of the latest analysis.
- After a reload the app restores the latest stored analysis instead of
  starting empty.
- Report tile in Mais explains the report is generated locally and nothing
  leaves the PC.

## Validation on the real machine
- 119 tests green locally (5 new for the report and `get_scan`).
- Live server validated in a real browser, zero console errors on every
  page: home metrics (CPU 9%, RAM 52%, GPU 35°C), charts with real
  fluctuation and seeded history, process tables listing real processes
  (SteelSeries, Discord, Qoder, python3.13), 30 domain cards with real
  values (Windows 11 Pro build 26300, i9-9900, GTX 960, 32 GB), history
  with 7 stored analyses and a working detail view, optimization plan with
  one real MANUAL_REVIEW finding (startup review), power panel intact with
  accented plan names and the transaction journal.
- Pause/resume of the observer verified; the frequency control stops and
  restarts both timers.
- `/api/report` returns HTTP 200 (attachment), `/api/scan/999` and
  `/api/scan/abc` return 404.
- No system mutation was performed during validation; the power flow was
  only opened, not executed.

## Boundaries kept
Read-only probes, no generic shell, strict GUID validation, consent hashed
and single-use, independent VERIFY, server on 127.0.0.1, no external CDN or
new dependency — everything is stdlib Python and vanilla JS.
