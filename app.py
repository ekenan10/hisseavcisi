from flask import Flask, jsonify, request
from flask_cors import CORS
import yfinance as yf
import time
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

price_cache = {}
fin_cache   = {}
comp_cache  = {}
PRICE_TTL   = 120
FIN_TTL     = 7200
COMP_TTL    = 3600  # 1 saat

def cache_ok(cache, key, ttl):
    c = cache.get(key)
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
        for i in range(0, len(fresh), 5):
            chunk = fresh[i:i+5]
            tickers_str = ' '.join([s + '.IS' for s in chunk])
            try:
                data = yf.download(tickers_str, period='2d', interval='1d',
                    auto_adjust=True, progress=False, threads=False)
                import pandas as pd
                if data.empty: continue
                if isinstance(data.columns, pd.MultiIndex):
                    closes = data['Close']
                    for sym in chunk:
                        try:
                            col = sym + '.IS'
                            if col not in closes.columns: continue
                            vals = closes[col].dropna()
                            if len(vals) >= 2:
                                price = round(float(vals.iloc[-1]), 2)
                                prev  = round(float(vals.iloc[-2]), 2)
                                chg   = round((price-prev)/prev*100, 2) if prev else 0
                                price_cache[sym] = {'price': price, 'prevClose': prev, 'change': chg, '_ts': time.time()}
                        except: pass
                else:
                    vals = data['Close'].dropna() if 'Close' in data.columns else data.iloc[:,0].dropna()
                    if len(vals) >= 2 and chunk:
                        sym = chunk[0]
                        price = round(float(vals.iloc[-1]), 2)
                        prev  = round(float(vals.iloc[-2]), 2)
                        chg   = round((price-prev)/prev*100, 2) if prev else 0
                        price_cache[sym] = {'price': price, 'prevClose': prev, 'change': chg, '_ts': time.time()}
            except Exception as e:
                print(f"Chunk error {chunk}: {e}")
            time.sleep(0.5)

    for sym in syms:
        c = price_cache.get(sym, {})
        result[sym] = {k: v for k, v in c.items() if not k.startswith('_')}

    return jsonify({'prices': result, 'ts': datetime.now().strftime('%H:%M:%S'),
                    'count': len([v for v in result.values() if v.get('price')])})

@app.route('/api/compare/<sym>')
def compare(sym):
    sym = sym.upper()
    period = request.args.get('period', '1mo')  # 1wk, 1mo, 3mo, 1y
    sector = request.args.get('sector', '')
    
    # Periyod → yfinance parametreleri
    period_map = {
        '1H': ('5d',  '1d'),
        '1A': ('1mo', '1d'),
        '3A': ('3mo', '1d'),
        '1Y': ('1y',  '1wk'),
    }
    yf_period, yf_interval = period_map.get(period, ('1mo', '1d'))
    cache_key = f"{sym}_{period}"
    
    if cache_ok(comp_cache, cache_key, COMP_TTL):
        data = {k: v for k, v in comp_cache[cache_key].items() if not k.startswith('_')}
        return jsonify({**data, 'cached': True})
    
    try:
        # Hisse + BIST100 + sektör ETF'i birlikte çek
        # BIST100 = XU100.IS
        # Sektör mapping
        sector_etf = {
            'Bankacılık': 'XBANK.IS',
            'Holding': 'XHOLD.IS',
            'Teknoloji': 'XUTEK.IS',
            'Sanayi': 'XUSIN.IS',
            'Mali': 'XMALI.IS',
            'Kimya': 'XKMYA.IS',
            'Metal': 'XMANA.IS',
            'Enerji': 'XELKT.IS',
            'Perakende': 'XTCRT.IS',
            'GYO': 'XGMYO.IS',
            'Savunma': None,
            'Havacılık': None,
        }
        
        sec_ticker = sector_etf.get(sector)
        tickers = [sym+'.IS', 'XU100.IS']
        if sec_ticker:
            tickers.append(sec_ticker)
        
        raw = yf.download(' '.join(tickers), period=yf_period, interval=yf_interval,
                         auto_adjust=True, progress=False, threads=False)
        
        import pandas as pd
        
        def extract_closes(data, ticker):
            try:
                if isinstance(data.columns, pd.MultiIndex):
                    # ExcelJS MultiIndex: ('Close', 'TICKER.IS')
                    for key in [('Close', ticker), ('Close', ticker.upper())]:
                        if key in data.columns:
                            vals = data[key].dropna()
                            if len(vals) > 1:
                                return vals.tolist()
                    # Alternatif: xs ile
                    try:
                        vals = data.xs(ticker, axis=1, level=1)['Close'].dropna()
                        if len(vals) > 1:
                            return vals.tolist()
                    except: pass
                    return []
                else:
                    # Tek ticker
                    if 'Close' in data.columns:
                        return data['Close'].dropna().tolist()
                    return []
            except Exception as ex:
                print(f"extract_closes error {ticker}: {ex}")
                return []
        
        hisse_closes  = extract_closes(raw, sym+'.IS')
        bist_closes   = extract_closes(raw, 'XU100.IS')
        sec_closes    = extract_closes(raw, sec_ticker) if sec_ticker else []
        
        # Normalize: başlangıç = 0, yüzde değişim
        def normalize(closes):
            if not closes or closes[0] == 0:
                return []
            base = closes[0]
            return [round((v/base - 1)*100, 2) for v in closes]
        
        # Tarihler
        dates = []
        try:
            if isinstance(raw.index, pd.DatetimeIndex):
                dates = [d.strftime('%d.%m') for d in raw.index]
        except: pass
        
        result = {
            'sym': normalize(hisse_closes),
            'bist': normalize(bist_closes),
            'sector': normalize(sec_closes),
            'sector_name': sector or '',
            'sector_available': bool(sec_closes),
            'dates': dates,
            'period': period,
            '_ts': time.time()
        }
        comp_cache[cache_key] = result
        data = {k: v for k, v in result.items() if not k.startswith('_')}
        return jsonify({**data, 'cached': False})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/fundamentals/<sym>')
def get_fundamentals(sym):
    sym = sym.upper()
    if cache_ok(fin_cache, sym, FIN_TTL):
        data = {k: v for k, v in fin_cache[sym].items() if not k.startswith('_')}
        return jsonify({'data': data, 'cached': True})
    
    last_err = None
    for attempt in range(3):
        try:
            if attempt > 0:
                time.sleep(2 * attempt)
            info = yf.Ticker(sym + '.IS').info
            if not info or len(info) < 5:
                raise Exception('Boş veri')
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
            last_err = str(e)
            if 'Rate' in str(e) or '429' in str(e):
                time.sleep(3)
                continue
            break
    
    return jsonify({'error': last_err or 'Veri alınamadı'}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
