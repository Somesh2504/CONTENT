"""
Zero Human Intervention Daily Content Generator – Premium Edition
================================================================
Picks a topic → Generates structured slide data via Gemini AI →
Renders premium HTML/CSS slides via Playwright → Sends to Telegram.

Author  : Senior Python Automation Engineer
Requires: Python 3.10+
"""

import io
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import google.generativeai as genai
import requests

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────
GEMINI_API_KEY      = os.getenv("GEMINI_API_KEY", "")
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "")
CREATOR_HANDLE      = os.getenv("CREATOR_HANDLE", "@codeinsights")

# Preferred Gemini models in priority order.
_PREFERRED_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-preview-05-20",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]
_env_model = os.getenv("GEMINI_MODEL", "")

TOPICS_FILE  = Path("topics.json")
OUTPUT_DIR   = Path("output")
SLIDE_COUNT  = 5
SLIDE_WIDTH  = 1080
SLIDE_HEIGHT = 1350

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
MAX_RETRIES  = 3
RETRY_DELAY  = 10


# ─────────────────────────────────────────────
# Helper utilities
# ─────────────────────────────────────────────

def _require_env(*names: str) -> None:
    """Abort early if critical environment variables are missing."""
    missing = [n for n in names if not os.getenv(n)]
    if missing:
        log.error("Missing required environment variables: %s", ", ".join(missing))
        sys.exit(1)


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _save_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


# ─────────────────────────────────────────────
# Step 1 – Topic management
# ─────────────────────────────────────────────

def peek_topic() -> str:
    """
    Return the first topic from topics.json WITHOUT removing it.
    The topic is only consumed (popped) after the full pipeline succeeds.
    """
    if not TOPICS_FILE.exists():
        log.error("topics.json not found. Please create it.")
        sys.exit(1)

    topics: list[str] = _load_json(TOPICS_FILE)

    if not topics:
        log.error("topics.json is empty – no topics left to process.")
        sys.exit(1)

    log.info("Next topic: '%s'  (%d topics total)", topics[0], len(topics))
    return topics[0]


def consume_topic() -> None:
    """
    Pop the first topic from topics.json and save.
    Call this ONLY after the full pipeline has succeeded.
    """
    topics: list[str] = _load_json(TOPICS_FILE)
    removed = topics.pop(0)
    _save_json(TOPICS_FILE, topics)
    log.info("Consumed topic: '%s'  (%d topics remaining)", removed, len(topics))


# ─────────────────────────────────────────────
# Step 2 – Gemini content generation
# ─────────────────────────────────────────────

GEMINI_SYSTEM = """\
You are an expert educational carousel designer for Instagram and LinkedIn.
You produce structured JSON for premium, visually stunning carousels.
You may include brief conversational text BEFORE the JSON output.
"""

GEMINI_USER_TEMPLATE = """\
Create a premium 5-slide educational carousel about: "{topic}"

DESIGN RULES:
- Pick the BEST color theme for this topic: "dark_tech", "clean_pro", or "bold_brand"
  • dark_tech  → Gold accent on black. Best for: coding, tech, AI, data.
  • clean_pro  → Indigo accent on white. Best for: business, productivity, design.
  • bold_brand → Red accent on navy. Best for: marketing, motivation, trends.
- Max 25 words per slide – people SKIM, not read
- One idea per slide – never cram two concepts
- Wrap 1-2 KEY words in each headline with **double asterisks** for accent color highlighting

SLIDE STRUCTURE:
Slide 1 (HOOK – stop the scroll):
  Big bold claim, shocking stat, or curiosity question. NO jargon. Add a thematic emoji icon.
Slide 2 (CONTEXT – why this matters):
  Step label + 3 short bullet points + a one-line practical tip.
Slide 3 (CONCEPT 1 – core idea):
  Step label + concept name + 1-2 sentence explanation using a simple analogy.
Slide 4 (CONCEPT 2 – deeper or example):
  Step label + second concept or real-world example + brief explanation.
Slide 5 (CTA – convert attention):
  Bold question or takeaway + exactly ONE clear action + motivational subtext.

Return this EXACT JSON schema (nothing else after it):
{{
  "color_theme": "dark_tech",
  "slides": [
    {{
      "slide_number": 1,
      "type": "hook",
      "icon": "🔌",
      "headline": "Bold headline with **accent** words",
      "subtext": "Teaser text. Swipe to learn →"
    }},
    {{
      "slide_number": 2,
      "type": "context",
      "step_label": "WHY THIS MATTERS",
      "headline": "Section **headline**",
      "bullets": ["Point one", "Point two", "Point three"],
      "tip": "One-line practical tip"
    }},
    {{
      "slide_number": 3,
      "type": "concept",
      "step_label": "CONCEPT 01",
      "icon": "📡",
      "headline": "Concept **name**",
      "body": "1-2 sentence explanation using a simple analogy"
    }},
    {{
      "slide_number": 4,
      "type": "concept",
      "step_label": "CONCEPT 02",
      "icon": "⚡",
      "headline": "Concept **name**",
      "body": "1-2 sentence explanation with a real-world example"
    }},
    {{
      "slide_number": 5,
      "type": "cta",
      "headline": "Bold **question** or takeaway?",
      "action": "Save this post",
      "subtext": "Follow for daily tech insights"
    }}
  ]
}}
"""


def _discover_models() -> list[str]:
    """
    Query the Gemini API for all models that support 'generateContent'.
    Returns model short names in preference order.
    """
    try:
        available: set[str] = set()
        for m in genai.list_models():
            if "generateContent" in (m.supported_generation_methods or []):
                short = m.name.replace("models/", "")
                available.add(short)

        log.info("API reports %d models supporting generateContent.", len(available))
        ordered = [m for m in _PREFERRED_MODELS if m in available]
        extras = sorted(m for m in available if m not in ordered and "flash" in m)
        result = ordered + extras

        if result:
            log.info("Models to try: %s", result[:5])
            return result
        return list(available)[:5]

    except Exception as exc:
        log.warning("Model discovery failed: %s. Using static list.", exc)
        return list(_PREFERRED_MODELS)


def _is_quota_error(exc: Exception) -> bool:
    """Return True if the exception is a quota/rate-limit error."""
    msg = str(exc).lower()
    return any(kw in msg for kw in ("429", "resourceexhausted", "quota", "rate"))


def _extract_json(raw_text: str) -> dict:
    """
    Extract JSON object or array from Gemini response text.
    Handles markdown fences and conversational text before/after JSON.
    """
    # Strip markdown fences
    cleaned = raw_text.strip()
    if "```" in cleaned:
        lines = cleaned.splitlines()
        cleaned = "\n".join(
            ln for ln in lines if not ln.strip().startswith("```")
        ).strip()

    # Use raw_decode to find JSON in the text
    decoder = json.JSONDecoder()
    for i, ch in enumerate(cleaned):
        if ch in ('{', '['):
            try:
                parsed, _ = decoder.raw_decode(cleaned, i)
                if isinstance(parsed, dict) and "slides" in parsed:
                    return parsed
                if isinstance(parsed, list) and len(parsed) > 0:
                    return {"color_theme": "dark_tech", "slides": parsed}
            except json.JSONDecodeError:
                continue

    raise ValueError("Could not find valid JSON in Gemini response")


def generate_slides(topic: str) -> dict:
    """
    Call Gemini and return the parsed slide data as a dict with
    'color_theme' and 'slides' keys.
    """
    genai.configure(api_key=GEMINI_API_KEY)
    prompt = GEMINI_USER_TEMPLATE.format(topic=topic)

    if _env_model:
        models_to_try = [_env_model]
        log.info("GEMINI_MODEL env override: using only '%s'", _env_model)
    else:
        models_to_try = _discover_models()

    if not models_to_try:
        raise RuntimeError("No Gemini models available. Check your API key.")

    last_error: Exception | None = None

    for model_name in models_to_try:
        log.info("──── Trying model: %s ────", model_name)

        try:
            model = genai.GenerativeModel(
                model_name=model_name,
                system_instruction=GEMINI_SYSTEM,
            )
        except Exception as exc:
            log.warning("Failed to initialise model '%s': %s", model_name, exc)
            last_error = exc
            continue

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                log.info("Calling Gemini [%s] (attempt %d/%d)…", model_name, attempt, MAX_RETRIES)
                response = model.generate_content(prompt)
                raw_text = response.text.strip()

                slides_data = _extract_json(raw_text)
                slides = slides_data.get("slides", [])

                if not isinstance(slides, list) or len(slides) != SLIDE_COUNT:
                    raise ValueError(
                        f"Expected {SLIDE_COUNT} slides, got "
                        f"{len(slides) if isinstance(slides, list) else type(slides)}"
                    )

                # Validate required keys
                for s in slides:
                    if "slide_number" not in s or "headline" not in s:
                        raise ValueError(f"Slide missing required keys: {s}")

                log.info("✅ Gemini [%s] returned %d valid slides.", model_name, len(slides))
                return slides_data

            except Exception as exc:
                last_error = exc
                log.warning("Gemini [%s] error (attempt %d): %s", model_name, attempt, exc)

                if _is_quota_error(exc):
                    log.warning("Quota exhausted on '%s' — skipping.", model_name)
                    break

                if attempt < MAX_RETRIES:
                    log.info("Sleeping %d seconds before retry…", RETRY_DELAY)
                    time.sleep(RETRY_DELAY)

    log.error("All models exhausted. Tried: %s", models_to_try)
    raise RuntimeError(f"Gemini generation failed. Last error: {last_error}")


# ─────────────────────────────────────────────
# Step 3 – Premium HTML/CSS slide rendering
# ─────────────────────────────────────────────

def _safe_text(text: str) -> str:
    """Escape HTML chars, then convert **word** to accent-highlighted spans."""
    safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    safe = re.sub(r'\*\*(.+?)\*\*', r'<span class="accent">\1</span>', safe)
    return safe


# ── The complete CSS design system ───────────────────────────────────────────
_SLIDE_CSS = """\
* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  margin: 0;
  padding: 0;
  width: 1080px;
  height: 1350px;
  overflow: hidden;
}

.slide {
  width: 1080px;
  height: 1350px;
  position: relative;
  overflow: hidden;
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  background: var(--bg);
  color: var(--text);
}

/* ── Themes ──────────────────────────────── */
.dark-tech {
  --bg: #0D0D0D;
  --bg-end: #141428;
  --accent: #FFD700;
  --accent-soft: rgba(255, 215, 0, 0.07);
  --accent-glow: rgba(255, 215, 0, 0.12);
  --text: #FFFFFF;
  --secondary: #B0B0B0;
  --card-bg: rgba(255,255,255,0.04);
  --card-border: rgba(255,255,255,0.08);
}

.clean-pro {
  --bg: #F7F8FA;
  --bg-end: #E4E8EF;
  --accent: #4F46E5;
  --accent-soft: rgba(79, 70, 229, 0.06);
  --accent-glow: rgba(79, 70, 229, 0.10);
  --text: #111827;
  --secondary: #6B7280;
  --card-bg: rgba(0,0,0,0.03);
  --card-border: rgba(0,0,0,0.06);
}

.bold-brand {
  --bg: #1A1A2E;
  --bg-end: #16213E;
  --accent: #E94560;
  --accent-soft: rgba(233, 69, 96, 0.07);
  --accent-glow: rgba(233, 69, 96, 0.12);
  --text: #FFFFFF;
  --secondary: #B0B0B8;
  --card-bg: rgba(255,255,255,0.04);
  --card-border: rgba(255,255,255,0.08);
}

/* ── Background gradient ──────────────────── */
.slide::before {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(170deg, var(--bg) 0%, var(--bg-end) 100%);
  z-index: 0;
}

/* ── Subtle dot pattern ───────────────────── */
.bg-pattern {
  position: absolute;
  inset: 0;
  background-image: radial-gradient(circle, var(--accent) 0.5px, transparent 0.5px);
  background-size: 30px 30px;
  opacity: 0.03;
  z-index: 1;
}

/* ── Top accent bar ───────────────────────── */
.top-bar {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 4px;
  background: linear-gradient(90deg, transparent 10%, var(--accent) 50%, transparent 90%);
  z-index: 3;
}

/* ── Decorative corners ───────────────────── */
.decor-tl, .decor-br {
  position: absolute;
  width: 30px;
  height: 30px;
  z-index: 2;
  opacity: 0.4;
}
.decor-tl {
  top: 55px;
  left: 50px;
  border-top: 2.5px solid var(--accent);
  border-left: 2.5px solid var(--accent);
}
.decor-br {
  bottom: 85px;
  right: 50px;
  border-bottom: 2.5px solid var(--accent);
  border-right: 2.5px solid var(--accent);
}

/* ── Glow circles ─────────────────────────── */
.glow-tl {
  position: absolute;
  width: 350px;
  height: 350px;
  border-radius: 50%;
  background: var(--accent-glow);
  filter: blur(100px);
  top: -120px;
  left: -120px;
  z-index: 1;
}
.glow-br {
  position: absolute;
  width: 280px;
  height: 280px;
  border-radius: 50%;
  background: var(--accent-glow);
  filter: blur(90px);
  bottom: -80px;
  right: -80px;
  z-index: 1;
}

/* ── Content safe zone ────────────────────── */
.content {
  position: relative;
  z-index: 2;
  padding: 110px 65px 100px;
  height: 100%;
  display: flex;
  flex-direction: column;
}

/* ── Typography ───────────────────────────── */
.headline {
  font-family: 'Space Grotesk', 'Inter', sans-serif;
  font-weight: 700;
  line-height: 1.12;
  letter-spacing: -1.5px;
  margin-bottom: 20px;
}
.headline .accent { color: var(--accent); }

.headline-xl { font-size: 68px; }
.headline-lg { font-size: 54px; }

.subtext {
  font-size: 26px;
  line-height: 1.5;
  color: var(--secondary);
  font-weight: 400;
}

.body-text {
  font-size: 26px;
  line-height: 1.65;
  color: var(--secondary);
  font-weight: 400;
}

/* ── Step label ───────────────────────────── */
.step-label {
  display: inline-block;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 2.5px;
  text-transform: uppercase;
  color: var(--accent);
  border: 2px solid var(--accent);
  border-radius: 6px;
  padding: 8px 18px;
  margin-bottom: 30px;
  width: fit-content;
}

/* ── Icon ──────────────────────────────────── */
.icon-display {
  font-size: 64px;
  margin-bottom: 20px;
  line-height: 1;
}

/* ── Accent line divider ──────────────────── */
.accent-line {
  width: 55px;
  height: 3px;
  background: var(--accent);
  border-radius: 2px;
  margin-bottom: 24px;
}

/* ── Bullet list ──────────────────────────── */
.bullets {
  list-style: none;
  padding: 0;
  margin: 16px 0;
}
.bullets li {
  font-size: 27px;
  line-height: 1.5;
  padding: 14px 0 14px 38px;
  position: relative;
  color: var(--text);
  font-weight: 400;
}
.bullets li::before {
  content: '▸';
  position: absolute;
  left: 0;
  color: var(--accent);
  font-size: 22px;
  font-weight: 700;
}

/* ── Tip callout box ──────────────────────── */
.tip-box {
  background: var(--accent-soft);
  border-left: 4px solid var(--accent);
  border-radius: 0 12px 12px 0;
  padding: 20px 24px;
  margin-top: auto;
}
.tip-label {
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 2px;
  text-transform: uppercase;
  color: var(--accent);
  margin-bottom: 6px;
}
.tip-text {
  font-size: 22px;
  line-height: 1.5;
  color: var(--secondary);
}

/* ── CTA badge button ─────────────────────── */
.cta-badge {
  display: inline-block;
  background: var(--accent);
  color: var(--bg);
  font-weight: 700;
  font-size: 22px;
  padding: 18px 44px;
  border-radius: 50px;
  margin-top: 28px;
  letter-spacing: 0.5px;
  width: fit-content;
}

/* ── Footer ───────────────────────────────── */
.footer {
  position: absolute;
  bottom: 36px;
  left: 65px;
  right: 65px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 17px;
  color: var(--secondary);
  z-index: 2;
  font-weight: 500;
}
.footer .page-num {
  font-weight: 700;
  font-size: 18px;
  color: var(--accent);
}

/* ── Spacer ────────────────────────────────── */
.spacer { flex: 1; }

/* ── Swipe indicator ──────────────────────── */
.swipe-arrow {
  display: inline-block;
  font-size: 20px;
  color: var(--accent);
  font-weight: 600;
  margin-top: 16px;
  letter-spacing: 1px;
}
"""


def _build_hook_html(slide: dict) -> str:
    """Slide 1: Big bold hook with icon."""
    icon = slide.get("icon", "🚀")
    headline = _safe_text(slide.get("headline", ""))
    subtext = _safe_text(slide.get("subtext", ""))
    return f'''
    <div class="spacer"></div>
    <div class="icon-display">{icon}</div>
    <h1 class="headline headline-xl">{headline}</h1>
    <div class="accent-line"></div>
    <p class="subtext">{subtext}</p>
    <div class="spacer"></div>
    '''


def _build_context_html(slide: dict) -> str:
    """Slide 2: Context with bullets and tip."""
    label = _safe_text(slide.get("step_label", "WHY THIS MATTERS"))
    headline = _safe_text(slide.get("headline", ""))
    bullets = slide.get("bullets", [])
    tip = slide.get("tip", "")

    bullets_html = "".join(f"<li>{_safe_text(b)}</li>" for b in bullets)

    tip_html = ""
    if tip:
        tip_html = f'''
        <div class="tip-box">
          <div class="tip-label">💡 PRO TIP</div>
          <div class="tip-text">{_safe_text(tip)}</div>
        </div>'''

    return f'''
    <div class="step-label">{label}</div>
    <h1 class="headline headline-lg">{headline}</h1>
    <div class="accent-line"></div>
    <ul class="bullets">{bullets_html}</ul>
    {tip_html}
    '''


def _build_concept_html(slide: dict) -> str:
    """Slide 3 or 4: Core concept explanation."""
    label = _safe_text(slide.get("step_label", "CONCEPT"))
    icon = slide.get("icon", "💡")
    headline = _safe_text(slide.get("headline", ""))
    body = _safe_text(slide.get("body", ""))

    return f'''
    <div class="step-label">{label}</div>
    <div class="spacer"></div>
    <div class="icon-display">{icon}</div>
    <h1 class="headline headline-lg">{headline}</h1>
    <div class="accent-line"></div>
    <p class="body-text">{body}</p>
    <div class="spacer"></div>
    '''


def _build_cta_html(slide: dict) -> str:
    """Slide 5: Call to action."""
    headline = _safe_text(slide.get("headline", ""))
    action = _safe_text(slide.get("action", "Save this post"))
    subtext = _safe_text(slide.get("subtext", ""))

    return f'''
    <div class="spacer"></div>
    <h1 class="headline headline-xl">{headline}</h1>
    <div class="accent-line"></div>
    <p class="subtext">{subtext}</p>
    <div class="cta-badge">{action}</div>
    <div class="spacer"></div>
    '''


_CONTENT_BUILDERS = {
    "hook":    _build_hook_html,
    "context": _build_context_html,
    "concept": _build_concept_html,
    "cta":     _build_cta_html,
}


def _build_full_slide_html(
    slide: dict,
    color_theme: str,
    handle: str,
    total: int,
) -> str:
    """Build complete, self-contained HTML for a single slide."""
    theme_class = color_theme.replace("_", "-")
    slide_num = slide["slide_number"]
    slide_type = slide.get("type", "concept")

    builder = _CONTENT_BUILDERS.get(slide_type, _build_concept_html)
    content_html = builder(slide)

    return f'''<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&family=Space+Grotesk:wght@500;700&display=swap" rel="stylesheet">
<style>
{_SLIDE_CSS}
</style>
</head>
<body>
<div class="slide {theme_class}">
  <div class="bg-pattern"></div>
  <div class="top-bar"></div>
  <div class="decor-tl"></div>
  <div class="decor-br"></div>
  <div class="glow-tl"></div>
  <div class="glow-br"></div>
  <div class="content">
    {content_html}
  </div>
  <div class="footer">
    <span class="handle">{handle}</span>
    <span class="page-num">{slide_num}/{total}</span>
  </div>
</div>
</body>
</html>'''


def render_all_slides(slides_data: dict) -> list[Path]:
    """
    Render each slide as a high-quality PNG using Playwright.
    Opens one browser, renders all slides, then closes.
    """
    from playwright.sync_api import sync_playwright

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    color_theme = slides_data.get("color_theme", "dark_tech")
    slides = slides_data["slides"]
    total = len(slides)

    paths: list[Path] = []

    log.info("Launching Playwright (Chromium) for slide rendering…")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": SLIDE_WIDTH, "height": SLIDE_HEIGHT},
        )

        for slide in slides:
            num = slide["slide_number"]
            html = _build_full_slide_html(slide, color_theme, CREATOR_HANDLE, total)

            # Load HTML and wait for Google Fonts to finish loading
            page.set_content(html, wait_until="networkidle")
            page.wait_for_timeout(500)  # Extra buffer for font rendering

            path = OUTPUT_DIR / f"slide_{num}.png"
            page.screenshot(path=str(path))
            paths.append(path)
            log.info("✅ Rendered slide %d/%d → %s", num, total, path.name)

        browser.close()

    log.info("All %d slides rendered successfully.", len(paths))
    return paths


# ─────────────────────────────────────────────
# Step 4 – Telegram delivery
# ─────────────────────────────────────────────

def send_telegram_album(image_paths: list[Path], topic: str) -> None:
    """
    Send all images as a single Telegram media album (carousel).
    Uses multipart/form-data so images are uploaded directly.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram credentials not set – skipping delivery.")
        return

    url = f"{TELEGRAM_API}/sendMediaGroup"

    # Build the media JSON (caption only on first item)
    media_meta = []
    for i, _ in enumerate(image_paths):
        entry: dict[str, Any] = {
            "type": "photo",
            "media": f"attach://slide_{i + 1}",
        }
        if i == 0:
            entry["caption"] = f"🚀 Today's topic: *{topic}*"
            entry["parse_mode"] = "Markdown"
        media_meta.append(entry)

    # Build multipart files dict
    files: dict[str, Any] = {}
    opened: list[io.BufferedReader] = []

    try:
        for i, path in enumerate(image_paths):
            fh = open(path, "rb")   # noqa: WPS515
            opened.append(fh)
            mime = "image/png" if path.suffix == ".png" else "image/jpeg"
            files[f"slide_{i + 1}"] = (path.name, fh, mime)

        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "media":   json.dumps(media_meta),
        }

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                log.info("Sending Telegram album (attempt %d/%d)…", attempt, MAX_RETRIES)

                # Re-seek all file handles before each attempt
                for fh in opened:
                    fh.seek(0)

                resp = requests.post(url, data=data, files=files, timeout=120)
                resp.raise_for_status()
                result = resp.json()

                if result.get("ok"):
                    log.info("✅ Telegram album sent successfully!")
                    return
                else:
                    raise RuntimeError(f"Telegram API error: {result}")

            except Exception as exc:
                log.warning("Telegram send error (attempt %d): %s", attempt, exc)
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)
                else:
                    log.error("Failed to send Telegram album after %d attempts.", MAX_RETRIES)
                    raise

    finally:
        for fh in opened:
            fh.close()


# ─────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────

def main() -> None:
    log.info("=" * 60)
    log.info("  Premium Content Generator – START")
    log.info("=" * 60)

    # ── Environment check ───────────────────────────────────────────
    _require_env("GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")

    # ── Step 1: Peek at topic (don't consume yet) ──────────────────
    topic = peek_topic()

    # ── Step 2: Generate structured slide data via Gemini ──────────
    slides_data = generate_slides(topic)
    log.info("Theme chosen: %s", slides_data.get("color_theme", "unknown"))

    # ── Step 3: Render premium HTML/CSS slides via Playwright ──────
    try:
        saved_paths = render_all_slides(slides_data)
    except Exception as exc:
        log.error("Slide rendering failed: %s", exc)
        sys.exit(1)

    # ── Step 4: Send to Telegram ───────────────────────────────────
    try:
        send_telegram_album(saved_paths, topic)
    except Exception as exc:
        log.error("Telegram delivery failed: %s", exc)
        sys.exit(1)

    # ── Step 5: NOW consume the topic (everything succeeded) ───────
    consume_topic()

    log.info("=" * 60)
    log.info("  Pipeline complete for topic: '%s'", topic)
    log.info("=" * 60)


if __name__ == "__main__":
    main()
