<!-- LOGO: add your logo as docs/images/logo.png, then delete this line and the closing line below
<p align="center"><img src="docs/images/logo.png" alt="RTTS logo" width="160"></p>
-->

<h1 align="center"><img src="docs/images/title.png" alt="RTTS" height="64"></h1>
<p align="center"><b>Roblox Text-to-Speech</b><br>Type in Roblox chat. Be heard in voice chat.</p>

<p align="center">
  <a href="../../releases/latest"><b>Download for Windows</b></a>
</p>

<!-- DEMO: add an image or GIF as docs/images/demo.png, then delete this line and the closing line below
<p align="center"><img src="docs/images/demo.png" alt="RTTS in action" width="720"></p>
-->

---

## What is RTTS?

RTTS is a Python-based app that lets you use voice chat without having to speak yourself. It listens to what you type in the Roblox window and speaks it using voice models that run on your own PC.

It plays the voice through a virtual microphone ([VB-Cable](https://vb-audio.com/Cable/)), and Roblox hears that as your mic. It comes with a bunch of voice models to choose from, from tiny robotic voices to very realistic ones.

<!-- SCREENSHOT: add the app window as docs/images/screenshot.png, then delete this line and the closing line below
<p align="center"><img src="docs/images/screenshot.png" alt="The RTTS window" width="520"></p>
-->

## AI use

**The code.** RTTS was made using AI, and AI was heavily used to write the code, mostly with [Claude](https://claude.com/claude-code) by Anthropic. The full source is right here if you want to see how it works.

**The voices.** RTTS uses AI voice models to do the talking. Kokoro, Piper and Orpheus are AI models, while eSpeak is a classic robotic voice that isn't AI.

**Your messages.** AI only turns your text into speech. It doesn't change, rewrite or reply to anything you type. RTTS says exactly what you wrote.

**Where the AI runs.** Every voice runs on your own PC, so your messages never leave it (see [Privacy](#privacy)).

## How to set up RTTS

First, install VB-Cable from [vb-audio.com/Cable](https://vb-audio.com/Cable/). Restart your computer when it asks you to.

Next, download `RTTS-Setup-<version>.exe` from [Releases](../../releases/latest) and run it. The installer checks for VB-Cable, and lets you pick voices to download right away. Installing a new version over an old one keeps your voices and settings.

Open RTTS and set **Output device** to **CABLE Input**. Then, in Roblox, set your microphone to **CABLE Output**.

Press **Start (F6)**, open chat in Roblox (the default chat key is `/`), type, and press Enter. Press **Stop (F7)** when you're done. You can change the Start and Stop keys in **Settings > Keybinds**.

The first time you open RTTS it downloads a voice (a few hundred MB), so give it a minute. You can switch voice models any time from the menu in the app.

## Privacy

RTTS reads your keyboard only to see what you type in chat, and by default only while Roblox is the active window. The code is public, so you can check.

Every voice runs on your PC, so your messages are never sent anywhere. RTTS needs no account or API key, and the only internet use is downloading the voices.

Orpheus runs in a helper program (llama.cpp's `llama-server`) that RTTS starts on your PC. It only accepts connections from your own computer, and RTTS closes it when you switch models or close RTTS.

## FAQ

**Windows says "Windows protected your PC".**
The installer isn't code-signed. Click **More info**, then **Run anyway**. Only download RTTS from this repo's Releases.

**My antivirus flags it.**
Some antivirus programs may flag it because it listens to what you type, which is also what malware called keyloggers does. RTTS only reads what you type while Roblox is the active window, and the full source is in this repo so anyone can check.

**Orpheus is slow, or my game lags while it speaks.**
Orpheus needs a strong graphics card. If yours struggles, pick Kokoro Best instead. It still sounds very natural and runs on almost any PC.

**Something went wrong.**
Most errors are saved in `%APPDATA%\RTTS\error.log`. If you report a bug, please include that file.

**Does it work on Mac or Linux?**
No, it's Windows only for now.

## Build it yourself

You need Windows and Python 3.10 or newer (tick *Add python.exe to PATH* and *tcl/tk* when installing).

Run `installer.bat` to install the Python libraries RTTS needs (it doesn't install the app). Use `start.bat` to run RTTS straight from the source, `build.bat` to build `dist\RTTS\RTTS.exe`, and `make_installer.bat` to build `installer_output\RTTS-Setup-<version>.exe` (set the version at the top of `RTTS.iss`), which needs [Inno Setup 6](https://jrsoftware.org/isdl.php).

## Credits

RTTS is built on great open-source work: [Kokoro](https://huggingface.co/hexgrad/Kokoro-82M) and [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx), [Piper](https://github.com/rhasspy/piper) and [piper-voices](https://huggingface.co/rhasspy/piper-voices), [espeak-ng](https://github.com/espeak-ng/espeak-ng), [Orpheus TTS](https://github.com/canopyai/Orpheus-TTS) by Canopy Labs (using [Unsloth's GGUF version](https://huggingface.co/unsloth/orpheus-3b-0.1-ft-GGUF)), [llama.cpp](https://github.com/ggml-org/llama.cpp), [SNAC](https://github.com/hubertsiuzdak/snac) (using the [ONNX version](https://huggingface.co/onnx-community/snac_24khz-ONNX)), and [VB-Cable](https://vb-audio.com/Cable/). Each project has its own license, so check them before redistributing anything.

## Disclaimer

RTTS is an independent project. It is **not affiliated with, endorsed by, or sponsored by Roblox Corporation**. "Roblox" is a trademark of Roblox Corporation. Use RTTS in line with Roblox's rules.

## License

See the [LICENSE](LICENSE) file.