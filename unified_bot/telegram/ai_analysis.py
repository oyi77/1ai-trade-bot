"""
AI Analysis Engine — connects to local Ollama for real market analysis.
Uses OpenAI-compatible API via Ollama at localhost:11434/v1
"""
from __future__ import annotations
import json, logging, os
from datetime import datetime
from openai import OpenAI

LOG = logging.getLogger(__name__)

OLLAMA_BASE = os.getenv("OLLAMA_HOST", "http://localhost:11434/v1")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
FALLBACK_MODEL = "gemma3:4b"

client = OpenAI(base_url=OLLAMA_BASE, api_key="ollama")  # Ollama ignores api_key

MARKET_CONTEXT = """
Anda adalah analis pasar keuangan profesional dengan spesialisasi analisa teknikal.
Tugas Anda: menganalisis aset trading dan memberikan sinyal.

Gunakan pendekatan analisa:
1. SMC (Smart Money Concept) — liquidity, order block, FVG
2. Price Action — support/resistance, candlestick pattern
3. Indikator — Overbought/oversold, divergence
4. Market Structure — trend, range, breakout

Format RESPON WAJIB JSON:
{
    "direction": "BUY or SELL or WAIT",
    "confidence": 0-100,
    "entry": "harga entry",
    "stop_loss": "harga SL",
    "take_profit": "harga TP",
    "analysis": "Penjelasan analisa singkat 2-3 kalimat",
    "key_levels": ["level1", "level2"],
    "risk": "HIGH/MEDIUM/LOW"
}
"""


async def analyze_market(symbol: str, timeframe: str = "1H") -> dict:
    """Analyze a trading symbol using local LLM."""
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    prompt = (
        f"Analisa pasar {symbol} untuk timeframe {timeframe} pada {today}.\n\n"
        f"Data pasar terkini:\n"
        f"- Simbol: {symbol}\n"
        f"- Timeframe: {timeframe}\n"
        f"- Tanggal: {today}\n\n"
        f"Berikan analisa teknikal lengkap dan sinyal trading.\n"
        f"RESPON HARUS JSON (tanpa markdown formatting)."
    )

    for model in [OLLAMA_MODEL, FALLBACK_MODEL]:
        try:
            LOG.info("Analyzing %s with %s...", symbol, model)
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": MARKET_CONTEXT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=500,
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content.strip()
            LOG.info("Ollama response received (%d chars)", len(raw))
            return _parse_response(raw, symbol)
        except Exception as e:
            LOG.warning("Model %s failed: %s", model, e)
            continue

    return _fallback_analysis(symbol)


def _parse_response(raw: str, symbol: str) -> dict:
    """Parse the LLM response, extracting JSON."""
    # Try direct JSON parse
    try:
        data = json.loads(raw)
        return {
            "symbol": symbol,
            "direction": data.get("direction", "WAIT"),
            "confidence": min(100, max(0, int(data.get("confidence", 50)))),
            "entry": str(data.get("entry", "—")),
            "stop_loss": str(data.get("stop_loss", "—")),
            "take_profit": str(data.get("take_profit", "—")),
            "analysis": data.get("analysis", "Analisa tidak tersedia"),
            "key_levels": data.get("key_levels", []),
            "risk": data.get("risk", "MEDIUM"),
            "model": "AI",
        }
    except (json.JSONDecodeError, ValueError):
        pass

    # Try extracting JSON from markdown
    import re
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            return {
                "symbol": symbol,
                "direction": data.get("direction", "WAIT"),
                "confidence": min(100, max(0, int(data.get("confidence", 50)))),
                "entry": str(data.get("entry", "—")),
                "stop_loss": str(data.get("stop_loss", "—")),
                "take_profit": str(data.get("take_profit", "—")),
                "analysis": data.get("analysis", "Analisa tidak tersedia"),
                "key_levels": data.get("key_levels", []),
                "risk": data.get("risk", "MEDIUM"),
                "model": "AI",
            }
        except (json.JSONDecodeError, ValueError):
            pass

    return _fallback_analysis(symbol)


def _fallback_analysis(symbol: str) -> dict:
    """Fallback when AI is unavailable."""
    return {
        "symbol": symbol,
        "direction": "WAIT",
        "confidence": 0,
        "entry": "—",
        "stop_loss": "—",
        "take_profit": "—",
        "analysis": "⚠️ AI Engine tidak tersedia. Coba lagi nanti.",
        "key_levels": [],
        "risk": "MEDIUM",
        "model": "FALLBACK",
    }
