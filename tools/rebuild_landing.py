#!/usr/bin/env python3
"""Rebuild the TriggerBOFF root landing page from MEASUREMENTS.json.

Everything is positioned at measured coordinates taken from the Figma node tree. Nothing is
invented, nothing is guessed: the frame is 1440x9398 and every region is emitted at its own
x/y/w/h with its own fill, gradient, radius and type.

The nine numbered circles in the design are a KEY, not artwork. Their geometry is used to
resolve which region each sheet-row asset occupies, and then they are not drawn.

Asset sizing: each circle sits over an image rectangle, and that rectangle IS the region the
animation occupies. Sizing from a guessed box clipped the content badly (the pixel diff read
47/255 across the chat band), so regions are resolved from the design itself.

Order matters: regions are resolved before anything is drawn, because the drawing pass must
know which static images an asset covers.

Output: app/page.tsx, plus public/landing/* served by the app.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

ROOT = Path("/data/.hermes/landing-sections")
REPO = Path("/data/repos/triggerboff")
PUBLIC = REPO / "public" / "landing"
# The rebuild is NOT routed. Writing straight to app/page.tsx overwrote the live
# design twice. It renders to a scratch path until it passes a visual check.
OUT = Path("/data/.hermes/landing-sections/REBUILD_page.tsx")

# circle number -> asset. The circle marks where the asset goes.
ASSETS = {
    1: ("section1.html", "frame"), 2: ("section2.html", "frame"),
    3: ("section3.mp4", "still"),  4: ("section4/43.bin", "overlay"),
    5: ("section5.html", "frame"), 6: ("section6.html", "frame"),
    7: ("section7.html", "frame"), 8: ("section8.html", "frame"),
    9: ("section9.html", "frame"),
}

FONT_MAP = {
    "GT America": "var(--font-sans)",
    "GT Pressura Mono Trial": "var(--font-mono)",
    "CircularXX Trial": "var(--font-sans)",
}


def dedupe(decls):
    """One property per name. Duplicated keys are a TS1117 build failure."""
    seen, out = set(), []
    for d in decls:
        key = d.split(":", 1)[0].strip()
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def map_font(name):
    return FONT_MAP.get(name, "var(--font-sans)")


def is_circle(n):
    return (n["type"] == "TEXT"
            and re.fullmatch(r"[1-9]", (n.get("text") or "").strip()) is not None
            and (n.get("size") or 0) > 60)


def esc(s):
    return (s.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
             .replace("<", "&lt;").replace(">", "&gt;"))


def css_bg(fills):
    for f in fills or []:
        if f.get("type") == "SOLID" and f.get("color"):
            return f["color"]
    for f in fills or []:
        if f.get("type", "").startswith("GRADIENT") and f.get("stops"):
            stops = ", ".join(f"{s['color']} {round(s['pos'] * 100)}%"
                              for s in f["stops"] if s.get("color"))
            if stops:
                return f"linear-gradient(180deg, {stops})"
    return None


def image_ref(n):
    for f in n.get("fills") or []:
        if f.get("type") == "IMAGE" and f.get("ref"):
            return f["ref"]
    return None


def main():
    d = json.loads((ROOT / "MEASUREMENTS.json").read_text())
    frame, nodes = d["frame"], d["nodes"]
    FW, FH = frame["w"], frame["h"]

    # 1. the key: the nine numbered circles.
    circles = {}
    for n in nodes:
        if is_circle(n):
            num = int(n["text"].strip())
            c = dict(n)
            c["num"] = num
            circles.setdefault(num, c)
    print(f"  circles (key): {sorted(circles)}")

    # 2. regions. The circles are ordered down the page, so pair them in order rather than
    # by nearest-match: greedy nearest gave section 4 the wrong image and stole section 9's.
    # Circles sharing a y are one row (3 and 4 are the chat section) and share a region.
    rows, seen_y = [], {}
    for num in sorted(circles):
        y = circles[num]["y"]
        if y in seen_y:
            rows[seen_y[y]]["nums"].append(num)
        else:
            seen_y[y] = len(rows)
            rows.append({"y": y, "nums": [num]})

    # image rectangles used by the page, in page order, excluding the footer banner's.
    rects = sorted(
        [n for n in nodes if image_ref(n) and n["w"] > 200 and n["y"] < 8000],
        key=lambda r: r["y"],
    )
    resolved = {}
    if len(rows) != len(rects):
        print(f"  !! {len(rows)} rows vs {len(rects)} image regions - pairing will be partial")
    for row, rect in zip(rows, rects):
        for num in row["nums"]:
            resolved[num] = rect
    print("  resolved regions (ordered pairing, not nearest-match):")
    for row, rect in zip(rows, rects):
        tag = "+".join(str(n) for n in row["nums"])
        print(f"    section {tag:<4} y={row['y']:>5} -> x={rect['x']:>5} y={rect['y']:>5} "
              f"{rect['w']}x{rect['h']}  ({rect['name']})")

    covered = {(r["x"], r["y"]) for r in resolved.values() if r}

    # 3. stage the assets for the app to serve.
    PUBLIC.mkdir(parents=True, exist_ok=True)
    for num, (f, kind) in ASSETS.items():
        if f.endswith(".mp4"):
            continue  # 28MB exceeds the 20MB ceiling; section 3 uses the still instead
        src = ROOT / f
        if not src.exists():
            print(f"  !! missing {f}")
            continue
        dest = PUBLIC / ("luca-overlay.js" if kind == "overlay" else f.replace("/", "_"))
        shutil.copy2(src, dest)
    for n in nodes:
        ref = image_ref(n)
        if ref:
            src = ROOT / "figma-images" / f"{ref}.png"
            if src.exists():
                shutil.copy2(src, PUBLIC / f"{ref}.png")
    print(f"  staged assets -> {PUBLIC}")

    # 4. draw: every painted region and every text layer, at measured coordinates.
    painted, texts = [], []
    for n in nodes:
        if is_circle(n) or n["type"] == "ELLIPSE":
            continue
        if n.get("text"):
            decls = ["position:'absolute'", f"left:{n['x']}", f"top:{n['y']}",
                     f"width:{max(n['w'], 8)}", f"height:{max(n['h'], 8)}"]
            if n.get("font"):
                decls.append(f"fontFamily:'{map_font(n['font'])}'")
            if n.get("size"):
                decls.append(f"fontSize:'{n['size']}px'")
            if n.get("weight"):
                decls.append(f"fontWeight:{n['weight']}")
            if n.get("lh"):
                decls.append(f"lineHeight:'{round(n['lh'])}px'")
            if n.get("ls"):
                decls.append(f"letterSpacing:'{n['ls']}px'")
            if n.get("color"):
                decls.append(f"color:'{n['color']}'")
            if n.get("align"):
                decls.append(f"textAlign:'{str(n['align']).lower()}'")
            style = "{" + ",".join(dedupe(decls)) + "}"
            texts.append('      <div className="n" style={' + style + '}>{`'
                         + esc(n["text"]) + '`}</div>')
            continue
        ref = image_ref(n)
        if ref:
            continue  # placeholder: replaced by the sheet asset, never rendered as static art
        bg = css_bg(n.get("fills"))
        if bg and n["w"] > 2 and n["h"] > 2:
            radius = f"borderRadius:'{n['radius']}px'," if n.get("radius") else ""
            painted.append(
                f'      <div style={{{{position:"absolute",left:{n["x"]},top:{n["y"]},'
                f'width:{n["w"]},height:{n["h"]},{radius}background:\'{bg}\'}}}} />'
            )

    # 5. the assets, in the regions the key resolved to.
    assets = []
    for num in sorted(resolved):
        r = resolved[num]
        if not r or num not in ASSETS:
            continue
        f, kind = ASSETS[num]
        if not (ROOT / f).exists():
            continue
        pub = "luca-overlay.js" if kind == "overlay" else f.replace("/", "_")
        box = (f"position:'absolute',left:{r['x']},top:{r['y']},"
               f"width:{r['w']},height:{r['h']}")
        if kind == "still":
            # The 28MB film exceeds the 20MB ceiling everywhere it has to travel. The overlay
            # demo ships the same shot as a 443KB still, which is what the component was built
            # to sit on. Use that until the film is compressed.
            assets.append(
                f'      <img src="/landing/harbour-still.jpg" alt="" '
                f'style={{{{{box},objectFit:"cover"}}}} />'
            )
        elif kind == "video":
            assets.append(
                f'      <video className="n" src="/landing/{pub}" autoPlay muted loop playsInline '
                f'controls style={{{{{box},objectFit:"cover"}}}} />'
            )
        else:
            assets.append(
                f'      <iframe className="n" src="/landing/{pub}" title="section {num}" '
                f'scrolling="no" loading="lazy" '
                f'style={{{{{box},border:0}}}} />'
            )
    print(f"  painted {len(painted)}  text {len(texts)}  assets {len(assets)}")

    style_css = (
        ".lr{width:100%;overflow-x:hidden;background:#fff}"
        f".page{{position:relative;width:{FW}px;height:{FH}px;"
        f"transform-origin:top left;background:#fff}}"
        ".lr .n{position:absolute;margin:0}"
    )
    OUT.write_text(
        "/* GENERATED by tools/rebuild_landing.py from MEASUREMENTS.json - do not hand-edit.\n"
        f" * Figma frame 116:3668, {FW}x{FH}. Positions, fills, gradients and type are measured.\n"
        " * The nine numbered circles are a key: their geometry places the assets and they are\n"
        " * not drawn. Asset boxes come from the design's own image rectangles.\n"
        " */\n"
        '"use client";\n'
        'import { useEffect, useRef } from "react";\n\n'
        "export default function Page() {\n"
        "  const wrap = useRef<HTMLDivElement>(null);\n"
        "  useEffect(() => {\n"
        "    const el = wrap.current;\n"
        "    if (!el) return;\n"
        f"    const fit = () => {{\n"
        f"      const s = Math.min(1, window.innerWidth / {FW});\n"
        "      el.style.transform = `scale(${s})`;\n"
        "      if (el.parentElement) el.parentElement.style.height = `${"
        f"{FH} * s}}px`;\n"
        "    };\n"
        "    fit();\n"
        '    window.addEventListener("resize", fit);\n'
        '    return () => window.removeEventListener("resize", fit);\n'
        "  }, []);\n"
        "  return (\n"
        '    <div className="lr">\n'
        '      <div ref={wrap} style={{ position: "relative" }}>\n'
        + "\n".join(painted) + "\n" + "\n".join(texts) + "\n" + "\n".join(assets) + "\n"
        "      </div>\n"
        "    </div>\n"
        "  );\n"
        "}\n"
    )
    print(f"  wrote {OUT} ({OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
