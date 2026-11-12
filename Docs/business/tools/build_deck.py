# Build the GTM advice deck: Docs/business/deck/gtm-deck.template.html filled with numbers.json
# (the money model, via deck_numbers.py) and the app screenshots in Docs/business/deck/screens.
# Usage: python build_deck.py <numbers.json> <out.html>
import base64
import json
import re
import sys
from pathlib import Path

DECK = Path(__file__).resolve().parents[1] / "deck"
TEMPLATE = DECK / "gtm-deck.template.html"
AI_SHARE = "56%"  # Phocuswright, The AI Surge, March 2026

MINUS = "\u2212"
INK, INK2, RULE, SAND, BG = "#23262e", "#5b6069", "#dfd5c4", "#c9bca5", "#f2ece1"
BLUE, RUST, GREEN = "#2c6cc0", "#a34f24", "#17704c"


def usd(v: float) -> str:
    return f"${v:,.2f}"


def money(v: float) -> str:
    a = abs(v)
    s = MINUS if v < 0 else ""
    if a >= 999.5e3:
        return f"{s}${a / 1e6:.1f}M"
    if a >= 1e3:
        return f"{s}${round(a / 1e3)}K"
    return f"{s}${round(a)}"


def screenshots() -> dict[str, str]:
    """Data URIs of the app screenshots, keyed by file name without extension."""
    return {p.stem: "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()
            for p in sorted((DECK / "screens").glob("*.png"))}


CITIES = [("New York", "United States"), ("Rome", "Italy"), ("Tokyo", "Japan"),
          ("Lisbon", "Portugal"), ("Mexico City", "Mexico"), ("Istanbul", "Türkiye"),
          ("Athens", "Greece"), ("London", "United Kingdom"), ("Barcelona", "Spain")]
MONO = 'font-family="Space Mono, monospace" font-weight="700"'
SERIF = 'font-family="Young Serif, Georgia, serif"'


def marquee() -> str:
    return "".join(f"<span>{c}<small>{k}</small><b></b></span>" for c, k in CITIES)


def waterfall(n: dict) -> str:
    """The per-pass sum as floating bars on one scale: 10px per dollar."""
    steps = [
        ("AVERAGE PRICE", n["price"], BLUE),
        ("APP STORE 15%", -n["fee"], RUST),
        (f"AI TOURS · {n['tours']:g} DAYS", -n["ai"], RUST),
        ("TICKET COMMISSION", n["comm"], GREEN),
    ]
    w, h, base_y, bar, gap, k = 1120, 400, 330, 150, 84, 10
    out = [f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
           f'aria-label="What one pass earns">',
           f'<line x1="0" y1="{base_y}" x2="{w}" y2="{base_y}" stroke="{INK}" stroke-width="2"/>']
    level, x, prev_top = 0.0, 20, None
    for label, delta, color in [*steps, ("PROFIT PER PASS", None, BLUE)]:
        if delta is None:
            lo, hi, value = 0.0, level, level
        else:
            lo, hi = sorted((level, level + delta))
            value = delta
            level += delta
        top = base_y - hi * k
        if prev_top is not None:
            out.append(f'<line x1="{x - gap}" x2="{x}" y1="{prev_top:.1f}" y2="{prev_top:.1f}" '
                       f'stroke="{INK2}" stroke-dasharray="3 4"/>')
        out.append(f'<rect x="{x}" y="{top:.1f}" width="{bar}" '
                   f'height="{max((hi - lo) * k, 3):.1f}" fill="{color}"/>')
        sign = "+" if delta is not None and delta > 0 and label != "AVERAGE PRICE" else ""
        shown = usd(value) if value >= 0 else MINUS + usd(-value)
        big = delta is None or label == "AVERAGE PRICE"
        size = 34 if big else 24
        out.append(f'<text x="{x + bar / 2}" y="{top - 12:.1f}" text-anchor="middle" {SERIF} '
                   f'font-size="{size}" fill="{INK}">{sign}{shown}</text>')
        out.append(f'<text x="{x + bar / 2}" y="{base_y + 28}" text-anchor="middle" {MONO} '
                   f'font-size="12" fill="{INK2}">{label}</text>')
        prev_top = base_y - level * k
        x += bar + gap
    out.append(f'<text x="20" y="{base_y + 50}" {MONO} font-size="11" '
               f'fill="{INK2}">SOLO {n["priceSolo"]:.2f} · COUPLE {n["priceCouple"]:.2f} · '
               f'GROUP {n["priceGroup"]:.2f}</text>')
    out.append(f'<text x="{20 + 4 * (bar + gap) + bar / 2}" y="{base_y + 50}" '
               f'text-anchor="middle" {MONO} font-size="12" fill="{BLUE}">'
               f'{round(n["margin"] * 100)}% OF THE PRICE</text>')
    out.append("</svg>")
    return "".join(out)


def dot_grid(one_in: int) -> str:
    """One dot per travel party, one highlighted: the share of parties that buys a pass."""
    cols = 35
    rows = max(1, round(one_in / cols))
    w, h = cols * 24, rows * 24
    cx, cy = 18 * 24 + 12, (rows // 2) * 24 + 12
    return (f'<svg viewBox="0 0 {w} {h}" width="{w * 0.9:.0f}" height="{h * 0.9:.0f}" role="img" '
            f'aria-label="{cols * rows} dots, one highlighted">'
            f'<defs><pattern id="pd" width="24" height="24" patternUnits="userSpaceOnUse">'
            f'<circle cx="12" cy="12" r="6" fill="{SAND}"/></pattern></defs>'
            f'<rect width="{w}" height="{h}" fill="url(#pd)"/>'
            f'<rect x="{cx + 14}" y="{cy - 20}" width="190" height="40" fill="{BG}"/>'
            f'<circle cx="{cx}" cy="{cy}" r="11" fill="{RUST}" stroke="{BG}" stroke-width="4"/>'
            f'<line x1="{cx + 12}" y1="{cy}" x2="{cx + 30}" y2="{cy}" stroke="{RUST}" '
            f'stroke-width="2"/><text x="{cx + 36}" y="{cy + 6}" {MONO} font-size="16" '
            f'fill="{RUST}">1 PASS</text></svg>')


def city_path(n: dict) -> str:
    """The gate out of New York, the cities that follow, and year-3 profit by funnel."""
    base, m1, m3 = n["lines"]
    cities = n["cities"]
    out = ['<svg viewBox="0 0 1120 470" width="1120" height="470" role="img" '
           'aria-label="New York, the gate, then more cities; profit by year">',
           f'<path d="M30 70 H1000" stroke="{BLUE}" stroke-width="3" stroke-dasharray="1 10" '
           f'stroke-linecap="round"/>',
           f'<circle cx="30" cy="70" r="13" fill="{RUST}"/>'
           f'<text x="30" y="122" {SERIF} font-size="24" fill="{INK}">New York</text>',
           f'<g stroke="{INK}" stroke-width="3"><line x1="250" y1="20" x2="250" y2="110"/>'
           f'<line x1="330" y1="20" x2="330" y2="110"/><line x1="250" y1="45" x2="330" y2="45"/>'
           f'</g><text x="290" y="140" text-anchor="middle" {MONO} font-size="12" fill="{INK2}">'
           f'500 PAID TOURS</text><text x="290" y="158" text-anchor="middle" {MONO} '
           f'font-size="12" fill="{INK2}">3% OF FREE WALKERS PAY</text><text x="290" y="176" '
           f'text-anchor="middle" {MONO} font-size="12" fill="{INK2}">30% RECOMMEND US</text>']
    for x, c, yr in zip((540, 770, 1000), cities, ("YEAR 1", "YEAR 2", "YEAR 3"), strict=True):
        out.append(f'<circle cx="{x}" cy="70" r="10" fill="{BLUE}"/>'
                   f'<text x="{x}" y="122" text-anchor="middle" {SERIF} font-size="26" '
                   f'fill="{INK}">{c} cities</text><text x="{x}" y="146" text-anchor="middle" '
                   f'{MONO} font-size="12" fill="{INK2}">{yr}</text>')
    top, bottom, hi = 230, 450, max(m3["profit"]) * 1.15
    xs = (540, 770, 1000)

    def y(v: float) -> float:
        return bottom - max(v, 0) * (bottom - top) / hi

    out.append(f'<line x1="520" x2="1110" y1="{bottom}" y2="{bottom}" stroke="{RULE}"/>')
    ends = []
    for line, color in ((m3, RUST), (m1, BLUE), (base, INK)):
        pts = " L".join(f"{x} {y(v):.1f}" for x, v in zip(xs, line["profit"], strict=True))
        out.append(f'<path d="M{pts}" fill="none" stroke="{color}" stroke-width="3"/>')
        ends.append([y(line["profit"][2]), line, color])
    for i in range(1, len(ends)):  # keep end labels at least 40px apart
        ends[i][0] = max(ends[i][0], ends[i - 1][0] + 40)
    for ly, line, color in ends:
        out.append(f'<text x="1016" y="{ly + 4:.1f}" {SERIF} font-size="22" fill="{color}">'
                   f'{money(line["profit"][2])}</text><text x="1016" y="{ly + 20:.1f}" {MONO} '
                   f'font-size="10" fill="{INK2}">{round(line["k"])} IN 1,000 BUY</text>')
    out.append(f'<text x="30" y="{top + 30}" {SERIF} font-size="28" fill="{INK}">'
               f'Year-3 profit</text>'
               f'<text x="30" y="{top + 58}" {MONO} font-size="12" fill="{INK2}">'
               f'BY HOW MANY TOUR-APP USERS BUY</text>')
    out.append("</svg>")
    return "".join(out)


def fields(n: dict) -> dict[str, str]:
    one_in = round(1 / n["nycShareFor1M"], -1)
    return {
        "marquee": marquee(),
        "aiShare": AI_SHARE,
        "aiBar": str(round(440 * int(AI_SHARE.rstrip("%")) / 100)),
        "price": usd(n["price"]),
        "contrib": usd(n["contrib"]),
        "fixed": money(n["fixed"]),
        "be": f"{n['be']:,}" if n["be"] is not None else "no number of",
        "priceSolo": usd(n["priceSolo"]),
        "nycVisitsM": f"{n['nycVisits'] / 1e6:.1f}M",
        "passesK": f"{round(n['passesPer1M'] / 1e3)}K",
        "oneIn": f"{one_in:,.0f}",
        "dotGrid": dot_grid(int(one_in)),
        "waterfall": waterfall(n),
        "cityPath": city_path(n),
    }


def build(numbers_path: str, out_path: str) -> None:
    page = TEMPLATE.read_text()
    shots = screenshots()
    values = fields(json.loads(Path(numbers_path).read_text()))

    def fill(m: re.Match) -> str:
        key = m.group(1)
        if key.startswith("img:"):
            if key[4:] not in shots:
                raise KeyError(f"screenshot {key[4:]!r} not in {DECK / 'screens'}")
            return shots[key[4:]]
        return values[key]

    page = re.sub(r"\{\{([^}]+)\}\}", fill, page)
    Path(out_path).write_text(page)
    size = len(page.encode()) / 1e6
    slides = page.count('class="slide')
    print(f"ok {out_path} ({size:.1f} MB, {slides} slides)")


if __name__ == "__main__":
    build(*sys.argv[1:3])
