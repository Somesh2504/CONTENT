"""
Zero Human Intervention Daily Content Generator
================================================
Picks a topic from topics.json → Generates 5-slide carousel via Gemini AI →
Downloads images from Pollinations.ai → Overlays text with Pillow →
Sends carousel to Telegram as a media album.

Author  : Senior Python Automation Engineer
Requires: Python 3.10+
"""

import io
import json
import logging
import os
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

import google.generativeai as genai
import requests
from PIL import Image, ImageDraw, ImageFont

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
GEMINI_MODEL        = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "")

TOPICS_FILE  = Path("topics.json")
FONT_FILE    = Path("bold_font.ttf")
OUTPUT_DIR   = Path("output")
SLIDE_COUNT  = 5

# Pollinations
POLL_WIDTH   = 1080
POLL_HEIGHT  = 1350
POLL_BASE    = "https://image.pollinations.ai/prompt/{prompt}?width={w}&height={h}&nologo=true&seed={seed}"

# Overlay opacity  0–255  (102 ≈ 40 %)
OVERLAY_ALPHA = 102

# Text layout
CANVAS_W     = POLL_WIDTH
CANVAS_H     = POLL_HEIGHT
H_PADDING    = 90          # pixels from each side
MAX_FONT     = 90          # starting font size
MIN_FONT     = 32          # minimum acceptable font size
LINE_SPACING = 1.35        # multiplier of font size
TEXT_COLOR   = (255, 255, 255, 255)

# Telegram
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# Retry settings
MAX_RETRIES  = 3
RETRY_DELAY  = 5           # seconds

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

def pick_topic() -> str:
    """
    Load topics.json, pop the first topic, save the remainder,
    and return the popped topic string.
    """
    if not TOPICS_FILE.exists():
        log.error("topics.json not found. Please create it.")
        sys.exit(1)

    topics: list[str] = _load_json(TOPICS_FILE)

    if not topics:
        log.error("topics.json is empty – no topics left to process.")
        sys.exit(1)

    topic = topics.pop(0)
    _save_json(TOPICS_FILE, topics)
    log.info("Picked topic: '%s'  (%d topics remaining)", topic, len(topics))
    return topic


# ─────────────────────────────────────────────
# Step 2 – Gemini content generation
# ─────────────────────────────────────────────

GEMINI_SYSTEM = """\
You are a world-class social-media copywriter who creates viral Instagram carousel posts.
Return ONLY a valid JSON array – no markdown fences, no extra text.
"""

GEMINI_USER_TEMPLATE = """\
Create a 5-slide Instagram carousel about the topic: "{topic}"

Rules:
- Slide 1 (Hook): MUST NOT contain any technical jargon. Open with a relatable daily-life situation, a shocking statistic, or a curiosity-inducing question that hooks a non-technical person instantly.
- Slides 2–4: Explain the core concept in simple, engaging language. Use analogies, short sentences, and avoid walls of text.
- Slide 5: A warm Call-to-Action (CTA) – e.g., "Follow for more", "Save this post", "Share with a friend who needs this".
- bg_prompt: Each slide needs a unique, dark, cinematic background image description. The scene must be ENTIRELY TEXT-FREE. Describe lighting, mood, objects, and colour palette.

Return STRICTLY this JSON schema – nothing else:
[
  {{
    "slide_number": 1,
    "text": "...",
    "bg_prompt": "..."
  }},
  ...5 objects total...
]
"""


def generate_slides(topic: str) -> list[dict]:
    """Call Gemini and return the parsed 5-slide list."""
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name=GEMINI_MODEL,
        system_instruction=GEMINI_SYSTEM,
    )

    prompt = GEMINI_USER_TEMPLATE.format(topic=topic)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log.info("Calling Gemini API (attempt %d/%d)…", attempt, MAX_RETRIES)
            response = model.generate_content(prompt)
            raw_text = response.text.strip()

            # Strip accidental markdown fences
            if raw_text.startswith("```"):
                lines = raw_text.splitlines()
                raw_text = "\n".join(
                    ln for ln in lines
                    if not ln.strip().startswith("```")
                ).strip()

            slides: list[dict] = json.loads(raw_text)

            if not isinstance(slides, list) or len(slides) != SLIDE_COUNT:
                raise ValueError(
                    f"Expected {SLIDE_COUNT} slides, got {len(slides) if isinstance(slides, list) else type(slides)}"
                )

            # Validate required keys
            for s in slides:
                if "slide_number" not in s or "text" not in s or "bg_prompt" not in s:
                    raise ValueError(f"Slide missing required keys: {s}")

            log.info("Gemini returned %d valid slides.", len(slides))
            return slides

        except Exception as exc:
            log.warning("Gemini error (attempt %d): %s", attempt, exc)
            if attempt < MAX_RETRIES:
                # The Free Tier quota is strictly 15 Requests Per Minute.
                # A 65-second sleep guarantees the 1-minute window completely resets
                # and safely bypasses the 'retry_delay: 46s' quota errors.
                sleep_time = 65
                log.info("Quota limit reached. Sleeping for %d seconds to clear 1-minute window...", sleep_time)
                time.sleep(sleep_time)
            else:
                log.error("All Gemini attempts exhausted. Aborting.")
                raise


# ─────────────────────────────────────────────
# Step 3a – Image download from Pollinations
# ─────────────────────────────────────────────

def download_image(bg_prompt: str, slide_num: int) -> Image.Image:
    """
    Format the prompt, call Pollinations.ai, and return a PIL Image.
    Retries up to MAX_RETRIES on failure.
    """
    safe_prompt = urllib.parse.quote(bg_prompt, safe="")
    url = POLL_BASE.format(
        prompt=safe_prompt,
        w=POLL_WIDTH,
        h=POLL_HEIGHT,
        seed=slide_num * 42,   # deterministic seed per slide
    )

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log.info(
                "Downloading image for slide %d (attempt %d/%d)…",
                slide_num, attempt, MAX_RETRIES,
            )
            resp = requests.get(url, timeout=120)
            resp.raise_for_status()

            img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
            log.info("Slide %d image downloaded: %dx%d px", slide_num, *img.size)
            return img

        except Exception as exc:
            log.warning("Image download error (attempt %d): %s", attempt, exc)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
            else:
                log.error("Failed to download image for slide %d after %d attempts.", slide_num, MAX_RETRIES)
                raise


# ─────────────────────────────────────────────
# Step 3b – Pillow text overlay
# ─────────────────────────────────────────────

def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load TTF font at given size, fall back to default if not found."""
    if FONT_FILE.exists():
        return ImageFont.truetype(str(FONT_FILE), size)
    log.warning("'%s' not found – using Pillow default font.", FONT_FILE)
    return ImageFont.load_default()


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    """
    Word-wrap `text` so each line fits within `max_width` pixels.
    Returns list of wrapped lines.
    """
    words = text.split()
    lines: list[str] = []
    current = ""

    for word in words:
        candidate = (current + " " + word).strip()
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines


def _fit_font_and_wrap(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    max_height: int,
) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """
    Binary-search for the largest font size where the wrapped text
    still fits inside (max_width × max_height).
    """
    lo, hi = MIN_FONT, MAX_FONT

    best_font  = _load_font(lo)
    best_lines = _wrap_text(draw, text, best_font, max_width)

    for size in range(hi, lo - 1, -2):          # step down by 2 for speed
        font  = _load_font(size)
        lines = _wrap_text(draw, text, font, max_width)
        line_h = size * LINE_SPACING
        total_h = len(lines) * line_h

        if total_h <= max_height:
            best_font  = font
            best_lines = lines
            break

    return best_font, best_lines


def _add_dark_overlay(img: Image.Image) -> Image.Image:
    """Blend a semi-transparent black rectangle over the full image."""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, OVERLAY_ALPHA))
    return Image.alpha_composite(img, overlay)


def _draw_slide_number(
    draw: ImageDraw.ImageDraw,
    num: int,
    total: int,
    canvas_w: int,
    canvas_h: int,
) -> None:
    """Small pill indicator at the bottom, e.g.  • • ● • •"""
    indicator_font = _load_font(28)
    dots = "  ".join("●" if i + 1 == num else "○" for i in range(total))
    bbox = draw.textbbox((0, 0), dots, font=indicator_font)
    x = (canvas_w - (bbox[2] - bbox[0])) // 2
    y = canvas_h - 80
    draw.text((x, y), dots, font=indicator_font, fill=(255, 255, 255, 180))


def create_slide_image(
    base_img: Image.Image,
    text: str,
    slide_num: int,
    total_slides: int,
) -> Image.Image:
    """
    Composite the full slide:
      1. Resize/crop source image to CANVAS_W × CANVAS_H
      2. Apply dark overlay
      3. Render word-wrapped, vertically centred text
      4. Add slide-number dots
    Returns the finished RGBA image.
    """
    # ── Resize keeping aspect ratio, then centre-crop ──────────────────────
    src = base_img.copy()
    src_ratio = src.width / src.height
    tgt_ratio = CANVAS_W / CANVAS_H

    if src_ratio > tgt_ratio:
        new_h = CANVAS_H
        new_w = int(new_h * src_ratio)
    else:
        new_w = CANVAS_W
        new_h = int(new_w / src_ratio)

    src = src.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - CANVAS_W) // 2
    top  = (new_h - CANVAS_H) // 2
    src  = src.crop((left, top, left + CANVAS_W, top + CANVAS_H))

    # ── Dark overlay ────────────────────────────────────────────────────────
    canvas = _add_dark_overlay(src)

    # ── Text rendering ──────────────────────────────────────────────────────
    draw       = ImageDraw.Draw(canvas)
    usable_w   = CANVAS_W - 2 * H_PADDING
    usable_h   = CANVAS_H - 200          # reserve space for dots + margins

    font, lines = _fit_font_and_wrap(draw, text, usable_w, usable_h)
    line_h      = int(font.size * LINE_SPACING)
    block_h     = len(lines) * line_h

    # Vertically centre the text block
    y_start = (CANVAS_H - block_h) // 2

    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        line_w = bbox[2] - bbox[0]
        x = (CANVAS_W - line_w) // 2          # horizontally centred

        # Subtle drop shadow for legibility
        draw.text((x + 3, y_start + i * line_h + 3), line,
                  font=font, fill=(0, 0, 0, 160))
        draw.text((x, y_start + i * line_h), line,
                  font=font, fill=TEXT_COLOR)

    # ── Slide-number dots ───────────────────────────────────────────────────
    _draw_slide_number(draw, slide_num, total_slides, CANVAS_W, CANVAS_H)

    return canvas


# ─────────────────────────────────────────────
# Step 3c – Save slides locally
# ─────────────────────────────────────────────

def save_slide(img: Image.Image, slide_num: int) -> Path:
    """Convert RGBA → RGB and save as JPEG. Returns the file path."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"slide_{slide_num}.jpg"
    img.convert("RGB").save(str(path), "JPEG", quality=95)
    log.info("Saved: %s", path)
    return path


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
            files[f"slide_{i + 1}"] = (path.name, fh, "image/jpeg")

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
    log.info("  Zero-Human-Intervention Content Generator – START")
    log.info("=" * 60)

    # ── Environment check ───────────────────────────────────────────
    _require_env("GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")

    # ── Step 1: Pick topic ──────────────────────────────────────────
    topic = pick_topic()

    # ── Step 2: Generate slides via Gemini ─────────────────────────
    slides = generate_slides(topic)

    # ── Step 3: Download → overlay → save each slide ───────────────
    saved_paths: list[Path] = []

    for slide in slides:
        num       = slide["slide_number"]
        text      = slide["text"]
        bg_prompt = slide["bg_prompt"]

        log.info("─── Processing slide %d/%d ───", num, SLIDE_COUNT)

        try:
            raw_img    = download_image(bg_prompt, num)
            final_img  = create_slide_image(raw_img, text, num, SLIDE_COUNT)
            path       = save_slide(final_img, num)
            saved_paths.append(path)
        except Exception as exc:
            log.error("Fatal error on slide %d: %s", num, exc)
            # Clean up any already-saved outputs so next run is clean
            for p in saved_paths:
                p.unlink(missing_ok=True)
            sys.exit(1)

    # ── Step 4: Send to Telegram ────────────────────────────────────
    try:
        send_telegram_album(saved_paths, topic)
    except Exception as exc:
        log.error("Telegram delivery failed: %s", exc)
        sys.exit(1)

    log.info("=" * 60)
    log.info("  Pipeline complete for topic: '%s'", topic)
    log.info("=" * 60)


if __name__ == "__main__":
    main()
