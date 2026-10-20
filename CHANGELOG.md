# Changelog

## 1.1.0

**New look**
- RTTS has a new retro design: raised grey buttons, sunken text boxes, etched group boxes, a red Start button, block progress bars, and the Space Mono font (it comes with RTTS, so nothing needs installing).
- New pixel icon for the app, the taskbar, the desktop shortcut and the installer, and new artwork in the installer. Updating refreshes the shortcut icons right away.

**New: colour themes**
- Settings > Appearance has 7 themes: Classic, Midnight, Ocean, Grape, Bubblegum, Terminal and Amber. Click one to switch right away.
- **Match Windows** uses Classic or Midnight to follow Windows' light or dark mode.
- **Custom** lets you pick any colour for the Start button, the sliders, headings and highlights, on a light or dark base.
- If you used the Light or Dark theme before, you get Classic or Midnight.

## 1.0.1

**New: optional anonymous voice stats**
- The installer has a new box, **unticked** by default: "Send anonymous stats about which voice I use".
- You can turn it on or off anytime in Settings > General > About and updates. Updates never change your choice.
- When it's on, pressing Start sends only which voice you use (for example "kokoro / af_heart"), once per voice each time you open RTTS. It never sends what you type, your Roblox username, or anything that identifies you or your PC. See [Privacy](README.md#privacy).

## 1.0.0

The first public release.

**Voices**
- Speaks what you type in Roblox chat through VB-Cable, so Roblox voice chat hears it as your microphone.
- Every voice runs on your own PC: eSpeak (built in), Piper Low / Medium / High, Kokoro Faster / Best, and ⚡ Orpheus.
- ⚡ Orpheus is the most realistic voice. It's a large model (3B parameters), so RTTS warns you and checks your PC before its first download.

**App**
- Start (F6) and Stop (F7) keys, which you can change in Settings > Keybinds. RTTS warns you if you pick a key Roblox already uses.
- Options to only work while Roblox is the active window, also play the voice on your speakers, and let a new message stop the one that is playing.
- Experimental option to skip emojis instead of reading them out.
- Light, dark or match-Windows theme.
- Checks GitHub for a new version every time it starts and asks if you want to update, or updates by itself if you turn on automatic updates.
- Settings > About and updates shows your version, with buttons to check for updates and open the GitHub page.

**Installer**
- Checks for VB-Cable and helps you install it.
- Lets you pick voices to download during the install.
- Installing a new version over an old one keeps your voices and settings.
- When you uninstall, it asks before deleting downloaded voices and settings.
