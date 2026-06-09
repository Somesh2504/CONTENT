# 🚀 Zero Human Intervention – Daily Content Generator

A fully automated Python pipeline that picks a topic, generates a 5-slide Instagram carousel using **Gemini AI** + **Pollinations.ai**, overlays text with **Pillow**, and delivers it to a **Telegram** chat — every single day, zero touch required.

---

## 📁 Project Structure

```
.
├── main.py               # Core automation script
├── requirements.txt      # Python dependencies
├── topics.json           # Queue of topics (auto-shrinks each run)
├── bold_font.ttf         # Your custom TTF font (add manually)
├── .env.example          # Environment variable template
├── .gitignore
└── .github/
    └── workflows/
        └── daily_post.yml   # GitHub Actions CI/CD
```

---

## ⚙️ How It Works

```
topics.json ──► Gemini (configurable model) ──► 5-slide JSON
                                           │
                              ┌────────────┼────────────┐
                         Slide 1      Slide 2–4      Slide 5
                              └────────────┼────────────┘
                                    Pollinations.ai
                                    (1080×1350 image)
                                           │
                                     Pillow overlay
                                  (dark layer + text)
                                           │
                                    output/slide_N.jpg
                                           │
                                  Telegram sendMediaGroup
                                    (album / carousel)
```

### Pipeline Steps
| Step | What happens |
|------|-------------|
| **1** | `pick_topic()` — pops the first item from `topics.json` and saves the remainder |
| **2** | `generate_slides()` — calls Gemini (default: `gemini-2.0-flash`), forces JSON output for 5 slides |
| **3** | `download_image()` — fetches a 1080×1350 cinematic image from Pollinations.ai |
| **4** | `create_slide_image()` — applies 40% dark overlay + auto-fitting word-wrapped text |
| **5** | `send_telegram_album()` — uploads all 5 images as a single Telegram media album |

---

## 🛠️ Local Setup

### 1. Clone & install
```bash
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
cd YOUR_REPO
pip install -r requirements.txt
```

### 2. Add your font
Download any bold `.ttf` font (e.g. [Montserrat Bold](https://fonts.google.com/specimen/Montserrat)) and place it in the project root as `bold_font.ttf`.

### 3. Set environment variables
```bash
# Windows PowerShell
$env:GEMINI_API_KEY     = "your_key_here"
$env:TELEGRAM_BOT_TOKEN = "your_token_here"
$env:TELEGRAM_CHAT_ID   = "your_chat_id_here"

# Linux / macOS / GitHub Actions
export GEMINI_API_KEY="your_key_here"
export GEMINI_MODEL="gemini-2.0-flash" # optional override
export TELEGRAM_BOT_TOKEN="your_token_here"
export TELEGRAM_CHAT_ID="your_chat_id_here"
```

### 4. Run
```bash
python main.py
```

Generated slides are saved to `output/slide_1.jpg` … `output/slide_5.jpg`.

---

## 🔐 GitHub Actions – Automated Daily Posting

### One-time setup

Go to your repo → **Settings → Secrets and variables → Actions** and add:

| Secret | Where to get it |
|--------|----------------|
| `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com) |
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) on Telegram |
| `TELEGRAM_CHAT_ID` | Send a message to your bot then check `getUpdates`, or use [@userinfobot](https://t.me/userinfobot) |

### Schedule
The workflow runs automatically at **03:30 UTC (09:00 AM IST)** every day.
You can also trigger it manually from the **Actions** tab → **Daily Content Generator** → **Run workflow**.

### How the commit-back works
After `main.py` runs, the workflow stages `topics.json` and pushes the updated file back to `main`. The commit message includes `[skip ci]` to prevent a recursive trigger.

---

## 📋 Adding More Topics

Edit `topics.json` and add strings to the array:
```json
[
  "Async Programming",
  "System Design",
  "Data Structures"
]
```
Commit and push — the next scheduled run will pick the first one.

---

## ⚠️ Important Notes

- **Never commit `.env`** — it is listed in `.gitignore`
- The `output/` folder is also gitignored (slides are uploaded to Telegram and saved as GitHub artifacts)
- If `topics.json` runs empty, the workflow fails gracefully with a clear error message
- All API calls have **3 retries** with a 5-second backoff

---

## 📦 Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `google-generativeai` | ≥ 0.7.2 | Gemini API client |
| `Pillow` | ≥ 10.3.0 | Image processing & text overlay |
| `requests` | ≥ 2.32.0 | HTTP calls (Pollinations + Telegram) |
