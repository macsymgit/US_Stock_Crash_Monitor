# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Streamlit dashboard that scores S&P 500 (VOO only) crash risk with a transparent six-factor rule model: Buffett indicator, Shiller CAPE, high-yield credit spread, 10Y–2Y Treasury spread, SMA200 deviation, and CNN Fear & Greed. UI text, code comments, and the main README are in Chinese (`README_EN.md` is the English mirror); keep new user-facing strings in Chinese.

## Commands

```bash
python -m pip install -r requirements-dev.txt   # runtime + pytest + ruff
python -m streamlit run app.py                  # http://localhost:8501
python -m pytest -q                             # all tests (testpaths/pythonpath set in pyproject.toml)
python -m pytest tests/test_risk_model.py::<test_name> -q   # single test
python -m ruff check .                          # line-length 100, py310 target
python data_fetcher.py                          # live probe of every data source (reads FRED_API_KEY from .env)
python notifier.py --now                        # one scan now → Telegram (needs TELEGRAM_BOT_TOKEN/CHAT_ID)
docker compose up --build                       # dashboard on host port 8082 + notifier service; mounts repo
```

`FRED_API_KEY` is optional and read from `.env` next to `app.py` (see `.env.example`). Without it, credit spread stays missing.

## Architecture

Data flows one way: `data_fetcher` → `app` (caching + inputs) → `risk_model` (scoring) → `risk_history` (persistence) / `charts` (rendering).

- **`data_fetcher.py`** — all network I/O. Two entry points used by the app: `fetch_market_data(ticker, proxy)` (yfinance adjusted bars; on failure returns a seeded mock frame with `is_mock=True`) and `fetch_macro_data(fred_key, proxy)` (runs independent source groups concurrently in a `ThreadPoolExecutor`, each wrapped with `copy_context()` so the proxy `ContextVar` propagates). Every reading is a dict `{"ok", "value", "source", "error", ...}`; failures never raise to the caller and never substitute default values. Fallback chains (e.g. 10Y: yfinance → CNBC → Eastmoney → FRED) live in the `_fetch_*` helpers. `_http_get` is the single HTTP path: urllib with TLS verification and a per-call `ProxyHandler`, or `curl_cffi` with `impersonate=` for sites that block on TLS fingerprint (GuruFocus). Proxy is never written to process env vars. Values are bounds-checked via `_value(...)`.
- **`app.py`** — the whole Streamlit script (three pages via sidebar radio). Market and macro data have separate `st.cache_data` caches (30 min TTL). Missing readings stay missing; manual values apply only when the per-factor "校准" override checkbox is enabled. Mock prices are excluded from technical scoring. A score is written to history only when data is real, all enabled factors are present, and no manual overrides are used (`provisional` flag).
- **`risk_model.py`** — pure scoring, no I/O. `calculate_risk(inputs, weights, hy_history)` returns `(score | None, details)`; `details["_meta"]` carries coverage, effective weights, contributions, redline floor, resonance boost, and cap adjustment — the UI renders contributions from this, so score and explanation must come from the same call. Zero-weight factors are fully disabled (no score, resonance, or floor); missing factors are dropped and remaining weights renormalized. `HISTORICAL_SCENARIOS` are constructed approximations, not backtests.
- **`risk_history.py`** — CSV store (`risk_history.csv`, gitignored) keyed by market observation date, ticker, `MODEL_VERSION`, and a `scoring_profile` string (normalized weights + credit method). Uses thread lock + file lock + atomic temp-file replace. Trends only show rows matching the current profile; legacy rows are kept in the file.
- **`notifier.py`** — headless scheduler (compose service `notifier`): weekdays at `NOTIFY_TIME` (default 10:00) America/New_York, fetches via `fetch_all`, scores with `DEFAULT_WEIGHTS`, records complete readings to history, sends a per-factor breakdown to Telegram (warning line at `HIGH_RISK_SCORE`). Shares `risk_model.build_factor_inputs` / `credit_method` with `app.py` so both produce identical scores and history profiles.
- **`charts.py` / `ui_theme.py` / `assets/app.css` / `.streamlit/config.toml`** — light theme; palette constants live in `ui_theme.py`.

## Conventions and gotchas

- Changing scoring logic or defaults: bump `MODEL_VERSION` in `risk_model.py` so old history is excluded from trends; the sidebar label in `app.py` (`MODEL / V1.4`) and README version badges are hardcoded separately.
- Tests never hit the network. `tests/test_app.py` drives the real script through `streamlit.testing.v1.AppTest` with `data_fetcher.fetch_market_data` / `fetch_macro_data` monkeypatched and `risk_history.HISTORY_FILE` redirected to `tmp_path`. Follow that pattern for UI tests; fetcher tests monkeypatch the individual `get_*` source functions to exercise fallback chains.
- The score is a model signal, not a crash probability — keep that framing in UI copy and docs.
- Commits should use a GitHub `noreply` author address (see README "Roadmap and contributing").
- Design/roadmap context: `docs/FEATURE_OPTIONS.md`, `docs/UI_REDESIGN.md`, `docs/2026-10-10-project-review.md`.
