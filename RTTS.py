"""
RTTS (Roblox Text-to-Speech) - local AI voice edition.

Type in Roblox chat as normal. When you press Enter, the message is also spoken
by an offline voice into a virtual audio cable (VB-Cable), which Roblox voice
chat uses as your microphone.

Pick a language and a voice model from the lists: eSpeak, Piper, Kokoro or Orpheus,
in English, Spanish, French, Portuguese (Brazil), German, Hindi, Russian, Arabic or
Chinese. Every model runs on your own PC (no account, no API key, no internet after
the one-time download). RTTS's own text is translated in rtts_i18n.py.


Made using AI
"""

import atexit
import contextlib
import ctypes
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unicodedata
import urllib.error
import urllib.request
import uuid
import webbrowser
from collections import namedtuple
from pathlib import Path

# pythonw.exe (no console) has no stdout/stderr; make sure printing never crashes.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

import tkinter as tk
from tkinter import colorchooser, messagebox, ttk
from tkinter import font as tkfont

APP_TITLE = "RTTS"
APP_VERSION = "1.1.1"      # keep the same as AppVer in RTTS.iss
USER_AGENT = "RTTS/" + APP_VERSION
GITHUB_REPO = "RobloxTTS/RTTS"
REPO_URL = "https://github.com/" + GITHUB_REPO
RELEASES_URL = REPO_URL + "/releases/latest"
LATEST_RELEASE_API = "https://api.github.com/repos/%s/releases/latest" % GITHUB_REPO
IS_WIN = sys.platform == "win32"
FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
MODEL_DIR = APP_DIR / "models"
DATA_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "RTTS"
SETTINGS_FILE = DATA_DIR / "settings.json"
ERROR_LOG = DATA_DIR / "error.log"
UPDATE_DIR = DATA_DIR / "updates"     # downloaded installers; cleared on the next start
STATS_CHOICE_FILE = DATA_DIR / "voice_stats_choice.txt"   # written by the installer's "voice stats" tick
# Anonymous voice stats (opt-in, see README > Privacy), sent to PostHog. The project API key starts with "phc_"
# (PostHog > Project settings); the host is https://us.i.posthog.com or https://eu.i.posthog.com, whichever the
# project is in. While the key is empty, RTTS sends nothing even with the setting on.
STATS_API_KEY = "phc_n5WxUgpG4SXEELokVELgR2TY4rLeVya9TuyAUePURW4z"
STATS_HOST = "https://us.i.posthog.com"

# RTTS's own text in other languages lives in rtts_i18n.py, next to this file (PyInstaller bundles it).
if not FROZEN and str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
from rtts_i18n import UI_FONTS, UI_LANGUAGES, set_ui_language, tr, windows_ui_language  # noqa: E402

# Before the rename to RTTS this app kept its settings in a folder named after the old name.
OLD_DATA_DIR = DATA_DIR.parent / "RobloxChatVoice"


def migrate_old_data():
    """One time: copy the settings from the old folder so the rename loses nothing."""
    try:
        if OLD_DATA_DIR.is_dir() and not DATA_DIR.exists():
            shutil.copytree(OLD_DATA_DIR, DATA_DIR)
    except Exception:
        pass


migrate_old_data()


def forget_openai_key():
    """One time: the cloud (OpenAI) voices were removed, so delete a saved API key instead of keeping it on disk."""
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "openai_key" in data:
            del data["openai_key"]
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
    except Exception:
        pass


forget_openai_key()


def apply_installer_stats_choice():
    """The installer's "voice stats" tick leaves a 1 / 0 file. Save it as the voice_stats setting, then delete it."""
    try:
        choice = STATS_CHOICE_FILE.read_text(encoding="utf-8").strip() == "1"
    except Exception:
        return
    data = {}
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            data = loaded
    except Exception:
        pass
    data["voice_stats"] = choice
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        STATS_CHOICE_FILE.unlink()
    except Exception:
        pass


apply_installer_stats_choice()

BASE_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
PIPER_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"

# One downloadable file. size / sha are checked when they are known; approx (bytes) only feeds the
# progress bar for files whose exact size isn't known in advance. unpack names a folder in models\ that a
# .zip is extracted into (the .zip itself is deleted afterwards).
Remote = namedtuple("Remote", "name url size sha approx unpack")


def remote(name, url, size=0, sha=None, approx=0, unpack=None):
    return Remote(name, url, size, sha, approx, unpack)


KOKORO_VOICES = remote("voices-v1.0.bin", BASE_URL + "voices-v1.0.bin", 28214398,
                       "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d")
KOKORO_BEST = remote("kokoro-v1.0.onnx", BASE_URL + "kokoro-v1.0.onnx", 325532387,
                     "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5")
KOKORO_FAST = remote("kokoro-v1.0.int8.onnx", BASE_URL + "kokoro-v1.0.int8.onnx", 92361271,
                     "6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb")


# Orpheus 3B runs in llama.cpp's llama-server, and SNAC turns its sound codes into audio. Both download the first
# time Orpheus is picked. The Vulkan build of llama.cpp is one small download that uses NVIDIA, AMD and Intel
# graphics cards (on an RTX 4060 Ti it is as fast as the 650 MB CUDA build), and falls back to the processor.
# Every URL is pinned to one release / commit, so the files can't change under RTTS.
LLAMA_BUILD = "b11228"
LLAMA_DIR = "llama.cpp-%s-vulkan" % LLAMA_BUILD
LLAMA_RUNTIME = remote(
    "llama-%s-bin-win-vulkan-x64.zip" % LLAMA_BUILD,
    "https://github.com/ggml-org/llama.cpp/releases/download/%s/llama-%s-bin-win-vulkan-x64.zip"
    % (LLAMA_BUILD, LLAMA_BUILD), 33065094,
    "3b80cbb95b79b40967a4ec24592e32e966f50742d58042e777dc48862f0802d5", unpack=LLAMA_DIR)
ORPHEUS_MODEL = remote(
    "orpheus-3b-0.1-ft-Q4_K_M.gguf",
    "https://huggingface.co/unsloth/orpheus-3b-0.1-ft-GGUF/resolve/e2b00302c46af8205f521f60016600aa25a068e6/"
    "orpheus-3b-0.1-ft-Q4_K_M.gguf", 2092569120,
    "4e94a925593c8fc1c20ee50e5b0e4bf915ea106a0bda3b917ff69b02559f7ab9")
SNAC_DECODER = remote(
    "snac_24khz_decoder.onnx",
    "https://huggingface.co/onnx-community/snac_24khz-ONNX/resolve/f317cdff7efb7b2a63148c9e68bbb0320fe629a1/"
    "onnx/decoder_model.onnx", 52600822,
    "9e2991fce96c3b49c508304e656052f4f6b860fe8432164fcd976eee195fd4f0")
ORPHEUS_FILES = [ORPHEUS_MODEL, SNAC_DECODER, LLAMA_RUNTIME]
ORPHEUS_VOICES = [
    ("tara", "Tara (female)"), ("leah", "Leah (female)"), ("jess", "Jess (female)"), ("mia", "Mia (female)"),
    ("zoe", "Zoe (female)"), ("leo", "Leo (male)"), ("dan", "Dan (male)"), ("zac", "Zac (male)"),
]

LARGE_MARK = "⚡"          # shown next to models with 1B+ parameters


def piper_files(path, mb, sha=None):
    """The two files of one Piper voice (model + settings). `path` is its folder/name in the voice repo."""
    name = path.rsplit("/", 1)[-1]
    return [remote(name + ".onnx", PIPER_URL + path + ".onnx", 0, sha, int(mb * 1000000)),
            remote(name + ".onnx.json", PIPER_URL + path + ".onnx.json", 0, None, 6000)]


# The languages RTTS can speak: (code, name in that language). Picked under Voice > Language; each model
# lists only its voices for that language, and models with none are hidden (see models_for()).
LANGUAGES = [
    ("en", "English"), ("es", "Español"), ("fr", "Français"), ("pt", "Português (Brasil)"), ("de", "Deutsch"),
    ("hi", "हिन्दी"), ("ru", "Русский"), ("ar", "العربية"), ("zh", "中文 (普通话)"),
]
LANGUAGE_NAMES = dict(LANGUAGES)
DEFAULT_LANGUAGE = "en"
# Shown under the voice when its language is picked: how to type messages RTTS can read in that language.
# (RTTS reads the keys you press. A plain keyboard layout gives it the letters; an input method that turns
# Latin letters into characters, like Pinyin, doesn't.)
LANGUAGE_TIPS = {
    "zh": "Chinese typed with an input method (like Pinyin) can't be read: RTTS only sees the Latin letters you "
          "press. Paste your message into the chat with Ctrl+V instead.",
}

# espeak-ng voices: (voice name, label, language). "+m3" / "+f3" are its built-in male / female variants.
# Its British English voice is called plain "en" ("en-gb" is not a voice name it knows). No Arabic: written
# Arabic leaves out most vowels, and eSpeak can't add them back, so its Arabic is very hard to understand.
ESPEAK_VOICES = [
    ("en-us", "Classic (US)", "en"),
    ("en-us+m3", "Male (US)", "en"),
    ("en-us+f3", "Female (US)", "en"),
    ("en+m3", "Male (UK)", "en"),
    ("en+f3", "Female (UK)", "en"),
    ("en-us+whisper", "Whisper (US)", "en"),
    ("es", "Classic (Spain)", "es"),
    ("es-419", "Classic (Latin America)", "es"),
    ("es+m3", "Male (Spain)", "es"),
    ("es+f3", "Female (Spain)", "es"),
]
for _code, _voice in (("fr", "fr"), ("pt", "pt-br"), ("de", "de"), ("hi", "hi"), ("ru", "ru"), ("zh", "cmn")):
    ESPEAK_VOICES += [(_voice, "Classic", _code), (_voice + "+m3", "Male", _code), (_voice + "+f3", "Female", _code)]

# The model list, from the lightest / most robotic to the heaviest / most natural.
#   kind  : which engine runs it        where : "local" (every model runs on this PC)
# Piper voices are (id, label, files, language); each is its own download.
CATALOG = [
    dict(id="espeak", name="eSpeak", kind="espeak", where="local", params="no neural net",
         note="Robotic. Instant, tiny, almost no CPU."),
    dict(id="piper-low", name="Piper Low", kind="piper", where="local", params="~16M params",
         note="Light neural voice. Fast, a bit rough.",
         voices=[("lessac-low", "Lessac (US female)",
                  piper_files("en/en_US/lessac/low/en_US-lessac-low", 63.2,
                              "f7d01dde371555732c4c314111ac79672b1a5ce2fc19266ab42178fd8df7f375"), "en"),
                 ("kerstin-low", "Kerstin (female)",
                  piper_files("de/de_DE/kerstin/low/de_DE-kerstin-low", 63.1,
                              "d352a7641892cebf2903859af94e9ba81a141110215fe3943bcda7f7da401b7a"), "de")]),
    dict(id="piper-medium", name="Piper Medium", kind="piper", where="local", params="~16M params",
         note="Natural neural voice. Good all-rounder.",
         voices=[("lessac-medium", "Lessac (US female)",
                  piper_files("en/en_US/lessac/medium/en_US-lessac-medium", 63.2), "en"),
                 ("alba-medium", "Alba (UK female)",
                  piper_files("en/en_GB/alba/medium/en_GB-alba-medium", 63.2,
                              "401369c4a81d09fdd86c32c5c864440811dbdcc66466cde2d64f7133a66ad03b"), "en"),
                 ("alan-medium", "Alan (UK male)",
                  piper_files("en/en_GB/alan/medium/en_GB-alan-medium", 63.2), "en"),
                 ("davefx-medium", "Davefx (Spain, male)",
                  piper_files("es/es_ES/davefx/medium/es_ES-davefx-medium", 63.2,
                              "6658b03b1a6c316ee4c265a9896abc1393353c2d9e1bca7d66c2c442e222a917"), "es"),
                 ("ald-medium", "Ald (Mexico, male)",
                  piper_files("es/es_MX/ald/medium/es_MX-ald-medium", 63.2,
                              "019b3803293c93e34a206dd2e53a3889209a514e786fd7144f7b70196c579b63"), "es"),
                 ("siwis-medium", "Siwis (female)",
                  piper_files("fr/fr_FR/siwis/medium/fr_FR-siwis-medium", 63.2,
                              "641d1ab097da2b81128c076810edb052b385decc8be3381814802a64a73baf99"), "fr"),
                 ("tom-medium", "Tom (male)",
                  piper_files("fr/fr_FR/tom/medium/fr_FR-tom-medium", 63.5,
                              "bf65074ccdeeeeaa832e75edb1c0a513c01c9a972bdf085ff8a6e71ea234fd41"), "fr"),
                 ("faber-medium", "Faber (male)",
                  piper_files("pt/pt_BR/faber/medium/pt_BR-faber-medium", 63.2,
                              "858555e3a064209c57088fe6bd70c4c3dc54d03eaa00c45d5ecaf43a33f95aa7"), "pt"),
                 ("thorsten-medium", "Thorsten (male)",
                  piper_files("de/de_DE/thorsten/medium/de_DE-thorsten-medium", 63.2,
                              "7e64762d8e5118bb578f2eea6207e1a35a8e0c30595010b666f983fc87bb7819"), "de"),
                 ("priyamvada-medium", "Priyamvada (female)",
                  piper_files("hi/hi_IN/priyamvada/medium/hi_IN-priyamvada-medium", 63.5,
                              "aa63bcf2cd493b55a450f280e23cf77f03afc9af7015e6e5acd43b652f166c88"), "hi"),
                 ("pratham-medium", "Pratham (male)",
                  piper_files("hi/hi_IN/pratham/medium/hi_IN-pratham-medium", 63.5,
                              "169964b0871667f6793416d4b35e97357a68ba1ad01df8580c28048989ee7693"), "hi"),
                 ("irina-medium", "Irina (female)",
                  piper_files("ru/ru_RU/irina/medium/ru_RU-irina-medium", 63.2,
                              "8ff38212d23da300bbe3705c645e6e5b9475f0bfde01558eb17813e22acaaaaa"), "ru"),
                 ("ruslan-medium", "Ruslan (male)",
                  piper_files("ru/ru_RU/ruslan/medium/ru_RU-ruslan-medium", 63.2,
                              "72a5f88e0b20928064eb45d88e1daa21f8af62d18613580d32cbb4aed48dcf7f"), "ru"),
                 ("kareem-medium", "Kareem (male)",
                  piper_files("ar/ar_JO/kareem/medium/ar_JO-kareem-medium", 63.2,
                              "9e95cab07b679da603bba17c4dec7ab3111320571964ee95c0379603c086491e"), "ar"),
                 ("huayan-medium", "Huayan (female)",
                  piper_files("zh/zh_CN/huayan/medium/zh_CN-huayan-medium", 63.2,
                              "9929917bf8cabb26fd528ea44d3a6699c11e87317a14765312420be230be0f3d"), "zh")]),
    dict(id="piper-high", name="Piper High", kind="piper", where="local", params="~30M params",
         note="Clearer neural voice. Bigger and slower.",
         voices=[("ryan-high", "Ryan (US male)",
                  piper_files("en/en_US/ryan/high/en_US-ryan-high", 121,
                              "b3990d7606e183ec8dbfba70a4607074f162de1a0c412e0180d1ff60bb154eca"), "en"),
                 ("daniela-high", "Daniela (Argentina, female)",
                  piper_files("es/es_AR/daniela/high/es_AR-daniela-high", 114.2,
                              "7ceb1fc0dab349418c5b54a639ae9ee595212d7c9ea422220d8419163d5cc985"), "es"),
                 ("thorsten-high", "Thorsten (male)",
                  piper_files("de/de_DE/thorsten/high/de_DE-thorsten-high", 113.9,
                              "9df1c43c61149ef9b39e618e2b861fbe41e1fcea9390b2dac62e8761573ea4f1"), "de")]),
    dict(id="kokoro-fast", name="Kokoro Faster", kind="kokoro", where="local", params="82M params",
         note="Very natural, compressed for speed. Incl. 28 MB voice pack.",
         files=[KOKORO_VOICES, KOKORO_FAST]),
    dict(id="kokoro-best", name="Kokoro Best", kind="kokoro", where="local", params="82M params",
         note="Very natural, best local quality. Incl. 28 MB voice pack.",
         files=[KOKORO_VOICES, KOKORO_BEST]),
    dict(id="orpheus", name="Orpheus", kind="orpheus", where="local", params="3B params", params_b=3.0,
         voices=ORPHEUS_VOICES,
         note="%s Large model. Most realistic local voice. Speed slider has no effect." % LARGE_MARK),
]
CATALOG_BY_ID = {e["id"]: e for e in CATALOG}
DEFAULT_MODEL = "kokoro-best"
LEGACY_MODELS = {"Best quality (326 MB)": "kokoro-best", "Faster (92 MB)": "kokoro-fast"}


def entry_mb(e):
    """Download size in MB shown in the list (0 = nothing to download)."""
    if e["kind"] == "kokoro":
        return int(round(sum(r.size for r in e["files"]) / 1e6))
    if e["kind"] == "piper":
        return int(round(max(v[2][0].approx for v in e["voices"]) / 1e6))
    if e["kind"] == "orpheus":
        return int(round(sum(r.size for r in ORPHEUS_FILES) / 1e6))
    return 0


def size_text(mb):
    return ("%.1f GB" % (mb / 1000.0)) if mb >= 1000 else ("%d MB" % mb)


def is_large(e):
    """Models with 1B+ parameters: marked in the list, and they ask before their first download."""
    return e.get("params_b", 0) >= 1


def model_label(e):
    """Text shown in the model drop-down: name, size (and the large-model mark)."""
    tail = size_text(entry_mb(e)) if entry_mb(e) else tr("built in")
    return "%s%s \u00b7 %s" % (LARGE_MARK + " " if is_large(e) else "", e["name"], tail)


def voice_language(e, voice_id):
    """The language code a voice of catalog entry e speaks."""
    if e["kind"] == "kokoro":
        return KOKORO_LANGUAGES.get(voice_id[:1], "en")
    if e["kind"] == "piper":
        return next((v[3] for v in e["voices"] if v[0] == voice_id), "en")
    if e["kind"] == "espeak":
        return next((v[2] for v in ESPEAK_VOICES if v[0] == voice_id), "en")
    return "en"                              # Orpheus speaks English only


def catalog_voices(e, lang=None):
    """[(voice_id, label)] for a catalog entry, known before anything is downloaded; only `lang` if given."""
    if e["kind"] == "kokoro":
        pairs = [(v, voice_label(v)) for v in VOICE_ORDER]
    elif e["kind"] == "piper":
        pairs = [(v[0], v[1]) for v in e["voices"]]
    elif e["kind"] == "espeak":
        pairs = [(v[0], v[1]) for v in ESPEAK_VOICES]
    elif e["kind"] == "orpheus":
        pairs = list(ORPHEUS_VOICES)
    else:
        pairs = [(v, v.capitalize()) for v in e["voices"]]
    if lang is not None:
        pairs = [p for p in pairs if voice_language(e, p[0]) == lang]
    return pairs


def models_for(lang):
    """The catalog entries that have at least one voice in `lang`, in catalog order."""
    return [e for e in CATALOG if catalog_voices(e, lang)]


def entry_files(e, voice):
    """The files that must be on disk to use this model (and voice). Empty for built-in (eSpeak)."""
    if e["kind"] == "kokoro":
        return list(e["files"])
    if e["kind"] == "piper":
        for v in e["voices"]:
            if v[0] == voice:
                return list(v[2])
        return list(e["voices"][0][2])
    if e["kind"] == "orpheus":
        return list(ORPHEUS_FILES)
    return []


def engine_key(e, voice):
    """What identifies a loaded engine: only Piper needs a new load when the voice changes."""
    return (e["id"], voice if e["kind"] == "piper" else "")

# Kokoro voices, best sounding first within each language. The first letter of a voice id is its language
# (a / b = US / UK English, e = Spanish, f = French, h = Hindi, p = Brazilian Portuguese). Its Chinese voices
# are left out: Kokoro's Chinese came out garbled in testing, and Piper's Chinese voice is clear.
VOICE_ORDER = [
    "af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky", "af_aoede",
    "af_kore", "af_nova", "af_river", "af_alloy", "af_jessica",
    "am_michael", "am_fenrir", "am_puck", "am_adam", "am_echo", "am_eric",
    "am_liam", "am_onyx", "am_santa",
    "bf_emma", "bf_isabella", "bf_alice", "bf_lily",
    "bm_george", "bm_fable", "bm_lewis", "bm_daniel",
    "ef_dora", "em_alex", "em_santa",
    "ff_siwis",
    "hf_alpha", "hf_beta", "hm_omega", "hm_psi",
    "pf_dora", "pm_alex", "pm_santa",
]
VOICE_KIND = {"af": "US female", "am": "US male", "bf": "UK female", "bm": "UK male",
              "ef": "female", "em": "male", "ff": "female", "hf": "female", "hm": "male", "pf": "female", "pm": "male"}
KOKORO_LANGUAGES = {"a": "en", "b": "en", "e": "es", "f": "fr", "h": "hi", "p": "pt"}
KOKORO_ESPEAK = {"a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi", "p": "pt-br"}   # its phonemizer's names
DEFAULT_VOICE = "af_heart"

MAX_CHAT_CHARS = 200          # Roblox's own chat limit
CAPTURE_TIMEOUT = 90          # seconds, same as the original app
LEAD_SILENCE = 0.15           # seconds of silence before the voice
TAIL_SILENCE = 0.35           # seconds of silence after the voice
CHUNK_FRAMES = 1024

DEFAULTS = {
    "voice": DEFAULT_VOICE,
    "model": DEFAULT_MODEL,
    "language": DEFAULT_LANGUAGE,  # the language the voice speaks (Voice > Language)
    "ui_language": "",         # the language of RTTS's own text; "" = match Windows (English if not supported)
    "output": "",
    "speed": 100,
    "volume": 100,
    "key": "/",
    "only_roblox": True,
    "speakers": True,
    "interrupt": False,        # a new chat message stops the one that is playing
    "slash_typed": False,
    "skip_emojis": False,
    "auto_update": False,      # install new versions without asking (Settings > About and updates)
    "voice_stats": False,      # send which voice is used, nothing else (opt-in; installer tick or Settings)
    "theme": "classic",        # a THEMES key, "system" (Classic or Midnight to match Windows) or "custom"
    "custom_accent": "#F3242F",   # the Custom theme: its colour, on a light or dark base
    "custom_base": "light",
    "topmost": False,
    "start_key": "f6",         # Start / Stop hotkeys (pynput key names), set in Settings > Keybinds
    "stop_key": "f7",
}

# The look: retro desktop windows (raised grey buttons, sunken white fields, etched group boxes) in
# Space Mono, which ships with RTTS in assets/fonts (SIL Open Font License, see assets/fonts/OFL.txt).
FONT_FILES = ("SpaceMono-Regular.ttf", "SpaceMono-Bold.ttf")
UI_FAMILY = "Space Mono"           # main() falls back to Consolas if the bundled font can't be loaded
FONT_SIZE = 9


def ui_font(size=None, bold=False):
    """(family, size[, "bold"]) in RTTS's font; size None = the normal text size (set in main())."""
    size = FONT_SIZE if size is None else size + (FONT_SIZE - 9)   # other sizes scale with a bigger base size
    return (UI_FAMILY, size, "bold") if bold else (UI_FAMILY, size)


# Colour schemes (Settings > Appearance). Every theme has the same keys:
#   face = window and button grey, hot = button under the mouse, text / hint / heading = text colours,
#   field = inside text boxes, light / light2 / shadow / darkest = the 3D bevel edges (outer light, inner
#   light, inner shadow, outer shadow), accent / accent_dark = the highlight colour (Start, the
#   sliders, progress, selections), good / warn / bad = status colours, dark = dark Windows title bar.
def _theme(name, dark, face, hot, text, hint, heading, field, light, light2, shadow, darkest,
           accent, accent_dark, good, warn, bad):
    return dict(name=name, dark=dark, face=face, hot=hot, text=text, hint=hint, heading=heading, field=field,
                light=light, light2=light2, shadow=shadow, darkest=darkest, accent=accent,
                accent_dark=accent_dark, good=good, warn=warn, bad=bad)


THEMES = {
    "classic": _theme("Classic", False, "#C0C0C0", "#CCCCCC", "#000000", "#333333", "#8E0E16", "#FFFFFF",
                      "#FFFFFF", "#DFDFDF", "#808080", "#000000", "#F3242F", "#7A0A12",
                      "#0F5A1E", "#6B3A00", "#8E0E16"),
    "midnight": _theme("Midnight", True, "#2A2D36", "#353945", "#F0F0F0", "#B3B7C2", "#FF7A82", "#14161C",
                       "#4E5361", "#3B3F4B", "#17191F", "#000000", "#F3242F", "#7A0A12",
                       "#6FCF7F", "#F0B050", "#FF7B72"),
    "ocean": _theme("Ocean", False, "#C0C0C0", "#CCCCCC", "#000000", "#333333", "#00006E", "#FFFFFF",
                    "#FFFFFF", "#DFDFDF", "#808080", "#000000", "#1084D0", "#000080",
                    "#0F5A1E", "#6B3A00", "#8E0E16"),
    "grape": _theme("Grape", False, "#CBC5DB", "#D6D1E3", "#000000", "#35304A", "#43207F", "#FFFFFF",
                    "#FFFFFF", "#E4E0EE", "#837C99", "#000000", "#8A5CE6", "#3B1E7A",
                    "#0F5A1E", "#6B3A00", "#8E0E16"),
    "bubblegum": _theme("Bubblegum", False, "#F0D3E1", "#F6E0EA", "#000000", "#4A2A3A", "#7A1442", "#FFFFFF",
                        "#FFFFFF", "#F9E9F1", "#B07F97", "#3A0A20", "#E0408A", "#7A1442",
                        "#0F5A1E", "#6B3A00", "#8E0E16"),
    "terminal": _theme("Terminal", True, "#0F1A12", "#16261A", "#7CFF8A", "#4FC25E", "#B6FFBE", "#000000",
                       "#2E4A33", "#22382A", "#060C07", "#000000", "#1FA83A", "#0B4D19",
                       "#7CFF8A", "#F0D050", "#FF7B72"),
    "amber": _theme("Amber", True, "#1E1810", "#2A2216", "#FFC861", "#D9A548", "#FFE0A0", "#0C0904",
                    "#4A3D24", "#372D1A", "#0A0805", "#000000", "#E89B12", "#7A4A00",
                    "#9BE07A", "#FFC861", "#FF8A6E"),
}
DEFAULT_THEME = "classic"
LEGACY_THEMES = {"light": "classic", "dark": "midnight"}     # the 1.0 Light / Dark choices
THEME_TILES = list(THEMES) + ["system", "custom"]            # the order of the tiles in Settings > Appearance
HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _rgb(c):
    return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)


def _hex(r, g, b):
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(round(v)))) for v in (r, g, b))


def mix(a, b, t):
    """The colour t of the way from a to b (0 = a, 1 = b)."""
    (r1, g1, b1), (r2, g2, b2) = _rgb(a), _rgb(b)
    return _hex(r1 + (r2 - r1) * t, g1 + (g2 - g1) * t, b1 + (b2 - b1) * t)


def luminance(c):
    def lin(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = _rgb(c)
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def readable_on(bg, color, toward):
    """Move `color` toward `toward` (black or white) until it reads well on `bg` (WCAG 4.5:1)."""
    for i in range(11):
        c = mix(color, toward, i / 10.0)
        if contrast(c, bg) >= 4.5:
            return c
    return toward


def custom_theme(accent, base):
    """The Custom theme: Classic or Midnight with the picked colour as the accent."""
    t = dict(THEMES["midnight" if base == "dark" else "classic"])
    t["name"] = "Custom"
    t["accent"] = accent
    t["accent_dark"] = mix(accent, "#000000", 0.5)
    t["heading"] = readable_on(t["face"], accent, "#FFFFFF" if t["dark"] else "#000000")
    return t


def load_bundled_fonts():
    """Make the fonts in assets/fonts usable by this process only (nothing is installed on the PC)."""
    if not IS_WIN:
        return False
    ok = False
    for name in FONT_FILES:
        f = resource_path("assets/fonts/" + name)
        if f is None:
            continue
        try:
            # FR_PRIVATE (0x10): only RTTS sees the font, and Windows forgets it when RTTS closes
            if ctypes.windll.gdi32.AddFontResourceExW(ctypes.c_wchar_p(str(f)), 0x10, 0) > 0:
                ok = True
        except Exception:
            log_error(traceback.format_exc())
    return ok


def resource_path(name):
    """Find a bundled file: next to the script/exe, or inside a PyInstaller build."""
    bases = [APP_DIR]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bases.append(Path(meipass))
    for base in bases:
        f = base / name
        if f.is_file():
            return f
    return None


def voice_label(voice_id):
    return "%s (%s)" % (voice_id[3:].capitalize(), VOICE_KIND.get(voice_id[:2], "?"))


def voice_display(label):
    """A voice label in RTTS's language: "Lessac (US female)" keeps the name and translates the part in brackets."""
    m = re.match(r"^(.*?) \((.*)\)$", label)
    if not m:
        return tr(label)
    return "%s (%s)" % (tr(m.group(1)), tr(m.group(2)))


def voice_lang(voice_id):
    """The phonemizer language Kokoro needs for one of its voices."""
    return KOKORO_ESPEAK.get(voice_id[:1], "en-us")


def log_error(text):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            f.write("[%s] RTTS %s\n%s\n\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), APP_VERSION, text))
    except Exception:
        pass


# --------------------------------------------------------------------------
#  Third-party libraries (installed by installer.bat)
# --------------------------------------------------------------------------
try:
    import numpy as np
    import sounddevice as sd
    from pynput import keyboard, mouse
except Exception as _exc:  # pragma: no cover - only hit on a broken install
    _r = tk.Tk()
    _r.withdraw()
    messagebox.showerror(
        APP_TITLE,
        "A required component is missing:\n\n%s\n\nRun installer.bat again." % _exc,
    )
    raise SystemExit(1)


# --------------------------------------------------------------------------
#  Windows helpers (foreground window, Caps Lock)
# --------------------------------------------------------------------------
if IS_WIN:
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32.GetForegroundWindow.argtypes = ()
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    _user32.GetWindowTextW.restype = ctypes.c_int
    _user32.GetKeyState.argtypes = (ctypes.c_int,)
    _user32.GetKeyState.restype = ctypes.c_short
    _user32.GetKeyboardLayout.argtypes = (wintypes.DWORD,)
    _user32.GetKeyboardLayout.restype = ctypes.c_void_p
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _user32.GetParent.argtypes = (wintypes.HWND,)
    _user32.GetParent.restype = wintypes.HWND
    _user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, wintypes.UINT)
    _user32.SetWindowPos.restype = wintypes.BOOL
    _user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
    _user32.GetAsyncKeyState.restype = ctypes.c_short
    _user32.OpenClipboard.argtypes = (wintypes.HWND,)
    _user32.OpenClipboard.restype = wintypes.BOOL
    _user32.CloseClipboard.argtypes = ()
    _user32.CloseClipboard.restype = wintypes.BOOL
    _user32.GetClipboardData.argtypes = (wintypes.UINT,)
    _user32.GetClipboardData.restype = wintypes.HANDLE
    _kernel32.GlobalLock.argtypes = (wintypes.HANDLE,)
    _kernel32.GlobalLock.restype = ctypes.c_void_p
    _kernel32.GlobalUnlock.argtypes = (wintypes.HANDLE,)
    _kernel32.GlobalUnlock.restype = wintypes.BOOL


def foreground_info():
    """Return (exe_name_lowercase, window_title) of the active window."""
    if not IS_WIN:
        return "", ""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return "", ""
    tbuf = ctypes.create_unicode_buffer(512)
    _user32.GetWindowTextW(hwnd, tbuf, 512)
    pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = ""
    handle = _kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if handle:
        try:
            size = wintypes.DWORD(1024)
            pbuf = ctypes.create_unicode_buffer(1024)
            if _kernel32.QueryFullProcessImageNameW(handle, 0, pbuf, ctypes.byref(size)):
                exe = os.path.basename(pbuf.value).lower()
        finally:
            _kernel32.CloseHandle(handle)
    return exe, tbuf.value


def roblox_active():
    if not IS_WIN:
        return True  # nothing to check against on other systems
    try:
        exe, title = foreground_info()
    except Exception:
        return False
    low = title.lower()
    if exe == "robloxplayerbeta.exe":
        return True
    if exe in ("windows10universal.exe", "applicationframehost.exe") and "roblox" in low:
        return True
    if exe == "" and title.strip() == "Roblox":   # exe name hidden from us
        return True
    return False


def caps_lock_on():
    if not IS_WIN:
        return False
    try:
        return bool(_user32.GetKeyState(0x14) & 1)
    except Exception:
        return False


def system_uses_dark():
    """True if Windows is set to dark mode for apps."""
    if not IS_WIN:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except Exception:
        return False


def set_titlebar_dark(win, dark):
    """Make the Windows title bar dark/light to match (Windows 10 1809+ / 11). Harmless elsewhere."""
    if not IS_WIN:
        return
    try:
        win.update_idletasks()
        hwnd = _user32.GetParent(win.winfo_id()) or win.winfo_id()
        value = ctypes.c_int(1 if dark else 0)
        dwm = ctypes.WinDLL("dwmapi")
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (20, older builds used 19)
            if dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), attr, ctypes.byref(value), 4) == 0:
                break
        # nudge Windows to repaint the frame: NOSIZE|NOMOVE|NOZORDER|NOACTIVATE|FRAMECHANGED
        _user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0037)
    except Exception:
        pass


def key_down(vk):
    """True while the given virtual key is physically held (Windows only)."""
    if not IS_WIN:
        return False
    try:
        return _user32.GetAsyncKeyState(vk) < 0
    except Exception:
        return False


def ctrl_only_down():
    """Ctrl held without Alt. AltGr shows up as Ctrl+Alt, and must still type characters."""
    return key_down(0x11) and not key_down(0x12)


def shift_down():
    return key_down(0x10)


def clipboard_text():
    """Plain text currently on the clipboard ('' if none)."""
    if not IS_WIN:
        return ""
    try:
        opened = False
        for _ in range(5):
            if _user32.OpenClipboard(None):
                opened = True
                break
            time.sleep(0.01)
        if not opened:
            return ""
        try:
            handle = _user32.GetClipboardData(13)  # CF_UNICODETEXT
            if not handle:
                return ""
            ptr = _kernel32.GlobalLock(handle)
            if not ptr:
                return ""
            try:
                return ctypes.wstring_at(ptr)
            finally:
                _kernel32.GlobalUnlock(handle)
        finally:
            _user32.CloseClipboard()
    except Exception:
        return ""


# --------------------------------------------------------------------------
#  PC check for large models (graphics card, memory, free space)
# --------------------------------------------------------------------------
GIB = 1024 ** 3
VRAM_GOOD_GB = 7.5             # "8 GB" cards report a little under 8 GiB
VRAM_MIN_GB = 3.5              # "4 GB" cards report a little under 4 GiB
RAM_GOOD_GB = 15               # "16 GB" PCs report a little under 16 GiB


def nvidia_gpus():
    """[(name, video_memory_bytes)] from the NVIDIA driver; [] if there is none.

    Windows' own WMI figure stops at 4 GB, so the real amount has to come from the driver (nvml.dll)."""
    if not IS_WIN:
        return []
    nv = None
    for dll in ("nvml.dll", os.path.join(os.environ.get("ProgramW6432") or r"C:\Program Files",
                                         "NVIDIA Corporation", "NVSMI", "nvml.dll")):
        try:
            nv = ctypes.WinDLL(dll)
            break
        except OSError:
            continue
    if nv is None:
        return []
    try:
        if nv.nvmlInit_v2() != 0:
            return []
    except Exception:
        return []
    try:
        dev = ctypes.c_void_p
        mem_t = ctypes.c_ulonglong * 3                 # nvmlMemory_t: total, free, used
        nv.nvmlDeviceGetCount_v2.argtypes = (ctypes.POINTER(ctypes.c_uint),)
        nv.nvmlDeviceGetHandleByIndex_v2.argtypes = (ctypes.c_uint, ctypes.POINTER(dev))
        nv.nvmlDeviceGetName.argtypes = (dev, ctypes.c_char_p, ctypes.c_uint)
        nv.nvmlDeviceGetMemoryInfo.argtypes = (dev, ctypes.POINTER(mem_t))
        count = ctypes.c_uint(0)
        if nv.nvmlDeviceGetCount_v2(ctypes.byref(count)) != 0:
            return []
        found = []
        for i in range(count.value):
            h, name, mem = dev(), ctypes.create_string_buffer(96), mem_t()
            if (nv.nvmlDeviceGetHandleByIndex_v2(i, ctypes.byref(h)) == 0
                    and nv.nvmlDeviceGetName(h, name, 96) == 0
                    and nv.nvmlDeviceGetMemoryInfo(h, ctypes.byref(mem)) == 0):
                found.append((name.value.decode("utf-8", "replace"), int(mem[0])))
        return found
    except Exception:
        log_error(traceback.format_exc())
        return []
    finally:
        try:
            nv.nvmlShutdown()
        except Exception:
            pass


def display_adapters():
    """[(name, video_memory_bytes)] for every graphics card Windows has a driver for (any brand)."""
    if not IS_WIN:
        return []
    import winreg
    found = []
    path = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as cls:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(cls, i)
                except OSError:
                    break
                i += 1
                if not sub.isdigit():                 # e.g. "Properties", which isn't an adapter
                    continue
                try:
                    with winreg.OpenKey(cls, sub) as k:
                        name = str(winreg.QueryValueEx(k, "DriverDesc")[0])
                        mem = 0
                        for value in ("HardwareInformation.qwMemorySize", "HardwareInformation.MemorySize"):
                            try:
                                v = winreg.QueryValueEx(k, value)[0]
                            except OSError:
                                continue
                            mem = int.from_bytes(v[:8], "little") if isinstance(v, bytes) else int(v)
                            if mem:
                                break
                        found.append((name, mem))
                except OSError:
                    continue
    except OSError:
        pass
    return found


def total_ram():
    """Installed memory (RAM) in bytes, 0 if unknown."""
    if not IS_WIN:
        return 0

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD)] + [
            (n, ctypes.c_ulonglong) for n in ("ullTotalPhys", "ullAvailPhys", "ullTotalPageFile", "ullAvailPageFile",
                                              "ullTotalVirtual", "ullAvailVirtual", "ullAvailExtendedVirtual")]
    st = MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(st)
    try:
        return int(st.ullTotalPhys) if _kernel32.GlobalMemoryStatusEx(ctypes.byref(st)) else 0
    except Exception:
        return 0


_GPU = []


def best_gpu():
    """The graphics card a large model would run on: dict(name, vram, nvidia). Checked once per run."""
    if not _GPU:
        nv = nvidia_gpus()
        if nv:
            name, vram = max(nv, key=lambda g: g[1])
            _GPU.append(dict(name=name, vram=vram, nvidia=True))
        else:
            cards = [a for a in display_adapters() if not a[0].lower().startswith("microsoft")]   # basic / remote display
            name, vram = max(cards, key=lambda a: a[1]) if cards else ("", 0)
            _GPU.append(dict(name=name, vram=vram, nvidia=False))
    return _GPU[0]


Check = namedtuple("Check", "level text detail")      # level: "ok", "warn" or "bad"


def large_model_check(entry):
    """The PC check shown in the large-model popup: ([Check, ...], bytes still to download, can_download)."""
    todo = [r for r in entry_files(entry, "") if not remote_ready(r)]
    download = sum(r.size for r in todo)
    checks = []

    gpu = best_gpu()
    vram = gpu["vram"] / GIB
    card = gpu["name"] or tr("No graphics card found")
    if gpu["name"] and gpu["vram"]:
        card += " \u00b7 " + tr("%d GB video memory") % max(1, round(vram))
    if gpu["nvidia"] and vram >= VRAM_GOOD_GB:
        checks.append(Check("ok", tr("Your GPU is powerful enough"), card))
    else:
        if gpu["nvidia"] and vram >= VRAM_MIN_GB:
            why = tr("It has less than the 8 GB of video memory this model needs to run smoothly.")
        elif gpu["nvidia"]:
            why = tr("It has less than 4 GB of video memory, so most of the model would run on your processor, "
                     "which is much slower.")
        else:
            why = tr("Large models run best on an NVIDIA graphics card. On other cards they can be much slower.")
        checks.append(Check("warn", tr("Your GPU might not be powerful enough to run this model"), card + "\n" + why))

    ram = total_ram() / GIB
    if ram and ram < RAM_GOOD_GB:
        checks.append(Check("warn", tr("Your PC has less memory (RAM) than recommended"),
                            tr("%d GB RAM \u00b7 16 GB recommended") % max(1, round(ram))))
    elif ram:
        checks.append(Check("ok", tr("Your PC has enough memory (RAM)"), tr("%d GB RAM") % round(ram)))

    # a .zip needs room for itself and for what comes out of it while it is unpacked
    need = sum(r.size * (3 if r.unpack else 1) for r in todo) + 200 * 1000000
    try:
        where = MODEL_DIR if MODEL_DIR.exists() else APP_DIR
        free = shutil.disk_usage(str(where)).free
    except OSError:
        free = None
    drive = os.path.splitdrive(str(APP_DIR))[0] or str(APP_DIR)
    if free is not None and free < need:
        checks.append(Check("bad", tr("Not enough free space"),
                            tr("Needs %s on %s, but only %s is free. Free up some space, then pick this model "
                               "again.") % (size_text(need / 1e6), drive, size_text(free / 1e6))))
    elif free is not None:
        checks.append(Check("ok", tr("Enough free space"), tr("%s free on %s") % (size_text(free / 1e6), drive)))
    return checks, download, not any(c.level == "bad" for c in checks)


# --------------------------------------------------------------------------
#  Voice model files
# --------------------------------------------------------------------------
def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(1 << 20)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


class DownloadCancelled(Exception):
    """Raised inside a download when the user has already picked another model."""


def download_file(url, dest, expected_size, expected_sha, progress):
    part = Path(str(dest) + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp, open(part, "wb") as out:
            announced = int(resp.headers.get("Content-Length") or 0)
            total = announced or expected_size or 0
            done = 0
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                progress(done, total)
        want = expected_size or announced
        if want and part.stat().st_size != want:
            raise IOError(tr("Download was incomplete (%d of %d bytes).") % (part.stat().st_size, want))
        if expected_sha and sha256_of(part) != expected_sha:
            raise IOError(tr("Downloaded file is corrupted (checksum mismatch). Please try again."))
        os.replace(part, dest)
    except BaseException:
        try:
            part.unlink()
        except Exception:
            pass
        raise


def unpack_marker(r):
    """Written into the folder once a .zip download has been fully unpacked there."""
    return MODEL_DIR / r.unpack / (r.name + ".ok")


def unpack_zip(r):
    """Extract the downloaded .zip `r` into models\\<r.unpack>, mark it complete, then delete the .zip."""
    import zipfile
    zip_path = MODEL_DIR / r.name
    folder = MODEL_DIR / r.unpack
    folder.mkdir(parents=True, exist_ok=True)
    root = folder.resolve()
    with zipfile.ZipFile(zip_path) as z:
        for member in z.infolist():                 # never write outside the folder
            target = (folder / member.filename).resolve()
            if target != root and root not in target.parents:
                raise IOError("%s contains an unsafe path: %s" % (r.name, member.filename))
        z.extractall(folder)
    unpack_marker(r).write_text(r.sha or "", encoding="utf-8")
    zip_path.unlink()


def remote_ready(r):
    """True if this file is already on disk (files only appear under their final name once complete)."""
    if r.unpack:
        return unpack_marker(r).is_file()
    try:
        n = (MODEL_DIR / r.name).stat().st_size
    except OSError:
        return False
    return n == r.size if r.size else n > 0


def files_ready(files):
    return bool(files) and all(remote_ready(r) for r in files)


def ensure_files(files, label, status, prog, cancelled=None, speed=None):
    """Download whichever of `files` are missing and report ONE overall percentage for all of them.

    status(text) gets a human message, prog(percent) the 0-100 number, and speed(text) the current
    download speed such as "4.2 MB/s" (and "" when the download is over). Returns the local paths."""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    paths = [MODEL_DIR / r.name for r in files]
    todo = [r for r in files if not remote_ready(r)]
    if not todo:
        return paths
    grand = max(1, sum(r.size or r.approx for r in todo))
    finished = 0
    last = [0.0]
    samples = []              # (time, bytes received) over the last ~1.5 s, for the MB/s readout
    received = [0]            # bytes actually received for the files that are already finished
    status(tr("Downloading %s ...") % label)
    prog(0)
    for r in todo:
        weight = r.size or r.approx
        seen = [0]            # bytes received so far for this file

        def progress(done, total, base=finished, seen=seen):
            if cancelled is not None and cancelled():
                raise DownloadCancelled()
            seen[0] = done
            now = time.time()
            if now - last[0] < 0.2 and not (total and done >= total):
                return
            last[0] = now
            got = min(grand, base + done)
            pct = min(99, int(got * 100 // grand))
            status(tr("Downloading %s: %d%% (%.0f of %.0f MB)") % (label, pct, got / 1e6, grand / 1e6))
            prog(pct)
            if speed is not None:
                have = received[0] + done
                samples.append((now, have))
                while len(samples) > 1 and now - samples[0][0] > 1.5:
                    samples.pop(0)
                t0, b0 = samples[0]
                if len(samples) > 1 and now - t0 >= 0.3:
                    speed("%.1f MB/s" % ((have - b0) / (now - t0) / 1e6))

        zip_path = MODEL_DIR / r.name
        if not (r.unpack and zip_path.is_file() and zip_path.stat().st_size == r.size
                and (not r.sha or sha256_of(zip_path) == r.sha)):     # a finished .zip that wasn't unpacked yet
            download_file(r.url, zip_path, r.size, r.sha, progress)
        if r.unpack:
            status(tr("Unpacking %s ...") % label)
            unpack_zip(r)
        finished += weight
        received[0] += seen[0]
    prog(100)
    if speed is not None:
        speed("")
    return paths


# --------------------------------------------------------------------------
#  Audio output helpers
# --------------------------------------------------------------------------
def mme_index():
    try:
        for i, api in enumerate(sd.query_hostapis()):
            if "MME" in api["name"]:
                return i
    except Exception:
        pass
    return None


def list_outputs():
    """Return ([(device_index, name), ...], default_device_index_or_None)."""
    mme = mme_index()
    outs = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] > 0 and (mme is None or d["hostapi"] == mme):
            outs.append((i, d["name"]))
    default = None
    try:
        if mme is not None:
            default = sd.query_hostapis(mme)["default_output_device"]
        if default is None or default < 0:
            default = sd.default.device[1]
        if default is None or default < 0:
            default = None
    except Exception:
        default = None
    return outs, default


def find_index(names, needle):
    if not needle:
        return -1
    needle = needle.lower()
    for i, n in enumerate(names):
        if needle in n.lower():
            return i
    return -1


def resample_linear(audio, sr_from, sr_to):
    if sr_from == sr_to:
        return audio
    n_out = int(round(len(audio) * sr_to / sr_from))
    x_old = np.linspace(0.0, 1.0, num=len(audio), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    return np.interp(x_new, x_old, audio).astype(np.float32)


# --------------------------------------------------------------------------
#  Updates (checked against this repo's latest GitHub release every time RTTS starts)
# --------------------------------------------------------------------------
def version_tuple(v):
    """'v1.2.3' / '1.2.3' -> (1, 2, 3), so versions compare as numbers."""
    return tuple(int(n) for n in re.findall(r"\d+", v or "")[:3])


def fetch_latest_release():
    """The newest published release: {version, url, asset (name, url, size, sha) or None}. None if there is none."""
    req = urllib.request.Request(LATEST_RELEASE_API, headers={"User-Agent": USER_AGENT,
                                                              "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:                       # no release published yet
            return None
        raise
    asset = None
    for a in data.get("assets") or ():
        if re.fullmatch(r"RTTS-Setup-[\w.\-]+\.exe", a.get("name") or ""):
            digest = a.get("digest") or ""
            sha = digest[7:] if digest.startswith("sha256:") else None
            asset = (a["name"], a["browser_download_url"], int(a.get("size") or 0), sha)
            break
    return {"version": (data.get("tag_name") or "").lstrip("vV"), "url": data.get("html_url") or RELEASES_URL,
            "asset": asset}


# --------------------------------------------------------------------------
#  Anonymous voice stats (only if the user turned them on)
# --------------------------------------------------------------------------
STATS_SESSION = uuid.uuid4().hex      # random per launch, never saved, so launches can't be linked


def send_voice_stat(model_id, voice):
    """Tell PostHog which voice is being used. Only the model and voice are sent; failures are ignored."""
    if not STATS_API_KEY:
        return
    body = {
        "api_key": STATS_API_KEY,
        "event": "voice_used",
        "distinct_id": STATS_SESSION,
        "properties": {
            "model": model_id,
            "voice": voice,
            "$process_person_profile": False,   # no person profile: just an anonymous event
            "$geoip_disable": True,             # don't look up a location from the IP address
        },
    }
    req = urllib.request.Request(STATS_HOST + "/i/v0/e/", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "User-Agent": "RTTS"})
    try:
        urllib.request.urlopen(req, timeout=10).close()
    except Exception:
        pass


def strip_emojis(text):
    """Remove emojis (with their skin tones, joiners and variation selectors), keeping plain symbols like © and °."""
    out = []
    for ch in text:
        cp = ord(ch)
        if (cp >= 0x2000 and unicodedata.category(ch) in ("So", "Sk")) \
                or cp in (0x200D, 0x20E3, 0xFE0E, 0xFE0F) or 0xE0020 <= cp <= 0xE007F:
            continue
        out.append(ch)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def parse_chat_key(text):
    """Turn what the user typed into ('char', 'x') or ('key', Key.xxx); None if unknown."""
    t = (text or "").strip()
    if not t:
        return None
    if len(t) == 1:
        return ("char", t)
    name = t.lower().replace(" ", "_")
    k = keyboard.Key.__members__.get(name)
    if k is not None and name not in ("shift", "ctrl", "alt", "cmd"):
        return ("key", k)
    return None


# Start / Stop hotkeys work in every program and still reach it, so they can't be keys you type, move or
# edit chat with. Keys Roblox itself uses are allowed, but only after a warning.
HOTKEY_BLOCKED = {
    "shift", "shift_l", "shift_r", "ctrl", "ctrl_l", "ctrl_r", "alt", "alt_l", "alt_r", "alt_gr",
    "cmd", "cmd_l", "cmd_r", "caps_lock",
    "enter", "esc", "space", "backspace", "delete", "left", "right", "up", "down", "home", "end",
}
ROBLOX_KEYS = {
    "tab": "shows the player list",
    "page_up": "moves the camera",
    "page_down": "moves the camera",
    "f9": "opens the Developer Console",
    "f11": "switches full screen on and off",
}
DEFAULT_HOTKEYS = {"start": "f6", "stop": "f7"}

# Where a chat-key character sits on a US keyboard (scan codes), to find that key under a non-Latin layout
US_SCAN_CODES = {"/": 0x35, "\\": 0x2B, ";": 0x27, "'": 0x28, "`": 0x29, "-": 0x0C, "=": 0x0D, "[": 0x1A,
                 "]": 0x1B, ",": 0x33, ".": 0x34}
US_SCAN_CODES.update(zip("qwertyuiop", range(0x10, 0x1A)))
US_SCAN_CODES.update(zip("asdfghjkl", range(0x1E, 0x27)))
US_SCAN_CODES.update(zip("zxcvbnm", range(0x2C, 0x33)))
# Primary language ids of keyboard layouts that don't type Latin letters: Arabic, Bulgarian, Chinese, Greek,
# Hebrew, Japanese, Korean, Russian, Thai, Persian, Ukrainian, Belarusian, Hindi, Bengali, Tamil, Kazakh ...
NON_LATIN_LANGUAGE_IDS = {0x01, 0x02, 0x04, 0x08, 0x0D, 0x11, 0x12, 0x19, 0x1E, 0x29, 0x22, 0x23, 0x39, 0x45,
                          0x46, 0x47, 0x49, 0x4A, 0x4B, 0x4E, 0x3F, 0x28, 0x2F, 0x40, 0x44, 0x50}


def hotkey_name(key):
    """The settings name of a Start / Stop key (e.g. "f6"), or None if the key can't be one."""
    if isinstance(key, keyboard.Key) and key.name not in HOTKEY_BLOCKED:
        return key.name
    return None


def key_display(key):
    """How a key is written in the window: "F6", "Page Up", or the character it types."""
    name = getattr(key, "name", None)
    if not name:
        ch = getattr(key, "char", None)
        return ch.upper() if ch and ch.strip() else tr("That key")
    if re.fullmatch(r"f\d+", name):
        return name.upper()
    return name.replace("_", " ").title()


# --------------------------------------------------------------------------
#  Voice engines (one class per kind of model in the catalog)
# --------------------------------------------------------------------------
SYNTH_LOCK = threading.Lock()   # espeak-ng keeps global state, so only one thing may synthesize at a time


class CorruptModel(Exception):
    """A downloaded model file could not be read (so it should be downloaded again)."""


def setup_espeak():
    """Point phonemizer at the espeak-ng that ships with espeakng_loader (what kokoro-onnx does too)."""
    import espeakng_loader
    from phonemizer.backend.espeak.wrapper import EspeakWrapper
    EspeakWrapper.set_data_path(espeakng_loader.get_data_path())
    EspeakWrapper.set_library(espeakng_loader.get_library_path())


class Engine:
    """A loaded voice engine. synth() returns (mono float32 audio, sample rate)."""

    def voice_list(self):
        return []

    def synth(self, text, voice, speed):
        raise NotImplementedError

    def speak(self, text, voice, speed):
        with SYNTH_LOCK:
            return self.synth(text, voice, speed)

    def close(self):
        """Free what the engine holds once another model replaces it (most engines hold nothing extra)."""

    @property
    def voice_ids(self):
        return [v for v, _label in self.voice_list()]

    @property
    def default_voice(self):
        ids = self.voice_ids
        return ids[0] if ids else ""


class KokoroEngine(Engine):
    def __init__(self, model_path, voices_path):
        from kokoro_onnx import Kokoro
        self.k = Kokoro(str(model_path), str(voices_path))
        self.ids = set(self.k.get_voices())
        try:  # warm-up so the first real message isn't slow
            self.speak("Ready.", DEFAULT_VOICE if DEFAULT_VOICE in self.ids else sorted(self.ids)[0], 1.0)
        except Exception:
            log_error(traceback.format_exc())

    def voice_list(self):
        got = [(v, voice_label(v)) for v in VOICE_ORDER if v in self.ids]
        return got or [(DEFAULT_VOICE, voice_label(DEFAULT_VOICE))]

    def synth(self, text, voice, speed):
        if voice not in self.ids:
            voice = DEFAULT_VOICE
        return self.k.create(text, voice=voice, speed=speed, lang=voice_lang(voice))


class EspeakRaw:
    """Tiny ctypes wrapper that turns text into audio with espeak-ng (the DLL bundled for phonemizer)."""
    _CB = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(ctypes.c_short), ctypes.c_int, ctypes.c_void_p)

    def __init__(self, lib_path, data_path):
        # Like phonemizer, use a private copy of the library: espeak-ng keeps global state, and a copy
        # can't disturb the instances phonemizer / kokoro / piper are using.
        self.tmp = tempfile.mkdtemp(prefix="rtts-espeak-")
        atexit.register(shutil.rmtree, self.tmp, True)
        copy = os.path.join(self.tmp, os.path.basename(lib_path))
        shutil.copy(lib_path, copy)
        self.lib = lib = ctypes.cdll.LoadLibrary(copy)
        lib.espeak_Initialize.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        lib.espeak_Initialize.restype = ctypes.c_int
        lib.espeak_SetSynthCallback.argtypes = [self._CB]
        lib.espeak_SetSynthCallback.restype = None
        lib.espeak_SetVoiceByName.argtypes = [ctypes.c_char_p]
        lib.espeak_SetVoiceByName.restype = ctypes.c_int
        lib.espeak_SetParameter.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
        lib.espeak_SetParameter.restype = ctypes.c_int
        lib.espeak_Synth.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_uint, ctypes.c_int,
                                     ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        lib.espeak_Synth.restype = ctypes.c_int
        lib.espeak_Synchronize.argtypes = []
        lib.espeak_Synchronize.restype = ctypes.c_int
        rate = lib.espeak_Initialize(0x02, 0, data_path.encode("utf-8"), 0)   # 0x02 = AUDIO_OUTPUT_SYNCHRONOUS
        if rate <= 0:
            raise RuntimeError("espeak-ng could not start (code %d)" % rate)
        self.rate = rate
        self.chunks = []
        self._cb = self._CB(self._on_audio)      # keep a reference so it isn't garbage collected
        lib.espeak_SetSynthCallback(self._cb)

    def _on_audio(self, wav, count, _events):
        if wav and count > 0:
            self.chunks.append(np.frombuffer(ctypes.string_at(wav, count * 2), dtype=np.int16))
        return 0                                  # 0 = keep going

    def synth(self, text, voice, wpm):
        self.chunks = []
        if self.lib.espeak_SetVoiceByName(voice.encode("utf-8")) != 0:
            raise RuntimeError("eSpeak doesn't know the voice %r" % voice)
        self.lib.espeak_SetParameter(1, int(wpm), 0)                    # 1 = espeakRATE (words per minute)
        data = text.encode("utf-8")
        err = self.lib.espeak_Synth(data, len(data) + 1, 0, 1, 0, 1, None, None)  # 1 = character positions / UTF-8
        if err != 0:
            raise RuntimeError("eSpeak couldn't speak that (error %d)" % err)
        self.lib.espeak_Synchronize()
        if not self.chunks:
            raise ValueError("eSpeak produced no audio")
        pcm = np.concatenate(self.chunks).astype(np.float32) / 32768.0
        self.chunks = []
        return pcm, self.rate


_ESPEAK_RAW = []


def espeak_raw():
    if not _ESPEAK_RAW:
        import espeakng_loader
        _ESPEAK_RAW.append(EspeakRaw(espeakng_loader.get_library_path(), espeakng_loader.get_data_path()))
    return _ESPEAK_RAW[0]


class ESpeakEngine(Engine):
    def __init__(self):
        self.raw = espeak_raw()

    def voice_list(self):
        return [(v[0], v[1]) for v in ESPEAK_VOICES]

    def synth(self, text, voice, speed):
        if voice not in {v[0] for v in ESPEAK_VOICES}:
            voice = ESPEAK_VOICES[0][0]
        pcm, rate = self.raw.synth(text, voice, min(450, max(80, int(175 * speed))))
        peak = float(np.max(np.abs(pcm))) if pcm.size else 0.0
        if peak > 0:
            pcm = pcm * (0.8 / peak)              # eSpeak is quiet; bring it level with the neural voices
        return pcm, rate


class PiperEngine(Engine):
    """Runs one Piper voice (.onnx + .onnx.json) with onnxruntime; text -> espeak phonemes -> ids -> audio."""

    def __init__(self, voice_id, label, model_path, config_path):
        import onnxruntime as ort
        self.vid, self.label = voice_id, label
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.rate = int(cfg["audio"]["sample_rate"])
            self.id_map = cfg["phoneme_id_map"]
            inf = cfg.get("inference") or {}
            self.noise = float(inf.get("noise_scale", 0.667))
            self.length = float(inf.get("length_scale", 1.0))
            self.noise_w = float(inf.get("noise_w", 0.8))
            self.espeak_voice = (cfg.get("espeak") or {}).get("voice") or "en-us"
            if (cfg.get("phoneme_type") or "espeak") != "espeak":
                raise ValueError("unsupported phoneme type %r" % cfg.get("phoneme_type"))
            opts = ort.SessionOptions()
            opts.log_severity_level = 3
            self.sess = ort.InferenceSession(str(model_path), sess_options=opts,
                                             providers=["CPUExecutionProvider"])
        except Exception as e:
            raise CorruptModel(str(e)) from e
        self.has_sid = "sid" in {i.name for i in self.sess.get_inputs()}
        setup_espeak()
        from phonemizer.backend import EspeakBackend
        self.backend = None
        fallback = "en-gb" if self.espeak_voice.startswith("en-gb") else "en-us"
        for lang in (self.espeak_voice, fallback):
            try:
                self.backend = EspeakBackend(lang, preserve_punctuation=True, with_stress=True,
                                             language_switch="remove-flags")
                break
            except Exception:
                log_error(traceback.format_exc())
        if self.backend is None:
            raise RuntimeError("Piper couldn't start its phonemizer (espeak-ng).")
        try:  # warm-up so the first real message isn't slow
            self.speak("Ready.", voice_id, 1.0)
        except Exception:
            log_error(traceback.format_exc())

    def voice_list(self):
        return [(self.vid, self.label)]

    def _ids(self, sentence):
        """Phoneme ids for one sentence, or None if there is nothing to say."""
        raw = self.backend.phonemize([sentence], strip=False)[0]
        phonemes = unicodedata.normalize("NFD", raw).strip()
        m = self.id_map
        ids = list(m["^"])
        spoken = 0
        for p in phonemes:
            if p in m:
                ids.extend(m[p])
                ids.extend(m["_"])
                if p.isalpha():
                    spoken += 1
        ids.extend(m["$"])
        return ids if spoken else None

    def _run(self, ids, speed):
        x = np.asarray([ids], dtype=np.int64)
        feed = {"input": x,
                "input_lengths": np.asarray([x.shape[1]], dtype=np.int64),
                "scales": np.asarray([self.noise, self.length / max(0.25, speed), self.noise_w],
                                     dtype=np.float32)}
        if self.has_sid:
            feed["sid"] = np.asarray([0], dtype=np.int64)
        a = np.asarray(self.sess.run(None, feed)[0], dtype=np.float32).reshape(-1)
        peak = float(np.max(np.abs(a))) if a.size else 0.0
        return a * (0.95 / max(0.01, peak)) if peak > 0 else a

    def synth(self, text, voice, speed):
        gap = np.zeros(int(self.rate * 0.12), dtype=np.float32)
        parts = []
        for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
            if not sentence.strip():
                continue
            ids = self._ids(sentence)
            if ids is None:
                continue
            if parts:
                parts.append(gap)
            parts.append(self._run(ids, speed))
        if not parts:
            raise ValueError("nothing speakable")
        return np.concatenate(parts), self.rate


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def end_with_rtts(proc):
    """Put a child process in a Windows job object that ends it when RTTS ends, even if RTTS crashes.

    Returns the job handle (keep it open for as long as the child should live), or None."""
    if not IS_WIN:
        return None
    try:
        k = _kernel32

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", ctypes.c_ulonglong * 6),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        k.SetInformationJobObject.restype = wintypes.BOOL
        k.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        k.AssignProcessToJobObject.restype = wintypes.BOOL
        job = k.CreateJobObjectW(None, None)
        if not job:
            return None
        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if (k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))   # 9 = extended limits
                and k.AssignProcessToJobObject(job, int(proc._handle))):
            return job
        k.CloseHandle(job)
    except Exception:
        log_error(traceback.format_exc())
    return None


class OrpheusEngine(Engine):
    """Orpheus 3B, a Llama-based speech model. llama.cpp's llama-server (a separate program that RTTS starts and
    stops) writes it as sound codes, and the SNAC decoder turns those into 24 kHz audio.

    It streams: stream() hands out the first bit of sound about half a second after it starts, and the rest while
    Orpheus is still writing it, so long messages don't keep everyone waiting."""
    RATE = 24000
    FRAME = 2048                      # samples of audio per frame (7 sound codes)
    START_HUMAN, BOS, EOT, END_HUMAN, START_AI, START_SPEECH = 128259, 128000, 128009, 128260, 128261, 128257
    END_SPEECH_ID = 128258
    END_SPEECH = "<custom_token_2>"   # token 128258, written out like this in the server's text
    AUDIO_BASE = 128266               # <custom_token_10>, the first sound code; 7 codes (one per slot) make a frame
    # Streamed decoding: 4 new frames at a time, with 4 frames before and 2 after as context. Measured: the result
    # differs from decoding the whole message at once no more than two whole decodes differ (SNAC adds a little noise).
    BLOCK, BEFORE, AFTER = 4, 4, 2
    # Sampling as Orpheus was tuned for, with top_k first: picking from 50 instead of all 156,000 tokens is what makes
    # it faster than real time on an RTX 4060 Ti (~100 tokens/s, and it needs ~86).
    SAMPLING = {"temperature": 0.6, "top_p": 0.8, "top_k": 50, "repeat_penalty": 1.1, "repeat_last_n": 64,
                "samplers": ["penalties", "top_k", "top_p", "temperature"]}
    LOG = DATA_DIR / "orpheus-server.log"
    streams = True

    def __init__(self, model_path, decoder_path, server_dir, status, cancelled):
        import onnxruntime as ort
        self.proc = None
        self.job = None
        self.closed = False
        self.ratio = 1.0              # seconds of speech made per second while streaming (updated every message)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # localhost: never via a proxy
        try:
            opts = ort.SessionOptions()
            opts.log_severity_level = 3
            opts.intra_op_num_threads = min(4, os.cpu_count() or 4)   # more threads is slower on P-core/E-core CPUs
            self.decoder = ort.InferenceSession(str(decoder_path), sess_options=opts,
                                                providers=["CPUExecutionProvider"])
        except Exception as e:
            raise CorruptModel(str(e)) from e
        self.exe = next(iter(sorted(Path(server_dir).rglob("llama-server.exe"))), None)
        if self.exe is None:
            raise RuntimeError(tr("llama-server.exe is missing from %s.") % server_dir)
        self.model_path = model_path
        self.extra = []
        atexit.register(self.close)
        status(tr("Starting Orpheus on your graphics card ..."))
        try:
            try:
                self._start(cancelled)
            except DownloadCancelled:
                raise
            except Exception:
                log_error(traceback.format_exc())
                status(tr("Orpheus couldn't use your graphics card, starting it on your processor (much slower) ..."))
                self.extra = ["-ngl", "0"]
                self._start(cancelled)
        except BaseException:
            self.close()                  # never leave a half-started llama-server behind
            raise
        status(tr("Warming up Orpheus ..."))
        try:  # warm-up so the first real message isn't slow (and to measure how fast this PC runs it)
            self.speak("Hello there, this is a quick test.", ORPHEUS_VOICES[0][0], 1.0)
        except Exception:
            log_error(traceback.format_exc())

    def _start(self, cancelled):
        """Start llama-server and wait until the model is loaded. With no -ngl it fits as much as it can on the GPU."""
        self._stop()
        self.url = "http://127.0.0.1:%d" % free_port()
        args = [str(self.exe), "-m", str(self.model_path), "--host", "127.0.0.1", "--port", self.url.rsplit(":", 1)[1],
                "-c", "4096", "-np", "1", "--no-webui"] + self.extra
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(self.LOG, "a" if self.extra else "w", encoding="utf-8", errors="replace") as log:   # keep a GPU try
            self.proc = subprocess.Popen(args, cwd=str(self.exe.parent), stdin=subprocess.DEVNULL, stdout=log,
                                         stderr=subprocess.STDOUT, creationflags=0x08000000)   # CREATE_NO_WINDOW
        self.job = end_with_rtts(self.proc)
        deadline = time.time() + 300
        while True:
            if cancelled():
                self.close()
                raise DownloadCancelled()
            if self.proc.poll() is not None:
                raise RuntimeError(tr("Orpheus stopped while it was loading (code %s). Details are in %s")
                                   % (self.proc.returncode, self.LOG))
            try:
                # a short timeout: on Windows a connection to a port nobody listens on yet can hang until it runs out
                with self.opener.open(self.url + "/health", timeout=0.5) as r:
                    if r.status == 200:
                        return
            except (urllib.error.URLError, OSError):     # not listening yet, or 503 while the model loads
                pass
            if time.time() > deadline:
                raise RuntimeError(tr("Orpheus took too long to start. Details are in %s") % self.LOG)
            time.sleep(0.1)

    def _ensure_running(self):
        """Start llama-server again if it stopped on its own (it is a separate program, so it can crash)."""
        if self.proc is None or self.proc.poll() is not None:
            log_error("Orpheus's llama-server had stopped (code %s); starting it again."
                      % (self.proc.returncode if self.proc else "-"))
            self._start(lambda: self.closed)

    def _failed(self, e):
        if self.closed:
            return RuntimeError(tr("Orpheus was switched off."))
        if self.proc is None or self.proc.poll() is not None:
            return RuntimeError(tr("Orpheus stopped working. It will start again with the next message."))
        return RuntimeError(tr("Couldn't get speech from Orpheus: %s") % e)

    def _open(self, path, body, timeout):
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        try:
            return self.opener.open(req, timeout=timeout)
        except (urllib.error.URLError, OSError) as e:
            raise self._failed(e)

    def _post(self, path, body, timeout=60):
        with self._open(path, body, timeout) as r:
            try:
                return json.loads(r.read().decode("utf-8"))
            except (OSError, ValueError) as e:
                raise self._failed(e)

    def voice_list(self):
        return list(ORPHEUS_VOICES)

    def synth(self, text, voice, speed):
        return np.concatenate(list(self.stream(text, voice, speed))), self.RATE

    def stream(self, text, voice, speed, cancelled=lambda: False):
        """Yield the speech as pieces of float32 audio (24 kHz) while Orpheus is still writing it."""
        if self.closed:
            raise RuntimeError(tr("Orpheus was switched off."))
        self._ensure_running()
        if voice not in dict(ORPHEUS_VOICES):
            voice = ORPHEUS_VOICES[0][0]
        # parse_special=False: typed text that looks like a control token (e.g. "<|eot_id|>") stays plain text
        words = self._post("/tokenize", {"content": "%s: %s" % (voice, text), "add_special": False,
                                         "parse_special": False})["tokens"]
        prompt = [self.START_HUMAN, self.BOS] + words + [self.EOT, self.END_HUMAN, self.START_AI, self.START_SPEECH]
        body = dict(self.SAMPLING, prompt=prompt, n_predict=min(2400, 400 + 10 * len(text)), stop=[self.END_SPEECH],
                    stream=True, return_tokens=True, cache_prompt=True)
        frames, frame, done = [], [], 0
        first = last = None                       # when the first / latest frame arrived
        first_n = 0
        resp = self._open("/completion", body, timeout=60)
        try:
            ended = False
            while not ended:
                try:
                    line = resp.readline()
                except OSError as e:
                    raise self._failed(e)
                if not line:
                    break
                if cancelled():
                    return
                if not line.startswith(b"data: "):
                    continue
                try:
                    d = json.loads(line[6:])
                except ValueError:
                    continue
                for t in d.get("tokens") or ():
                    if t == self.END_SPEECH_ID:
                        ended = True
                        break
                    if self._take(t, frame, frames):
                        last = time.time()
                        if first is None:
                            first, first_n = last, len(frames)
                while len(frames) - done >= self.BLOCK + self.AFTER:
                    yield self._span(frames, done, done + self.BLOCK)
                    done += self.BLOCK
                if d.get("stop"):
                    break
        finally:
            resp.close()                          # also tells the server to stop if we gave up early
        if cancelled():
            return
        if not frames:
            raise ValueError("Orpheus produced no speech")
        if done < len(frames):
            yield self._span(frames, done, len(frames))
        if first is not None and last > first and len(frames) - first_n >= 8:
            measured = (len(frames) - first_n) * self.FRAME / float(self.RATE) / (last - first)
            self.ratio = 0.6 * self.ratio + 0.4 * measured

    def _take(self, t, frame, frames):
        """Add one generated token. Every 7 sound codes make a frame; returns True when a frame is complete.

        Each of the 7 slots has its own range of 4096 token ids. A code that doesn't fit the slot it would go in is
        skipped, so one stray token can't shift every frame after it."""
        if t < self.AUDIO_BASE:
            return False
        code = t - self.AUDIO_BASE - len(frame) * 4096
        if not 0 <= code < 4096:
            return False
        frame.append(code)
        if len(frame) < 7:
            return False
        frames.append(list(frame))
        del frame[:]
        return True

    def _span(self, frames, a, b):
        """Audio for frames a..b, decoded with some frames around them so the joins are seamless."""
        lo, hi = max(0, a - self.BEFORE), min(len(frames), b + self.AFTER)
        audio = self.decode(frames[lo:hi])
        return audio[(a - lo) * self.FRAME:(b - lo) * self.FRAME]

    def decode(self, frames):
        """SNAC's three layers per frame: 1 coarse code, 2 middle codes, 4 fine codes."""
        f = np.asarray(frames, dtype=np.int64)
        feed = {"audio_codes.0": f[:, 0].reshape(1, -1),
                "audio_codes.1": f[:, [1, 4]].reshape(1, -1),
                "audio_codes.2": f[:, [2, 3, 5, 6]].reshape(1, -1)}
        return np.asarray(self.decoder.run(None, feed)[0], dtype=np.float32).reshape(-1)

    def _stop(self):
        p, self.proc = self.proc, None
        if p is not None and p.poll() is None:
            try:
                p.terminate()
                p.wait(5)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        job, self.job = self.job, None
        if job:
            _kernel32.CloseHandle(job)

    def close(self):
        """Stop llama-server, which frees the graphics card's memory."""
        self.closed = True
        self._stop()


def build_engine(entry, voice, status, prog, cancelled, speed=None):
    """Download whatever is missing, then load the engine. Runs on a background thread."""
    kind = entry["kind"]
    if kind == "espeak":
        status(tr("Loading eSpeak ..."))
        return ESpeakEngine()
    if kind == "kokoro":
        paths = ensure_files(entry["files"], entry["name"], status, prog, cancelled, speed)
        status(tr("Loading voice engine (first start can take a few seconds)..."))
        return KokoroEngine(paths[1], paths[0])
    if kind == "piper":
        vid, label, files = next((v for v in entry["voices"] if v[0] == voice), entry["voices"][0])[:3]
        paths = ensure_files(files, "%s (%s)" % (entry["name"], label.split(" (")[0]), status, prog, cancelled,
                             speed)
        status(tr("Loading %s ...") % entry["name"])
        try:
            return PiperEngine(vid, label, paths[0], paths[1])
        except CorruptModel as e:
            for p in paths:
                try:
                    p.unlink()
                except Exception:
                    pass
            raise RuntimeError(tr("The voice file looked damaged, so it was deleted. "
                                  "Pick the voice again to re-download it. (%s)") % e)
    if kind == "orpheus":
        ensure_files(ORPHEUS_FILES, entry["name"], status, prog, cancelled, speed)
        return OrpheusEngine(MODEL_DIR / ORPHEUS_MODEL.name, MODEL_DIR / SNAC_DECODER.name, MODEL_DIR / LLAMA_DIR,
                             status, cancelled)
    raise RuntimeError("Unknown model type: %s" % kind)


# --------------------------------------------------------------------------
#  Retro look: bevels, the slider, the theme tiles, the ttk theme
# --------------------------------------------------------------------------
# Bevel rings from the outside in, as (top-left colour, bottom-right colour) theme keys
RAISED = (("light", "darkest"), ("light2", "shadow"))
SUNKEN = (("shadow", "light"), ("darkest", "light2"))
ETCHED = (("shadow", "light"), ("light", "shadow"))
THIN_SUNKEN = (("shadow", "light"),)


def pixel_scale(widget):
    """Whole-pixel size of one "retro pixel": 1 at 100-125% Windows scaling, 2 at 150-200%, and so on."""
    return max(1, int(round(widget.winfo_fpixels("1i") / 96.0)))


def draw_bevel(cv, x0, y0, x1, y1, rings, fill, p=1):
    """Draw a bevelled box on a canvas covering pixels x0..x1-1, y0..y1-1. `rings` hold colours, p = ring width."""
    for tl, br in rings:
        for _ in range(p):
            cv.create_rectangle(x0, y0, x1 - 1, y0 + 1, fill=tl, outline="")        # top
            cv.create_rectangle(x0, y0, x0 + 1, y1 - 1, fill=tl, outline="")        # left
            cv.create_rectangle(x0, y1 - 1, x1, y1, fill=br, outline="")            # bottom
            cv.create_rectangle(x1 - 1, y0, x1, y1, fill=br, outline="")            # right
            x0, y0, x1, y1 = x0 + 1, y0 + 1, x1 - 1, y1 - 1
    if fill and x1 > x0 and y1 > y0:
        cv.create_rectangle(x0, y0, x1, y1, fill=fill, outline="")


def draw_gradient(cv, x0, y0, x1, y1, a, b, steps=48):
    """Left-to-right fade from colour a to colour b."""
    steps = max(1, min(steps, x1 - x0))
    for i in range(steps):
        xa = x0 + (x1 - x0) * i // steps
        xb = x0 + (x1 - x0) * (i + 1) // steps
        cv.create_rectangle(xa, y0, xb, y1, fill=mix(a, b, i / float(max(1, steps - 1))), outline="")


def on_color(bg):
    """Black or white, whichever reads better on bg."""
    return "#FFFFFF" if contrast("#FFFFFF", bg) >= contrast("#000000", bg) else "#000000"


BEVEL_W, BEVEL_H = 320, 96          # bevel image size: most widgets are filled with 1-6 copies of it


def bevel_image(master, w, h, rings, center, p, extra_w=0):
    """A w x h PhotoImage of a bevelled box (rings of theme colours, each p pixels wide), filled with `center`.

    ttk fills a widget by repeating the middle of its image one copy at a time, so these images are made large
    (BEVEL_W x BEVEL_H): with a tiny middle, one repaint took thousands of copies and froze the window.
    extra_w adds that many clear pixels on the right (a gap before a check box's text)."""
    img = tk.PhotoImage(master=master, width=w + extra_w, height=h)
    img.put(center, to=(0, 0, w, h))
    o = 0
    for tl, br in rings:
        for _ in range(p):
            if w - 2 * o <= 0 or h - 2 * o <= 0:
                break
            img.put(tl, to=(o, o, w - 1 - o, o + 1))              # top (its right end belongs to the right side)
            img.put(tl, to=(o, o, o + 1, h - 1 - o))              # left (its bottom end belongs to the bottom)
            img.put(br, to=(o, h - 1 - o, w - o, h - o))          # bottom
            img.put(br, to=(w - 1 - o, o, w - o, h - o))          # right
            o += 1
    return img


CHECK_MARK = ("......#", ".....##", "#...###", "##.###.", "#####..", ".###...", "..#....")


def check_image(master, t, p, checked, center, mark):
    """A 13 x 13 (retro pixel) check box, sunken, with the tick drawn in `mark` when checked."""
    rings = [(t[a], t[b]) for a, b in SUNKEN]
    img = bevel_image(master, 13 * p, 13 * p, rings, center, p, extra_w=5 * p)
    if checked:
        for row, line in enumerate(CHECK_MARK):
            for col, ch in enumerate(line):
                if ch == "#":
                    x, y = (3 + col) * p, (3 + row) * p
                    img.put(mark, to=(x, y, x + p, y + p))
    return img


class FlatSlider(tk.Canvas):
    """Horizontal slider in the retro style: a sunken groove, filled with the accent colour up to a raised handle.

    Works like ttk.Scale for what RTTS needs: `variable` (a DoubleVar) holds the value, `command`
    is called after the user moves it. Values are whole numbers from `from_` to `to`."""

    def __init__(self, master, from_, to, variable, command=None, width=100):
        k = max(1.0, master.winfo_fpixels("1i") / 96.0)      # follow Windows display scaling
        self.k = k
        self.p = pixel_scale(master)
        self.thumb_w = int(round(13 * k))
        super().__init__(master, width=width, height=int(round(22 * k)), highlightthickness=0, bd=0,
                         takefocus=True)
        self.lo, self.hi = float(from_), float(to)
        self.t = THEMES[DEFAULT_THEME]
        self.var = variable
        self.command = command
        self.hot = False                  # mouse is over the handle (or dragging it)
        self.focused = False
        self.drag_dx = None               # where inside the handle the mouse grabbed it; None = not dragging
        self.var.trace_add("write", lambda *_: self.redraw())
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", self._leave)
        self.bind("<FocusIn>", lambda e: self._focus(True))
        self.bind("<FocusOut>", lambda e: self._focus(False))
        for seq, step in (("<Left>", -1), ("<Down>", -1), ("<Right>", 1), ("<Up>", 1),
                          ("<Prior>", 10), ("<Next>", -10)):
            self.bind(seq, lambda e, s=step: self._key(s))
        self.bind("<Home>", lambda e: self._key_to(self.lo))
        self.bind("<End>", lambda e: self._key_to(self.hi))

    # ---- value <-> pixels ----
    def _value(self):
        try:
            v = float(self.var.get())
        except Exception:
            v = self.lo
        return min(self.hi, max(self.lo, v))

    def _width(self):
        w = self.winfo_width()
        return w if w > 1 else int(float(self.cget("width")))

    def _travel(self):
        """How far (in pixels) the left edge of the handle can move."""
        return max(1, self._width() - 1 - self.thumb_w)

    def _thumb_x(self):
        span = self.hi - self.lo
        frac = (self._value() - self.lo) / span if span else 0.0
        return int(round(frac * self._travel()))

    def _set(self, value):
        value = float(int(round(min(self.hi, max(self.lo, value)))))
        if value != self._value():
            self.var.set(value)
            if self.command is not None:
                self.command(value)

    def _set_from_x(self, x0):
        frac = min(1.0, max(0.0, x0 / float(self._travel())))
        self._set(self.lo + frac * (self.hi - self.lo))

    # ---- mouse and keyboard ----
    def _over_thumb(self, x):
        x0 = self._thumb_x()
        return x0 <= x <= x0 + self.thumb_w

    def _press(self, e):
        self.focus_set()
        if self._over_thumb(e.x):
            self.drag_dx = e.x - self._thumb_x()
        else:                                          # click on the track: jump there and keep dragging
            self.drag_dx = self.thumb_w // 2
            self._set_from_x(e.x - self.drag_dx)
        self.hot = True
        self.redraw()

    def _drag(self, e):
        if self.drag_dx is not None:
            self._set_from_x(e.x - self.drag_dx)

    def _release(self, e):
        self.drag_dx = None
        self.hot = self._over_thumb(e.x) and 0 <= e.y < self.winfo_height()
        self.redraw()

    def _hover(self, e):
        hot = self.drag_dx is not None or self._over_thumb(e.x)
        if hot != self.hot:
            self.hot = hot
            self.redraw()

    def _leave(self, _e):
        if self.drag_dx is None and self.hot:
            self.hot = False
            self.redraw()

    def _focus(self, on):
        self.focused = on
        self.redraw()

    def _key(self, step):
        self._set(self._value() + step)
        return "break"

    def _key_to(self, value):
        self._set(value)
        return "break"

    def set_theme(self, t):
        """Recolour for the theme `t` (a THEMES-style dict)."""
        self.t = t
        self.configure(bg=t["face"])
        self.redraw()

    # ---- drawing ----
    def redraw(self):
        self.delete("all")
        w = self._width()
        h = self.winfo_height()
        if h <= 1:
            h = int(float(self.cget("height")))
        t, p = self.t, self.p
        x0 = self._thumb_x()
        # groove: 6 retro pixels high, centred, accent-filled up to the handle
        gh = 6 * p
        gy = (h - gh) // 2
        draw_bevel(self, 0, gy, w, gy + gh, [(t[a], t[b]) for a, b in SUNKEN], t["field"], p)
        if x0 > 2 * p:
            self.create_rectangle(2 * p, gy + 2 * p, x0 + self.thumb_w // 2, gy + gh - 2 * p,
                                  fill=t["accent"], outline="")
        # handle: a raised button the full height of the slider
        draw_bevel(self, x0, 0, x0 + self.thumb_w, h, [(t[a], t[b]) for a, b in RAISED],
                   t["hot"] if self.hot else t["face"], p)
        cx = x0 + self.thumb_w // 2
        self.create_rectangle(cx - p // 2, h // 3, cx - p // 2 + p, h - h // 3, fill=t["shadow"], outline="")
        if self.focused:
            self.create_rectangle(0, 0, w - 1, h - 1, outline=t["text"], dash=(1, 1))


class ThemeTile(tk.Canvas):
    """A clickable preview of one colour scheme for Settings > Appearance: a tiny window in its colours."""

    def __init__(self, master, key, command):
        k = max(1.0, master.winfo_fpixels("1i") / 96.0)
        self.p = pixel_scale(master)
        super().__init__(master, width=int(round(112 * k)), height=int(round(64 * k)), highlightthickness=0,
                         bd=0, takefocus=True, cursor="hand2")
        self.key = key
        self.command = command
        self.selected = False
        self.preview = THEMES.get(key, THEMES[DEFAULT_THEME])
        self.window_t = THEMES[DEFAULT_THEME]           # the theme in use, for the area around the preview
        self.focused = False
        self.bind("<Button-1>", lambda e: self.command(self.key))
        self.bind("<space>", lambda e: self.command(self.key))
        self.bind("<Return>", lambda e: self.command(self.key))
        self.bind("<FocusIn>", lambda e: self._focus(True))
        self.bind("<FocusOut>", lambda e: self._focus(False))
        self.bind("<Configure>", lambda e: self.redraw())

    def _focus(self, on):
        self.focused = on
        self.redraw()

    def update_look(self, window_t, preview_t, selected):
        self.window_t, self.preview, self.selected = window_t, preview_t, selected
        self.redraw()

    def _mini(self, x0, y0, x1, y1, t):
        p = self.p
        draw_bevel(self, x0, y0, x1, y1, [(t[a], t[b]) for a, b in RAISED], t["face"], p)
        tb0, tb1 = y0 + 2 * p + 1, y0 + 2 * p + 1 + 7 * p
        draw_gradient(self, x0 + 2 * p + 1, tb0, x1 - 2 * p - 1, tb1, t["accent_dark"], t["accent"], 16)
        fy = tb1 + 4 * p
        draw_bevel(self, x0 + 5 * p, fy, x1 - 5 * p, fy + 7 * p, [(t[a], t[b]) for a, b in THIN_SUNKEN],
                   t["field"], p)
        self.create_rectangle(x0 + 8 * p, fy + 3 * p, x0 + 8 * p + (x1 - x0) // 3, fy + 4 * p,
                              fill=t["text"] if t["dark"] else "#000000", outline="")
        by = fy + 7 * p + 4 * p
        if by + 6 * p < y1 - 3 * p:
            draw_bevel(self, x0 + 5 * p, by, x0 + 5 * p + 16 * p, by + 6 * p,
                       [(mix(t["accent"], "#FFFFFF", 0.4), t["darkest"])], t["accent"], p)
            draw_bevel(self, x0 + 5 * p + 19 * p, by, x0 + 5 * p + 35 * p, by + 6 * p,
                       [(t["light"], t["darkest"])], t["face"], p)

    def redraw(self):
        self.delete("all")
        wt, p = self.window_t, self.p
        self.configure(bg=wt["face"])
        w = self.winfo_width()
        if w <= 1:
            w = int(float(self.cget("width")))
        h = self.winfo_height()
        if h <= 1:
            h = int(float(self.cget("height")))
        m = 4 * p
        if self.key == "system":          # Match Windows: Classic and Midnight side by side
            mid = w // 2
            self._mini(m, m, w - m - (w // 3), h - m - 3 * p, THEMES["classic"])
            self._mini(mid - 6 * p, m + 3 * p, w - m, h - m, THEMES["midnight"])
        else:
            self._mini(m, m, w - m, h - m, self.preview)
        if self.selected:
            for i in range(2 * p):
                self.create_rectangle(i, i, w - 1 - i, h - 1 - i, outline=wt["heading"])
        elif self.focused:
            self.create_rectangle(1, 1, w - 2, h - 2, outline=wt["text"], dash=(1, 1))


def ensure_retro_theme(root, st, key, t, images):
    """Create the ttk theme "rtts-<key>" for colours t, once. `images` keeps its PhotoImages alive.

    Built on "clam": buttons, text boxes, group boxes, check boxes, the status bar and progress bars get
    pixel-exact bevel images; combo box arrows, scroll bars and tabs use clam's own parts in our colours."""
    name = "rtts-" + key
    if name in st.theme_names():
        return name
    p = pixel_scale(root)
    b = 2 * p                                    # two bevel rings
    n = 2 * b + 2                                # the smallest a bevelled box can be (its requested size)
    bw, bh = BEVEL_W * p, BEVEL_H * p

    def rings(spec):
        return [(t[a], t[c]) for a, c in spec]

    def bevel(spec_rings, center):
        return bevel_image(root, bw, bh, spec_rings, center, p)

    acc_rings = [(mix(t["accent"], "#FFFFFF", 0.45), t["darkest"]), (mix(t["accent"], "#FFFFFF", 0.2), t["accent_dark"])]
    acc_down = [(t["darkest"], mix(t["accent"], "#FFFFFF", 0.45)), (t["accent_dark"], mix(t["accent"], "#FFFFFF", 0.2))]
    img = {
        "btn": bevel(rings(RAISED), t["face"]),
        "btn_hot": bevel(rings(RAISED), t["hot"]),
        "btn_down": bevel(rings(SUNKEN), t["face"]),
        "acc": bevel(acc_rings, t["accent"]),
        "acc_hot": bevel(acc_rings, mix(t["accent"], "#FFFFFF", 0.12)),
        "acc_down": bevel(acc_down, t["accent"]),
        "acc_dis": bevel(rings(RAISED), t["face"]),
        "field": bevel(rings(SUNKEN), t["field"]),
        "field_dis": bevel(rings(SUNKEN), t["face"]),
        "group": bevel(rings(ETCHED), t["face"]),
        "status": bevel(rings(THIN_SUNKEN), t["face"]),
        "chk_off": check_image(root, t, p, False, t["field"], t["text"] if t["dark"] else "#000000"),
        "chk_on": check_image(root, t, p, True, t["field"], t["text"] if t["dark"] else "#000000"),
        "chk_off_dis": check_image(root, t, p, False, t["face"], t["shadow"]),
        "chk_on_dis": check_image(root, t, p, True, t["face"], t["shadow"]),
        "chk_off_down": check_image(root, t, p, False, t["face"], t["text"]),
        "chk_on_down": check_image(root, t, p, True, t["face"], t["text"] if t["dark"] else "#000000"),
    }
    # progress: blocks of the accent colour with gaps (6 on, 2 off), repeated along the bar; wide and tall so
    # a whole bar takes only a couple of copies
    blk = tk.PhotoImage(master=root, width=8 * p * 40, height=48 * p)
    blk.put(t["accent"], to=(0, 0, 8 * p * 40, 48 * p))
    for i in range(40):
        blk.put(t["field"], to=(i * 8 * p + 6 * p, 0, (i + 1) * 8 * p, 48 * p))
    img["pbar"] = blk
    images[name] = img

    # width / height give each element its small requested size; without them the big image would set the
    # smallest size of every button and box
    st.theme_create(name, parent="clam")
    st.theme_use(name)
    st.element_create("Retro.Button.border", "image", img["btn"], ("disabled", img["btn"]),
                      ("pressed", img["btn_down"]), ("active", img["btn_hot"]), border=b, width=n, height=n,
                      sticky="nsew")
    st.element_create("Retro.Accent.border", "image", img["acc"], ("disabled", img["acc_dis"]),
                      ("pressed", img["acc_down"]), ("active", img["acc_hot"]), border=b, width=n, height=n,
                      sticky="nsew")
    st.element_create("Retro.Field.border", "image", img["field"], ("disabled", img["field_dis"]),
                      border=b, width=n, height=n, sticky="nsew")
    st.element_create("Retro.Group.border", "image", img["group"], border=b, width=n, height=n, sticky="nsew")
    st.element_create("Retro.Status.border", "image", img["status"], border=p, width=2 * p + 2,
                      height=2 * p + 2, sticky="nsew")
    st.element_create("Retro.Check.indicator", "image", img["chk_off"],
                      ("disabled", "selected", img["chk_on_dis"]), ("disabled", img["chk_off_dis"]),
                      ("pressed", "selected", img["chk_on_down"]), ("pressed", img["chk_off_down"]),
                      ("selected", img["chk_on"]), sticky="")
    st.element_create("Retro.Progress.trough", "image", img["field"], border=b, padding=b + p, width=n,
                      height=int(round(18 * root.winfo_fpixels("1i") / 96.0)), sticky="nsew")
    st.element_create("Retro.Progress.pbar", "image", img["pbar"], border=0, width=1, height=1, sticky="nsew")

    def button(border):
        return [(border, {"sticky": "nswe", "children": [
            ("Button.focus", {"sticky": "nswe", "children": [
                ("Button.padding", {"sticky": "nswe", "children": [("Button.label", {"sticky": "nswe"})]})]})]})]

    st.layout("TButton", button("Retro.Button.border"))
    st.layout("Accent.TButton", button("Retro.Accent.border"))
    st.layout("TEntry", [("Retro.Field.border", {"sticky": "nswe", "children": [
        ("Entry.padding", {"sticky": "nswe", "children": [("Entry.textarea", {"sticky": "nswe"})]})]})])
    st.layout("TCombobox", [("Retro.Field.border", {"sticky": "nswe", "children": [
        ("Combobox.downarrow", {"side": "right", "sticky": "ns"}),
        ("Combobox.padding", {"expand": "1", "sticky": "nswe", "children": [
            ("Combobox.textarea", {"sticky": "nswe"})]})]})])
    st.layout("TLabelframe", [("Retro.Group.border", {"sticky": "nswe"})])
    st.layout("Field.TFrame", [("Retro.Field.border", {"sticky": "nswe"})])
    st.layout("Status.TLabel", [("Retro.Status.border", {"sticky": "nswe", "children": [
        ("Label.padding", {"sticky": "nswe", "children": [("Label.label", {"sticky": "nswe"})]})]})])
    st.layout("TCheckbutton", [("Checkbutton.padding", {"sticky": "nswe", "children": [
        ("Retro.Check.indicator", {"side": "left", "sticky": ""}),
        ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
            ("Checkbutton.label", {"sticky": "nswe"})]})]})])
    st.layout("Horizontal.TProgressbar", [("Retro.Progress.trough", {"sticky": "nswe", "children": [
        ("Retro.Progress.pbar", {"side": "left", "sticky": "ns"})]})])
    return name


def style_retro_theme(st, t):
    """Colours and fonts for the theme in use (run after theme_use, every time the theme changes)."""
    sel_bg = t["accent_dark"]
    sel_fg = on_color(sel_bg)
    disabled = mix(t["face"], t["text"], 0.45)
    trough = mix(t["face"], t["field"], 0.5)
    st.configure(".", background=t["face"], foreground=t["text"], fieldbackground=t["field"],
                 bordercolor=t["darkest"], lightcolor=t["light"], darkcolor=t["shadow"], troughcolor=trough,
                 focuscolor=t["text"], insertcolor=t["text"], selectbackground=sel_bg,
                 selectforeground=sel_fg, font=ui_font())
    st.map(".", foreground=[("disabled", disabled)])
    st.configure("TFrame", background=t["face"])
    st.configure("TLabel", background=t["face"], foreground=t["text"])
    st.configure("TButton", foreground=t["text"], padding=(10, 3), font=ui_font(bold=True),
                 focusthickness=1, focuscolor=t["text"], anchor="center")
    st.map("TButton", foreground=[("disabled", disabled)])
    st.configure("Accent.TButton", foreground=on_color(t["accent"]), padding=(10, 3), font=ui_font(bold=True),
                 focusthickness=1, focuscolor=on_color(t["accent"]), anchor="center")
    st.map("Accent.TButton", foreground=[("disabled", disabled)],
           focuscolor=[("disabled", t["text"])])
    st.configure("TEntry", foreground=t["text"], fieldbackground=t["field"], padding=(4, 2))
    st.configure("TCombobox", foreground=t["text"], fieldbackground=t["field"], background=t["face"],
                 arrowcolor=t["text"], bordercolor=t["darkest"], lightcolor=t["light"], darkcolor=t["shadow"],
                 padding=(4, 1), arrowsize=13)
    st.map("TCombobox", fieldbackground=[("readonly", t["field"])], foreground=[("readonly", t["text"])],
           background=[("pressed", t["face"]), ("active", t["hot"])],
           selectbackground=[("readonly", t["field"])], selectforeground=[("readonly", t["text"])],
           arrowcolor=[("disabled", disabled)])
    st.configure("TCheckbutton", background=t["face"], foreground=t["text"], focuscolor=t["text"], padding=(0, 1))
    st.map("TCheckbutton", background=[("active", t["face"])])
    st.configure("TRadiobutton", background=t["face"], foreground=t["text"], indicatorbackground=t["field"],
                 indicatorforeground=t["text"], upperbordercolor=t["shadow"], lowerbordercolor=t["light"])
    st.map("TRadiobutton", background=[("active", t["face"])],
           indicatorbackground=[("pressed", t["face"]), ("selected", t["field"])])
    st.configure("TLabelframe", background=t["face"], padding=(10, 6), labelmargins=(8, 0, 8, 0))
    st.configure("TLabelframe.Label", background=t["face"], foreground=t["heading"], font=ui_font(bold=True),
                 padding=(3, 0))
    st.configure("Status.TLabel", background=t["face"], foreground=t["text"], padding=(6, 2))
    st.configure("Vertical.TScrollbar", background=t["face"], troughcolor=trough, bordercolor=t["darkest"],
                 lightcolor=t["light"], darkcolor=t["shadow"], arrowcolor=t["text"], gripcount=0, arrowsize=15)
    st.map("Vertical.TScrollbar", background=[("pressed", t["face"]), ("active", t["hot"])])
    st.configure("TSeparator", background=t["shadow"])
    st.configure("TNotebook", background=t["face"], bordercolor=t["darkest"], lightcolor=t["light"],
                 darkcolor=t["shadow"], tabmargins=(2, 4, 2, 0))
    st.configure("TNotebook.Tab", background=mix(t["face"], t["shadow"], 0.3), foreground=t["text"],
                 bordercolor=t["darkest"], lightcolor=t["light"], darkcolor=t["shadow"], padding=(12, 3),
                 font=ui_font(bold=True))
    st.map("TNotebook.Tab", background=[("selected", t["face"]), ("active", t["hot"])],
           lightcolor=[("selected", t["light"])], expand=[("selected", (2, 2, 2, 0))])
    st.configure("Hint.TLabel", foreground=t["hint"])
    st.configure("Warn.TLabel", foreground=t["warn"])
    st.configure("Good.TLabel", foreground=t["good"])
    st.configure("Bad.TLabel", foreground=t["bad"])
    st.configure("Title.TLabel", font=ui_font(11, True))
    st.configure("Bold.TLabel", font=ui_font(bold=True))
    for name in ("Good", "Warn", "Bad"):
        st.configure("%sBold.TLabel" % name, font=ui_font(bold=True), foreground=t[name.lower()])


# --------------------------------------------------------------------------
#  The app
# --------------------------------------------------------------------------
class App:
    def __init__(self, root):
        self.root = root
        root.title("%s %s" % (APP_TITLE, APP_VERSION))
        root.resizable(False, False)

        self.ui_q = queue.Queue()          # callables to run on the Tk thread
        self.jobs = queue.Queue()          # speech jobs for the worker thread
        self.gen = 0                       # bumped to cancel everything queued/playing
        self.closing = False
        self.running = False

        self.engine = None
        self.engine_ready = threading.Event()   # set while an engine is loaded and no model switch is running
        self.load_lock = threading.Lock()
        self.loaded_key = None             # engine_key() of the engine in self.engine
        self.loaded_entry = None
        self.loaded_voice = ""
        self.loading = False               # a model is being downloaded / loaded right now
        self.loading_key = None
        self.load_gen = 0                  # bumped on every model switch; older loads give up
        self.voice_map = {}                # voice label -> voice id
        self.chosen_entry = CATALOG_BY_ID[DEFAULT_MODEL]   # the model picked last (the menu goes back to it on Cancel)
        self._pct = 0                      # download progress and speed shown left of the progress bar
        self._speed = ""

        self.out_devices = []              # [(index, name)]
        self.default_out = None

        # Chat capture state (touched by the key-listener thread and Tk tick)
        self.cap_lock = threading.Lock()
        self.cap_active = False
        self.cap_buf = []
        self.cap_started = 0.0
        self.cap_pos = 0                   # cursor position inside cap_buf
        self.cap_sel = False               # True after Ctrl+A (everything selected)

        self.audio_lock = threading.Lock()
        self._streams = 0                  # number of open output streams

        # Start / Stop hotkeys, and which one is waiting for a new key in Settings > Keybinds (None = neither)
        self.hotkeys = {w: keyboard.Key[n] for w, n in DEFAULT_HOTKEYS.items()}
        self.binding = None
        self.bind_btns = {}
        self.v_bind = {}
        self.last_layout = None            # keyboard layout of the window in front (see follow_keyboard_layout)
        self.non_latin_layout = False      # ... and whether it types a non-Latin script (see _is_chat_key)

        # Settings mirrored for the listener thread (plain values, replaced atomically)
        self.snap = {"keyspec": ("char", "/"), "only": True, "slash": False,
                     "start": self.hotkeys["start"], "stop": self.hotkeys["stop"]}
        self.recent = []

        self.settings_win = None
        self.ui_language_setting = ""      # Settings > General > Language: a UI_LANGUAGES code, "" = match Windows
        self.theme = THEMES[DEFAULT_THEME]   # the colours in use (see apply_theme)
        self.theme_images = {}             # ttk theme name -> its bevel images (kept alive here)
        self.tiles = {}                    # Settings > Appearance: theme key -> ThemeTile
        # text boxes and drop-down menus take Tk's default fonts, so those become the RTTS font too
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkTooltipFont",
                     "TkCaptionFont", "TkSmallCaptionFont", "TkIconFont"):
            try:
                tkfont.nametofont(name, root=root).configure(family=UI_FAMILY, size=FONT_SIZE)
            except Exception:
                pass
        self.apply_icon(root)
        self.build_ui()
        self.load_settings()
        self.sync_snap()
        self.refresh_ready_status()

        self.worker = threading.Thread(target=self.worker_loop, daemon=True)
        self.worker.start()

        self.kb_listener = keyboard.Listener(on_press=self.on_key_press)
        self.ms_listener = mouse.Listener(on_click=self.on_mouse_click)
        self.kb_listener.daemon = True
        self.ms_listener.daemon = True
        self.kb_listener.start()
        self.ms_listener.start()

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(40, self.pump)
        root.after(250, self.tick)
        if self.needs_large_confirm(self.current_entry()):
            # a large model is saved as the choice but its files aren't here: ask first, once the window is up
            self.set_status(tr("Waiting for you to confirm the %s download ...") % self.current_entry()["name"])
            root.after(300, self.confirm_startup_model)
        else:
            self.start_engine_load(self.current_entry(), self.voice_id())
        self.update_busy = False          # an update is downloading / starting
        self.update_checking = False      # a check is in progress
        root.after(1500, self.start_update_check)

    # ---------------- UI ----------------
    def build_ui(self):
        r = self.root
        r.option_add("*TCombobox*Listbox.font", ui_font())
        r.columnconfigure(0, weight=1)

        self.v_voice = tk.StringVar()
        self.v_lang = tk.StringVar(value=LANGUAGE_NAMES[DEFAULT_LANGUAGE])   # shows the language's own name
        self.v_lang_note = tk.StringVar()
        self.v_model = tk.StringVar()
        self.v_output = tk.StringVar()
        self.v_speed = tk.DoubleVar(value=100)
        self.v_volume = tk.DoubleVar(value=100)
        self.v_key = tk.StringVar(value="/")
        self.v_only = tk.BooleanVar(value=True)
        self.v_mon = tk.BooleanVar(value=True)
        self.v_interrupt = tk.BooleanVar(value=False)
        self.v_slash = tk.BooleanVar(value=False)
        self.v_emoji = tk.BooleanVar(value=False)
        self.v_auto = tk.BooleanVar(value=False)
        self.v_stats = tk.BooleanVar(value=False)
        self.stats_sent = set()          # (model, voice) already reported this launch
        self.v_theme = tk.StringVar(value=DEFAULT_THEME)
        self.v_custom_accent = tk.StringVar(value=DEFAULTS["custom_accent"])
        self.v_custom_base = tk.StringVar(value=DEFAULTS["custom_base"])
        self.v_top = tk.BooleanVar(value=False)
        self.v_test = tk.StringVar(value=tr("Hello, this is a test."))
        self.v_status = tk.StringVar(value=tr("Starting..."))
        self.v_info = tk.StringVar()
        self.v_pct = tk.StringVar()
        self.v_speed_txt = tk.StringVar(value="100%")
        self.v_vol_txt = tk.StringVar(value="100")

        outer = ttk.Frame(r, padding=(12, 8, 12, 0))
        outer.grid(row=0, column=0)

        # Voice group
        g1 = ttk.LabelFrame(outer, text=tr("Voice"), padding=(10, 6))
        g1.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        g1.columnconfigure(2, weight=1)
        ttk.Label(g1, text=tr("Language:")).grid(row=0, column=0, sticky="w", pady=2)
        self.cb_lang = ttk.Combobox(g1, textvariable=self.v_lang, state="readonly",
                                    values=[name for _code, name in LANGUAGES], width=20)
        self.cb_lang.grid(row=0, column=1, sticky="w", pady=2)
        ttk.Label(g1, text=tr("Model / voice:")).grid(row=1, column=0, sticky="w", pady=2)
        labels = [model_label(e) for e in CATALOG]
        self.cb_model = ttk.Combobox(g1, textvariable=self.v_model, state="readonly",
                                     values=labels, width=max(len(t) for t in labels))
        self.cb_model.grid(row=1, column=1, sticky="w", pady=2)
        self.cb_voice = ttk.Combobox(g1, textvariable=self.v_voice, state="readonly", width=20)
        self.cb_voice.grid(row=1, column=2, sticky="ew", padx=(6, 0), pady=2)
        ttk.Label(g1, textvariable=self.v_info, justify="left", style="Hint.TLabel").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(0, 2))
        # Shown when the language isn't English: which models can't speak it (and typing tips for some)
        self.lbl_lang_note = ttk.Label(g1, textvariable=self.v_lang_note, justify="left", style="Warn.TLabel",
                                       wraplength=1)
        self.lbl_lang_note.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(0, 2))
        self.lbl_lang_note.bind("<Configure>", lambda e: self.lbl_lang_note.configure(wraplength=max(200, e.width)))
        ttk.Label(g1, text=tr("Output device:")).grid(row=4, column=0, sticky="w", pady=2)
        self.cb_out = ttk.Combobox(g1, textvariable=self.v_output, state="readonly", width=36)
        self.cb_out.grid(row=4, column=1, columnspan=2, sticky="ew", pady=2)
        bf = ttk.Frame(g1)
        bf.grid(row=5, column=1, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Button(bf, text=tr("Refresh devices"), command=self.refresh_devices).pack(side="left")
        ttk.Button(bf, text=tr("Get VB-Cable"),
                   command=lambda: webbrowser.open("https://vb-audio.com/Cable/")).pack(side="left", padx=(6, 0))

        # Voice options
        g2 = ttk.LabelFrame(outer, text=tr("Voice Options"), padding=(10, 6))
        g2.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        g2.columnconfigure(1, weight=1)
        ttk.Label(g2, text=tr("Speed")).grid(row=0, column=0, sticky="w", pady=2)
        sl_speed = FlatSlider(g2, 60, 160, self.v_speed, self.on_slider)
        sl_speed.grid(row=0, column=1, sticky="ew", padx=8, pady=2)
        ttk.Label(g2, textvariable=self.v_speed_txt, width=6).grid(row=0, column=2)
        ttk.Label(g2, text=tr("Volume")).grid(row=1, column=0, sticky="w", pady=2)
        sl_volume = FlatSlider(g2, 0, 100, self.v_volume, self.on_slider)
        sl_volume.grid(row=1, column=1, sticky="ew", padx=8, pady=2)
        ttk.Label(g2, textvariable=self.v_vol_txt, width=6).grid(row=1, column=2)
        self.sliders = [sl_speed, sl_volume]           # recoloured to the window background by apply_theme

        # Chat options
        g3 = ttk.LabelFrame(outer, text=tr("Chat Options"), padding=(10, 6))
        g3.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(g3, text=tr("Chat key:")).grid(row=0, column=0, sticky="w")
        self.ed_key = ttk.Entry(g3, textvariable=self.v_key, width=6)
        self.ed_key.grid(row=0, column=1, sticky="w", padx=(6, 8))
        ttk.Label(g3, text=tr("Press this in Roblox to open chat")).grid(row=0, column=2, sticky="w")
        self.ed_key.bind("<FocusOut>", lambda e: self.apply_chat_key())
        self.ed_key.bind("<Return>", lambda e: self.apply_chat_key())
        ttk.Checkbutton(g3, text=tr("Only work while Roblox is the active window"),
                        variable=self.v_only, command=self.sync_snap).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Checkbutton(g3, text=tr("Also play the voice on my speakers"),
                        variable=self.v_mon).grid(row=2, column=0, columnspan=3, sticky="w", pady=(2, 0))
        ttk.Checkbutton(g3, text=tr("A new message stops the one that is playing"),
                        variable=self.v_interrupt).grid(row=3, column=0, columnspan=3, sticky="w", pady=(2, 0))

        # Test
        g4 = ttk.LabelFrame(outer, text=tr("Test"), padding=(10, 6))
        g4.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        g4.columnconfigure(0, weight=1)
        e = ttk.Entry(g4, textvariable=self.v_test)
        e.grid(row=0, column=0, sticky="ew")
        e.bind("<Return>", lambda ev: self.test_speak())
        ttk.Button(g4, text=tr("Speak"), command=self.test_speak).grid(row=0, column=1, padx=(6, 0))

        # Recent messages
        g5 = ttk.LabelFrame(outer, text=tr("Recent Messages"), padding=(10, 6))
        g5.grid(row=4, column=0, sticky="ew", pady=(0, 6))
        g5.columnconfigure(0, weight=1)
        box = ttk.Frame(g5, style="Field.TFrame", padding=2 * pixel_scale(r))   # sunken box: text + scroll bar
        box.grid(row=0, column=0, sticky="ew")
        box.columnconfigure(0, weight=1)
        self.txt_log = tk.Text(box, height=4, width=46, wrap="word", state="disabled", font=ui_font(),
                               relief="flat", borderwidth=0, highlightthickness=0, padx=4, pady=2)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=sb.set)
        self.txt_log.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")

        # Buttons
        bb = ttk.Frame(outer)
        bb.grid(row=5, column=0, sticky="ew", pady=(0, 8))
        for c in range(5):
            bb.columnconfigure(c, weight=1)
        self.btn_start = ttk.Button(bb, command=self.start_app, style="Accent.TButton")
        self.btn_start.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.btn_stop = ttk.Button(bb, command=self.stop_app)
        self.btn_stop.grid(row=0, column=1, sticky="ew", padx=4)
        self.update_hotkey_labels()
        ttk.Button(bb, text=tr("Save Settings"), command=self.save_settings).grid(row=0, column=2, sticky="ew", padx=4)
        ttk.Button(bb, text=tr("Reset Settings"), command=self.reset_settings).grid(row=0, column=3, sticky="ew", padx=4)
        ttk.Button(bb, text=tr("Settings..."), command=self.open_settings).grid(row=0, column=4, sticky="ew", padx=(4, 0))

        # Download progress: at the bottom, just above the status bar. Percentage and speed on the left.
        # Only shown while a voice file is downloading (show_progress_row); the window shrinks back after.
        pf = self.pf = ttk.Frame(r, padding=(12, 0, 12, 6))
        pf.grid(row=1, column=0, sticky="ew")
        pf.columnconfigure(1, weight=1)
        ttk.Label(pf, textvariable=self.v_pct, width=19).grid(row=0, column=0, sticky="w")
        self.pb = ttk.Progressbar(pf, orient="horizontal", mode="determinate", maximum=100)
        self.pb.grid(row=0, column=1, sticky="ew")
        pf.grid_remove()

        sf = ttk.Frame(r, padding=(4, 0, 4, 4))
        sf.grid(row=2, column=0, sticky="ew")
        sf.columnconfigure(0, weight=1)
        ttk.Label(sf, textvariable=self.v_status, style="Status.TLabel", anchor="w").grid(row=0, column=0, sticky="ew")

        self.cb_voice.bind("<<ComboboxSelected>>", self.on_voice_change)
        self.cb_model.bind("<<ComboboxSelected>>", self.on_model_change)
        self.cb_lang.bind("<<ComboboxSelected>>", self.on_language_change)
        default = CATALOG_BY_ID[DEFAULT_MODEL]
        self.v_model.set(model_label(default))
        self.fill_voice_list(catalog_voices(default, DEFAULT_LANGUAGE), prefer=DEFAULT_VOICE)
        self.fill_devices(self.v_output.get())

    # ---------------- look: icon, light/dark theme, settings window ----------------
    def apply_icon(self, win):
        """Use icon.ico (next to RTTS.py / the exe) as the window icon if it exists."""
        ico = resource_path("icon.ico")
        if IS_WIN and ico is not None:
            try:
                win.iconbitmap(str(ico))
            except Exception:
                log_error(traceback.format_exc())

    def current_theme(self):
        """(colours, key) of the chosen theme. "system" follows Windows; "custom" is built from the picked colour."""
        key = self.v_theme.get()
        if key == "system":
            key = "midnight" if system_uses_dark() else "classic"
        if key == "custom":
            accent = self.v_custom_accent.get()
            accent = accent.upper() if HEX_COLOR.match(accent) else DEFAULTS["custom_accent"]
            base = "dark" if self.v_custom_base.get() == "dark" else "light"
            return custom_theme(accent, base), "custom-%s-%s" % (accent[1:].lower(), base)
        if key not in THEMES:
            key = DEFAULT_THEME
        return THEMES[key], key

    def apply_theme(self):
        t, key = self.current_theme()
        self.theme = t
        st = ttk.Style(self.root)
        try:
            name = ensure_retro_theme(self.root, st, key, t, self.theme_images)
            st.theme_use(name)
            style_retro_theme(st, t)
        except Exception:
            log_error(traceback.format_exc())
        self.root.configure(bg=t["face"])
        for s in self.sliders:
            s.set_theme(t)
        self.txt_log.configure(bg=t["field"], fg=t["text"], insertbackground=t["text"],
                               selectbackground=t["accent_dark"], selectforeground=on_color(t["accent_dark"]),
                               inactiveselectbackground=t["accent_dark"])
        # the drop-down list of the comboboxes is a plain Tk listbox
        combos = [self.cb_lang, self.cb_voice, self.cb_model, self.cb_out]
        if self.tiles:                     # Settings is open: its menus too
            combos += [self.cb_base, self.cb_ui_lang]
        for cb in combos:
            try:
                lb = "%s.f.l" % self.root.tk.call("ttk::combobox::PopdownWindow", cb)
                self.root.tk.call(lb, "configure", "-background", t["field"], "-foreground", t["text"],
                                  "-selectbackground", t["accent_dark"],
                                  "-selectforeground", on_color(t["accent_dark"]), "-font", ui_font())
            except Exception:
                pass
        set_titlebar_dark(self.root, t["dark"])
        if self.settings_win is not None and self.settings_win.winfo_exists():
            self.settings_win.configure(bg=t["face"])
            set_titlebar_dark(self.settings_win, t["dark"])
            self.update_tiles()

    def open_settings(self):
        if self.settings_win is not None and self.settings_win.winfo_exists():
            self.settings_win.lift()
            self.settings_win.focus_force()
            return
        w = tk.Toplevel(self.root)
        self.settings_win = w
        w.title(tr("%s - Settings") % APP_TITLE)
        w.resizable(False, False)
        w.transient(self.root)
        self.apply_icon(w)
        w.protocol("WM_DELETE_WINDOW", self.close_settings)

        outer = ttk.Frame(w, padding=(12, 8, 12, 8))
        outer.grid(row=0, column=0)
        nb = ttk.Notebook(outer)
        nb.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        general = ttk.Frame(nb, padding=(8, 8, 8, 2))
        appearance = ttk.Frame(nb, padding=(8, 8, 8, 2))
        keybinds = ttk.Frame(nb, padding=(8, 8, 8, 2))
        nb.add(general, text=tr("General"))
        nb.add(appearance, text=tr("Appearance"))
        nb.add(keybinds, text=tr("Keybinds"))
        self.build_appearance_tab(appearance)
        self.build_keybinds_tab(keybinds)

        g1 = ttk.LabelFrame(general, text=tr("Language"), padding=(10, 6))
        g1.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(g1, text=tr("RTTS in:")).grid(row=0, column=0, sticky="w")
        choices = [tr("Match Windows")] + [name for _code, name in UI_LANGUAGES]
        self.cb_ui_lang = ttk.Combobox(g1, values=choices, state="readonly", width=22)
        self.cb_ui_lang.set(self.ui_language_name(self.ui_language_setting))
        self.cb_ui_lang.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.cb_ui_lang.bind("<<ComboboxSelected>>", self.on_ui_language_change)
        ttk.Label(g1, style="Hint.TLabel", wraplength=380, justify="left",
                  text=tr("The language of RTTS's buttons and messages. The language the voice speaks is "
                          "picked in the main window, under Voice.")).grid(row=1, column=0, columnspan=2,
                                                                          sticky="w", pady=(4, 0))

        g2 = ttk.LabelFrame(general, text=tr("Window"), padding=(10, 6))
        g2.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        ttk.Checkbutton(g2, text=tr("Keep this window on top of other windows"),
                        variable=self.v_top, command=self.on_topmost_change).grid(row=0, column=0, sticky="w")

        g3 = ttk.LabelFrame(general, text=tr("Experimental"), padding=(10, 6))
        g3.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        ttk.Checkbutton(g3, text=tr('Roblox types a "/" in the chat box when I press "/"'),
                        variable=self.v_slash, command=self.on_slash_change).grid(row=0, column=0, sticky="w")
        ttk.Label(g3, text=tr('Lets RTTS skip emote commands such as "/e dance" instead of speaking them.'),
                  wraplength=380, justify="left").grid(row=1, column=0, sticky="w", pady=(2, 0))
        ttk.Label(g3, style="Warn.TLabel", wraplength=380, justify="left",
                  text=tr("Warning: this might not work. Most versions of Roblox do not type the \"/\", "
                          "and if yours doesn't, none of your messages will be spoken. "
                          "If that happens, turn this off.")).grid(row=2, column=0, sticky="w", pady=(4, 0))
        ttk.Checkbutton(g3, text=tr("Skip emojis"), variable=self.v_emoji,
                        command=self.save_ui_prefs).grid(row=3, column=0, sticky="w", pady=(10, 0))
        ttk.Label(g3, text=tr("Emojis are left out instead of being read out. A message that is only emojis "
                              "isn't spoken."), wraplength=380, justify="left").grid(row=4, column=0, sticky="w", pady=(2, 0))

        g4 = ttk.LabelFrame(general, text=tr("About and updates"), padding=(10, 6))
        g4.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(g4, text="RTTS %s" % APP_VERSION, style="Bold.TLabel").grid(row=0, column=0, sticky="w")
        bf = ttk.Frame(g4)
        bf.grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Button(bf, text=tr("Check for updates"), command=lambda: self.start_update_check(manual=True)).pack(side="left")
        ttk.Button(bf, text=tr("GitHub page"), command=lambda: webbrowser.open(REPO_URL)).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(g4, text=tr("Install updates automatically"), variable=self.v_auto,
                        command=self.save_ui_prefs).grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Label(g4, text=tr("RTTS checks GitHub for a new version every time it starts. With this on, it updates "
                              "without asking first."), wraplength=380, justify="left").grid(row=3, column=0, sticky="w", pady=(2, 0))
        ttk.Checkbutton(g4, text=tr("Send anonymous voice stats"), variable=self.v_stats,
                        command=self.save_ui_prefs).grid(row=4, column=0, sticky="w", pady=(8, 0))
        ttk.Label(g4, text=tr("Only which voice you use is sent, so popular voices can be improved. Nothing you type "
                              "is ever sent."), wraplength=380, justify="left").grid(row=5, column=0, sticky="w", pady=(2, 0))

        ttk.Label(outer, text=tr("Changes here are saved automatically.")).grid(row=1, column=0, sticky="w", pady=(0, 6))
        ttk.Button(outer, text=tr("Close"), command=self.close_settings).grid(row=2, column=0, sticky="e")

        self.root.update_idletasks()
        w.geometry("+%d+%d" % (self.root.winfo_x() + 40, self.root.winfo_y() + 40))
        self.apply_theme()
        w.focus_force()

    def build_appearance_tab(self, tab):
        tab.columnconfigure(0, weight=1)
        g = ttk.LabelFrame(tab, text=tr("Theme"), padding=(10, 6))
        g.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.tiles = {}
        self.tile_labels = {}
        for i, key in enumerate(THEME_TILES):
            cell = ttk.Frame(g)
            cell.grid(row=i // 3, column=i % 3, padx=4, pady=(2, 6))
            tile = self.tiles[key] = ThemeTile(cell, key, self.pick_theme)
            tile.grid(row=0, column=0)
            lbl = self.tile_labels[key] = ttk.Label(cell, text=self.theme_name(key), cursor="hand2")
            lbl.grid(row=1, column=0, pady=(2, 0))
            lbl.bind("<Button-1>", lambda e, k=key: self.pick_theme(k))

        g2 = ttk.LabelFrame(tab, text=tr("Custom theme"), padding=(10, 6))
        g2.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        k = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        ttk.Label(g2, text=tr("Colour:")).grid(row=0, column=0, sticky="w")
        self.swatch = tk.Canvas(g2, width=int(round(40 * k)), height=int(round(22 * k)), highlightthickness=0,
                                bd=0, cursor="hand2")
        self.swatch.grid(row=0, column=1, padx=(8, 8))
        self.swatch.bind("<Button-1>", lambda e: self.pick_custom_color())
        ttk.Button(g2, text=tr("Pick colour..."), command=self.pick_custom_color).grid(row=0, column=2, sticky="w")
        ttk.Label(g2, text=tr("Base:")).grid(row=0, column=3, sticky="w", padx=(14, 0))
        self.cb_base = ttk.Combobox(g2, values=(tr("Light"), tr("Dark")), state="readonly", width=6)
        self.cb_base.set(tr("Dark") if self.v_custom_base.get() == "dark" else tr("Light"))
        self.cb_base.bind("<<ComboboxSelected>>", lambda e: self.set_custom_base(self.cb_base.get()))
        self.cb_base.grid(row=0, column=4, sticky="w", padx=(8, 0))
        ttk.Label(g2, style="Hint.TLabel", wraplength=400, justify="left",
                  text=tr("Pick any colour for the Start button, the sliders, headings and highlights. Base sets the "
                          "window colours: light grey or dark.")).grid(row=1, column=0, columnspan=5, sticky="w",
                                                                   pady=(6, 0))
        self.update_tiles()

    @staticmethod
    def theme_name(key):
        return tr({"system": "Match Windows", "custom": "Custom"}.get(key) or THEMES[key]["name"])

    def update_tiles(self):
        """Redraw Settings > Appearance: the tiles in the colours in use, the chosen one marked, the swatch."""
        if not self.tiles:
            return
        try:
            chosen = self.v_theme.get()
            accent = self.v_custom_accent.get()
            custom = custom_theme(accent if HEX_COLOR.match(accent) else DEFAULTS["custom_accent"],
                                  self.v_custom_base.get())
            for key, tile in self.tiles.items():
                preview = custom if key == "custom" else THEMES.get(key, THEMES["classic"])
                tile.update_look(self.theme, preview, key == chosen)
                self.tile_labels[key].configure(style="Bold.TLabel" if key == chosen else "TLabel")
            self.cb_base.set(tr("Dark") if self.v_custom_base.get() == "dark" else tr("Light"))
            sw = self.swatch
            sw.delete("all")
            sw.configure(bg=self.theme["face"])
            draw_bevel(sw, 0, 0, int(sw.cget("width")), int(sw.cget("height")),
                       [(self.theme[a], self.theme[b]) for a, b in SUNKEN], custom["accent"], pixel_scale(sw))
        except tk.TclError:                # the Settings window is closing
            pass

    def pick_theme(self, key):
        self.v_theme.set(key)
        self.on_theme_change()

    def pick_custom_color(self):
        res = colorchooser.askcolor(color=self.v_custom_accent.get(), title=tr("%s - Custom colour") % APP_TITLE,
                                    parent=self.settings_win or self.root)
        if res and res[1] and HEX_COLOR.match(res[1]):
            self.v_custom_accent.set(res[1].upper())
            self.pick_theme("custom")

    def set_custom_base(self, label):
        self.v_custom_base.set("dark" if label == tr("Dark") else "light")
        self.pick_theme("custom")

    @staticmethod
    def ui_language_name(code):
        """What the Settings > Language menu shows for a ui_language setting ("" = match Windows)."""
        return dict(UI_LANGUAGES).get(code) or tr("Match Windows")

    def on_ui_language_change(self, _event=None):
        """A new language for RTTS's text: it applies when RTTS starts, so offer to restart now."""
        name = self.cb_ui_lang.get()
        code = next((c for c, n in UI_LANGUAGES if n == name), "")
        if code == self.ui_language_setting:
            return
        if not messagebox.askyesno(APP_TITLE, tr("RTTS restarts to change its language. Restart now?"),
                                   parent=self.settings_win or self.root):
            self.cb_ui_lang.set(self.ui_language_name(self.ui_language_setting))
            return
        self.ui_language_setting = code
        self.save_ui_prefs()
        self.restart_app()

    def restart_app(self):
        """Start a new RTTS, then close this one (the new one waits for nothing: settings are already saved)."""
        args = [sys.executable] if FROZEN else [sys.executable, str(Path(__file__).resolve())]
        try:
            subprocess.Popen(args, cwd=str(APP_DIR), close_fds=True,
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        except Exception as e:
            log_error(traceback.format_exc())
            messagebox.showerror(APP_TITLE, tr("Couldn't restart RTTS. Close it and open it again.\n\n%s") % e)
            return
        self.on_close()

    def build_keybinds_tab(self, tab):
        g = ttk.LabelFrame(tab, text=tr("Start / Stop"), padding=(10, 6))
        g.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        tab.columnconfigure(0, weight=1)
        self.bind_btns = {}
        self.v_bind = {}
        for row, (which, label) in enumerate((("start", tr("Start RTTS:")), ("stop", tr("Stop RTTS:")))):
            ttk.Label(g, text=label).grid(row=row, column=0, sticky="w", pady=2)
            v = self.v_bind[which] = tk.StringVar(value=key_display(self.hotkeys[which]))
            ttk.Label(g, textvariable=v, width=16).grid(row=row, column=1, sticky="w", padx=(8, 8))
            b = self.bind_btns[which] = ttk.Button(g, text=tr("Change..."), command=lambda x=which: self.begin_bind(x))
            b.grid(row=row, column=2, pady=2)
        ttk.Label(g, wraplength=380, justify="left", style="Hint.TLabel",
                  text=tr("Click Change, then press the key you want (Esc cancels). These keys work in every "
                          "program, even while RTTS is in the background, and the program you're in still "
                          "gets the key too.")).grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(tab, wraplength=400, justify="left",
                  text=tr("The chat key is set in the main window, under Chat Options.")).grid(
            row=1, column=0, sticky="w", pady=(0, 6))

    def begin_bind(self, which):
        """Wait for the next key press to become the Start or Stop key (clicking Change again cancels)."""
        if self.binding == which:
            self.end_bind()
            return
        self.binding = which
        for w, b in self.bind_btns.items():
            b.configure(text=tr("Press a key...") if w == which else tr("Change..."))
        self.settings_win.focus_set()      # so the key you press doesn't also work a button in this window

    def end_bind(self):
        self.binding = None
        for b in self.bind_btns.values():
            try:
                b.configure(text=tr("Change..."))
            except tk.TclError:            # the Settings window is already gone
                pass

    def on_bind_key(self, which, key):
        """The key pressed after Change was clicked. Runs on the Tk thread."""
        self.end_bind()
        win = self.settings_win
        if win is None or not win.winfo_exists() or key == keyboard.Key.esc:
            return
        other = "stop" if which == "start" else "start"
        name = hotkey_name(key)
        shown = key_display(key)
        if name is None:
            messagebox.showwarning(
                APP_TITLE,
                tr("%s can't be used as a Start / Stop key.\n\nThese keys work in every program, so a key you "
                   "type, move or edit chat with would keep starting or stopping RTTS.\n\n"
                   "Pick a key like F1-F12, Insert or Pause.") % shown, parent=win)
            return
        if key == self.hotkeys[which]:
            return
        if key == self.hotkeys[other]:
            messagebox.showwarning(APP_TITLE, tr("%s is already your %s key.") % (shown, tr(other.capitalize())), parent=win)
            return
        if self.snap["keyspec"] == ("key", key):
            messagebox.showwarning(
                APP_TITLE, tr("%s is your chat key. Pick another key, or change the chat key first.") % shown, parent=win)
            return
        if name in ROBLOX_KEYS:
            if not messagebox.askyesno(
                    APP_TITLE,
                    tr("This keybind is already in use.\n\n%s is a Roblox key (it %s), and Roblox still gets the key "
                       "when you press it.\n\nAre you sure you want to use it? (Not recommended)")
                    % (shown, tr(ROBLOX_KEYS[name])), icon="warning", default="no", parent=win):
                return
        self.hotkeys[which] = key
        self.v_bind[which].set(shown)
        self.sync_snap()
        self.update_hotkey_labels()
        self.save_ui_prefs()
        self.refresh_ready_status()

    def update_hotkey_labels(self):
        self.btn_start.configure(text=tr("Start (%s)") % key_display(self.hotkeys["start"]))
        self.btn_stop.configure(text=tr("Stop (%s)") % key_display(self.hotkeys["stop"]))

    def close_settings(self):
        self.end_bind()
        if self.settings_win is not None:
            try:
                self.settings_win.destroy()
            except Exception:
                pass
        self.settings_win = None
        self.tiles = {}

    def confirm_large_model(self, entry):
        """The "are you sure" popup before a large (1B+) model's first download, with a check of this PC.

        Returns True to go ahead. Warnings never block it (the button becomes "Use anyway"); only a lack of
        disk space does, because the download couldn't finish."""
        checks, download, can = large_model_check(entry)
        warned = any(c.level == "warn" for c in checks)
        dark = self.theme["dark"]
        parent = self.root
        win = tk.Toplevel(parent)
        win.withdraw()
        win.title(tr("%s - Large model") % APP_TITLE)
        win.resizable(False, False)
        win.transient(parent)
        self.apply_icon(win)
        win.configure(bg=self.theme["face"])
        answer = []

        def finish(ok):
            answer.append(ok)
            win.destroy()

        f = ttk.Frame(win, padding=(18, 14, 18, 14))
        f.grid(row=0, column=0, sticky="nsew")
        f.columnconfigure(1, weight=1)
        ttk.Label(f, text=tr("%s  %s is a large model (%s)") % (LARGE_MARK, entry["name"], tr(entry["params"])),
                  style="Title.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(f, text=tr("Are you sure you want to use this model?"), style="Bold.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Label(f, wraplength=430, justify="left",
                  text=tr("Models like this need a lot of computing power: a strong graphics card (GPU) with plenty "
                          "of video memory. They are slow on lower-end devices, and can lower your FPS in "
                          "Roblox while they speak.")).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(f, wraplength=430, justify="left",
                  text=tr("It is also a large file: %s to download.") % size_text(download / 1e6)).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Separator(f).grid(row=4, column=0, columnspan=2, sticky="ew", pady=(12, 8))
        ttk.Label(f, text=tr("Your PC"), style="Hint.TLabel").grid(row=5, column=0, columnspan=2, sticky="w",
                                                                pady=(0, 4))
        symbols = {"ok": "✔", "warn": "⚠", "bad": "✖"}
        styles = {"ok": "Good", "warn": "Warn", "bad": "Bad"}
        row = 6
        for c in checks:
            ttk.Label(f, text=symbols[c.level], style="%sBold.TLabel" % styles[c.level]).grid(
                row=row, column=0, sticky="nw", padx=(2, 8))
            ttk.Label(f, text=c.text, style="%sBold.TLabel" % styles[c.level]).grid(row=row, column=1, sticky="w")
            ttk.Label(f, text=c.detail, style="Hint.TLabel", wraplength=400, justify="left").grid(
                row=row + 1, column=1, sticky="w", pady=(0, 6))
            row += 2

        bf = ttk.Frame(f)
        bf.grid(row=row, column=0, columnspan=2, sticky="e", pady=(10, 0))
        go = ttk.Button(bf, text=tr("Use anyway (not recommended)") if warned else tr("Download and use"),
                        command=lambda: finish(True))
        go.pack(side="left")
        cancel = ttk.Button(bf, text=tr("Cancel"), command=lambda: finish(False))
        cancel.pack(side="left", padx=(8, 0))
        if not can:
            go.configure(state="disabled")
        win.protocol("WM_DELETE_WINDOW", lambda: finish(False))
        win.bind("<Escape>", lambda e: finish(False))
        win.bind("<Return>", lambda e: e.widget.invoke() if isinstance(e.widget, ttk.Button) else None)

        win.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - win.winfo_reqwidth()) // 2
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - win.winfo_reqheight()) // 3)
        win.geometry("+%d+%d" % (max(0, x), max(0, y)))
        if self.v_top.get():
            win.attributes("-topmost", True)
        win.deiconify()
        set_titlebar_dark(win, dark)
        win.lift()
        cancel.focus_set()                 # Enter on its own never starts a 3 GB download
        try:
            win.grab_set()
        except tk.TclError:
            pass
        win.wait_window()
        return bool(answer and answer[0])

    def on_theme_change(self):
        self.apply_theme()
        self.save_ui_prefs()

    def on_slash_change(self):
        if self.v_slash.get():
            ok = messagebox.askokcancel(
                APP_TITLE,
                tr('Experimental feature\n\n'
                   'This only works if Roblox types a "/" into the chat box when you press "/". '
                   'Many versions do not. If yours does not, NONE of your messages will be spoken.\n\n'
                   'You can turn it off again here at any time.\n\nTurn it on?'),
                icon="warning", parent=self.settings_win or self.root)
            if not ok:
                self.v_slash.set(False)
        self.sync_snap()
        self.save_ui_prefs()

    def on_topmost_change(self):
        self.apply_topmost()
        self.save_ui_prefs()

    def apply_topmost(self):
        try:
            self.root.attributes("-topmost", bool(self.v_top.get()))
        except Exception:
            pass

    def save_ui_prefs(self):
        """Write just the look settings, leaving any other saved settings untouched."""
        data = {}
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            pass
        data["theme"] = self.v_theme.get()
        data["custom_accent"] = self.v_custom_accent.get()
        data["custom_base"] = self.v_custom_base.get()
        data["ui_language"] = self.ui_language_setting
        data["topmost"] = bool(self.v_top.get())
        data["slash_typed"] = bool(self.v_slash.get())
        data["skip_emojis"] = bool(self.v_emoji.get())
        data["auto_update"] = bool(self.v_auto.get())
        data["voice_stats"] = bool(self.v_stats.get())
        data["start_key"] = self.hotkeys["start"].name
        data["stop_key"] = self.hotkeys["stop"].name
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            self.set_status(tr("Couldn't save look settings: %s") % e)

    def on_slider(self, *_):
        self.v_speed_txt.set("%d%%" % round(self.v_speed.get()))
        self.v_vol_txt.set("%d" % round(self.v_volume.get()))

    # ---------------- posting to the Tk thread ----------------
    def post(self, fn, *args):
        self.ui_q.put((fn, args))

    def pump(self):
        try:
            while True:
                fn, args = self.ui_q.get_nowait()
                try:
                    fn(*args)
                except Exception:
                    log_error(traceback.format_exc())
        except queue.Empty:
            pass
        if not self.closing:
            self.root.after(40, self.pump)

    def set_status(self, text):
        self.v_status.set(text)

    def ready_text(self):
        if not self.running:
            return tr("Stopped")
        return tr("Running - press %s in Roblox to chat") % self.v_key.get().strip()

    def refresh_ready_status(self):
        if self.loading or (self.engine is None and not self.engine_ready.is_set()):
            return  # a loading/download message is showing
        self.set_status(self.ready_text())

    def set_progress(self, pct):
        pct = max(0, min(100, int(pct)))
        self.pb["value"] = pct
        self._pct = pct
        self.show_download_info()

    def set_speed(self, text, gen=None):
        """Download speed such as "4.2 MB/s" ("" clears it). A speed from a load that has since been replaced is ignored."""
        if gen is not None and gen != self.load_gen:
            return
        self._speed = text
        self.show_download_info()

    def show_download_info(self):
        """The label left of the progress bar: percentage and download speed, e.g. "42% - 4.2 MB/s"."""
        parts = []
        if self._pct:
            parts.append("%d%%" % self._pct)
        if self._speed:
            parts.append(self._speed)
        self.v_pct.set("  \u00b7  ".join(parts))

    def show_progress_row(self, visible):
        """Show the progress bar row (only while downloading); hiding it lets the window shrink to fit."""
        if visible:
            self.pf.grid()
        else:
            self.pf.grid_remove()

    def download_progress(self, pct, gen=None):
        """Progress of a voice file download (0-99 while it runs, 100 when it is finished)."""
        if gen is not None and gen != self.load_gen:
            return
        self.set_progress(pct)
        self.show_progress_row(pct < 100)

    # ---------------- voices / devices ----------------
    def voice_id(self):
        return self.voice_map.get(self.v_voice.get(), "")

    def fill_voice_list(self, pairs, prefer=None):
        """pairs: [(voice_id, label)]. Keeps the chosen voice if the list has it, else picks the first."""
        want = prefer if prefer is not None else self.voice_id()
        pairs = [(vid, voice_display(label)) for vid, label in pairs]
        self.voice_map = {label: vid for vid, label in pairs}
        self.cb_voice["values"] = [label for _vid, label in pairs]
        ids = [vid for vid, _label in pairs]
        pick = want if want in ids else (ids[0] if ids else "")
        self.v_voice.set(next((label for vid, label in pairs if vid == pick), ""))

    def current_entry(self):
        label = self.v_model.get()
        for e in CATALOG:
            if model_label(e) == label:
                return e
        return CATALOG_BY_ID[DEFAULT_MODEL]

    def update_info(self):
        """The two grey lines under the model menu: what it is, size, parameters, state."""
        e = self.current_entry()
        mb = entry_mb(e)
        facts = [(size_text(mb) + (tr(" per voice") if e["kind"] == "piper" else "")) if mb else tr("built in")]
        if e.get("params"):
            facts.append(tr(e["params"]))
        files = entry_files(e, self.voice_id())
        if files:
            facts.append(tr("downloaded") if files_ready(files) else tr("not downloaded yet"))
        self.v_info.set("%s\n%s" % (tr(e["note"]), " \u00b7 ".join(facts)))

    @staticmethod
    def needs_large_confirm(entry):
        """Large (1B+) models ask before their first download, and only then."""
        return is_large(entry) and not files_ready(entry_files(entry, ""))

    def confirm_startup_model(self):
        """The saved model is a large one that isn't downloaded: ask, or fall back to the default model."""
        if self.closing:
            return
        entry = self.current_entry()
        if not self.confirm_large_model(entry):
            entry = CATALOG_BY_ID[DEFAULT_MODEL]
            self.v_model.set(model_label(entry))
            self.fill_voice_list(catalog_voices(entry, self.language()), prefer=DEFAULT_VOICE)
        self.chosen_entry = entry
        self.update_info()
        self.start_engine_load(entry, self.voice_id())

    def on_model_change(self, _event=None):
        entry = self.current_entry()
        if entry is not self.chosen_entry and self.needs_large_confirm(entry):
            if not self.confirm_large_model(entry):
                self.v_model.set(model_label(self.chosen_entry))
                return
        self.chosen_entry = entry
        self.fill_voice_list(catalog_voices(entry, self.language()))
        self.update_info()
        self.request_engine()

    # ---------------- voice language ----------------
    def language(self):
        """The code of the language picked under Voice > Language."""
        name = self.v_lang.get()
        return next((code for code, n in LANGUAGES if n == name), DEFAULT_LANGUAGE)

    def show_language(self, lang, prefer_voice=None):
        """Offer only the models and voices that speak `lang`; switch model if the current one can't.

        Doesn't load anything (callers do). Returns the entry that is now picked."""
        self.v_lang.set(LANGUAGE_NAMES.get(lang, LANGUAGE_NAMES[DEFAULT_LANGUAGE]))
        models = models_for(lang)
        self.cb_model["values"] = [model_label(e) for e in models]
        entry = self.current_entry()
        if entry not in models:
            # the closest kind of model that speaks it: Kokoro if it can, else Piper Medium, else eSpeak
            entry = next((CATALOG_BY_ID[i] for i in ("kokoro-best", "kokoro-fast", "piper-medium", "piper-high",
                                                      "piper-low", "espeak") if CATALOG_BY_ID[i] in models),
                         models[0])
            self.v_model.set(model_label(entry))
        self.chosen_entry = entry
        self.fill_voice_list(catalog_voices(entry, lang), prefer=prefer_voice)
        self.update_lang_note()
        return entry

    def update_lang_note(self):
        """Tell the user which models can't speak the picked language (and how to type it, where that matters)."""
        lang = self.language()
        lines = []
        missing = [e["name"] for e in CATALOG if e not in models_for(lang)]
        if missing:
            lines.append(tr("Not every voice speaks this language. Not available in it: %s.") % ", ".join(missing))
        tip = LANGUAGE_TIPS.get(lang)
        if tip:
            lines.append(tr(tip))
        self.v_lang_note.set("\n".join(lines))
        if lines:
            self.lbl_lang_note.grid()
        else:
            self.lbl_lang_note.grid_remove()

    def on_language_change(self, _event=None):
        self.show_language(self.language())
        self.update_info()
        self.request_engine()

    def on_voice_change(self, _event=None):
        piper = self.current_entry()["kind"] == "piper"   # each Piper voice is its own file
        if (not piper and not self.loading and self.engine is not None
                and self.current_entry() is self.loaded_entry):
            self.loaded_voice = self.voice_id()
        self.update_info()
        if piper:
            self.request_engine()

    def fill_devices(self, preferred):
        try:
            self.out_devices, self.default_out = list_outputs()
        except Exception as e:
            self.out_devices, self.default_out = [], None
            self.set_status(tr("Couldn't list audio devices: %s") % e)
        names = [n for _, n in self.out_devices]
        self.cb_out["values"] = names
        idx = find_index(names, preferred)
        if idx < 0:
            idx = find_index(names, "CABLE Input")
        if idx < 0 and names:
            idx = 0
        if idx >= 0:
            self.cb_out.current(idx)
        else:
            self.v_output.set("")
        if find_index(names, "CABLE") < 0:
            self.set_status(tr("VB-Cable not found - install it, restart, then press Refresh devices")
                            if self.running else tr("Stopped - VB-Cable not found (see Get VB-Cable)"))

    def refresh_devices(self):
        keep = self.v_output.get()
        with self.audio_lock:              # no stream can open while PortAudio restarts
            if self._streams == 0:
                try:
                    sd._terminate()
                    sd._initialize()
                except Exception:
                    log_error(traceback.format_exc())
            self.fill_devices(keep)
        if find_index([n for _, n in self.out_devices], "CABLE") >= 0:
            self.refresh_ready_status()

    def target_devices(self):
        devs = []
        i = self.cb_out.current()
        if 0 <= i < len(self.out_devices):
            devs.append(self.out_devices[i][0])
        if self.v_mon.get() and self.default_out is not None and self.default_out not in devs:
            devs.append(self.default_out)
        return devs

    # ---------------- chat key ----------------
    def apply_chat_key(self):
        spec = parse_chat_key(self.v_key.get())
        taken = self.is_hotkey(spec)
        if spec is None or taken:
            messagebox.showwarning(
                APP_TITLE,
                (tr("%s is your Start / Stop key, so it can't open chat too. Pick another chat key, or change "
                    "the Start / Stop keys in Settings > Keybinds.") % key_display(spec[1])) if taken else
                (tr("That isn't a key I recognise: %r\n\nUse a single character (like /) or a key name "
                    "such as tab, enter or f2.") % self.v_key.get()))
            prev = self.snap["keyspec"]
            self.v_key.set(prev[1] if prev[0] == "char" else getattr(prev[1], "name", "/"))
            return False
        self.v_key.set(self.v_key.get().strip())
        self.sync_snap()
        self.refresh_ready_status()
        return True

    def is_hotkey(self, spec):
        """True if a parsed chat key is one of the Start / Stop keys."""
        return spec is not None and spec[0] == "key" and spec[1] in self.hotkeys.values()

    def sync_snap(self):
        spec = parse_chat_key(self.v_key.get())
        if spec is None or self.is_hotkey(spec):
            spec = self.snap["keyspec"]
        self.snap = {"keyspec": spec, "only": bool(self.v_only.get()),
                     "slash": bool(self.v_slash.get()),
                     "chat_scan": US_SCAN_CODES.get(spec[1].lower()) if spec[0] == "char" else None,
                     "start": self.hotkeys["start"], "stop": self.hotkeys["stop"]}

    # ---------------- settings ----------------
    def load_settings(self):
        data = dict(DEFAULTS)
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data.update({k: v for k, v in loaded.items() if k in DEFAULTS})
        except Exception:
            pass
        self.apply_settings(data)

    def apply_settings(self, d):
        mid = LEGACY_MODELS.get(d.get("model"), d.get("model"))
        entry = CATALOG_BY_ID.get(mid) or CATALOG_BY_ID[DEFAULT_MODEL]
        lang = d.get("language") if d.get("language") in LANGUAGE_NAMES else DEFAULT_LANGUAGE
        self.v_model.set(model_label(entry))
        self.show_language(lang, prefer_voice=str(d.get("voice") or ""))   # may swap a model that can't speak it
        self.v_speed.set(min(160, max(60, self._int(d.get("speed"), 100))))
        self.v_volume.set(min(100, max(0, self._int(d.get("volume"), 100))))
        self.hotkeys = self.load_hotkeys(d)
        self.update_hotkey_labels()
        key = str(d.get("key") or "/")
        spec = parse_chat_key(key)
        self.v_key.set(key if spec and not self.is_hotkey(spec) else "/")
        self.v_only.set(bool(d.get("only_roblox", True)))
        self.v_mon.set(bool(d.get("speakers", True)))
        self.v_interrupt.set(bool(d.get("interrupt", False)))
        self.v_slash.set(bool(d.get("slash_typed", False)))
        self.v_emoji.set(bool(d.get("skip_emojis", False)))
        self.v_auto.set(bool(d.get("auto_update", False)))
        self.v_stats.set(bool(d.get("voice_stats", False)))
        theme = LEGACY_THEMES.get(d.get("theme"), d.get("theme"))
        self.v_theme.set(theme if theme in THEME_TILES else DEFAULT_THEME)
        accent = str(d.get("custom_accent") or "")
        self.v_custom_accent.set(accent.upper() if HEX_COLOR.match(accent) else DEFAULTS["custom_accent"])
        self.v_custom_base.set("dark" if d.get("custom_base") == "dark" else "light")
        ui = d.get("ui_language")
        self.ui_language_setting = ui if ui in {c for c, _n in UI_LANGUAGES} else ""
        self.v_top.set(bool(d.get("topmost", False)))
        self.apply_theme()
        self.apply_topmost()
        self.fill_devices(str(d.get("output") or ""))
        self.on_slider()
        self.sync_snap()
        self.update_info()

    @staticmethod
    def load_hotkeys(d):
        """Start / Stop keys from saved settings; anything unusable (or both the same) falls back to F6 / F7."""
        keys = {}
        for which, default in DEFAULT_HOTKEYS.items():
            k = keyboard.Key.__members__.get(str(d.get(which + "_key") or default).lower())
            keys[which] = k if hotkey_name(k) else keyboard.Key[default]
        if keys["start"] == keys["stop"]:
            keys = {w: keyboard.Key[n] for w, n in DEFAULT_HOTKEYS.items()}
        return keys

    @staticmethod
    def _int(v, default):
        try:
            return int(round(float(v)))
        except Exception:
            return default

    def save_settings(self):
        d = {
            "voice": self.voice_id(),
            "model": self.current_entry()["id"],
            "language": self.language(),
            "ui_language": self.ui_language_setting,
            "output": self.v_output.get(),
            "speed": round(self.v_speed.get()),
            "volume": round(self.v_volume.get()),
            "key": self.v_key.get().strip(),
            "only_roblox": bool(self.v_only.get()),
            "speakers": bool(self.v_mon.get()),
            "interrupt": bool(self.v_interrupt.get()),
            "slash_typed": bool(self.v_slash.get()),
            "skip_emojis": bool(self.v_emoji.get()),
            "auto_update": bool(self.v_auto.get()),
            "voice_stats": bool(self.v_stats.get()),
            "theme": self.v_theme.get(),
            "custom_accent": self.v_custom_accent.get(),
            "custom_base": self.v_custom_base.get(),
            "topmost": bool(self.v_top.get()),
            "start_key": self.hotkeys["start"].name,
            "stop_key": self.hotkeys["stop"].name,
        }
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(d, f, indent=2)
            self.set_status(tr("Settings saved"))
        except Exception as e:
            messagebox.showwarning(APP_TITLE, tr("Couldn't save settings.\n\n%s") % e)

    def reset_settings(self):
        keep_model = self.current_entry()["id"]
        d = dict(DEFAULTS)
        d["model"] = keep_model          # don't trigger a re-download/reload on reset
        d["language"] = self.language()  # nor switch the voice to another language
        d["ui_language"] = self.ui_language_setting
        d["theme"] = self.v_theme.get()  # looks are chosen in Settings, not reset here
        d["custom_accent"] = self.v_custom_accent.get()
        d["custom_base"] = self.v_custom_base.get()
        d["topmost"] = bool(self.v_top.get())
        d["auto_update"] = bool(self.v_auto.get())
        d["voice_stats"] = bool(self.v_stats.get())   # a privacy choice, so Reset doesn't change it
        d["start_key"] = self.hotkeys["start"].name   # keybinds are chosen in Settings too
        d["stop_key"] = self.hotkeys["stop"].name
        self.apply_settings(d)
        self.request_engine()                      # the default voice may need another Piper file
        self.set_status(tr("Settings reset"))

    # ---------------- engine loading ----------------
    def request_engine(self):
        """Load the model / voice that is selected, unless it is already loaded (or loading)."""
        entry = self.current_entry()
        voice = self.voice_id()
        key = engine_key(entry, voice)
        if self.loading:
            if key == self.loading_key:
                return
        elif self.engine is not None and key == self.loaded_key:
            return
        self.start_engine_load(entry, voice)

    def start_engine_load(self, entry, voice):
        self.load_gen += 1
        gen = self.load_gen
        self.loading = True
        self.loading_key = engine_key(entry, voice)
        self.engine_ready.clear()          # speech jobs wait here until the new model is ready
        self.set_progress(0)
        self.set_speed("")
        self.show_progress_row(False)
        self.set_status(tr("Loading %s ...") % entry["name"])
        threading.Thread(target=self._load_engine, args=(entry, voice, gen), daemon=True).start()

    def _load_engine(self, entry, voice, gen):
        with self.load_lock:
            if gen != self.load_gen:
                return                     # a newer choice replaced this one while it waited
            lk = engine_key(entry, voice)
            if self.engine is not None and lk == self.loaded_key:
                self.engine_ready.set()
                self.post(self.on_engine_ready, gen)
                return
            try:
                status = lambda t: self.post(self.set_status, t)
                prog = lambda p: self.post(self.download_progress, p, gen)
                spd = lambda t: self.post(self.set_speed, t, gen)
                cancelled = lambda: gen != self.load_gen or self.closing
                eng = build_engine(entry, voice, status, prog, cancelled, spd)
                if cancelled():
                    eng.close()
                    return
                old, self.engine = self.engine, eng
                if old is not None:
                    with SYNTH_LOCK:           # let a message that is being made finish first
                        old.close()
                self.loaded_key = lk
                self.loaded_entry = entry
                self.loaded_voice = voice
                self.engine_ready.set()
                self.post(self.on_engine_ready, gen)
            except DownloadCancelled:
                return
            except Exception as e:
                log_error(traceback.format_exc())
                self.post(self.on_engine_failed, e, gen)

    def on_engine_ready(self, gen):
        if gen != self.load_gen:
            return
        self.loading = False
        self.set_progress(0)
        self.set_speed("")
        self.show_progress_row(False)
        self.fill_voice_list(self.menu_voices())           # e.g. Kokoro: only voices the file really has
        self.update_info()
        if find_index([n for _, n in self.out_devices], "CABLE") < 0:
            self.set_status(tr("Voice engine ready - VB-Cable not found (see Get VB-Cable)"))
        else:
            self.set_status(self.ready_text() if self.running else
                            tr("Voice engine ready - press Start (%s)") % key_display(self.hotkeys["start"]))

    def menu_voices(self):
        """Voices to offer for the loaded engine. A Piper engine holds just one voice, so offer the model's full list."""
        lang = self.language()
        if self.loaded_entry["kind"] == "piper":
            return catalog_voices(self.loaded_entry, lang)
        got = [p for p in self.engine.voice_list() if voice_language(self.loaded_entry, p[0]) == lang]
        return got or catalog_voices(self.loaded_entry, lang)

    def on_engine_failed(self, err, gen):
        if gen != self.load_gen:
            return
        self.loading = False
        self.set_progress(0)
        self.set_speed("")
        self.show_progress_row(False)
        if self.engine is not None:        # keep using the model that works
            self.engine_ready.set()
            self.chosen_entry = self.loaded_entry
            self.v_model.set(model_label(self.loaded_entry))
            self.fill_voice_list(self.menu_voices(), prefer=self.loaded_voice)
            self.update_info()
            self.set_status(tr("Couldn't load that model: %s") % err)
            return
        self.cancel_speech()               # don't speak messages queued for the load that failed later on
        self.set_status(tr("Voice engine failed to load: %s") % err)
        files = entry_files(self.current_entry(), self.voice_id())
        hint = ""
        if files:
            hint = ("\n\n" + tr("If it was a download problem, download these files in your browser and put them "
                                "in:") + "\n%s\n\n%s" % (MODEL_DIR, "\n".join(r.url for r in files)))
        messagebox.showerror(
            APP_TITLE, tr("The voice engine couldn't start.\n\n%s%s\n\nDetails are in %s") % (err, hint, ERROR_LOG))

    # ---------------- speaking ----------------
    def say(self, text, purge=None):
        """purge=True stops what is playing first; None (chat messages) follows the "new message stops" option."""
        if purge is None:
            purge = bool(self.v_interrupt.get())
        text = (text or "").strip()
        if self.v_emoji.get():
            text = strip_emojis(text)
        if not text:
            return
        if self.engine is None and not self.loading:
            self.set_status(tr("No voice engine is loaded - pick a model to try again"))
            return
        if purge:
            self.cancel_speech()
        speed = round(self.v_speed.get()) / 100.0
        job = {
            "text": text,
            "voice": self.voice_id(),
            "speed": min(2.0, max(0.5, speed)),
            "volume": min(1.0, max(0.0, round(self.v_volume.get()) / 100.0)),
            "devices": self.target_devices(),
            "gen": self.gen,
        }
        if not job["devices"]:
            self.set_status(tr("No audio output device selected"))
            return
        self.jobs.put(job)

    def test_speak(self):
        self.say(self.v_test.get(), purge=True)

    def cancel_speech(self):
        self.gen += 1
        try:
            while True:
                self.jobs.get_nowait()
        except queue.Empty:
            pass

    def worker_loop(self):
        while not self.closing:
            try:
                job = self.jobs.get(timeout=0.3)
            except queue.Empty:
                continue
            try:
                self.run_job(job)
            except Exception as e:
                log_error(traceback.format_exc())
                self.post(self.set_status, tr("Speech error: %s") % e)

    def run_job(self, job):
        if job["gen"] != self.gen:
            return
        while not self.engine_ready.wait(0.3):
            if self.closing or job["gen"] != self.gen:
                return
        engine = self.engine
        if engine is None:
            return
        voice = job["voice"] if job["voice"] in engine.voice_ids else engine.default_voice
        try:
            if getattr(engine, "streams", False):
                self.speak_streamed(engine, job, voice)
                if self.jobs.empty():
                    self.post(self.refresh_ready_status)
                return
            audio, sr = engine.speak(job["text"], voice, job["speed"])
        except ValueError as e:
            self.post(self.set_status, tr("Nothing speakable in that message"))
            log_error("create() rejected %r: %s" % (job["text"], e))
            self.post(self.refresh_ready_status)
            return
        if job["gen"] != self.gen:
            return
        audio = np.asarray(audio, dtype=np.float32).reshape(-1) * job["volume"]
        np.clip(audio, -1.0, 1.0, out=audio)
        audio = np.concatenate([
            np.zeros(int(sr * LEAD_SILENCE), dtype=np.float32),
            audio,
            np.zeros(int(sr * TAIL_SILENCE), dtype=np.float32),
        ])
        self.post(self.set_status, tr("Speaking..."))
        self.play(audio, sr, job["devices"], job["gen"])
        if self.jobs.empty():
            self.post(self.refresh_ready_status)

    def play(self, audio, sr, devices, gen):
        cancelled = lambda: self.gen != gen or self.closing
        threads = [threading.Thread(target=self.play_one, args=(audio, sr, d, cancelled), daemon=True)
                   for d in devices]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def speak_streamed(self, engine, job, voice):
        """Play a voice that is made while it plays (Orpheus). Sound starts after a short buffer instead of only
        once the whole message is made. The buffer is sized from how fast this PC makes speech, so playback
        doesn't catch up with it and stutter."""
        gen = job["gen"]
        cancelled = lambda: self.gen != gen or self.closing
        sr = engine.RATE
        feeds = [queue.Queue() for _ in job["devices"]]
        players = [threading.Thread(target=self.play_feed, args=(q, sr, d, cancelled), daemon=True)
                   for q, d in zip(feeds, job["devices"])]
        held = [np.zeros(int(sr * LEAD_SILENCE), dtype=np.float32)]   # audio waiting until playback starts
        held_s = made_s = 0.0
        expected_s = max(1.0, len(job["text"]) / 14.0)                 # rough length of the message (~14 chars/s)
        ratio = max(0.2, getattr(engine, "ratio", 1.0))
        started = False

        def send(piece):
            for q in feeds:
                q.put(piece)

        try:
            with SYNTH_LOCK:
                for piece in engine.stream(job["text"], voice, job["speed"], cancelled):
                    piece = np.clip(np.asarray(piece, dtype=np.float32) * job["volume"], -1.0, 1.0)
                    made_s += len(piece) / float(sr)
                    if started:
                        send(piece)
                        continue
                    held.append(piece)
                    held_s += len(piece) / float(sr)
                    # start once the buffer covers what playback would gain on the voice before it is finished
                    need = 0.3 + max(0.0, (expected_s - made_s) * (1.0 / ratio - 1.0))
                    if held_s >= need:
                        for t in players:
                            t.start()
                        for p in held:
                            send(p)
                        held, started = [], True
                        self.post(self.set_status, tr("Speaking..."))
            if cancelled():
                return
            if not started:                                            # short message: all made before the start
                for t in players:
                    t.start()
                for p in held:
                    send(p)
                started = True
                self.post(self.set_status, tr("Speaking..."))
            send(np.zeros(int(sr * TAIL_SILENCE), dtype=np.float32))
        finally:
            send(None)                                                 # tells each player there is no more
            if started:
                for t in players:
                    t.join()

    def play_feed(self, feed, sr, device, cancelled):
        """Play pieces of audio from `feed` on one device until it sends None (or the message is cancelled)."""
        with self.audio_lock:
            self._streams += 1
        try:
            stream, rate, ch = self.open_output(sr, device)
            try:
                stream.start()
                aborted = False
                while not aborted:
                    try:
                        piece = feed.get(timeout=0.1)
                    except queue.Empty:
                        aborted = cancelled()
                        continue
                    if piece is None:
                        break
                    data = piece if rate == sr else resample_linear(piece, sr, rate)
                    for i in range(0, len(data), CHUNK_FRAMES):
                        if cancelled():
                            aborted = True
                            break
                        chunk = data[i:i + CHUNK_FRAMES]
                        block = np.repeat(chunk[:, None], ch, axis=1) if ch > 1 else chunk[:, None]
                        stream.write(np.ascontiguousarray(block, dtype=np.float32))
                if aborted:
                    stream.abort()
                else:
                    stream.stop()      # lets the last bit finish playing
            finally:
                stream.close()
        except Exception as e:
            log_error("Audio error on device %s:\n%s" % (device, traceback.format_exc()))
            self.post(self.set_status, tr("Audio error: %s") % e)
        finally:
            with self.audio_lock:
                self._streams = max(0, self._streams - 1)

    def open_output(self, sr, device):
        """Open an output stream on `device`, trying a few formats; return (stream, sample_rate, channels)."""
        last = None
        for rate in (sr, 48000):
            for ch in (2, 1):
                try:
                    return sd.OutputStream(samplerate=rate, device=device, channels=ch, dtype="float32"), rate, ch
                except Exception as e:
                    last = e
        raise last

    def open_stream(self, audio, sr, device):
        """Try a few formats; return (stream, audio_for_that_stream, channels)."""
        stream, rate, ch = self.open_output(sr, device)
        return stream, (audio if rate == sr else resample_linear(audio, sr, rate)), ch

    def play_one(self, audio, sr, device, cancelled):
        stream = None
        with self.audio_lock:
            self._streams += 1
        try:
            stream, data, ch = self.open_stream(audio, sr, device)
            try:
                stream.start()
                aborted = False
                for i in range(0, len(data), CHUNK_FRAMES):
                    if cancelled():
                        aborted = True
                        break
                    chunk = data[i:i + CHUNK_FRAMES]
                    block = np.repeat(chunk[:, None], ch, axis=1) if ch > 1 else chunk[:, None]
                    stream.write(np.ascontiguousarray(block, dtype=np.float32))
                if aborted:
                    stream.abort()
                else:
                    stream.stop()      # lets the last bit finish playing
            finally:
                stream.close()
        except Exception as e:
            log_error("Audio error on device %s:\n%s" % (device, traceback.format_exc()))
            self.post(self.set_status, tr("Audio error: %s") % e)
        finally:
            with self.audio_lock:
                self._streams = max(0, self._streams - 1)

    # ---------------- start / stop ----------------
    def start_app(self):
        if not self.apply_chat_key():
            return
        self.running = True
        self.report_voice()
        if self.engine is None and not self.engine_ready.is_set():
            self.set_status(tr("Running - voice engine is still loading, messages will be spoken once it's ready"))
        else:
            self.set_status(self.ready_text())

    def report_voice(self):
        """With voice stats on, send the model and voice once per launch for each voice that is started."""
        if not self.v_stats.get():
            return
        pick = (self.current_entry()["id"], self.voice_id())
        if pick in self.stats_sent:
            return
        self.stats_sent.add(pick)
        threading.Thread(target=send_voice_stat, args=pick, daemon=True).start()

    def stop_app(self):
        self.running = False
        self.cancel_capture()
        self.cancel_speech()
        self.set_status(self.ready_text() if self.engine_ready.is_set() else tr("Stopped"))

    # ---------------- chat capture (runs on the keyboard listener thread) ----------------
    def on_key_press(self, key, *_):
        try:
            self._handle_key(key)
        except Exception:
            log_error(traceback.format_exc())

    def _handle_key(self, key):
        # Settings > Keybinds is waiting for a new Start / Stop key: this press is it, and does nothing else.
        which = self.binding
        if which is not None:
            self.binding = None
            self.post(self.on_bind_key, which, key)
            return
        # Global start/stop hotkeys (work any time, keys still reach other programs).
        snap = self.snap
        if key == snap["start"]:
            self.post(self.start_app)
            return
        if key == snap["stop"]:
            self.post(self.stop_app)
            return
        if not self.running:
            return

        with self.cap_lock:
            if self.cap_active:
                self._capture_key(key)
                return
            if self._is_chat_key(key) and (not self.snap["only"] or roblox_active()):
                self.cap_active = True
                self.cap_sel = False
                # If Roblox types the "/" into the box when it opens, mirror that so slash commands
                # (/e dance, /mute ...) are recognised and not spoken.
                kind, val = self.snap["keyspec"]
                self.cap_buf = ["/"] if (self.snap["slash"] and kind == "char" and val == "/") else []
                self.cap_pos = len(self.cap_buf)
                self.cap_started = time.time()
                self.post(self.set_status, tr("Listening to your chat message..."))

    def _is_chat_key(self, key):
        kind, val = self.snap["keyspec"]
        if kind == "key":
            return key == val
        ch = getattr(key, "char", None)
        if ch and not getattr(key, "is_dead", False) and ch.lower() == val.lower():
            return True
        # With a non-Latin keyboard (Russian, Arabic, Hindi ...) the chat key's spot types another character: the
        # "/" key types "." on a Russian keyboard. Roblox still opens chat with that key, so match where it is.
        # Latin layouts (German, French ...) keep matching the character only, as before.
        scan = self.snap.get("chat_scan")
        return bool(scan) and self.non_latin_layout and getattr(key, "_scan", None) == scan

    def _capture_key(self, key):
        # caller holds cap_lock
        if self.snap["only"] and not roblox_active():
            self._end_capture_locked(None)
            return
        K = keyboard.Key
        if key == K.enter:
            self._end_capture_locked(self._finish_text())
            return
        if key == K.esc:
            self._end_capture_locked(None)
            return

        ctrl = ctrl_only_down()
        shift = shift_down()

        # Keys that move the cursor somewhere we can't follow (or select text): give up on this
        # message rather than speak something different from what ends up in the chat box.
        if key in (K.up, K.down, K.page_up, K.page_down) or (
                shift and key in (K.left, K.right, K.home, K.end)):
            self._end_capture_locked(None, tr("Message cancelled (cursor/selection keys aren't supported)"))
            return

        if key == K.left:
            self._move_cursor(self._word_start(self.cap_pos) if ctrl else self.cap_pos - 1, -1)
            return
        if key == K.right:
            self._move_cursor(self._word_end(self.cap_pos) if ctrl else self.cap_pos + 1, 1)
            return
        if key == K.home:
            self._move_cursor(0, -1)
            return
        if key == K.end:
            self._move_cursor(len(self.cap_buf), 1)
            return
        if key == K.backspace:
            if self._clear_selection():
                return
            start = self._word_start(self.cap_pos) if ctrl else max(0, self.cap_pos - 1)
            del self.cap_buf[start:self.cap_pos]
            self.cap_pos = start
            return
        if key == K.delete:
            if self._clear_selection():
                return
            end = self._word_end(self.cap_pos) if ctrl else min(len(self.cap_buf), self.cap_pos + 1)
            del self.cap_buf[self.cap_pos:end]
            return
        if key == K.space:
            self._insert(" ")
            return

        vk = getattr(key, "vk", None)
        if ctrl:
            if vk == 0x41:        # Ctrl+A
                self.cap_sel = bool(self.cap_buf)
            elif vk == 0x56:      # Ctrl+V
                text = " ".join(clipboard_text().split())    # one line, tabs/newlines -> spaces
                text = "".join(c for c in text if c.isprintable())
                if text:
                    self._clear_selection()
                    self._insert(text)
            elif vk == 0x58:      # Ctrl+X cuts the selection (if any)
                self._clear_selection()
            return                # every other Ctrl shortcut: no text

        ch = getattr(key, "char", None)
        if not ch or getattr(key, "is_dead", False) or not ch.isprintable():
            return
        if len(ch) == 1 and ch.isalpha() and caps_lock_on():
            ch = ch.swapcase()   # pynput's layout table ignores Caps Lock
        self._clear_selection()
        self._insert(ch)

    # ---- tiny text-box model (all callers hold cap_lock) ----
    def _insert(self, text):
        room = MAX_CHAT_CHARS - len(self.cap_buf)
        text = text[:max(0, room)]
        if text:
            self.cap_buf[self.cap_pos:self.cap_pos] = list(text)
            self.cap_pos += len(text)

    def _clear_selection(self):
        """If everything is selected (Ctrl+A), delete it. Returns True if something was selected."""
        if not self.cap_sel:
            return False
        self.cap_sel = False
        self.cap_buf = []
        self.cap_pos = 0
        return True

    def _move_cursor(self, pos, direction):
        """Move the cursor. direction is -1 (left/home) or +1 (right/end)."""
        sel = self.cap_sel
        self.cap_sel = False
        if sel:                             # collapsing a selection jumps to its start or end
            pos = 0 if direction < 0 else len(self.cap_buf)
        self.cap_pos = max(0, min(len(self.cap_buf), pos))

    def _word_start(self, pos):
        i = pos
        while i > 0 and self.cap_buf[i - 1] == " ":
            i -= 1
        while i > 0 and self.cap_buf[i - 1] != " ":
            i -= 1
        return i

    def _word_end(self, pos):
        n, i = len(self.cap_buf), pos
        while i < n and self.cap_buf[i] == " ":
            i += 1
        while i < n and self.cap_buf[i] != " ":
            i += 1
        return i

    def _finish_text(self):
        return "".join(self.cap_buf).strip()

    def _end_capture_locked(self, text, note=None):
        self.cap_active = False
        self.cap_buf = []
        self.cap_pos = 0
        self.cap_sel = False
        self.post(self.refresh_ready_status)
        if note:
            self.post(self.set_status, note)
        if text and not text.startswith("/"):
            self.post(self.say, text)
            self.post(self.add_recent, text)

    def cancel_capture(self):
        with self.cap_lock:
            if self.cap_active:
                self._end_capture_locked(None)

    def on_mouse_click(self, x, y, button, pressed):
        if pressed and self.cap_active:
            self.cancel_capture()

    def tick(self):
        if self.closing:
            return
        try:
            with self.cap_lock:
                if self.cap_active:
                    if time.time() - self.cap_started > CAPTURE_TIMEOUT:
                        self._end_capture_locked(None)
                    elif self.snap["only"] and not roblox_active():
                        self._end_capture_locked(None)
            self.follow_keyboard_layout()
        except Exception:
            log_error(traceback.format_exc())
        self.root.after(250, self.tick)

    def follow_keyboard_layout(self):
        """Keep the key listener's letters in step with the keyboard layout of the window in front.

        pynput works out which letter a key types from a table it builds for one layout: the layout of the
        thread that builds it, never the window in front. Without this, Russian / Arabic / Hindi typing would be
        read as English letters. The table is rebuilt here, on the Tk thread (building it must not happen inside
        the keyboard hook), for the layout of the window in front."""
        if not IS_WIN:
            return
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return
        pid = wintypes.DWORD()
        thread = _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        layout = _user32.GetKeyboardLayout(thread)
        if not layout or layout == self.last_layout:
            return
        self.last_layout = layout
        self.non_latin_layout = (layout & 0x3FF) in NON_LATIN_LANGUAGE_IDS     # low word = the language id
        translator = getattr(self.kb_listener, "_translator", None)
        if translator is None or not hasattr(translator, "_thread_input"):
            return

        @contextlib.contextmanager
        def front_thread():                        # pynput asks this for the thread whose layout to use
            h = _user32.GetForegroundWindow()
            yield _user32.GetWindowThreadProcessId(h, ctypes.byref(wintypes.DWORD())) if h else thread

        translator._thread_input = front_thread
        translator.update_layout()                 # a few ms: ~2000 ToUnicodeEx calls

    # ---------------- recent messages ----------------
    def add_recent(self, text):
        line = "%s  %s" % (time.strftime("%H:%M:%S"), text)
        self.recent.insert(0, line)
        del self.recent[8:]
        self.txt_log.configure(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.insert("1.0", "\n".join(self.recent))
        self.txt_log.configure(state="disabled")

    # ---------------- updates ----------------
    def start_update_check(self, manual=False):
        """Ask GitHub for the latest release on a background thread. manual=True also reports "up to date" and errors."""
        if self.closing or self.update_busy or self.update_checking:
            return
        self.update_checking = True
        if not manual:
            shutil.rmtree(UPDATE_DIR, ignore_errors=True)     # the installer from the last update is done by now

        def work():
            try:
                rel, err = fetch_latest_release(), None
            except Exception as e:
                rel, err = None, e
            self.post(self.on_update_result, rel, err, manual)

        threading.Thread(target=work, daemon=True).start()

    def on_update_result(self, rel, err, manual):
        self.update_checking = False
        if self.closing or self.update_busy:
            return
        parent = self.settings_win if manual and self.settings_win is not None else self.root
        if err is not None:
            if manual:
                messagebox.showwarning(APP_TITLE, tr("Couldn't check for updates.\n\n%s") % err, parent=parent)
            return
        if rel is None or version_tuple(rel["version"]) <= version_tuple(APP_VERSION):
            if manual:
                messagebox.showinfo(APP_TITLE, tr("You have the latest version (RTTS %s).") % APP_VERSION, parent=parent)
            return
        if self.v_auto.get() and not manual and FROZEN and rel["asset"]:
            self.install_update(rel)
            return
        if messagebox.askyesno(
                tr("%s update") % APP_TITLE,
                tr("RTTS %s is out. You have %s.\n\nDo you want to update now?\n\n"
                   "Yes (recommended): updates keep RTTS stable and fix bugs. RTTS closes, updates, "
                   "and opens again, and your voices and settings are kept.\n\n"
                   "No: keep using this version. RTTS asks again next time it starts.")
                % (rel["version"], APP_VERSION), icon="question", parent=parent):
            self.install_update(rel)

    def install_update(self, rel):
        """Download the new installer with a progress window, then run it and close RTTS (it reopens after)."""
        if not (FROZEN and rel["asset"]):
            webbrowser.open(rel["url"])       # running from source, or the release has no installer: show the page
            return
        self.update_busy = True
        name, url, size, sha = rel["asset"]
        win = tk.Toplevel(self.root)
        win.title(tr("Updating %s") % APP_TITLE)
        win.resizable(False, False)
        win.transient(self.root)
        win.configure(bg=self.theme["face"])
        self.apply_icon(win)
        f = ttk.Frame(win, padding=(14, 12))
        f.grid(row=0, column=0)
        set_titlebar_dark(win, self.theme["dark"])
        v_text = tk.StringVar(value=tr("Downloading RTTS %s ...") % rel["version"])
        ttk.Label(f, textvariable=v_text, width=46).grid(row=0, column=0, sticky="w")
        pb = ttk.Progressbar(f, length=320, maximum=100)
        pb.grid(row=1, column=0, sticky="ew", pady=(8, 8))
        cancelled = [False]

        def cancel():
            cancelled[0] = True

        ttk.Button(f, text=tr("Cancel"), command=cancel).grid(row=2, column=0, sticky="e")
        win.protocol("WM_DELETE_WINDOW", cancel)
        self.root.update_idletasks()
        win.geometry("+%d+%d" % (self.root.winfo_x() + 40, self.root.winfo_y() + 60))
        shown = [-1]

        def show(pct):
            pb["value"] = pct
            v_text.set(tr("Downloading RTTS %s: %d%%") % (rel["version"], pct))

        def progress(done, total):
            if cancelled[0] or self.closing:
                raise DownloadCancelled()
            pct = int(done * 100 // total) if total else 0
            if pct != shown[0]:
                shown[0] = pct
                self.post(show, pct)

        def finish(path, err):
            self.update_busy = False
            try:
                win.destroy()
            except Exception:
                pass
            if path is None:
                if err is not None and messagebox.askyesno(
                        APP_TITLE, tr("Couldn't download the update.\n\n%s\n\nOpen the download page on GitHub instead?")
                        % err, icon="warning"):
                    webbrowser.open(rel["url"])
                return
            try:
                # /RELAUNCH=1 makes the installer open RTTS again when it's done (see RTTS.iss)
                subprocess.Popen([str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/RELAUNCH=1"],
                                 creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                                 close_fds=True)
            except Exception as e:
                log_error(traceback.format_exc())
                messagebox.showerror(APP_TITLE, tr("Couldn't start the update.\n\n%s") % e)
                return
            self.on_close()

        def work():
            path = UPDATE_DIR / name
            try:
                UPDATE_DIR.mkdir(parents=True, exist_ok=True)
                download_file(url, path, size, sha, progress)
            except DownloadCancelled:
                self.post(finish, None, None)
                return
            except Exception as e:
                log_error(traceback.format_exc())
                self.post(finish, None, e)
                return
            self.post(finish, path, None)

        threading.Thread(target=work, daemon=True).start()

    # ---------------- shutdown ----------------
    def on_close(self):
        self.closing = True
        self.cancel_speech()
        for lst in (self.kb_listener, self.ms_listener):
            try:
                lst.stop()
            except Exception:
                pass
        if self.engine is not None:
            try:
                self.engine.close()            # e.g. stops Orpheus's llama-server right away
            except Exception:
                pass
        self.root.destroy()


def saved_ui_language():
    """RTTS's text language: the one picked in Settings, else Windows' own language if RTTS has it, else English."""
    code = ""
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            code = str(json.load(f).get("ui_language") or "")
    except Exception:
        pass
    known = {c for c, _name in UI_LANGUAGES}
    return code if code in known else windows_ui_language()


def main():
    global UI_FAMILY, FONT_SIZE
    if IS_WIN:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    ui_lang = saved_ui_language()
    set_ui_language(ui_lang)
    load_bundled_fonts()
    root = tk.Tk()
    try:
        families = set(tkfont.families(root))
        if ui_lang in UI_FONTS:
            # Space Mono only has Latin letters: these languages use a Windows font made for their script
            family, size = UI_FONTS[ui_lang]
            if family in families:
                UI_FAMILY, FONT_SIZE = family, size
        if UI_FAMILY not in families:
            UI_FAMILY = "Consolas"      # the bundled font is missing; Consolas comes with Windows
    except Exception:
        log_error(traceback.format_exc())
    try:
        App(root)
    except Exception:
        log_error(traceback.format_exc())
        messagebox.showerror(APP_TITLE, tr("The app hit an error while starting.\n\nDetails are in:\n%s") % ERROR_LOG)
        raise SystemExit(1)
    root.mainloop()


if __name__ == "__main__":
    main()