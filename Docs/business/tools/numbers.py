# Extract the simulator's model and compute every deck figure from it (single source).
import re,subprocess,json,sys
html=open('/Users/sairambkrishnan/git/ondoway/docs/business/ondoway-financial-model.html').read()
js=re.search(r'<script>(.*)</script>',html,re.S).group(1).split('/* ---------- Controls')[0].replace('"use strict";','')
js+=r'''
const g=preset("grounded"), s=simulate(g), Y=years(s), r0=s.rows[0];
const fee=s.price*r0.rate, ai=s.toursPerBuyer*s.tc, comm=g.commOn==="on"?g.bookShare*s.ticketsPerBuyer*g.ticket*g.commRate:0, contrib=s.price-fee-ai+comm;
const per=Y[2].revenue/(g.ourShare*g.conv); // Y3 revenue per unit of buy-rate among tour-app users (linear, no ads)
const y3p=k=>years(simulate({...g,ourShare:(k/1000)/g.conv}))[2].profit;
const seek=t=>{let a=0,b=100;for(let i=0;i<60;i++){const m=(a+b)/2;(y3p(m)<t)?a=m:b=m}return a};
const k1=seek(1e6),k3=seek(3e6);
const lines=[g.ourShare*g.conv*1000,k1,k3].map(k=>{const p={...g,ourShare:(k/1000)/g.conv};const yy=years(simulate(p));return {k,profit:yy.map(v=>v.profit),revenue:yy.map(v=>v.revenue)}});
const lo=years(simulate(preset("low"))), hi=years(simulate(preset("high")));
const nyc=g.nycVisits/g.partySize, world=702e6/g.partySize, rate=g.categoryUse*g.ourShare*g.conv;
console.log(JSON.stringify({price:s.price,fee,feeRate:r0.rate,ai,comm,tours:s.toursPerBuyer,tc:s.tc,contrib,margin:contrib/s.price,fixed:r0.fixed,
 be:Math.ceil(r0.fixed/contrib),perHire:Math.ceil(10000/contrib),cac:g.cpi/g.conv,
 cities:Y.map(v=>v.live),lines,needFor1M:1e6/per*1000,needFor3M:3e6/per*1000,
 lowY:lo.map(v=>v.profit),baseProfit:Y.map(v=>v.profit),hiY:hi.map(v=>v.revenue),
 nycParties:nyc,nycBase:nyc*rate*s.price,passesPer1M:1e6/s.price,nycShareFor1M:1e6/s.price/nyc,
 worldParties:world,worldBase:world*rate*s.price}));
'''
open(sys.argv[1]+'/model_numbers.js','w').write(js)
print(subprocess.run(['node',sys.argv[1]+'/model_numbers.js'],capture_output=True,text=True,check=True).stdout)
