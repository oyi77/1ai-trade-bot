import sys, os, pathlib, urllib.request, json
sys.path.insert(0, '.')
from scripts.vilona_tradefx_handler import tg_send

env_path = 'strategies/vilona_tradefx/.env'
token = None
for line in pathlib.Path(env_path).read_text().splitlines():
    if line.startswith('TELEGRAM_BOT_TOKEN='):
        token = line.partition('=')[2]
        break

if not token:
    raise RuntimeError('No Telegram bot token found')

chat_id = '-1003257064212'
caption = """🟢 BUY BTCUSD ₿
━━━━━━━━━━━━━━━━━━━━━━
🕐 2026.06.13 15:55 WIB
📌 SETUP | Conf 49%

📍 Entry: $63750
🔴 SL: $63100 | -650 pip
🟢 TP1: $64500 | +750 pip
🟢 TP2: $65200 | +1500 pip
📊 RR 1:1.4
━━━━━━━━━━━━━━━━━━━━━━
🏛 NEUTRAL | D1 bullish, H4/H1 chop
🧠 Crypto 24/7 bypass — M15 structure intact
⚠️ Risk 1% per trade — verify sendiri."""

chart_path = 'data/btc_chart_mpl.png'

# Use sendPhoto via multipart form
import mimetypes
boundary = '----VilonaFormBoundary7MA4YWxkTrZu0gW'
body = (
    '--' + boundary + '\r\n'
    'Content-Disposition: form-data; name=\"chat_id\"\r\n\r\n' + chat_id + '\r\n'
    '--' + boundary + '\r\n'
    'Content-Disposition: form-data; name=\"caption\"\r\n\r\n' + caption + '\r\n'
    '--' + boundary + '\r\n'
    'Content-Disposition: form-data; name=\"photo\"; filename=\"btc_chart.png\"\r\n'
    'Content-Type: image/png\r\n\r\n'
).encode('utf-8') + pathlib.Path(chart_path).read_bytes() + ('\r\n--' + boundary + '--\r\n').encode('utf-8')

req = urllib.request.Request(
    f'https://api.telegram.org/bot{token}/sendPhoto',
    data=body,
    headers={'Content-Type': 'multipart/form-data; boundary=' + boundary},
    method='POST',
)
with urllib.request.urlopen(req, timeout=15) as r:
    resp = json.loads(r.read())

print(json.dumps({
    'ok': resp.get('ok'),
    'message_id': resp.get('result', {}).get('message_id'),
    'photo_file_id': resp.get('result', {}).get('photo', [{}])[0].get('file_id'),
    'chat_id': resp.get('result', {}).get('chat', {}).get('id'),
}, indent=2))
