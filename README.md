# ⚡ TeleRelay - Forward Restricted Telegram Media

A high-speed, zero-disk web application and PWA designed to stream media from protected / restricted Telegram channels and bots directly to your **Saved Messages** (or any destination chat).

![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)
![Telethon](https://img.shields.io/badge/Telethon-MTProto-green.svg)
![Flask](https://img.shields.io/badge/Flask-Web%20App-orange.svg)
![PWA](https://img.shields.io/badge/PWA-Installable-purple.svg)

---

## 🚀 Features

- 🔒 **Bypasses Client-Side Restrictions**: Access media from chats where forward & save are disabled.
- ⚡ **Zero-Disk Streaming**: Uses in-memory RAM buffers (`io.BytesIO`) — no media files ever touch your hard drive.
- 🚀 **Parallel DC Engine**: Multi-connection parallel worker transfer (`fast_telethon`) utilizing 512 KB chunks for maximum network speed.
- 📱 **Mobile & Desktop App (PWA)**:
  - Responsive, touch-friendly UI.
  - Installable as a standalone app on Android, iOS, Windows, and Mac.
  - Scan terminal QR code with your phone to open instantly over local Wi-Fi.
- 🔍 **Search & Infinite Pagination**:
  - Search episodes, movies, or filenames in real time.
  - "Load Next 50 Files" pagination to browse hundreds or thousands of files.
- 📦 **Batch Multi-Select**: Select multiple items with checkboxes or "Select All" to forward in bulk.

---

## 🛠️ Local Setup

### 1. Clone the repository
```bash
git clone https://github.com/Chandan-14r/telerelay.git
cd telerelay
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure credentials
Set your Telegram credentials as environment variables:
```bash
# Windows PowerShell
$env:TELEGRAM_API_ID = "YOUR_API_ID"
$env:TELEGRAM_API_HASH = "YOUR_API_HASH"
```
*(Get your `api_id` and `api_hash` from [my.telegram.org](https://my.telegram.org) -> API development tools)*

### 4. Run the Web App
```bash
python web_app.py
```
- **On Laptop**: Open `http://localhost:5000`
- **On Phone**: Scan the QR code in the terminal or visit `http://<YOUR_LAPTOP_IP>:5000`

---

## 📲 Install as an App on Mobile

1. Open the URL in Chrome (Android) or Safari (iOS).
2. Tap the banner **"📲 Install App"** (or Safari: **Share -> Add to Home Screen**).
3. TeleRelay now opens fullscreen as an independent native app on your phone.

---

## ☁️ Deploy as a Dedicated 24/7 Website

You can deploy this repository to **Render**, **Railway**, **Fly.io**, or any VPS using Docker:

```bash
docker build -t telerelay .
docker run -p 5000:5000 \
  -e TELEGRAM_API_ID=your_id \
  -e TELEGRAM_API_HASH=your_hash \
  telerelay
```

---

## 🛡️ Privacy & Security

- TeleRelay connects directly to Telegram's official MTProto servers via your own user credentials.
- No third-party servers, databases, or intermediary tracking.
- Session tokens are stored strictly in your local `.session` file and are ignored by git.
