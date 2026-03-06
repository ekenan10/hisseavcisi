from flask import Flask, jsonify, request
from flask_cors import CORS
import yfinance as yf
import json
from datetime import datetime
import threading
import time

app = Flask(__name__)
CORS(app)  # Tüm origin'lere izin ver

# ─── Cache ───────────────────────────────────────────
price_cache = {}   # {sym: {price, change, prevClose, ts}}
fin_cache   = {}   # {sym: {pe, pb, ...}}
PRICE_TTL   = 60   # saniye
FIN_TTL     = 3600 # 1 saat

def cache_valid(cache, sym, ttl):
    c = cache.get(sym)
    return c and (time.time() - c.get('_ts', 0)) < ttl

# ─── Fiyat endpoint ──────────────────────────────────
@app.route('/api/prices')
def get_prices():
    syms = request.args.get('symbols', '').split(',')
    syms = [s.strip().upper() for s in syms if s.strip()]
    if not syms:
        return jsonify({'error': 'symbols parametresi gerekli'}), 400

    result = {}
    fresh = [s for s in syms if not cache_valid(price_cache, s, PRICE_TTL)]

    if fresh:
        try:
            tickers = [s + '.IS' for s in fresh]
            data = yf.download(
                tickers, period='2d', interval='1d',
                auto_adjust=True, progress=False, threads=True
            )
            import pandas as pd
            for sym in fresh:
                try:
                    tick = yf.Ticker(sym + '.IS')
                    info = tick.fast_info
                    price      = float(info.last_price or 0)
                    prev_close = float(info.previous_close or 0)
                    change     = ((price - prev_close) / prev_close * 100) if prev_close else 0
                    volume     = int(info.three_month_average_volume or 0)
                    price_cache[sym] = {
                        'price': round(price, 2),
                        'prevClose': round(prev_close, 2),
                        'change': round(change, 2),
                        'volume': volume,
                        '_ts': time.time()
                    }
                except Exception as e:
                    price_cache[sym] = {'error': str(e), '_ts': time.time()}
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    for sym in syms:
        c = price_cache.get(sym, {})
        result[sym] = {k: v for k, v in c.items() if not k.startswith('_')}

    return jsonify({'prices': result, 'ts': datetime.now().strftime('%H:%M:%S')})


# ─── Bilanço endpoint ─────────────────────────────────
@app.route('/api/fundamentals/<sym>')
def get_fundamentals(sym):
    sym = sym.upper()
    if cache_valid(fin_cache, sym, FIN_TTL):
        data = {k: v for k, v in fin_cache[sym].items() if not k.startswith('_')}
        return jsonify({'data': data, 'cached': True})

    try:
        tick = yf.Ticker(sym + '.IS')
        info = tick.info

        price      = info.get('currentPrice') or info.get('regularMarketPrice', 0)
        pe         = info.get('trailingPE')
        pb         = info.get('priceToBook')
        eps        = info.get('trailingEps')
        bvps       = info.get('bookValue')
        div_yield  = (info.get('dividendYield') or 0) * 100
        roe        = (info.get('returnOnEquity') or 0) * 100
        roa        = (info.get('returnOnAssets') or 0) * 100
        revenue    = info.get('totalRevenue')
        net_income = info.get('netIncomeToCommon')
        equity     = info.get('bookValue', 0) * info.get('sharesOutstanding', 0) if info.get('bookValue') else None
        debt_eq    = (info.get('debtToEquity') or 0) / 100
        curr_ratio = info.get('currentRatio')
        market_cap = info.get('marketCap')
        shares     = info.get('sharesOutstanding')

        graham = None
        ptg    = None
        if eps and bvps and eps > 0 and bvps > 0:
            graham = round((22.5 * eps * bvps) ** 0.5, 2)
            ptg    = round(price / graham, 2) if (graham and price) else None

        data = {
            'price': price, 'pe': pe, 'pb': pb, 'eps': eps, 'bvps': bvps,
            'divYield': round(div_yield, 2) if div_yield else None,
            'roe': round(roe, 2) if roe else None,
            'roa': round(roa, 2) if roa else None,
            'revenue': revenue, 'netIncome': net_income,
            'equity': equity, 'debtEq': round(debt_eq, 2) if debt_eq else None,
            'currentRatio': curr_ratio, 'marketCap': market_cap, 'shares': shares,
            'graham': graham, 'priceToGraham': ptg,
            'name': info.get('longName', sym),
            'sector': info.get('sector', ''),
            'ts': datetime.now().strftime('%H:%M:%S')
        }

        fin_cache[sym] = {**data, '_ts': time.time()}
        return jsonify({'data': data, 'cached': False})

    except Exception as e:
        return jsonify({'error': str(e), 'sym': sym}), 500


# ─── Health check ─────────────────────────────────────
@app.route('/')
def index():
    return jsonify({
        'status': 'ok',
        'name': 'Hisse Avcısı API',
        'endpoints': ['/api/prices?symbols=THYAO,GARAN', '/api/fundamentals/THYAO']
    })

@app.route('/api/health')
def health():
    return jsonify({'status': 'ok', 'ts': datetime.now().isoformat()})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
