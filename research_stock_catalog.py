"""GET-only Pionex stock research. No credentials, private endpoints or orders.
Run --download to refresh snapshots; otherwise builds report from saved responses.
Classification is an explicit research subset, NOT inferred from an X suffix.
"""
import argparse, concurrent.futures, datetime, json, urllib.parse, urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parent
DATA=ROOT/'research_details'/'stock_catalog'
ASSETS={'AAPLX':('Apple','equity'),'NVDAX':('NVIDIA','equity'),'TSLAX':('Tesla','equity'),'AMZNX':('Amazon','equity'),'GOOGLX':('Alphabet','equity'),'METAX':('Meta','equity'),'CRCLX':('Circle','equity'),'BMNRX':('BitMine','equity'),'SPYX':('SPDR S&P 500','ETF'),'QQQX':('Invesco QQQ','ETF'),'SLVX':('SLV silver exposure','ETF'),'USOX':('USO oil exposure','ETF'),'PAXG':('Pax Gold','gold token'),'XAUT':('Tether Gold','gold token')}
HISTORY=['NVDAX','AAPLX','TSLAX','AMZNX','GOOGLX','SPYX','QQQX']
MISSING=['TSMX','SPCX','SKHY','SOXLX','MSTRX','MUX','MSFTX']
def fetch(job):
 name,path,params=job
 url='https://api.pionex.com'+path+'?'+urllib.parse.urlencode(params)
 try:
  req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
  with urllib.request.urlopen(req,timeout=25) as r: obj=json.load(r)
 except Exception as e: obj={'result':False,'error_type':type(e).__name__}
 (DATA/(name+'.json')).write_text(json.dumps(obj,indent=2))
 print(name,obj.get('result'),flush=True)
def read(name):
 f=DATA/(name+'.json');return json.loads(f.read_text()) if f.exists() else {}
def iso(ms):return datetime.datetime.fromtimestamp(ms/1000,datetime.timezone.utc).isoformat()
def main(download=False):
 DATA.mkdir(parents=True,exist_ok=True)
 if download:
  jobs=[('all_rules','/api/v1/common/symbols',{}),('all_tickers','/api/v1/market/tickers',{})]
  jobs += [('depth_'+s,'/api/v1/market/depth',{'symbol':s+'_USDT','limit':20}) for s in ASSETS]
  jobs += [('daily_'+s,'/api/v1/market/klines',{'symbol':s+'_USDT','interval':'1D','limit':500}) for s in HISTORY]
  jobs += [('rule_'+s,'/api/v1/common/symbols',{'symbols':s+'_USDT'}) for s in MISSING]
  with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:list(ex.map(fetch,jobs))
 rules=read('all_rules');tickers=read('all_tickers')
 if not rules.get('result') or not tickers.get('result'):raise RuntimeError('Missing successful catalog snapshots')
 rs={x['symbol']:x for x in rules['data']['symbols']};ts={x['symbol']:x for x in tickers['data']['tickers']}
 report={'generated_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'balance_reference_USDT':16.38048824,'scope':'Public API research subset, not exhaustive UI catalog or account eligibility','api_catalog_counts':{'rules':len(rs),'tickers':len(ts)},'spot_stock_fee_per_fill':0.001,'assets':[],'daily_history':[],'unresolved_symbols':{},'limitations':['One order-book snapshot per asset; spreads and depth can change.','API enable flag does not establish UI/account eligibility.','Current API minimums are not historical order rules.','Stock histories include zero-volume days; candle existence does not prove executable fills.','Daily histories include current unfinished UTC day; exclude it in simulations.','Minimum 10 USDT exceeds previous 30% exposure limit on this balance.','No event strategy tested here; corporate actions and issuer tracking still need verification.'],'sources':['https://www.pionex.com/blog/xstocks/','https://www.pionex.com/blog/pionex-tokenized-stocks-fees-explained/','https://www.pionex.com/blog/top-us-stocks-may-2026/']}
 for s,(name,kind) in ASSETS.items():
  sym=s+'_USDT';r=rs.get(sym,{});t=ts.get(sym,{})
  d=read('depth_'+s)
  if not d and s=='NVDAX':d=read('nvda_depth')
  book=d.get('data',{});b=book.get('bids',[]);a=book.get('asks',[])
  row={'symbol':sym,'name':name,'kind':kind,'api_enable':r.get('enable'),'minAmount_USDT':r.get('minAmount'),'minTradeSize':r.get('minTradeSize'),'basePrecision':r.get('basePrecision'),'price':t.get('close'),'rolling24h_amount_USDT':t.get('amount'),'ticker_time':t.get('time')}
  if a and b:
   ask=float(a[0][0]);bid=float(b[0][0]);row.update({'book_utc':iso(book['updateTime']),'bid':bid,'ask':ask,'spread_percent':100*(ask-bid)/((ask+bid)/2),'best_ask_notional_USDT':ask*float(a[0][1]),'best_bid_notional_USDT':bid*float(b[0][1])})
  report['assets'].append(row)
 for s in HISTORY:
  o=read('daily_'+s)
  if not o and s=='NVDAX':o=read('nvda_daily')
  bars=o.get('data',{}).get('klines',[])
  if not bars:continue
  times=sorted(x['time'] for x in bars);today=int(datetime.datetime.now(datetime.timezone.utc).timestamp()//86400)*86400000
  report['daily_history'].append({'symbol':s+'_USDT','bars':len(bars),'first_utc':iso(times[0]),'last_utc':iso(times[-1]),'duplicates':len(times)-len(set(times)),'missing_calendar_days':int((times[-1]-times[0])/86400000+1)-len(set(times)),'zero_volume_bars':sum(float(x['volume'])==0 for x in bars),'completed_bars':sum(x['time']<today for x in bars)})
 for s in MISSING:
  o=read('rule_'+s);report['unresolved_symbols'][s]={'result':o.get('result'),'code':o.get('code'),'message':o.get('message'),'interpretation':'API did not return rules; does not establish absence from UI.'}
 out=ROOT/'reports'/'stock_catalog';out.mkdir(parents=True,exist_ok=True)
 (out/'summary.json').write_text(json.dumps(report,indent=2))
 print(json.dumps({'assets':len(report['assets']),'histories':report['daily_history'],'minAmounts':sorted(set(x['minAmount_USDT'] for x in report['assets']))},indent=2))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--download',action='store_true');main(p.parse_args().download)
