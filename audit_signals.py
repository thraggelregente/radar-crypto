"""Exploratory diagnosis; does not select/optimize strategy or submit orders."""
import inspect,json,statistics
from pathlib import Path
import research_pullback as r
D,H=r.load(); S={s:r.indicators(b) for s,b in D.items()}; n=len(D['SOL_USDT']); cut=int(n*.3)
def compact(x):return {k:v for k,v in x.items() if k not in ['trade_log','by_symbol']}
report={'source':'Binance proxy reused history','approved_live':False,'hashes':H}
report['zero_cost']=compact(r.simulate(D,S,cut,n,multiplier=0))
# Sensitivity only: target-first is an optimistic bound, not a realistic execution model.
src=inspect.getsource(r.simulate)
a="if b['low']<=pos['stop']: close(pos['stop'],i,'stop')\n            elif b['high']>=pos['target']: close(pos['target'],i,'target')"
b="if b['high']>=pos['target']: close(pos['target'],i,'target')\n            elif b['low']<=pos['stop']: close(pos['stop'],i,'stop')"
assert a in src
ns=dict(r.__dict__); exec(src.replace(a,b),ns)
report['optimistic_target_first']=compact(ns['simulate'](D,S,cut,n))
orders=[]; syms=sorted(r.RULES)
for j in range(5):
 order=syms[j:]+syms[:j]; ns=dict(r.__dict__)
 exec(src.replace('sorted(RULES)',repr(order)),ns)
 orders.append({'priority':order,'result':compact(ns['simulate'](D,S,cut,n))})
report['priority_rotations']=orders
forward={}
for h in [1,4,12,24]:
 all_signals=[]; baseline=[]; detail={}
 for s,rows in D.items():
  closes=[b['close'] for b in rows]; e50=r.ema(closes,50); e200=r.ema(closes,200)
  events=[]; trend=[]
  for i in range(cut,n-h):
   raw=rows[i+h-1]['close']/rows[i]['open']-1
   net=(1+raw)*(1-.0005)**2*(1-.0005)/((1+.0005)*(1+.0005))*(1-.0005)-1
   if e50[i-1]>e200[i-1] and e50[i-1]>e50[i-2]:trend.append(net)
   if i in S[s]:events.append((raw,net))
  all_signals.extend(events); baseline.extend(trend)
  detail[s]={'signals':len(events),'mean_net_pct':100*statistics.mean(t[1] for t in events),'median_net_pct':100*statistics.median(t[1] for t in events),'trend_baseline_net_pct':100*statistics.mean(trend)}
 forward[str(h)]={'signals':len(all_signals),'raw_mean_pct':100*statistics.mean(t[0] for t in all_signals),'net_mean_pct':100*statistics.mean(t[1] for t in all_signals),'net_median_pct':100*statistics.median(t[1] for t in all_signals),'positive_net_pct':100*sum(t[1]>0 for t in all_signals)/len(all_signals),'trend_baseline_net_pct':100*statistics.mean(baseline),'by_symbol':detail}
report['forward_returns']=forward
report['limitations']=['Overlapping events; sample count is not independent observations','Trend baseline is diagnostic, not matched randomized control','Zero costs alter sizing/minimum eligibility and drawdown halts','Priority tests exploratory, no best order selected','Intrabar stop/target chronology unknown','Hourly OHLC excludes manual stop delay and actual Pionex fills','Current minimum/8-decimal assumptions remain unverified historically']
out=Path('reports/audit_signal_execution');out.mkdir(parents=True,exist_ok=True)
(out/'summary.json').write_text(json.dumps(report,indent=2,allow_nan=False))
print(json.dumps({k:v for k,v in report.items() if k not in ['hashes','priority_rotations','forward_returns']},indent=2))
print('priorities',[(x['priority'][0],x['result']['net_return_pct'],x['result']['trades']) for x in orders])
print('forward',json.dumps({k:{a:b for a,b in v.items() if a!='by_symbol'} for k,v in forward.items()},indent=2))
