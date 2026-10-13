"""Trial 2, exploratory Binance proxy. Never imports or submits live orders."""
import csv, hashlib, json, math, statistics
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
CAPITAL = 16.48
RULES = {'SOL_USDT': (1.09, .01), 'ETH_USDT': (.1, .00004),
         'NEAR_USDT': (.054, .01), 'SUI_USDT': (1., 1.), 'AVAX_USDT': (.1, .0095)}
PROTOCOL = {'trial': 2, 'capital': CAPITAL, 'interval_hours': 1,
 'ema': [20,50,200], 'ema50_slope_bars': 1, 'setup_expiry_bars': 3,
 'stop_atr_margin': .25, 'atr_period': 14, 'target_risk_multiple': 2,
 'max_hold_hours': 24, 'risk_fraction': .005, 'exposure_cap': .30,
 'order_cap': 5, 'max_positions': 1, 'daily_loss_limit': .02,
 'drawdown_halt': .08, 'fee_per_side': .0005,
 'slippage_per_side': .0005, 'spread_total': .001,
 'rules': RULES, 'quantity_decimals_assumed': 8,
 'tie_break': 'alphabetical', 'ambiguous_bar': 'stop first',
 'entry': 'next open after confirmation', 'btc_filter': False,
 'scope': 'exploratory reused history; no independent validation or LIVE approval'}

def ema(xs, n):
    out = [xs[0]]; alpha = 2/(n+1)
    for x in xs[1:]: out.append(alpha*x+(1-alpha)*out[-1])
    return out

def indicators(rows):
    closes = [x['close'] for x in rows]
    e20,e50,e200 = [ema(closes,n) for n in (20,50,200)]
    tr = [rows[0]['high']-rows[0]['low']]
    for i,b in enumerate(rows[1:],1):
        tr.append(max(b['high']-b['low'],abs(b['high']-closes[i-1]),abs(b['low']-closes[i-1])))
    atr = [None]*len(rows); atr[13] = statistics.mean(tr[:14])
    for i in range(14,len(rows)): atr[i] = (13*atr[i-1]+tr[i])/14
    # One active setup per symbol. An unexpired setup is never overwritten.
    signals = {}
    setup = None
    for i in range(300,len(rows)):
        b = rows[i]
        trend = e50[i]>e200[i] and e50[i]>e50[i-1]
        if setup:
            j = setup
            if i-j>3 or not trend or b['low']<rows[j]['low']:
                setup = None
            elif b['close']>rows[j]['high']:
                signals[i+1] = {'setup':j,'confirm':i,
                  'stop':rows[j]['low']-.25*atr[j]}
                setup = None
                continue
        if setup is None and trend and b['low']<=e20[i] and b['close']>e50[i]:
            setup=i
    return signals

def load():
    root=ROOT/'history_binance'; manifest=json.loads((root/'manifest.json').read_text())
    data={}; hashes={}
    for s in RULES:
        raw=(root/(s+'.csv')).read_bytes(); digest=hashlib.sha256(raw).hexdigest()
        assert digest==manifest['files'][s]['sha256'], 'CSV checksum mismatch'
        rows=list(csv.DictReader(raw.decode().splitlines()))
        data[s]=[{k:int(v) if k=='time' else float(v) for k,v in b.items()} for b in rows]
        hashes[s]=digest
    times=[b['time'] for b in data[next(iter(data))]]
    assert all([b['time'] for b in rows]==times for rows in data.values())
    assert all(b-a==3600 for a,b in zip(times,times[1:]))
    assert all(b['low']<=min(b['open'],b['close'])<=max(b['open'],b['close'])<=b['high'] for rows in data.values() for b in rows)
    return data,hashes

def simulate(data, signals, start, end, multiplier=1, delay=0):
    fee=.0005*multiplier; slip=.0005*multiplier; half=.0005*multiplier
    cash=CAPITAL; pos=None; dust={s:0. for s in RULES}; trades=[]; curve=[]
    peak=CAPITAL; dd=0.; halted=False; day=None; daily_block=False
    rejects={'buy_minimum':0,'sell_minimum':0,'invalid_stop':0,'risk_halt':0}
    def value(i, field='close'):
        total=cash+sum(q*data[s][i][field]*(1-half)*(1-slip)*(1-fee) for s,q in dust.items())
        if pos: total+=pos['qty']*data[pos['symbol']][i][field]*(1-half)*(1-slip)*(1-fee)
        return total
    def mark(v):
        nonlocal peak,dd,halted,daily_block
        curve.append(v); peak=max(peak,v); dd=max(dd,1-v/peak)
        if dd>=.08: halted=True
        if v<=day_start*.98: daily_block=True
    def close(raw,i,reason):
        nonlocal cash,pos
        s=pos['symbol']; proceeds=pos['qty']*raw*(1-half)*(1-slip)*(1-fee)
        cash+=proceeds
        trades.append({**pos,'exit_time':data[s][i]['time'],'exit_raw':raw,
                       'reason':reason,'pnl':proceeds-pos['cost']})
        pos=None
    for i in range(start,end):
        stamp=data['SOL_USDT'][i]['time']; newday=stamp//86400
        if newday!=day:
            day=newday; day_start=value(i,'open'); daily_block=False
        mark(value(i,'open'))
        if pos:
            b=data[pos['symbol']][i]
            if b['open']<=pos['stop']: close(b['open'],i,'stop_gap')
            elif b['open']>=pos['target']: close(b['open'],i,'target_gap')
            elif stamp-pos['entry_time']>=24*3600: close(b['open'],i,'time')
        if pos is None:
            for s in sorted(RULES):
                sig=signals[s].get(i-delay)
                if not sig: continue
                if halted or daily_block: rejects['risk_halt']+=1; continue
                raw=data[s][i]['open']; execution=raw*(1+half)*(1+slip)
                stop=sig['stop']; distance=raw-stop
                if distance<=0 or stop<=0: rejects['invalid_stop']+=1; continue
                v=value(i,'open'); costs=2*(fee+slip)+2*half
                exposure=sum(q*data[z][i]['open'] for z,q in dust.items())
                amount=min(5,v*.005/(distance/raw+costs),max(0,v*.30-exposure),cash)
                amount=math.floor(amount*100)/100
                qty=math.floor(amount/execution*(1-fee)*1e8)/1e8
                buy_min,sell_min=RULES[s]
                if amount<buy_min: rejects['buy_minimum']+=1; continue
                if qty<sell_min: rejects['sell_minimum']+=1; continue
                cash-=amount
                # Base fee and rounding are charged; tiny rounded residue is conservatively written off.
                pos={'symbol':s,'entry_time':stamp,'signal_time':data[s][sig['confirm']]['time'],
                     'qty':qty,'cost':amount,'entry_raw':raw,'stop':stop,'target':raw+2*distance}
                break
        if pos:
            b=data[pos['symbol']][i]
            if b['low']<=pos['stop']: close(pos['stop'],i,'stop')
            elif b['high']>=pos['target']: close(pos['target'],i,'target')
        mark(value(i))
    if pos: close(data[pos['symbol']][end-1]['close'],end-1,'end')
    mark(value(end-1))
    gains=sum(max(0,t['pnl']) for t in trades); losses=-sum(min(0,t['pnl']) for t in trades)
    streak=worst=0
    for t in trades:
        streak=streak+1 if t['pnl']<0 else 0; worst=max(worst,streak)
    return {'start':data['SOL_USDT'][start]['time'],'end':data['SOL_USDT'][end-1]['time']+3600,
      'trades':len(trades),'final_usdt':cash,'net_return_pct':100*(cash/CAPITAL-1),
      'profit_factor':gains/losses if losses else None,'max_close_and_open_drawdown_pct':dd*100,
      'win_rate_pct':100*sum(t['pnl']>0 for t in trades)/len(trades) if trades else None,
      'max_losing_streak':worst,'halted':halted,'rejected':rejects,
      'by_symbol':{s:{'trades':sum(t['symbol']==s for t in trades),'pnl_usdt':sum(t['pnl'] for t in trades if t['symbol']==s)} for s in RULES},
      'trade_log':trades}

def main():
    out=ROOT/'reports'/'pullback_trial2'; out.mkdir(parents=True,exist_ok=True)
    # Protocol is written before data is evaluated.
    (out/'protocol.json').write_text(json.dumps(PROTOCOL,indent=2))
    data,hashes=load(); signals={s:indicators(rows) for s,rows in data.items()}
    n=len(data['SOL_USDT']); cut=int(n*.3)
    report={'protocol':PROTOCOL,'hashes':hashes,'source':'Binance public archive','approved_live':False,
      'base':simulate(data,signals,cut,n),'double_cost':simulate(data,signals,cut,n,2),
      'entry_delay_1h':simulate(data,signals,cut,n,delay=1),
      'folds':[simulate(data,signals,cut+k*(n-cut)//3,cut+(k+1)*(n-cut)//3) for k in range(3)],
      'limitations':['Reused research data, not blind OOS','Current user-reported minima applied to entire history',
      'Base precision assumed 8 decimals; not verified Pionex','Stops modeled automatically; manual execution can be worse',
      'Drawdown measured at hourly opens/closes, excludes intermediate price path','No Pionex book or fills']}
    (out/'results.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    for name in ('base','double_cost','entry_delay_1h'):
        text={k:v for k,v in report[name].items() if k!='trade_log'}
        print(name,json.dumps(text),flush=True)
    print('folds',[(r['trades'],r['net_return_pct']) for r in report['folds']])

if __name__=='__main__': main()
