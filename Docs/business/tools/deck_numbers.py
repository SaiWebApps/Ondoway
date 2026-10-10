# Compute every deck figure from the money model (Docs/business/model.js, the single source).
# Usage: python deck_numbers.py <out_dir>  -> prints JSON; build_deck.py reads it as numbers.json.
import json
import subprocess
import sys
from pathlib import Path

MODEL = Path(__file__).resolve().parents[1] / "model.js"
WORLD_TOP100_ARRIVALS = 702e6  # Euromonitor Top 100 City Destinations, 5 Dec 2025

js = f"""
const M=require({json.dumps(str(MODEL))});
const g=M.preset("base"), s=M.simulate(g), Y=M.years(s), r0=s.rows[0], u=M.perPass(g);
const y3p=k=>M.years(M.simulate({{...g,ourShare:(k/1000)/g.conv}}))[2].profit;
const seek=t=>{{let a=0,b=100;for(let i=0;i<60;i++){{const m=(a+b)/2;(y3p(m)<t)?a=m:b=m}}return a}};
const lines=[g.ourShare*g.conv*1000,seek(1e6),seek(3e6)].map(k=>{{
  const yy=M.years(M.simulate({{...g,ourShare:(k/1000)/g.conv}}));
  return {{k,profit:yy.map(v=>v.profit),revenue:yy.map(v=>v.revenue)}}}});
const per=Y[2].revenue/(g.ourShare*g.conv);
const lo=M.years(M.simulate(M.preset("low"))), hi=M.years(M.simulate(M.preset("high")));
const nyc=g.nycVisits/g.partySize, world={WORLD_TOP100_ARRIVALS}/g.partySize;
const rate=g.categoryUse*g.ourShare*g.conv;
console.log(JSON.stringify({{price:u.price,fee:u.fee,feeRate:r0.rate,ai:u.tours,freeWalks:u.free,
 comm:u.comm,tours:M.toursPerBuyer(g),tc:M.tourCost(g),contrib:u.total,margin:u.total/u.price,
 floor:M.priceFloor(g),fixed:r0.running,be:u.total>0?Math.ceil(r0.running/u.total):null,
 perHire:u.total>0?Math.ceil(g.hireCost/u.total):null,cac:g.cpi/g.conv,
 cities:Y.map(v=>v.live),lines,needFor1M:1e6/per*1000,needFor3M:3e6/per*1000,
 lowY:lo.map(v=>v.profit),baseProfit:Y.map(v=>v.profit),hiY:hi.map(v=>v.revenue),
 nycParties:nyc,nycBase:nyc*rate*u.price,passesPer1M:1e6/u.price,nycShareFor1M:1e6/u.price/nyc,
 worldParties:world,worldBase:world*rate*u.price}}));
"""
out = Path(sys.argv[1]) / "model_numbers.js"
out.write_text(js)
print(subprocess.run(["node", str(out)], capture_output=True, text=True, check=True).stdout)
