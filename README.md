# poster-wall

A Raspberry Pi kiosk for displaying Plex posters and media.

## Requirements

- Raspberry Pi 5 (4GB tested). Other models may work but are untested.
- Raspberry Pi OS Lite 64-bit (Bookworm recommended).
- SSH enabled and network access.

## Quickstart

1. Flash Raspberry Pi OS Lite (64-bit) to your SD card.
2. In "Advanced options" (on initial setup) configure:
   - Hostname
   - Username and password
   - Wireless LAN
   - Locale settings
   - Enable SSH
3. Boot the Pi and SSH in.

## Setup (on the Pi)

Update packages:

```bash
sudo apt-get update && \
  sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a \
  apt-get -y -o Dpkg::Options::="--force-confdef" \
  -o Dpkg::Options::="--force-confnew" full-upgrade
```

Install Git:

```bash
sudo apt install git -y
```

Clone the project:

```bash
git clone https://github.com/your-username/poster-wall.git
cd poster-wall
```

Run the setup script:

```bash
chmod +x setup.sh
./setup.sh --rotate 90
```

## Kiosk / Plex configuration

To configure Plex access for the kiosk, find your Plex token:

1. In Plex, open the item (movie or show) and click the "..." → "Get Info".
2. Click "View XML".
3. In the URL look for `X-Plex-Token=...` — the value after `=` is your token.

Use the Plex base URL like `http://<local-plex-ip>:32400` (adjust port if custom).

After setup, visit `http://your-pi-hostname.local:8088/settings.html` to configure your poster wall.

## Configuration Options

The poster wall includes extensive customization options through the web interface:

### Display & Typography
- **Library Selection**: Configure which Plex library sections to include (Movies, TV Shows, etc.)
- **Rotation Speed**: Set how long each poster is displayed (3-3600 seconds)
- **"Now Showing" Text**: Customize the text displayed during active playback
- **Google Fonts Integration**: Choose from 7 cinematic fonts including:
  - Oswald, Anton, Bebas Neue (condensed poster fonts)
  - Playfair Display, Cinzel (classical serif fonts)  
  - Raleway, Libre Baskerville (clean modern fonts)
- **Typography Controls**: Adjust font size, weight, letter spacing, and colors
- **Progress Bar Customization**: Configure padding, height, and colors for the "Now Playing" progress bar

### Poster Transitions
- **Enable Transitions**: Toggle advanced poster transitions on/off
- **Multiple Transition Types**: Select from GPU-optimized effects:
  - **Crossfade**: Smooth opacity transition (default)
  - **Slide Transitions**: Left, Right, Up, Down sliding effects
  - **3D Flip**: Card flip animation using CSS 3D transforms
  - **Scale & Fade**: Zoom in/out effects with opacity changes
- **Random Selection**: Choose multiple transition types for variety - the system randomly picks one for each poster change
- **Pi 5 Optimized**: All transitions use hardware-accelerated CSS transforms for smooth performance

### Now Playing Integration
- **Device Monitoring**: Configure specific Plex client devices to monitor for active playback
- **Live Progress**: Real-time progress bar and poster display during movie/TV playback
- **Automatic Switching**: Seamlessly switches between poster rotation and "Now Playing" mode
- **Content Filtering**: Only shows content from your configured library sections
- **Media Information**: Displays resolution, audio format, and content rating icons

### Visual Enhancements  
- **Auto-Dim Bright Posters**: Automatically reduces brightness of very bright movie posters
- **Full-Width Display**: Progress bars and titles use the complete viewport width
- **Responsive Design**: Optimized for various display orientations and resolutions
- **TV Show Support**: Displays TV series posters alongside movies in rotation

All settings are stored server-side and persist across browser sessions and system restarts.

## Development

Proxy (example local dev proxy):

```bash
cd proxy
python -m venv .venv
# On Windows PowerShell:
# .\.venv\Scripts\Activate.ps1
pip install flask requests
# Set environment variables and run:
export PLEX_URL="http://192.168.1.5:32400"
export PLEX_TOKEN="your-plex-token-here"
export SECTION_ID="1"
python app.py
```

Web app (static dev server):

```bash
cd web
python -m http.server 8088
```

## Notes

- The README documents tested steps on an RPi5 — adapt as needed for other hardware.
- If you experience display or rotation issues, try adjusting the `--rotate` option when running `setup.sh` (supports 0, 90, 180, or 270 degrees).

## License & Contributing

This project is licensed under the **Creative Commons Attribution-NonCommercial 4.0 International License (CC BY-NC 4.0)**.

### What this means:
- ✅ **Personal use**: Free to use, modify, and share for personal projects
- ✅ **Educational use**: Free for schools, universities, and educational purposes
- ✅ **Attribution required**: Please credit this project when sharing or using
- ❌ **Commercial use**: Requires explicit permission - please [contact me](mailto:dan@santee.ws) to discuss licensing

### Full License
You can view the complete license terms at: https://creativecommons.org/licenses/by-nc/4.0/

### Commercial Licensing
Interested in using this project commercially? I'm happy to discuss flexible licensing options. Please reach out to [me](mailto:dan@santee.ws) to discuss your specific use case.

---

**Contributing**: Pull requests and issues are welcome! By contributing, you agree that your contributions will be licensed under the same CC BY-NC 4.0 license.




