# Backend + Frontend Handoff
## Consolidated Execution Artifacts

> Source of truth: `/home/openclaw/projects/1ai-trade-bot/`  
> Runtime model: `tradebot/app.py`  
> Web layer: `tradebot/web/server.py`  
> Bot layer: `tradebot/bots/platforms/vilona/bot.py`

---

## At a Glance

- **1 app process** — `python -m tradebot` (`tradebot/app.py`)
- **1 FastAPI server** on **port 9090** — `tradebot/web/server.py`
- **1 Telegram bot class** — `VilonaBot`
- **1 backend intentionally** — no duplicate dashboards, no legacy aliases
- **1 frontend intentionally** — Jinja2 templates + public JSON APIs; no SPA, no build step

---

## BACKEND — `docs/backend-abstraction.md`

Covers:
- runtime topology and process contracts
- real public/admin route inventory
- service ownership table
- tight coupling points and “API by convention”
- deployment boundary (systemd)
- open loads and validation checklist

Critical invariants:
- `run_engine_consensus()` is cached 120s per symbol
- 12 public APIs + 3 admin pages are the only stable web boundaries
- `members_service` + payment webhook are sole donor state writers
- `.gitignore` and AGENTS.md already cover sensitive/runtime file exclusion rules

---

## FRONTEND — `docs/frontend-abstraction.md`

Covers:
- page route map with auth contract
- API -> JSON endpoint inventory
- editable surfaces (copy, layout, pricing, donor feed, Tripay form)
- deploy / ops steps

Key facts:
- Templates live under `tradebot/web/templates/` (10 files)
- No bundler, no build step, no node_modules
- Admin auth via server-side session cookie

---

## HOW TO USE THESE DOCS

| Role | Read |
|---|---|
| Engineering | `backend-abstraction.md` |
| Frontend / content | `frontend-abstraction.md` |
| QA | Both backend + frontend + AGENTS.md |
| New dev onboarding | Start here, then `AGENTS.md`, then module-by-module |

---

## CHANGE CONTRACT

Backend is considered changed when:
- any route in `tradebot/web/server.py` is added/removed/renamed
- any service module under `tradebot/services/` is changed
- `consensus_service.py` cache contract changes
- payer state transitions in `payment.py` or `members_service.py`

Frontend is considered changed when:
- any template under `tradebot/web/templates/` is changed
- any public API response schema on the frontend page changes
- admin auth flow changes

When backend changes, update `docs/backend-abstraction.md` in the same change.  
When frontend changes, update `docs/frontend-abstraction.md` in the same change.

---

*(Generated from actual runtime code, not a synthetic template.)*
