#!/usr/bin/env python3
"""
Retro Game Packager for Steam Deck
Downloads, packages, and installs PS1, GBA, and N64 games directly to Steam
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Gio, Pango, GdkPixbuf
import cairo
import subprocess
import threading
import shutil
import shlex
import os
import json
import struct
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
import requests

# === CONFIGURATION ===
SCRIPT_DIR = Path(__file__).parent.resolve()
OUTPUT_DIR_PS1 = Path.home() / "Games" / "PS1"
OUTPUT_DIR_GBA = Path.home() / "Games" / "GBA"
OUTPUT_DIR_N64 = Path.home() / "Games" / "N64"
OUTPUT_DIR = OUTPUT_DIR_PS1  # Default
DOWNLOAD_DIR = Path.home() / "Games" / "downloads"
EMULATOR_DIR = Path.home() / "Games" / "emulators"
DEBUG_LOG = SCRIPT_DIR / "retro-packager-debug.log"

# System configurations
SYSTEMS = {
    "ps1": {
        "name": "PlayStation",
        "short": "PS1",
        "output_dir": OUTPUT_DIR_PS1,
        "emulator_url": "https://github.com/stenzek/duckstation/releases/download/latest/DuckStation-x64.AppImage",
        "emulator_name": "DuckStation.AppImage",
        "needs_bios": True,
        "bios_files": ["scph1001.bin", "scph5501.bin", "scph7001.bin"],
        "extensions": [".bin", ".cue", ".chd", ".iso", ".img", ".pbp"],
        "icon": "🎮",
        "color": "#003791",
        # Installation config
        "rom_extensions": [".cue", ".chd", ".iso", ".bin", ".pbp"],  # Priority order for ROM detection
        "emulator_portable": True,      # Copy emulator to game dir (portable mode)
        "emulator_subdir": None,        # No shared dir, copied per-game
        "needs_settings": True,         # Generate settings.ini
        "launch_args": "-fullscreen --",  # Args before ROM path
        "launch_relative_rom": True,    # Use relative ./rom/ path in launch script
        "tags": ["PS1", "PlayStation", "DuckStation"],
        "parse_cue_files": True,        # Parse .cue to find associated .bin files
    },
    "gba": {
        "name": "Game Boy Advance",
        "short": "GBA",
        "output_dir": OUTPUT_DIR_GBA,
        "emulator_url": "https://github.com/mgba-emu/mgba/releases/download/0.10.5/mGBA-0.10.5-appimage-x64.appimage",
        "emulator_name": "mGBA.AppImage",
        "needs_bios": False,
        "bios_files": [],
        "extensions": [".gba", ".gbc", ".gb", ".zip", ".7z"],
        "icon": "🕹️",
        "color": "#4F2683",
        # Installation config
        "rom_extensions": [".gba", ".gbc", ".gb"],  # Priority order for ROM detection
        "emulator_portable": False,     # Use shared emulator
        "emulator_subdir": "mgba",      # Shared dir: ~/Games/emulators/mgba/
        "needs_settings": False,        # No settings.ini needed
        "launch_args": "-f",            # Fullscreen flag
        "launch_relative_rom": False,   # Use absolute ROM path in launch script
        "tags": ["GBA", "Game Boy Advance", "mGBA"],
        "parse_cue_files": False,       # Not applicable
    },
    "n64": {
        "name": "Nintendo 64",
        "short": "N64",
        "output_dir": OUTPUT_DIR_N64,
        "emulator_url": "https://github.com/Rosalie241/RMG/releases/download/v0.8.8/RMG-Portable-Linux64-v0.8.8.AppImage",
        "emulator_name": "RMG.AppImage",
        "needs_bios": False,
        "bios_files": [],
        "extensions": [".z64", ".n64", ".v64", ".zip", ".7z"],
        "icon": "🎲",
        "color": "#008000",
        # Installation config
        "rom_extensions": [".z64", ".n64", ".v64"],  # Priority order for ROM detection
        "emulator_portable": False,     # Use shared emulator
        "emulator_subdir": "rmg",       # Shared dir: ~/Games/emulators/rmg/
        "needs_settings": False,        # RMG handles its own config
        "launch_args": "--fullscreen",  # Fullscreen flag
        "launch_relative_rom": False,   # Use absolute ROM path in launch script
        "tags": ["N64", "Nintendo 64", "RMG"],
        "parse_cue_files": False,       # Not applicable
    }
}

# Debug logger - writes to file and can be displayed in UI
class DebugLog:
    _instance = None
    _ui_callback = None
    
    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def __init__(self):
        # Clear log on startup
        with open(DEBUG_LOG, 'w') as f:
            f.write(f"=== Retro Packager Debug Log - {datetime.now()} ===\n\n")
    
    def set_ui_callback(self, callback):
        """Set callback to also log to UI"""
        DebugLog._ui_callback = callback
    
    def log(self, message):
        """Write to debug log file"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        with open(DEBUG_LOG, 'a') as f:
            f.write(line + "\n")
        # Also call UI callback if set
        if DebugLog._ui_callback:
            try:
                DebugLog._ui_callback(f"  [DEBUG] {message}")
            except Exception:
                # UI callback may fail if widget is destroyed; ignore safely
                pass

def debug_log(message):
    """Convenience function for debug logging"""
    DebugLog.get().log(message)

# DuckStation AppImage
DUCKSTATION_URL = "https://github.com/stenzek/duckstation/releases/download/latest/DuckStation-x64.AppImage"
APPIMAGE_NAME = "DuckStation.AppImage"

# Steam paths (native and Flatpak)
STEAM_PATHS = [
    Path.home() / ".steam" / "steam",                          # Native Steam
    Path.home() / ".local" / "share" / "Steam",                # Alternative native
    Path.home() / ".var/app/com.valvesoftware.Steam/.steam/steam",  # Flatpak Steam
    Path.home() / ".var/app/com.valvesoftware.Steam/.local/share/Steam",  # Flatpak alt
]

def find_steam_root():
    """Find the Steam installation directory"""
    for path in STEAM_PATHS:
        if path.exists() and (path / "userdata").exists():
            return path
    return None

STEAM_ROOT = find_steam_root()
STEAM_USERDATA = STEAM_ROOT / "userdata" if STEAM_ROOT else None

# PS1 Archive.org collections (searchable)
PS1_ARCHIVE_COLLECTIONS = {
    "psxgames": "PSX Games Collection",
    "chd_psx": "PSX CHD (USA)",
    "Centuron-PSX": "Centuron PSX",
}

# GBA Archive.org item (file list browsing)
GBA_ARCHIVE_ITEM = "GameboyAdvanceRomCollectionByGhostware"

# N64 Archive.org item (file list browsing)
N64_ARCHIVE_ITEM = "N64TOSEC"

# Legacy compatibility
ARCHIVE_COLLECTIONS = PS1_ARCHIVE_COLLECTIONS

# PS1 Game Genre Database - maps game names to genres
PS1_GENRES = {
    "Final Fantasy VII": ["RPG"], "Final Fantasy VIII": ["RPG"], "Final Fantasy IX": ["RPG"],
    "Final Fantasy Tactics": ["RPG", "Strategy"], "Final Fantasy Anthology": ["RPG"],
    "Final Fantasy Chronicles": ["RPG"], "Final Fantasy Origins": ["RPG"],
    "Chrono Cross": ["RPG"], "Chrono Trigger": ["RPG"], "Xenogears": ["RPG"],
    "Vagrant Story": ["RPG", "Action"], "Legend of Dragoon": ["RPG"], "Legend of Legaia": ["RPG"],
    "Legend of Mana": ["RPG", "Action"], "Breath of Fire III": ["RPG"], "Breath of Fire IV": ["RPG"],
    "Suikoden": ["RPG"], "Suikoden II": ["RPG"], "Wild Arms": ["RPG"], "Wild Arms 2": ["RPG"],
    "Star Ocean": ["RPG", "Action"], "Star Ocean: The Second Story": ["RPG", "Action"],
    "Tales of Destiny": ["RPG", "Action"], "Tales of Destiny II": ["RPG", "Action"],
    "Grandia": ["RPG"], "Lunar: Silver Star Story Complete": ["RPG"],
    "Lunar 2: Eternal Blue Complete": ["RPG"], "Arc the Lad Collection": ["RPG", "Strategy"],
    "Valkyrie Profile": ["RPG", "Action"], "Persona": ["RPG"], "Persona 2: Eternal Punishment": ["RPG"],
    "Parasite Eve": ["RPG", "Horror"], "Parasite Eve II": ["RPG", "Horror", "Action"],
    "Front Mission 3": ["RPG", "Strategy"], "Tactics Ogre": ["RPG", "Strategy"],
    "Ogre Battle": ["RPG", "Strategy"], "Kartia": ["RPG", "Strategy"],
    "Vandal Hearts": ["RPG", "Strategy"], "Vandal Hearts II": ["RPG", "Strategy"],
    "Brigandine": ["RPG", "Strategy"], "Alundra": ["RPG", "Action", "Adventure"],
    "Alundra 2": ["Action", "Adventure"], "Azure Dreams": ["RPG"], "Threads of Fate": ["RPG", "Action"],
    "Brave Fencer Musashi": ["RPG", "Action"], "SaGa Frontier": ["RPG"], "SaGa Frontier 2": ["RPG"],
    "Rhapsody: A Musical Adventure": ["RPG"], "Thousand Arms": ["RPG"], "Jade Cocoon": ["RPG"],
    "Monster Rancher": ["RPG", "Simulation"], "Monster Rancher 2": ["RPG", "Simulation"],
    "Digimon World": ["RPG", "Simulation"], "Digimon World 2": ["RPG"], "Digimon World 3": ["RPG"],
    "Metal Gear Solid": ["Action", "Stealth"], "Metal Gear Solid: VR Missions": ["Action"],
    "Syphon Filter": ["Action", "Shooter"], "Syphon Filter 2": ["Action", "Shooter"],
    "Syphon Filter 3": ["Action", "Shooter"], "Tenchu: Stealth Assassins": ["Action", "Stealth"],
    "Tenchu 2": ["Action", "Stealth"], "Dino Crisis": ["Action", "Horror"],
    "Dino Crisis 2": ["Action", "Horror"], "Resident Evil": ["Horror", "Action"],
    "Resident Evil 2": ["Horror", "Action"], "Resident Evil 3": ["Horror", "Action"],
    "Resident Evil: Director's Cut": ["Horror", "Action"], "Resident Evil Survivor": ["Horror", "Shooter"],
    "Silent Hill": ["Horror", "Adventure"], "Clock Tower": ["Horror", "Adventure"],
    "Clock Tower II": ["Horror", "Adventure"], "Nightmare Creatures": ["Horror", "Action"],
    "Nightmare Creatures II": ["Horror", "Action"], "Alone in the Dark": ["Horror", "Adventure"],
    "Echo Night": ["Horror", "Adventure"], "Koudelka": ["Horror", "RPG"], "Galerians": ["Horror", "Action"],
    "Fear Effect": ["Action", "Adventure"], "Fear Effect 2": ["Action", "Adventure"],
    "Mega Man Legends": ["Action", "Adventure"], "Mega Man Legends 2": ["Action", "Adventure"],
    "Mega Man X4": ["Action", "Platformer"], "Mega Man X5": ["Action", "Platformer"],
    "Mega Man X6": ["Action", "Platformer"], "Mega Man 8": ["Action", "Platformer"],
    "Castlevania: Symphony of the Night": ["Action", "Adventure", "Platformer"],
    "Castlevania Chronicles": ["Action", "Platformer"],
    "Legacy of Kain: Soul Reaver": ["Action", "Adventure"],
    "Legacy of Kain: Blood Omen": ["Action", "Adventure", "RPG"],
    "MediEvil": ["Action", "Adventure"], "MediEvil II": ["Action", "Adventure"],
    "Tomba!": ["Action", "Platformer"], "Tomba! 2": ["Action", "Platformer"],
    "Klonoa": ["Platformer"], "Klonoa 2": ["Platformer"], "Rayman": ["Platformer"], "Rayman 2": ["Platformer"],
    "Crash Bandicoot": ["Platformer"], "Crash Bandicoot 2: Cortex Strikes Back": ["Platformer"],
    "Crash Bandicoot: Warped": ["Platformer"], "Crash Bandicoot 3": ["Platformer"],
    "Crash Bash": ["Party"], "Crash Team Racing": ["Racing"],
    "Spyro the Dragon": ["Platformer"], "Spyro 2: Ripto's Rage": ["Platformer"],
    "Spyro: Year of the Dragon": ["Platformer"], "Gex": ["Platformer"],
    "Gex: Enter the Gecko": ["Platformer"], "Gex 3: Deep Cover Gecko": ["Platformer"],
    "Croc: Legend of the Gobbos": ["Platformer"], "Croc 2": ["Platformer"],
    "Ape Escape": ["Platformer", "Action"], "Pandemonium!": ["Platformer"], "Pandemonium 2": ["Platformer"],
    "Frogger": ["Platformer", "Puzzle"], "Frogger 2": ["Platformer", "Puzzle"],
    "Pac-Man World": ["Platformer"], "Ms. Pac-Man Maze Madness": ["Puzzle"],
    "Gran Turismo": ["Racing", "Simulation"], "Gran Turismo 2": ["Racing", "Simulation"],
    "Ridge Racer": ["Racing"], "Ridge Racer Type 4": ["Racing"], "Ridge Racer Revolution": ["Racing"],
    "Need for Speed": ["Racing"], "Need for Speed II": ["Racing"],
    "Need for Speed III: Hot Pursuit": ["Racing"], "Need for Speed: High Stakes": ["Racing"],
    "Need for Speed: Porsche Unleashed": ["Racing"], "Driver": ["Racing", "Action"],
    "Driver 2": ["Racing", "Action"], "Colin McRae Rally": ["Racing"], "Colin McRae Rally 2.0": ["Racing"],
    "Rally Cross": ["Racing"], "Rally Cross 2": ["Racing"],
    "WipEout": ["Racing"], "WipEout XL": ["Racing"], "WipEout 3": ["Racing"],
    "Jet Moto": ["Racing"], "Jet Moto 2": ["Racing"], "Jet Moto 3": ["Racing"],
    "Road Rash": ["Racing", "Action"], "Road Rash 3D": ["Racing", "Action"],
    "Twisted Metal": ["Racing", "Action"], "Twisted Metal 2": ["Racing", "Action"],
    "Twisted Metal III": ["Racing", "Action"], "Twisted Metal 4": ["Racing", "Action"],
    "Vigilante 8": ["Racing", "Action"], "Vigilante 8: 2nd Offense": ["Racing", "Action"],
    "Destruction Derby": ["Racing"], "Destruction Derby 2": ["Racing"],
    "CTR: Crash Team Racing": ["Racing"], "Chocobo Racing": ["Racing"],
    "Tekken": ["Fighting"], "Tekken 2": ["Fighting"], "Tekken 3": ["Fighting"],
    "Street Fighter Alpha": ["Fighting"], "Street Fighter Alpha 2": ["Fighting"],
    "Street Fighter Alpha 3": ["Fighting"], "Street Fighter EX": ["Fighting"],
    "Street Fighter EX Plus Alpha": ["Fighting"], "Street Fighter EX2 Plus": ["Fighting"],
    "Marvel vs. Capcom": ["Fighting"], "X-Men vs. Street Fighter": ["Fighting"],
    "Marvel Super Heroes": ["Fighting"], "Marvel Super Heroes vs. Street Fighter": ["Fighting"],
    "Darkstalkers 3": ["Fighting"], "Rival Schools": ["Fighting"],
    "Soul Blade": ["Fighting"], "SoulCalibur": ["Fighting"],
    "Battle Arena Toshinden": ["Fighting"], "Battle Arena Toshinden 2": ["Fighting"],
    "Battle Arena Toshinden 3": ["Fighting"], "Dead or Alive": ["Fighting"],
    "Bloody Roar": ["Fighting"], "Bloody Roar 2": ["Fighting"],
    "Mortal Kombat Trilogy": ["Fighting"], "Mortal Kombat 4": ["Fighting"],
    "Bushido Blade": ["Fighting"], "Bushido Blade 2": ["Fighting"],
    "Tobal No. 1": ["Fighting"], "Tobal 2": ["Fighting"], "Ehrgeiz": ["Fighting"],
    "Guilty Gear": ["Fighting"], "The King of Fighters '95": ["Fighting"],
    "The King of Fighters '98": ["Fighting"], "The King of Fighters '99": ["Fighting"],
    "Fighting Force": ["Fighting", "Action"], "Fighting Force 2": ["Action"],
    "Tony Hawk's Pro Skater": ["Sports"], "Tony Hawk's Pro Skater 2": ["Sports"],
    "Tony Hawk's Pro Skater 3": ["Sports"], "Tony Hawk's Pro Skater 4": ["Sports"],
    "Cool Boarders": ["Sports"], "Cool Boarders 2": ["Sports"],
    "Cool Boarders 3": ["Sports"], "Cool Boarders 4": ["Sports"],
    "NBA Live 98": ["Sports"], "NBA Live 99": ["Sports"], "NBA Live 2000": ["Sports"],
    "NFL GameDay": ["Sports"], "NFL GameDay 98": ["Sports"], "NFL GameDay 99": ["Sports"],
    "Madden NFL 98": ["Sports"], "Madden NFL 99": ["Sports"], "Madden NFL 2000": ["Sports"],
    "NHL 98": ["Sports"], "NHL 99": ["Sports"], "NHL 2000": ["Sports"],
    "FIFA 98": ["Sports"], "FIFA 99": ["Sports"], "FIFA 2000": ["Sports"],
    "WWF SmackDown!": ["Sports", "Fighting"], "WWF SmackDown! 2": ["Sports", "Fighting"],
    "WWF Attitude": ["Sports", "Fighting"], "WWF War Zone": ["Sports", "Fighting"],
    "Hot Shots Golf": ["Sports"], "Hot Shots Golf 2": ["Sports"],
    "Armored Core": ["Action", "Simulation"], "Armored Core: Project Phantasma": ["Action", "Simulation"],
    "Armored Core: Master of Arena": ["Action", "Simulation"],
    "G-Police": ["Action", "Shooter"], "G-Police 2": ["Action", "Shooter"],
    "Colony Wars": ["Shooter", "Simulation"], "Colony Wars: Vengeance": ["Shooter", "Simulation"],
    "Ace Combat 2": ["Shooter", "Simulation"], "Ace Combat 3": ["Shooter", "Simulation"],
    "Omega Boost": ["Shooter"], "Einhander": ["Shooter"],
    "R-Type Delta": ["Shooter"], "R-Types": ["Shooter"], "Gradius Gaiden": ["Shooter"],
    "Raiden Project": ["Shooter"], "Raiden DX": ["Shooter"],
    "Raystorm": ["Shooter"], "Thunder Force V": ["Shooter"],
    "G Darius": ["Shooter"], "Strikers 1945": ["Shooter"], "Strikers 1945 II": ["Shooter"],
    "Nuclear Strike": ["Shooter", "Action"], "Soviet Strike": ["Shooter", "Action"],
    "Future Cop: LAPD": ["Shooter", "Action"], "Apocalypse": ["Action", "Shooter"],
    "Tomb Raider": ["Adventure", "Action"], "Tomb Raider II": ["Adventure", "Action"],
    "Tomb Raider III": ["Adventure", "Action"], "Tomb Raider: The Last Revelation": ["Adventure", "Action"],
    "Tomb Raider Chronicles": ["Adventure", "Action"],
    "Oddworld: Abe's Oddysee": ["Adventure", "Platformer"],
    "Oddworld: Abe's Exoddus": ["Adventure", "Platformer"],
    "Heart of Darkness": ["Adventure", "Platformer"], "Broken Sword": ["Adventure"],
    "Broken Sword II": ["Adventure"], "Myst": ["Adventure", "Puzzle"], "Riven": ["Adventure", "Puzzle"],
    "Tetris Plus": ["Puzzle"], "Bust-A-Move": ["Puzzle"], "Bust-A-Move 2": ["Puzzle"],
    "Bust-A-Move 4": ["Puzzle"], "Super Puzzle Fighter II Turbo": ["Puzzle"],
    "Intelligent Qube": ["Puzzle"], "Devil Dice": ["Puzzle"],
    "Parappa the Rapper": ["Rhythm"], "Um Jammer Lammy": ["Rhythm"], "Vib-Ribbon": ["Rhythm"],
    "Bust a Groove": ["Rhythm"], "Bust a Groove 2": ["Rhythm"],
    "Dance Dance Revolution": ["Rhythm"], "Beatmania": ["Rhythm"],
    "Command & Conquer": ["Strategy"], "Command & Conquer: Red Alert": ["Strategy"],
    "Warcraft II": ["Strategy"], "StarCraft": ["Strategy"], "Civilization II": ["Strategy"],
    "Theme Park": ["Strategy", "Simulation"], "Theme Hospital": ["Strategy", "Simulation"],
    "SimCity 2000": ["Strategy", "Simulation"], "Harvest Moon: Back to Nature": ["Simulation", "RPG"],
    "Spiderman": ["Action"], "Spider-Man 2: Enter Electro": ["Action"],
    "X-Men: Mutant Academy": ["Fighting"], "X-Men: Mutant Academy 2": ["Fighting"],
    "Toy Story 2": ["Platformer"], "A Bug's Life": ["Platformer"], "Tarzan": ["Platformer"],
    "Harry Potter": ["Adventure"], "Harry Potter and the Sorcerer's Stone": ["Adventure"],
    "Duke Nukem": ["Shooter", "Action"], "Duke Nukem: Time to Kill": ["Shooter", "Action"],
    "Doom": ["Shooter"], "Final Doom": ["Shooter"], "Quake II": ["Shooter"],
    "Hexen": ["Shooter"], "Alien Trilogy": ["Shooter", "Horror"],
    "Medal of Honor": ["Shooter"], "Medal of Honor: Underground": ["Shooter"],
    "Army Men": ["Shooter", "Action"], "Army Men 3D": ["Shooter", "Action"],
    "Warhawk": ["Shooter", "Action"], "Jumping Flash!": ["Platformer", "Shooter"],
    "Jumping Flash! 2": ["Platformer", "Shooter"], "Blasto": ["Action", "Platformer"],
    "Point Blank": ["Shooter"], "Point Blank 2": ["Shooter"],
    "Time Crisis": ["Shooter"], "Time Crisis: Project Titan": ["Shooter"],
    "Die Hard Trilogy": ["Action", "Shooter"], "Die Hard Trilogy 2": ["Action", "Shooter"],
    "Dynasty Warriors": ["Action", "Fighting"], "Kessen": ["Strategy"],
    "Strider": ["Action", "Platformer"], "Strider 2": ["Action", "Platformer"],
    "Bomberman": ["Puzzle", "Party"], "Bomberman World": ["Puzzle", "Party"],
    "Worms": ["Strategy", "Party"], "Worms Armageddon": ["Strategy", "Party"],
    "Rollcage": ["Racing"], "Rollcage Stage II": ["Racing"],
    "Micro Machines": ["Racing"], "Micro Machines V3": ["Racing"], "Re-Volt": ["Racing"],
}

# Top 10 curated picks per genre - Archive.org item IDs
# Excludes games with good native Steam/PC ports (FF7-9, Chrono Cross, RE2/3 remakes, etc.)
TOP_PICKS = {
    "all": [
        ("Metal Gear Solid", "psx_mgs"),
        ("Castlevania: Symphony of the Night", "psx_sotn"),
        ("Crash Bandicoot: Warped", "psx_crash3"),
        ("Tekken 3", "psx_tekken3"),
        ("Gran Turismo 2", "psx_gt2"),
        ("Spyro: Year of the Dragon", "psx_spyro3"),
        ("Silent Hill", "psx_silenthill"),
        ("Legend of Dragoon", "psx_lod"),
        ("Xenogears", "psx_xenogears"),
        ("Parasite Eve", "psx_parasiteeve"),
    ],
    "rpg": [
        ("Xenogears", "psx_xenogears"),
        ("Legend of Dragoon", "psx_lod"),
        ("Suikoden II", "psx_suikoden2"),
        ("Vagrant Story", "psx_vagrantstory"),
        ("Star Ocean: The Second Story", "psx_starocean2"),
        ("Parasite Eve", "psx_parasiteeve"),
        ("Breath of Fire IV", "psx_bof4"),
        ("Legend of Mana", "psx_legendofmana"),
        ("Grandia", "psx_grandia"),
        ("Valkyrie Profile", "psx_valkyrieprofile"),
    ],
    "action": [
        ("Metal Gear Solid", "psx_mgs"),
        ("Castlevania: Symphony of the Night", "psx_sotn"),
        ("Mega Man Legends", "psx_mmleg"),
        ("Mega Man X4", "psx_mmx4"),
        ("Legacy of Kain: Soul Reaver", "psx_soulreaver"),
        ("Syphon Filter", "psx_syphonfilter"),
        ("Tenchu: Stealth Assassins", "psx_tenchu"),
        ("MediEvil", "psx_medievil"),
        ("Tomba!", "psx_tomba"),
        ("Dino Crisis", "psx_dinocrisis"),
    ],
    "adventure": [
        ("Castlevania: Symphony of the Night", "psx_sotn"),
        ("Legacy of Kain: Soul Reaver", "psx_soulreaver"),
        ("Tomb Raider II", "psx_tombraider2"),
        ("Tomb Raider III", "psx_tombraider3"),
        ("Oddworld: Abe's Oddysee", "psx_abesoddysee"),
        ("Oddworld: Abe's Exoddus", "psx_abesexoddus"),
        ("MediEvil", "psx_medievil"),
        ("Heart of Darkness", "psx_heartofdarkness"),
        ("Alundra", "psx_alundra"),
        ("Fear Effect", "psx_feareffect"),
    ],
    "platformer": [
        ("Crash Bandicoot: Warped", "psx_crash3"),
        ("Crash Bandicoot 2", "psx_crash2"),
        ("Spyro: Year of the Dragon", "psx_spyro3"),
        ("Spyro 2: Ripto's Rage", "psx_spyro2"),
        ("Rayman", "psx_rayman"),
        ("Klonoa", "psx_klonoa"),
        ("Mega Man X4", "psx_mmx4"),
        ("Tomba!", "psx_tomba"),
        ("Ape Escape", "psx_apeescape"),
        ("Jumping Flash!", "psx_jumpingflash"),
    ],
    "racing": [
        ("Gran Turismo 2", "psx_gt2"),
        ("Gran Turismo", "psx_gt"),
        ("Crash Team Racing", "psx_ctr"),
        ("Need for Speed III: Hot Pursuit", "psx_nfs3"),
        ("Ridge Racer Type 4", "psx_rrt4"),
        ("WipEout XL", "psx_wipeoutxl"),
        ("WipEout 3", "psx_wipeout3"),
        ("Driver", "psx_driver"),
        ("Twisted Metal 2", "psx_twistedmetal2"),
        ("Jet Moto 2", "psx_jetmoto2"),
    ],
    "fighting": [
        ("Tekken 3", "psx_tekken3"),
        ("Street Fighter Alpha 3", "psx_sfa3"),
        ("Marvel vs. Capcom", "psx_mvc"),
        ("Soul Blade", "psx_soulblade"),
        ("Guilty Gear", "psx_guiltygear"),
        ("Bloody Roar 2", "psx_bloodyroar2"),
        ("Dead or Alive", "psx_doa"),
        ("Mortal Kombat Trilogy", "psx_mktrilogy"),
        ("Rival Schools", "psx_rivalschools"),
        ("Bushido Blade", "psx_bushidoblade"),
    ],
    "sports": [
        ("Tony Hawk's Pro Skater 2", "psx_thps2"),
        ("Tony Hawk's Pro Skater", "psx_thps"),
        ("Cool Boarders 2", "psx_coolboarders2"),
        ("NBA Live 2000", "psx_nbalive2000"),
        ("FIFA 2000", "psx_fifa2000"),
        ("WWF SmackDown! 2", "psx_smackdown2"),
        ("Hot Shots Golf 2", "psx_hsga2"),
        ("Madden NFL 2000", "psx_madden2000"),
        ("Mat Hoffman's Pro BMX", "psx_mathoffman"),
        ("NHL 2000", "psx_nhl2000"),
    ],
    "puzzle": [
        ("Intelligent Qube", "psx_iq"),
        ("Bust-A-Move 4", "psx_bustamove4"),
        ("Super Puzzle Fighter II Turbo", "psx_puzzlefighter"),
        ("Devil Dice", "psx_devildice"),
        ("Tetris Plus", "psx_tetrisplus"),
        ("Klax", "psx_klax"),
        ("Bust a Groove", "psx_bustagr"),
        ("Parappa the Rapper", "psx_parappa"),
        ("Um Jammer Lammy", "psx_ujl"),
        ("Vib-Ribbon", "psx_vibribbon"),
    ],
    "shooter": [
        ("Einhander", "psx_einhander"),
        ("R-Type Delta", "psx_rtypedelta"),
        ("G Darius", "psx_gdarius"),
        ("Gradius Gaiden", "psx_gradiusgaiden"),
        ("Thunder Force V", "psx_thunderforce5"),
        ("Raystorm", "psx_raystorm"),
        ("Colony Wars", "psx_colonywars"),
        ("Ace Combat 2", "psx_acecombat2"),
        ("Omega Boost", "psx_omegaboost"),
        ("Medal of Honor", "psx_moh"),
    ],
    "horror": [
        ("Silent Hill", "psx_silenthill"),
        ("Dino Crisis", "psx_dinocrisis"),
        ("Dino Crisis 2", "psx_dinocrisis2"),
        ("Parasite Eve", "psx_parasiteeve"),
        ("Parasite Eve II", "psx_parasiteeve2"),
        ("Clock Tower", "psx_clocktower"),
        ("Clock Tower II", "psx_clocktower2"),
        ("Nightmare Creatures", "psx_nightmarecreatures"),
        ("Galerians", "psx_galerians"),
        ("Echo Night", "psx_echonight"),
    ],
    "strategy": [
        ("Front Mission 3", "psx_fm3"),
        ("Tactics Ogre", "psx_tacticsogre"),
        ("Command & Conquer: Red Alert", "psx_ccredalert"),
        ("Vandal Hearts", "psx_vandalhearts"),
        ("Vandal Hearts II", "psx_vandalhearts2"),
        ("Brigandine", "psx_brigandine"),
        ("Worms Armageddon", "psx_worms"),
        ("Ogre Battle", "psx_ogrebattle"),
        ("Kartia", "psx_kartia"),
        ("Arc the Lad Collection", "psx_arcthelad"),
    ],
    "simulation": [
        ("Gran Turismo 2", "psx_gt2"),
        ("Harvest Moon: Back to Nature", "psx_harvestmoon"),
        ("Monster Rancher 2", "psx_mr2"),
        ("Armored Core", "psx_armoredcore"),
        ("Armored Core: Master of Arena", "psx_acmoa"),
        ("Ace Combat 3", "psx_acecombat3"),
        ("Colony Wars: Vengeance", "psx_colonywars2"),
        ("G-Police", "psx_gpolice"),
        ("MechWarrior 2", "psx_mechwarrior2"),
        ("Motor Toon Grand Prix 2", "psx_mtgp2"),
    ],
}

# GBA Game Genre Database
GBA_GENRES = {
    "Pokemon FireRed": ["RPG"], "Pokemon LeafGreen": ["RPG"], "Pokemon Ruby": ["RPG"],
    "Pokemon Sapphire": ["RPG"], "Pokemon Emerald": ["RPG"], "Pokemon Mystery Dungeon": ["RPG"],
    "Legend of Zelda": ["Action", "Adventure"], "Zelda Minish Cap": ["Action", "Adventure"],
    "Zelda A Link to the Past": ["Action", "Adventure"], "Zelda Four Swords": ["Action", "Adventure"],
    "Metroid Fusion": ["Action", "Adventure"], "Metroid Zero Mission": ["Action", "Adventure"],
    "Castlevania Aria of Sorrow": ["Action", "Adventure"], "Castlevania Harmony of Dissonance": ["Action"],
    "Castlevania Circle of the Moon": ["Action", "Adventure"],
    "Fire Emblem": ["Strategy", "RPG"], "Fire Emblem Sacred Stones": ["Strategy", "RPG"],
    "Advance Wars": ["Strategy"], "Advance Wars 2": ["Strategy"],
    "Golden Sun": ["RPG"], "Golden Sun Lost Age": ["RPG"],
    "Final Fantasy Tactics Advance": ["Strategy", "RPG"], "Final Fantasy I & II": ["RPG"],
    "Final Fantasy IV": ["RPG"], "Final Fantasy V": ["RPG"], "Final Fantasy VI": ["RPG"],
    "Mario Kart Super Circuit": ["Racing"], "F-Zero Maximum Velocity": ["Racing"],
    "Super Mario Advance": ["Platformer"], "Super Mario World": ["Platformer"],
    "Super Mario Bros 3": ["Platformer"], "Yoshi's Island": ["Platformer"],
    "Kirby Nightmare in Dream Land": ["Platformer"], "Kirby Amazing Mirror": ["Platformer"],
    "Sonic Advance": ["Platformer"], "Sonic Advance 2": ["Platformer"], "Sonic Advance 3": ["Platformer"],
    "Wario Land 4": ["Platformer"], "WarioWare": ["Puzzle"],
    "Mario & Luigi Superstar Saga": ["RPG"], "Mario Golf": ["Sports"], "Mario Tennis": ["Sports"],
    "Mega Man Zero": ["Action"], "Mega Man Zero 2": ["Action"], "Mega Man Zero 3": ["Action"],
    "Mega Man Battle Network": ["RPG", "Action"], "Mega Man Battle Network 2": ["RPG", "Action"],
    "Harvest Moon Friends of Mineral Town": ["Simulation"],
    "Breath of Fire": ["RPG"], "Breath of Fire II": ["RPG"],
    "Dragon Ball Z Legacy of Goku": ["Action", "RPG"], "Dragon Ball Z Buu's Fury": ["Action", "RPG"],
    "Kingdom Hearts Chain of Memories": ["Action", "RPG"],
    "Tactics Ogre": ["Strategy", "RPG"],
    "Street Fighter Alpha 3": ["Fighting"], "Super Street Fighter II": ["Fighting"],
    "Mortal Kombat": ["Fighting"], "Tekken Advance": ["Fighting"],
}

# N64 Game Genre Database
N64_GENRES = {
    "Super Mario 64": ["Platformer", "Adventure"], "Mario Kart 64": ["Racing"],
    "Mario Party": ["Party"], "Mario Party 2": ["Party"], "Mario Party 3": ["Party"],
    "Mario Golf": ["Sports"], "Mario Tennis": ["Sports"],
    "Paper Mario": ["RPG"], "Dr. Mario 64": ["Puzzle"],
    "Legend of Zelda Ocarina of Time": ["Action", "Adventure", "RPG"],
    "Legend of Zelda Majora's Mask": ["Action", "Adventure", "RPG"],
    "GoldenEye 007": ["Shooter", "Action"], "Perfect Dark": ["Shooter", "Action"],
    "Banjo-Kazooie": ["Platformer", "Adventure"], "Banjo-Tooie": ["Platformer", "Adventure"],
    "Donkey Kong 64": ["Platformer", "Adventure"], "Diddy Kong Racing": ["Racing"],
    "Conker's Bad Fur Day": ["Platformer", "Adventure"],
    "Star Fox 64": ["Shooter", "Action"], "F-Zero X": ["Racing"],
    "Wave Race 64": ["Racing"], "1080 Snowboarding": ["Sports"],
    "Excitebike 64": ["Racing"], "Cruisin USA": ["Racing"], "Cruisin World": ["Racing"],
    "Super Smash Bros": ["Fighting"], "Pokemon Stadium": ["RPG", "Strategy"],
    "Pokemon Stadium 2": ["RPG", "Strategy"], "Pokemon Snap": ["Adventure"],
    "Pokemon Puzzle League": ["Puzzle"], "Hey You Pikachu": ["Simulation"],
    "Kirby 64": ["Platformer"], "Yoshi's Story": ["Platformer"],
    "Starcraft 64": ["Strategy"], "Command & Conquer": ["Strategy"],
    "Ogre Battle 64": ["Strategy", "RPG"], "Harvest Moon 64": ["Simulation"],
    "Mystical Ninja Starring Goemon": ["Action", "Adventure"],
    "Goemon's Great Adventure": ["Action", "Platformer"],
    "Bomberman 64": ["Action", "Puzzle"], "Bomberman Hero": ["Action", "Platformer"],
    "Turok Dinosaur Hunter": ["Shooter", "Action"], "Turok 2 Seeds of Evil": ["Shooter", "Action"],
    "Turok 3 Shadow of Oblivion": ["Shooter", "Action"], "Turok Rage Wars": ["Shooter"],
    "Doom 64": ["Shooter"], "Quake 64": ["Shooter"], "Duke Nukem 64": ["Shooter"],
    "Resident Evil 2": ["Horror", "Action"], "Shadow Man": ["Action", "Adventure"],
    "Castlevania 64": ["Action", "Adventure"], "Castlevania Legacy of Darkness": ["Action", "Adventure"],
    "Body Harvest": ["Action", "Adventure"], "Jet Force Gemini": ["Shooter", "Action"],
    "Blast Corps": ["Action", "Puzzle"], "Pilotwings 64": ["Simulation"],
    "Sin and Punishment": ["Shooter", "Action"], "Mischief Makers": ["Platformer", "Action"],
    "Rayman 2": ["Platformer"], "Rocket Robot on Wheels": ["Platformer"],
    "Glover": ["Platformer"], "Chameleon Twist": ["Platformer"],
    "Space Station Silicon Valley": ["Puzzle", "Platformer"],
    "Snowboard Kids": ["Racing"], "Snowboard Kids 2": ["Racing"],
    "Ridge Racer 64": ["Racing"], "San Francisco Rush": ["Racing"],
    "Beetle Adventure Racing": ["Racing"], "Road Rash 64": ["Racing"],
    "WCW vs NWO World Tour": ["Wrestling"], "WCW vs NWO Revenge": ["Wrestling"],
    "WWF No Mercy": ["Wrestling"], "WWF WrestleMania 2000": ["Wrestling"],
    "Tony Hawk's Pro Skater": ["Sports"], "Tony Hawk's Pro Skater 2": ["Sports"],
    "Tony Hawk's Pro Skater 3": ["Sports"],
    "NFL Blitz": ["Sports"], "NBA Jam": ["Sports"], "Wayne Gretzky Hockey": ["Sports"],
    "International Superstar Soccer": ["Sports"], "FIFA 99": ["Sports"],
    "Mortal Kombat Trilogy": ["Fighting"], "Mortal Kombat 4": ["Fighting"],
    "Killer Instinct Gold": ["Fighting"], "Fighters Destiny": ["Fighting"],
    "Flying Dragon": ["Fighting"], "ClayFighter 63 1/3": ["Fighting"],
    "Wipeout 64": ["Racing"], "Extreme-G": ["Racing"], "Extreme-G 2": ["Racing"],
    "Star Wars Rogue Squadron": ["Shooter", "Action"],
    "Star Wars Episode I Racer": ["Racing"], "Star Wars Shadows of the Empire": ["Action"],
    "Mission Impossible": ["Action", "Stealth"], "Winback": ["Action", "Shooter"],
    "Army Men Sarges Heroes": ["Action", "Shooter"],
    "Gauntlet Legends": ["Action", "RPG"], "Hybrid Heaven": ["Action", "RPG"],
    "Quest 64": ["RPG"], "Aidyn Chronicles": ["RPG"],
    "Tetrisphere": ["Puzzle"], "Wetrix": ["Puzzle"], "Bust-A-Move 2": ["Puzzle"],
    "Rampage World Tour": ["Action"], "Rampage 2": ["Action"],
    "Rush 2": ["Racing"], "Top Gear Rally": ["Racing"], "Top Gear Overdrive": ["Racing"],
}

# GBA Top Picks per genre
GBA_TOP_PICKS = {
    "all": [
        ("Pokemon Emerald", "pokemon_emerald"),
        ("Legend of Zelda - Minish Cap", "zelda_minish"),
        ("Metroid Fusion", "metroid_fusion"),
        ("Fire Emblem", "fire_emblem"),
        ("Golden Sun", "golden_sun"),
        ("Castlevania - Aria of Sorrow", "cv_aria"),
        ("Advance Wars", "advance_wars"),
        ("Final Fantasy VI Advance", "ff6_advance"),
        ("Mario & Luigi Superstar Saga", "mario_luigi"),
        ("Mega Man Zero", "mmzero"),
    ],
    "rpg": [
        ("Pokemon Emerald", "pokemon_emerald"),
        ("Golden Sun", "golden_sun"),
        ("Golden Sun Lost Age", "golden_sun_2"),
        ("Final Fantasy VI Advance", "ff6_advance"),
        ("Final Fantasy Tactics Advance", "ffta"),
        ("Mario & Luigi Superstar Saga", "mario_luigi"),
        ("Fire Emblem", "fire_emblem"),
        ("Breath of Fire II", "bof2"),
        ("Kingdom Hearts Chain of Memories", "kh_com"),
        ("Mother 3", "mother3"),
    ],
    "action": [
        ("Metroid Fusion", "metroid_fusion"),
        ("Metroid Zero Mission", "metroid_zero"),
        ("Castlevania - Aria of Sorrow", "cv_aria"),
        ("Castlevania - Circle of the Moon", "cv_cotm"),
        ("Mega Man Zero", "mmzero"),
        ("Mega Man Zero 2", "mmzero2"),
        ("Astro Boy - Omega Factor", "astro_boy"),
        ("Ninja Five-O", "ninja_five_o"),
        ("Gunstar Super Heroes", "gunstar"),
        ("Drill Dozer", "drill_dozer"),
    ],
    "platformer": [
        ("Super Mario World", "smw_advance"),
        ("Yoshi's Island", "yoshi_island"),
        ("Super Mario Bros 3", "smb3_advance"),
        ("Kirby Nightmare in Dream Land", "kirby_nightmare"),
        ("Kirby Amazing Mirror", "kirby_mirror"),
        ("Sonic Advance 2", "sonic_advance2"),
        ("Wario Land 4", "wario_land4"),
        ("Donkey Kong Country", "dkc_advance"),
        ("Donkey Kong Country 2", "dkc2_advance"),
        ("Klonoa", "klonoa"),
    ],
    "strategy": [
        ("Fire Emblem", "fire_emblem"),
        ("Fire Emblem Sacred Stones", "fe_sacred"),
        ("Advance Wars", "advance_wars"),
        ("Advance Wars 2", "advance_wars2"),
        ("Final Fantasy Tactics Advance", "ffta"),
        ("Tactics Ogre", "tactics_ogre"),
        ("Shining Force", "shining_force"),
        ("Super Robot Taisen", "srt"),
        ("Onimusha Tactics", "onimusha"),
        ("Zone of the Enders", "zoe"),
    ],
}

# N64 Top Picks per genre
N64_TOP_PICKS = {
    "all": [
        ("Super Mario 64", "mario64"),
        ("Legend of Zelda - Ocarina of Time", "zelda_oot"),
        ("GoldenEye 007", "goldeneye"),
        ("Mario Kart 64", "mk64"),
        ("Super Smash Bros", "smash64"),
        ("Banjo-Kazooie", "banjo"),
        ("Perfect Dark", "perfect_dark"),
        ("Star Fox 64", "starfox"),
        ("Paper Mario", "paper_mario"),
        ("Donkey Kong 64", "dk64"),
    ],
    "platformer": [
        ("Super Mario 64", "mario64"),
        ("Banjo-Kazooie", "banjo"),
        ("Banjo-Tooie", "banjo2"),
        ("Donkey Kong 64", "dk64"),
        ("Conker's Bad Fur Day", "conker"),
        ("Rayman 2", "rayman2"),
        ("Kirby 64", "kirby64"),
        ("Yoshi's Story", "yoshi"),
        ("Mischief Makers", "mischief"),
        ("Rocket Robot on Wheels", "rocket_robot"),
    ],
    "action": [
        ("Legend of Zelda - Ocarina of Time", "zelda_oot"),
        ("Legend of Zelda - Majora's Mask", "zelda_mm"),
        ("GoldenEye 007", "goldeneye"),
        ("Perfect Dark", "perfect_dark"),
        ("Star Fox 64", "starfox"),
        ("Jet Force Gemini", "jfg"),
        ("Turok 2 - Seeds of Evil", "turok2"),
        ("Sin and Punishment", "sin_punishment"),
        ("Body Harvest", "body_harvest"),
        ("Blast Corps", "blastcorps"),
    ],
    "racing": [
        ("Mario Kart 64", "mk64"),
        ("Diddy Kong Racing", "dkr"),
        ("F-Zero X", "fzerox"),
        ("Wave Race 64", "waverace"),
        ("Star Wars Episode I Racer", "sw_racer"),
        ("Beetle Adventure Racing", "beetle"),
        ("Excitebike 64", "excitebike"),
        ("1080 Snowboarding", "1080"),
        ("San Francisco Rush", "rush"),
        ("Extreme-G", "extremeg"),
    ],
    "rpg": [
        ("Paper Mario", "paper_mario"),
        ("Legend of Zelda - Ocarina of Time", "zelda_oot"),
        ("Legend of Zelda - Majora's Mask", "zelda_mm"),
        ("Ogre Battle 64", "ogre64"),
        ("Quest 64", "quest64"),
        ("Hybrid Heaven", "hybrid"),
        ("Gauntlet Legends", "gauntlet"),
        ("Aidyn Chronicles", "aidyn"),
        ("Harvest Moon 64", "hm64"),
        ("Pokemon Stadium 2", "pokemon_stadium2"),
    ],
    "shooter": [
        ("GoldenEye 007", "goldeneye"),
        ("Perfect Dark", "perfect_dark"),
        ("Turok Dinosaur Hunter", "turok"),
        ("Turok 2 - Seeds of Evil", "turok2"),
        ("Doom 64", "doom64"),
        ("Quake 64", "quake64"),
        ("Jet Force Gemini", "jfg"),
        ("Star Wars Rogue Squadron", "rogue_squad"),
        ("Duke Nukem 64", "duke64"),
        ("Sin and Punishment", "sin_punishment"),
    ],
    "fighting": [
        ("Super Smash Bros", "smash64"),
        ("Killer Instinct Gold", "ki_gold"),
        ("Mortal Kombat Trilogy", "mk_trilogy"),
        ("Mortal Kombat 4", "mk4"),
        ("Fighters Destiny", "fighters_dest"),
        ("Flying Dragon", "flying_dragon"),
        ("ClayFighter 63 1/3", "clayfighter"),
        ("WWF No Mercy", "wwf_nomercy"),
        ("WCW vs NWO Revenge", "wcw_revenge"),
        ("WWF WrestleMania 2000", "wm2000"),
    ],
}

def get_game_genre(game_name):
    """Look up genres for a game name using fuzzy matching"""
    import re
    # Clean up the game name
    clean = re.sub(r'\s*[\(\[].*?[\)\]]', '', game_name)  # Remove (USA), [NTSC], etc
    clean = re.sub(r'\s+', ' ', clean).strip()
    
    # Check PS1, GBA, and N64 databases
    all_genres = {**PS1_GENRES, **GBA_GENRES, **N64_GENRES}
    
    # Try exact match first
    if clean in all_genres:
        return all_genres[clean]
    
    # Try case-insensitive match
    clean_lower = clean.lower()
    for name, genres in all_genres.items():
        if name.lower() == clean_lower:
            return genres
    
    # Try partial match (game name contains DB entry or vice versa)
    for name, genres in all_genres.items():
        if name.lower() in clean_lower or clean_lower in name.lower():
            return genres
    
    return None

def get_settings_template(game_dir):
    """Generate DuckStation settings.ini content"""
    return f"""[BIOS]
SearchDirectory = {game_dir}/bios
PathNTSCU = 
PathNTSCJ = 
PathPAL = 

[Main]
SettingsVersion = 3
SetupWizardIncomplete = false
StartFullscreen = true
ConfirmPowerOff = false
SaveStateOnExit = true
StartPaused = false
PauseOnFocusLoss = false
PauseOnControllerDisconnection = false

[Display]
Fullscreen = true
VSync = true
AspectRatio = 4:3
CropMode = Overscan
Stretch = false
IntegerScaling = false
DisplayAlignment = Center

[Console]
Region = Auto

[ControllerPorts]
MultitapMode = Disabled

[InputSources]
SDL = true
SDLControllerEnhancedMode = true

[Pad1]
Type = AnalogController
Up = SDL-0/DPadUp
Down = SDL-0/DPadDown
Left = SDL-0/DPadLeft
Right = SDL-0/DPadRight
Triangle = SDL-0/Y
Circle = SDL-0/B
Cross = SDL-0/A
Square = SDL-0/X
Select = SDL-0/Back
Start = SDL-0/Start
L1 = SDL-0/LeftShoulder
R1 = SDL-0/RightShoulder
L2 = SDL-0/+LeftTrigger
R2 = SDL-0/+RightTrigger
L3 = SDL-0/LeftStick
R3 = SDL-0/RightStick
LLeft = SDL-0/-LeftX
LRight = SDL-0/+LeftX
LUp = SDL-0/-LeftY
LDown = SDL-0/+LeftY
RLeft = SDL-0/-RightX
RRight = SDL-0/+RightX
RUp = SDL-0/-RightY
RDown = SDL-0/+RightY

[Hotkeys]
OpenPauseMenu = SDL-0/Back & SDL-0/Start
FastForward = SDL-0/LeftStick
SaveSelectedSaveState = SDL-0/Start & SDL-0/RightShoulder
LoadSelectedSaveState = SDL-0/Start & SDL-0/LeftShoulder
SelectPreviousSaveStateSlot = SDL-0/Start & SDL-0/DPadLeft
SelectNextSaveStateSlot = SDL-0/Start & SDL-0/DPadRight
Screenshot = SDL-0/Start & SDL-0/Y

[GPU]
Renderer = Vulkan
ResolutionScale = 3

[Audio]
Backend = Cubeb
OutputVolume = 100
FastForwardVolume = 100

[Cheevos]
Enabled = false

[GameList]
RecursiveScan = true
"""

# === FRUTIGER AERO COLOR PALETTE ===
# Bright sky gradient aesthetic with glossy glass elements
COLORS = {
    'sky_top': '#e6f5ff',            # Bright white-cyan sky top
    'sky_mid': '#7ec8f0',            # Mid-tone sky blue
    'sky_bottom': '#1e8cdc',         # Vibrant sky blue bottom
    'glass': 'rgba(255, 255, 255, 0.25)',  # Glass overlay
    'glass_border': 'rgba(255, 255, 255, 0.5)',  # Glass border
    'accent': '#00a8e8',             # Bright aqua
    'accent_light': '#4dc8ff',       # Light aqua
    'accent_glow': '#00d4ff',        # Glow color
    'green': '#50c878',              # Success green (aurora-like)
    'text': '#1a3a5a',               # Dark blue text for readability
    'text_light': '#ffffff',         # White text for dark elements
    'text_dim': '#4a7090',           # Dimmed blue-gray text
    'success': '#50c878',
    'warning': '#f0a030',
    'bubble': 'rgba(200, 230, 255, 0.4)',  # Glossy bubble tint
}

CSS = f"""
/* === FRUTIGER AERO - BRIGHT SKY THEME === */
/* Bright sky gradient with glossy glass elements and bubbly aesthetic */

/* Base text colors for all elements */
* {{
    color: {COLORS['text']};
}}

window {{
    background: linear-gradient(180deg,
        {COLORS['sky_top']} 0%,
        {COLORS['sky_mid']} 50%,
        {COLORS['sky_bottom']} 100%);
}}

.title {{
    font-size: 36px;
    font-weight: bold;
    color: {COLORS['text']};
    text-shadow: 0 1px 0 rgba(255, 255, 255, 0.8),
                 0 2px 8px rgba(0, 168, 232, 0.3);
}}

.subtitle {{
    font-size: 18px;
    color: {COLORS['text_dim']};
}}

/* Glass card - frosted glass effect */
.card {{
    background: linear-gradient(180deg,
        rgba(255, 255, 255, 0.25) 0%,
        rgba(255, 255, 255, 0.12) 100%);
    border-radius: 16px;
    border: 1px solid rgba(255, 255, 255, 0.4);
    padding: 16px;
    box-shadow: 0 8px 32px rgba(30, 100, 180, 0.15),
                inset 0 1px 0 rgba(255, 255, 255, 0.5),
                inset 0 -1px 0 rgba(255, 255, 255, 0.2);
}}

/* Glossy bubble buttons - signature Aero style - semi-transparent */
.menu-button {{
    background: linear-gradient(180deg,
        rgba(255, 255, 255, 0.35) 0%,
        rgba(255, 255, 255, 0.15) 45%,
        rgba(200, 230, 255, 0.2) 55%,
        rgba(150, 210, 255, 0.25) 100%);
    border-radius: 16px;
    border: 1px solid rgba(255, 255, 255, 0.5);
    padding: 14px 18px;
    box-shadow: 0 4px 20px rgba(30, 100, 180, 0.15),
                inset 0 2px 0 rgba(255, 255, 255, 0.5),
                inset 0 -2px 4px rgba(100, 180, 255, 0.15);
}}

.menu-button:hover {{
    background: linear-gradient(180deg,
        rgba(255, 255, 255, 0.5) 0%,
        rgba(255, 255, 255, 0.25) 45%,
        rgba(180, 230, 255, 0.3) 55%,
        rgba(130, 200, 255, 0.35) 100%);
    border-color: rgba(255, 255, 255, 0.7);
    box-shadow: 0 6px 25px rgba(0, 168, 232, 0.25),
                inset 0 2px 0 rgba(255, 255, 255, 0.6),
                inset 0 -2px 6px rgba(100, 180, 255, 0.2);
}}

.menu-button:focus {{
    background: linear-gradient(180deg,
        rgba(200, 240, 255, 0.5) 0%,
        rgba(150, 220, 255, 0.3) 45%,
        rgba(100, 200, 255, 0.35) 55%,
        rgba(80, 180, 255, 0.4) 100%);
    border: 2px solid {COLORS['accent_light']};
    box-shadow: 0 0 30px rgba(0, 168, 232, 0.4),
                inset 0 2px 0 rgba(255, 255, 255, 0.5);
    outline: none;
}}

.menu-button-title {{
    font-size: 22px;
    font-weight: bold;
    color: {COLORS['text']};
    text-shadow: 0 1px 0 rgba(255, 255, 255, 0.6);
}}

.menu-button-subtitle {{
    font-size: 16px;
    color: {COLORS['text_dim']};
}}

/* Ensure all labels are visible */
label {{
    color: {COLORS['text']};
}}

/* Button labels need explicit color */
button label {{
    color: inherit;
}}

/* Vibrant glossy accent button - like a shiny orb */
.accent-button {{
    background: linear-gradient(180deg,
        #9de8ff 0%,
        {COLORS['accent_light']} 40%,
        {COLORS['accent']} 60%,
        #0088c0 100%);
    color: white;
    border: 1px solid rgba(255, 255, 255, 0.5);
    border-radius: 12px;
    padding: 10px 20px;
    font-weight: bold;
    text-shadow: 0 1px 2px rgba(0, 60, 120, 0.5);
    box-shadow: 0 6px 20px rgba(0, 168, 232, 0.5),
                inset 0 2px 0 rgba(255, 255, 255, 0.5),
                inset 0 -2px 4px rgba(0, 80, 160, 0.3);
}}

.accent-button label {{
    color: white;
}}

.accent-button:hover {{
    background: linear-gradient(180deg,
        #c0f0ff 0%,
        #7dd8ff 40%,
        {COLORS['accent_light']} 60%,
        {COLORS['accent']} 100%);
    box-shadow: 0 8px 30px rgba(0, 168, 232, 0.6),
                inset 0 2px 0 rgba(255, 255, 255, 0.7),
                inset 0 -2px 6px rgba(0, 80, 160, 0.3);
}}

.accent-button:focus {{
    background: linear-gradient(180deg,
        #d0f5ff 0%,
        #90e0ff 40%,
        #60d0ff 60%,
        {COLORS['accent']} 100%);
    border: 2px solid white;
    box-shadow: 0 0 35px rgba(0, 200, 255, 0.7),
                inset 0 2px 0 rgba(255, 255, 255, 0.8);
    outline: none;
}}

/* Secondary glass button - more transparent */
.flat-button {{
    background: linear-gradient(180deg,
        rgba(255, 255, 255, 0.3) 0%,
        rgba(255, 255, 255, 0.15) 45%,
        rgba(200, 230, 255, 0.2) 100%);
    color: {COLORS['text']};
    border: 1px solid rgba(255, 255, 255, 0.5);
    border-radius: 10px;
    padding: 10px 20px;
    box-shadow: 0 3px 12px rgba(30, 100, 180, 0.1),
                inset 0 1px 0 rgba(255, 255, 255, 0.4);
}}

.flat-button:hover {{
    background: linear-gradient(180deg,
        rgba(255, 255, 255, 0.45) 0%,
        rgba(255, 255, 255, 0.25) 45%,
        rgba(180, 220, 255, 0.3) 100%);
    border-color: rgba(255, 255, 255, 0.7);
    box-shadow: 0 5px 18px rgba(0, 168, 232, 0.2),
                inset 0 1px 0 rgba(255, 255, 255, 0.5);
}}

.flat-button:focus {{
    background: linear-gradient(180deg,
        rgba(200, 240, 255, 0.4) 0%,
        rgba(160, 220, 255, 0.25) 100%);
    border: 2px solid {COLORS['accent']};
    box-shadow: 0 0 20px rgba(0, 168, 232, 0.3);
    outline: none;
}}

.flat-button label {{
    color: {COLORS['text']};
}}

/* Text input - frosted glass */
.entry {{
    background: linear-gradient(180deg,
        rgba(255, 255, 255, 0.85) 0%,
        rgba(255, 255, 255, 0.7) 100%);
    color: {COLORS['text']};
    border: 1px solid rgba(255, 255, 255, 0.8);
    border-radius: 10px;
    padding: 10px;
    box-shadow: inset 0 2px 4px rgba(30, 100, 180, 0.15),
                0 2px 8px rgba(30, 100, 180, 0.1);
}}

.entry:focus {{
    border: 2px solid {COLORS['accent']};
    box-shadow: 0 0 15px rgba(0, 168, 232, 0.3),
                inset 0 2px 4px rgba(30, 100, 180, 0.1);
}}

/* Status bar - aurora-like gradient */
.status-bar {{
    background: linear-gradient(90deg,
        rgba(100, 255, 180, 0.3) 0%,
        rgba(80, 220, 255, 0.25) 50%,
        rgba(150, 200, 255, 0.2) 100%);
    padding: 8px 14px;
    border-radius: 8px;
    font-size: 16px;
    color: {COLORS['text']};
    border: 1px solid rgba(255, 255, 255, 0.5);
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.5);
}}

/* Progress bar - glossy orb-like gradient */
.progress {{
    background: rgba(255, 255, 255, 0.4);
    border-radius: 12px;
    min-height: 24px;
    border: 1px solid rgba(255, 255, 255, 0.6);
    box-shadow: inset 0 2px 4px rgba(30, 100, 180, 0.15);
}}

.progress trough {{
    background: rgba(255, 255, 255, 0.4);
    border-radius: 12px;
    min-height: 24px;
}}

.progress progress {{
    background: linear-gradient(180deg,
        #9de8ff 0%,
        {COLORS['accent_light']} 30%,
        {COLORS['accent']} 70%,
        #0088c0 100%);
    border-radius: 12px;
    min-height: 24px;
    box-shadow: 0 0 20px rgba(0, 168, 232, 0.5),
                inset 0 2px 0 rgba(255, 255, 255, 0.5),
                inset 0 -2px 4px rgba(0, 80, 160, 0.3);
}}

.progress text {{
    color: white;
    font-weight: bold;
    font-size: 14px;
    text-shadow: 0 1px 2px rgba(0, 60, 120, 0.5);
}}

/* Scrollbar - glossy bubble style */
scrolledwindow {{
    background: transparent;
}}

scrollbar {{
    background: rgba(255, 255, 255, 0.2);
}}

scrollbar slider {{
    background: linear-gradient(90deg,
        rgba(150, 220, 255, 0.7) 0%,
        rgba(200, 240, 255, 0.8) 50%,
        rgba(150, 220, 255, 0.7) 100%);
    border-radius: 8px;
    min-width: 10px;
    border: 1px solid rgba(255, 255, 255, 0.6);
}}

scrollbar slider:hover {{
    background: linear-gradient(90deg,
        {COLORS['accent_light']} 0%,
        #a0e8ff 50%,
        {COLORS['accent_light']} 100%);
    box-shadow: 0 0 10px rgba(0, 168, 232, 0.5);
}}

/* Game cards - glossy glass tiles */
.game-card {{
    background: linear-gradient(180deg,
        rgba(255, 255, 255, 0.3) 0%,
        rgba(255, 255, 255, 0.15) 45%,
        rgba(200, 230, 255, 0.2) 100%);
    border-radius: 14px;
    padding: 4px;
    border: 2px solid rgba(255, 255, 255, 0.4);
    box-shadow: 0 6px 20px rgba(30, 100, 180, 0.15),
                inset 0 1px 0 rgba(255, 255, 255, 0.5);
}}

.game-card:hover {{
    background: linear-gradient(180deg,
        rgba(255, 255, 255, 0.45) 0%,
        rgba(255, 255, 255, 0.25) 45%,
        rgba(180, 220, 255, 0.3) 100%);
    border-color: rgba(255, 255, 255, 0.6);
    box-shadow: 0 8px 25px rgba(0, 168, 232, 0.25),
                inset 0 1px 0 rgba(255, 255, 255, 0.6);
}}

flowboxchild:selected .game-card {{
    background: linear-gradient(180deg,
        rgba(150, 220, 255, 0.4) 0%,
        rgba(100, 200, 255, 0.3) 100%);
    border-color: {COLORS['accent_light']};
}}

flowboxchild:focus .game-card {{
    border-color: {COLORS['accent']};
    box-shadow: 0 0 25px rgba(0, 168, 232, 0.4),
                inset 0 1px 0 rgba(255, 255, 255, 0.5);
}}

flowboxchild:selected:focus .game-card {{
    background: linear-gradient(180deg,
        rgba(130, 210, 255, 0.5) 0%,
        rgba(80, 180, 255, 0.4) 100%);
    border-color: white;
    box-shadow: 0 0 35px rgba(0, 200, 255, 0.5),
                inset 0 2px 0 rgba(255, 255, 255, 0.5);
}}

.game-title {{
    font-size: 18px;
    font-weight: bold;
    color: white;
    background: linear-gradient(transparent, rgba(30, 80, 140, 0.85));
    padding: 40px 8px 8px 8px;
    text-shadow: 0 1px 3px rgba(0, 40, 80, 0.6);
}}

/* Packaging view */
.packaging-title {{
    font-size: 28px;
    font-weight: bold;
    color: {COLORS['text']};
    text-shadow: 0 1px 0 rgba(255, 255, 255, 0.7),
                 0 2px 8px rgba(0, 168, 232, 0.2);
}}

.packaging-step-done {{
    color: #2a9050;
    font-weight: bold;
    text-shadow: 0 1px 0 rgba(255, 255, 255, 0.5);
}}

.packaging-step-active {{
    color: #0080c0;
    font-weight: bold;
    text-shadow: 0 0 10px rgba(0, 168, 232, 0.4);
}}

.packaging-step-pending {{
    color: {COLORS['text_dim']};
}}

.log-view {{
    background: linear-gradient(180deg,
        rgba(255, 255, 255, 0.85) 0%,
        rgba(255, 255, 255, 0.7) 100%);
    border-radius: 10px;
    padding: 12px;
    font-family: monospace;
    font-size: 14px;
    color: {COLORS['text']};
    border: 1px solid rgba(255, 255, 255, 0.8);
    box-shadow: inset 0 2px 4px rgba(30, 100, 180, 0.1);
}}

.success-text {{
    color: #2a9050;
    font-size: 18px;
    font-weight: bold;
    text-shadow: 0 1px 0 rgba(255, 255, 255, 0.5);
}}

.warning-text {{
    color: #c07020;
    text-shadow: 0 1px 0 rgba(255, 255, 255, 0.4);
}}

/* System toggle buttons - glossy pills */
.system-button {{
    background: linear-gradient(180deg,
        rgba(255, 255, 255, 0.6) 0%,
        rgba(255, 255, 255, 0.35) 45%,
        rgba(200, 230, 255, 0.4) 100%);
    border: 1px solid rgba(255, 255, 255, 0.7);
    border-radius: 22px;
    padding: 10px 22px;
    font-size: 18px;
    font-weight: bold;
    color: {COLORS['text_dim']};
    box-shadow: 0 3px 10px rgba(30, 100, 180, 0.15),
                inset 0 1px 0 rgba(255, 255, 255, 0.8);
}}

.system-button:checked {{
    background: linear-gradient(180deg,
        #9de8ff 0%,
        {COLORS['accent_light']} 40%,
        {COLORS['accent']} 60%,
        #0088c0 100%);
    border-color: rgba(255, 255, 255, 0.5);
    color: white;
    text-shadow: 0 1px 2px rgba(0, 60, 120, 0.5);
    box-shadow: 0 0 25px rgba(0, 168, 232, 0.5),
                inset 0 2px 0 rgba(255, 255, 255, 0.5),
                inset 0 -2px 4px rgba(0, 80, 160, 0.3);
}}

.system-button:hover {{
    border-color: rgba(255, 255, 255, 0.9);
    box-shadow: 0 5px 15px rgba(0, 168, 232, 0.25),
                inset 0 1px 0 rgba(255, 255, 255, 0.9);
}}

.system-button:focus {{
    border: 2px solid {COLORS['accent']};
    box-shadow: 0 0 20px rgba(0, 168, 232, 0.4);
    outline: none;
}}

.system-button label {{
    color: {COLORS['text_dim']};
}}

.system-button:checked label {{
    color: white;
}}

/* Exit button - danger red with glossy orb style */
.exit-button {{
    background: linear-gradient(180deg,
        #ff9999 0%,
        #e06060 40%,
        #c04040 60%,
        #a03030 100%);
    color: white;
    border: 1px solid rgba(255, 255, 255, 0.4);
    border-radius: 10px;
    padding: 8px 16px;
    font-weight: bold;
    text-shadow: 0 1px 2px rgba(80, 0, 0, 0.5);
    box-shadow: 0 4px 15px rgba(180, 60, 60, 0.4),
                inset 0 2px 0 rgba(255, 255, 255, 0.4),
                inset 0 -2px 4px rgba(100, 0, 0, 0.3);
}}

.exit-button:hover {{
    background: linear-gradient(180deg,
        #ffb0b0 0%,
        #e87070 40%,
        #d05050 60%,
        #b04040 100%);
    box-shadow: 0 6px 20px rgba(180, 60, 60, 0.5),
                inset 0 2px 0 rgba(255, 255, 255, 0.5);
}}

.exit-button:focus {{
    border: 2px solid white;
    box-shadow: 0 0 25px rgba(200, 80, 80, 0.6);
    outline: none;
}}

.exit-button label {{
    color: white;
}}

/* Dialog styling - fullscreen themed dialogs */
dialog {{
    background: linear-gradient(180deg,
        {COLORS['sky_top']} 0%,
        {COLORS['sky_mid']} 50%,
        {COLORS['sky_bottom']} 100%);
}}

.dialog-content {{
    background: linear-gradient(180deg,
        rgba(255, 255, 255, 0.3) 0%,
        rgba(255, 255, 255, 0.15) 100%);
    border-radius: 20px;
    border: 1px solid rgba(255, 255, 255, 0.5);
    padding: 24px;
    margin: 20px;
    box-shadow: 0 8px 40px rgba(30, 100, 180, 0.2),
                inset 0 1px 0 rgba(255, 255, 255, 0.6);
}}

.dialog-title {{
    font-size: 30px;
    font-weight: bold;
    color: {COLORS['text']};
    text-shadow: 0 1px 0 rgba(255, 255, 255, 0.8);
}}

.dialog-message {{
    font-size: 18px;
    color: {COLORS['text']};
}}

.dialog-secondary {{
    font-size: 16px;
    color: {COLORS['text_dim']};
}}

/* Warning dialog accent */
.dialog-warning {{
    border-left: 4px solid {COLORS['warning']};
}}

/* Listbox styling */
list, listbox {{
    background: transparent;
}}

list row, listbox row {{
    background: linear-gradient(180deg,
        rgba(255, 255, 255, 0.5) 0%,
        rgba(255, 255, 255, 0.3) 100%);
    border-radius: 10px;
    margin: 4px 0;
    border: 1px solid rgba(255, 255, 255, 0.5);
}}

list row:hover, listbox row:hover {{
    background: linear-gradient(180deg,
        rgba(255, 255, 255, 0.7) 0%,
        rgba(255, 255, 255, 0.4) 100%);
    border-color: rgba(255, 255, 255, 0.8);
}}

list row:selected, listbox row:selected {{
    background: linear-gradient(180deg,
        rgba(150, 220, 255, 0.6) 0%,
        rgba(100, 200, 255, 0.5) 100%);
    border-color: {COLORS['accent_light']};
}}

/* Text view styling */
textview {{
    background: rgba(255, 255, 255, 0.8);
    color: {COLORS['text']};
}}

textview text {{
    background: transparent;
    color: {COLORS['text']};
}}

/* Combobox / Dropdown styling */
combobox {{
    background: linear-gradient(180deg,
        rgba(255, 255, 255, 0.85) 0%,
        rgba(255, 255, 255, 0.7) 100%);
    border-radius: 10px;
    border: 1px solid rgba(255, 255, 255, 0.8);
}}

combobox button {{
    background: linear-gradient(180deg,
        rgba(255, 255, 255, 0.7) 0%,
        rgba(255, 255, 255, 0.5) 100%);
    border-radius: 10px;
    border: 1px solid rgba(255, 255, 255, 0.8);
    padding: 8px 12px;
}}

combobox button:hover {{
    background: linear-gradient(180deg,
        rgba(255, 255, 255, 0.85) 0%,
        rgba(255, 255, 255, 0.6) 100%);
}}

combobox cellview {{
    color: {COLORS['text']};
}}

combobox arrow {{
    color: {COLORS['text']};
}}

/* Dropdown/Popup menu styling */
popover, popover.background {{
    background: linear-gradient(180deg,
        rgba(230, 245, 255, 0.98) 0%,
        rgba(200, 230, 255, 0.95) 100%);
    border-radius: 12px;
    border: 1px solid rgba(255, 255, 255, 0.8);
    box-shadow: 0 8px 32px rgba(30, 100, 180, 0.3);
}}

popover contents {{
    background: transparent;
}}

popover modelbutton {{
    background: transparent;
    padding: 8px 16px;
    border-radius: 8px;
    color: {COLORS['text']};
}}

popover modelbutton:hover {{
    background: linear-gradient(180deg,
        rgba(150, 220, 255, 0.5) 0%,
        rgba(100, 200, 255, 0.4) 100%);
}}

/* Menu styling */
menu {{
    background: linear-gradient(180deg,
        rgba(230, 245, 255, 0.98) 0%,
        rgba(200, 230, 255, 0.95) 100%);
    border-radius: 12px;
    border: 1px solid rgba(255, 255, 255, 0.8);
    box-shadow: 0 8px 32px rgba(30, 100, 180, 0.3);
    padding: 8px;
}}

menu menuitem {{
    background: transparent;
    padding: 8px 16px;
    border-radius: 8px;
    color: {COLORS['text']};
}}

menu menuitem:hover {{
    background: linear-gradient(180deg,
        rgba(150, 220, 255, 0.5) 0%,
        rgba(100, 200, 255, 0.4) 100%);
}}

menu menuitem label {{
    color: {COLORS['text']};
}}

/* File chooser dialog styling */
filechooser {{
    background: linear-gradient(180deg,
        {COLORS['sky_top']} 0%,
        {COLORS['sky_mid']} 50%,
        {COLORS['sky_bottom']} 100%);
}}

filechooser .view {{
    background: rgba(255, 255, 255, 0.7);
    color: {COLORS['text']};
}}

filechooser list {{
    background: rgba(255, 255, 255, 0.5);
}}

filechooser row {{
    background: transparent;
    color: {COLORS['text']};
}}

filechooser row:selected {{
    background: linear-gradient(180deg,
        rgba(150, 220, 255, 0.6) 0%,
        rgba(100, 200, 255, 0.5) 100%);
}}

/* Placesidebar (file chooser sidebar) */
placessidebar {{
    background: rgba(255, 255, 255, 0.4);
}}

placessidebar row {{
    background: transparent;
    color: {COLORS['text']};
}}

placessidebar row:selected {{
    background: linear-gradient(180deg,
        rgba(150, 220, 255, 0.6) 0%,
        rgba(100, 200, 255, 0.5) 100%);
}}

/* Pathbar in file chooser */
pathbar button {{
    background: linear-gradient(180deg,
        rgba(255, 255, 255, 0.6) 0%,
        rgba(255, 255, 255, 0.4) 100%);
    border: 1px solid rgba(255, 255, 255, 0.7);
    border-radius: 6px;
    color: {COLORS['text']};
}}

pathbar button:hover {{
    background: linear-gradient(180deg,
        rgba(255, 255, 255, 0.8) 0%,
        rgba(255, 255, 255, 0.5) 100%);
}}

pathbar button label {{
    color: {COLORS['text']};
}}
"""


class SteamShortcuts:
    """Manage Steam non-Steam game shortcuts"""
    
    @staticmethod
    def find_user_id():
        """Find the Steam user ID"""
        if not STEAM_USERDATA or not STEAM_USERDATA.exists():
            return None
        for user_dir in STEAM_USERDATA.iterdir():
            if user_dir.is_dir() and user_dir.name.isdigit():
                return user_dir.name
        return None
    
    @staticmethod
    def get_shortcuts_path():
        """Get path to shortcuts.vdf"""
        user_id = SteamShortcuts.find_user_id()
        if user_id:
            return STEAM_USERDATA / user_id / "config" / "shortcuts.vdf"
        return None
    
    @staticmethod
    def generate_app_id(exe, name):
        """Generate Steam app ID for non-Steam game grid artwork
        
        For grid artwork, Steam uses: (crc32 | 0x80000000) << 32 | 0x02000000
        But the filename just uses the upper 32 bits as a signed int.
        """
        import binascii
        key = exe + name
        crc = binascii.crc32(key.encode('utf-8')) & 0xffffffff
        # Set high bit and return as unsigned
        shortcut_id = crc | 0x80000000
        return shortcut_id
    
    @staticmethod
    def generate_shortcut_id(exe, name):
        """Generate shortcut ID for shortcuts.vdf (signed 32-bit)"""
        import binascii
        key = exe + name
        crc = binascii.crc32(key.encode('utf-8')) & 0xffffffff
        shortcut_id = crc | 0x80000000
        # Convert to signed 32-bit for shortcuts.vdf
        if shortcut_id >= 0x80000000:
            shortcut_id = shortcut_id - 0x100000000
        return shortcut_id
    
    @staticmethod
    def read_shortcuts():
        """Read existing shortcuts from shortcuts.vdf"""
        shortcuts_path = SteamShortcuts.get_shortcuts_path()
        if not shortcuts_path or not shortcuts_path.exists():
            return {}
        
        try:
            with open(shortcuts_path, 'rb') as f:
                data = f.read()
            return SteamShortcuts._parse_vdf(data)
        except Exception as e:
            print(f"Error reading shortcuts: {e}")
            return {}
    
    @staticmethod
    def _parse_vdf(data):
        """Parse binary VDF format"""
        shortcuts = {}
        pos = 0
        
        try:
            # Skip initial null byte if present
            if pos < len(data) and data[pos:pos+1] == b'\x00':
                pos += 1
            
            # Check for "shortcuts" header
            if data[pos:pos+9] != b'shortcuts':
                debug_log("VDF: No shortcuts header found")
                return {}
            pos += 9
            
            # Skip null byte after header
            if pos < len(data) and data[pos:pos+1] == b'\x00':
                pos += 1
            
            # Parse each shortcut entry
            while pos < len(data):
                # Each entry starts with \x00 + index + \x00
                if data[pos:pos+1] != b'\x00':
                    break
                pos += 1
                
                # Read index string
                idx_start = pos
                while pos < len(data) and data[pos:pos+1] != b'\x00':
                    pos += 1
                idx_str = data[idx_start:pos].decode('utf-8', errors='ignore')
                pos += 1  # Skip null terminator
                
                if not idx_str or not idx_str.isdigit():
                    break
                
                shortcut = {}
                
                # Parse key-value pairs until we hit end marker \x08\x08
                while pos < len(data):
                    type_byte = data[pos:pos+1]
                    
                    if type_byte == b'\x08':  # End of this shortcut
                        pos += 1
                        if pos < len(data) and data[pos:pos+1] == b'\x08':
                            pos += 1  # End of all shortcuts
                        break
                    
                    pos += 1
                    
                    # Read key name
                    key_start = pos
                    while pos < len(data) and data[pos:pos+1] != b'\x00':
                        pos += 1
                    key_name = data[key_start:pos].decode('utf-8', errors='ignore')
                    pos += 1  # Skip null terminator
                    
                    if type_byte == b'\x01':  # String
                        val_start = pos
                        while pos < len(data) and data[pos:pos+1] != b'\x00':
                            pos += 1
                        shortcut[key_name] = data[val_start:pos].decode('utf-8', errors='ignore')
                        pos += 1
                    elif type_byte == b'\x02':  # Int32
                        shortcut[key_name] = struct.unpack('<I', data[pos:pos+4])[0]
                        pos += 4
                    elif type_byte == b'\x00':  # Nested (like tags)
                        # Skip nested structure for now, read until \x08
                        nested = {}
                        while pos < len(data) and data[pos:pos+1] != b'\x08':
                            if data[pos:pos+1] == b'\x01':  # String in nested
                                pos += 1
                                nkey_start = pos
                                while pos < len(data) and data[pos:pos+1] != b'\x00':
                                    pos += 1
                                nkey = data[nkey_start:pos].decode('utf-8', errors='ignore')
                                pos += 1
                                nval_start = pos
                                while pos < len(data) and data[pos:pos+1] != b'\x00':
                                    pos += 1
                                nested[nkey] = data[nval_start:pos].decode('utf-8', errors='ignore')
                                pos += 1
                            else:
                                pos += 1
                        shortcut[key_name] = nested
                        pos += 1  # Skip \x08
                
                shortcuts[idx_str] = shortcut
                debug_log(f"VDF: Parsed shortcut {idx_str}: {shortcut.get('AppName', 'unknown')}")
            
            debug_log(f"VDF: Found {len(shortcuts)} existing shortcuts")
        except Exception as e:
            debug_log(f"VDF parse error: {e}")
            return {}
        
        return shortcuts
    
    @staticmethod
    def write_shortcuts(shortcuts_dict):
        """Write shortcuts to shortcuts.vdf"""
        shortcuts_path = SteamShortcuts.get_shortcuts_path()
        if not shortcuts_path:
            return False

        shortcuts_path.parent.mkdir(parents=True, exist_ok=True)

        # Create backup before modifying
        if shortcuts_path.exists():
            backup_path = shortcuts_path.with_suffix('.vdf.backup')
            try:
                shutil.copy2(shortcuts_path, backup_path)
                debug_log(f"Created backup: {backup_path}")
            except OSError as e:
                debug_log(f"Warning: Could not create backup: {e}")
        
        # Build binary VDF
        data = b'\x00shortcuts\x00'
        
        for idx, (key, shortcut) in enumerate(shortcuts_dict.items()):
            data += b'\x00' + str(idx).encode() + b'\x00'
            
            # appid (stored as 32-bit, but can be signed or unsigned in dict)
            data += b'\x02appid\x00'
            appid = shortcut.get('appid', 0)
            # Convert to unsigned 32-bit representation
            if appid < 0:
                appid = appid & 0xFFFFFFFF
            data += struct.pack('<I', appid)
            
            # AppName (string)
            data += b'\x01AppName\x00'
            data += shortcut.get('AppName', '').encode('utf-8') + b'\x00'
            
            # Exe (string)
            data += b'\x01Exe\x00'
            data += shortcut.get('Exe', '').encode('utf-8') + b'\x00'
            
            # StartDir (string)
            data += b'\x01StartDir\x00'
            data += shortcut.get('StartDir', '').encode('utf-8') + b'\x00'
            
            # icon (string)
            data += b'\x01icon\x00'
            data += shortcut.get('icon', '').encode('utf-8') + b'\x00'
            
            # LaunchOptions (string)
            data += b'\x01LaunchOptions\x00'
            data += shortcut.get('LaunchOptions', '').encode('utf-8') + b'\x00'
            
            # IsHidden (int32)
            data += b'\x02IsHidden\x00'
            data += struct.pack('<I', shortcut.get('IsHidden', 0))
            
            # AllowDesktopConfig (int32)
            data += b'\x02AllowDesktopConfig\x00'
            data += struct.pack('<I', shortcut.get('AllowDesktopConfig', 1))
            
            # AllowOverlay (int32)
            data += b'\x02AllowOverlay\x00'
            data += struct.pack('<I', shortcut.get('AllowOverlay', 1))
            
            # OpenVR (int32)
            data += b'\x02OpenVR\x00'
            data += struct.pack('<I', 0)
            
            # Devkit (int32)
            data += b'\x02Devkit\x00'
            data += struct.pack('<I', 0)
            
            # DevkitGameID (string)
            data += b'\x01DevkitGameID\x00\x00'
            
            # DevkitOverrideAppID (int32)
            data += b'\x02DevkitOverrideAppID\x00'
            data += struct.pack('<I', 0)
            
            # LastPlayTime (int32)
            data += b'\x02LastPlayTime\x00'
            data += struct.pack('<I', shortcut.get('LastPlayTime', 0))
            
            # tags
            data += b'\x00tags\x00'
            tags = shortcut.get('tags', {})
            for tag_idx, tag in enumerate(tags.values() if isinstance(tags, dict) else tags):
                data += b'\x01' + str(tag_idx).encode() + b'\x00'
                data += tag.encode('utf-8') + b'\x00'
            data += b'\x08'
            
            data += b'\x08'
        
        data += b'\x08\x08'
        
        try:
            with open(shortcuts_path, 'wb') as f:
                f.write(data)
            return True
        except Exception as e:
            print(f"Error writing shortcuts: {e}")
            return False
    
    @staticmethod
    def add_shortcut(name, exe_path, start_dir, icon_path="", tags=None):
        """Add a non-Steam game shortcut
        
        Returns the unsigned app_id (for artwork filenames), not the signed shortcut_id
        """
        if tags is None:
            tags = ["PS1", "PlayStation"]
        
        exe_str = f'"{exe_path}"'
        shortcut_id = SteamShortcuts.generate_shortcut_id(exe_str, name)
        app_id = SteamShortcuts.generate_app_id(exe_str, name)  # Unsigned for artwork
        
        shortcuts = SteamShortcuts.read_shortcuts()
        
        # Check if already exists
        for key, shortcut in shortcuts.items():
            if shortcut.get('AppName') == name:
                return app_id  # Already exists, return app_id for artwork
        
        # Add new shortcut
        new_idx = len(shortcuts)
        shortcuts[str(new_idx)] = {
            'appid': shortcut_id,  # Signed ID goes in VDF
            'AppName': name,
            'Exe': exe_str,
            'StartDir': f'"{start_dir}"',
            'icon': icon_path,
            'LaunchOptions': '',
            'IsHidden': 0,
            'AllowDesktopConfig': 1,
            'AllowOverlay': 1,
            'LastPlayTime': 0,
            'tags': {str(i): tag for i, tag in enumerate(tags)},
        }
        
        if SteamShortcuts.write_shortcuts(shortcuts):
            return app_id  # Return unsigned app_id for artwork filenames
        return None

    @staticmethod
    def update_shortcut_icon(name, icon_path):
        """Update the icon field of an existing shortcut by name

        Returns True if shortcut was found and updated, False otherwise
        """
        shortcuts = SteamShortcuts.read_shortcuts()
        if not shortcuts:
            return False

        for key, shortcut in shortcuts.items():
            if shortcut.get('AppName') == name:
                shortcut['icon'] = str(icon_path)
                debug_log(f"Updated shortcut icon for {name}: {icon_path}")
                return SteamShortcuts.write_shortcuts(shortcuts)

        debug_log(f"Shortcut not found for icon update: {name}")
        return False

    @staticmethod
    def remove_shortcut(name=None, exe_path=None):
        """Remove a non-Steam game shortcut by name or exe path

        Returns True if shortcut was found and removed, False otherwise
        """
        if not name and not exe_path:
            return False

        shortcuts = SteamShortcuts.read_shortcuts()
        if not shortcuts:
            return False

        # Find and remove matching shortcut
        key_to_remove = None
        removed_shortcut = None
        for key, shortcut in shortcuts.items():
            if name and shortcut.get('AppName') == name:
                key_to_remove = key
                removed_shortcut = shortcut
                break
            if exe_path:
                exe_str = f'"{exe_path}"'
                if shortcut.get('Exe') == exe_str:
                    key_to_remove = key
                    removed_shortcut = shortcut
                    break

        if key_to_remove is None:
            debug_log(f"Shortcut not found: name={name}, exe={exe_path}")
            return False

        # Remove the shortcut
        del shortcuts[key_to_remove]
        debug_log(f"Removing shortcut: {removed_shortcut.get('AppName', 'unknown')}")

        # Reindex remaining shortcuts (Steam expects sequential indices)
        reindexed = {}
        for i, (_, shortcut) in enumerate(sorted(shortcuts.items(), key=lambda x: int(x[0]))):
            reindexed[str(i)] = shortcut

        # Remove artwork files
        if removed_shortcut:
            app_name = removed_shortcut.get('AppName', '')
            exe = removed_shortcut.get('Exe', '')
            if app_name and exe:
                app_id = SteamShortcuts.generate_app_id(exe, app_name)
                SteamShortcuts.remove_artwork(app_id)

        return SteamShortcuts.write_shortcuts(reindexed)

    @staticmethod
    def remove_artwork(app_id):
        """Remove all artwork files for a given app_id"""
        grid_path = SteamShortcuts.get_grid_path()
        if not grid_path:
            return

        # Steam artwork file patterns
        artwork_files = [
            f"{app_id}p.png",      # Portrait cover
            f"{app_id}.png",       # Horizontal grid
            f"{app_id}_hero.png",  # Hero banner
            f"{app_id}_logo.png",  # Logo
            f"{app_id}_icon.png",  # Icon (if exists)
        ]

        for filename in artwork_files:
            filepath = grid_path / filename
            if filepath.exists():
                try:
                    filepath.unlink()
                    debug_log(f"Removed artwork: {filename}")
                except OSError as e:
                    debug_log(f"Failed to remove artwork {filename}: {e}")

    @staticmethod
    def get_all_shortcuts():
        """Get list of all shortcuts with their details

        Returns list of dicts with 'name', 'exe', 'app_id' keys
        """
        shortcuts = SteamShortcuts.read_shortcuts()
        result = []
        for key, shortcut in shortcuts.items():
            name = shortcut.get('AppName', '')
            exe = shortcut.get('Exe', '')
            if name and exe:
                app_id = SteamShortcuts.generate_app_id(exe, name)
                result.append({
                    'name': name,
                    'exe': exe,
                    'app_id': app_id,
                    'tags': list(shortcut.get('tags', {}).values()) if isinstance(shortcut.get('tags'), dict) else []
                })
        return result

    @staticmethod
    def remove_shortcuts_by_tags(tags):
        """Remove all shortcuts that have any of the specified tags

        Returns number of shortcuts removed
        """
        shortcuts = SteamShortcuts.read_shortcuts()
        if not shortcuts:
            return 0

        tags_set = set(t.lower() for t in tags)
        keys_to_remove = []

        for key, shortcut in shortcuts.items():
            shortcut_tags = shortcut.get('tags', {})
            if isinstance(shortcut_tags, dict):
                shortcut_tags_lower = set(t.lower() for t in shortcut_tags.values())
            else:
                shortcut_tags_lower = set()

            if shortcut_tags_lower & tags_set:  # Intersection
                keys_to_remove.append(key)
                # Remove artwork
                name = shortcut.get('AppName', '')
                exe = shortcut.get('Exe', '')
                if name and exe:
                    app_id = SteamShortcuts.generate_app_id(exe, name)
                    SteamShortcuts.remove_artwork(app_id)
                debug_log(f"Marking for removal: {name}")

        if not keys_to_remove:
            return 0

        # Remove marked shortcuts
        for key in keys_to_remove:
            del shortcuts[key]

        # Reindex
        reindexed = {}
        for i, (_, shortcut) in enumerate(sorted(shortcuts.items(), key=lambda x: int(x[0]))):
            reindexed[str(i)] = shortcut

        if SteamShortcuts.write_shortcuts(reindexed):
            return len(keys_to_remove)
        return 0
    
    @staticmethod
    def get_grid_path():
        """Get path to Steam grid folder for artwork"""
        user_id = SteamShortcuts.find_user_id()
        if user_id and STEAM_USERDATA:
            grid_path = STEAM_USERDATA / user_id / "config" / "grid"
            grid_path.mkdir(parents=True, exist_ok=True)
            return grid_path
        return None
    
    @staticmethod
    def save_artwork(shortcut_id, image_path_or_url, exe_path, name):
        """Save artwork for a Steam shortcut
        
        Steam uses different IDs for different artwork:
        - {shortcut_id}p.png - Portrait/Cover (600x900)
        - {shortcut_id}_hero.png - Hero banner (1920x620)  
        - {shortcut_id}.png - Grid/Landscape (920x430)
        - {shortcut_id}_logo.png - Logo
        """
        grid_path = SteamShortcuts.get_grid_path()
        if not grid_path:
            return False
        
        try:
            # Download image if URL, otherwise read from file
            if image_path_or_url.startswith('http'):
                response = requests.get(image_path_or_url, timeout=30)
                if response.status_code != 200:
                    return False
                image_data = response.content
            else:
                with open(image_path_or_url, 'rb') as f:
                    image_data = f.read()
            
            # Save as portrait cover (main grid image in library)
            cover_path = grid_path / f"{shortcut_id}p.png"
            with open(cover_path, 'wb') as f:
                f.write(image_data)
            
            # Also save as regular grid (for horizontal views)
            grid_file = grid_path / f"{shortcut_id}.png"
            with open(grid_file, 'wb') as f:
                f.write(image_data)
            
            return True
        except Exception as e:
            print(f"Error saving artwork: {e}")
            return False


# Config file path
CONFIG_FILE = SCRIPT_DIR / "ps1-packager.conf"

class SteamGridDB:
    """Fetch artwork from SteamGridDB"""
    
    BASE_URL = "https://www.steamgriddb.com/api/v2"
    DEFAULT_API_KEY = "2afc4b8f27c1d75437a2dc00c6fe3d0a"
    
    @staticmethod
    def clean_game_name(game_name):
        """Clean up game name for better search results"""
        import re
        name = game_name.replace('_', ' ')
        # Remove content in brackets and parentheses first (often contains region info)
        name = re.sub(r'\s*\[.*?\]', '', name)  # [anything in brackets]
        name = re.sub(r'\s*\(.*?\)', '', name)  # (anything in parens)
        # Remove standalone region/version tags (word boundaries to avoid matching inside words)
        name = re.sub(r'\b(USA|Europe|Japan|World|JP|EU|US|PAL|NTSC)\b', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s+v?\d+\.\d+\s*$', '', name)  # v1.0, v1.1, etc at end
        name = re.sub(r'\b(Disc|Disk)\s*\d+\b', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s+', ' ', name).strip()  # Clean up whitespace
        return name
    
    @staticmethod
    def search_game(api_key, game_name):
        """Search for a game and return its SteamGridDB ID"""
        try:
            headers = {"Authorization": f"Bearer {api_key}"}
            # Clean up game name for search
            search_name = SteamGridDB.clean_game_name(game_name)
            url = f"{SteamGridDB.BASE_URL}/search/autocomplete/{requests.utils.quote(search_name)}"
            debug_log(f"SteamGridDB search: {search_name}")
            debug_log(f"SteamGridDB URL: {url}")
            response = requests.get(url, headers=headers, timeout=10)
            debug_log(f"SteamGridDB response code: {response.status_code}")
            debug_log(f"SteamGridDB response body: {response.text[:500]}")
            if response.status_code == 200:
                data = response.json()
                if data.get('success') and data.get('data'):
                    game_id = data['data'][0]['id']
                    debug_log(f"SteamGridDB found game ID: {game_id}")
                    return game_id
                else:
                    debug_log(f"SteamGridDB: No results in response")
            elif response.status_code == 401:
                debug_log("SteamGridDB: Invalid API key (401)")
            elif response.status_code == 404:
                debug_log("SteamGridDB: Not found (404)")
            else:
                debug_log(f"SteamGridDB: Unexpected status {response.status_code}")
        except Exception as e:
            debug_log(f"SteamGridDB search error: {e}")
        return None
    
    @staticmethod
    def get_grid(api_key, game_id):
        """Get grid/cover artwork URL (portrait preferred)"""
        try:
            headers = {"Authorization": f"Bearer {api_key}"}
            # Try portrait dimensions first (2:3 ratio for Steam library)
            for dimensions in ["600x900", "342x482", "460x215", None]:
                url_params = f"?dimensions={dimensions}" if dimensions else ""
                response = requests.get(
                    f"{SteamGridDB.BASE_URL}/grids/game/{game_id}{url_params}",
                    headers=headers,
                    timeout=10
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get('success') and data.get('data'):
                        url = data['data'][0]['url']
                        debug_log(f"SteamGridDB grid URL ({dimensions}): {url}")
                        return url
            debug_log(f"SteamGridDB grid: no data for game {game_id}")
        except Exception as e:
            debug_log(f"SteamGridDB grid error: {e}")
        return None
    
    @staticmethod
    def get_icon(api_key, game_id):
        """Get square icon artwork URL (512x512)"""
        try:
            headers = {"Authorization": f"Bearer {api_key}"}
            response = requests.get(
                f"{SteamGridDB.BASE_URL}/icons/game/{game_id}",
                headers=headers,
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                if data.get('success') and data.get('data'):
                    url = data['data'][0]['url']
                    debug_log(f"SteamGridDB icon URL: {url}")
                    return url
            debug_log(f"SteamGridDB icon: no data for game {game_id}")
        except Exception as e:
            debug_log(f"SteamGridDB icon error: {e}")
        return None
    
    @staticmethod
    def get_square_grid(api_key, game_id):
        """Get square grid artwork URL (512x512 or 1024x1024)"""
        try:
            headers = {"Authorization": f"Bearer {api_key}"}
            for dimensions in ["512x512", "1024x1024"]:
                response = requests.get(
                    f"{SteamGridDB.BASE_URL}/grids/game/{game_id}?dimensions={dimensions}",
                    headers=headers,
                    timeout=10
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get('success') and data.get('data'):
                        url = data['data'][0]['url']
                        debug_log(f"SteamGridDB square grid URL ({dimensions}): {url}")
                        return url
            debug_log(f"SteamGridDB square grid: no data for game {game_id}")
        except Exception as e:
            debug_log(f"SteamGridDB square grid error: {e}")
        return None
    
    @staticmethod
    def get_horizontal_grid(api_key, game_id):
        """Get horizontal grid artwork URL (920x430 or 460x215 for Recently Played)"""
        try:
            headers = {"Authorization": f"Bearer {api_key}"}
            # Try horizontal dimensions (Steam uses ~2:1 ratio for these)
            for dimensions in ["920x430", "460x215", None]:
                url_params = f"?dimensions={dimensions}" if dimensions else ""
                response = requests.get(
                    f"{SteamGridDB.BASE_URL}/grids/game/{game_id}{url_params}",
                    headers=headers,
                    timeout=10
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get('success') and data.get('data'):
                        url = data['data'][0]['url']
                        debug_log(f"SteamGridDB horizontal grid URL ({dimensions}): {url}")
                        return url
            debug_log(f"SteamGridDB horizontal grid: no data for game {game_id}")
        except Exception as e:
            debug_log(f"SteamGridDB horizontal grid error: {e}")
        return None
    
    @staticmethod
    def get_hero(api_key, game_id):
        """Get hero/banner artwork URL (1920x620)"""
        try:
            headers = {"Authorization": f"Bearer {api_key}"}
            response = requests.get(
                f"{SteamGridDB.BASE_URL}/heroes/game/{game_id}",
                headers=headers,
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                if data.get('success') and data.get('data'):
                    url = data['data'][0]['url']
                    debug_log(f"SteamGridDB hero URL: {url}")
                    return url
            debug_log(f"SteamGridDB hero: no data for game {game_id}")
        except Exception as e:
            debug_log(f"SteamGridDB hero error: {e}")
        return None
    
    @staticmethod
    def get_logo(api_key, game_id):
        """Get logo artwork URL"""
        try:
            headers = {"Authorization": f"Bearer {api_key}"}
            response = requests.get(
                f"{SteamGridDB.BASE_URL}/logos/game/{game_id}",
                headers=headers,
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                if data.get('success') and data.get('data'):
                    url = data['data'][0]['url']
                    debug_log(f"SteamGridDB logo URL: {url}")
                    return url
            debug_log(f"SteamGridDB logo: no data for game {game_id}")
        except Exception as e:
            debug_log(f"SteamGridDB logo error: {e}")
        return None
    
    @staticmethod
    def download_all_artwork(api_key, game_name, shortcut_id):
        """Download all artwork types for a game and save to Steam grid folder"""
        debug_log(f"download_all_artwork called: game={game_name}, shortcut_id={shortcut_id}")
        grid_path = SteamShortcuts.get_grid_path()
        if not grid_path:
            debug_log("download_all_artwork: No grid path found")
            return False
        debug_log(f"Grid path: {grid_path}")
        
        game_id = SteamGridDB.search_game(api_key, game_name)
        if not game_id:
            debug_log("download_all_artwork: Game not found in search")
            return False
        
        debug_log(f"Found game_id: {game_id}, downloading artwork...")
        success = False
        
        # Get and save grid/cover (portrait)
        grid_url = SteamGridDB.get_grid(api_key, game_id)
        if grid_url:
            try:
                response = requests.get(grid_url, timeout=30)
                if response.status_code == 200:
                    # Portrait cover
                    filepath = grid_path / f"{shortcut_id}p.png"
                    with open(filepath, 'wb') as f:
                        f.write(response.content)
                    debug_log(f"Saved portrait grid to: {filepath}")
                    success = True
            except Exception as e:
                debug_log(f"Error downloading grid: {e}")
        
        # Get and save horizontal grid (for Recently Played, etc)
        horiz_url = SteamGridDB.get_horizontal_grid(api_key, game_id)
        if horiz_url:
            try:
                response = requests.get(horiz_url, timeout=30)
                if response.status_code == 200:
                    filepath = grid_path / f"{shortcut_id}.png"
                    with open(filepath, 'wb') as f:
                        f.write(response.content)
                    debug_log(f"Saved horizontal grid to: {filepath}")
            except Exception as e:
                debug_log(f"Error downloading horizontal grid: {e}")
        
        # Get and save hero (banner)
        hero_url = SteamGridDB.get_hero(api_key, game_id)
        if hero_url:
            try:
                response = requests.get(hero_url, timeout=30)
                if response.status_code == 200:
                    filepath = grid_path / f"{shortcut_id}_hero.png"
                    with open(filepath, 'wb') as f:
                        f.write(response.content)
                    debug_log(f"Saved hero to: {filepath}")
            except Exception as e:
                debug_log(f"Error downloading hero: {e}")
        
        # Get and save logo
        logo_url = SteamGridDB.get_logo(api_key, game_id)
        if logo_url:
            try:
                response = requests.get(logo_url, timeout=30)
                if response.status_code == 200:
                    filepath = grid_path / f"{shortcut_id}_logo.png"
                    with open(filepath, 'wb') as f:
                        f.write(response.content)
                    debug_log(f"Saved logo to: {filepath}")
            except Exception as e:
                debug_log(f"Error downloading logo: {e}")

        # Get and save icon (for Big Picture guide button overlay)
        icon_path = None
        icon_url = SteamGridDB.get_icon(api_key, game_id)
        if icon_url:
            try:
                response = requests.get(icon_url, timeout=30)
                if response.status_code == 200:
                    icon_path = grid_path / f"{shortcut_id}_icon.png"
                    with open(icon_path, 'wb') as f:
                        f.write(response.content)
                    debug_log(f"Saved icon to: {icon_path}")
            except Exception as e:
                debug_log(f"Error downloading icon: {e}")

        debug_log(f"download_all_artwork complete, success={success}, icon_path={icon_path}")
        return success, icon_path


class RetroPackagerApp(Gtk.Window):
    def __init__(self):
        super().__init__(title="RetroPackager")

        # Current system (ps1 or gba)
        self.current_system = "ps1"

        # Get screen dimensions
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        if monitor:
            geometry = monitor.get_geometry()
            scale = monitor.get_scale_factor()
            screen_width = geometry.width
            screen_height = geometry.height

            # For handhelds (ROG Ally 1920x1080, Steam Deck 1280x800, etc.)
            # Maximize to fill the screen
            if screen_width <= 1920 and screen_height <= 1200:
                self.set_default_size(screen_width, screen_height)
                # Fullscreen for gaming mode
                self.fullscreen()
            else:
                self.set_default_size(1200, 800)
        else:
            self.set_default_size(1200, 800)

        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_resizable(True)

        # Connect to key press for fullscreen toggle
        self.connect('key-press-event', self._on_key_press)

        # Apply CSS
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # Initialize bubble animation
        self._init_bubbles()

        # Use overlay for bubble background
        overlay = Gtk.Overlay()
        self.add(overlay)

        # Bubble drawing area (background layer)
        self.bubble_canvas = Gtk.DrawingArea()
        self.bubble_canvas.connect('draw', self._draw_bubbles)
        overlay.add(self.bubble_canvas)

        # Main container (foreground layer)
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        overlay.add_overlay(self.main_box)

        # Start bubble animation
        GLib.timeout_add(50, self._animate_bubbles)  # 20 FPS
        
        # Stack for different views
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_transition_duration(200)
        
        # Build views
        self._build_main_menu()
        self._build_archive_browser()
        self._build_packaging_view()
        
        self.main_box.pack_start(self.stack, True, True, 0)
        
        # Status bar
        self.status_bar = Gtk.Label(label="Ready")
        self.status_bar.get_style_context().add_class('status-bar')
        self.status_bar.set_halign(Gtk.Align.START)
        self.status_bar.set_margin_start(16)
        self.status_bar.set_margin_end(16)
        self.status_bar.set_margin_bottom(8)
        self.main_box.pack_end(self.status_bar, False, False, 0)
        
        # Initialize
        self.selected_item = None
        self.current_game_dir = None
        
        # Gamepad support - map Steam Input to keyboard events
        # Steam translates gamepad to: A=Enter, B=Escape, DPad=Arrows, LB/RB=Tab
        # We just need to handle keyboard events properly
        self.connect('key-press-event', self._on_gamepad_key)
        
        # Ensure directories exist
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    
    def set_status(self, text):
        """Update status bar"""
        self.status_bar.set_text(text)

    def _init_bubbles(self):
        """Initialize bubble animation state"""
        import random
        self.bubbles = []
        # Create initial bubbles
        for _ in range(15):
            self.bubbles.append({
                'x': random.uniform(0, 1),      # Relative position 0-1
                'y': random.uniform(0, 1.2),    # Start some below screen
                'size': random.uniform(20, 80),
                'speed': random.uniform(0.001, 0.004),
                'wobble': random.uniform(0, 6.28),
                'wobble_speed': random.uniform(0.02, 0.05),
                'opacity': random.uniform(0.4, 0.7),
            })

    def _animate_bubbles(self):
        """Update bubble positions each frame"""
        import random
        for bubble in self.bubbles:
            # Float upward
            bubble['y'] -= bubble['speed']
            # Wobble side to side
            bubble['wobble'] += bubble['wobble_speed']

            # Respawn at bottom when off top
            if bubble['y'] < -0.1:
                bubble['y'] = 1.1
                bubble['x'] = random.uniform(0, 1)
                bubble['size'] = random.uniform(20, 80)
                bubble['speed'] = random.uniform(0.001, 0.004)
                bubble['opacity'] = random.uniform(0.4, 0.7)

        # Trigger redraw
        if hasattr(self, 'bubble_canvas'):
            self.bubble_canvas.queue_draw()
        return True  # Keep animation running

    def _draw_bubbles(self, widget, cr):
        """Draw bubbles on the canvas using Cairo"""
        import math
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()

        for bubble in self.bubbles:
            # Calculate actual position with wobble
            x = bubble['x'] * width + math.sin(bubble['wobble']) * 20
            y = bubble['y'] * height
            size = bubble['size']

            # Draw bubble with gradient
            # Outer glow
            pattern = cairo.RadialGradient(x, y, 0, x, y, size)
            pattern.add_color_stop_rgba(0, 0.8, 0.95, 1, bubble['opacity'] * 0.85)
            pattern.add_color_stop_rgba(0.7, 0.6, 0.9, 1, bubble['opacity'] * 0.6)
            pattern.add_color_stop_rgba(1, 0.5, 0.85, 1, 0)
            cr.set_source(pattern)
            cr.arc(x, y, size, 0, 2 * math.pi)
            cr.fill()

            # Inner highlight (glossy effect)
            highlight_x = x - size * 0.3
            highlight_y = y - size * 0.3
            highlight_size = size * 0.4
            pattern2 = cairo.RadialGradient(
                highlight_x, highlight_y, 0,
                highlight_x, highlight_y, highlight_size
            )
            pattern2.add_color_stop_rgba(0, 1, 1, 1, bubble['opacity'] * 1.0)
            pattern2.add_color_stop_rgba(1, 1, 1, 1, 0)
            cr.set_source(pattern2)
            cr.arc(highlight_x, highlight_y, highlight_size, 0, 2 * math.pi)
            cr.fill()

            # Edge ring
            cr.set_source_rgba(0.7, 0.92, 1, bubble['opacity'] * 0.6)
            cr.set_line_width(1.5)
            cr.arc(x, y, size - 2, 0, 2 * math.pi)
            cr.stroke()

    def _on_key_press(self, widget, event):
        """Handle key press events"""
        # F11 to toggle fullscreen
        if event.keyval == Gdk.KEY_F11:
            if self.get_window().get_state() & Gdk.WindowState.FULLSCREEN:
                self.unfullscreen()
            else:
                self.fullscreen()
            return True
        # Escape to exit fullscreen or go back
        elif event.keyval == Gdk.KEY_Escape:
            if self.get_window().get_state() & Gdk.WindowState.FULLSCREEN:
                self.unfullscreen()
                return True
            current = self.stack.get_visible_child_name()
            if current != "main":
                self.stack.set_visible_child_name("main")
                return True
        return False
    
    def _on_gamepad_key(self, widget, event):
        """Handle gamepad inputs (Steam translates gamepad to keyboard)
        
        Steam Input mapping:
        - A button = Enter/Return (activate/select)
        - B button = Escape (back)
        - D-Pad = Arrow keys (navigate)
        - LB/RB = Page Up/Down or Tab
        - Start = F1 or similar
        """
        current_view = self.stack.get_visible_child_name()
        
        # B button / Escape = Go back
        if event.keyval == Gdk.KEY_Escape:
            if current_view == "archive":
                self.stack.set_visible_child_name("main")
                # Focus first button in main menu
                GLib.idle_add(self._focus_main_menu)
                return True
            elif current_view == "packaging":
                self.stack.set_visible_child_name("main")
                GLib.idle_add(self._focus_main_menu)
                return True
        
        # A button / Enter = Activate
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            focused = self.get_focus()
            if focused:
                if isinstance(focused, Gtk.Button):
                    focused.emit('clicked')
                    return True
                elif isinstance(focused, Gtk.FlowBoxChild):
                    focused.emit('activate')
                    return True
            
            # If in archive and have selection, download
            if current_view == "archive" and hasattr(self, 'selected_item') and self.selected_item:
                self.on_download_selected(None)
                return True
        
        # D-Pad navigation
        if event.keyval in (Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Left, Gdk.KEY_Right):
            if current_view == "main":
                # Navigate between buttons
                return self._navigate_main_menu(event.keyval)
            elif current_view == "archive":
                # Let FlowBox handle navigation
                return False
        
        return False
    
    def _focus_main_menu(self):
        """Focus the first button in main menu"""
        if hasattr(self, 'main_menu_buttons') and self.main_menu_buttons:
            self.main_menu_buttons[0].grab_focus()
    
    def _navigate_main_menu(self, keyval):
        """Navigate between main menu buttons with D-pad"""
        if not hasattr(self, 'main_menu_buttons'):
            return False
        
        focused = self.get_focus()
        if focused in self.main_menu_buttons:
            idx = self.main_menu_buttons.index(focused)
            if keyval == Gdk.KEY_Down and idx < len(self.main_menu_buttons) - 1:
                self.main_menu_buttons[idx + 1].grab_focus()
                return True
            elif keyval == Gdk.KEY_Up and idx > 0:
                self.main_menu_buttons[idx - 1].grab_focus()
                return True
        else:
            # Nothing focused, focus first button
            self.main_menu_buttons[0].grab_focus()
            return True
        return False
    
    def _build_main_menu(self):
        """Build the main menu view"""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_start(24)
        box.set_margin_end(24)
        box.set_margin_top(20)
        box.set_margin_bottom(16)
        
        # Track buttons for gamepad navigation
        self.main_menu_buttons = []

        # Header row with title and exit button
        header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)

        # Title and subtitle on left
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.title_label = Gtk.Label(label="RetroPackager")
        self.title_label.get_style_context().add_class('title')
        self.title_label.set_halign(Gtk.Align.START)
        title_box.pack_start(self.title_label, False, False, 0)

        subtitle = Gtk.Label(label="Download • Package • Play")
        subtitle.get_style_context().add_class('subtitle')
        subtitle.set_halign(Gtk.Align.START)
        title_box.pack_start(subtitle, False, False, 0)

        header_row.pack_start(title_box, True, True, 0)

        # Exit button on right - glossy red Aero style
        exit_btn = Gtk.Button(label="✕ EXIT")
        exit_btn.get_style_context().add_class('exit-button')
        exit_btn.connect('clicked', lambda w: Gtk.main_quit())
        exit_btn.set_valign(Gtk.Align.START)
        header_row.pack_end(exit_btn, False, False, 0)

        box.pack_start(header_row, False, False, 0)
        
        # System toggle buttons
        system_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        system_box.set_margin_top(8)
        system_box.set_margin_bottom(8)
        
        self.ps1_toggle = Gtk.ToggleButton(label="🎮 PlayStation")
        self.ps1_toggle.set_active(True)
        self.ps1_toggle.get_style_context().add_class('system-button')
        self.ps1_toggle.connect('toggled', self._on_system_toggled, "ps1")
        self.ps1_toggle.set_can_focus(True)
        system_box.pack_start(self.ps1_toggle, False, False, 0)
        
        self.gba_toggle = Gtk.ToggleButton(label="🕹️ Game Boy Advance")
        self.gba_toggle.set_active(False)
        self.gba_toggle.get_style_context().add_class('system-button')
        self.gba_toggle.connect('toggled', self._on_system_toggled, "gba")
        self.gba_toggle.set_can_focus(True)
        system_box.pack_start(self.gba_toggle, False, False, 0)

        self.n64_toggle = Gtk.ToggleButton(label="🎲 Nintendo 64")
        self.n64_toggle.set_active(False)
        self.n64_toggle.get_style_context().add_class('system-button')
        self.n64_toggle.connect('toggled', self._on_system_toggled, "n64")
        self.n64_toggle.set_can_focus(True)
        system_box.pack_start(self.n64_toggle, False, False, 0)

        box.pack_start(system_box, False, False, 0)
        
        # BIOS/System status
        self.bios_label = Gtk.Label()
        self._update_system_status()
        self.bios_label.set_halign(Gtk.Align.START)
        box.pack_start(self.bios_label, False, False, 0)
        
        # Menu buttons grid - expands to fill space
        grid = Gtk.Grid()
        grid.set_column_spacing(16)
        grid.set_row_spacing(16)
        grid.set_margin_top(16)
        grid.set_column_homogeneous(True)
        grid.set_row_homogeneous(True)
        grid.set_hexpand(True)
        grid.set_vexpand(True)
        
        # Archive.org Browser
        self.archive_btn = self._create_menu_button(
            "🌐", "Archive.org", "Browse and download games",
            self.on_browse_archive
        )
        grid.attach(self.archive_btn, 0, 0, 1, 1)
        self.main_menu_buttons.append(self.archive_btn)
        
        # Package Local ROMs
        package_btn = self._create_menu_button(
            "📦", "Package Local ROM", "Package a ROM file you already have",
            self.on_package_local
        )
        grid.attach(package_btn, 1, 0, 1, 1)
        self.main_menu_buttons.append(package_btn)
        
        # View Games
        self.games_btn = self._create_menu_button(
            "🎮", "My Games", "View installed games",
            self.on_view_games
        )
        grid.attach(self.games_btn, 0, 1, 1, 1)
        self.main_menu_buttons.append(self.games_btn)
        
        # Settings
        settings_btn = self._create_menu_button(
            "⚙️", "Settings", "Configure BIOS and paths",
            self.on_settings
        )
        grid.attach(settings_btn, 1, 1, 1, 1)
        self.main_menu_buttons.append(settings_btn)
        
        box.pack_start(grid, True, True, 0)
        
        # Info box at bottom
        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        info_box.get_style_context().add_class('card')
        
        self.info_label = Gtk.Label(
            label="Place your BIOS next to this app • Browse Archive.org or select a local ROM"
        )
        self.info_label.set_halign(Gtk.Align.CENTER)
        self.info_label.set_line_wrap(True)
        self.info_label.get_style_context().add_class('subtitle')
        info_box.pack_start(self.info_label, False, False, 0)
        
        box.pack_start(info_box, False, False, 0)
        
        self.stack.add_named(box, "main")
    
    def _on_system_toggled(self, button, system):
        """Handle system toggle button"""
        if button.get_active():
            self.current_system = system
            # Update other toggles - deactivate all except the selected one
            if system != "ps1":
                self.ps1_toggle.set_active(False)
            if system != "gba":
                self.gba_toggle.set_active(False)
            if system != "n64":
                self.n64_toggle.set_active(False)

            # Update UI
            self._update_system_status()

            # Clear search results when switching systems
            if hasattr(self, 'results_flow'):
                for child in self.results_flow.get_children():
                    self.results_flow.remove(child)
            if hasattr(self, 'search_entry'):
                self.search_entry.set_text("")
            if hasattr(self, 'selected_item'):
                self.selected_item = None
            if hasattr(self, 'download_btn'):
                self.download_btn.set_sensitive(False)

            # Update status
            system_name = SYSTEMS[system]["name"]
            self.set_status(f"Switched to {system_name}")
    
    def _update_system_status(self):
        """Update the BIOS/system status label"""
        system_config = SYSTEMS[self.current_system]
        if not system_config["needs_bios"]:
            # Systems without BIOS requirement (GBA, N64)
            emulator_name = system_config["emulator_name"].replace(".AppImage", "")
            self.bios_label.set_text(f"✓ No BIOS required ({emulator_name})")
            self.bios_label.get_style_context().remove_class('warning-text')
            self.bios_label.get_style_context().add_class('success-text')
            if hasattr(self, 'info_label'):
                self.info_label.set_text("Browse Archive.org or select a local ROM • Play from Gaming Mode!")
        else:
            # Systems requiring BIOS (PS1)
            bios_status = self._get_bios_status()
            self.bios_label.set_text(f"BIOS: {bios_status}")
            if "✓" in bios_status:
                self.bios_label.get_style_context().remove_class('warning-text')
                self.bios_label.get_style_context().add_class('success-text')
            else:
                self.bios_label.get_style_context().remove_class('success-text')
                self.bios_label.get_style_context().add_class('warning-text')
            if hasattr(self, 'info_label'):
                self.info_label.set_text("Place your BIOS next to this app • Browse Archive.org or select a local ROM")
    
    def get_output_dir(self):
        """Get output directory for current system"""
        return SYSTEMS[self.current_system]["output_dir"]
    
    def _create_menu_button(self, icon, title, subtitle, callback):
        """Create a styled menu button that expands to fill grid cell"""
        btn = Gtk.Button()
        btn.get_style_context().add_class('menu-button')
        btn.set_hexpand(True)
        btn.set_vexpand(True)
        btn.set_can_focus(True)  # Ensure button can receive focus
        
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        
        icon_label = Gtk.Label(label=icon)
        icon_label.set_markup(f'<span size="xx-large">{icon}</span>')
        box.pack_start(icon_label, False, False, 0)
        
        title_label = Gtk.Label(label=title)
        title_label.get_style_context().add_class('menu-button-title')
        box.pack_start(title_label, False, False, 0)
        
        sub_label = Gtk.Label(label=subtitle)
        sub_label.get_style_context().add_class('menu-button-subtitle')
        sub_label.set_line_wrap(True)
        sub_label.set_max_width_chars(25)
        sub_label.set_justify(Gtk.Justification.CENTER)
        box.pack_start(sub_label, False, False, 0)
        
        btn.add(box)
        btn.connect('clicked', lambda w: callback())
        
        return btn
    
    def _build_archive_browser(self):
        """Build the Archive.org browser view"""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_start(20)
        box.set_margin_end(20)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        
        # Header with back button and download button
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        
        back_btn = Gtk.Button(label="← Back")
        back_btn.get_style_context().add_class('flat-button')
        back_btn.connect('clicked', lambda w: self.stack.set_visible_child_name("main"))
        header.pack_start(back_btn, False, False, 0)
        
        title = Gtk.Label(label="Archive.org Browser")
        title.get_style_context().add_class('title')
        header.pack_start(title, False, False, 0)
        
        # Download button in header (right side)
        self.download_btn = Gtk.Button(label="Download & Install")
        self.download_btn.get_style_context().add_class('accent-button')
        self.download_btn.set_sensitive(False)
        self.download_btn.connect('clicked', self.on_download_selected)
        header.pack_end(self.download_btn, False, False, 0)
        
        box.pack_start(header, False, False, 0)
        
        # Combined search toolbar
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.get_style_context().add_class('card')
        
        # Region filter
        self.language_combo = Gtk.ComboBoxText()
        self.language_combo.append("all", "All Regions")
        self.language_combo.append("usa", "USA")
        self.language_combo.append("europe", "Europe")
        self.language_combo.append("japan", "Japan")
        self.language_combo.set_active(0)
        self.language_combo.get_style_context().add_class('entry')
        toolbar.pack_start(self.language_combo, False, False, 0)
        
        # Genre filter
        self.genre_combo = Gtk.ComboBoxText()
        self.genre_combo.append("all", "All Genres")
        self.genre_combo.append("rpg", "RPG")
        self.genre_combo.append("action", "Action")
        self.genre_combo.append("adventure", "Adventure")
        self.genre_combo.append("platformer", "Platformer")
        self.genre_combo.append("racing", "Racing")
        self.genre_combo.append("fighting", "Fighting")
        self.genre_combo.append("sports", "Sports")
        self.genre_combo.append("puzzle", "Puzzle")
        self.genre_combo.append("shooter", "Shooter")
        self.genre_combo.append("horror", "Horror")
        self.genre_combo.append("strategy", "Strategy")
        self.genre_combo.append("simulation", "Simulation")
        self.genre_combo.set_active(0)
        self.genre_combo.get_style_context().add_class('entry')
        toolbar.pack_start(self.genre_combo, False, False, 0)
        
        # Search entry (expands to fill)
        self.search_entry = Gtk.Entry()
        self.search_entry.set_placeholder_text("Search games...")
        self.search_entry.get_style_context().add_class('entry')
        self.search_entry.connect('activate', self.on_search)
        toolbar.pack_start(self.search_entry, True, True, 0)
        
        # Buttons
        search_btn = Gtk.Button(label="Search")
        search_btn.get_style_context().add_class('accent-button')
        search_btn.connect('clicked', self.on_search)
        toolbar.pack_start(search_btn, False, False, 0)
        
        top_btn = Gtk.Button(label="⭐ Top Picks")
        top_btn.get_style_context().add_class('flat-button')
        top_btn.connect('clicked', self.on_browse_top_picks)
        toolbar.pack_start(top_btn, False, False, 0)
        
        recent_btn = Gtk.Button(label="Recent")
        recent_btn.get_style_context().add_class('flat-button')
        recent_btn.connect('clicked', self.on_browse_recent)
        toolbar.pack_start(recent_btn, False, False, 0)
        
        box.pack_start(toolbar, False, False, 0)
        
        # Results grid with cover art
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        
        self.results_flow = Gtk.FlowBox()
        self.results_flow.set_valign(Gtk.Align.START)
        self.results_flow.set_max_children_per_line(20)
        self.results_flow.set_min_children_per_line(1)
        self.results_flow.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.results_flow.set_homogeneous(False)
        self.results_flow.set_column_spacing(10)
        self.results_flow.set_row_spacing(10)
        self.results_flow.connect('child-activated', self.on_item_selected_flow)
        scroll.add(self.results_flow)
        
        box.pack_start(scroll, True, True, 0)
        
        self.stack.add_named(box, "archive")
    
    def _build_packaging_view(self):
        """Build the dynamic packaging progress view - two column layout"""
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main_box.set_margin_start(20)
        main_box.set_margin_end(20)
        main_box.set_margin_top(20)
        main_box.set_margin_bottom(20)
        
        # Two column container
        columns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        columns.set_vexpand(True)
        
        # LEFT COLUMN: Cover art, Title, Log
        left_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        left_col.set_hexpand(True)
        
        # Top row: cover art + title
        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        
        # Cover art
        self.packaging_cover = Gtk.Image()
        self.packaging_cover.set_size_request(120, 120)
        self.packaging_cover.set_from_icon_name("application-x-executable", Gtk.IconSize.DIALOG)
        top_row.pack_start(self.packaging_cover, False, False, 0)
        
        # Title
        self.packaging_title = Gtk.Label(label="Installing Game...")
        self.packaging_title.get_style_context().add_class('packaging-title')
        self.packaging_title.set_halign(Gtk.Align.START)
        self.packaging_title.set_valign(Gtk.Align.CENTER)
        self.packaging_title.set_line_wrap(True)
        top_row.pack_start(self.packaging_title, True, True, 0)
        
        left_col.pack_start(top_row, False, False, 0)
        
        # Log output (fills remaining space)
        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        log_scroll.set_vexpand(True)
        
        self.log_buffer = Gtk.TextBuffer()
        self.log_view = Gtk.TextView(buffer=self.log_buffer)
        self.log_view.set_editable(False)
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.log_view.get_style_context().add_class('log-view')
        log_scroll.add(self.log_view)
        
        left_col.pack_start(log_scroll, True, True, 0)
        
        columns.pack_start(left_col, True, True, 0)
        
        # RIGHT COLUMN: Steps, Buttons
        right_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        right_col.set_size_request(180, -1)
        
        # Steps list
        steps_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        steps_frame.get_style_context().add_class('card')
        
        self.step_labels = {}
        steps = [
            ("download", "⬇️ Download"),
            ("duckstation", "🎮 DuckStation"),
            ("copy_rom", "📀 Copy ROM"),
            ("copy_bios", "💾 Copy BIOS"),
            ("config", "⚙️ Configure"),
            ("steam", "🚀 Add to Steam"),
            ("done", "✅ Done!")
        ]
        
        for step_id, step_text in steps:
            step_label = Gtk.Label(label=step_text)
            step_label.set_halign(Gtk.Align.START)
            step_label.get_style_context().add_class('packaging-step')
            step_label.get_style_context().add_class('packaging-step-pending')
            steps_frame.pack_start(step_label, False, False, 0)
            self.step_labels[step_id] = step_label
        
        right_col.pack_start(steps_frame, False, False, 0)
        
        # Action buttons
        button_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        
        self.packaging_play_btn = Gtk.Button(label="🎮 Launch Game")
        self.packaging_play_btn.get_style_context().add_class('accent-button')
        self.packaging_play_btn.connect('clicked', self.on_launch_game)
        self.packaging_play_btn.set_sensitive(False)
        button_box.pack_start(self.packaging_play_btn, False, False, 0)
        
        self.packaging_done_btn = Gtk.Button(label="Done")
        self.packaging_done_btn.get_style_context().add_class('flat-button')
        self.packaging_done_btn.connect('clicked', lambda w: self.stack.set_visible_child_name("main"))
        self.packaging_done_btn.set_sensitive(False)
        button_box.pack_start(self.packaging_done_btn, False, False, 0)
        
        right_col.pack_end(button_box, False, False, 0)
        
        columns.pack_start(right_col, False, False, 0)
        
        main_box.pack_start(columns, True, True, 0)
        
        # BOTTOM: Progress bar (fixed to bottom)
        self.packaging_progress = Gtk.ProgressBar()
        self.packaging_progress.set_show_text(True)
        self.packaging_progress.set_size_request(-1, 32)
        self.packaging_progress.get_style_context().add_class('progress')
        self.packaging_progress.set_margin_top(12)
        main_box.pack_end(self.packaging_progress, False, False, 0)
        
        self.stack.add_named(main_box, "packaging")
    
    def on_package_local(self):
        """Package a local ROM file"""
        bios_path = self._get_bios_path()
        if not bios_path:
            self.show_message("BIOS Required", 
                "Please place a PS1 BIOS file (.bin, 512KB) in the same folder as this app.")
            return
        
        dialog = Gtk.FileChooserDialog(
            title="Select PS1 ROM",
            parent=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK
        )
        
        filter_rom = Gtk.FileFilter()
        filter_rom.set_name("PS1 ROMs")
        filter_rom.add_pattern("*.chd")
        filter_rom.add_pattern("*.cue")
        filter_rom.add_pattern("*.iso")
        filter_rom.add_pattern("*.bin")
        filter_rom.add_pattern("*.pbp")
        dialog.add_filter(filter_rom)
        
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            rom_path = Path(dialog.get_filename())
            dialog.destroy()
            self._package_local_rom(rom_path, bios_path)
        else:
            dialog.destroy()
    
    def _package_local_rom(self, rom_path, bios_path):
        """Package a local ROM file"""
        import re
        game_name = rom_path.stem
        game_name = re.sub(r'\s*\([^)]*\)', '', game_name)
        game_name = re.sub(r'\s*\[[^\]]*\]', '', game_name)
        game_name = game_name.strip()

        # Ask for name - fullscreen themed dialog
        name_dialog = Gtk.Dialog(title="Game Name", parent=self, flags=Gtk.DialogFlags.MODAL)
        name_dialog.fullscreen()

        main_box = name_dialog.get_content_area()
        main_box.set_valign(Gtk.Align.CENTER)
        main_box.set_halign(Gtk.Align.CENTER)

        # Content card
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.get_style_context().add_class('dialog-content')
        content.set_size_request(500, -1)

        # Title
        title_label = Gtk.Label(label="Enter Game Name")
        title_label.get_style_context().add_class('dialog-title')
        title_label.set_halign(Gtk.Align.START)
        content.pack_start(title_label, False, False, 0)

        # Subtitle
        sub_label = Gtk.Label(label=f"File: {rom_path.name}")
        sub_label.get_style_context().add_class('dialog-secondary')
        sub_label.set_halign(Gtk.Align.START)
        sub_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        content.pack_start(sub_label, False, False, 0)

        # Entry
        name_entry = Gtk.Entry()
        name_entry.set_text(game_name)
        name_entry.get_style_context().add_class('entry')
        content.pack_start(name_entry, False, False, 0)

        # Buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btn_box.set_halign(Gtk.Align.END)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.get_style_context().add_class('flat-button')
        cancel_btn.connect('clicked', lambda w: name_dialog.response(Gtk.ResponseType.CANCEL))
        btn_box.pack_start(cancel_btn, False, False, 0)

        install_btn = Gtk.Button(label="Install")
        install_btn.get_style_context().add_class('accent-button')
        install_btn.connect('clicked', lambda w: name_dialog.response(Gtk.ResponseType.OK))
        btn_box.pack_start(install_btn, False, False, 0)

        content.pack_start(btn_box, False, False, 0)

        main_box.pack_start(content, False, False, 0)
        name_dialog.show_all()
        response = name_dialog.run()

        if response == Gtk.ResponseType.OK:
            game_name = name_entry.get_text().strip()
            name_dialog.destroy()
            if game_name:
                self._start_local_packaging(rom_path, bios_path, game_name)
        else:
            name_dialog.destroy()
    
    def on_view_games(self):
        """Show installed games with uninstall option - fullscreen dialog"""
        dialog = Gtk.Dialog(
            title="Installed Games",
            parent=self,
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT
        )
        dialog.fullscreen()

        main_box = dialog.get_content_area()
        main_box.set_spacing(0)

        # Header bar with back button
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.set_margin_start(20)
        header.set_margin_end(20)
        header.set_margin_top(20)
        header.set_margin_bottom(10)

        back_btn = Gtk.Button(label="← Back")
        back_btn.get_style_context().add_class('flat-button')
        back_btn.connect('clicked', lambda w: dialog.destroy())
        header.pack_start(back_btn, False, False, 0)

        title = Gtk.Label(label="Installed Games")
        title.get_style_context().add_class('dialog-title')
        header.pack_start(title, True, True, 0)

        open_folder_btn = Gtk.Button(label="📁 Open Folder")
        open_folder_btn.get_style_context().add_class('flat-button')
        open_folder_btn.connect('clicked', lambda w: subprocess.Popen(['xdg-open', str(Path.home() / "Games")]))
        header.pack_end(open_folder_btn, False, False, 0)

        main_box.pack_start(header, False, False, 0)

        # Scrollable content area
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_start(20)
        content.set_margin_end(20)
        content.set_margin_top(10)
        content.set_margin_bottom(20)

        # Game list
        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)

        # Find installed games from PS1, GBA, and N64 directories
        games_found = False
        all_games = []

        for system_name, system_dir in [("PS1", OUTPUT_DIR_PS1), ("GBA", OUTPUT_DIR_GBA), ("N64", OUTPUT_DIR_N64)]:
            if system_dir.exists():
                for game_dir in system_dir.iterdir():
                    if game_dir.is_dir() and (game_dir / "launch.sh").exists():
                        all_games.append((system_name, game_dir))

        # Sort all games by name
        all_games.sort(key=lambda x: x[1].name.lower())

        for system_name, game_dir in all_games:
            games_found = True
            row = Gtk.ListBoxRow()

            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            box.set_margin_start(12)
            box.set_margin_end(12)
            box.set_margin_top(10)
            box.set_margin_bottom(10)

            # System badge
            system_label = Gtk.Label(label=system_name)
            system_label.get_style_context().add_class('subtitle')
            system_label.set_size_request(40, -1)
            box.pack_start(system_label, False, False, 0)

            # Game name
            name_label = Gtk.Label(label=game_dir.name)
            name_label.set_halign(Gtk.Align.START)
            name_label.get_style_context().add_class('menu-button-title')
            box.pack_start(name_label, True, True, 0)

            # Calculate size
            try:
                size = sum(f.stat().st_size for f in game_dir.rglob('*') if f.is_file())
                size_mb = size / (1024 * 1024)
                size_label = Gtk.Label(label=f"{size_mb:.0f} MB")
                size_label.get_style_context().add_class('subtitle')
                box.pack_start(size_label, False, False, 0)
            except OSError:
                pass

            # Uninstall button
            uninstall_btn = Gtk.Button(label="🗑️ Uninstall")
            uninstall_btn.get_style_context().add_class('flat-button')
            uninstall_btn.connect('clicked', lambda w, gd=game_dir, d=dialog: self._uninstall_game(gd, d))
            box.pack_end(uninstall_btn, False, False, 0)

            row.add(box)
            listbox.add(row)

        if not games_found:
            empty_label = Gtk.Label(label="No games installed yet.\nUse Archive.org or Package Local ROM to add games.")
            empty_label.set_justify(Gtk.Justification.CENTER)
            empty_label.get_style_context().add_class('dialog-message')
            content.pack_start(empty_label, True, True, 0)
        else:
            content.pack_start(listbox, True, True, 0)

        scroll.add(content)
        main_box.pack_start(scroll, True, True, 0)

        dialog.show_all()
        dialog.run()
        dialog.destroy()
    
    def _uninstall_game(self, game_dir, parent_dialog):
        """Uninstall a game and remove from Steam"""
        # Get game name (directory name, but with underscores converted back to spaces for display)
        game_name = game_dir.name.replace('_', ' ')

        if not self.show_confirm(
            f"Uninstall {game_name}?",
            "This will delete the game files and remove it from Steam.",
            warning=True
        ):
            return

        try:
            # First, try to remove from Steam shortcuts
            # Try both the display name and directory name variants
            launch_path = game_dir / "launch.sh"
            steam_removed = False

            if launch_path.exists():
                steam_removed = SteamShortcuts.remove_shortcut(exe_path=str(launch_path))

            # Also try by name (with underscores as stored, and with spaces)
            if not steam_removed:
                steam_removed = SteamShortcuts.remove_shortcut(name=game_dir.name)
            if not steam_removed:
                steam_removed = SteamShortcuts.remove_shortcut(name=game_name)

            # Delete game files
            shutil.rmtree(game_dir)

            if steam_removed:
                self.set_status(f"Uninstalled: {game_name} (removed from Steam)")
            else:
                self.set_status(f"Uninstalled: {game_name} (Steam shortcut not found)")

            # Refresh the dialog
            parent_dialog.destroy()
            self.on_view_games()
        except OSError as e:
            self.show_message("Error", f"Failed to uninstall: {e}")
    
    def on_settings(self):
        """Open settings dialog - fullscreen for handheld devices"""
        dialog = Gtk.Dialog(
            title="Settings",
            parent=self,
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT
        )
        dialog.fullscreen()

        # Main container
        main_box = dialog.get_content_area()
        main_box.set_spacing(0)

        # Header bar with back button
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.set_margin_start(20)
        header.set_margin_end(20)
        header.set_margin_top(20)
        header.set_margin_bottom(10)

        back_btn = Gtk.Button(label="← Back")
        back_btn.get_style_context().add_class('flat-button')
        back_btn.connect('clicked', lambda w: dialog.destroy())
        header.pack_start(back_btn, False, False, 0)

        title = Gtk.Label(label="Settings")
        title.get_style_context().add_class('menu-button-title')
        header.pack_start(title, True, True, 0)

        # Spacer to balance the back button
        spacer = Gtk.Label(label="")
        spacer.set_size_request(80, -1)
        header.pack_end(spacer, False, False, 0)

        main_box.pack_start(header, False, False, 0)

        # Scrollable content area
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_start(40)
        content.set_margin_end(40)
        content.set_margin_top(20)
        content.set_margin_bottom(40)

        # BIOS Section
        bios_label = Gtk.Label(label="PS1 BIOS File")
        bios_label.set_halign(Gtk.Align.START)
        bios_label.get_style_context().add_class('menu-button-title')
        content.pack_start(bios_label, False, False, 0)

        self.bios_path_label = Gtk.Label(label=self._get_bios_status())
        self.bios_path_label.set_halign(Gtk.Align.START)
        self.bios_path_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        content.pack_start(self.bios_path_label, False, False, 0)

        bios_btn = Gtk.Button(label="Select BIOS")
        bios_btn.get_style_context().add_class('accent-button')
        bios_btn.connect('clicked', self.on_select_bios)
        content.pack_start(bios_btn, False, False, 8)

        # Output directories
        separator1 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        content.pack_start(separator1, False, False, 12)

        out_label = Gtk.Label(label="Games Directories")
        out_label.set_halign(Gtk.Align.START)
        out_label.get_style_context().add_class('menu-button-title')
        content.pack_start(out_label, False, False, 0)

        out_ps1_label = Gtk.Label(label=f"PS1: {OUTPUT_DIR_PS1}")
        out_ps1_label.set_halign(Gtk.Align.START)
        out_ps1_label.set_selectable(True)
        out_ps1_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        content.pack_start(out_ps1_label, False, False, 0)

        out_gba_label = Gtk.Label(label=f"GBA: {OUTPUT_DIR_GBA}")
        out_gba_label.set_halign(Gtk.Align.START)
        out_gba_label.set_selectable(True)
        out_gba_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        content.pack_start(out_gba_label, False, False, 0)

        # Steam status
        separator2 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        content.pack_start(separator2, False, False, 12)

        steam_label = Gtk.Label(label="Steam Integration")
        steam_label.set_halign(Gtk.Align.START)
        steam_label.get_style_context().add_class('menu-button-title')
        content.pack_start(steam_label, False, False, 0)

        steam_user = SteamShortcuts.find_user_id()
        steam_status = f"✓ Found Steam user: {steam_user}" if steam_user else "⚠ Steam user not found"
        steam_status_label = Gtk.Label(label=steam_status)
        steam_status_label.set_halign(Gtk.Align.START)
        content.pack_start(steam_status_label, False, False, 0)

        # SteamGridDB API Key
        separator3 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        content.pack_start(separator3, False, False, 12)

        sgdb_label = Gtk.Label(label="SteamGridDB API Key")
        sgdb_label.set_halign(Gtk.Align.START)
        sgdb_label.get_style_context().add_class('menu-button-title')
        content.pack_start(sgdb_label, False, False, 0)

        sgdb_hint = Gtk.Label(label="For high-quality cover artwork (get free key at steamgriddb.com)")
        sgdb_hint.set_halign(Gtk.Align.START)
        sgdb_hint.get_style_context().add_class('subtitle')
        content.pack_start(sgdb_hint, False, False, 0)

        self.sgdb_entry = Gtk.Entry()
        self.sgdb_entry.set_placeholder_text("Paste API key here")
        self.sgdb_entry.set_text(self._load_sgdb_key())
        self.sgdb_entry.set_visibility(False)
        content.pack_start(self.sgdb_entry, False, False, 4)

        sgdb_save_btn = Gtk.Button(label="Save API Key")
        sgdb_save_btn.get_style_context().add_class('accent-button')
        sgdb_save_btn.connect('clicked', lambda w: self._save_sgdb_key(self.sgdb_entry.get_text()))
        content.pack_start(sgdb_save_btn, False, False, 8)

        # Add to Steam section
        separator4 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        content.pack_start(separator4, False, False, 12)

        steam_add_label = Gtk.Label(label="Add RetroPackager to Steam")
        steam_add_label.set_halign(Gtk.Align.START)
        steam_add_label.get_style_context().add_class('menu-button-title')
        content.pack_start(steam_add_label, False, False, 0)

        steam_add_hint = Gtk.Label(label="Launch from Gaming Mode with Frutiger Aero artwork")
        steam_add_hint.set_halign(Gtk.Align.START)
        steam_add_hint.get_style_context().add_class('subtitle')
        content.pack_start(steam_add_hint, False, False, 0)

        steam_add_btn = Gtk.Button(label="⭐ Add to Steam Library")
        steam_add_btn.get_style_context().add_class('accent-button')
        steam_add_btn.connect('clicked', lambda w: self._add_self_to_steam(dialog))
        content.pack_start(steam_add_btn, False, False, 8)

        # Manage Steam Shortcuts section
        separator5 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        content.pack_start(separator5, False, False, 12)

        manage_label = Gtk.Label(label="Manage Steam Shortcuts")
        manage_label.set_halign(Gtk.Align.START)
        manage_label.get_style_context().add_class('menu-button-title')
        content.pack_start(manage_label, False, False, 0)

        manage_hint = Gtk.Label(label="View or remove game shortcuts from Steam library")
        manage_hint.set_halign(Gtk.Align.START)
        manage_hint.get_style_context().add_class('subtitle')
        content.pack_start(manage_hint, False, False, 0)

        view_shortcuts_btn = Gtk.Button(label="📋 View All Shortcuts")
        view_shortcuts_btn.get_style_context().add_class('flat-button')
        view_shortcuts_btn.connect('clicked', lambda w: self._show_steam_shortcuts_dialog(dialog))
        content.pack_start(view_shortcuts_btn, False, False, 4)

        remove_all_btn = Gtk.Button(label="🗑️ Remove All Game Shortcuts")
        remove_all_btn.get_style_context().add_class('flat-button')
        remove_all_btn.connect('clicked', lambda w: self._remove_all_game_shortcuts(dialog))
        content.pack_start(remove_all_btn, False, False, 4)

        scroll.add(content)
        main_box.pack_start(scroll, True, True, 0)

        dialog.show_all()
        dialog.run()
        dialog.destroy()
    
    def _load_sgdb_key(self):
        """Load SteamGridDB API key from config"""
        try:
            if CONFIG_FILE.exists():
                config = {}
                for line in CONFIG_FILE.read_text().strip().split('\n'):
                    if '=' in line:
                        key, value = line.split('=', 1)
                        config[key.strip()] = value.strip()
                return config.get('sgdb_api_key', '')
        except (OSError, ValueError) as e:
            debug_log(f"Error loading SGDB key: {e}")
        return ''
    
    def _save_sgdb_key(self, api_key):
        """Save SteamGridDB API key to config"""
        try:
            config = {}
            if CONFIG_FILE.exists():
                for line in CONFIG_FILE.read_text().strip().split('\n'):
                    if '=' in line:
                        key, value = line.split('=', 1)
                        config[key.strip()] = value.strip()
            config['sgdb_api_key'] = api_key
            CONFIG_FILE.write_text('\n'.join(f"{k}={v}" for k, v in config.items()))
            self.set_status("SteamGridDB API key saved!")
        except Exception as e:
            self.set_status(f"Error saving API key: {e}")

    def _show_steam_shortcuts_dialog(self, parent_dialog):
        """Show fullscreen dialog listing all Steam shortcuts with option to remove individually"""
        dialog = Gtk.Dialog(
            title="Steam Shortcuts",
            parent=self,
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT
        )
        dialog.fullscreen()

        main_box = dialog.get_content_area()
        main_box.set_spacing(0)

        # Header bar
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.set_margin_start(20)
        header.set_margin_end(20)
        header.set_margin_top(20)
        header.set_margin_bottom(10)

        back_btn = Gtk.Button(label="← Back")
        back_btn.get_style_context().add_class('flat-button')
        back_btn.connect('clicked', lambda w: dialog.destroy())
        header.pack_start(back_btn, False, False, 0)

        title = Gtk.Label(label="Steam Shortcuts")
        title.get_style_context().add_class('dialog-title')
        header.pack_start(title, True, True, 0)

        # Spacer for balance
        spacer = Gtk.Label(label="")
        spacer.set_size_request(80, -1)
        header.pack_end(spacer, False, False, 0)

        main_box.pack_start(header, False, False, 0)

        # Scrollable content
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_start(20)
        content.set_margin_end(20)
        content.set_margin_top(10)
        content.set_margin_bottom(20)

        hint = Gtk.Label(label="These are all non-Steam games in your library. Click 🗑️ to remove.")
        hint.get_style_context().add_class('dialog-secondary')
        hint.set_halign(Gtk.Align.START)
        content.pack_start(hint, False, False, 0)

        # Shortcuts list
        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)

        shortcuts = SteamShortcuts.get_all_shortcuts()

        if not shortcuts:
            empty_label = Gtk.Label(label="No non-Steam shortcuts found.")
            empty_label.get_style_context().add_class('dialog-message')
            empty_label.set_margin_top(20)
            content.pack_start(empty_label, False, False, 0)
        else:
            for shortcut in shortcuts:
                row = Gtk.ListBoxRow()

                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
                box.set_margin_start(12)
                box.set_margin_end(12)
                box.set_margin_top(10)
                box.set_margin_bottom(10)

                # Name
                name_label = Gtk.Label(label=shortcut['name'])
                name_label.set_halign(Gtk.Align.START)
                name_label.get_style_context().add_class('menu-button-title')
                box.pack_start(name_label, True, True, 0)

                # Tags
                tags_str = ", ".join(shortcut['tags'][:3]) if shortcut['tags'] else "No tags"
                tags_label = Gtk.Label(label=tags_str)
                tags_label.get_style_context().add_class('dialog-secondary')
                box.pack_start(tags_label, False, False, 0)

                # Remove button
                remove_btn = Gtk.Button(label="🗑️")
                remove_btn.set_tooltip_text("Remove from Steam")
                remove_btn.get_style_context().add_class('flat-button')
                remove_btn.connect('clicked', lambda w, name=shortcut['name'], d=dialog: self._remove_single_shortcut(name, d))
                box.pack_end(remove_btn, False, False, 0)

                row.add(box)
                listbox.add(row)

            content.pack_start(listbox, True, True, 0)

        # Count label
        count_label = Gtk.Label(label=f"Total: {len(shortcuts)} shortcuts")
        count_label.get_style_context().add_class('dialog-secondary')
        count_label.set_halign(Gtk.Align.START)
        content.pack_start(count_label, False, False, 0)

        scroll.add(content)
        main_box.pack_start(scroll, True, True, 0)

        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def _remove_single_shortcut(self, name, parent_dialog):
        """Remove a single shortcut and refresh the dialog"""
        if not self.show_confirm(
            f"Remove '{name}' from Steam?",
            "This will remove the shortcut and its artwork.",
            secondary="Game files will not be deleted.",
            warning=True
        ):
            return

        if SteamShortcuts.remove_shortcut(name=name):
            self.set_status(f"Removed from Steam: {name}")
            # Refresh the shortcuts dialog
            parent_dialog.destroy()
            self._show_steam_shortcuts_dialog(None)
        else:
            self.set_status(f"Failed to remove: {name}")

    def _remove_all_game_shortcuts(self, parent_dialog):
        """Remove all game shortcuts added by RetroPackager"""
        # Get count first
        shortcuts = SteamShortcuts.get_all_shortcuts()

        # Filter to only RetroPackager shortcuts (those with our tags)
        retro_tags = {'ps1', 'playstation', 'gba', 'game boy advance', 'duckstation', 'mgba'}
        retro_shortcuts = [s for s in shortcuts if any(t.lower() in retro_tags for t in s['tags'])]

        if not retro_shortcuts:
            self.show_message("No Shortcuts", "No RetroPackager game shortcuts found in Steam.")
            return

        games_list = "\n".join(f"  • {s['name']}" for s in retro_shortcuts[:10])
        if len(retro_shortcuts) > 10:
            games_list += f"\n  ... and {len(retro_shortcuts) - 10} more"

        if not self.show_confirm(
            f"Remove {len(retro_shortcuts)} game shortcuts from Steam?",
            "This will remove all PS1, GBA, and N64 game shortcuts added by RetroPackager.",
            secondary=f"Game files will NOT be deleted - only the Steam library entries.\n\nGames:\n{games_list}",
            warning=True
        ):
            return

        # Remove shortcuts with RetroPackager tags
        removed = SteamShortcuts.remove_shortcuts_by_tags(
            ['PS1', 'PlayStation', 'GBA', 'Game Boy Advance', 'N64', 'Nintendo 64', 'DuckStation', 'mGBA', 'RMG']
        )
        self.set_status(f"Removed {removed} shortcuts from Steam. Restart Steam to see changes.")
        self.show_message("Shortcuts Removed",
            f"Removed {removed} game shortcuts from Steam.\n\nRestart Steam to see changes.")

    def _generate_frutiger_aero_assets(self):
        """Generate proper Frutiger Aero style graphics for Steam"""
        try:
            from PIL import Image, ImageDraw, ImageFont, ImageFilter
        except ImportError:
            import subprocess
            subprocess.run([sys.executable, '-m', 'pip', 'install', 'Pillow', '--break-system-packages', '-q'])
            from PIL import Image, ImageDraw, ImageFont, ImageFilter
        
        import math
        import random
        
        assets_dir = OUTPUT_DIR_PS1 / '.retro-packager-assets'
        assets_dir.mkdir(parents=True, exist_ok=True)
        
        def get_font(size):
            """Get the best available bold font"""
            font_paths = [
                '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
                '/usr/share/fonts/TTF/DejaVuSans-Bold.ttf', 
                '/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf',
                '/usr/share/fonts/liberation/LiberationSans-Bold.ttf',
                '/usr/share/fonts/google-noto/NotoSans-Bold.ttf',
            ]
            for path in font_paths:
                if os.path.exists(path):
                    return ImageFont.truetype(path, size)
            import glob
            ttfs = glob.glob('/usr/share/fonts/**/*.ttf', recursive=True)
            for ttf in ttfs:
                if 'Bold' in ttf or 'bold' in ttf:
                    try:
                        return ImageFont.truetype(ttf, size)
                    except (OSError, IOError):
                        # Font file couldn't be loaded; try next one
                        continue
            return ImageFont.load_default()
        
        def create_aero_sky(width, height):
            """Create bright Frutiger Aero sky gradient - the signature look"""
            img = Image.new('RGB', (width, height))
            draw = ImageDraw.Draw(img)
            
            # Bright sky gradient - white/cyan at top to vibrant blue at bottom
            for y in range(height):
                ratio = y / height
                # Top: bright white-cyan (230, 245, 255)
                # Bottom: vibrant sky blue (30, 140, 220)
                r = int(230 - (230 - 30) * ratio)
                g = int(245 - (245 - 140) * ratio)
                b = int(255 - (255 - 220) * ratio)
                draw.line([(0, y), (width, y)], fill=(r, g, b))
            
            return img.convert('RGBA')
        
        def draw_bubble(img, x, y, radius, opacity=180):
            """Draw a glossy Frutiger Aero bubble"""
            bubble = Image.new('RGBA', (radius * 2, radius * 2), (0, 0, 0, 0))
            draw = ImageDraw.Draw(bubble)
            
            # Main bubble - subtle blue tint
            draw.ellipse([0, 0, radius * 2 - 1, radius * 2 - 1], 
                        fill=(200, 230, 255, int(opacity * 0.3)))
            
            # Edge highlight - white ring
            draw.ellipse([2, 2, radius * 2 - 3, radius * 2 - 3], 
                        outline=(255, 255, 255, int(opacity * 0.6)), width=2)
            
            # Top-left gloss highlight
            highlight_r = int(radius * 0.4)
            offset_x = int(radius * 0.3)
            offset_y = int(radius * 0.25)
            for i in range(highlight_r, 0, -1):
                alpha = int((opacity * 0.8) * (i / highlight_r))
                draw.ellipse([offset_x + (highlight_r - i), offset_y + (highlight_r - i),
                             offset_x + highlight_r + i, offset_y + highlight_r + i],
                            fill=(255, 255, 255, alpha))
            
            img.paste(bubble, (x - radius, y - radius), bubble)
        
        def draw_aurora(img, width, height):
            """Draw subtle aurora/light streaks"""
            aurora = Image.new('RGBA', (width, height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(aurora)
            
            # Green-cyan aurora streaks
            colors = [
                (100, 255, 180, 30),  # Green
                (80, 220, 255, 25),   # Cyan
                (150, 255, 200, 20),  # Light green
            ]
            
            for i, color in enumerate(colors):
                # Wavy aurora band
                points = []
                y_base = int(height * (0.15 + i * 0.12))
                for x in range(0, width + 20, 20):
                    y = y_base + int(math.sin(x * 0.02 + i) * 30)
                    points.append((x, y))
                    points.append((x, y + 60))
                
                if len(points) >= 4:
                    draw.polygon(points, fill=color)
            
            # Blur the aurora
            aurora = aurora.filter(ImageFilter.GaussianBlur(radius=20))
            return Image.alpha_composite(img, aurora)
        
        def draw_lens_flare(img, x, y, size):
            """Draw a lens flare effect"""
            flare = Image.new('RGBA', img.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(flare)
            
            # Central bright spot
            for i in range(size, 0, -2):
                alpha = int(200 * (i / size) ** 2)
                draw.ellipse([x - i, y - i, x + i, y + i],
                            fill=(255, 255, 255, alpha))
            
            # Orange/yellow glow ring
            ring_r = int(size * 1.5)
            draw.ellipse([x - ring_r, y - ring_r, x + ring_r, y + ring_r],
                        outline=(255, 200, 100, 60), width=3)
            
            return Image.alpha_composite(img, flare)
        
        def draw_glossy_orb(size, color=(100, 200, 255)):
            """Draw a glossy 3D orb/sphere - signature Aero element"""
            orb = Image.new('RGBA', (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(orb)
            center = size // 2
            
            # Base sphere gradient (dark at edges, lighter toward highlight)
            for r in range(center, 0, -1):
                ratio = r / center
                # Darken edges
                factor = 0.4 + 0.6 * ratio
                c = tuple(int(c * factor) for c in color)
                draw.ellipse([center - r, center - r, center + r, center + r], fill=c + (255,))
            
            # Main gloss highlight (top-left)
            highlight = Image.new('RGBA', (size, size), (0, 0, 0, 0))
            h_draw = ImageDraw.Draw(highlight)
            h_size = int(size * 0.35)
            h_x, h_y = int(size * 0.28), int(size * 0.22)
            # Draw concentric ellipses from outside in, getting brighter
            cx, cy = h_x + h_size // 2, h_y + h_size // 2
            for i in range(h_size, 0, -1):
                alpha = int(220 * (i / h_size) ** 0.5)
                h_draw.ellipse([cx - i, cy - i, cx + i, cy + i],
                              fill=(255, 255, 255, alpha))
            
            orb = Image.alpha_composite(orb, highlight)
            
            # Small secondary highlight
            h2_size = int(size * 0.12)
            h2_x, h2_y = int(size * 0.6), int(size * 0.65)
            for i in range(h2_size, 0, -1):
                alpha = int(100 * (i / h2_size))
                draw.ellipse([h2_x, h2_y, h2_x + i * 2, h2_y + i],
                            fill=(255, 255, 255, alpha))
            
            return orb
        
        def draw_text_glossy(draw, pos, text, font, color=(255, 255, 255)):
            """Draw text with glossy Aero effect"""
            x, y = pos
            # Shadow
            draw.text((x + 3, y + 3), text, font=font, fill=(0, 50, 100, 150))
            # Main text
            draw.text((x, y), text, font=font, fill=color)
        
        # Seed for consistent bubbles
        random.seed(42)
        
        # ============================================
        # GRID COVER (600x900) - Portrait
        # ============================================
        grid = create_aero_sky(600, 900)
        grid = draw_aurora(grid, 600, 900)
        
        # Add bubbles
        bubble_positions = [(80, 150, 45), (500, 200, 35), (150, 750, 50), 
                          (450, 650, 40), (300, 100, 25), (550, 450, 30)]
        for bx, by, br in bubble_positions:
            draw_bubble(grid, bx, by, br)
        
        # Glossy orbs
        orb1 = draw_glossy_orb(280, (80, 180, 255))
        orb2 = draw_glossy_orb(180, (100, 220, 200))
        grid.paste(orb1, (160, 120), orb1)
        grid.paste(orb2, (350, 350), orb2)
        
        # Lens flare
        grid = draw_lens_flare(grid, 480, 80, 40)
        
        draw = ImageDraw.Draw(grid)
        font_title = get_font(90)
        font_sub = get_font(42)
        
        draw_text_glossy(draw, (40, 550), "Retro", font_title)
        draw_text_glossy(draw, (40, 650), "Packager", font_title)
        draw_text_glossy(draw, (45, 780), "Download • Package • Play", font_sub, (240, 250, 255))
        
        grid_path = assets_dir / 'grid.png'
        grid.convert('RGB').save(grid_path, quality=95)
        
        # ============================================
        # HERO BANNER (1920x620) - Wide
        # ============================================
        hero = create_aero_sky(1920, 620)
        hero = draw_aurora(hero, 1920, 620)
        
        # Many bubbles scattered
        for _ in range(15):
            bx = random.randint(50, 1870)
            by = random.randint(50, 570)
            br = random.randint(20, 60)
            draw_bubble(hero, bx, by, br, opacity=150)
        
        # Large glossy orbs
        orb_big = draw_glossy_orb(350, (70, 170, 255))
        orb_med = draw_glossy_orb(220, (80, 200, 180))
        orb_small = draw_glossy_orb(150, (120, 180, 255))
        hero.paste(orb_big, (80, 135), orb_big)
        hero.paste(orb_med, (380, 350), orb_med)
        hero.paste(orb_small, (1700, 100), orb_small)
        
        # Lens flares
        hero = draw_lens_flare(hero, 300, 100, 50)
        hero = draw_lens_flare(hero, 1800, 150, 35)
        
        draw = ImageDraw.Draw(hero)
        font_hero = get_font(140)
        font_hero_sub = get_font(52)
        
        draw_text_glossy(draw, (620, 140), "RetroPackager", font_hero)
        draw_text_glossy(draw, (640, 320), "Download • Package • Play", font_hero_sub, (240, 250, 255))
        draw_text_glossy(draw, (640, 400), "Retro Games on Steam Deck", font_hero_sub, (220, 240, 255))
        
        hero_path = assets_dir / 'hero.png'
        hero.convert('RGB').save(hero_path, quality=95)
        
        # ============================================
        # WIDE GRID (920x430) - Horizontal tile
        # ============================================
        wide = create_aero_sky(920, 430)
        wide = draw_aurora(wide, 920, 430)
        
        # Bubbles
        for _ in range(8):
            bx = random.randint(30, 890)
            by = random.randint(30, 400)
            br = random.randint(15, 40)
            draw_bubble(wide, bx, by, br, opacity=140)
        
        # Orbs
        orb_w = draw_glossy_orb(260, (80, 180, 255))
        wide.paste(orb_w, (30, 85), orb_w)
        
        wide = draw_lens_flare(wide, 200, 60, 30)
        
        draw = ImageDraw.Draw(wide)
        font_wide = get_font(80)
        font_wide_sub = get_font(36)
        
        draw_text_glossy(draw, (340, 100), "Retro", font_wide)
        draw_text_glossy(draw, (340, 190), "Packager", font_wide)
        draw_text_glossy(draw, (345, 310), "Download • Package • Play", font_wide_sub, (240, 250, 255))
        
        wide_path = assets_dir / 'wide.png'
        wide.convert('RGB').save(wide_path, quality=95)
        
        # ============================================
        # LOGO (256x256) - Glossy orb
        # ============================================
        logo = Image.new('RGBA', (256, 256), (0, 0, 0, 0))
        orb_logo = draw_glossy_orb(240, (60, 160, 255))
        logo.paste(orb_logo, (8, 8), orb_logo)
        
        logo_path = assets_dir / 'logo.png'
        logo.save(logo_path)
        
        # ============================================
        # ICON (64x64) - Small glossy orb
        # ============================================
        icon = draw_glossy_orb(64, (60, 160, 255))
        
        icon_path = assets_dir / 'icon.png'
        icon.save(icon_path)
        
        return {
            'hero': str(hero_path),
            'grid': str(grid_path), 
            'wide': str(wide_path),
            'logo': str(logo_path),
            'icon': str(icon_path)
        }
    
    def _add_self_to_steam(self, parent_dialog):
        """Add RetroPackager to Steam library with Frutiger Aero artwork"""

        # Create fullscreen progress dialog
        progress_dialog = Gtk.Dialog(
            title="Adding to Steam",
            parent=self,
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT
        )
        progress_dialog.fullscreen()

        main_box = progress_dialog.get_content_area()
        main_box.set_valign(Gtk.Align.CENTER)
        main_box.set_halign(Gtk.Align.CENTER)

        # Content card
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.get_style_context().add_class('dialog-content')
        content.set_size_request(600, 400)

        # Title
        title_label = Gtk.Label(label="Adding RetroPackager to Steam")
        title_label.get_style_context().add_class('dialog-title')
        title_label.set_halign(Gtk.Align.START)
        content.pack_start(title_label, False, False, 0)

        # Progress bar
        progress_bar = Gtk.ProgressBar()
        progress_bar.set_show_text(True)
        progress_bar.set_text("Starting...")
        progress_bar.get_style_context().add_class('progress')
        content.pack_start(progress_bar, False, False, 0)

        # Log output
        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        log_scroll.set_vexpand(True)

        log_view = Gtk.TextView()
        log_view.set_editable(False)
        log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        log_view.set_monospace(True)
        log_view.get_style_context().add_class('log-view')
        log_buffer = log_view.get_buffer()
        log_scroll.add(log_view)
        content.pack_start(log_scroll, True, True, 0)

        # Close button (disabled until done)
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        btn_box.set_halign(Gtk.Align.END)
        close_btn = Gtk.Button(label="Close")
        close_btn.get_style_context().add_class('accent-button')
        close_btn.set_sensitive(False)
        close_btn.connect('clicked', lambda w: progress_dialog.destroy())
        btn_box.pack_end(close_btn, False, False, 0)
        content.pack_start(btn_box, False, False, 0)

        main_box.pack_start(content, False, False, 0)
        progress_dialog.show_all()
        
        def log(msg):
            """Add message to log"""
            def update():
                end_iter = log_buffer.get_end_iter()
                log_buffer.insert(end_iter, msg + "\n")
                # Auto-scroll
                mark = log_buffer.create_mark(None, log_buffer.get_end_iter(), False)
                log_view.scroll_to_mark(mark, 0, False, 0, 0)
            GLib.idle_add(update)
        
        def set_progress(fraction, text):
            """Update progress bar"""
            def update():
                progress_bar.set_fraction(fraction)
                progress_bar.set_text(text)
            GLib.idle_add(update)
        
        def do_install():
            try:
                log("🎨 Generating Frutiger Aero artwork...")
                set_progress(0.1, "Generating artwork...")
                
                log("  • Creating hero banner (1920x620)")
                log("  • Creating grid cover (600x900)")
                log("  • Creating wide grid (920x430)")
                log("  • Creating logo (256x256)")
                log("  • Creating icon (64x64)")
                
                assets = self._generate_frutiger_aero_assets()
                
                log("✓ Artwork generated!")
                log("")
                set_progress(0.4, "Finding Steam user...")
                
                # Get script path
                script_path = str(Path(__file__).resolve())
                log(f"📁 Script path: {script_path}")
                
                # Find Steam user
                log("🔍 Looking for Steam user...")
                user_id = SteamShortcuts.find_user_id()
                if not user_id:
                    log("❌ Could not find Steam user!")
                    log("   Make sure Steam has been run at least once.")
                    GLib.idle_add(lambda: close_btn.set_sensitive(True))
                    set_progress(1.0, "Failed")
                    return
                
                log(f"✓ Found Steam user: {user_id}")
                log("")
                set_progress(0.5, "Generating app ID...")
                
                # Use quoted exe path - this must match what's in shortcuts.vdf
                exe_str = f'"{script_path}"'
                
                # Generate app ID for artwork (unsigned) and shortcut ID (signed)
                app_id = SteamShortcuts.generate_app_id(exe_str, "RetroPackager")
                shortcut_id = SteamShortcuts.generate_shortcut_id(exe_str, "RetroPackager")
                log(f"🔢 Generated App ID (for artwork): {app_id}")
                log(f"🔢 Generated Shortcut ID (for vdf): {shortcut_id}")
                
                set_progress(0.6, "Reading shortcuts.vdf...")
                
                # Add to shortcuts.vdf
                shortcuts_path = Path.home() / '.steam/steam/userdata' / user_id / 'config/shortcuts.vdf'
                log(f"📄 Shortcuts file: {shortcuts_path}")
                
                existing_shortcuts = {}
                if shortcuts_path.exists():
                    log("  • Reading existing shortcuts...")
                    existing_shortcuts = SteamShortcuts.read_shortcuts()
                    log(f"  • Found {len(existing_shortcuts)} existing shortcuts")
                else:
                    log("  • No existing shortcuts file, creating new")
                
                # Check if already added
                for key, s in existing_shortcuts.items():
                    if s.get('AppName') == 'RetroPackager':
                        log("")
                        log("ℹ️  RetroPackager is already in your Steam library!")
                        GLib.idle_add(lambda: close_btn.set_sensitive(True))
                        set_progress(1.0, "Already installed")
                        return
                
                set_progress(0.7, "Creating shortcut entry...")
                log("")
                log("📝 Creating shortcut entry...")
                
                # Create shortcut entry
                new_shortcut = {
                    'appid': shortcut_id,
                    'AppName': 'RetroPackager',
                    'Exe': exe_str,
                    'StartDir': f'"{str(Path(script_path).parent)}"',
                    'icon': assets['icon'],
                    'LaunchOptions': '',
                    'IsHidden': 0,
                    'AllowDesktopConfig': 1,
                    'AllowOverlay': 1,
                    'OpenVR': 0,
                    'LastPlayTime': 0,
                    'tags': {'0': 'Tools', '1': 'Retro', '2': 'Emulation'}
                }
                
                # Add to dict with next index
                next_idx = str(len(existing_shortcuts))
                existing_shortcuts[next_idx] = new_shortcut
                log("✓ Shortcut entry created")
                
                set_progress(0.8, "Writing shortcuts.vdf...")
                
                # Write shortcuts
                log("💾 Writing shortcuts.vdf...")
                if SteamShortcuts.write_shortcuts(existing_shortcuts):
                    log("✓ Shortcuts saved")
                else:
                    log("❌ Failed to write shortcuts")
                    GLib.idle_add(lambda: close_btn.set_sensitive(True))
                    set_progress(1.0, "Failed")
                    return
                
                set_progress(0.9, "Copying artwork...")
                
                # Copy artwork to Steam grid folder
                grid_dir = Path.home() / '.steam/steam/userdata' / user_id / 'config/grid'
                grid_dir.mkdir(parents=True, exist_ok=True)
                log(f"📁 Grid folder: {grid_dir}")
                
                import shutil
                log("🖼️  Copying artwork to Steam...")
                shutil.copy(assets['hero'], grid_dir / f'{app_id}_hero.png')
                log(f"  • hero.png → {app_id}_hero.png")
                shutil.copy(assets['grid'], grid_dir / f'{app_id}p.png')
                log(f"  • grid.png → {app_id}p.png")
                shutil.copy(assets['wide'], grid_dir / f'{app_id}.png')
                log(f"  • wide.png → {app_id}.png")
                shutil.copy(assets['logo'], grid_dir / f'{app_id}_logo.png')
                log(f"  • logo.png → {app_id}_logo.png")
                
                log("")
                log("=" * 45)
                log("✅ SUCCESS! RetroPackager added to Steam!")
                log("=" * 45)
                log("")
                log("👉 Restart Steam to see it in your library")
                log("👉 Launch from Gaming Mode for full experience!")
                
                set_progress(1.0, "Complete!")
                GLib.idle_add(lambda: close_btn.set_sensitive(True))
                GLib.idle_add(lambda: self.set_status("Added to Steam successfully!"))
                
            except Exception as e:
                log("")
                log(f"❌ ERROR: {e}")
                import traceback
                log(traceback.format_exc())
                set_progress(1.0, "Failed")
                GLib.idle_add(lambda: close_btn.set_sensitive(True))
                GLib.idle_add(lambda: self.set_status(f"Error: {e}"))
        
        threading.Thread(target=do_install, daemon=True).start()
    
    def on_select_bios(self, widget=None):
        """Select BIOS file"""
        dialog = Gtk.FileChooserDialog(
            title="Select PS1 BIOS (.bin)",
            parent=self.get_toplevel() if hasattr(self, 'get_toplevel') else None,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK
        )
        
        filter_bios = Gtk.FileFilter()
        filter_bios.set_name("BIOS files (*.bin)")
        filter_bios.add_pattern("*.bin")
        dialog.add_filter(filter_bios)
        
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            src_path = Path(dialog.get_filename())
            dialog.destroy()
            
            # Copy to script directory
            dest_path = SCRIPT_DIR / src_path.name
            if not dest_path.exists():
                shutil.copy2(src_path, dest_path)
            
            self.bios_path_label.set_text(f"✓ {dest_path.name}")
            self.bios_label.set_text(f"BIOS: ✓ {dest_path.name}")
            self.set_status(f"BIOS set: {dest_path.name}")
        else:
            dialog.destroy()
    
    def _get_bios_status(self):
        """Get current BIOS file status"""
        bios = self._get_bios_path()
        if bios:
            return f"✓ {bios.name}"
        return "Not found - place .bin file next to this app"
    
    def _get_bios_path(self):
        """Get the BIOS file path - auto-detects from script folder"""
        # Check script directory for any 512KB .bin file (BIOS_FILE_SIZE = 524288)
        BIOS_FILE_SIZE = 524288  # 512KB
        for bin_file in SCRIPT_DIR.glob("*.bin"):
            try:
                if bin_file.stat().st_size == BIOS_FILE_SIZE:
                    return bin_file
            except OSError:
                # File inaccessible; skip it
                continue
        return None
    
    def on_browse_archive(self):
        """Switch to archive browser view"""
        self.stack.set_visible_child_name("archive")
        # Focus the FlowBox for gamepad navigation
        GLib.idle_add(self._focus_archive_browser)
    
    def _focus_archive_browser(self):
        """Focus the game grid for keyboard/gamepad navigation"""
        if hasattr(self, 'results_flow'):
            self.results_flow.grab_focus()
            # Select first child if any exist
            children = self.results_flow.get_children()
            if children:
                self.results_flow.select_child(children[0])
                children[0].grab_focus()
    
    def on_search(self, widget=None):
        """Search Archive.org"""
        query = self.search_entry.get_text().strip()
        region = self.language_combo.get_active_id()
        genre = self.genre_combo.get_active_id()

        self.set_status("Searching...")

        if self.current_system == "gba":
            # GBA: Browse file list from item
            self._search_gba(query, region, genre)
        elif self.current_system == "n64":
            # N64: Browse file list from item
            self._search_n64(query, region, genre)
        else:
            # PS1: Use collection search
            self._search_ps1(query, None, region, genre)
    
    def _search_ps1(self, query, collection, region, genre):
        """Search PS1 games via Archive.org collection API"""
        # Use hardcoded default collection
        collection = "psxgames"
        
        def search():
            try:
                url = f"https://archive.org/advancedsearch.php?q=collection:({collection})"
                if query:
                    url += f"+AND+title:({urllib.parse.quote(query)})"
                
                if region and region != "all":
                    if region == "usa":
                        url += "+AND+(title:(USA)+OR+title:(US)+OR+title:(NTSC-U))"
                    elif region == "europe":
                        url += "+AND+(title:(Europe)+OR+title:(PAL)+OR+title:(EUR))"
                    elif region == "japan":
                        url += "+AND+(title:(Japan)+OR+title:(Jpn)+OR+title:(NTSC-J)+OR+title:(JP))"
                    elif region == "en":
                        url += "+AND+(title:(USA)+OR+title:(Europe)+OR+title:(En)+OR+title:(English))"
                
                # Genre search
                if genre and genre != "all":
                    genre_terms = {
                        "rpg": "(RPG+OR+%22Role+Playing%22+OR+%22Role-Playing%22)",
                        "action": "(Action)",
                        "adventure": "(Adventure)",
                        "platformer": "(Platform+OR+Platformer)",
                        "racing": "(Racing+OR+Race+OR+Driving)",
                        "fighting": "(Fighting+OR+Fighter+OR+Combat)",
                        "sports": "(Sports+OR+Football+OR+Soccer+OR+Basketball+OR+Baseball+OR+Hockey)",
                        "puzzle": "(Puzzle)",
                        "shooter": "(Shooter+OR+Shooting+OR+%22Shoot+em%22)",
                        "horror": "(Horror+OR+Survival+OR+Resident+OR+Silent)",
                        "strategy": "(Strategy+OR+Tactical)",
                        "simulation": "(Simulation+OR+Simulator)",
                    }
                    if genre in genre_terms:
                        url += f"+AND+{genre_terms[genre]}"
                
                url += "&fl[]=identifier,title&sort[]=title+asc&rows=100&output=json"
                
                with urllib.request.urlopen(url, timeout=15) as response:
                    data = json.loads(response.read().decode())
                
                results = data.get('response', {}).get('docs', [])
                g = genre
                def populate():
                    shown = self._populate_results(results, g)
                    if g and g != "all":
                        self.set_status(f"Found {shown} {g.upper()} games (filtered from {len(results)} results)")
                    else:
                        self.set_status(f"Found {len(results)} results")
                GLib.idle_add(populate)
            except Exception as e:
                GLib.idle_add(lambda: self.set_status(f"Search failed: {e}"))
        
        threading.Thread(target=search, daemon=True).start()
    
    def _search_gba(self, query, region, genre):
        """Search GBA games by browsing item file list"""
        def search():
            try:
                url = f"https://archive.org/metadata/{GBA_ARCHIVE_ITEM}"
                debug_log(f"Fetching GBA item: {url}")
                
                with urllib.request.urlopen(url, timeout=30) as response:
                    data = json.loads(response.read().decode())
                
                valid_exts = ('.gba', '.gbc', '.gb', '.zip')
                results = []
                query_lower = query.lower() if query else ""
                
                for f in data.get('files', []):
                    name = f.get('name', '')
                    name_lower = name.lower()
                    
                    if not name_lower.endswith(valid_exts):
                        continue
                    
                    # Skip GameCube files
                    if '(gamecube)' in name_lower or '(gc)' in name_lower:
                        continue
                    
                    if query_lower and query_lower not in name_lower:
                        continue
                    
                    # Region filter
                    if region and region != "all":
                        if region == "usa" and not any(x in name for x in ['(USA)', '(U)', '[U]', '(En)']):
                            continue
                        elif region == "europe" and not any(x in name for x in ['(Europe)', '(E)', '[E]', '(PAL)']):
                            continue
                        elif region == "japan" and not any(x in name for x in ['(Japan)', '(J)', '[J]', '(Jpn)']):
                            continue
                    
                    # Clean title
                    title = name
                    for ext in valid_exts:
                        if title.lower().endswith(ext):
                            title = title[:-len(ext)]
                    
                    results.append({
                        'identifier': GBA_ARCHIVE_ITEM,
                        'filename': name,
                        'title': title,
                        'size': int(f.get('size', 0))
                    })
                
                results.sort(key=lambda x: x['title'].lower())
                
                if len(results) > 200:
                    results = results[:200]
                    truncated = True
                else:
                    truncated = False
                
                g = genre
                q = query
                def populate():
                    shown = self._populate_results(results, g)
                    status = f"Found {shown} games"
                    if q:
                        status += f" matching '{q}'"
                    if truncated:
                        status += " (first 200)"
                    self.set_status(status)
                GLib.idle_add(populate)
                
            except Exception as e:
                debug_log(f"GBA search error: {e}")
                GLib.idle_add(lambda: self.set_status(f"Search failed: {e}"))
        
        threading.Thread(target=search, daemon=True).start()

    def _search_n64(self, query, region, genre):
        """Search N64 games by browsing item file list"""
        def search():
            try:
                url = f"https://archive.org/metadata/{N64_ARCHIVE_ITEM}"
                debug_log(f"Fetching N64 item: {url}")

                with urllib.request.urlopen(url, timeout=30) as response:
                    data = json.loads(response.read().decode())

                valid_exts = ('.z64', '.n64', '.v64', '.zip')
                results = []
                query_lower = query.lower() if query else ""

                for f in data.get('files', []):
                    name = f.get('name', '')
                    name_lower = name.lower()

                    if not name_lower.endswith(valid_exts):
                        continue

                    if query_lower and query_lower not in name_lower:
                        continue

                    # Region filter
                    if region and region != "all":
                        if region == "usa" and not any(x in name for x in ['(USA)', '(U)', '[U]', '(En)']):
                            continue
                        elif region == "europe" and not any(x in name for x in ['(Europe)', '(E)', '[E]', '(PAL)']):
                            continue
                        elif region == "japan" and not any(x in name for x in ['(Japan)', '(J)', '[J]', '(Jpn)']):
                            continue

                    # Clean title
                    title = name
                    for ext in valid_exts:
                        if title.lower().endswith(ext):
                            title = title[:-len(ext)]

                    results.append({
                        'identifier': N64_ARCHIVE_ITEM,
                        'filename': name,
                        'title': title,
                        'size': int(f.get('size', 0))
                    })

                results.sort(key=lambda x: x['title'].lower())

                if len(results) > 200:
                    results = results[:200]
                    truncated = True
                else:
                    truncated = False

                g = genre
                q = query
                def populate():
                    shown = self._populate_results(results, g)
                    status = f"Found {shown} games"
                    if q:
                        status += f" matching '{q}'"
                    if truncated:
                        status += " (first 200)"
                    self.set_status(status)
                GLib.idle_add(populate)

            except Exception as e:
                debug_log(f"N64 search error: {e}")
                GLib.idle_add(lambda: self.set_status(f"Search failed: {e}"))

        threading.Thread(target=search, daemon=True).start()

    def on_browse_top_picks(self, widget=None):
        """Show curated top picks for selected genre"""
        genre = self.genre_combo.get_active_id()

        # Get picks for correct system
        if self.current_system == "gba":
            picks_db = GBA_TOP_PICKS
            system_name = "GBA"
        elif self.current_system == "n64":
            picks_db = N64_TOP_PICKS
            system_name = "N64"
        else:
            picks_db = TOP_PICKS
            system_name = "PS1"

        # Get picks for selected genre, default to "all"
        picks = picks_db.get(genre, picks_db.get("all", []))
        genre_name = "All Time Classics" if genre == "all" or genre is None else genre.upper()

        if not picks:
            self.set_status(f"No {genre_name} picks available for {system_name}")
            return

        self.set_status(f"Loading Top {system_name} {genre_name} picks...")

        if self.current_system == "gba":
            # For GBA, search for these games in the file list
            self._search_gba_top_picks(picks, genre_name)
        elif self.current_system == "n64":
            # For N64, search for these games in the file list
            self._search_n64_top_picks(picks, genre_name)
        else:
            # For PS1, convert to results format directly
            results = []
            for title, item_id in picks:
                results.append({
                    'identifier': item_id,
                    'title': title
                })

            # These are curated so don't apply genre filter again
            shown = self._populate_results(results, None)
            self.set_status(f"⭐ Top 10 {system_name} {genre_name} Games")
    
    def _search_gba_top_picks(self, picks, genre_name):
        """Search GBA collection for top picks by name"""
        def search():
            try:
                url = f"https://archive.org/metadata/{GBA_ARCHIVE_ITEM}"
                with urllib.request.urlopen(url, timeout=30) as response:
                    data = json.loads(response.read().decode())
                
                valid_exts = ('.gba', '.gbc', '.gb', '.zip')
                all_files = []
                
                for f in data.get('files', []):
                    name = f.get('name', '')
                    name_lower = name.lower()
                    
                    if not name_lower.endswith(valid_exts):
                        continue
                    if '(gamecube)' in name_lower or '(gc)' in name_lower:
                        continue
                    
                    all_files.append({
                        'name': name,
                        'size': int(f.get('size', 0))
                    })
                
                # Find matches for each pick
                results = []
                for title, _ in picks:
                    # Search for title in filenames
                    title_words = title.lower().split()[:3]  # First 3 words
                    
                    for f in all_files:
                        name_lower = f['name'].lower()
                        # Check if all title words appear in filename
                        if all(word in name_lower for word in title_words):
                            # Prefer USA region
                            if '(usa)' in name_lower or '(u)' in name_lower:
                                clean_title = f['name']
                                for ext in valid_exts:
                                    if clean_title.lower().endswith(ext):
                                        clean_title = clean_title[:-len(ext)]
                                
                                results.append({
                                    'identifier': GBA_ARCHIVE_ITEM,
                                    'filename': f['name'],
                                    'title': clean_title,
                                    'size': f['size']
                                })
                                break
                    else:
                        # No USA version, try any region
                        for f in all_files:
                            name_lower = f['name'].lower()
                            if all(word in name_lower for word in title_words):
                                clean_title = f['name']
                                for ext in valid_exts:
                                    if clean_title.lower().endswith(ext):
                                        clean_title = clean_title[:-len(ext)]
                                
                                results.append({
                                    'identifier': GBA_ARCHIVE_ITEM,
                                    'filename': f['name'],
                                    'title': clean_title,
                                    'size': f['size']
                                })
                                break
                
                def populate():
                    shown = self._populate_results(results, None)
                    self.set_status(f"⭐ Found {shown} of {len(picks)} Top GBA {genre_name} Games")
                GLib.idle_add(populate)
                
            except Exception as e:
                debug_log(f"GBA top picks error: {e}")
                GLib.idle_add(lambda: self.set_status(f"Failed to load top picks: {e}"))
        
        threading.Thread(target=search, daemon=True).start()

    def _search_n64_top_picks(self, picks, genre_name):
        """Search N64 collection for top picks by name"""
        def search():
            try:
                url = f"https://archive.org/metadata/{N64_ARCHIVE_ITEM}"
                with urllib.request.urlopen(url, timeout=30) as response:
                    data = json.loads(response.read().decode())

                valid_exts = ('.z64', '.n64', '.v64', '.zip')
                all_files = []

                for f in data.get('files', []):
                    name = f.get('name', '')
                    name_lower = name.lower()

                    if not name_lower.endswith(valid_exts):
                        continue

                    all_files.append({
                        'name': name,
                        'size': int(f.get('size', 0))
                    })

                # Find matches for each pick
                results = []
                for title, _ in picks:
                    # Search for title in filenames
                    title_words = title.lower().split()[:3]  # First 3 words

                    for f in all_files:
                        name_lower = f['name'].lower()
                        # Check if all title words appear in filename
                        if all(word in name_lower for word in title_words):
                            # Prefer USA region
                            if '(usa)' in name_lower or '(u)' in name_lower:
                                clean_title = f['name']
                                for ext in valid_exts:
                                    if clean_title.lower().endswith(ext):
                                        clean_title = clean_title[:-len(ext)]

                                results.append({
                                    'identifier': N64_ARCHIVE_ITEM,
                                    'filename': f['name'],
                                    'title': clean_title,
                                    'size': f['size']
                                })
                                break
                    else:
                        # No USA version, try any region
                        for f in all_files:
                            name_lower = f['name'].lower()
                            if all(word in name_lower for word in title_words):
                                clean_title = f['name']
                                for ext in valid_exts:
                                    if clean_title.lower().endswith(ext):
                                        clean_title = clean_title[:-len(ext)]

                                results.append({
                                    'identifier': N64_ARCHIVE_ITEM,
                                    'filename': f['name'],
                                    'title': clean_title,
                                    'size': f['size']
                                })
                                break

                def populate():
                    shown = self._populate_results(results, None)
                    self.set_status(f"⭐ Found {shown} of {len(picks)} Top N64 {genre_name} Games")
                GLib.idle_add(populate)

            except Exception as e:
                debug_log(f"N64 top picks error: {e}")
                GLib.idle_add(lambda: self.set_status(f"Failed to load top picks: {e}"))

        threading.Thread(target=search, daemon=True).start()

    def on_browse_recent(self, widget=None):
        """Browse recent additions via RSS"""
        # Use hardcoded default collection for PS1
        collection = "psxgames"
        genre = self.genre_combo.get_active_id()
        self.set_status("Fetching recent additions...")
        
        def fetch_rss():
            try:
                url = f"https://archive.org/services/collection-rss.php?collection={collection}"
                with urllib.request.urlopen(url, timeout=15) as response:
                    rss_data = response.read().decode()
                
                root = ET.fromstring(rss_data)
                results = []
                for item in root.findall('.//item'):
                    link = item.find('link')
                    title = item.find('title')
                    if link is not None and title is not None:
                        identifier = link.text.split('/details/')[-1] if link.text else None
                        if identifier:
                            results.append({
                                'identifier': identifier,
                                'title': title.text or identifier
                            })
                
                g = genre
                def populate():
                    shown = self._populate_results(results, g)
                    if g and g != "all":
                        self.set_status(f"Found {shown} {g.upper()} games (filtered from {len(results)} recent)")
                    else:
                        self.set_status(f"Found {len(results)} recent items")
                GLib.idle_add(populate)
            except Exception as e:
                GLib.idle_add(lambda: self.set_status(f"RSS fetch failed: {e}"))
        
        threading.Thread(target=fetch_rss, daemon=True).start()
    
    def _populate_results(self, results, genre_filter=None):
        """Populate results grid with cover art, optionally filtered by genre"""
        for child in self.results_flow.get_children():
            self.results_flow.remove(child)
        
        filtered_count = 0
        shown_count = 0
        
        for item in results:
            identifier = item.get('identifier', '')
            title = item.get('title', identifier)
            
            # Apply genre filter if specified
            if genre_filter and genre_filter != "all":
                game_genres = get_game_genre(title)
                if not game_genres:
                    continue  # Skip games not in our database
                # Check if any of the game's genres match the filter
                filter_map = {
                    "rpg": "RPG", "action": "Action", "adventure": "Adventure",
                    "platformer": "Platformer", "racing": "Racing", "fighting": "Fighting",
                    "sports": "Sports", "puzzle": "Puzzle", "shooter": "Shooter",
                    "horror": "Horror", "strategy": "Strategy", "simulation": "Simulation"
                }
                target_genre = filter_map.get(genre_filter, "")
                if target_genre not in game_genres:
                    filtered_count += 1
                    continue
            
            shown_count += 1
            
            # Use Overlay to put title on top of image
            overlay = Gtk.Overlay()
            overlay.set_size_request(308, 308)  # Fixed size (300 + padding)
            overlay.get_style_context().add_class('game-card')
            overlay.item_data = item
            
            # Big cover art (300x300)
            image = Gtk.Image()
            image.set_size_request(300, 300)
            image.set_from_icon_name("image-loading", Gtk.IconSize.DIALOG)
            overlay.add(image)
            
            self._load_cover_art_async(identifier, image, title)
            
            # Title overlay at bottom
            title_label = Gtk.Label(label=title)
            title_label.get_style_context().add_class('game-title')
            title_label.set_max_width_chars(26)
            title_label.set_ellipsize(Pango.EllipsizeMode.END)
            title_label.set_halign(Gtk.Align.FILL)
            title_label.set_valign(Gtk.Align.END)
            title_label.set_xalign(0)
            overlay.add_overlay(title_label)
            
            flow_child = Gtk.FlowBoxChild()
            flow_child.set_halign(Gtk.Align.START)
            flow_child.add(overlay)
            flow_child.item_data = item
            
            self.results_flow.add(flow_child)
        
        self.results_flow.show_all()
        return shown_count
    
    def _load_cover_art_async(self, identifier, image_widget, game_title=None):
        """Load cover art - tries SteamGridDB first, then Archive.org as fallback"""
        def load():
            tmp_path = None
            try:
                import tempfile
                
                # Get title for SteamGridDB search
                title = game_title or identifier
                
                # Try SteamGridDB first
                api_key = SteamGridDB.DEFAULT_API_KEY
                clean_name = SteamGridDB.clean_game_name(title)
                
                sgdb_success = False
                try:
                    game_id = SteamGridDB.search_game(api_key, clean_name)
                    if game_id:
                        # Try square artwork first: square grid -> icon -> regular grid
                        grid_url = SteamGridDB.get_square_grid(api_key, game_id)
                        if not grid_url:
                            grid_url = SteamGridDB.get_icon(api_key, game_id)
                        if not grid_url:
                            grid_url = SteamGridDB.get_grid(api_key, game_id)
                        
                        if grid_url:
                            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                                tmp_path = tmp.name
                                urllib.request.urlretrieve(grid_url, tmp_path)
                                sgdb_success = True
                except Exception as e:
                    debug_log(f"SteamGridDB cover failed for {clean_name}: {e}")
                
                # Fallback to Archive.org if SteamGridDB failed
                if not sgdb_success:
                    url = f"https://archive.org/services/img/{identifier}"
                    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                        tmp_path = tmp.name
                        urllib.request.urlretrieve(url, tmp_path)
                
                def set_image():
                    try:
                        # Load original image
                        pixbuf = GdkPixbuf.Pixbuf.new_from_file(tmp_path)

                        # Get dimensions and crop to square from center if needed
                        width = pixbuf.get_width()
                        height = pixbuf.get_height()

                        # Only crop if not already square
                        if width != height:
                            size = min(width, height)
                            x_offset = (width - size) // 2
                            y_offset = (height - size) // 2
                            pixbuf = pixbuf.new_subpixbuf(x_offset, y_offset, size, size)

                        # Scale to final size
                        pixbuf = pixbuf.scale_simple(300, 300, GdkPixbuf.InterpType.BILINEAR)
                        image_widget.set_from_pixbuf(pixbuf)
                    except (GLib.Error, OSError) as e:
                        # Image loading/processing failed; show placeholder
                        debug_log(f"Image load error: {e}")
                        image_widget.set_from_icon_name("image-missing", Gtk.IconSize.DIALOG)
                    finally:
                        try:
                            if tmp_path:
                                os.unlink(tmp_path)
                        except OSError:
                            # Temp file cleanup failed; not critical
                            pass

                GLib.idle_add(set_image)
            except (urllib.error.URLError, OSError) as e:
                debug_log(f"Cover art download failed: {e}")
                GLib.idle_add(lambda: image_widget.set_from_icon_name("image-missing", Gtk.IconSize.DIALOG))
        
        threading.Thread(target=load, daemon=True).start()
    
    def on_item_selected_flow(self, flowbox, child):
        """Handle item selection from flow grid"""
        if child and hasattr(child, 'item_data'):
            self.selected_item = child.item_data
            self.download_btn.set_sensitive(True)
            self.set_status(f"Selected: {self.selected_item.get('title', self.selected_item.get('identifier'))}")
    
    def on_download_selected(self, widget=None):
        """Download the selected item"""
        if not hasattr(self, 'selected_item') or not self.selected_item:
            return
        
        item_id = self.selected_item.get('identifier')
        
        # If we already have a filename (from GBA browse), download directly
        if 'filename' in self.selected_item:
            filename = self.selected_item['filename']
            self._start_download_and_install(item_id, filename)
            return
        
        # Otherwise, fetch the file list and let user choose
        self.set_status(f"Fetching file list for {item_id}...")
        
        def fetch_files():
            try:
                url = f"https://archive.org/metadata/{item_id}"
                with urllib.request.urlopen(url, timeout=15) as response:
                    data = json.loads(response.read().decode())
                
                files = []
                has_cue = False
                
                # Get valid extensions for current system
                if self.current_system == "gba":
                    valid_exts = ('.gba', '.gbc', '.gb', '.zip', '.7z')
                elif self.current_system == "n64":
                    valid_exts = ('.z64', '.n64', '.v64', '.zip', '.7z')
                else:
                    valid_exts = ('.chd', '.iso', '.pbp', '.cue', '.bin', '.zip', '.7z')
                
                for f in data.get('files', []):
                    name = f.get('name', '')
                    name_lower = name.lower()
                    
                    if name_lower.endswith('.cue'):
                        has_cue = True
                        files.append({'name': name, 'size': int(f.get('size', 0))})
                    elif name_lower.endswith(valid_exts) and not name_lower.endswith('.bin'):
                        files.append({'name': name, 'size': int(f.get('size', 0))})
                
                # Add BIN files only if no CUE (PS1)
                if not has_cue and self.current_system == "ps1":
                    for f in data.get('files', []):
                        name = f.get('name', '')
                        if name.lower().endswith('.bin') and not any(x['name'] == name for x in files):
                            files.append({'name': name, 'size': int(f.get('size', 0))})
                
                def sort_key(f):
                    name = f['name'].lower()
                    if self.current_system == "gba":
                        if name.endswith('.gba'): return 0
                        if name.endswith('.gbc'): return 1
                        if name.endswith('.gb'): return 2
                        if name.endswith('.zip'): return 3
                    elif self.current_system == "n64":
                        if name.endswith('.z64'): return 0
                        if name.endswith('.n64'): return 1
                        if name.endswith('.v64'): return 2
                        if name.endswith('.zip'): return 3
                    else:
                        if name.endswith('.chd'): return 0
                        if name.endswith('.cue'): return 1
                        if name.endswith('.iso'): return 2
                        if name.endswith('.pbp'): return 3
                    return 4
                
                files.sort(key=sort_key)
                
                if files:
                    GLib.idle_add(lambda: self._show_file_selector(item_id, files))
                else:
                    GLib.idle_add(lambda: self.set_status("No ROM files found"))
            except Exception as e:
                GLib.idle_add(lambda: self.set_status(f"Failed to fetch files: {e}"))
        
        threading.Thread(target=fetch_files, daemon=True).start()
    
    def _show_file_selector(self, item_id, files):
        """Show fullscreen dialog to select which file to download"""
        dialog = Gtk.Dialog(
            title="Select File",
            parent=self,
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT
        )
        dialog.fullscreen()

        main_box = dialog.get_content_area()
        main_box.set_spacing(0)

        # Header bar
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.set_margin_start(20)
        header.set_margin_end(20)
        header.set_margin_top(20)
        header.set_margin_bottom(10)

        back_btn = Gtk.Button(label="← Cancel")
        back_btn.get_style_context().add_class('flat-button')
        back_btn.connect('clicked', lambda w: dialog.response(Gtk.ResponseType.CANCEL))
        header.pack_start(back_btn, False, False, 0)

        title = Gtk.Label(label="Select File")
        title.get_style_context().add_class('dialog-title')
        header.pack_start(title, True, True, 0)

        download_btn = Gtk.Button(label="Download & Install")
        download_btn.get_style_context().add_class('accent-button')
        download_btn.connect('clicked', lambda w: dialog.response(Gtk.ResponseType.OK))
        header.pack_end(download_btn, False, False, 0)

        main_box.pack_start(header, False, False, 0)

        # Subtitle
        sub_label = Gtk.Label(label=f"Select a file from {item_id}:")
        sub_label.get_style_context().add_class('dialog-message')
        sub_label.set_halign(Gtk.Align.START)
        sub_label.set_margin_start(20)
        sub_label.set_margin_bottom(10)
        main_box.pack_start(sub_label, False, False, 0)

        # Scrollable file list
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_margin_start(20)
        scroll.set_margin_end(20)
        scroll.set_margin_bottom(20)

        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)

        for f in files:
            row = Gtk.ListBoxRow()
            row.file_data = f

            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            box.set_margin_start(12)
            box.set_margin_end(12)
            box.set_margin_top(10)
            box.set_margin_bottom(10)

            name_label = Gtk.Label(label=f['name'])
            name_label.set_halign(Gtk.Align.START)
            name_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            name_label.get_style_context().add_class('dialog-message')
            box.pack_start(name_label, True, True, 0)

            size_mb = f['size'] / (1024 * 1024)
            size_label = Gtk.Label(label=f"{size_mb:.1f} MB")
            size_label.get_style_context().add_class('dialog-secondary')
            box.pack_end(size_label, False, False, 0)

            row.add(box)
            listbox.add(row)

        scroll.add(listbox)
        main_box.pack_start(scroll, True, True, 0)

        dialog.show_all()
        response = dialog.run()

        if response == Gtk.ResponseType.OK:
            selected_row = listbox.get_selected_row()
            if selected_row and hasattr(selected_row, 'file_data'):
                filename = selected_row.file_data['name']
                dialog.destroy()
                self._start_download_and_install(item_id, filename)
            else:
                dialog.destroy()
        else:
            dialog.destroy()
    
    def _start_download_and_install(self, item_id, filename):
        """Download and install a game from Archive.org"""
        # Only PS1 requires BIOS
        bios_path = None
        if self.current_system == "ps1":
            bios_path = self._get_bios_path()
            if not bios_path:
                self.show_message("BIOS Required", "Please place a PS1 BIOS file next to this app.")
                return
        
        # Get game name from item title
        import re
        game_name = self.selected_item.get('title', filename.rsplit('.', 1)[0])
        game_name = re.sub(r'\s*\([^)]*\)', '', game_name)
        game_name = re.sub(r'\s*\[[^\]]*\]', '', game_name)
        game_name = game_name.strip()
        
        # Confirm name - fullscreen themed dialog
        name_dialog = Gtk.Dialog(title="Game Name", parent=self, flags=Gtk.DialogFlags.MODAL)
        name_dialog.fullscreen()

        main_box = name_dialog.get_content_area()
        main_box.set_valign(Gtk.Align.CENTER)
        main_box.set_halign(Gtk.Align.CENTER)

        # Content card
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.get_style_context().add_class('dialog-content')
        content.set_size_request(500, -1)

        # Title
        title_label = Gtk.Label(label="Enter Game Name")
        title_label.get_style_context().add_class('dialog-title')
        title_label.set_halign(Gtk.Align.START)
        content.pack_start(title_label, False, False, 0)

        # Subtitle
        sub_label = Gtk.Label(label=f"File: {filename}")
        sub_label.get_style_context().add_class('dialog-secondary')
        sub_label.set_halign(Gtk.Align.START)
        sub_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        content.pack_start(sub_label, False, False, 0)

        # Entry
        name_entry = Gtk.Entry()
        name_entry.set_text(game_name)
        name_entry.get_style_context().add_class('entry')
        content.pack_start(name_entry, False, False, 0)

        # Buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btn_box.set_halign(Gtk.Align.END)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.get_style_context().add_class('flat-button')
        cancel_btn.connect('clicked', lambda w: name_dialog.response(Gtk.ResponseType.CANCEL))
        btn_box.pack_start(cancel_btn, False, False, 0)

        install_btn = Gtk.Button(label="Install")
        install_btn.get_style_context().add_class('accent-button')
        install_btn.connect('clicked', lambda w: name_dialog.response(Gtk.ResponseType.OK))
        btn_box.pack_start(install_btn, False, False, 0)

        content.pack_start(btn_box, False, False, 0)

        main_box.pack_start(content, False, False, 0)
        name_dialog.show_all()
        response = name_dialog.run()

        if response == Gtk.ResponseType.OK:
            game_name = name_entry.get_text().strip()
            name_dialog.destroy()
            if game_name:
                self._run_system_installation(self.current_system, item_id, filename, game_name, bios_path)
        else:
            name_dialog.destroy()
    
    def _reset_packaging_view(self, game_name, item_id=None):
        """Reset packaging view for a new installation"""
        self.packaging_title.set_text(f"Installing: {game_name}")
        self.packaging_progress.set_fraction(0)
        self.packaging_progress.set_text("Starting...")
        self.log_buffer.set_text("")
        self.packaging_play_btn.set_sensitive(False)
        self.packaging_done_btn.set_sensitive(False)
        self.current_launch_path = None
        
        if item_id:
            self._load_cover_art_async(item_id, self.packaging_cover, game_name)
        else:
            # For local ROMs, still try to get cover from SteamGridDB using game name
            self._load_cover_art_async("local", self.packaging_cover, game_name)
        
        for step_label in self.step_labels.values():
            ctx = step_label.get_style_context()
            ctx.remove_class('packaging-step-done')
            ctx.remove_class('packaging-step-active')
            ctx.add_class('packaging-step-pending')
    
    def _set_step(self, step_id, status):
        """Update a packaging step status"""
        def update():
            if step_id in self.step_labels:
                label = self.step_labels[step_id]
                ctx = label.get_style_context()
                ctx.remove_class('packaging-step-pending')
                ctx.remove_class('packaging-step-active')
                ctx.remove_class('packaging-step-done')
                ctx.add_class(f'packaging-step-{status}')
        GLib.idle_add(update)
    
    def _log(self, message):
        """Add message to packaging log"""
        def update():
            end_iter = self.log_buffer.get_end_iter()
            self.log_buffer.insert(end_iter, message + "\n")
            self.log_view.scroll_to_iter(self.log_buffer.get_end_iter(), 0, False, 0, 0)
        GLib.idle_add(update)
    
    def _set_progress(self, fraction, text=""):
        """Update progress bar"""
        def update():
            self.packaging_progress.set_fraction(fraction)
            if text:
                self.packaging_progress.set_text(text)
        GLib.idle_add(update)

    def _run_system_installation(self, system_key, item_id, filename, game_name, bios_path=None):
        """Unified installation for any system - uses SYSTEMS config for system-specific behavior"""
        system = SYSTEMS[system_key]
        self._reset_packaging_view(game_name, item_id)
        self.stack.set_visible_child_name("packaging")

        DebugLog.get().set_ui_callback(self._log)
        debug_log(f"Starting {system['short']} installation: {game_name}")

        def install_thread():
            try:
                # Setup paths
                safe_game_name = game_name.replace(' ', '_')
                game_dir = system["output_dir"] / safe_game_name
                self.current_game_dir = game_dir

                # === Step 1: Download ===
                self._set_step("download", "active")
                self._set_progress(0.05, "Downloading...")
                self._log(f"Downloading: {filename}")
                self._log(f"From: archive.org/download/{item_id}/")

                dest_dir = DOWNLOAD_DIR / item_id
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_file = dest_dir / filename

                encoded_filename = urllib.parse.quote(filename)
                url = f"https://archive.org/download/{item_id}/{encoded_filename}"

                if not dest_file.exists():
                    self._log(f"Downloading from {url}")
                    with urllib.request.urlopen(url, timeout=120) as response:
                        total_size = int(response.headers.get('content-length', 0))
                        downloaded = 0
                        chunk_size = 1024 * 1024

                        with open(dest_file, 'wb') as f:
                            while True:
                                chunk = response.read(chunk_size)
                                if not chunk:
                                    break
                                f.write(chunk)
                                downloaded += len(chunk)
                                if total_size > 0:
                                    pct = downloaded / total_size
                                    self._set_progress(0.05 + pct * 0.30, f"Downloading... {int(pct*100)}%")
                    self._log(f"✓ Downloaded {downloaded / 1024 / 1024:.1f} MB")
                else:
                    self._log("✓ Already downloaded")

                self._set_step("download", "done")

                # === Step 2: Get Emulator ===
                self._set_step("duckstation", "active")
                self._set_progress(0.40, f"Getting {system['emulator_name']}...")

                if system["emulator_portable"]:
                    # Portable mode: emulator stored in shared location, copied to game dir later
                    emulator_cache = system["output_dir"] / system["emulator_name"]
                else:
                    # Shared mode: emulator in EMULATOR_DIR/subdir/
                    emu_dir = EMULATOR_DIR / system["emulator_subdir"]
                    emu_dir.mkdir(parents=True, exist_ok=True)
                    emulator_cache = emu_dir / system["emulator_name"]

                if not emulator_cache.exists():
                    self._log(f"Downloading {system['emulator_name']}...")
                    with urllib.request.urlopen(system["emulator_url"], timeout=120) as response:
                        total_size = int(response.headers.get('content-length', 0))
                        downloaded = 0
                        with open(emulator_cache, 'wb') as f:
                            while True:
                                chunk = response.read(1024 * 64)
                                if not chunk:
                                    break
                                f.write(chunk)
                                downloaded += len(chunk)
                                if total_size > 0:
                                    pct = downloaded / total_size
                                    self._set_progress(0.40 + pct * 0.05, f"Downloading emulator... {int(pct*100)}%")
                    emulator_cache.chmod(0o755)
                    self._log(f"✓ {system['emulator_name']} downloaded")
                else:
                    self._log(f"✓ {system['emulator_name']} ready")

                self._set_step("duckstation", "done")

                # === Step 3: Extract and Copy ROM ===
                self._set_step("copy_rom", "active")
                self._set_progress(0.50, "Preparing ROM...")

                game_dir.mkdir(parents=True, exist_ok=True)
                rom_dir = game_dir / "rom"
                rom_dir.mkdir(exist_ok=True)
                if system["needs_bios"]:
                    (game_dir / "bios").mkdir(exist_ok=True)

                rom_path = None
                rom_extensions = tuple(system["rom_extensions"])

                # Handle extraction
                if filename.lower().endswith('.zip'):
                    self._log(f"Extracting {filename}...")
                    import zipfile
                    with zipfile.ZipFile(dest_file, 'r') as zf:
                        # Find ROM file inside
                        for name in zf.namelist():
                            if name.lower().endswith(rom_extensions):
                                zf.extract(name, rom_dir)
                                rom_path = rom_dir / name
                                self._log(f"✓ Extracted {name}")
                                break
                        if not rom_path:
                            zf.extractall(rom_dir)
                            for f in rom_dir.rglob('*'):
                                if f.suffix.lower() in rom_extensions:
                                    rom_path = f
                                    break

                elif filename.lower().endswith('.7z'):
                    self._log(f"Extracting {filename}...")
                    result = subprocess.run(['7z', 'x', '-y', f'-o{rom_dir}', str(dest_file)],
                                          capture_output=True, text=True)
                    if result.returncode != 0:
                        raise Exception(f"7z extraction failed: {result.stderr}")
                    for f in rom_dir.rglob('*'):
                        if f.suffix.lower() in rom_extensions:
                            rom_path = f
                            self._log(f"✓ Extracted {f.name}")
                            break

                else:
                    # Direct ROM file - check if it matches expected extensions or just copy
                    if filename.lower().endswith(rom_extensions):
                        shutil.copy2(dest_file, rom_dir)
                        rom_path = rom_dir / filename
                        self._log(f"✓ Copied {filename}")
                    else:
                        # Try to find ROM inside (for archives with different extensions)
                        shutil.copy2(dest_file, rom_dir)
                        rom_path = rom_dir / filename
                        self._log(f"✓ Copied {filename}")

                if not rom_path or not rom_path.exists():
                    raise Exception(f"Could not find {system['short']} ROM file")

                # Parse CUE files to find associated BIN files (PS1 specific)
                if system.get("parse_cue_files") and rom_path.suffix.lower() == '.cue':
                    import re
                    with open(rom_path, 'r') as f:
                        for line in f:
                            if 'FILE' in line.upper():
                                match = re.search(r'FILE\s+"?([^"]+)"?\s+', line, re.IGNORECASE)
                                if match:
                                    bin_name = match.group(1)
                                    bin_path = dest_file.parent / bin_name
                                    if not bin_path.exists():
                                        # Check in extracted dir
                                        bin_path = rom_path.parent / bin_name
                                    if bin_path.exists() and bin_path != rom_path:
                                        if not (rom_dir / bin_name).exists():
                                            shutil.copy2(bin_path, rom_dir)
                                            self._log(f"✓ Copied {bin_name}")

                self._set_step("copy_rom", "done")

                # === Step 4: BIOS ===
                self._set_step("copy_bios", "active")
                self._set_progress(0.60, "BIOS check...")

                if system["needs_bios"]:
                    if bios_path:
                        shutil.copy2(bios_path, game_dir / "bios")
                        self._log(f"✓ Copied BIOS: {bios_path.name}")
                    else:
                        self._log("⚠ No BIOS provided")
                else:
                    self._log(f"✓ {system['emulator_name']} has built-in BIOS")

                self._set_step("copy_bios", "done")

                # === Step 5: Config and Launch Script ===
                self._set_step("config", "active")
                self._set_progress(0.70, "Creating launcher...")

                # Determine emulator path for launch script
                if system["emulator_portable"]:
                    # Copy emulator to game dir
                    emulator_dest = game_dir / system["emulator_name"]
                    if emulator_dest.exists():
                        emulator_dest.unlink()
                    shutil.copy2(emulator_cache, emulator_dest)
                    emulator_dest.chmod(0o755)
                    self._log(f"✓ Copied {system['emulator_name']}")
                    # Create portable.txt for DuckStation
                    (game_dir / "portable.txt").touch()
                    emulator_launch_path = f"./{system['emulator_name']}"
                else:
                    # Use shared emulator path
                    emulator_launch_path = shlex.quote(str(emulator_cache))

                # Generate settings.ini if needed (PS1 specific)
                if system.get("needs_settings"):
                    settings_content = get_settings_template(game_dir)
                    (game_dir / "settings.ini").write_text(settings_content)
                    self._log("✓ Created settings.ini")

                # Build ROM path for launch script
                if system.get("launch_relative_rom"):
                    rom_launch_path = shlex.quote(f"./rom/{rom_path.name}")
                else:
                    rom_launch_path = shlex.quote(str(rom_path))

                # Create launch script
                launch_script = f"""#!/bin/bash
cd {shlex.quote(str(game_dir))}
{emulator_launch_path} {system['launch_args']} {rom_launch_path}
"""
                launch_path = game_dir / "launch.sh"
                launch_path.write_text(launch_script)
                launch_path.chmod(0o755)
                self._log("✓ Created launch.sh")
                self.current_launch_path = launch_path

                self._set_step("config", "done")

                # === Step 6: Add to Steam ===
                self._set_step("steam", "active")
                self._set_progress(0.85, "Adding to Steam...")

                shortcut_id = SteamShortcuts.add_shortcut(
                    name=game_name,
                    exe_path=str(launch_path),
                    start_dir=str(game_dir),
                    tags=system["tags"]
                )

                if shortcut_id:
                    self._log(f"✓ Added to Steam library")

                    sgdb_key = self._load_sgdb_key() or SteamGridDB.DEFAULT_API_KEY
                    clean_name = SteamGridDB.clean_game_name(game_name)
                    self._log(f"  Searching SteamGridDB for: {clean_name}")

                    artwork_success, icon_path = SteamGridDB.download_all_artwork(sgdb_key, game_name, shortcut_id)
                    if artwork_success:
                        self._log(f"✓ Added high-quality artwork")
                        # Update shortcut's icon field for Big Picture guide button
                        if icon_path:
                            SteamShortcuts.update_shortcut_icon(game_name, icon_path)
                            self._log(f"✓ Set game icon for Big Picture")
                    else:
                        self._log(f"⚠ Game not found on SteamGridDB")
                        cover_url = f"https://archive.org/services/img/{item_id}"
                        if SteamShortcuts.save_artwork(shortcut_id, cover_url, str(launch_path), game_name):
                            self._log(f"✓ Added cover artwork (Archive.org)")

                    self._log(f"  Restart Steam to see the game!")
                else:
                    self._log("⚠ Could not add to Steam automatically")

                self._set_step("steam", "done")

                # === Done! ===
                self._set_step("done", "done")
                self._set_progress(1.0, "Complete!")
                self._log(f"\n🎉 Successfully installed: {game_name}")
                self._log(f"📁 Location: {game_dir}")
                self._log(f"\n💡 Restart Steam, then find '{game_name}' in your library!")

                def finish():
                    self.packaging_play_btn.set_sensitive(True)
                    self.packaging_done_btn.set_sensitive(True)
                GLib.idle_add(finish)

            except Exception as e:
                self._log(f"\n❌ Error: {e}")
                import traceback
                self._log(traceback.format_exc())
                self._set_progress(0, "Failed!")
                def show_error():
                    self.packaging_done_btn.set_sensitive(True)
                GLib.idle_add(show_error)

        threading.Thread(target=install_thread, daemon=True).start()

    # Legacy methods - redirect to unified _run_system_installation()
    def _run_installation(self, item_id, filename, game_name, bios_path):
        """Legacy wrapper for PS1 - redirects to unified installation"""
        self._run_system_installation("ps1", item_id, filename, game_name, bios_path)

    def _run_gba_installation(self, item_id, filename, game_name):
        """Legacy wrapper for GBA - redirects to unified installation"""
        self._run_system_installation("gba", item_id, filename, game_name)

    def _start_local_packaging(self, rom_path, bios_path, game_name):
        """Package a local ROM"""
        self._reset_packaging_view(game_name)
        self.stack.set_visible_child_name("packaging")
        
        # Hook up debug logger to UI
        DebugLog.get().set_ui_callback(self._log)
        debug_log(f"Starting local ROM installation: {game_name}")
        
        def install_thread():
            try:
                # Replace spaces with underscores to avoid path issues
                safe_game_name = game_name.replace(' ', '_')
                game_dir = OUTPUT_DIR / safe_game_name
                self.current_game_dir = game_dir
                
                # Skip download step
                self._set_step("download", "done")
                self._log("✓ Using local ROM file")
                
                # Step 2: DuckStation
                self._set_step("duckstation", "active")
                self._set_progress(0.2, "Preparing DuckStation...")
                
                appimage_path = OUTPUT_DIR / APPIMAGE_NAME
                if not appimage_path.exists():
                    self._log("Downloading DuckStation...")
                    urllib.request.urlretrieve(DUCKSTATION_URL, str(appimage_path))
                    os.chmod(appimage_path, 0o755)
                    self._log("✓ DuckStation downloaded")
                else:
                    self._log("✓ DuckStation ready")
                self._set_step("duckstation", "done")
                
                # Step 3: Copy ROM
                self._set_step("copy_rom", "active")
                self._set_progress(0.4, "Copying ROM...")
                
                game_dir.mkdir(parents=True, exist_ok=True)
                (game_dir / "rom").mkdir(exist_ok=True)
                (game_dir / "bios").mkdir(exist_ok=True)
                # settings folder not needed for portable mode
                
                rom_path_obj = Path(rom_path)
                if rom_path_obj.suffix.lower() == '.cue':
                    shutil.copy2(rom_path_obj, game_dir / "rom")
                    self._log(f"✓ Copied {rom_path_obj.name}")
                    import re
                    with open(rom_path_obj, 'r') as f:
                        for line in f:
                            if 'FILE' in line.upper():
                                match = re.search(r'FILE\s+"?([^"]+)"?\s+', line, re.IGNORECASE)
                                if match:
                                    bin_name = match.group(1)
                                    bin_path = rom_path_obj.parent / bin_name
                                    if bin_path.exists():
                                        shutil.copy2(bin_path, game_dir / "rom")
                                        self._log(f"✓ Copied {bin_name}")
                else:
                    shutil.copy2(rom_path_obj, game_dir / "rom")
                    self._log(f"✓ Copied {rom_path_obj.name}")
                
                self._set_step("copy_rom", "done")
                
                # Step 4: BIOS
                self._set_step("copy_bios", "active")
                self._set_progress(0.55, "Copying BIOS...")
                
                shutil.copy2(bios_path, game_dir / "bios")
                bios_filename = bios_path.name
                self._log(f"✓ Copied BIOS: {bios_filename}")
                self._set_step("copy_bios", "done")
                
                # Step 5: Config
                self._set_step("config", "active")
                self._set_progress(0.7, "Creating configuration...")
                
                appimage_dest = game_dir / APPIMAGE_NAME
                if appimage_dest.exists():
                    appimage_dest.unlink()
                shutil.copy2(appimage_path, appimage_dest)
                appimage_dest.chmod(0o755)
                self._log("✓ Copied DuckStation")
                
                (game_dir / "portable.txt").touch()
                
                rom_filename = rom_path_obj.name
                bios_full_path = game_dir / "bios" / bios_filename
                settings_content = get_settings_template(game_dir)
                (game_dir / "settings.ini").write_text(settings_content)
                self._log("✓ Created settings.ini")

                # Create launch script (use shlex.quote for safety)
                launch_script = f"""#!/bin/bash
cd {shlex.quote(str(game_dir))}
./{APPIMAGE_NAME} -fullscreen -- {shlex.quote(f"./rom/{rom_filename}")}
"""
                launch_path = game_dir / "launch.sh"
                launch_path.write_text(launch_script)
                launch_path.chmod(0o755)
                self._log("✓ Created launch.sh")
                self.current_launch_path = launch_path
                
                self._set_step("config", "done")
                
                # Step 6: Add to Steam
                self._set_step("steam", "active")
                self._set_progress(0.85, "Adding to Steam...")
                
                shortcut_id = SteamShortcuts.add_shortcut(
                    name=game_name,
                    exe_path=str(launch_path),
                    start_dir=str(game_dir),
                    tags=["PS1", "PlayStation", "DuckStation"]
                )
                
                if shortcut_id:
                    self._log(f"✓ Added to Steam library")

                    # Try SteamGridDB for artwork (local ROMs don't have Archive.org fallback)
                    sgdb_key = self._load_sgdb_key() or SteamGridDB.DEFAULT_API_KEY
                    clean_name = SteamGridDB.clean_game_name(game_name)
                    self._log(f"  Searching SteamGridDB for: {clean_name}")
                    artwork_success, icon_path = SteamGridDB.download_all_artwork(sgdb_key, game_name, shortcut_id)
                    if artwork_success:
                        self._log(f"✓ Added high-quality artwork")
                        # Update shortcut's icon field for Big Picture guide button
                        if icon_path:
                            SteamShortcuts.update_shortcut_icon(game_name, icon_path)
                            self._log(f"✓ Set game icon for Big Picture")
                    else:
                        self._log(f"⚠ Game not found on SteamGridDB")
                        self._log(f"  You can add artwork manually via Decky/SteamGridDB plugin")

                    self._log(f"  Restart Steam to see the game!")
                else:
                    self._log("⚠ Could not add to Steam automatically")
                    self._log("  You can add it manually via 'Add Non-Steam Game'")
                
                self._set_step("steam", "done")
                
                # Done!
                self._set_step("done", "done")
                self._set_progress(1.0, "Complete!")
                self._log(f"\n🎉 Successfully installed: {game_name}")
                self._log(f"📁 Location: {game_dir}")
                self._log(f"\n💡 Restart Steam, then find '{game_name}' in your library!")
                
                def finish():
                    self.packaging_play_btn.set_sensitive(True)
                    self.packaging_done_btn.set_sensitive(True)
                GLib.idle_add(finish)
                
            except Exception as e:
                self._log(f"\n❌ Error: {e}")
                import traceback
                self._log(traceback.format_exc())
                self._set_progress(0, "Failed!")
                def show_error():
                    self.packaging_done_btn.set_sensitive(True)
                GLib.idle_add(show_error)
        
        threading.Thread(target=install_thread, daemon=True).start()
    
    def on_launch_game(self, widget=None):
        """Launch the game directly"""
        if self.current_launch_path and self.current_launch_path.exists():
            subprocess.Popen(['bash', str(self.current_launch_path)])
    
    def show_message(self, title, message):
        """Show a themed fullscreen message dialog"""
        dialog = Gtk.Dialog(parent=self, flags=Gtk.DialogFlags.MODAL)
        dialog.fullscreen()

        main_box = dialog.get_content_area()
        main_box.set_valign(Gtk.Align.CENTER)
        main_box.set_halign(Gtk.Align.CENTER)

        # Content card
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.get_style_context().add_class('dialog-content')
        content.set_size_request(500, -1)

        # Title
        title_label = Gtk.Label(label=title)
        title_label.get_style_context().add_class('dialog-title')
        title_label.set_halign(Gtk.Align.START)
        content.pack_start(title_label, False, False, 0)

        # Message
        msg_label = Gtk.Label(label=message)
        msg_label.get_style_context().add_class('dialog-message')
        msg_label.set_halign(Gtk.Align.START)
        msg_label.set_line_wrap(True)
        msg_label.set_max_width_chars(50)
        content.pack_start(msg_label, False, False, 0)

        # OK button
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        btn_box.set_halign(Gtk.Align.END)
        ok_btn = Gtk.Button(label="OK")
        ok_btn.get_style_context().add_class('accent-button')
        ok_btn.connect('clicked', lambda w: dialog.destroy())
        btn_box.pack_end(ok_btn, False, False, 0)
        content.pack_start(btn_box, False, False, 0)

        main_box.pack_start(content, False, False, 0)
        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def show_confirm(self, title, message, secondary=None, warning=False):
        """Show a themed fullscreen confirmation dialog. Returns True if confirmed."""
        dialog = Gtk.Dialog(parent=self, flags=Gtk.DialogFlags.MODAL)
        dialog.fullscreen()

        main_box = dialog.get_content_area()
        main_box.set_valign(Gtk.Align.CENTER)
        main_box.set_halign(Gtk.Align.CENTER)

        # Content card
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.get_style_context().add_class('dialog-content')
        if warning:
            content.get_style_context().add_class('dialog-warning')
        content.set_size_request(500, -1)

        # Title
        title_label = Gtk.Label(label=title)
        title_label.get_style_context().add_class('dialog-title')
        title_label.set_halign(Gtk.Align.START)
        content.pack_start(title_label, False, False, 0)

        # Message
        msg_label = Gtk.Label(label=message)
        msg_label.get_style_context().add_class('dialog-message')
        msg_label.set_halign(Gtk.Align.START)
        msg_label.set_line_wrap(True)
        msg_label.set_max_width_chars(50)
        content.pack_start(msg_label, False, False, 0)

        # Secondary message
        if secondary:
            sec_label = Gtk.Label(label=secondary)
            sec_label.get_style_context().add_class('dialog-secondary')
            sec_label.set_halign(Gtk.Align.START)
            sec_label.set_line_wrap(True)
            sec_label.set_max_width_chars(50)
            content.pack_start(sec_label, False, False, 0)

        # Buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btn_box.set_halign(Gtk.Align.END)

        no_btn = Gtk.Button(label="No")
        no_btn.get_style_context().add_class('flat-button')
        no_btn.connect('clicked', lambda w: dialog.response(Gtk.ResponseType.NO))
        btn_box.pack_start(no_btn, False, False, 0)

        yes_btn = Gtk.Button(label="Yes")
        yes_btn.get_style_context().add_class('accent-button')
        yes_btn.connect('clicked', lambda w: dialog.response(Gtk.ResponseType.YES))
        btn_box.pack_start(yes_btn, False, False, 0)

        content.pack_start(btn_box, False, False, 0)

        main_box.pack_start(content, False, False, 0)
        dialog.show_all()
        response = dialog.run()
        dialog.destroy()
        return response == Gtk.ResponseType.YES


# Legacy alias
PS1PackagerApp = RetroPackagerApp


def main():
    app = RetroPackagerApp()
    app.connect('destroy', Gtk.main_quit)
    app.show_all()
    Gtk.main()


if __name__ == '__main__':
    main()
