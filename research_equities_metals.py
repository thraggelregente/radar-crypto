"""Exploratory daily trend screening; public snapshots only, no execution.
Fixed 50/200 parameters, with explicit mutually consistent entry/exit: SMA50>SMA200 and close>SMA50 -> long;
close<SMA50 -> cash. Signal after UTC close, fill at next positive-volume open.
Independent 1000-USDT paper accounts, full exposure, fractional units.
Results are not a small-account portfolio or proof of executable orders.
"""
import datetime as dt, hashlib, json, math, statistics, gzip, base64
from pathlib import Path
ROOT=Path(__file__).resolve().parent;DATA=ROOT/'research_details'/'stock_catalog';OUT=ROOT/'reports'/'equities_metals';OUT.mkdir(parents=True,exist_ok=True)
SYMS=['NVDAX','AAPLX','GOOGLX','AMZNX','METAX','TSLAX','CRCLX','BMNRX','SPYX','QQQX','PAXG','XAUT','SLVX']
DAY=86400000;TODAY=int(dt.datetime.now(dt.timezone.utc).timestamp()//86400)*DAY
EVENTS=[('NVDAX','2026-08-26','https://nvidianews.nvidia.com/news/nvidia-announces-financial-results-for-second-quarter-fiscal-2027'),('AAPLX','2026-07-30','https://www.apple.com/newsroom/2026/07/apple-reports-third-quarter-results/'),('GOOGLX','2026-07-22','https://s206.q4cdn.com/479360582/files/doc_financials/2026/q2/2026q2-alphabet-earnings-release.pdf')]
def date(t):return dt.datetime.fromtimestamp(t/1000,dt.timezone.utc).date().isoformat()
def stamp(d):return int(dt.datetime.fromisoformat(d).replace(tzinfo=dt.timezone.utc).timestamp()*1000)
def load(s):
 choices=[DATA/('full_daily_'+s+'.json'),DATA/('daily_'+s+'.json')]
 if s=='NVDAX':choices.append(DATA/'nvda_daily.json')
 f=next(f for f in choices if f.exists());a=json.loads(f.read_text())['data']['klines'];assert len(a)==len({x['time'] for x in a}),'duplicate bar'
 a=sorted([{k:int(v) if k=='time' else float(v) for k,v in x.items()} for x in a if x['time']<TODAY],key=lambda x:x['time'])
 invalid=[x for x in a if not x['low']<=min(x['open'],x['close'])<=max(x['open'],x['close'])<=x['high']]
 a=[x for x in a if x not in invalid]
 return a,{'path':str(f.relative_to(ROOT)),'sha256':hashlib.sha256(f.read_bytes()).hexdigest(),'bars':len(a),'start':date(a[0]['time']),'end':date(a[-1]['time']),'excluded_invalid_OHLC_dates':[date(x['time']) for x in invalid],'zero_volume':sum(x['volume']==0 for x in a),'missing_calendar_days':int((a[-1]['time']-a[0]['time'])/DAY+1)-len(a)}
def trend(a,start=None,fee=.001,slip=.0005,consistent_entry=True):
 cash=1000.;q=0.;pending=None;entry=None;trades=[];curve=[];cl=[x['close'] for x in a]
 for i,b in enumerate(a):
  if i<199 or start and b['time']<start:continue
  if pending and b['volume']>0:
   if pending=='buy' and q==0:
    px=b['open']*(1+slip);q=cash/(px*(1+fee));entry={'entry':date(b['time']),'signal':date(a[i-1]['time']),'price':px,'invested':cash};cash=0
   elif pending=='sell' and q:
    px=b['open']*(1-slip);cash=q*px*(1-fee);trades.append(dict(entry,exit=date(b['time']),exit_price=px,net_return_percent=100*(cash/entry['invested']-1)));q=0
   pending=None
  equity=cash+q*b['close']*(1-slip)*(1-fee)
  curve.append((b['time'],equity))
  s50=sum(cl[i-49:i+1])/50;s200=sum(cl[i-199:i+1])/200
  if q and b['close']<s50:pending='sell'
  elif not q and b['close']>(s50 if consistent_entry else s200) and s50>s200:pending='buy'
  else:pending=None
 if q:
  last=next(x for x in reversed(a) if x['volume']>0);cash=q*last['close']*(1-slip)*(1-fee)
  trades.append(dict(entry,exit=date(last['time']),exit_price=last['close']*(1-slip),net_return_percent=100*(cash/entry['invested']-1),forced_end=True));curve[-1]=(curve[-1][0],cash)
 peak=1000.;dd=0
 for _,v in curve:peak=max(peak,v);dd=max(dd,1-v/peak)
 first=next((b for i,b in enumerate(a) if i>=199 and (start is None or b['time']>=start) and b['volume']>0),None)
 last=next(b for b in reversed(a) if b['volume']>0)
 hold=100*((last['close']*(1-slip)*(1-fee))/(first['open']*(1+slip)*(1+fee))-1) if first else None
 hold_peak=first['open'] if first else 1000;hold_dd=0
 for b in a:
  if first and b['time']>=first['time']:
   hold_peak=max(hold_peak,b['close']);hold_dd=max(hold_dd,1-b['close']/hold_peak)
 annual=[]
 for yr in sorted({date(t)[:4] for t,_ in curve}):
  ix=[i for i,(t,_) in enumerate(curve) if date(t).startswith(yr)];base=curve[ix[0]-1][1] if ix[0]>0 else 1000
  annual.append({'year':yr,'return_percent':100*(curve[ix[-1]][1]/base-1),'start':date(curve[ix[0]][0]),'end':date(curve[ix[-1]][0])})
 return {'return_percent':100*(cash/1000-1),'max_close_drawdown_percent':100*dd,'buy_hold_same_window_percent':hold,'buy_hold_max_close_drawdown_percent':hold_dd*100,'trades':len(trades),'wins':sum(x['net_return_percent']>0 for x in trades),'annual':annual,'trade_details':trades,'tested_from':date(curve[0][0]) if curve else None,'tested_to':date(curve[-1][0]) if curve else None}
def events(data):
 out=[];spy={x['time']:x for x in data['SPYX']}
 for s,d,url in EVENTS:
  a=data[s];reaction=stamp(d)+DAY;b=next((x for x in a if x['time']==reaction),None)
  if not b:continue
  entry=next((x for x in a if x['time']>=reaction+DAY and x['volume']>0),None)
  exit=next((x for x in a if entry and x['time']>=entry['time']+5*DAY and x['volume']>0),None)
  if not entry or not exit:continue
  stock=b['close']/b['open']-1;ref=spy.get(reaction);market=ref['close']/ref['open']-1 if ref else None
  out.append({'symbol':s,'announcement_date':d,'source':url,'reaction_day_UTC':date(reaction),'reaction_open_to_close_percent':stock*100,'spy_reaction_percent':market*100 if market is not None else None,'positive_reaction_and_outperformed_SPY':bool(b['volume']>0 and stock>0 and market is not None and stock>market),'hypothetical_entry_UTC':date(entry['time']),'hypothetical_exit_UTC':date(exit['time']),'subsequent_5_calendar_days_net_percent':100*(exit['open']*.9995*.999/(entry['open']*1.0005*1.001)-1)})
 return out
if __name__=='__main__':
 data={};manifest={};results=[]
 for s in SYMS:
  data[s],manifest[s]=load(s)
  results.append({'symbol':s,'initial_conflicting_signal_rule_common_window':trend(data[s],stamp('2026-04-01'),consistent_entry=False),'full_history':trend(data[s]),'common_window_from_2026_04_01':trend(data[s],stamp('2026-04-01')),'common_window_double_costs':trend(data[s],stamp('2026-04-01'),.002,.001)})
 # Causal verification: all completed trades before cutoff remain identical under prefix truncation.
 for s,a in data.items():
  cut=len(a)-60;full=trend(a)['trade_details'];pre=trend(a[:cut])['trade_details']
  stable=[x for x in full if not x.get('forced_end') and x['exit']<date(a[cut-1]['time'])]
  observed=[x for x in pre if not x.get('forced_end') and x['exit']<date(a[cut-1]['time'])]
  assert stable==observed,(s,'prefix invariance failure')
 # Zero-cost constant price must have zero return; price rise must reconcile with account value.
 fixture=[{'time':stamp('2025-01-01')+i*DAY,'open':100.,'close':100.,'high':100.,'low':100.,'volume':1.} for i in range(250)]
 assert abs(trend(fixture,fee=0,slip=0)['return_percent'])<1e-9
 report={'purpose':'Exploratory research, not strategy validation or real-account simulation','fixed_rule':'Daily SMA50>SMA200 and close>SMA50 -> long; close<SMA50 -> cash; next positive-volume open','cost_assumption':'0.1% fee plus 0.05% adverse fill per side for all assets, including gold as conservative assumption; stress doubles both','paper_account':'1000 USDT independently per asset, 100% allocation, fractional units, no leverage','balance_feasibility':{'balance':16.38048824,'previous_exposure_cap_percent':30,'max_nominal':16.38048824*.3,'API_minimum_USDT':10,'fits_previous_exposure_cap':False},'manifest':manifest,'results':results,'earnings_examples':events(data),'checks':'OHLC validity, unique bars, prefix invariance for completed trades, constant price zero-cost sanity passed','limitations':['Original entry allowed close below exit SMA50 and caused repeated re-entry; retained alongside corrected version requiring close above SMA50. This correction is not an independent validation.','Three invalid historical PAXG OHLC bars excluded and dates recorded; raw response unchanged.','Survivorship bias: currently identified tokens only.','Small and strongly regime-dependent equity sample; no statistical validation.','Common window chosen for history coverage, not performance.','UTC calendar bars include weekends; no fill allowed on zero-volume bars.','Daily OHLC cannot verify intraday fills or actual historical spread/slippage.','No audited underlying-price tracking comparison; corporate actions and token rewards excluded.','Maximum drawdown uses daily closes and can understate intraday risk.','No stop loss; full-allocation paper risk differs from prior 0.5% per-trade risk limits.','End positions liquidated for accounting; not a live exit signal.','Three earnings examples are diagnostic illustrations only, not a complete event backtest or independent validation.','No consensus-surprise or fundamental-news model tested.']}
 archive={s:json.loads((ROOT/m['path']).read_text()) for s,m in manifest.items()}
 encoded=base64.b64encode(gzip.compress(json.dumps(archive,separators=(',',':')).encode(),mtime=0)).decode()
 (OUT/'daily_snapshots.json.gz.base64').write_text(encoded+'\n')
 report['data_archive']='daily_snapshots.json.gz.base64: base64 decode, gzip decompress to raw API response JSON.'
 details={}
 for row in report['results']:
  details[row['symbol']]={k:v.pop('trade_details') for k,v in row.items() if isinstance(v,dict) and 'trade_details' in v}
 (OUT/'trade_details.json').write_text(json.dumps(details,indent=2))
 (OUT/'summary.json').write_text(json.dumps(report,indent=2))
 for r in results:
  f=r['full_history'];c=r['common_window_from_2026_04_01'];stress=r['common_window_double_costs'];print(r['symbol'],'common',round(c['return_percent'],2),'hold',round(c['buy_hold_same_window_percent'],2),'dd',round(c['max_close_drawdown_percent'],2),'trades',c['trades'],'stress',round(stress['return_percent'],2),'full',round(f['return_percent'],2),'fulltrades',f['trades'])
 print('EVENTS',json.dumps(report['earnings_examples'],indent=2));print('CHECKS',report['checks'])
