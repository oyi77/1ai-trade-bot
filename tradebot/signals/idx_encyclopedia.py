"""
IDX Stock Encyclopedia — sector, industry, and board classification.

Loaded from a local cache of the IDX company listing (958 stocks).
Fetched from ``idx.co.id/primary/ListedCompany/GetCompanyProfiles``.

Usage::

    from tradebot.signals.idx_encyclopedia import (
        get_sector, get_industry, get_board, get_peers,
        resolve_code, is_idx_stock, IDX_STOCKS,
    )
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

LOG = logging.getLogger("tradebot.signals.idx_encyclopedia")

# ── Cache path ─────────────────────────────────────────────────────
try:
    _MODULE_DIR = Path(__file__).resolve().parent
except NameError:
    _MODULE_DIR = Path(os.getcwd()) / "tradebot" / "signals"
CACHE_DIR = _MODULE_DIR.parent.parent / "data" / "idx"
CACHE_FILE = CACHE_DIR / "companies.json"

# ── In-memory index ────────────────────────────────────────────────
_stocks: dict[str, dict[str, str]] = {}
_sectors: dict[str, list[str]] = {}
_loaded: bool = False


def _load() -> None:
    global _stocks, _sectors, _loaded
    if _loaded:
        return

    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE) as f:
                companies = json.load(f)
            if companies:
                _build_index(companies)
                LOG.info("Loaded %d IDX stocks from cache", len(_stocks))
                _loaded = True
                return
            LOG.info("IDX cache empty (%s), using built-in fallback", CACHE_FILE)
        except Exception:
            LOG.warning("Failed to load IDX cache, using built-in fallback")

    _build_fallback()
    _loaded = True


def _build_index(companies: list[dict[str, str]]) -> None:
    for c in companies:
        code = c.get("KodeEmiten", "").upper()
        if not code:
            continue
        _stocks[code] = {
            "name": c.get("NamaEmiten", ""),
            "sector": c.get("Sektor", ""),
            "sub_sector": c.get("SubSektor", ""),
            "board": c.get("PapanPencatatan", ""),
        }
        sub = c.get("SubSektor", "Lainnya")
        _sectors.setdefault(sub, []).append(code)


def _build_fallback() -> None:
    """Minimal built-in fallback with major IDX stocks."""
    fallback: dict[str, tuple[str, str, str, str]] = {
        "BBCA": ("Bank Central Asia Tbk", "Keuangan", "Bank", "Utama"),
        "BBRI": ("Bank Rakyat Indonesia Tbk", "Keuangan", "Bank", "Utama"),
        "BMRI": ("Bank Mandiri Tbk", "Keuangan", "Bank", "Utama"),
        "BBNI": ("Bank Negara Indonesia Tbk", "Keuangan", "Bank", "Utama"),
        "TLKM": ("Telkom Indonesia Tbk", "Infrastruktur", "Telekomunikasi", "Utama"),
        "ASII": ("Astra International Tbk", "Aneka Industri", "Otomotif & Komponen", "Utama"),
        "UNVR": ("Unilever Indonesia Tbk", "Barang Konsumsi", "Kosmetik & Barang Keperluan Rumah Tangga", "Utama"),
        "ICBP": ("Indofood CBP Sukses Makmur Tbk", "Barang Konsumsi", "Makanan & Minuman", "Utama"),
        "INDF": ("Indofood Sukses Makmur Tbk", "Barang Konsumsi", "Makanan & Minuman", "Utama"),
        "ADRO": ("Adaro Energy Indonesia Tbk", "Pertambangan", "Batubara", "Utama"),
        "PTBA": ("Bukit Asam Tbk", "Pertambangan", "Batubara", "Utama"),
        "ANTM": ("Aneka Tambang Tbk", "Pertambangan", "Logam & Mineral", "Utama"),
        "GGRM": ("Gudang Garam Tbk", "Barang Konsumsi", "Rokok", "Utama"),
        "HMSP": ("HM Sampoerna Tbk", "Barang Konsumsi", "Rokok", "Utama"),
        "UNTR": ("United Tractors Tbk", "Perdagangan, Jasa & Investasi", "Perdagangan", "Utama"),
        "KLBF": ("Kalbe Farma Tbk", "Barang Konsumsi", "Farmasi", "Utama"),
        "PGAS": ("Perusahaan Gas Negara Tbk", "Infrastruktur", "Energi", "Utama"),
        "SMGR": ("Semen Indonesia Tbk", "Industri Dasar & Kimia", "Semen", "Utama"),
        "CPIN": ("Charoen Pokphand Indonesia Tbk", "Industri Dasar & Kimia", "Pakan Ternak", "Utama"),
        "AMRT": ("Sumber Alfaria Trijaya Tbk", "Perdagangan, Jasa & Investasi", "Perdagangan Eceran", "Utama"),
        "ACES": ("Ace Hardware Indonesia Tbk", "Perdagangan, Jasa & Investasi", "Perdagangan Eceran", "Utama"),
        "BRIS": ("Bank Syariah Indonesia Tbk", "Keuangan", "Bank", "Utama"),
        "TOWR": ("Sarana Menara Nusantara Tbk", "Infrastruktur", "Telekomunikasi", "Utama"),
        "EXCL": ("XL Axiata Tbk", "Infrastruktur", "Telekomunikasi", "Utama"),
        "ISAT": ("Indosat Tbk", "Infrastruktur", "Telekomunikasi", "Utama"),
        "MTEL": ("Dayamitra Telekomunikasi Tbk", "Infrastruktur", "Telekomunikasi", "Utama"),
        "BUKA": ("Bukalapak.com Tbk", "Teknologi", "Software & IT Services", "Utama"),
        "GOTO": ("GoTo Gojek Tokopedia Tbk", "Teknologi", "Software & IT Services", "Utama"),
        "EMTK": ("Elang Mahkota Teknologi Tbk", "Teknologi", "Media", "Utama"),
        "MDKA": ("Merdeka Copper Gold Tbk", "Pertambangan", "Logam & Mineral", "Utama"),
    }
    for code, (name, sector, sub, board) in fallback.items():
        _stocks[code] = {"name": name, "sector": sector, "sub_sector": sub, "board": board}
        _sectors.setdefault(sub, []).append(code)
    LOG.info("Loaded %d IDX stocks from built-in fallback", len(_stocks))


# ── Public API ──────────────────────────────────────────────────────

def get_sector(symbol: str) -> str:
    code = resolve_code(symbol)
    _load()
    return _stocks.get(code, {}).get("sector", "")


def get_sub_sector(symbol: str) -> str:
    code = resolve_code(symbol)
    _load()
    return _stocks.get(code, {}).get("sub_sector", "")


def get_board(symbol: str) -> str:
    code = resolve_code(symbol)
    _load()
    return _stocks.get(code, {}).get("board", "")


def get_name(symbol: str) -> str:
    code = resolve_code(symbol)
    _load()
    return _stocks.get(code, {}).get("name", code)


def get_peers(symbol: str) -> list[str]:
    """Get stocks in the same sub-sector."""
    code = resolve_code(symbol)
    _load()
    sub = _stocks.get(code, {}).get("sub_sector", "")
    if not sub:
        return []
    return [c for c in _sectors.get(sub, []) if c != code]


def resolve_code(symbol: str) -> str:
    """Convert Yahoo symbol (BBCA.JK) → IDX code (BBCA)."""
    return symbol.replace(".JK", "").replace(".jk", "").upper()


def is_idx_stock(symbol: str) -> bool:
    code = resolve_code(symbol)
    _load()
    return code in _stocks


def stock_count() -> int:
    _load()
    return len(_stocks)


def sector_count() -> int:
    _load()
    return len(_sectors)


# ── Bulk data access ───────────────────────────────────────────────

def all_stocks() -> dict[str, dict[str, str]]:
    _load()
    return dict(_stocks)


def sector_groups() -> dict[str, list[str]]:
    _load()
    return dict(_sectors)
