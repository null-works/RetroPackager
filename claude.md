# RetroPackager - Project Knowledge

## Overview

RetroPackager is a GTK3 Python application for downloading, packaging, and installing PS1/GBA/N64 retro games on Steam Deck and handheld PCs (ROG Ally, etc.). It integrates with Archive.org for game downloads, SteamGridDB for artwork, and Steam for library management.

**Primary Use Case**: Personal retro gaming setup on Steam Deck/ROG Ally in Gaming Mode.

**Scope**: This is a private application built for personal use by 3 people. It is not intended for public distribution. As such, the hardcoded SteamGridDB API key and other security considerations (auto pip install, no checksum verification, etc.) are acceptable trade-offs for convenience in this trusted environment.

## CRITICAL Design Principle: Portable Emulators

**ALL emulators MUST be portable and self-contained per game installation.**

This means:
1. **Each game gets its own copy of the emulator** - No shared emulator installations
2. **Pre-configured controller mappings** - Must work out-of-the-box with Xbox-style controllers (Steam Deck, ROG Ally)
3. **Settings bundled with game** - Each game directory contains all config needed to run
4. **No external dependencies** - Game should launch without requiring system-wide emulator setup

### Why Portable Mode?
- **Consistency**: Games always launch with known-working settings
- **Controller support**: Pre-configured mappings for handheld devices
- **Isolation**: One game's config can't break another
- **Portability**: Copy game folder to another device and it works
- **No system pollution**: Uninstalling removes everything cleanly

### Implementing Portable Mode for New Emulators

When adding a new system/emulator:

1. **Set `emulator_portable: True`** in SYSTEMS config
2. **Set `needs_settings: True`** to generate config files
3. **Create settings template function** (e.g., `get_n64_settings_template()`)
4. **Include controller mappings** for Xbox-style controllers:
   - Left Stick, Right Stick, D-Pad
   - A/B/X/Y buttons
   - LB/RB shoulder buttons
   - LT/RT triggers
   - Start/Back buttons
5. **Use AppImage portable config** if supported (e.g., `AppName.AppImage.config/` directory)

### Example: N64 Portable Structure
```
~/Games/N64/GameName/
├── RMG.AppImage                    # Emulator copy
├── RMG.AppImage.config/            # Portable config (AppImage convention)
│   └── RMG/
│       └── mupen64plus.cfg         # Controller + emulator settings
├── rom/
│   └── game.z64
└── launch.sh
```

## Architecture

### Single-File Monolith
- **Main file**: `retro-packager.py` (~4,500 lines)
- No external modules or package structure
- All classes and functions in one file

### Key Classes

| Class | Lines | Purpose |
|-------|-------|---------|
| `DebugLog` | 61-95 | Singleton logger with UI callback support |
| `SteamShortcuts` | 1036-1485 | Steam shortcuts.vdf binary parser/writer + removal |
| `SteamGridDB` | 1486-1745 | SteamGridDB API client for artwork |
| `RetroPackagerApp` | 1748-4600+ | Main GTK3 application window |

### Important Functions

| Function | Location | Purpose |
|----------|----------|---------|
| `find_steam_root()` | 109-114 | Locates Steam installation |
| `get_game_genre()` | 540-565 | Fuzzy genre lookup from game databases |
| `get_settings_template()` | 567-654 | Generates DuckStation settings.ini |
| `debug_log()` | 93-95 | Convenience wrapper for DebugLog |

## Configuration

### Paths
```python
SCRIPT_DIR = Path(__file__).parent.resolve()
OUTPUT_DIR_PS1 = Path.home() / "Games" / "PS1"
OUTPUT_DIR_GBA = Path.home() / "Games" / "GBA"
OUTPUT_DIR_N64 = Path.home() / "Games" / "N64"
DOWNLOAD_DIR = Path.home() / "Games" / "downloads"
EMULATOR_DIR = Path.home() / "Games" / "emulators"  # Cache for downloaded emulators
DEBUG_LOG = SCRIPT_DIR / "retro-packager-debug.log"
CONFIG_FILE = SCRIPT_DIR / "ps1-packager.conf"
```

### Steam Paths Checked
```python
~/.steam/steam
~/.local/share/Steam
~/.var/app/com.valvesoftware.Steam/.steam/steam
~/.var/app/com.valvesoftware.Steam/.local/share/Steam
```

## Key Data Structures

### SYSTEMS Dict (lines 33-102)
Contains config for each supported system:
- `ps1`: DuckStation, requires BIOS, CHD/CUE/ISO formats, portable per-game
- `gba`: mGBA, no BIOS needed, GBA/GBC/GB formats, portable per-game
- `n64`: RMG (Rosalie's Mupen GUI), no BIOS needed, Z64/N64/V64 formats, portable per-game

### Genre Databases
- `PS1_GENRES`: 350+ games mapped to genres
- `GBA_GENRES`: 50+ games mapped to genres
- `N64_GENRES`: 70+ games mapped to genres
- `TOP_PICKS` / `GBA_TOP_PICKS` / `N64_TOP_PICKS`: Curated lists per genre

## Steam Integration

### shortcuts.vdf Format
- Binary VDF format with type markers:
  - `\x00` = nested object start
  - `\x01` = string value
  - `\x02` = int32 value
  - `\x08` = object end

### App ID Generation
```python
# For artwork filenames (unsigned):
crc = binascii.crc32(f'"{exe_path}"' + name) | 0x80000000

# For shortcuts.vdf (signed 32-bit):
if shortcut_id >= 0x80000000:
    shortcut_id = shortcut_id - 0x100000000
```

### Artwork Files
Steam grid folder: `~/.steam/steam/userdata/{user_id}/config/grid/`
- `{app_id}p.png` - Portrait cover (600x900)
- `{app_id}.png` - Horizontal grid (920x430)
- `{app_id}_hero.png` - Hero banner (1920x620)
- `{app_id}_logo.png` - Logo

### Shortcut Management Methods (SteamShortcuts class)
```python
# Add a shortcut
SteamShortcuts.add_shortcut(name, exe_path, start_dir, icon_path="", tags=[])

# Remove a shortcut by name or exe path
SteamShortcuts.remove_shortcut(name=None, exe_path=None)

# Remove all shortcuts matching specific tags
SteamShortcuts.remove_shortcuts_by_tags(['PS1', 'GBA', ...])

# Get all shortcuts
SteamShortcuts.get_all_shortcuts()  # Returns list of dicts

# Remove artwork for an app
SteamShortcuts.remove_artwork(app_id)
```

### Game Tags Used
- PS1 games: `['PS1', 'PlayStation', 'DuckStation']`
- GBA games: `['GBA', 'Game Boy Advance', 'mGBA']`
- N64 games: `['N64', 'Nintendo 64', 'RMG']`

## Threading Pattern

All network/file operations use daemon threads with GLib.idle_add for UI updates:
```python
def async_operation():
    def work():
        # ... do work ...
        GLib.idle_add(update_ui)
    threading.Thread(target=work, daemon=True).start()
```

## Installation Flow

1. Download ROM from Archive.org
2. Download/cache emulator AppImage
3. Extract and copy ROM files to game directory
4. Copy BIOS (PS1 only)
5. **Copy emulator to game directory** (portable mode)
6. **Generate emulator config with controller mappings**
7. Create launch.sh script
8. Add to Steam shortcuts.vdf
9. Download artwork from SteamGridDB

### Game Directory Structures

**PS1 (DuckStation)**:
```
~/Games/PS1/{GameName}/
├── rom/                    # ROM files (.chd, .cue, .bin, etc.)
├── bios/                   # PS1 BIOS files
├── DuckStation.AppImage    # Emulator copy
├── settings.ini            # DuckStation config + controller mappings
├── launch.sh               # Launch script
└── portable.txt            # Portable mode flag
```

**GBA (mGBA)**:
```
~/Games/GBA/{GameName}/
├── rom/                    # ROM files (.gba, .gbc, .gb)
├── mGBA.AppImage           # Emulator copy
├── launch.sh               # Launch script
└── portable.txt            # Portable mode flag
```

**N64 (RMG)**:
```
~/Games/N64/{GameName}/
├── rom/                    # ROM files (.z64, .n64, .v64)
├── RMG.AppImage            # Emulator copy
├── RMG.AppImage.config/    # Portable config directory
│   └── RMG/
│       └── mupen64plus.cfg # Controller + emulator settings
├── launch.sh               # Launch script
└── portable.txt            # Portable mode flag
```

## Known Technical Debt

### Code Quality
- Monolithic single file - should split into modules
- No `requirements.txt` (needs: PyGObject, requests, Pillow)
- Duplicate code between PS1/GBA installation methods
- Inconsistent import placement (some inside functions)
- Config file named `ps1-packager.conf` despite app rename

### Security Considerations
- Hardcoded SteamGridDB API key (line 1389) - intentionally kept for personal use
- Launch scripts now use `shlex.quote()` for path safety

## Recent Changes (2026-01-09)

### Security & Error Handling Fixes
1. **Shell injection prevention**: Added `shlex.quote()` to all launch scripts
2. **VDF backup**: `shortcuts.vdf.backup` created before modification
3. **Exception handling**: Replaced 8 bare `except:` with specific types
4. **Dead code**: Removed duplicate `return None` in `SteamGridDB.search_game()`

### Steam Shortcut Management Features
1. **Uninstall now removes Steam shortcuts**: `_uninstall_game()` removes shortcut + artwork
2. **View Shortcuts dialog**: Settings → View Shortcuts shows all non-Steam games
3. **Mass uninstall**: Settings → Remove All Game Shortcuts removes all RetroPackager entries
4. **Individual removal**: Each shortcut in View dialog has 🗑️ button
5. **New SteamShortcuts methods**:
   - `remove_shortcut(name, exe_path)` - Remove single shortcut
   - `remove_shortcuts_by_tags(tags)` - Mass removal by tag
   - `get_all_shortcuts()` - List all shortcuts with metadata
   - `remove_artwork(app_id)` - Clean up Steam grid images

### UI Improvements
1. **Settings dialog fullscreen**: Better visibility on ROG Ally / Steam Deck
2. **EXIT button**: Added to home page header (top right)
3. **Frutiger Aero artwork fix**: Fixed ellipse coordinate bug in `draw_glossy_orb()`
4. **Bright Frutiger Aero theme**: Complete UI overhaul matching artwork generation:
   - Sky gradient background (white-cyan to vibrant blue)
   - Glossy glass bubble buttons with translucent gradients
   - Frosted glass cards and panels
   - Orb-like accent buttons with highlight effects
   - Aurora-inspired status bar
   - Red glossy exit button

## Testing Notes

- No automated tests exist
- Manual testing on Steam Deck recommended
- Check debug log at `retro-packager-debug.log`

## UI Styling

Uses authentic Frutiger Aero theme matching the artwork generation:
- **Background**: Bright sky gradient (white-cyan `#e6f5ff` to vibrant blue `#1e8cdc`)
- **Buttons**: Glossy glass bubble effect with white/translucent gradients
- **Cards**: Frosted glass panels with white borders and soft shadows
- **Accent**: Bright aqua `#00a8e8` with orb-like glossy gradients
- **Text**: Dark blue `#1a3a5a` for readability on bright backgrounds
- **Exit Button**: Glossy red orb-style button
- CSS embedded in `CSS` variable (lines 679-1115)

### Key Aero Effects
- Multi-stop gradients mimicking glossy orb highlights
- Inset shadows creating 3D bubble effect
- White border highlights for glass-like appearance
- Aurora-inspired status bar with green-cyan gradient

## Gamepad Navigation

Steam translates controller to keyboard:
- A = Enter/Return (activate)
- B = Escape (back)
- D-Pad = Arrow keys
- LB/RB = Page Up/Down
- F11 = Toggle fullscreen

## External Dependencies

### Runtime
- Python 3
- GTK 3 (PyGObject)
- requests
- Pillow (optional, for artwork generation)

### System Tools
- 7z (for .7z extraction)
- xdg-open (for opening folders)

### External Services
- Archive.org (game downloads)
- SteamGridDB API (artwork)
- GitHub releases (emulator downloads)
