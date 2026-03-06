from flask import Flask, jsonify, request
from flask_cors import CORS
import yfinance as yf
import pandas as pd
import time
from datetime import datetime

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

price_cache = {}
fin_cache   = {}
comp_cache  = {}
PRICE_TTL   = 120
FIN_TTL     = 7200
COMP_TTL    = 3600

# BIST sektör endeksleri — tam liste
SECTOR_ETF = {
    'Bankacılık':  'XBANK.IS',
    'Holding':     'XHOLD.IS',
    'Teknoloji':   'XUTEK.IS',
    'Sanayi':      'XUSIN.IS',
    'GYO':         'XGMYO.IS',
    'Enerji':      'XELKT.IS',
    'Perakende':   'XTCRT.IS',
    'Kimya':       'XKMYA.IS',
    'Metal':       'XMANA.IS',
    'Gıda':        'XGIDA.IS',
    'Tekstil':     'XTEKS.IS',
    'Ulaştırma':   'XULAS.IS',
    'Havacılık':   'XULAS.IS',   # Ulaştırma endeksine dahil
    'Lojistik':    'XULAS.IS',
    'Otomotiv':    'XMOTO.IS',
    'Çimento':     'XNONM.IS',
    'Cam':         'XNONM.IS',
    'Seramik':     'XNONM.IS',
    'İnşaat':      'XNONM.IS',
    'Madencilik':  'XMADN.IS',
    'Sigorta':     'XSGRT.IS',
    'Finans':      'XFINK.IS',
    'İçecek':      'XGIDA.IS',
    'Tarım':       'XGIDA.IS',
    'İlaç':        'XSAGX.IS',
    'Sağlık':      'XSAGX.IS',
    'Telekom':     'XUTEK.IS',
    'Medya':       'XUTEK.IS',
    'Savunma':     'XUSIN.IS',
    'Makina':      'XUSIN.IS',
    'Elektrik':    'XELKT.IS',
    'Kağıt':       'XKAGT.IS',
    'Ambalaj':     'XKAGT.IS',
    'Plastik':     'XKMYA.IS',
    'Turizm':      'XTRZM.IS',
    'Spor':        'XSPOR.IS',
    'Beyaz Eşya':  'XMOTO.IS',
    'Lastik':      'XMOTO.IS',
    'Kırtasiye':   'XUSIN.IS',
}

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


def safe_extract(data, ticker):
    """MultiIndex veya tekli DataFrame'den Close serisini güvenle çıkar."""
    try:
        if isinstance(data.columns, pd.MultiIndex):
            if ('Close', ticker) in data.columns:
                s = data[('Close', ticker)].dropna()
                return s.values.tolist(), [d.strftime('%d.%m') for d in s.index]
        else:
            if 'Close' in data.columns:
                s = data['Close'].dropna()
                return s.values.tolist(), [d.strftime('%d.%m') for d in s.index]
    except Exception as e:
        print(f"safe_extract {ticker}: {e}")
    return [], []


def normalize(closes):
    if not closes or closes[0] == 0:
        return []
    base = closes[0]
    return [round((v / base - 1) * 100, 2) for v in closes]


@app.route('/api/compare/<sym>')
def compare(sym):
    sym = sym.upper()
    period  = request.args.get('period', '1A')
    sector  = request.args.get('sector', '')
    cache_key = f"{sym}_{period}"

    if cache_ok(comp_cache, cache_key, COMP_TTL):
        d = {k: v for k, v in comp_cache[cache_key].items() if not k.startswith('_')}
        return jsonify({**d, 'cached': True})

    period_map = {
        '1H': ('5d',  '1d'),
        '1A': ('1mo', '1d'),
        '3A': ('3mo', '1d'),
        '1Y': ('1y',  '1wk'),
    }
    yf_period, yf_interval = period_map.get(period, ('1mo', '1d'))

    sec_etf = SECTOR_ETF.get(sector)
    tickers = [sym + '.IS', 'XU100.IS']
    if sec_etf:
        tickers.append(sec_etf)

    try:
        raw = yf.download(
            ' '.join(tickers),
            period=yf_period,
            interval=yf_interval,
            auto_adjust=True,
            progress=False,
            threads=False
        )

        if raw.empty:
            return jsonify({'error': 'Veri boş'}), 500

        hisse_c, dates = safe_extract(raw, sym + '.IS')
        bist_c,  _     = safe_extract(raw, 'XU100.IS')
        sec_c,   _     = safe_extract(raw, sec_etf) if sec_etf else ([], [])

        result = {
            'sym':              normalize(hisse_c),
            'bist':             normalize(bist_c),
            'sector':           normalize(sec_c),
            'sector_name':      sector,
            'sector_etf':       sec_etf or '',
            'sector_available': len(sec_c) > 1,
            'dates':            dates,
            'period':           period,
            'data_points':      len(hisse_c),
            '_ts':              time.time()
        }
        comp_cache[cache_key] = result
        out = {k: v for k, v in result.items() if not k.startswith('_')}
        return jsonify({**out, 'cached': False})

    except Exception as e:
        print(f"Compare error {sym}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/fundamentals/<sym>')
def get_fundamentals(sym):
    sym = sym.upper()
    if cache_ok(fin_cache, sym, FIN_TTL):
        d = {k: v for k, v in fin_cache[sym].items() if not k.startswith('_')}
        return jsonify({'data': d, 'cached': True})

    last_err = None
    for attempt in range(3):
        try:
            if attempt > 0:
                time.sleep(3 * attempt)

            t = yf.Ticker(sym + '.IS')

            # fast_info daha güvenilir
            try:
                fi = t.fast_info
                price = round(float(fi.last_price), 2) if fi.last_price else None
                mktcap = int(fi.market_cap) if fi.market_cap else None
            except:
                price, mktcap = None, None

            # info — bazı alanlar burada
            info = {}
            try:
                info = t.info or {}
            except:
                pass

            if not info and price is None:
                raise Exception('Veri alınamadı')

            eps   = info.get('trailingEps')
            bvps  = info.get('bookValue')
            if price is None:
                price = info.get('currentPrice') or info.get('regularMarketPrice', 0) or 0

            graham = None
            if eps and bvps and eps > 0 and bvps > 0:
                try:
                    graham = round((22.5 * eps * bvps) ** 0.5, 2)
                except:
                    pass

            data = {
                'price':        price,
                'pe':           info.get('trailingPE'),
                'pb':           info.get('priceToBook'),
                'eps':          eps,
                'bvps':         bvps,
                'divYield':     round((info.get('dividendYield') or 0) * 100, 2),
                'roe':          round((info.get('returnOnEquity') or 0) * 100, 2),
                'roa':          round((info.get('returnOnAssets') or 0) * 100, 2),
                'revenue':      info.get('totalRevenue'),
                'netIncome':    info.get('netIncomeToCommon'),
                'debtEq':       round((info.get('debtToEquity') or 0) / 100, 2),
                'currentRatio': info.get('currentRatio'),
                'marketCap':    mktcap or info.get('marketCap'),
                'graham':       graham,
                'priceToGraham': round(price / graham, 2) if graham and price else None,
                'ts':           datetime.now().strftime('%H:%M:%S'),
            }

            fin_cache[sym] = {**data, '_ts': time.time()}
            return jsonify({'data': data, 'cached': False})

        except Exception as e:
            last_err = str(e)
            if '429' in str(e) or 'rate' in str(e).lower() or 'Rate' in str(e):
                time.sleep(5)
                continue
            break

    return jsonify({'error': last_err or 'Veri alınamadı'}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)


# ── Gerçek OHLCV endpoint ────────────────────────────────────
ohlcv_cache = {}
OHLCV_TTL   = 3600  # 1 saat

@app.route('/api/ohlcv/<sym>')
def get_ohlcv(sym):
    sym = sym.upper()
    period = request.args.get('period', '6mo')  # 6mo = ~130 mum
    cache_key = f"{sym}_{period}"

    if cache_ok(ohlcv_cache, cache_key, OHLCV_TTL):
        d = {k: v for k, v in ohlcv_cache[cache_key].items() if not k.startswith('_')}
        return jsonify({**d, 'cached': True})

    try:
        df = yf.download(
            sym + '.IS',
            period=period,
            interval='1d',
            auto_adjust=True,
            progress=False,
            threads=False
        )

        if df.empty:
            return jsonify({'error': 'Veri yok'}), 404

        # MultiIndex düzelt
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df.dropna(subset=['Close'])
        df.index = pd.to_datetime(df.index)

        ohlcv = []
        for ts, row in df.iterrows():
            try:
                o = float(row['Open'])
                h = float(row['High'])
                l = float(row['Low'])
                c = float(row['Close'])
                v = int(row['Volume']) if not pd.isna(row['Volume']) else 0
                ohlcv.append({
                    't': ts.strftime('%Y-%m-%d'),
                    'o': round(o, 2),
                    'h': round(h, 2),
                    'l': round(l, 2),
                    'c': round(c, 2),
                    'v': v
                })
            except:
                continue

        if len(ohlcv) < 10:
            return jsonify({'error': 'Yetersiz veri'}), 404

        result = {
            'sym':    sym,
            'ohlcv':  ohlcv,
            'count':  len(ohlcv),
            'from':   ohlcv[0]['t'],
            'to':     ohlcv[-1]['t'],
            '_ts':    time.time()
        }
        ohlcv_cache[cache_key] = result
        out = {k: v for k, v in result.items() if not k.startswith('_')}
        return jsonify({**out, 'cached': False})

    except Exception as e:
        print(f"OHLCV error {sym}: {e}")
        return jsonify({'error': str(e)}), 500
