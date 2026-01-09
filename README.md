# RetroPackager

A GTK3 application for downloading, packaging, and installing retro games directly to your Steam library on Steam Deck and SteamOS-based systems.

![Frutiger Aero UI](https://img.shields.io/badge/UI-Frutiger%20Aero-00a8e8)
![Python 3](https://img.shields.io/badge/Python-3.x-blue)
![GTK3](https://img.shields.io/badge/GTK-3.0-green)

## Features

- **Browse Archive.org** - Search and download PS1 and GBA games
- **One-Click Install** - Downloads game, emulator, configures everything, adds to Steam
- **Steam Integration** - Games appear in your Steam library with cover art
- **SteamGridDB Support** - High-quality artwork for your game tiles
- **Gamepad Navigation** - Full controller support for Gaming Mode
- **Portable Emulators** - Each game is self-contained with its own emulator instance

## Supported Systems

| System | Emulator | BIOS Required |
|--------|----------|---------------|
| PlayStation (PS1) | DuckStation | Yes (512KB .bin) |
| Game Boy Advance | mGBA | No (built-in) |

## Requirements

- **OS**: SteamOS, Bazzite, Nobara, or similar
- **Python 3** with GTK3 bindings
- **Steam** (native installation)

### Dependencies

```bash
# Fedora/Bazzite/Nobara
sudo dnf install python3-gobject gtk3 python3-requests

# Arch/SteamOS (Desktop Mode)
sudo pacman -S python-gobject gtk3 python-requests

# For 7z extraction support
sudo dnf install p7zip       # Fedora
sudo pacman -S p7zip         # Arch
```

## Installation

```bash
git clone https://github.com/null-works/RetroPackager.git
cd RetroPackager
chmod +x retro-packager.py
./retro-packager.py
```

## Setup

### PS1 BIOS
Place a valid PS1 BIOS file (512KB `.bin` file) in the same directory as `retro-packager.py`. The app will auto-detect it.

Common BIOS files: `scph1001.bin`, `scph5501.bin`, `scph7001.bin`

### SteamGridDB API Key (Optional)
For high-quality cover artwork:
1. Get a free API key at [steamgriddb.com/profile/preferences/api](https://www.steamgriddb.com/profile/preferences/api)
2. Enter it in Settings within the app

Without an API key, Archive.org thumbnails will be used as fallback.

## Usage

### From Desktop
```bash
./retro-packager.py
```

### From Gaming Mode
1. Open Settings in the app
2. Click "Add to Steam Library"
3. Restart Steam
4. Launch RetroPackager from your Steam library

### Controls (Gaming Mode)
- **A** - Select/Confirm
- **B** - Back
- **D-Pad** - Navigate
- **F11** - Toggle fullscreen

## File Structure

```
~/Games/
├── PS1/
│   └── Game_Name/
│       ├── DuckStation.AppImage
│       ├── launch.sh
│       ├── settings.ini
│       ├── portable.txt
│       ├── rom/
│       └── bios/
├── GBA/
│   └── Game_Name/
│       ├── launch.sh
│       └── rom/
├── downloads/
└── emulators/
```

## How It Works

1. **Search** - Query Archive.org for games or browse curated top picks
2. **Download** - Fetches the ROM and extracts if needed
3. **Package** - Creates game directory with emulator, ROM, BIOS, and config
4. **Steam** - Adds non-Steam game shortcut with artwork
5. **Play** - Launch from Steam like any other game

## Troubleshooting

### Game doesn't appear in Steam
Restart Steam after installation.

### No cover artwork
- Check your SteamGridDB API key in Settings
- Some obscure games may not have artwork on SteamGridDB

### PS1 game won't start
- Verify BIOS file is 512KB
- Check the debug log at `retro-packager-debug.log`

### Download fails
- Check your internet connection
- Some Archive.org items may be temporarily unavailable

## License

Personal use.

## Credits

- [DuckStation](https://github.com/stenzek/duckstation) - PS1 emulator
- [mGBA](https://mgba.io/) - GBA emulator
- [Archive.org](https://archive.org/) - Game archive
- [SteamGridDB](https://www.steamgriddb.com/) - Cover artwork
