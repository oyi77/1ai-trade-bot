"""
Panduan Lengkap Vilona Trade FX — untuk user Indonesia.
Bahasa sederhana, step-by-step, visual-friendly.

Commands:
  /panduan          → Menu utama panduan (pilih topik)
  /cara_analisa     → Cara analisa teknikal
  /cara_baca        → Cara baca sinyal trading
  /cara_pasang      → Cara pasang posisi (entry/SL/TP)
  /cara_ea          → Cara pasang EA & Bridge
  /cara_trailing    → Cara kerja Trailing Stop
  /alasan_sinyal    → Kenapa sinyal keluar (reasoning)
"""

from __future__ import annotations

from typing import Any


# ── Panduan Utama — Menu Pilih Topik ──

def panduan_menu() -> str:
    return (
        "📚 <b>PANDUAN LENGKAP VILONA TRADE FX</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Pilih topik yang ingin kamu pelajari:\n\n"

        "🔍 /cara_analisa — <b>Belajar Analisa Teknikal</b>\n"
        "   Pahami Support/Resistance, Killzone, MTF Matrix\n\n"

        "📖 /cara_baca — <b>Cara Baca Sinyal Trading</b>\n"
        "   Entry, SL, TP, Grade, Confidence, Direction\n\n"

        "🚀 /cara_pasang — <b>Cara Pasang Posisi</b>\n"
        "   Entry di harga berapa, SL dimana, TP dimana\n\n"

        "🤖 /cara_ea — <b>Cara Pasang EA & Bridge</b>\n"
        "   Install EA di MT5, hubungkan ke Bridge Vilona\n\n"

        "🏃 /cara_trailing — <b>Cara Kerja Trailing Stop</b>\n"
        "   Profit lock otomatis, breakeven, step logic\n\n"

        "🧠 /alasan_sinyal — <b>Kenapa Sinyal Keluar?</b>\n"
        "   Algoritma Smart Money, Quality Gate, Killzone\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <i>Semua panduan pakai bahasa sederhana.</i>\n"
        "   <i>Cocok untuk pemula yang baru belajar trading.</i>"
    )


# ── /cara_analisa — Belajar Analisa Teknikal ──

def cara_analisa() -> str:
    return (
        "🔍 <b>CARA ANALISA TEKNIKAL — VILONA METHOD</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "<b>📌 APA ITU ANALISA TEKNIKAL?</b>\n"
        "Membaca pergerakan harga dari chart (grafik) untuk\n"
        "memprediksi kemana harga akan bergerak selanjutnya.\n\n"

        "<b>🧱 1. SUPPORT & RESISTANCE</b>\n"
        "• <b>Support</b>: level harga dimana harga cenderung\n"
        "  berhenti turun dan balik naik (lantai)\n"
        "• <b>Resistance</b>: level harga dimana harga cenderung\n"
        "  berhenti naik dan balik turun (atap)\n"
        "• <i>Ibarat bola pantul: lantai = support, atap = resistance</i>\n\n"

        "<b>🕐 2. KILLZONE (SESI TRADING)</b>\n"
        "• <b>London</b>: 14:00–17:00 WIB — volatilitas SEDANG\n"
        "• <b>New York</b>: 19:00–22:00 WIB — volatilitas TINGGI\n"
        "• <b>Asian</b>: 07:00–10:00 WIB — sideways, HINDARI entry\n"
        "• <i>Emas & Minyak HANYA aktif di London + New York!</i>\n\n"

        "<b>🧬 3. MTF MATRIX (MULTI-TIMEFRAME)</b>\n"
        "Vilona menganalisa 5 timeframe sekaligus:\n"
        "• D1 (Daily) — trend besar (macro)\n"
        "• H4 (4 Jam) — struktur menengah\n"
        "• H1 (1 Jam) — konfirmasi arah\n"
        "• M15 (15 Menit) — entry zone ⭐ (utama!)\n"
        "• M5 (5 Menit) — trigger presisi\n"
        "• <i>Kalau 90% timeframe setuju = SINYAL KUAT!</i>\n\n"

        "<b>📊 4. SMART MONEY CONCEPTS (SMC)</b>\n"
        "• <b>Liquidity Sweep</b>: harga ngejar stop loss trader lain\n"
        "  sebelum berbalik arah (jebakan market maker)\n"
        "• <b>Displacement</b>: candle besar yang menembus struktur\n"
        "  (tanda smart money masuk)\n"
        "• <b>FVG</b>: celah harga yang belum terisi (magnet harga)\n"
        "• <b>Order Block</b>: candle terakhir sebelum harga berbalik\n"
        "  (level entry institusi)\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <b>TIPS PEMULA:</b> Fokus ke 1-2 pair dulu (XAUUSD).\n"
        "   Gunakan timeframe M15. Tunggu sinyal, jangan FOMO."
    )


# ── /cara_baca — Cara Baca Sinyal Trading ──

def cara_baca() -> str:
    return (
        "📖 <b>CARA BACA SINYAL VILONA</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "<b>🎯 CONTOH SINYAL:</b>\n"
        "<pre>"
        "━━━ VILONA SIGNAL ━━━\n"
        "🟢 BUY XAUUSD | Grade B\n"
        "📊 MTF: 4/5 aligned | Conf: 72%\n"
        "📌 Entry: $2,650.50\n"
        "🛑 SL:    $2,645.00 (-55 pips)\n"
        "🎯 TP1:   $2,658.00 (+75 pips)\n"
        "🎯 TP2:   $2,665.00 (+145 pips)\n"
        "⚡ Risk:  1% | RR Ratio: 1:1.4\n"
        "━━━━━━━━━━━━━━━━━━━"
        "</pre>\n\n"

        "<b>📌 CARA BACANYA SATU-SATU:</b>\n\n"

        "<b>🟢 BUY / 🔴 SELL</b>\n"
        "• BUY = prediksi harga NAIK (beli)\n"
        "• SELL = prediksi harga TURUN (jual)\n\n"

        "<b>Grade (A / B / C)</b>\n"
        "• <b>Grade A</b>: MTF 90%+ aligned + SMC valid ⭐⭐⭐\n"
        "• <b>Grade B</b>: MTF 70-89% aligned + SMC valid ⭐⭐\n"
        "• <b>Grade C</b>: MTF 50-69% aligned ⭐\n"
        "• <i>Semakin tinggi Grade, semakin yakin sinyalnya</i>\n\n"

        "<b>Confidence (Conf: 72%)</b>\n"
        "• Persentase keyakinan algoritma (0-100%)\n"
        "• Di atas 70% = sinyal kuat\n"
        "• Di bawah 50% = sinyal lemah, sebaiknya skip\n\n"

        "<b>Entry / SL / TP</b>\n"
        "• <b>Entry</b>: Harga dimana kamu buka posisi\n"
        "• <b>SL (Stop Loss)</b>: Batas rugi maksimum\n"
        "  Kalau harga nyentuh SL = posisi TUTUP OTOMATIS\n"
        "• <b>TP (Take Profit)</b>: Target keuntungan\n"
        "  Kalau harga nyentuh TP = posisi TUTUP OTOMATIS\n\n"

        "<b>RR Ratio (Risk:Reward)</b>\n"
        "• 1:1.4 artinya: risk 1 dapat reward 1.4\n"
        "• RR minimal yang bagus: 1:1.5\n"
        "• Di bawah 1:1 sebaiknya skip\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ <b>INGAT:</b> Tidak ada sinyal yang 100% akurat.\n"
        "   Selalu pakai SL. Jangan all-in satu sinyal."
    )


# ── /cara_pasang — Cara Pasang Posisi ──

def cara_pasang() -> str:
    return (
        "🚀 <b>CARA PASANG POSISI TRADING</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "<b>📱 PILIH PLATFORM:</b>\n"
        "Kamu bisa pasang posisi manual di:\n"
        "• MetaTrader 5 (MT5) — Desktop & Mobile\n"
        "• MetaTrader 4 (MT4)\n"
        "• cTrader\n"
        "• Atau AUTOMATIS dengan EA Vilona (lihat /cara_ea)\n\n"

        "<b>🖥️ LANGKAH-LANGKAH DI MT5:</b>\n\n"

        "<b>1️⃣ Buka Chart Pair</b>\n"
        "• Buka MT5 → Klik kanan \"Market Watch\"\n"
        "• Cari XAUUSD (atau pair dari sinyal)\n"
        "• Klik \"Chart Window\"\n\n"

        "<b>2️⃣ Tekan F9 (New Order)</b>\n"
        "• Atau klik tombol \"New Order\" di toolbar\n"
        "• Atau klik kanan chart → \"Trading\" → \"New Order\"\n\n"

        "<b>3️⃣ Isi Form Order:</b>\n"
        "• <b>Symbol</b>: XAUUSD (sesuai sinyal)\n"
        "• <b>Volume</b>: 0.01 (untuk pemula, modal kecil)\n"
        "• <b>Type</b>: Market Execution\n"
        "• Klik <b>SELL</b> atau <b>BUY</b> (sesuai sinyal)\n\n"

        "<b>4️⃣ Pasang SL & TP:</b>\n"
        "• Setelah posisi terbuka, klik kanan posisi\n"
        "• Pilih \"Modify or Delete Order\"\n"
        "• Isi:\n"
        "  — <b>Stop Loss</b>: harga SL dari sinyal\n"
        "  — <b>Take Profit</b>: harga TP dari sinyal\n"
        "• Klik \"Modify\"\n\n"

        "<b>📐 CARA HITUNG LOT SIZE (UNTUK PEMULA):</b>\n"
        "• Modal $100 → Lot 0.01 (mikro)\n"
        "• Modal $500 → Lot 0.03\n"
        "• Modal $1,000 → Lot 0.05\n"
        "• <i>JANGAN pakai lot gede kalau modal kecil!</i>\n\n"

        "<b>⏰ KAPAN ENTRY?</b>\n"
        "• Pasang posisi SEGERA setelah sinyal keluar\n"
        "• Jangan tunda > 15 menit — harga udah berubah\n"
        "• Kalau sudah lewat 30 menit = sinyal expired\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <b>TIPS:</b> Latihan dulu pakai AKUN DEMO gratis.\n"
        "   Jangan langsung real money kalau belum paham."
    )


# ── /cara_ea — Cara Pasang EA & Bridge ──

def cara_ea() -> str:
    return (
        "🤖 <b>CARA PASANG EA & BRIDGE VILONA</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "<b>❓ APA ITU EA (EXPERT ADVISOR)?</b>\n"
        "EA adalah robot trading yang jalan di MT5.\n"
        "Dia terima sinyal dari Vilona Bridge, lalu\n"
        "BUKA & TUTUP posisi OTOMATIS tanpa kamu sentuh.\n\n"

        "<b>🔌 APA ITU BRIDGE?</b>\n"
        "Bridge adalah jembatan antara Sinyal Vilona → EA.\n"
        "Sinyal dikirim via internet ke Bridge, lalu Bridge\n"
        "teruskan ke EA kamu di MT5.\n\n"

        "<b>📥 CARA PASANG (5 LANGKAH):</b>\n\n"

        "<b>1️⃣ Download EA Vilona</b>\n"
        "• Minta file EA ke admin: /mykey\n"
        "• Dapatkan <b>EA License Key</b> (kode aktivasi)\n"
        "• Download file <code>VilonaTradeFX.ex5</code>\n\n"

        "<b>2️⃣ Install EA di MT5</b>\n"
        "• Buka MT5 → File → Open Data Folder\n"
        "• Masuk folder <code>MQL5/Experts/</code>\n"
        "• Copy file <code>VilonaTradeFX.ex5</code> ke folder itu\n"
        "• Restart MT5\n"
        "• EA akan muncul di Navigator → Expert Advisors\n\n"

        "<b>3️⃣ Aktifkan EA di Chart</b>\n"
        "• Buka chart XAUUSD (atau pair yang di-support)\n"
        "• Drag <code>VilonaTradeFX</code> dari Navigator ke chart\n"
        "• Centang \"Allow Auto Trading\"\n"
        "• Di tab \"Common\":\n"
        "  — <b>LicenseKey</b>: isi kode dari /mykey\n"
        "  — <b>BridgeURL</b>: http://YOUR_SERVER:8765\n"
        "  — <b>AutoTrade</b>: true\n"
        "  — <b>MaxRiskPercent</b>: 1.0 (rekomendasi)\n"
        "• Klik OK\n\n"

        "<b>4️⃣ Verifikasi EA Aktif</b>\n"
        "• Cek pojok kanan atas chart: ada icon EA 😊\n"
        "• Kalau senyum = EA AKTIF\n"
        "• Kalau silang X = EA ERROR, cek setting\n\n"

        "<b>5️⃣ Aktifkan Bridge</b>\n"
        "• Kirim command /autotrade ke Bot Vilona\n"
        "• Bot akan daftarkan akun kamu ke Bridge\n"
        "• EA akan otomatis fetch sinyal dari Bridge\n\n"

        "<b>🔄 CARA KERJA EA:</b>\n"
        "<pre>"
        "Sinyal → Bridge → EA → MT5 → Posisi Terbuka\n"
        "                           ↓\n"
        "                  Trailing Stop Aktif\n"
        "                           ↓\n"
        "            TP Tercapai → Posisi Tertutup\n"
        "            SL Tercapai → Posisi Tertutup\n"
        "</pre>\n\n"

        "<b>⚙️ PARAMETER EA PENTING:</b>\n"
        "• <b>MaxRiskPercent</b>: Maksimal risiko per trade (1%)\n"
        "• <b>MaxPositions</b>: Maksimal posisi buka bersamaan (2)\n"
        "• <b>TrailingEnabled</b>: Aktifkan trailing stop (true)\n"
        "• <b>TrailingStartPips</b>: Profit berapa pips baru trailing aktif\n"
        "• <b>TrailingStepPips</b>: Jarak trailing ikuti harga\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ <b>SYARAT:</b> MT5 harus nyala 24 jam.\n"
        "   Bisa pakai VPS murah (Rp 50rb/bulan).\n"
        "💡 /cara_trailing — Pelajari cara trailing stop."
    )


# ── /cara_trailing — Cara Kerja Trailing Stop ──

def cara_trailing() -> str:
    return (
        "🏃 <b>CARA KERJA TRAILING STOP</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "<b>❓ APA ITU TRAILING STOP?</b>\n"
        "Trailing Stop adalah fitur yang mengunci profit\n"
        "secara OTOMATIS saat harga bergerak sesuai arah.\n"
        "SL (Stop Loss) akan \"mengikuti\" harga naik/turun\n"
        "sehingga profit kamu TIDAK HILANG kalau harga balik.\n\n"

        "<b>📐 CARA KERJA — SIMULASI:</b>\n"
        "Misal: BUY XAUUSD, Entry $2,650, SL awal $2,645\n\n"

        "<b>1. Harga naik ke $2,655 (+50 pips):</b>\n"
        "   → SL digeser ke $2,650 (BREAKEVEN)\n"
        "   → Sekarang kamu GAK MUNGKIN RUGI\n\n"

        "<b>2. Harga naik lagi ke $2,660 (+100 pips):</b>\n"
        "   → SL digeser ke $2,655 (lock profit +50 pips)\n"
        "   → Profit minimal udah dikunci\n\n"

        "<b>3. Harga naik ke $2,665 (+150 pips):</b>\n"
        "   → SL digeser ke $2,660 (lock profit +100 pips)\n"
        "   → Terus ngikutin selama harga masih naik\n\n"

        "<b>4. Harga turun balik ke $2,660:</b>\n"
        "   → SL tersentuh → Posisi TUTUP otomatis\n"
        "   → Profit +100 pips berhasil dikunci! 💰\n\n"

        "<b>⚙️ PARAMETER TRAILING:</b>\n"
        "• <b>TrailingStartPips</b> (default: 50):\n"
        "  Profit minimal berapa pips baru trailing AKTIF\n"
        "• <b>TrailingStepPips</b> (default: 25):\n"
        "  Berapa pips SL ikuti harga setiap kali naik\n"
        "• <b>BreakevenPips</b> (default: 30):\n"
        "  Di profit berapa SL digeser ke harga entry\n\n"

        "<b>📊 ILUSTRASI:</b>\n"
        "<pre>"
        "Harga ↑ $2,665 ─── TP TERCAPAI 🎯\n"
        "     ↑ $2,660 ─── SL bergeser (lock +100)\n"
        "     ↑ $2,655 ─── SL bergeser (lock +50)\n"
        "     ↑ $2,650 ─── BREAKEVEN (SL = entry)\n"
        "Entry $2,650 ─── BUKA POSISI\n"
        "     ↓ $2,645 ─── SL AWAL (-50 pips)\n"
        "</pre>\n\n"

        "<b>✅ KEUNTUNGAN TRAILING:</b>\n"
        "• Profit otomatis dikunci\n"
        "• Tidak perlu pantau chart 24 jam\n"
        "• Menghindari \"udah profit malah balik SL\"\n"
        "• Cocok untuk trend panjang\n\n"

        "<b>❌ RISIKO TRAILING:</b>\n"
        "• Kalau step terlalu kecil → gampang ke-trigger noise\n"
        "• Kalau step terlalu besar → profit banyak yang hilang\n"
        "• Pakai setting DEFAULT dulu, jangan diutak-atik\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 Trailing otomatis jalan kalau EA terhubung.\n"
        "   Cek /cara_ea untuk cara pasang EA."
    )


# ── /alasan_sinyal — Kenapa Sinyal Keluar ──

def alasan_sinyal() -> str:
    return (
        "🧠 <b>KENAPA SINYAL INI KELUAR?</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "<b>🔬 ALGORITMA DI BALIK SINYAL:</b>\n"
        "Vilona bukan nebak. Dia analisa pakai:\n"
        "• 11 mesin analisa (Engine) berjalan paralel\n"
        "• MTF Matrix 5 timeframe (D1/H4/H1/M15/M5)\n"
        "• Smart Money Concepts (SMC)\n"
        "• Quality Gate (penyaring kualitas)\n\n"

        "<b>🧩 11 ENGINE YANG BEKERJA:</b>\n"
        "1. SMC Engine — Liquidity sweep, FVG, Order Block\n"
        "2. FVG Engine — Fair Value Gap detection\n"
        "3. Liquidity Engine — Cari level stop loss cluster\n"
        "4. Sweep Engine — Deteksi jebakan market maker\n"
        "5. Chaos Engine — Fractal & Alligator Williams\n"
        "6. CRT/TBS Engine — Candle Range Theory\n"
        "7. TV Engine — TradingView indikator teknikal\n"
        "8. Quant Engine — Quantitative momentum model\n"
        "9. Hermes Engine — AI consensus reasoning\n"
        "10. Layering Engine — Multiple timeframe confluence\n"
        "11. Session Engine — Killzone timing + level\n\n"

        "<b>🏆 QUALITY GATE (PENYARING):</b>\n"
        "Setelah 11 engine analisa, hasilnya masuk QUALITY GATE:\n\n"

        "<b>✅ LULUS → SINYAL KELUAR kalau:</b>\n"
        "• Minimal 50% engine setuju arah (BUY/SELL)\n"
        "• MTF Matrix minimal 3 dari 5 timeframe aligned\n"
        "• Ada valid SMC setup (liquidity sweep + FVG/OB)\n"
        "• Dalam jam Killzone aktif (Forex/Metal)\n"
        "• Risk:Reward ratio di atas 1:1\n\n"

        "<b>❌ DIBLOCK → SINYAL TIDAK KELUAR kalau:</b>\n"
        "• Engine consensus di bawah 50%\n"
        "• Killzone tidak aktif (misal: XAUUSD di jam Asian)\n"
        "• Tidak ada SMC setup yang valid\n"
        "• Harga lagi sideways / choppy\n"
        "• RR ratio di bawah 1:1\n\n"

        "<b>🪙 ROUTING PER ASSET:</b>\n"
        "• <b>XAUUSD, USOIL</b> → HANYA London & NY Killzone\n"
        "  Asian session = DIAM, tidak keluar sinyal\n"
        "• <b>BTCUSD, ETHUSD</b> → 24/7, tidak peduli Killzone\n"
        "  Bisa keluar sinyal kapan saja kalau SMC valid\n\n"

        "<b>📊 MTF MATRIX ALIGNMENT:</b>\n"
        "• D1 (Daily): trend besar — bullish atau bearish?\n"
        "• H4: konfirmasi struktur menengah\n"
        "• H1: arah momentum jangka pendek\n"
        "• M15: zona entry (timeframe UTAMA Vilona)\n"
        "• M5: trigger candle confirmation\n"
        "• Kalau 5/5 aligned + SMC valid = SINYAL GRADE A!\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <b>INTINYA:</b> Sinyal keluar bukan karena feeling.\n"
        "   Tapi karena 11 engine + MTF Matrix + Quality Gate\n"
        "   SETUJU bahwa ada peluang bagus.\n"
        "   Kalau gak setuju = sinyal gak keluar = safety first."
    )


# ── Dictionary untuk command routing ──

PANDUAN_COMMANDS: dict[str, Any] = {
    "panduan":      panduan_menu,
    "cara_analisa": cara_analisa,
    "cara_baca":    cara_baca,
    "cara_pasang":  cara_pasang,
    "cara_ea":      cara_ea,
    "cara_trailing":cara_trailing,
    "alasan_sinyal":alasan_sinyal,
}
