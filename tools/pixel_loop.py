"""
TriggerBOFF Pixel-Perfect UI Loop Tool.

Compares a live Vercel URL against a Figma frame by:
1. Exporting the Figma frame as PNG via Figma API
2. Screenshotting the live URL via Playwright
3. Compositing both at 50% opacity + generating colour channel diff
4. Returning a diff score and base64 composite image for vision analysis

Usage:
    python pixel_loop.py <figma_file_key> <node_id> <live_url> [viewport_width] [viewport_height]
"""
import base64
import json
import os
import sys
import tempfile
import urllib.request
from io import BytesIO

FIGMA_API_KEY = os.environ.get("FIGMA_API_KEY", "")
VIEWPORT_WIDTH = 1440
VIEWPORT_HEIGHT = 900


def export_figma_frame(file_key: str, node_id: str, scale: int = 2) -> bytes:
    """Export a Figma frame as PNG at 2x scale."""
    url = (
        f"https://api.figma.com/v1/images/{file_key}"
        f"?ids={node_id}&format=png&scale={scale}"
    )
    req = urllib.request.Request(url, headers={"X-Figma-Token": FIGMA_API_KEY})
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())

    image_url = data["images"][node_id.replace("-", ":")]
    req2 = urllib.request.Request(image_url)
    with urllib.request.urlopen(req2) as resp2:
        return resp2.read()


def screenshot_url(url: str, width: int, height: int) -> bytes:
    """Screenshot a URL via Playwright at exact viewport dimensions."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("playwright not installed — run: pip install playwright && playwright install chromium")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": width, "height": height},
            device_scale_factor=2
        )
        page.goto(url, wait_until="networkidle", timeout=30000)
        # Wait for fonts and animations to settle
        page.wait_for_timeout(2000)
        png_bytes = page.screenshot(full_page=False)
        browser.close()
        return png_bytes


def generate_diff(figma_bytes: bytes, live_bytes: bytes) -> dict:
    """
    Generate composite overlay and colour diff.
    Returns: diff_score (0-100, lower = more similar), composite_b64, diff_b64
    """
    try:
        from PIL import Image, ImageChops, ImageEnhance
        import numpy as np
    except ImportError:
        raise RuntimeError("Pillow and numpy required — run: pip install Pillow numpy")

    figma_img = Image.open(BytesIO(figma_bytes)).convert("RGBA")
    live_img = Image.open(BytesIO(live_bytes)).convert("RGBA")

    # Resize live to match figma dimensions
    if live_img.size != figma_img.size:
        live_img = live_img.resize(figma_img.size, Image.LANCZOS)

    # 50% opacity overlay (figma on top of live)
    composite = Image.blend(live_img.convert("RGB"), figma_img.convert("RGB"), alpha=0.5)

    # Colour channel diff
    diff = ImageChops.difference(figma_img.convert("RGB"), live_img.convert("RGB"))
    diff_enhanced = ImageEnhance.Contrast(diff).enhance(3.0)

    # Diff score: mean pixel difference as percentage of max (255)
    diff_array = np.array(diff).astype(float)
    diff_score = round((diff_array.mean() / 255.0) * 100, 2)

    # Encode to base64
    def img_to_b64(img):
        buf = BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()

    return {
        "diff_score": diff_score,
        "composite_b64": img_to_b64(composite),
        "diff_b64": img_to_b64(diff_enhanced),
        "figma_size": figma_img.size,
        "live_size": live_img.size,
        "pass": diff_score < 2.0,
        "verdict": "PASS — pixel-perfect" if diff_score < 2.0 else f"FAIL — {diff_score}% difference detected"
    }


def run_pixel_loop(
    figma_file_key: str,
    node_id: str,
    live_url: str,
    viewport_width: int = VIEWPORT_WIDTH,
    viewport_height: int = VIEWPORT_HEIGHT
) -> str:
    """
    Run one iteration of the pixel comparison loop.
    Returns JSON with diff score, pass/fail verdict, and base64 images.
    """
    if not FIGMA_API_KEY:
        return json.dumps({"error": "FIGMA_API_KEY not set"})

    try:
        print(f"Exporting Figma frame {node_id} from {figma_file_key}...")
        figma_bytes = export_figma_frame(figma_file_key, node_id)

        print(f"Screenshotting {live_url} at {viewport_width}x{viewport_height}...")
        live_bytes = screenshot_url(live_url, viewport_width, viewport_height)

        print("Generating diff...")
        result = generate_diff(figma_bytes, live_bytes)

        print(f"Result: {result['verdict']}")
        return json.dumps({k: v for k, v in result.items() if k not in ('composite_b64', 'diff_b64')}, indent=2)

    except Exception as e:
        return json.dumps({"error": str(e)})


# Hermes registry
try:
    from tools.registry import registry

    registry.register(
        name="run_pixel_loop",
        toolset="ui_quality",
        schema={
            "name": "run_pixel_loop",
            "description": (
                "Compare a live Vercel URL against a Figma frame. "
                "Returns a diff score (0-100, lower=better), pass/fail verdict, "
                "and composite overlay image. Run this loop until diff_score < 2.0."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "figma_file_key": {"type": "string", "description": "Figma file key (e.g. eXzt6bEokhCMPMdfFo2M5H)"},
                    "node_id": {"type": "string", "description": "Figma node ID (e.g. 16:1416)"},
                    "live_url": {"type": "string", "description": "Live URL to screenshot"},
                    "viewport_width": {"type": "integer", "description": "Viewport width in px (default 1440)"},
                    "viewport_height": {"type": "integer", "description": "Viewport height in px (default 900)"},
                },
                "required": ["figma_file_key", "node_id", "live_url"],
            },
        },
        handler=lambda args, **kw: run_pixel_loop(
            figma_file_key=args.get("figma_file_key", ""),
            node_id=args.get("node_id", ""),
            live_url=args.get("live_url", ""),
            viewport_width=args.get("viewport_width", VIEWPORT_WIDTH),
            viewport_height=args.get("viewport_height", VIEWPORT_HEIGHT),
        ),
        check_fn=lambda: bool(os.environ.get("FIGMA_API_KEY")),
        requires_env=["FIGMA_API_KEY"],
    )
except ImportError:
    pass


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: pixel_loop.py <figma_file_key> <node_id> <live_url> [width] [height]")
        sys.exit(1)
    w = int(sys.argv[4]) if len(sys.argv) > 4 else VIEWPORT_WIDTH
    h = int(sys.argv[5]) if len(sys.argv) > 5 else VIEWPORT_HEIGHT
    print(run_pixel_loop(sys.argv[1], sys.argv[2], sys.argv[3], w, h))
