from flask import Flask, jsonify, request
from flask_cors import CORS
import yfinance as yf
import time
from datetime import datetime

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

price_cache = {}
fin_cache   = {}
PRICE_TTL   = 120

def cache_ok(cache, sym, ttl):
    c = cache.get(sym)
    return c and (time.time() - c.get('_ts', 0)) < ttl

@app.after_request
def add_cors(r):
    r.headers['Access-Control-Allow-Origin'] = '*'
    r.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    r.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return r

@app.route('/')
def index():
    return jsonify({'status': 'ok', 'name': 'Hisse Avcısı API'})

@app.route('/api/health')
def health():
    return jsonify({'status': 'ok', 'ts': datetime.now().isoformat()})

@app.route('/api/prices')
def get_prices():
    syms = [s.strip().upper() for s in request.args.get('symbols','').split(',') if s.strip()]
    if not syms:
        return jsonify({'error': 'symbols gerekli'}), 400

    result = {}
    fresh = [s for s in syms if not cache_ok(price_cache, s, PRICE_TTL)]

    if fresh:
        # Küçük gruplarda çek — rate limit dostu
        for i in range(0, len(fresh), 5):
            chunk = fresh[i:i+5]
            tickers_str = ' '.join([s + '.IS' for s in chunk])
            try:
                data = yf.download(
                    tickers_str, period='2d', interval='1d',
                    auto_adjust=True, progress=False, threads=False
                )
                import pandas as pd
                if data.empty:
                    continue
                if isinstance(data.columns, pd.MultiIndex):
                    closes = data['Close'] if 'Close' in data.columns.get_level_values(0) else data
                    for sym in chunk:
                        try:
                            col = sym + '.IS'
                            if col not in closes.columns:
                                continue
                            vals = closes[col].dropna()
                            if len(vals) >= 2:
                                price = round(float(vals.iloc[-1]), 2)
                                prev  = round(float(vals.iloc[-2]), 2)
                                chg   = round((price-prev)/prev*100, 2) if prev else 0
                                price_cache[sym] = {'price': price, 'prevClose': prev, 'change': chg, '_ts': time.time()}
                        except:
                            pass
                else:
                    # Tek hisse
                    vals = data['Close'].dropna() if 'Close' in data.columns else data.iloc[:,0].dropna()
                    if len(vals) >= 2 and chunk:
                        sym = chunk[0]
                        price = round(float(vals.iloc[-1]), 2)
                        prev  = round(float(vals.iloc[-2]), 2)
                        chg   = round((price-prev)/prev*100, 2) if prev else 0
                        price_cache[sym] = {'price': price, 'prevClose': prev, 'change': chg, '_ts': time.time()}
            except Exception as e:
                print(f"Chunk error {chunk}: {e}")
            time.sleep(0.5)  # rate limit için bekle

    for sym in syms:
        c = price_cache.get(sym, {})
        result[sym] = {k: v for k, v in c.items() if not k.startswith('_')}

    return jsonify({'prices': result, 'ts': datetime.now().strftime('%H:%M:%S'), 'count': len([v for v in result.values() if v.get('price')])})

@app.route('/api/fundamentals/<sym>')
def get_fundamentals(sym):
    sym = sym.upper()
    if cache_ok(fin_cache, sym, 3600):
        data = {k: v for k, v in fin_cache[sym].items() if not k.startswith('_')}
        return jsonify({'data': data, 'cached': True})
    try:
        info = yf.Ticker(sym + '.IS').info
        eps   = info.get('trailingEps')
        bvps  = info.get('bookValue')
        price = info.get('currentPrice') or info.get('regularMarketPrice', 0)
        graham = round((22.5*eps*bvps)**0.5, 2) if eps and bvps and eps>0 and bvps>0 else None
        data = {
            'price': price, 'pe': info.get('trailingPE'), 'pb': info.get('priceToBook'),
            'eps': eps, 'bvps': bvps,
            'divYield': round((info.get('dividendYield') or 0)*100, 2),
            'roe': round((info.get('returnOnEquity') or 0)*100, 2),
            'roa': round((info.get('returnOnAssets') or 0)*100, 2),
            'revenue': info.get('totalRevenue'), 'netIncome': info.get('netIncomeToCommon'),
            'debtEq': round((info.get('debtToEquity') or 0)/100, 2),
            'currentRatio': info.get('currentRatio'), 'marketCap': info.get('marketCap'),
            'graham': graham,
            'priceToGraham': round(price/graham, 2) if graham and price else None,
            'ts': datetime.now().strftime('%H:%M:%S')
        }
        fin_cache[sym] = {**data, '_ts': time.time()}
        return jsonify({'data': data, 'cached': False})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
