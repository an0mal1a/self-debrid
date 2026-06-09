# 🎬 Self-Debrid

**A free, self-hosted alternative to premium debrid services like Real-Debrid, AllDebrid, and Premiumize.**

Stream torrents and direct downloads (1fichier, MediaFire, etc.) through Kodi without paying monthly fees. Everything runs locally using free services.

This project was thinked to use with **Palantir 3**, but its known that works with other similar plugins.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![GitHub stars](https://img.shields.io/github/stars/an0mal1a/self-debrid?style=social)](https://github.com/an0mal1a/self-debrid/stargazers)
[![GitHub issues](https://img.shields.io/github/issues/an0mal1a/self-debrid)](https://github.com/an0mal1a/self-debrid/issues)

---
## ❗Advertisement
I've noticed that this repository is getting a few stars; there are some known issues that I think I can fix easily. One of the most important ones is the need to restart the API to refresh the JDownloader session. If I have time, I'll fix a few bugs and improve the project!


## ✨ Features

- 🧲 **Torrent support** via qBittorrent - Download and stream torrents
- 📥 **Direct downloads** via JDownloader - Support for 1fichier, MediaFire, Uptobox, and 100+ hosters
- 🎬 **Live streaming** - Start watching while downloading (no waiting for completion)
- 💾 **Smart caching** - Reuses downloaded files automatically
- 🆔 **No duplicate downloads** - Intelligent file tracking by unique ID
- 🔒 **HTTPS API** - Drop-in replacement for AllDebrid/Real-Debrid addons in Kodi
- 💰 **100% Free** - Uses only free services (no premium accounts needed)

---

## 📋 Requirements

### Software
- **Python 3.10+** - [Download](https://www.python.org/downloads/)
- **qBittorrent** - [Download](https://www.qbittorrent.org/download.php) (for torrents)
- **JDownloader 2** - [Download](https://jdownloader.org/download/index) (for direct downloads - **highly recommended**)

### Why JDownloader?
This project is intended to use with Kodi and specific specific plugin that almost always uses 1fichier, this hoster in the free tier has a very slow download and high timeout, using JDownloader, the speed of the download is incredible (100MB/s) and the cooldown between downloads are shorts (5 to 10 minutes), this is **more than enough** to stream a movie or TV episode.

---

## 🚀 Quick Start

### 1. Install Dependencies

```bash
# Clone the repository
git clone https://github.com/an0mal1a/self-debrid.git
cd self-debrid

# Install Python packages
python3 -m .venv venv
source .venv/bin/activate  # LINUX
.venv/Scripts/Activate.ps1 # WINDOWS
pip install -r requirements.txt
```

### 2. Configure Services

#### a) Setup qBittorrent

1. Install and open qBittorrent
2. Go to **Tools → Options → User Interface**
3. Enable "Web Interface"
4. Set username: `admin`
5. Set password: `adminadmin` (or your choice)
6. Port: `8080` (default)

#### b) Setup JDownloader (Required for most content)

1. Create a free account at [my.jdownloader.org](https://my.jdownloader.org)
2. Install JDownloader 2
3. Open JDownloader → Settings → My.JDownloader
4. Log in with your account
5. Note your device name (e.g., "JDownloader@yourusername")

#### c) Generate SSL Certificates

Self-Debrid uses HTTPS to be compatible with Kodi streaming addon.

```bash
# Create cert directory
mkdir cert

# Generate self-signed certificate (valid for 1 year)
openssl req -x509 -newkey rsa:4096 -nodes -out cert/cert.pem -keyout cert/key.pem -days 365
```

When prompted, you can press Enter for all fields or fill them as you like.

### 3. Configure Environment

Copy the example configuration:

```bash
cp .env.example .env
```

Edit `.env` with your settings.

### 4. Run Self-Debrid

```bash
python main.py
```

You should see:

```
============================================================
🚀 SELF-DEBRID
============================================================

📡 API: https://0.0.0.0:443
🎬 Stream: http://0.0.0.0:8081
💾 Cache: J:/DebridCache
🔧 qBittorrent: ✅ Connected
📥 JDownloader: ✅ Connected
============================================================
```

---

## 📃 Hosts Setup

In order to allow the plugin Palantir on Kodi works with self-debrid we need to modify our hosts file

- Linux: `echo '127.0.0.1 api.alldebrid.com' | sudo tee -a /etc/hosts'`
- Windows
    1. Open this file as admin "C:\Windows\System32\drivers\etc\hosts"
    2. In a new line add the following text: 127.0.0.1 api.alldebrid.com

## 📺 Kodi Setup

### Configure Debrid Addon

Kodi > Palantir 3 supports alldebrid and real-debrid. Here's how to configure them for Self-Debrid:

1. Open your addon settings
2. Find **Accounts**
3. Select **AllDebrid** (Self-Debrid mimics AllDebrid API)
4. Set **API Key**: `anything` (not validated, use any text)
5. Test and authorize

---

## 🎯 How It Works

### For Direct Downloads (1fichier, MediaFire, etc.)

```
1. Kodi addon finds a 1fichier link
2. Sends to Self-Debrid API 
3. Self-Debrid passes link to JDownloader
4. JDownloader starts downloading
5. Self-Debrid begins streaming immediately (as soon as 5MB downloaded)
6. You start watching while download continues in background
```

**Free 1fichier & JDownloader Limitations:**
- 1 download every 5-10 minutes
- This is **perfect** for streaming - by the time you finish one episode, the cooldown is over
- No speed limits (downloads at full speed)

### For Torrents

```
1. Kodi addon finds a magnet/torrent
2. Sends to Self-Debrid API
3. Self-Debrid adds to qBittorrent
4. Downloads largest file (the video)
5. Streams to Kodi while downloading
```

### Smart Caching

Self-Debrid remembers downloaded files:
- Each file gets a unique ID (based on the 1fichier file ID or torrent hash)
- If you request the same file again, it streams from cache instantly
- Old files are automatically deleted when cache is full

---

## 🛠️ Advanced Configuration

### Change Ports (You CANNOT change the ports, Palantir 3 dont allow you to change or set a custom API)

If ports 443 or 8081 are in use (in case of using this with other plugin):

```env
API_PORT=8443      # Use 8443 instead of 443
STREAM_PORT=8082   # Use 8082 instead of 8081
```

Don't forget to update the API URL in Kodi!

### Increase Cache Size

```env
MAX_CACHE_SIZE_GB=100  # Store up to 100GB of files
```

### Multiple Devices

Self-Debrid can serve multiple Kodi devices on your network simultaneously.

---

## 🐛 Troubleshooting

### "Archivo no encontrado" (File not found)

**Problem:** JDownloader hasn't started the download yet.

**Solution:** 
- Wait 10-15 seconds and retry
- Check JDownloader is running and connected
- Verify MyJDownloader credentials in `.env`

### qBittorrent Not Connected (If you have dont use in the config this is not a problem)

**Problem:** Self-Debrid can't reach qBittorrent.

**Solution:**
- Verify qBittorrent Web UI is enabled
- Check username/password in `.env` match qBittorrent settings
- Ensure port 8080 is correct

### JDownloader Not Connected (MyJDownloader) (If you have dont use in the config this is not a problem)

**Problem:** Can't connect to remote JDownloader.

**Solution:**
- Verify email/password are correct
- Check device name matches exactly (case-sensitive)
- Make sure JDownloader is running and logged into MyJDownloader
- Try local mode: leave `JD_EMAIL` and `JD_PASSWORD` empty in `.env`

### Download Stuck in JDownloader

**Problem:** File in download queue but not starting.

**Solution:**
- 1fichier cooldown - wait 5-10 minutes
- Right-click download in JD → Force Download
- Some hosters may require premium (try another source)

---

## 📊 Supported Services

### Direct Download (via JDownloader)
- ✅ 1fichier (most common)
- ✅ MediaFire
- ✅ Uptobox
- ✅ Mega
- ✅ RapidGator (free limits)
- ✅ 100+ other hosters

### Torrents (via qBittorrent)
- ✅ All magnet links
- ✅ .torrent files
- ✅ Any public/private tracker

---

🖥️ Platform-Specific Notes

**Windows**

- Run Command Prompt or PowerShell as Administrator when editing hosts file
- Windows Defender may flag the SSL certificate generation - this is safe to allow
- Firewall may prompt for network access - allow for both private and public networks

**macOS**

- Use sudo when editing hosts file or installing system-wide packages
- macOS may require allowing the app in Security & Privacy settings
- OpenSSL is pre-installed on most macOS versions

**Linux**

- Most distributions include Python 3 by default
- OpenSSL is typically pre-installed
- Use your package manager to install qBittorrent: sudo apt install qbittorrent (Debian/Ubuntu)

---

## 🤝 Contributing

Contributions are welcome! Feel free to:

- Report bugs
- Suggest features
- Submit pull requests
- Improve documentation

---

## 📜 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## ⚠️ Disclaimer

This tool is for **personal use only**. 

- Only download content you have the right to access
- Respect copyright laws in your country
- The developers are not responsible for how you use this software
- Use of this software is at your own risk

---

## 🙏 Acknowledgments

- Inspired by the need for a free alternative to premium debrid services
- Thanks to the developers of qBittorrent and JDownloader

---

## 💬 Support

- **Issues:** [GitHub Issues](https://github.com/an0mal1a/self-debrid/issues)
- **Discussions:** [GitHub Discussions](https://github.com/an0mal1a/self-debrid/discussions)

---

**Enjoy streaming without monthly fees! 🎉**
