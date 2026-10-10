# Write "Ondoway GTM Advice v2.html": deck figures come from numbers.json (the simulator's model).
import json,re,sys,base64
src,nums,out=sys.argv[1:4]
s=open(src).read(); n=json.load(open(nums))
def M(v): return f"${v/1e6:.1f}M" if abs(v)>=1e6 else (f"${v/1e6:.2f}M" if abs(v)>=1e5 else f"${round(v/1e3)}K")
def d2(v): return f"${v:,.2f}"
L=n['lines']; base,m1,m3=L
cities=n['cities']
def rep(old,new,count=1):
    global s
    c=s.count(old); assert c==count,(old,c); s=s.replace(old,new)
def rep_in(start_marker,old,new):
    # replace first occurrence of old after start_marker (slide-scoped)
    global s
    i=s.index(start_marker); j=s.index(old,i); s=s[:j]+new+s[j+len(old):]

# --- Unit economics slide ---
U='>What has to be true<'
rep('>Every pass makes money. New York covers the team at 31,200 passes a year.<',f'>Every pass makes money. Today’s costs are covered at {n["be"]} passes a month.<')
rep('>Blended Average pass<','>Average pass, per party<')
rep_in(U,'>$26.99<',f'>{d2(n["price"])}<')
rep('>App Store fee, 30%<',f'>App Store fee, {round(n["feeRate"]*100)}%<')
rep('>−$8.10<',f'>−{d2(n["fee"])}<')
rep('>AI and delivery<',f'>AI writing and voice, {n["tours"]:g} tours<')
rep('>−$0.25<',f'>−{d2(n["ai"])}<')
rep('>Ticket commission<','>Ticket commission, not built yet<')
rep('>+$1.08<','>$0.00<')
rep('>Cost to win them, assumed<','>Cost to win them<')
rep('>−$5.00<','>to prove<')
rep('>Profit per customer<','>Profit before marketing<')
rep_in(U,'>$14.72<',f'>{d2(n["contrib"])}<')
rep('>55% of the pass price. We still break even if a customer costs up to $19.72 to win.<',
    f'>{round(n["margin"]*100)}% of the pass price. Paid ads at ~$3 an install and 2.1% paying cost ~${round(n["cac"],-1):.0f} a buyer, so we grow through search, shares and partners first.<')
rep('>NEW YORK Break-even<','>BREAK-EVEN TODAY<')
rep('>0.12%<',f'>{n["be"]} / mo<')
rep(">of New York's travel parties covers every fixed cost. About 2,600 passes a month.<",
    f'>passes cover every fixed cost: ${n["fixed"]:,.0f} a month in AI tools and servers. Founders are unpaid.<')
rep('>Our target is 1% in every city we enter, about eight times that.<',f'>Each $10K-a-month hire needs about {n["perHire"]} more passes a month.<')
rep('>YEAR 1-3 Up to 23 CITIES (Y1:5, Y2:14 Y3:23)<',f'>PROFIT, YEARS 1–3 · {cities[0]} / {cities[1]} / {cities[2]} CITIES<')
rep('>1% buy, our plan · $14.3M Y3<',f'>{round(m3["k"])} in 1,000 tour-app users buy · {M(m3["profit"][2])} Y3<')
rep('>0.5% · $6.6M<',f'>{round(m1["k"])} in 1,000 · {M(m1["profit"][2])}<')
rep('>0.1% · $0.5M<',f'>{round(base["k"])} in 1,000, base case · {M(base["profit"][2])}<')
for old,new in [('>$15M<','>$3M<'),('>$10M<','>$2M<'),('>$5M<','>$1M<')]: rep_in(U,old,new)
rep('>At a tenth of our target, we still break even by Year 2. Blended $26.99 pass, full plan costs included.<',
    f'>Per 1,000 travellers who already use tour apps in our cities. Base: 10% install us and 2.1% of them pay (app-store median). No ads; App Store fee rises to 30% above $1M a year.<')
rep('>Two numbers to prove: how many travel parties buy, and what it costs to win each one.<','>Two numbers to prove: how many tour-app users install us, and how many of them pay.<')
# chart image -> SVG from the model (same scale as the original: gridlines 20 / 101.2 / 182.5 / 263.8)
y=lambda v: 263.8-v/3e6*243.8; X=[30,270,510]
def line(vals,color,w,dash=''):
    pts=' L'.join(f'{x} {y(v):.1f}' for x,v in zip(X,vals)); da=f' stroke-dasharray="{dash}"' if dash else ''
    return f'<path d="M{pts}" fill="none" stroke="{color}" stroke-width="{w}"{da}/>'+''.join(f'<circle cx="{x}" cy="{y(v):.1f}" r="6" fill="{color}"/>' for x,v in zip(X,vals))
svg=('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 540 290" width="540" height="290">'
 +''.join(f'<path d="M0 {g} H540" stroke="#dfd5c4" stroke-width="1"/>' for g in (20,101.2,182.5))+'<path d="M0 263.8 H540" stroke="#23262e" stroke-width="1.5"/>'
 +line(base['profit'],'#A34F24',3,'7 6')+line(m1['profit'],'#7BB2F5',3)+line(m3['profit'],'#2C6CC0',4)+'</svg>')
i=s.index(U); j=s.index('data-img-path="1.2.2.1"',i); k=s.index('src="',j)+5; e=s.index('"',k)
s=s[:k]+'data:image/svg+xml;base64,'+base64.b64encode(svg.encode()).decode()+s[e:]

# --- Market slide ---
rep(">International arrivals each year across the world's top 100 city destinations. Domestic city travel not included.<",
    ">International arrivals in 2025 across the world's top 100 city destinations (Euromonitor). Domestic city travel not included.<")
rep('>At a $27 average pass, 1% of those travel parties is roughly $95M a year<',
    f'>If 13% use tour apps, 10% of them install us and 2.1% pay, that is about {M(n["worldBase"])} a year<')
rep('>52.4M<',f'>{n["nycParties"]*2/1e6:.1f}M<')
rep('>Leisure visitors to New York City each year. No language carveout, so international visitors are addressable too.<',
    '>Overnight leisure visits to New York City in 2025 (NYC Tourism + Conventions). Day-trips and business travel left out.<')
rep('>37K = $1M<',f'>{round(n["passesPer1M"]/1e3)}K = $1M<')
rep(">At a $27 average pass, 37,000 passes a year is $1M in revenue. That is about 0.14% of NYC's ~26M leisure travel parties.<",
    f">At a {d2(n['price'])} average pass, {round(n['passesPer1M'],-3):,.0f} passes a year is $1M in revenue. That is about {n['nycShareFor1M']*100:.2f}% of NYC's ~{n['nycParties']/1e6:.0f}M overnight leisure travel parties.<")
rep('>At our 1% target, New York alone is ≈ $7M a year<',f'>At our base funnel New York alone is ≈ {M(n["nycBase"])} a year. Every step up in installs or paying moves it straight up.<')
rep('>13% of US and European travellers already use self-guided mobile tours.<','>13% of US and European travellers took a self-guided mobile tour in the past year (Arival, 2026).<')

# --- Pricing slide ---
rep('>Solo. $29.99 for a couple, $39.99 for a group of up to 5. One city, five days from first use. First tour free.<',
    '>Solo or pair. $34.99 for a household of up to 5. One city, five days from first use. First walk free.<')
rep('>A guided NYC walking tour costs $30 to $100 per person. Ondoway is your guide for the whole trip, from $19.99.<',
    '>A guided NYC group walk costs $30 to $60 per person. Ondoway is your guide for the whole trip, from $19.99 for two.<')

# --- Competition slides ---
rep('>$19.99 / trip<','>$19.99 / trip, for two<')
rep('>$30–100 / person<','>$30–60 / person<')
rep('>$200–450 / tour, whole group<','>$250–585 / tour, whole group<')
rep('>LE WALK<','>ROUTES (EX-LE WALK)<')
rep('>$4.1M funded, ex-TikTok<','>$4.1M funded; passes from $5.99<')
s,c=re.subn(r"changes, and Le Walk, the best-funded player, <font[^>]*>isn't live in New York\.</font>",
    "changes. Routes (formerly Le Walk), the best-funded player, <font color=\"#7bb2f5\" style=\"color: rgb(123, 178, 245); -webkit-text-fill-color: currentcolor;\">sells fixed tours from $5.99.</font>",s); assert c==1

# --- Why now ---
rep('>&gt;50%<','>56%<')
rep('>Arival / Phocuswright, 2026<','>Phocuswright, 2026<')

# --- Risks ---
rep('>Customers cost more than $5 to win<','>Paid ads cost more than a pass earns<')
rep('>Our biggest unproven number<','>Our biggest unproven number<')
rep('>We clear $14.72 per pass, so we still break even at $19.72 to win one. Cheapest channels first: hotel QR cards, creators, a free first tour. Measured in New York before city two.<',
    f'>At ~$3 an install and 2.1% paying, an ad-won buyer costs ~${round(n["cac"],-1):.0f}; a pass clears {d2(n["contrib"])}. So we grow through search, shares, hotel QR cards and a free first walk, measured in New York before city two.<')
rep('>Plan assumes 1% of travel parties<','>Base case: 2.1% of installs pay<')
rep('>We cover fixed costs at 0.09%, and Year 3 is still profitable at 0.1%. No new city opens until New York hits its target.<',
    f'>Today’s costs are covered at {n["be"]} passes a month. Even the low case (1% pay, 3% install) is about break-even by Year 3. No new city opens until New York hits its target.<')
open(out,'w').write(s); print('ok',out)
