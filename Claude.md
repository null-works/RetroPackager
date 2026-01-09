# Claude.md - Cross-Prompt Memory

> This file serves as persistent memory for Claude across sessions. Update this file when making significant changes or discoveries.

## Project Overview

**RetroPackager** is a GTK3 Python application for downloading and packaging retro games (PS1, GBA) for Steam Deck and SteamOS-based systems (SteamOS, Nobara, Bazzite).

- **Author's Use Case**: Personal tool, private repository
- **Target Environment**: SteamOS-based systems with native Steam installation (`~/.steam/steam`)
- **UI Theme**: Frutiger Aero aesthetic (glossy, sky gradients, bubbles)

## Architecture

```
retro-packager.py (~4,300 lines)
├── Configuration & Constants (lines 1-650)
│   ├── SYSTEMS dict (PS1, GBA configs)
│   ├── Genre databases (PS1_GENRES, GBA_GENRES)
│   ├── Top picks per genre (TOP_PICKS, GBA_TOP_PICKS)
│   └── CSS theme (Frutiger Aero)
│
├── Utility Classes
│   ├── DebugLog - Singleton logger with UI callback
│   ├── SteamShortcuts - VDF read/write, shortcut management
│   └── SteamGridDB - Artwork API integration
│
├── RetroPackagerApp (Gtk.Window)
│   ├── Views: main menu, archive browser, packaging progress
│   ├── Installation Helpers (refactored):
│   │   ├── _download_from_archive()
│   │   ├── _extract_rom()
│   │   ├── _setup_emulator()
│   │   ├── _copy_cue_with_bins()
│   │   ├── _add_to_steam_with_artwork()
│   │   └── _finish_installation()
│   └── Installation Entry Points:
│       ├── _run_installation() - PS1 from Archive.org
│       ├── _run_gba_installation() - GBA from Archive.org
│       └── _start_local_packaging() - Local PS1 ROM
```

## Key Components

### Steam Integration
- **VDF Parsing**: Binary format for `shortcuts.vdf`
- **App ID Generation**: CRC32-based, handles signed/unsigned conversion
- **Artwork Paths**: `~/.steam/steam/userdata/{user_id}/config/grid/`
- **Artwork Types**: `{id}p.png` (portrait), `{id}.png` (wide), `{id}_hero.png`, `{id}_logo.png`

### Emulators
- **PS1**: DuckStation AppImage (portable mode with `portable.txt`)
- **GBA**: mGBA AppImage (built-in BIOS, no external BIOS needed)

### Archive.org Integration
- **PS1**: Collection search API (`psxgames` collection)
- **GBA**: File list browsing from `GameboyAdvanceRomCollectionByGhostware` item

## Session History

### 2026-01-09 - Initial Review & Refactoring

**Issues Fixed:**
1. Duplicate `return None` statement in `SteamGridDB.search_game()`
2. Removed then restored hardcoded SteamGridDB API key (private repo, user preference)
3. Removed unused `Gio` import
4. Added missing `sys` import (used by Pillow auto-install)
5. Fixed "My Games" dialog to show both PS1 and GBA games (was PS1 only)

**Major Refactoring:**
- Extracted ~400 lines of duplicated installation code into 6 reusable helper methods
- Reduced `_run_installation`, `_run_gba_installation`, `_start_local_packaging` from ~140 lines each to ~40-50 lines

**Not Issues (Clarified):**
- Hardcoded API key: Acceptable for private repo (like storing in config)
- Bare `except:` clauses: Acceptable for personal use
- Re-importing inside functions: Style preference, not a bug
- Shell injection in launch scripts: Personal use only, not a concern
- Hardcoded Steam path (`~/.steam/steam`): Correct for SteamOS environments

## Code Style Notes

- Uses threading for async operations with `GLib.idle_add()` for UI updates
- Gamepad navigation supported via Steam Input (translates to keyboard events)
- Error handling is intentionally permissive (bare excepts) for robustness

## File Locations

| Purpose | Path |
|---------|------|
| PS1 Games | `~/Games/PS1/{game_name}/` |
| GBA Games | `~/Games/GBA/{game_name}/` |
| Downloads | `~/Games/downloads/{item_id}/` |
| Emulators | `~/Games/emulators/` |
| Debug Log | `{script_dir}/retro-packager-debug.log` |
| Config | `{script_dir}/ps1-packager.conf` |

## Future Considerations

- Could add more systems (SNES, Genesis, N64) following existing pattern
- Download resume not implemented (large downloads restart on failure)
- No emulator update mechanism (downloaded once)
- Failed installations don't clean up partial directories

---
*Last updated: 2026-01-09*
