import pathlib, urllib.request, json
import mplfinance as mpf
import yfinance as yf

out = pathlib.Path('data/btc_chart_mpl.png')

# Fetch BTC 15m
df = yf.Ticker('BTC-USD').history(period='1d', interval='15m')
df.index = df.index.tz_localize(None)

mc = mpf.make_marketcolors(up='#22c55e', down='#ef4444', wick='inherit', edge='inherit')
s = mpf.make_mpf_style(marketcolors=mc, rc={'axes.edgecolor':'#3b4a5e','axes.labelcolor':'#9ca3af','xtick.color':'#9ca3af','ytick.color':'#9ca3af','figure.facecolor':'#0a0a14'})
entry, sl, tp1, tp2 = 63750, 63100, 64500, 65200
fig, axlist = mpf.plot(df, type='candle', style=s, title='BTCUSD M15 BUY', ylabel='Price', returnfig=True, figsize=(10,6), hlines=dict(hlines=[tp1, tp2, entry, sl], colors=['#22c55e','#16a34a','#f59e0b','#ef4444'], linewidths=[1.2,1.2,1.5,1.5], linestyle='--'))
fig.savefig(out, dpi=150, facecolor='#0a0a14', bbox_inches='tight')
print('chart saved:', out.stat().st_size)

# Read token
token = None
for env in ['strategies/vilona_tradefx/.env', '.env']:
    p = pathlib.Path(env)
    if not p.exists():
        continue
    for line in p.read_text(errors='ignore').splitlines():
        if line.startswith('TELEGRAM_BOT_TOKEN=***            token = line.partition('=')[2].strip()
            break
    if token:
        break
if not token:
    raise SystemExit('TELEGRAM_BOT_TOKEN missing')

# Send as photo
boundary = '----VilonaBoundary7MA4YWxk'
parts = []
parts.append(('--' + boundary).encode())
parts.append(b'Content-Disposition: form-data; name="chat_id"\r\n\r\n' + b'-1003257064212')
parts.append(('--' + boundary).encode())
parts.append(b'Content-Disposition: form-data; name="caption"\r\n\r\n' + b'BTCUSD/M15 - BUY - Entry 63750 - SL 63100 - TP1 64500')
parts.append(('--' + boundary).encode())
parts.append(b'Content-Disposition: form-data; name="photo"; filename="btc_chart.png"\r\nContent-Type: image/png\r\n\r\n' + out.read_bytes())
parts.append(('--' + boundary + '--\r\n').encode())

body = b'\r\n'.join(parts)
req = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendPhoto', data=body, headers={'Content-Type': 'multipart/form-data; boundary=' + boundary}, method='POST')
with urllib.request.urlopen(req, timeout=20) as r:
    resp = json.loads(r.read())
    print('message_id=', resp.get('result', {}).get('message_id'))
    print('photo_file_id=', resp.get('result', {}).get('photo', [{}])[0].get('file_id'))
