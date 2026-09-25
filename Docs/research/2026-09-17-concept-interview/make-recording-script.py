#!/usr/bin/env python3
"""Generate recording-script.md from scripts.md.

scripts.md has always said the recording script is generated from it, but until now that was
done by hand, and on 2026-09-24 a two-word edit left deepdive_7's stated runtime one second
stale in two places while the file's own total stopped deriving from its parts. Every number
in the recording script is derived, so nothing is typed twice:

    python3 make-recording-script.py        # writes recording-script.md
    python3 make-recording-script.py --check # exits 1 if the file is out of date

Runtimes are word count at 150 words a minute, the pace the script asks the reader for.
"""
from __future__ import annotations
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).parent
WPM = 150

PRONUNCIATION = (
    "Pronunciation: **Arad** AH-rahd · **Callery** CAL-er-ee · **repoussé** ruh-poo-SAY ·\n"
    "**Isère** ee-ZAIR · **Laboulaye** lah-boo-LAY · **Bartholdi** bar-TOL-dee · **Upjohn** UP-john ·\n"
    "**Frazee** fray-ZEE · **Ithiel** ITH-ee-el · **Bitter** BIT-er · **Lazarus** LAZ-uh-rus ·\n"
    "**Pulitzer** PULL-it-ser · **Schuyler** SKY-ler · **Jenny Lind** LIND (short i)."
)


def blocks(md: str) -> list[tuple[str, str, list[str]]]:
    """(slot id, title, paragraphs) for every `## id — title` section with a Script block."""
    out = []
    for part in re.split(r"(?m)^## ", md)[1:]:
        head = part.split("\n", 1)[0].strip()
        if "**Script**" not in part or " — " not in head:
            continue
        sid, title = head.split(" — ", 1)
        body = part.split("**Script**", 1)[1].split("**Sources**", 1)[0]
        paras = [re.sub(r"[ \t]+", " ", p).strip() for p in body.strip().split("\n\n") if p.strip()]
        out.append((sid.strip(), title.strip(), paras))
    return out


def mmss(words: int) -> str:
    secs = round(words / WPM * 60)
    return f"{secs // 60}:{secs % 60:02d}"


def render(md: str) -> str:
    bs = blocks(md)
    total = sum(sum(len(p.split()) for p in paras) for _, _, paras in bs)
    lines = [
        "# Recording script — read these, name the files exactly",
        "",
        "Generated from `scripts.md` by `make-recording-script.py`. Edit the scripts, not this file.",
        "",
        "One file per block. **Name each file after the id in its heading**, with `.m4a`, `.mp3` or",
        f"`.wav`. Read unhurried, about {WPM} words a minute. **Each blank line is a pause.**",
        "",
        "These are individually selectable beats, not a sequence that must all play. `fh_maya` and",
        "`fh_dan` are the two treatments of Federal Hall heard side by side, so keep their delivery",
        "identical — any difference in energy reads as one person's story being better.",
        "",
        f"Total: {len(bs)} blocks, about {mmss(total)} of audio.",
        "",
        "| File | What it is | Length |",
        "|---|---|---|",
    ]
    for sid, title, paras in bs:
        lines.append(f"| `{sid}` | {title} | {mmss(sum(len(p.split()) for p in paras))} |")
    lines += ["", PRONUNCIATION, "", "---", ""]
    for sid, title, paras in bs:
        lines.append(f"## {sid} · {title} · {mmss(sum(len(p.split()) for p in paras))}")
        lines.append("")
        lines += [p + "\n" for p in paras]
        lines += ["---", ""]
    return "\n".join(lines)


def main() -> int:
    want = render((HERE / "scripts.md").read_text())
    out = HERE / "recording-script.md"
    if "--check" in sys.argv:
        if out.exists() and out.read_text() == want:
            print("recording-script.md is up to date")
            return 0
        print("recording-script.md is STALE — run: python3 make-recording-script.py")
        return 1
    out.write_text(want)
    print(f"wrote {out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
