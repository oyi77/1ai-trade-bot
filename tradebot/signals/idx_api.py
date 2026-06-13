"""
IDX (Indonesia Stock Exchange) direct data source.

⚠️  Most IDX endpoints are now Cloudflare-protected (403).
This module provides the access patterns that still work, plus
documentation of all known endpoints for future use.

Currently working:
    * Company listing (paginated, 958 stocks)

Blocked (Cloudflare 403):
    * Trading info (OHLCV)
    * Company detail (profiles, directors)
    * Financial ratios

For enriched stock data, use :mod:`tradebot.signals.idx_enricher` which
combines Yahoo Finance fundamentals with :mod:`tradebot.signals.idx_encyclopedia`.

Endpoint reference (from community repos):
    - Company list:    /primary/ListedCompany/GetCompanyProfiles
    - Company detail:  /primary/ListedCompany/GetCompanyProfilesDetail
    - Trading info:    /umbraco/Surface/ListedCompany/GetTradingInfoSS
    - Financials:      /primary/DigitalStatistic/GetApiDataPaginated
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

from curl_cffi import requests

LOG = logging.getLogger("tradebot.signals.idx_api")

BASE_URL = "https://idx.co.id"
COMPANY_LIST_URL = f"{BASE_URL}/primary/ListedCompany/GetCompanyProfiles"

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "referer": "https://idx.co.id/id/data-pasar/ringkasan-perdagangan/",
}

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "idx"


async def fetch_company_list(page_size: int = 100) -> list[dict[str, str]]:
    """Fetch full IDX company listing (paginated, async-safe).

    Returns list of dicts with keys: KodeEmiten, NamaEmiten, Sektor,
    SubSektor, PapanPencatatan, Alamat, etc.

    Caches result to ``data/idx/companies.json``.
    """
    cache_file = CACHE_DIR / "companies.json"
    if cache_file.exists():
        try:
            with open(cache_file) as f:
                data = json.load(f)
            if data:
                LOG.info("Loaded %d companies from cache", len(data))
                return data
        except Exception as e:
            LOG.warning("Silent exception caught: %s", e)

    all_companies: list[dict[str, str]] = []
    page = 0

    while True:
        url = f"{COMPANY_LIST_URL}?start={page * page_size}&length={page_size}"
        try:
            resp = await asyncio.to_thread(
                lambda: requests.Session().get(
                    url, headers=HEADERS, impersonate="chrome", timeout=30
                )
            )
            if resp.status_code != 200:
                LOG.warning("IDX page %d: HTTP %d", page, resp.status_code)
                break

            data = resp.json()
            items = data.get("data", [])
            if not items:
                break

            all_companies.extend(items)
            LOG.info("Page %d: %d companies (total %d)", page, len(items), len(all_companies))
            page += 1

            # Rate limit — fresh session per page
            await asyncio.sleep(2)

        except Exception as exc:
            LOG.warning("IDX page %d failed: %s", page, exc)
            break

    if all_companies:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(cache_file, "w") as f:
            json.dump(all_companies, f, indent=2)
        LOG.info("Cached %d companies to %s", len(all_companies), cache_file)

    return all_companies
