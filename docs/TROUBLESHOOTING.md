# Troubleshooting

## Python is missing or the installer rejects it

Use Python **3.12, Windows x64** from python.org. In PowerShell, `py -3.12 --version`
should report 3.12.x. Enable the Python launcher or PATH option in the installer.
Close and reopen the terminal after installation. A Microsoft Store alias alone
is not a complete Python installation.

## Package installation or model downloads fail

Check the network connection, free disk space, and error shown above the final
message. Rerun **Install Studio.cmd**. Model downloads keep completed files and
can resume partial files. Networks that block PyPI or Hugging Face can prevent
setup; do not disable certificate checks as a workaround.

If an inference library reports a missing Windows DLL, install Microsoft's
[Visual C++ Redistributable x64](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist),
then retry. Python package versions are pinned in `requirements.txt`; avoid
randomly mixing newer packages into the tested environment.

## The website is unavailable

Open **Start Studio.cmd** first and keep it running. Visit
<http://127.0.0.1:9764>. Do not use an old preview-server address. If the launcher
shows a port conflict, close the older Zirnevis launcher before opening another.
If another unrelated application uses this port, developers can adjust the
launcher and server port consistently; see `launch.py` and `STUDIO_PORT` notes.

## An update notice appears or new buttons do not work

Let active work finish, close the previous launcher, start it again, and refresh
the browser. The running Python process continues using its old code until
restarted. Updating files without restarting is insufficient.

## YouTube says Node.js is missing

Install Node.js **22 or newer** from nodejs.org. Reopen the launcher so it picks
up the updated PATH. `node --version` should work in a newly opened terminal.
Node is needed by the YouTube downloader, not uploaded-file translation.

## YouTube download or quality lookup fails

The video may be restricted, require sign-in, be unavailable from your connection,
or use a format the current downloader cannot access. Try an unrestricted video
or upload an allowed local copy. Some videos do not offer the resolution you want.
The app only lists available qualities and checks the downloaded dimensions.
YouTube changes can require a reviewed yt-dlp update; dependency pins do not
guarantee every future video will remain downloadable.

## A process seems stuck

Read the card's step and message. High-resolution codec conversion, model loading,
speech recognition, and CPU rendering can take time. Playback preparation copies
compatible video streams when possible and reports conversion progress otherwise.
Check that free disk space remains. Use **Cancel** if needed, then **Resume**.
Inspect that project's `worker.log`, `preview.log`, or `render.log` for errors,
but remove paths and private text before sharing a log.

## Recognition or translation is wrong

Choose the spoken language explicitly, use clearer audio, and edit the result.
Whisper small and NLLB run locally and have accuracy limits. Audio with music,
several speakers, unusual accents, or specialist vocabulary is harder to handle.
Translation through an English bridge can lose meaning. This version does not
provide a model selector or an OpenAI API option.

## English text is missing

On an older project, select English and click **Generate English subtitles**.
Wait for it to finish, review the text, and save. English is a separate field;
editing Persian does not regenerate English automatically.

## Captions move, disappear, or use the wrong position

Use the website's Fullscreen button rather than an external player. Drag a visible
caption and click Save. Fullscreen uses the entire player wrapper so the overlay
remains present. A downloaded video uses the position from its last export;
export again after changing it. Ensure you restarted after installing an update.

## Saving reports invalid timing

Each subtitle must end after it starts, stay within the media duration, and avoid
overlapping the previous cue. Text must be present. Fix those fields and save
again. Persian and English share timings.

## Open folder, rename, or delete fails

Copy the shown path into File Explorer. For rename or deletion, close programs
holding the media and wait until processing stops. Renaming cannot overwrite
another folder. Windows folder names cannot contain certain characters; the app
normalizes them. Linked folders outside the library are not supported.

## A download contains old edits

Save before downloading SRT or TXT. For video, export again after edits. The
previously exported MP4 is kept until a new export finishes; a new download alone
does not rerender it. Browser downloads are separate from the studio's saved files.

## I need to free space or recover deleted projects

Use Delete or Clear all videos after keeping the downloads you want. Model files
remain to avoid re-downloading them. Restore deleted project folders from the
Windows Recycle Bin and restart the app. Removing the entire application folder
also removes its local models and projects; back them up first.
