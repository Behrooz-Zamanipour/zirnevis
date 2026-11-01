# Install Zirnevis on Windows

## Before you start

- Use a Windows x64 laptop or desktop with a modern browser.
- Install **Python 3.12 (64-bit)** from the [official Windows downloads](https://www.python.org/downloads/windows/).
  Select the Python launcher or **Add Python to PATH**. The installer deliberately
  targets Python 3.12 to match the tested dependency wheels.
- For YouTube support, install **Node.js 22 or newer** from
  [the official download page](https://nodejs.org/en/download). Upload-only use
  does not need Node.js.
- Have internet access for setup and several GB of free space, plus media storage.
- Download **Code → Download ZIP** and extract it to a writable local folder.
  Avoid running inside a ZIP, `Program Files`, a read-only folder, or a syncing
  cloud folder while processing.

Python and Node.js are installed separately; they are not bundled in this repository.
The project does not depend on Codex, ChatGPT, a paid API, or the author's computer.

## Install

1. Double-click **Install Studio.cmd**.
2. It creates `.venv`, installs the versions in `requirements.txt`, checks FFmpeg,
   and downloads the speech and translation models into `data/models`.
3. Wait for the **Ready** message. Downloads can take time; model files total
   approximately 1.1 GB, in addition to Python packages.
4. If a model download is interrupted, run the installer again. Completed model
   files are reused and partial model downloads can resume.
5. Double-click **Start Studio.cmd**. Keep its window open while using the app.

Both command files can be opened in a text editor before running them.
`Install Studio.cmd` runs the included PowerShell script for this installation;
it does not permanently change the system execution policy.

## Open it next time

Double-click **Start Studio.cmd** again. The browser normally opens automatically.
If it does not, visit <http://127.0.0.1:9764>. You can bookmark that address, but the
launcher must be running before the page is available. Close the launcher when done.

After setup, uploaded media works offline. YouTube links require internet access.
Keep the whole application folder together; moving an installed virtual environment
can break its paths. If you move the source to another folder, close the app and
reinstall its environment there. Preserve `data` to keep your work and models.

## Manual installation for developers

Open PowerShell in the repository folder:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe prepare_models.py
.\.venv\Scripts\python.exe launch.py
```

No activation command is required. FFmpeg is supplied by `imageio-ffmpeg`.
Use an ordinary x64 Python installation rather than the Microsoft Store app alias.
Some systems may also need the current Microsoft Visual C++ Redistributable x64
for the native inference packages; see [troubleshooting](TROUBLESHOOTING.md).

## Updating

Wait for active work to finish and close the launcher. Back up `data/jobs` first.
Update the source files in the same folder, keeping `data`, then rerun
**Install Studio.cmd** if dependencies changed. Restart **Start Studio.cmd** and
refresh the browser. Do not replace files during an active export.

## Back up or uninstall

Close the launcher before copying or removing files. Back up `data/jobs` for media,
edits, and exports; include `data/models` if you want to avoid downloading models
again. A fresh source copy plus these folders can restore projects after reinstalling.

Zirnevis itself can be removed by deleting its extracted folder. Python and Node.js
are shared programs and remain installed. Browser download copies remain separate.
