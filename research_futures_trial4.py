"""Trial 4: public Pionex perpetuals, fixed symmetric breakout hypothesis.
GET-only downloader; synthetic 1x portfolio simulation, never sends orders.
"""
import argparse, hashlib, json, math, statistics, time, urllib.parse, urllib.request
from pathlib import Path
CAPITAL=16.38048824
SYMBOLS=['SOL_USDT_PERP','LTC_USDT_PERP']
PROTOCOL={'trial':4,'capital':CAPITAL,'symbols':SYMBOLS,'interval_seconds':3600,
 'strategy':'Symmetric 20-hour channel breakout with EMA50/200 trend and volume confirmation',
 'ema':[50,200],'channel_bars':20,'volume_ratio_minimum':1.2,'atr_period':14,
 'stop_atr':2,'target_atr':3,'max_hold_hours':48,'risk_per_trade':.005,
 'nominal_exposure_cap':.30,'order_nominal_cap':5,'positions':1,
 'daily_loss_limit':.02,'drawdown_halt':.08,'assumed_leverage':1,
 'fee_per_side':.0005,'slippage_per_side':.0005,'spread_total':.001,
 'entry':'Next hour open after closed signal; skip opening already beyond intended stop',
 'priority':'Greater channel excess divided by ATR, then symbol',
 'funding':'Actual historical settlements, long pays positive funding and short receives',
 'ambiguous_bar':'Stop first','validation':'Exploratory only; no LIVE approval',
 'limitations':['Historical order rules approximated by current rules','Hourly prices do not reconstruct fills or manual exit delay',
 'Synthetic 1x margin, not exchange liquidation or maintenance-margin model','No optimization, no blind holdout yet']}
ROOT=Path(__file__).resolve().parent
DATA=ROOT/'research_details'/'pionex_public'
OUT=ROOT/'reports'/'futures_trial4'

def ema(xs,n):
 out=[xs[0]];a=2/(n+1)
 for x in xs[1:]:out.append(a*x+(1-a)*out[-1])
 return out

def signals(rows):
 c=[x['close'] for x in rows];e50=ema(c,50);e200=ema(c,200)
 tr=[rows[0]['high']-rows[0]['low']]
 for i,b in enumerate(rows[1:],1):tr.append(max(b['high']-b['low'],abs(b['high']-c[i-1]),abs(b['low']-c[i-1])))
 atr=[0.]*len(rows);atr[13]=statistics.mean(tr[:14])
 for i in range(14,len(rows)):atr[i]=(13*atr[i-1]+tr[i])/14
 result={}
 for i in range(300,len(rows)-1):
  b=rows[i];hist=rows[i-20:i];av=statistics.mean(x['volume'] for x in hist)
  if av<=0 or b['volume']/av<1.2:continue
  hi=max(x['high'] for x in hist);lo=min(x['low'] for x in hist)
  side=1 if e50[i]>e200[i] and e50[i]>e50[i-1] and c[i]>hi else -1 if e50[i]<e200[i] and e50[i]<e50[i-1] and c[i]<lo else 0
  if side:
   result[i+1]={'side':side,'atr':atr[i],'signal_time':b['time'],
    'stop':c[i]-side*2*atr[i],'target':c[i]+side*3*atr[i],
    'rank':((c[i]-hi) if side==1 else (lo-c[i]))/atr[i]}
 return result

def public_get(path,params):
 assert path in ['/api/v1/common/symbols','/api/v1/market/klines','/api/v1/market/fundingRates']
 url='https://api.pionex.com'+path+'?'+urllib.parse.urlencode(params)
 with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'radar-crypto-public-research/1.0'}),timeout=15) as z:o=json.load(z)
 if not o.get('result'):raise ValueError(str(o.get('code','Public request rejected')))
 time.sleep(.6)
 return o

def download(days):
 DATA.mkdir(parents=True,exist_ok=True)
 rules=public_get('/api/v1/common/symbols',{'symbols':','.join(SYMBOLS)})
 (DATA/'rules_trial4.json').write_text(json.dumps(rules))
 cutoff=int((time.time()-days*86400)*1000)
 for s in SYMBOLS:
  for kind,key,path in [('bars','klines','/api/v1/market/klines'),('funding','rates','/api/v1/market/fundingRates')]:
   end=int(time.time()*1000);allrows={}
   for page in range(200):
    args={'symbol':s,'limit':500,'endTime':end}
    if kind=='bars':args['interval']='60M'
    o=public_get(path,args);rows=o['data'][key]
    if not rows:break
    tk='time' if kind=='bars' else 'fundingTime'
    oldest=min(int(x[tk]) for x in rows)
    if oldest>=end:raise ValueError('Pagination did not move backward')
    for row in rows:allrows[int(row[tk])]=row
    (DATA/(kind+'_full_'+s+'.json')).write_text(json.dumps({'result':True,'data':{key:list(allrows.values())},'source':'Pionex public API'}))
    print(s,kind,len(allrows),flush=True)
    if oldest<=cutoff:break
    end=oldest-1
   else:raise ValueError('Page budget exhausted')

def load():
 rulespath=DATA/'rules_trial4.json'
 if not rulespath.exists():rulespath=DATA/'rules.json'
 rules={x['symbol']:x for x in json.loads(rulespath.read_text())['data']['symbols']}
 ds={};fund={};hashes={}
 for s in SYMBOLS:
  full=DATA/('bars_full_'+s+'.json')
  paths=[full] if full.exists() else [DATA/('klines_'+s+'.json'),DATA/('older_'+s+'.json')]
  allrows={}
  for p in paths:
   raw=p.read_bytes();hashes[p.name]=hashlib.sha256(raw).hexdigest()
   for b in json.loads(raw)['data']['klines']:
    if int(b['time'])+3600000<=int(time.time()*1000):allrows[int(b['time'])]={k:int(v)//1000 if k=='time' else float(v) for k,v in b.items()}
  ds[s]=[allrows[t] for t in sorted(allrows)]
  assert all(b['time']-a['time']==3600 for a,b in zip(ds[s],ds[s][1:])), 'Candle gap'
  fp=DATA/('funding_full_'+s+'.json')
  if not fp.exists():fp=DATA/('funding_'+s+'.json')
  raw=fp.read_bytes();hashes[fp.name]=hashlib.sha256(raw).hexdigest();o=json.loads(raw)
  if not o.get('result'):raise ValueError('Missing historical funding '+s)
  fund[s]=sorted([(int(x['fundingTime'])//1000,float(x['fundingRate'])) for x in o['data']['rates']])
  assert fund[s][0][0]<=ds[s][0]['time'] and fund[s][-1][0]>=ds[s][-1]['time']-86400,'Insufficient funding coverage'
 first=max(x[0]['time'] for x in ds.values());last=min(x[-1]['time'] for x in ds.values())
 ds={s:[b for b in rows if first<=b['time']<=last] for s,rows in ds.items()}
 assert all([b['time'] for b in rows]==[b['time'] for b in ds[SYMBOLS[0]]] for rows in ds.values())
 return ds,fund,rules,hashes

def funding_cash(side,qty,price,rate):return -side*qty*price*rate

def run(ds,fund,rules,mult=1):
 sig={s:signals(rows) for s,rows in ds.items()};n=len(ds[SYMBOLS[0]])
 cash=CAPITAL;pos=None;trades=[];peak=CAPITAL;dd=0.;halt=False;day=None;blocked=False;skips=0;curve=[]
 fee=.0005*mult;slip=.0005*mult;half=.0005*mult
 def fill(raw,side,entry):return raw*(1+(side if entry else -side)*(half+slip))
 def eq(i,field):
  if not pos:return cash
  raw=ds[pos['symbol']][i][field];exitprice=fill(raw,pos['side'],False)
  return cash+pos['side']*pos['qty']*(exitprice-pos['entry'])-fee*pos['qty']*exitprice
 def mark(v):
  nonlocal peak,dd,halt,blocked
  curve.append(v);peak=max(peak,v);dd=max(dd,1-v/peak)
  if dd>=.08:halt=True
  if v<=daystart*.98:blocked=True
 def close(raw,stamp,reason):
  nonlocal cash,pos
  px=fill(raw,pos['side'],False);cost=pos['qty']*px*fee
  pnl=pos['side']*pos['qty']*(px-pos['entry'])-cost
  cash+=pnl;trades.append({**pos,'exit_time':stamp,'exit':px,'reason':reason,
   'pnl':pnl-pos['entry_fee']+pos['funding']});pos=None
 for i in range(301,n):
  now=ds[SYMBOLS[0]][i]['time']
  if now//86400!=day:day=now//86400;daystart=eq(i,'open');blocked=False
  # Funding at open applies only to positions already held before the settlement.
  if pos:
   for stamp,rate in fund[pos['symbol']]:
    if stamp==now and pos['opened']<stamp:
     delta=funding_cash(pos['side'],pos['qty'],ds[pos['symbol']][i]['open'],rate)
     cash+=delta;pos['funding']+=delta
  mark(eq(i,'open'))
  if pos:
   b=ds[pos['symbol']][i];side=pos['side']
   reason='stop_gap' if side*(b['open']-pos['stop'])<=0 else 'target_gap' if side*(b['open']-pos['target'])>=0 else 'time' if now-pos['opened']>=48*3600 else ''
   if reason:close(b['open'],now,reason)
  if pos is None and not halt and not blocked:
   choices=sorted([(s,sig[s][i]) for s in SYMBOLS if i in sig[s]],key=lambda x:(-x[1]['rank'],x[0]))
   for s,d in choices:
    raw=ds[s][i]['open'];side=d['side'];distance=side*(raw-d['stop'])
    if distance<=0 or side*(d['target']-raw)<=0:skips+=1;continue
    v=cash;nominal=min(5,.30*v,.005*v/(distance/raw+2*(fee+slip+half)))
    price=fill(raw,side,True);step=float(rules[s]['baseStep']);qty=math.floor(nominal/price/step+1e-12)*step
    if qty<float(rules[s]['minSizeMarket']) or qty*price<float(rules[s]['minNotional']):skips+=1;continue
    cost=qty*price*fee;cash-=cost
    pos={'symbol':s,'side':side,'opened':now,'signal_time':d['signal_time'],'entry':price,'qty':qty,
         'stop':d['stop'],'target':d['target'],'entry_fee':cost,'funding':0.}
    break
  if pos:
   b=ds[pos['symbol']][i];side=pos['side']
   # Adverse hourly estimate capped at modeled stop; OHLC path remains unknown.
   adverse=max(b['low'],pos['stop']) if side==1 else min(b['high'],pos['stop'])
   mark(cash+side*pos['qty']*(fill(adverse,side,False)-pos['entry'])-fee*pos['qty']*adverse)
   stophit=b['low']<=pos['stop'] if side==1 else b['high']>=pos['stop']
   targethit=b['high']>=pos['target'] if side==1 else b['low']<=pos['target']
   if stophit:close(pos['stop'],now+3599,'stop')
   elif targethit:close(pos['target'],now+3599,'target')
  mark(eq(i,'close'))
 if pos:close(ds[pos['symbol']][-1]['close'],ds[pos['symbol']][-1]['time']+3599,'sample_end')
 assert abs(cash-CAPITAL-sum(t['pnl'] for t in trades))<1e-9
 profit=sum(max(0,t['pnl']) for t in trades);loss=-sum(min(0,t['pnl']) for t in trades)
 return {'trades':len(trades),'final_usdt':cash,'return_pct':100*(cash/CAPITAL-1),'drawdown_estimate_pct':dd*100,
  'profit_factor':profit/loss if loss else None,'funding_net_usdt':sum(t['funding'] for t in trades),
  'halted':halt,'skipped':skips,'trade_log':trades}

def main():
 p=argparse.ArgumentParser();p.add_argument('--download-days',type=int);a=p.parse_args()
 OUT.mkdir(parents=True,exist_ok=True);(OUT/'protocol.json').write_text(json.dumps(PROTOCOL,indent=2))
 if a.download_days:download(a.download_days)
 ds,fund,rules,hashes=load();report={'protocol':PROTOCOL,'hashes':hashes,'source':'Pionex public API',
 'history_hours':len(ds[SYMBOLS[0]]),'evaluated_hours':max(0,len(ds[SYMBOLS[0]])-301),
 'base':run(ds,fund,rules),'double_cost':run(ds,fund,rules,2),'approved_live':False}
 (OUT/'results.json').write_text(json.dumps(report,indent=2,allow_nan=False))
 for k in ['base','double_cost']:report[k].pop('trade_log')
 (OUT/'summary.json').write_text(json.dumps(report,indent=2,allow_nan=False));print(json.dumps(report,indent=2),flush=True)
if __name__=='__main__':main()
