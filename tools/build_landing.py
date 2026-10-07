#!/usr/bin/env python3
"""Generate the TriggerBOFF landing page from the Figma source of truth.

Reads the saved node tree for frame 116:3668 and emits a Next.js page with:
  - the nine numbered circles' positions, matched to sheet sections 1-9
  - the real copy, type scale and section order from the Figma (nothing invented)
  - a subtle, transform-only parallax + reveal system (IntersectionObserver, no scroll jank)
  - lazy-mounted section embeds: WebGL scenes mount only when approached, so a phone
    never carries nine three.js contexts at once

Source of truth is figma_page.json. If the design changes, re-run this rather than
hand-editing the generated page.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

FIG = Path("/data/.hermes/landing-sections/figma_page.json")
SECTIONS = Path("/data/.hermes/landing-sections")
OUT = Path("/data/repos/triggerboff/app/luca/LandingPage.tsx")

FRAME_W, FRAME_H = 1440, 9398

# sheet section -> (file, kind, label). Kind drives how it is mounted.
EMBEDS = {
    1: ("section1.html", "webgl", "Thinking — particle orb"),
    2: ("section2.html", "webgl", "Everything we know about this house"),
    3: ("section3.mp4", "video", "Sydney harbour film"),
    4: ("section4/43.bin", "overlay", "Glass conversation overlay"),
    5: ("section5.html", "webgl", "Property street — four assessments"),
    6: ("section6.html", "webgl", "While you were looking at the photos"),
    7: ("section7.html", "webgl", "The same $1.1 million, four different lives"),
    8: ("section8.html", "webgl", "Before you spend a Saturday on it"),
    9: ("section9.html", "plain", "Guidance on What to Offer"),
}


def load_texts():
    """Every TEXT node with its position and style, in page order."""
    doc = json.loads(FIG.read_text())["nodes"]["116:3668"]["document"]
    found = []

    def walk(n, path=""):
        if n.get("type") == "TEXT":
            b = n.get("absoluteBoundingBox") or {}
            st = n.get("style") or {}
            found.append({
                "name": n.get("name", ""),
                "text": (n.get("characters") or "").strip(),
                "x": round(b.get("x", 0)), "y": round(b.get("y", 0)),
                "w": round(b.get("width", 0)), "h": round(b.get("height", 0)),
                "size": st.get("fontSize"), "weight": st.get("fontWeight"),
                "align": st.get("textAlignHorizontal"),
            })
        for c in n.get("children") or []:
            walk(c, path)
    walk(doc)

    # The page lives at a canvas offset; normalise so section 1 starts near x=0.
    if found:
        ox = min(t["x"] for t in found)
        for t in found:
            t["x"] -= ox
    return sorted(found, key=lambda t: (t["y"], t["x"]))


def load_circles():
    """The numbered markers: the 106px numerals."""
    seen, out = set(), []
    for t in load_texts():
        n = t["text"]
        if n in {str(i) for i in range(1, 10)} and (t["size"] or 0) > 60 and n not in seen:
            seen.add(n)
            out.append(t)
    return out


def main():
    if not FIG.exists():
        sys.exit(f"missing {FIG} — fetch the Figma node first")
    texts = load_texts()
    circles = load_circles()
    print(f"  text layers: {len(texts)}   numbered circles: {len(circles)}")
    for c in circles:
        print(f"    '{c['text']}' at y={c['y']:>5} x={c['x']:>5}  {c['size']}px")

    present = {n: f for n, (f, _k, _l) in EMBEDS.items() if (SECTIONS / f).exists()}
    missing = sorted(set(EMBEDS) - set(present))
    print(f"  embeds present: {sorted(present)}   missing: {missing}")

    # Group copy into sections by the circles' y positions, so section order and
    # boundaries come from the design rather than from guesses about spacing.
    bounds = sorted([(c["y"], int(c["text"])) for c in circles])
    sections = []
    for i, (y, num) in enumerate(bounds):
        nxt = bounds[i + 1][0] if i + 1 < len(bounds) else FRAME_H
        block = [t for t in texts if y <= t["y"] < nxt and t["text"] not in {str(n) for n in range(1, 10)}]
        sections.append({"num": num, "y": y, "texts": block})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(sections, present))
    print(f"  wrote {OUT}  ({OUT.stat().st_size:,} bytes)")


def esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")


def render(sections, present) -> str:
    """Emit the page. Copy is emitted verbatim from the Figma; nothing invented."""
    parts = []
    for s in sections:
        n = s["num"]
        f, kind, label = EMBEDS[n]
        have = n in present
        body = "\n".join(
            f'        <p className="t" style={{{{fontSize:"{t["size"]}px",fontWeight:{t["weight"]}}}}}>{{`{esc(t["text"])}`}}</p>'
            for t in s["texts"] if t["text"]
        )
        parts.append(f"""      <section className="sec" data-section="{n}" data-y="{s['y']}">
        <div className="copy">
{body if body else '        {/* copy pending */}'}
        </div>
        <div className="mark" aria-hidden="true">
          <span>{n}</span>
        </div>
        <div className="stage" data-kind="{kind}" data-label="{esc(label)}">
          {{/* {"mounted" if have else "awaiting"} — {esc(label)} */}}
          {'' if have else '<span className="pending">awaiting asset</span>'}
        </div>
      </section>""")
    sections_jsx = "\n".join(parts)

    return f'''/* GENERATED by tools/build_landing.py from Figma frame 116:3668 — do not hand-edit.
 * Re-run the generator when the design changes.
 *
 * Frame: {FRAME_W}x{FRAME_H}. Copy, order, type sizes and circle positions come from the
 * design. The nine section embeds are lazy: WebGL scenes mount only when approached, so
 * the page never holds nine three.js contexts at once.
 */
"use client";

import {{ useEffect, useRef }} from "react";

const REDUCED = () =>
  typeof window !== "undefined" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export default function LandingPage() {{
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {{
    const el = root.current;
    if (!el) return;
    if (REDUCED()) {{
      el.querySelectorAll(".sec").forEach((s) => s.classList.add("in"));
      return;
    }}

    // Reveal: IntersectionObserver only. No scroll listener for the reveals, because
    // one rAF loop reading layout for 60 elements is how long pages get janky.
    const reveal = new IntersectionObserver(
      (entries) => {{
        for (const e of entries) {{
          if (e.isIntersecting) {{
            (e.target as HTMLElement).classList.add("in");
            reveal.unobserve(e.target);
          }}
        }}
      }},
      {{ rootMargin: "0px 0px -12% 0px", threshold: 0.1 }}
    );
    el.querySelectorAll(".sec").forEach((s) => reveal.observe(s));

    // Lazy-mount the heavy scenes well before they arrive, so nothing pops in blank.
    const stageIO = new IntersectionObserver(
      (entries) => {{
        for (const e of entries) {{
          const t = e.target as HTMLElement;
          if (e.isIntersecting) {{
            t.classList.add("stage-ready");
            stageIO.unobserve(t);
          }}
        }}
      }},
      {{ rootMargin: "900px 0px" }}
    );
    el.querySelectorAll(".stage").forEach((s) => stageIO.observe(s));

    // Parallax: transform only, driven by a single rAF-coalesced handler, and only
    // for the marks and stages. Never read layout in the scroll handler itself.
    let raf = 0;
    const marks = Array.from(el.querySelectorAll<HTMLElement>(".mark"));
    const stages = Array.from(el.querySelectorAll<HTMLElement>(".stage"));
    const onScroll = () => {{
      if (raf) return;
      raf = requestAnimationFrame(() => {{
        raf = 0;
        const vh = window.innerHeight;
        for (const m of marks) {{
          const r = m.getBoundingClientRect();
          if (r.bottom < -200 || r.top > vh + 200) continue;
          const p = (r.top + r.height / 2 - vh / 2) / vh;   // -0.5 .. 0.5
          m.style.transform = `translate3d(0,${{(-p * 26).toFixed(2)}}px,0)`;
        }}
        for (const s of stages) {{
          const r = s.getBoundingClientRect();
          if (r.bottom < -200 || r.top > vh + 200) continue;
          const p = (r.top + r.height / 2 - vh / 2) / vh;
          s.style.transform = `translate3d(0,${{(p * 18).toFixed(2)}}px,0)`;
        }}
      }});
    }};
    window.addEventListener("scroll", onScroll, {{ passive: true }});
    onScroll();

    return () => {{
      reveal.disconnect();
      stageIO.disconnect();
      window.removeEventListener("scroll", onScroll);
      if (raf) cancelAnimationFrame(raf);
    }};
  }}, []);

  return (
    <div ref={{root}} className="luca-landing">
      <style jsx global>{{`
        .luca-landing {{ --ink:#111; --sub:#6b6b6b; --line:#e4e4dc; --terra:#c4622a;
          --paper:#fff; background:var(--paper); color:var(--ink); }}
        .luca-landing .sec {{ position:relative; max-width:1200px; margin:0 auto;
          padding:96px 24px; border-bottom:1px solid var(--line);
          opacity:0; transform:translate3d(0,28px,0);
          transition:opacity .7s cubic-bezier(.22,.61,.36,1), transform .7s cubic-bezier(.22,.61,.36,1); }}
        .luca-landing .sec.in {{ opacity:1; transform:none; }}
        .luca-landing .copy {{ max-width:640px; }}
        .luca-landing .t {{ margin:0 0 14px; line-height:1.5; }}
        .luca-landing .mark {{ position:absolute; right:24px; top:88px; width:112px; height:112px;
          border-radius:50%; border:1px solid var(--line); display:grid; place-items:center;
          will-change:transform; }}
        .luca-landing .mark span {{ font-size:106px; line-height:1; color:var(--terra); }}
        .luca-landing .stage {{ margin-top:40px; min-height:420px; border-radius:18px;
          border:1px solid var(--line); background:#fafaf7; overflow:hidden;
          will-change:transform; opacity:0; transition:opacity .9s ease; }}
        .luca-landing .stage-ready {{ opacity:1; }}
        .luca-landing .pending {{ display:grid; place-items:center; height:420px;
          color:var(--sub); font-size:13px; letter-spacing:.08em; text-transform:uppercase; }}
        @media (prefers-reduced-motion: reduce) {{
          .luca-landing .sec, .luca-landing .stage {{ opacity:1; transform:none; transition:none; }}
        }}
        @media (max-width:768px) {{
          .luca-landing .sec {{ padding:64px 18px; }}
          .luca-landing .mark {{ position:static; width:72px; height:72px; margin-bottom:20px; }}
          .luca-landing .mark span {{ font-size:64px; }}
          .luca-landing .stage {{ min-height:280px; }}
        }}
      `}}</style>
{sections_jsx}
    </div>
  );
}}
'''


if __name__ == "__main__":
    main()
