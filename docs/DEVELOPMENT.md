# Developer notes

## Layout

| File | Responsibility |
| --- | --- |
| `launch.py` | Starts the local service and opens the browser. |
| `server.py` | Local request checks, project storage, uploads, queue, workers, file actions. |
| `job_worker.py` | Runs one processing/export/English-generation task in its own process. |
| `engine.py` | Probing, playback preparation, local models, cue validation, subtitle/video rendering. |
| `static/` | Browser UI, drag positioning, language editing, upload controls. |
| `prepare_models.py` | Prepares inference models and verifies FFmpeg subtitle support. |

Project IDs remain stable while storage folders can be renamed. Use `job_folder`
and `checked_job_folder` rather than assuming the folder name equals the ID.
The loader supports older ID-named folders. Workers receive the same jobs root
through their environment. Do not rename folders during active work.

## Isolated local development

Install dependencies in a Python 3.12 environment. To use a separate jobs folder
and port in PowerShell:

```powershell
$env:STUDIO_JOBS_DIR = Join-Path $PWD 'work/test-jobs'
$env:STUDIO_PORT = '9765'
.\.venv\Scripts\python.exe -m uvicorn server:app --host 127.0.0.1 --port 9765
```

Open <http://127.0.0.1:9765>. `launch.py` intentionally uses the normal 9764 port;
use uvicorn directly for an alternate port and keep `STUDIO_PORT` consistent.
Stop your test server with Ctrl+C. Avoid `--reload` when exercising job workers.

`STUDIO_JOBS_DIR` isolates media/projects, **not models**: `engine.py` keeps models
under this checkout's `data/models`. Set the environment before importing `server`,
because importing it restores persisted jobs and marks interrupted work accordingly.

## Checks

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m py_compile engine.py server.py job_worker.py launch.py prepare_models.py
node --check static/app.js
```

The unit suite uses an isolated temporary library and does not download models.
It checks cue validation, fixed export coordinates, folder isolation and naming,
legacy project loading, rename behavior, and the former upload-size boundary.
These checks do not replace real-media and browser verification.

For workflow changes, test a short generated/public video and an audio recording:
upload, language switch, save/reopen, drag/fullscreen, SRT/TXT, export, cancel,
resume, rename, open-folder, and recoverable deletion. Check original dimensions
and selected export language. Never test deletion with someone's actual library.

## Local service assumptions

The app binds to loopback, checks allowed hosts/origins, and requires a generated
per-run token for writes and media endpoints. Keep these protections. It has no
multi-user account system, public-server hardening, remote storage, or background
service installer. Do not expose it publicly without designing those features.

## Release preparation

Update documentation and dependency notices when behavior or packages change.
Verify a fresh installation on a clean Windows x64 machine before claiming
broader compatibility. Keep `data`, virtual environments, model binaries, media,
credentials, and logs out of commits and source archives. Images under
`docs/images` use public/generated demonstration content.
