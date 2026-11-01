from __future__ import annotations
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
import engine

ROOT = Path(__file__).resolve().parent
JOBS = Path(os.environ.get('STUDIO_JOBS_DIR', str(ROOT / 'data' / 'jobs'))).resolve()
JOBS.mkdir(parents=True, exist_ok=True)
PORT = int(os.environ.get('STUDIO_PORT', '9764'))
TOKEN = secrets.token_urlsafe(32)
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
pool = ThreadPoolExecutor(max_workers=1)
maintenance_pool = ThreadPoolExecutor(max_workers=1)
lock = threading.RLock()
jobs = {}
LANGUAGES = {'auto', 'en', 'fa', 'ar', 'tr', 'de', 'fr', 'es', 'ru', 'hi', 'zh', 'ja', 'ko', 'pt', 'it', 'ur'}
ACTIVE = {'uploading', 'queued', 'downloading', 'preparing', 'transcribing', 'translating', 'exporting', 'canceling', 'deleting'}
youtube_inspections = {}
futures = {}
running_processes = {}
cancel_requested = set()
delete_requested = set()
uploading_ids = set()
API_VERSION = 'named-folders-4'

def clean_folder_name(value):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '-', str(value)).strip().rstrip('. ')[:100].rstrip('. ')
    if not name or name in {'.', '..'}:
        name = 'Untitled video'
    if re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', name, re.I):
        name = '_' + name
    return name

def job_folder(job):
    if isinstance(job, str):
        job = jobs[job]
    name = job.get('folder_name', job['id'])
    if not isinstance(name, str) or name != Path(name).name or name in {'.', '..'} or ':' in name or '/' in name or '\\' in name:
        raise HTTPException(400, 'Invalid video folder.')
    folder = JOBS / name
    if folder.is_symlink() or (hasattr(folder, 'is_junction') and folder.is_junction()):
        raise HTTPException(400, 'Linked folders cannot be used by the studio.')
    if folder.resolve().parent != JOBS:
        raise HTTPException(400, 'This folder is outside the studio library.')
    return folder

def create_folder(name):
    base = clean_folder_name(name)
    number = 1
    while True:
        candidate = base if number == 1 else f'{base} ({number})'
        folder = JOBS / candidate
        try:
            folder.mkdir()
            return candidate
        except FileExistsError:
            number += 1

def persist(job):
    path = job_folder(job) / 'job.json'
    temporary = path.with_name(f'job.{uuid.uuid4().hex}.tmp')
    temporary.write_text(json.dumps(job, ensure_ascii=False), encoding='utf-8')
    # Windows can briefly deny replacement while the supervisor reads progress.
    for attempt in range(20):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 19:
                temporary.unlink(missing_ok=True)
                raise
            time.sleep(min(.02 * (attempt + 1), .1))

for path in JOBS.glob('*/job.json'):
    try:
        job = json.loads(path.read_text(encoding='utf-8'))
        job['folder_name'] = path.parent.name
        job_folder(job)
        if job['state'] in ACTIVE and os.environ.get('STUDIO_WORKER') != '1':
            job.update(state='error', message='Processing was interrupted. Click Retry to continue from the saved video.')
            persist(job)
        jobs[job['id']] = job
    except (OSError, ValueError, KeyError, HTTPException):
        logging.exception('Could not restore a previous job')

def snapshot(job):
    result = json.loads(json.dumps(job))
    if result.get('detected_language') == 'en' or result.get('language') == 'en':
        for cue in result.get('cues', []):
            if not cue.get('english'):
                cue['english'] = cue.get('source', '')
    result['folder_path'] = str(job_folder(job))
    if job['id'] in delete_requested:
        result.update(state='deleting', message='Stopping processing and moving this video to the Recycle Bin…')
    return result

def worker_command(job_id, operation):
    return [sys.executable, str(ROOT / 'job_worker.py'), job_id, operation]

def sync_worker(job_id):
    try:
        latest = json.loads((job_folder(job_id) / 'job.json').read_text(encoding='utf-8'))
        if latest.get('id') == job_id:
            jobs[job_id] = latest
    except (OSError, ValueError):
        pass

def terminate_worker(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        # This PID belongs to this job's isolated worker. /T also stops its FFmpeg/Node children.
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=15,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        import signal
        os.killpg(process.pid, signal.SIGTERM)
    if process.poll() is None:
        process.wait(timeout=5)

def finish_cancellation(job_id):
    job = jobs[job_id]
    job.update(state='cancelled', message='Cancelled. Completed files and saved subtitle edits are kept.')
    persist(job)
    cancel_requested.discard(job_id)

def supervise_job(job_id, operation):
    process = None
    try:
        with lock:
            if job_id in cancel_requested or jobs[job_id]['state'] == 'cancelled':
                finish_cancellation(job_id)
                return
            environment = {**os.environ, 'STUDIO_WORKER': '1', 'STUDIO_JOBS_DIR': str(JOBS), 'STUDIO_PARENT_PID': str(os.getpid())}
            log_path = job_folder(job_id) / 'worker.log'
            with log_path.open('a', encoding='utf-8') as log:
                process = subprocess.Popen(worker_command(job_id, operation), cwd=ROOT, env=environment,
                                           stdout=log, stderr=log,
                                           creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == 'nt' else 0,
                                           start_new_session=os.name != 'nt')
            running_processes[job_id] = process
        while process.poll() is None:
            with lock:
                cancelled = job_id in cancel_requested
                if not cancelled:
                    sync_worker(job_id)
            if cancelled:
                terminate_worker(process)
                break
            time.sleep(.15)
        with lock:
            cancelled = job_id in cancel_requested
            sync_worker(job_id)
            if cancelled:
                finish_cancellation(job_id)
            elif jobs[job_id]['state'] in ACTIVE:
                jobs[job_id].update(state='error', message='The processor stopped unexpectedly. Your files are kept; click Retry.')
                persist(jobs[job_id])
    except Exception:
        logging.exception('Job supervisor failed for %s', job_id)
        if process and process.poll() is None:
            try:
                terminate_worker(process)
            except Exception:
                logging.exception('Could not stop job worker')
        with lock:
            jobs[job_id].update(state='error', message='The processor could not finish. Your files are kept; restart the studio and Retry.')
            persist(jobs[job_id])
    finally:
        with lock:
            if running_processes.get(job_id) is process:
                running_processes.pop(job_id, None)

def queue_job(job_id, operation='process'):
    with lock:
        cancel_requested.discard(job_id)
        jobs[job_id]['operation'] = operation
        persist(jobs[job_id])
        futures[job_id] = pool.submit(supervise_job, job_id, operation)

def update(job_id, state, progress, message, **fields):
    with lock:
        job = jobs[job_id]
        job.update(state=state, progress=round(progress), message=message, **fields)
        persist(job)

def get_job(job_id, allow_deleting=False):
    if not re.fullmatch(r'[a-f0-9]{32}', job_id) or job_id not in jobs:
        raise HTTPException(404, 'Video not found.')
    if job_id in delete_requested and not allow_deleting:
        raise HTTPException(409, 'This video is being deleted.')
    return jobs[job_id]

def checked_job_folder(job_id):
    if not re.fullmatch(r'[a-f0-9]{32}', job_id):
        raise HTTPException(400, 'Invalid video folder.')
    folder = job_folder(job_id)
    if folder.is_symlink() or (hasattr(folder, 'is_junction') and folder.is_junction()):
        raise HTTPException(400, 'Linked folders cannot be removed by the studio.')
    resolved = folder.resolve()
    if resolved.parent != JOBS.resolve() :
        raise HTTPException(400, 'This folder is outside the studio video library.')
    return resolved

def reveal_folder(folder):
    folder = Path(folder).resolve()
    if not folder.is_dir():
        raise HTTPException(404, 'This folder no longer exists.')
    if os.name != 'nt':
        raise HTTPException(400, f'Open this path in your file manager: {folder}')
    try:
        # Invoke Explorer directly so this enters the folder, rather than opening
        # its parent and selecting it or relying on a broken folder association.
        explorer = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'explorer.exe'
        subprocess.Popen([str(explorer), str(folder)], creationflags=subprocess.CREATE_NO_WINDOW)
    except OSError:
        raise HTTPException(500, f'Windows could not open the folder. Copy this path into File Explorer: {folder}')
    return {'opened': True, 'folder_path': str(folder)}

def recycle_job_folder(job_id):
    folder = checked_job_folder(job_id)
    if not folder.exists():
        return
    # Require the modern Windows implementation: it forces recycling, with no permanent-delete fallback.
    if os.name == 'nt':
        from send2trash.win.modern import send2trash
    else:
        from send2trash import send2trash
    for attempt in range(20):
        try:
            send2trash(str(folder))
            return
        except OSError:
            if not folder.exists():
                return
            if attempt == 19:
                raise
            time.sleep(.25)

def delete_videos(job_ids):
    for job_id in job_ids:
        try:
            deadline = time.monotonic() + 30
            while True:
                with lock:
                    future = futures.get(job_id)
                    busy = job_id in uploading_ids or (future is not None and not future.done())
                if not busy:
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError('Processing is still stopping. Close any open players and try Delete again.')
                time.sleep(.1)
            recycle_job_folder(job_id)
            with lock:
                jobs.pop(job_id, None)
                futures.pop(job_id, None)
                cancel_requested.discard(job_id)
                delete_requested.discard(job_id)
        except Exception:
            logging.exception('Could not recycle video %s', job_id)
            with lock:
                delete_requested.discard(job_id)
                if job_id in jobs:
                    jobs[job_id].update(state='error', message='Could not delete this video. Close open video players or folders and try Delete again. Files are kept.')
                    persist(jobs[job_id])

def request_deletion(job_ids):
    with lock:
        targets = [job_id for job_id in job_ids if job_id not in delete_requested]
        # Validate every absolute destination before moving any directory.
        for job_id in targets:
            get_job(job_id)
            checked_job_folder(job_id)
        for job_id in targets:
            if jobs[job_id]['state'] in ACTIVE:
                cancel_job(job_id)
            delete_requested.add(job_id)
        maintenance_pool.submit(delete_videos, targets)
        return {'deleting': len(targets)}

def youtube_url(value):
    try:
        parsed = urlparse(value.strip())
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError()
        host = (parsed.hostname or '').lower()
        if host in {'youtube.com', 'www.youtube.com', 'm.youtube.com'}:
            video_id = parse_qs(parsed.query).get('v', [''])[0] if parsed.path == '/watch' else parsed.path.split('/')[2] if parsed.path.startswith(('/shorts/', '/embed/', '/live/')) else ''
        elif host == 'youtu.be':
            video_id = parsed.path.strip('/')
        else:
            raise ValueError()
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
            raise ValueError()
        return 'https://www.youtube.com/watch?v=' + video_id
    except (ValueError, IndexError):
        raise HTTPException(400, 'Paste a valid YouTube video link, such as https://www.youtube.com/watch?v=…')

@app.middleware('http')
async def local_only(request: Request, call_next):
    host = request.headers.get('host', '').split(':')[0]
    if host not in {'127.0.0.1', 'localhost'}:
        return JSONResponse({'detail': 'This website is available only on this laptop.'}, status_code=403)
    if request.method != 'GET':
        if request.headers.get('x-studio-token') != TOKEN:
            return JSONResponse({'detail': 'Reload this page before continuing.'}, status_code=403)
        origin = request.headers.get('origin')
        if origin and origin not in {f'http://127.0.0.1:{PORT}', f'http://localhost:{PORT}'}:
            return JSONResponse({'detail': 'Local requests only.'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Frame-Options'] = 'DENY'
    return response

@app.get('/')
def index():
    content = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8').replace('__STUDIO_TOKEN__', TOKEN)
    return HTMLResponse(content, headers={'Cache-Control': 'no-store'})

@app.get('/health')
def health():
    return {'app': 'persian-subtitle-studio', 'ready': True, 'api_version': API_VERSION}

@app.get('/api/storage')
def storage():
    return {'folder_path': str(JOBS)}

@app.post('/api/open-storage')
def open_storage():
    return reveal_folder(JOBS)

@app.post('/api/jobs/{job_id}/open-folder')
def open_folder(job_id: str):
    get_job(job_id)
    return reveal_folder(checked_job_folder(job_id))

@app.post('/api/jobs/{job_id}/rename-folder')
async def rename_folder(job_id: str, request: Request):
    body = await request.json()
    name = clean_folder_name(body.get('folder_name', ''))
    with lock:
        job = get_job(job_id)
        future = futures.get(job_id)
        if job['state'] in ACTIVE or (future and not future.done()):
            raise HTTPException(409, 'Wait for this video to finish before renaming its folder.')
        old = checked_job_folder(job_id)
        target = JOBS / name
        if target == old:
            return snapshot(job)
        if target.resolve().parent != JOBS or target.is_symlink() or target.exists():
            raise HTTPException(409, 'A folder with this name already exists. Choose another name.')
        previous_name = job.get('folder_name', job_id)
        try:
            old.rename(target)
        except OSError as error:
            raise HTTPException(409, 'The folder could not be renamed. Close files open in another app and try again.') from error
        try:
            job['folder_name'] = name
            persist(job)
        except Exception:
            job['folder_name'] = previous_name
            target.rename(old)
            raise
        return snapshot(job)

@app.post('/api/jobs/clear')
def clear_videos():
    with lock:
        return request_deletion(list(jobs))

@app.post('/api/jobs/{job_id}/delete')
def delete_video(job_id: str):
    return request_deletion([job_id])

@app.post('/api/jobs/{job_id}/cancel')
def cancel_job(job_id: str):
    with lock:
        job = get_job(job_id, allow_deleting=True)
        if job['state'] not in ACTIVE:
            return snapshot(job)
        cancel_requested.add(job_id)
        future = futures.get(job_id)
        if job['state'] == 'uploading':
            # The upload coroutine will see this state and discard its incomplete upload.
            job.update(state='cancelled', message='Upload cancelled. Add the video again to upload it.')
            persist(job)
        elif future and future.cancel():
            finish_cancellation(job_id)
        elif not future and job_id not in running_processes:
            finish_cancellation(job_id)
        else:
            # Do not write over the worker's progress file until its process has stopped.
            job.update(state='canceling', message='Stopping this job…')
        return snapshot(job)

@app.get('/api/jobs')
def list_jobs():
    with lock:
        return [snapshot(job) for job in sorted(jobs.values(), key=lambda j: j['created'], reverse=True)]

def new_job(name, language, url=None, folder_name=None):
    if language not in LANGUAGES:
        raise HTTPException(400, 'Choose a supported spoken language.')
    job_id = uuid.uuid4().hex
    folder_name = create_folder(folder_name or Path(name).stem)
    job = {'id': job_id, 'name': name[:200], 'created': time.time(), 'state': 'queued', 'progress': 0,
           'message': 'Waiting for the local processor…', 'language': language, 'url': url, 'cues': [],
           'font_size': 38, 'position': 'bottom', 'folder_name': folder_name}
    with lock:
        jobs[job_id] = job
        persist(job)
    return job

@app.post('/api/upload')
async def upload(request: Request, name: str = 'video.mp4', language: str = 'auto', folder_name: str = ''):
    suffix = Path(name).suffix.lower()
    if suffix not in {'.mp4', '.mkv', '.mov', '.webm', '.avi', '.m4v', '.mp3', '.wav', '.m4a', '.flac', '.ogg', '.opus', '.aac'}:
        raise HTTPException(400, 'Choose a supported video or audio file.')
    job = new_job(Path(name).name, language, folder_name=folder_name)
    with lock:
        uploading_ids.add(job['id'])
    update(job['id'], 'uploading', 0, 'Uploading the video to the studio folder…')
    path = job_folder(job) / ('source' + suffix)
    total = 0
    try:
        with path.open('wb') as output:
            async for chunk in request.stream():
                if jobs[job['id']]['state'] == 'cancelled':
                    raise HTTPException(409, 'Upload cancelled.')
                total += len(chunk)
                output.write(chunk)
        if total == 0:
            raise HTTPException(400, 'This video file is empty.')
        with lock:
            if jobs[job['id']]['state'] == 'cancelled':
                raise HTTPException(409, 'Upload cancelled.')
            job['source'] = path.name
            job.update(state='queued', message='Waiting for the local processor…')
            persist(job)
        queue_job(job['id'])
        return snapshot(job)
    except BaseException as error:
        path.unlink(missing_ok=True)
        from starlette.requests import ClientDisconnect
        if jobs[job['id']]['state'] == 'cancelled' or isinstance(error, (ClientDisconnect, asyncio.CancelledError)):
            update(job['id'], 'cancelled', 0, 'Upload cancelled. Add the video again to upload it.')
            with lock:
                cancel_requested.discard(job['id'])
        else:
            update(job['id'], 'error', 0, 'The upload did not finish. Add this video again.')
        raise
    finally:
        with lock:
            uploading_ids.discard(job['id'])

@app.post('/api/youtube')
async def youtube(request: Request):
    body = await request.json()
    url = youtube_url(str(body.get('url', '')))
    inspection = youtube_inspections.get(str(body.get('inspection_id', '')))
    if not inspection or inspection['url'] != url or inspection['expires'] < time.time():
        raise HTTPException(400, 'Check available qualities for this link again before downloading.')
    choice = next((c for c in inspection['qualities'] if str(c['height']) == str(body.get('quality', ''))), None)
    if not choice:
        raise HTTPException(400, 'Choose one of the available video qualities.')
    reuse = get_job(str(body['reuse_job_id'])) if body.get('reuse_job_id') else None
    if reuse and (reuse.get('url') != url or reuse['state'] in ACTIVE or not reuse.get('cues')):
        raise HTTPException(400, 'This project cannot reuse its subtitles yet. Wait for it to finish first.')
    job = new_job(inspection['title'], str(body.get('language', 'auto')), url, body.get('folder_name'))
    with lock:
        job.update(requested_quality=choice['height'], quality_label=choice['label'], format_id=choice['format_id'], separate_audio=choice['separate_audio'])
        if reuse:
            job.update(reuse_cues=snapshot(reuse)['cues'], detected_language=reuse.get('detected_language'),
                       font_size=reuse.get('font_size', 38), position=reuse.get('position', 'bottom'),
                       anchor=reuse.get('anchor', {'x': 50, 'y': 90}), subtitle_language=reuse.get('subtitle_language', 'fa'), reused_from=reuse['id'])
        persist(job)
    queue_job(job['id'])
    return snapshot(job)

def youtube_options():
    portable_node = ROOT / '.tools' / 'node.exe'
    node = str(portable_node) if portable_node.exists() else shutil.which('node')
    if not node:
        raise RuntimeError('YouTube links need Node.js 22 or newer. Install it from nodejs.org or upload a video file instead.')
    return {'ffmpeg_location': engine.ffmpeg(), 'noplaylist': True, 'quiet': True, 'no_warnings': True,
            'noprogress': True, 'socket_timeout': 30, 'retries': 2,
            'js_runtimes': {'node': {'path': node}}, 'remote_components': set()}

def video_qualities(info):
    by_height = {}
    for entry in info.get('formats', []):
        if entry.get('vcodec') in (None, 'none') or entry.get('has_drm') or not entry.get('url'):
            continue
        width, height = entry.get('width'), entry.get('height')
        format_id = str(entry.get('format_id', ''))
        if not width or not height or not re.fullmatch(r'[A-Za-z0-9_.-]+', format_id):
            continue
        # Portrait video still uses its short edge for labels such as 1080p.
        quality = int(min(width, height))
        fps = round(entry.get('fps') or 0)
        candidate = {'height': quality, 'width': int(width), 'frame_height': int(height),
                     'fps': fps, 'format_id': format_id, 'separate_audio': entry.get('acodec') in (None, 'none'),
                     'label': f'{quality}p' + (f' · {fps} fps' if fps > 30 else '')}
        # yt-dlp returns formats in increasing preference order. Prefer higher FPS,
        # then the highest-ranked format at the same resolution and frame rate.
        if quality not in by_height or fps >= by_height[quality]['fps']:
            by_height[quality] = candidate
    return sorted(by_height.values(), key=lambda c: c['height'], reverse=True)

def inspect_youtube(url):
    import yt_dlp
    with yt_dlp.YoutubeDL(youtube_options()) as downloader:
        info = downloader.extract_info(url, download=False)
    if not info or info.get('is_live') or info.get('live_status') == 'is_live':
        raise ValueError('Choose a finished video rather than a live stream.')
    qualities = video_qualities(info)
    if not qualities:
        raise ValueError('No downloadable video qualities were available for this link.')
    if (info.get('duration') or 0) > 28800:
        raise ValueError('Choose a video shorter than eight hours.')
    inspection_id = secrets.token_urlsafe(20)
    with lock:
        for key in list(youtube_inspections):
            if youtube_inspections[key]['expires'] < time.time():
                del youtube_inspections[key]
        youtube_inspections[inspection_id] = {'url': url, 'title': (info.get('title') or 'YouTube video')[:200],
                                              'qualities': qualities, 'expires': time.time() + 1200}
    return {'inspection_id': inspection_id, 'url': url, 'title': info.get('title') or 'YouTube video',
            'qualities': [{k: v for k, v in choice.items() if k != 'format_id' and k != 'separate_audio'} for choice in qualities]}

@app.post('/api/youtube/qualities')
async def inspect_link(request: Request):
    body = await request.json()
    url = youtube_url(str(body.get('url', '')))
    try:
        return await asyncio.to_thread(inspect_youtube, url)
    except Exception as error:
        raise HTTPException(400, 'Could not check this video. ' + str(error)[-500:])

def download_youtube(job):
    import yt_dlp
    directory = job_folder(job)
    update(job['id'], 'downloading', 2, 'Fetching the YouTube video…')
    last_update = [0]
    def hook(event):
        if time.time() - last_update[0] < 1:
            return
        last_update[0] = time.time()
        total = event.get('total_bytes') or event.get('total_bytes_estimate') or 0
        fraction = event.get('downloaded_bytes', 0) / total if total else 0
        update(job['id'], 'downloading', 2 + min(6, fraction * 6), 'Downloading the YouTube video…')
    format_selector = 'bv*+ba/b'
    if job.get('format_id'):
        # Download the selected video format exactly; never silently fall back to a lower resolution.
        format_selector = job['format_id'] + '+bestaudio' if job.get('separate_audio', True) else job['format_id']
    options = {**youtube_options(), 'format': format_selector,
               'outtmpl': str(directory / 'source.%(ext)s'), 'merge_output_format': 'mkv',
               'ffmpeg_location': engine.ffmpeg(), 'noplaylist': True, 'quiet': True, 'no_warnings': True, 'noprogress': True,
               'socket_timeout': 30, 'retries': 2, 'progress_hooks': [hook],
               'match_filter': lambda info, *, incomplete: 'Choose a video shorter than eight hours.' if (info.get('duration') or 0) > 28800 else None}
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(job['url'], download=True)
    candidates = [p for p in directory.glob('source.*') if p.suffix in {'.mp4', '.webm', '.mkv', '.mov'}]
    if not candidates:
        raise RuntimeError('The video could not be downloaded. Download it yourself and use Upload videos instead.')
    source = max(candidates, key=lambda p: p.stat().st_size)
    actual = engine.probe(source)
    if job.get('requested_quality') and min(actual['width'], actual['height']) != job['requested_quality']:
        raise ValueError('YouTube did not deliver the selected resolution. Check available qualities and try again.')
    with lock:
        job['source'] = source.name
        job['name'] = (info.get('title') or 'YouTube video')[:200]
        job['downloaded_resolution'] = f"{actual['width']} × {actual['height']}"
        persist(job)
    return source

def process_video(job_id):
    job = jobs[job_id]
    try:
        source = job_folder(job_id) / job['source'] if job.get('source') else download_youtube(job)
        metadata = engine.probe(source)
        if metadata['duration'] > 28800:
            raise ValueError('Choose a video shorter than eight hours.')
        with lock:
            job['metadata'] = metadata
            persist(job)
        update(job_id, 'preparing', 9, 'Preparing full-resolution video playback…')
        engine.make_preview(source, lambda percent, message: update(job_id, 'preparing', 9 + .16 * percent, message))
        if job.get('reused_from'):
            cues = engine.validate_cues(job.get('reuse_cues', job['cues']), metadata['duration'])
            detected = job.get('detected_language')
        else:
            cues, detected, metadata = engine.transcribe(source, job['language'], lambda *args: update(job_id, *args))
        # Normalize tiny word-boundary rounding overlaps before the editable review.
        for i in range(1, len(cues)):
            cues[i]['start'] = max(cues[i]['start'], cues[i - 1]['end'])
        cues = [c for c in cues if c['end'] > c['start']]
        with lock:
            job.update(cues=cues, detected_language=detected, metadata=metadata, media_kind='video' if metadata['video_codec'] else 'audio')
            persist(job)
        (job_folder(job_id) / 'persian.srt').write_text(engine.srt(cues), encoding='utf-8-sig')
        update(job_id, 'review', 100, 'Your Persian subtitles are ready to review.')
    except Exception as error:
        logging.exception('Processing failed for %s', job_id)
        message = str(error)
        if job.get('url') and not job.get('source'):
            message = 'YouTube could not provide this video. It may require sign-in, be unavailable, or be blocked by your connection. Upload a downloaded video instead.\n' + message[-450:]
        update(job_id, 'error', 0, message[:1800])

async def read_edits(request, job):
    body = await request.json()
    try:
        cues = engine.validate_cues(body['cues'], job['metadata']['duration'])
        font_size = int(body.get('font_size', 38))
        position = str(body.get('position', 'bottom'))
        if not 24 <= font_size <= 72 or position not in {'bottom', 'top'}:
            raise ValueError('Choose a font size from 24 to 72 and a valid subtitle position.')
        anchor = body.get('anchor', job.get('anchor', {'x': 50, 'y': 90 if position == 'bottom' else 15}))
        anchor = {k: float(anchor[k]) for k in ('x', 'y')}
        if not all(__import__('math').isfinite(v) and 2 <= v <= 98 for v in anchor.values()):
            raise ValueError('Place subtitles within the picture.')
        subtitle_language = body.get('subtitle_language', 'fa')
        if subtitle_language not in {'fa', 'en'}:
            raise ValueError('Choose Persian or English.')
        return cues, font_size, position, anchor, subtitle_language
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(400, str(error))

@app.post('/api/jobs/{job_id}/save')
async def save(job_id: str, request: Request):
    job = get_job(job_id)
    if job['state'] in ACTIVE:
        raise HTTPException(409, 'Wait for this video to finish processing.')
    cues, font_size, position, anchor, subtitle_language = await read_edits(request, job)
    with lock:
        changed = cues != job['cues'] or font_size != job['font_size'] or position != job['position'] or anchor != job.get('anchor') or subtitle_language != job.get('subtitle_language', 'fa')
        if changed and job['state'] == 'complete':
            job['export_stale'] = True
        job.update(cues=cues, font_size=font_size, position=position, anchor=anchor, subtitle_language=subtitle_language)
        persist(job)
    (job_folder(job_id) / 'persian.srt').write_text(engine.srt(cues), encoding='utf-8-sig')
    return {'saved': True}

@app.post('/api/jobs/{job_id}/export')
async def export(job_id: str, request: Request):
    job = get_job(job_id)
    if job['state'] in ACTIVE:
        raise HTTPException(409, 'This video is already processing.')
    cues, font_size, position, anchor, subtitle_language = await read_edits(request, job)
    with lock:
        if job['state'] in ACTIVE:
            raise HTTPException(409, 'This video is already processing.')
        job.update(cues=cues, font_size=font_size, position=position, anchor=anchor, subtitle_language=subtitle_language, operation='export', state='queued', progress=0, message='Waiting to export your video…')
        persist(job)
    queue_job(job_id, 'export')
    return {'queued': True}

def export_video(job_id):
    job = jobs[job_id]
    try:
        update(job_id, 'exporting', 0, 'Starting video export…')
        source = job_folder(job_id) / job['source']
        if not job['metadata'].get('video_codec'):
            raise ValueError('Audio projects use the text and subtitle downloads; there is no video to export.')
        cues = engine.language_cues(job['cues'], job.get('subtitle_language', 'fa'))
        if any(not c['text'].strip() for c in cues):
            raise ValueError('Generate or enter the English subtitles before exporting.')
        engine.render_video(source, cues, engine.probe(source),
                            lambda *args: update(job_id, *args), job['font_size'], job['position'], job.get('anchor'))
        update(job_id, 'complete', 100, 'Your video with permanent subtitles is ready.', export_stale=False,
               exported_language=job.get('subtitle_language', 'fa'))
    except Exception as error:
        logging.exception('Export failed')
        update(job_id, 'error', 0, str(error)[:1800])

@app.post('/api/jobs/{job_id}/retry')
def retry(job_id: str):
    job = get_job(job_id)
    if job['state'] not in {'error', 'cancelled'}:
        raise HTTPException(409, 'Only failed or cancelled videos can be resumed.')
    if not job.get('source') and not job.get('url'):
        raise HTTPException(400, 'Add this video again to finish uploading it.')
    update(job_id, 'queued', 0, 'Waiting for the local processor…')
    queue_job(job_id, job['operation'] if job.get('operation') in {'export', 'english'} and job.get('cues') else 'process')
    return {'queued': True}


@app.post('/api/jobs/{job_id}/english')
def english(job_id: str):
    job = get_job(job_id)
    if job['state'] in ACTIVE or not job.get('cues'):
        raise HTTPException(409, 'Wait for processing to finish first.')
    update(job_id, 'queued', 0, 'Waiting to prepare English subtitles…')
    queue_job(job_id, 'english')
    return {'queued': True}

def generate_english(job_id):
    job = jobs[job_id]
    try:
        engine.add_english(job['cues'], job.get('detected_language', 'en'), lambda *args: update(job_id, *args))
        with lock:
            persist(job)
        update(job_id, 'review', 100, 'Persian and English subtitles are ready.')
    except Exception as error:
        update(job_id, 'error', 0, str(error)[:1800])

@app.get('/api/jobs/{job_id}/{kind}')
def asset(job_id: str, kind: str, token: str = '', language: str = 'fa'):
    if not secrets.compare_digest(token, TOKEN):
        raise HTTPException(403, 'Reload the page and try again.')
    job = get_job(job_id)
    directory = job_folder(job_id)
    if language not in {'en', 'fa'}:
        raise HTTPException(400, 'Choose Persian or English.')
    if kind in {'subtitles', 'transcript'}:
        cues = engine.language_cues(snapshot(job)['cues'], language)
        if any(not c['text'].strip() for c in cues):
            raise HTTPException(409, 'Generate the English subtitles first.')
        content = engine.srt(cues) if kind == 'subtitles' else '\n\n'.join(c['text'] for c in cues)
        suffix = 'srt' if kind == 'subtitles' else 'txt'
        filename = f'subtitles-{language}.{suffix}'
        return Response(content.encode('utf-8-sig'), media_type='text/plain; charset=utf-8', headers={'Content-Disposition': f'attachment; filename="{filename}"'})
    if kind == 'captions.vtt':
        return Response(engine.vtt(job['cues']), media_type='text/vtt; charset=utf-8')
    if kind == 'preview':
        if not job.get('source'):
            raise HTTPException(404, 'This video is not ready yet.')
        # Existing projects also receive the quality upgrade without re-transcribing.
        return FileResponse(engine.make_preview(directory / job['source']), media_type='audio/mp4' if not job['metadata'].get('video_codec') else 'video/mp4')
    names = {'preview': 'preview.mp4', 'video': 'persian-video.mp4', 'subtitles': 'persian.srt'}
    if kind not in names or not (directory / names[kind]).exists():
        raise HTTPException(404, 'This file is not ready yet.')
    exported_name = 'english' if job.get('exported_language') == 'en' else 'persian'
    filename = None if kind == 'preview' else f"{Path(job['name']).stem[:100]}-{exported_name}." + ('srt' if kind == 'subtitles' else 'mp4')
    return FileResponse(directory / names[kind], filename=filename,
                        media_type='text/plain; charset=utf-8' if kind == 'subtitles' else 'video/mp4')

app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=PORT, log_level='info')
