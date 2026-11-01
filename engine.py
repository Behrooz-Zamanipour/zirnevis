"""Local speech recognition, translation, and Persian subtitle rendering."""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import re
import subprocess
import textwrap
import threading
import time

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
MODELS = DATA / 'models'
MODELS.mkdir(parents=True, exist_ok=True)
os.environ.setdefault('HF_HOME', str(MODELS / 'huggingface'))
os.environ.setdefault('HF_HUB_DISABLE_SYMLINKS_WARNING', '1')
os.environ.setdefault('HF_HUB_DISABLE_XET', '1')
_speech = None
_translator = None
_tokenizer = None
_model_lock = threading.Lock()
SOURCE_CODES = {'en': 'eng_Latn', 'ar': 'arb_Arab', 'tr': 'tur_Latn', 'de': 'deu_Latn',
                'fr': 'fra_Latn', 'es': 'spa_Latn', 'ru': 'rus_Cyrl', 'hi': 'hin_Deva',
                'zh': 'zho_Hans', 'ja': 'jpn_Jpan', 'ko': 'kor_Hang', 'pt': 'por_Latn',
                'it': 'ita_Latn', 'ur': 'urd_Arab', 'fa': 'pes_Arab'}

def ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()

def run(args, cwd=None, timeout=None):
    result = subprocess.run([str(a) for a in args], cwd=cwd, capture_output=True, encoding='utf-8', errors='replace', timeout=timeout,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if result.returncode:
        raise RuntimeError('Video processing failed. ' + result.stderr[-1800:])
    return result

def probe(path):
    import av
    with av.open(str(path)) as container:
        videos = list(container.streams.video)
        if not videos:
            audio = list(container.streams.audio)
            if not audio:
                raise ValueError('This file has no playable audio or video.')
            a = audio[0]
            duration = float(container.duration / av.time_base) if container.duration else float((a.duration or 0) * a.time_base)
            if duration <= 0:
                raise ValueError('The recording duration could not be read.')
            return {'duration': duration, 'width': 0, 'height': 0, 'audio': True,
                    'video_codec': None, 'pixel_format': None, 'audio_codec': a.codec_context.name}
        v = videos[0]
        duration = float(container.duration / av.time_base) if container.duration else float(v.duration * v.time_base) if v.duration else 0
        if not duration or duration <= 0:
            raise ValueError('The video duration could not be read. Try an MP4 copy.')
        audio = list(container.streams.audio)
        return {'duration': duration, 'width': v.width, 'height': v.height, 'audio': bool(audio),
                'video_codec': v.codec_context.name, 'pixel_format': v.codec_context.format.name,
                'audio_codec': audio[0].codec_context.name if audio else None}

def make_preview(video, progress=lambda percent, message: None):
    """Use originals or stream-copy compatible video; encode only unsupported video."""
    metadata = probe(video)
    if not metadata['video_codec']:
        target = video.parent / 'audio-preview.m4a'
        if not target.exists():
            from filelock import FileLock
            with FileLock(str(target) + '.lock'):
                if not target.exists():
                    partial = video.parent / 'audio-preview.partial.m4a'
                    run([ffmpeg(), '-y', '-i', video, '-vn', '-c:a', 'aac', '-b:a', '256k', partial])
                    partial.replace(target)
        return target
    compatible_video = metadata['video_codec'] in {'h264', 'av1', 'libdav1d', 'vp9'} and metadata['pixel_format'] == 'yuv420p'
    compatible_audio = metadata['audio_codec'] in {None, 'aac', 'mp3'}
    if video.suffix.lower() in {'.mp4', '.m4v'} and compatible_video and compatible_audio:
        progress(100, 'Original-quality video is ready for playback.')
        return video
    from filelock import FileLock
    target = video.parent / 'preview-hq.mp4'
    with FileLock(str(target) + '.lock', timeout=7200):
        if not target.exists():
            partial = video.parent / 'preview-hq.partial.mp4'
            command = [ffmpeg(), '-y', '-i', str(video.resolve()), '-map', '0:v:0', '-map', '0:a:0?']
            if compatible_video:
                command += ['-c:v', 'copy']
                progress(0, 'Preparing playback while keeping the original video quality…')
            else:
                command += ['-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2', '-c:v', 'libx264', '-preset', 'medium', '-crf', '16', '-pix_fmt', 'yuv420p']
                progress(0, 'Converting this video for browser playback…')
            command += ['-c:a', 'copy' if compatible_audio else 'aac']
            if not compatible_audio:
                command += ['-b:a', '256k']
            command += ['-movflags', '+faststart', '-progress', 'pipe:1', '-nostats', str(partial.resolve())]
            log_path = video.parent / 'preview.log'
            last_percent = -1
            with log_path.open('w', encoding='utf-8') as log:
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log, text=True, encoding='utf-8', errors='replace',
                                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                for line in process.stdout:
                    if line.startswith('out_time_us='):
                        try:
                            seconds = max(0, int(line.strip().split('=')[1]) / 1_000_000)
                            percent = min(99, round(seconds / metadata['duration'] * 100))
                            if percent != last_percent:
                                elapsed = f'{int(seconds) // 60}:{int(seconds) % 60:02}'
                                total = f"{int(metadata['duration']) // 60}:{int(metadata['duration']) % 60:02}"
                                progress(percent, f'Preparing playback: {percent}% of this step ({elapsed} / {total}).')
                                last_percent = percent
                        except ValueError:
                            pass
                code = process.wait()
            if code:
                partial.unlink(missing_ok=True)
                raise RuntimeError('Video preparation failed. ' + log_path.read_text(encoding='utf-8')[-1400:])
            partial.replace(target)
    progress(100, 'Video playback is ready.')
    return target

def download_file(url, target, progress=lambda text: None):
    import requests
    from filelock import FileLock
    target = Path(target)
    partial = target.with_suffix(target.suffix + '.part')
    with FileLock(str(target) + '.lock', timeout=900):
        if target.exists():
            return
        for attempt in range(4):
            try:
                offset = partial.stat().st_size if partial.exists() else 0
                headers = {'Accept-Encoding': 'identity'}
                if offset:
                    headers['Range'] = f'bytes={offset}-'
                    progress(f'Resuming {target.stem} at {offset // 1048576} MB…')
                with requests.get(url, headers=headers, stream=True, timeout=(30, 60)) as response:
                    response.raise_for_status()
                    resumed = response.status_code == 206 and response.headers.get('Content-Range', '').startswith(f'bytes {offset}-')
                    read = offset if resumed else 0
                    total = int(response.headers.get('Content-Length', 0)) + read
                    last_mb = -1
                    with partial.open('ab' if resumed else 'wb') as output:
                        for chunk in response.iter_content(1024 * 1024):
                            if chunk:
                                output.write(chunk)
                                read += len(chunk)
                                current_mb = read // 1048576
                                if current_mb != last_mb:
                                    progress(f'Downloading {target.stem}: {current_mb} MB' + (f' / {total // 1048576} MB' if total else ''))
                                    last_mb = current_mb
                    if total and read < total:
                        raise requests.ConnectionError('The download ended before all data arrived.')
                partial.replace(target)
                return
            except requests.RequestException:
                if attempt == 3:
                    raise RuntimeError('The model download was interrupted. Check your connection, then Retry. Your download progress has been saved.')
                progress('The download connection was interrupted. Reconnecting…')
                time.sleep(2)

def setup_translation(progress=lambda text: None):
    folder = MODELS / 'nllb-persian'
    folder.mkdir(exist_ok=True)
    # Quantized data-only conversion of Meta's NLLB-200 distilled 600M model.
    # Pin the model revision; no remote Python code is imported or executed.
    revision = '302d78f00e6fdb50a1064059df7c392b735e9d05'
    base = f'https://huggingface.co/JustFrederik/nllb-200-distilled-600M-ct2-int8/resolve/{revision}/'
    for filename in ('config.json', 'shared_vocabulary.txt', 'sentencepiece.bpe.model', 'model.bin'):
        if not (folder / filename).exists():
            progress('Preparing the Persian translation model (one-time download)…')
            download_file(base + filename + '?download=true', folder / filename, progress)
    (folder / 'ready.json').write_text(json.dumps({'source': 'facebook/nllb-200-distilled-600M', 'conversion': base, 'license': 'CC-BY-NC-4.0'}), encoding='utf-8')
    return folder

def get_speech(progress=lambda text: None):
    global _speech
    with _model_lock:
        if _speech is None:
            progress('Loading the speech model from this laptop…')
            from faster_whisper import WhisperModel
            folder = MODELS / 'whisper-small'
            folder.mkdir(exist_ok=True)
            # Plain HTTPS downloads avoid optional hub transfer clients and work reliably on Windows.
            for filename in ('config.json', 'tokenizer.json', 'vocabulary.txt', 'model.bin'):
                if not (folder / filename).exists():
                    download_file('https://huggingface.co/Systran/faster-whisper-small/resolve/main/' + filename + '?download=true', folder / filename, progress)
            _speech = WhisperModel(str(folder), device='cpu', compute_type='int8',
                                   cpu_threads=max(1, min(6, (os.cpu_count() or 4) - 1)))
    return _speech

def get_translation(progress=lambda text: None):
    global _translator, _tokenizer
    with _model_lock:
        if _translator is None:
            folder = setup_translation(progress)
            import ctranslate2
            import sentencepiece
            _translator = ctranslate2.Translator(str(folder), device='cpu', compute_type='int8', intra_threads=4)
            _tokenizer = sentencepiece.SentencePieceProcessor(model_file=str(folder / 'sentencepiece.bpe.model'))
    return _translator, _tokenizer

def translate(text, progress=lambda text: None, source_language='en', target_language='fa'):
    model, tokenizer = get_translation(progress)
    # Whisper segments are short; split any exceptional long segment into sentences.
    pieces = re.split(r'(?<=[.!?])\s+', text.strip())
    results = []
    for piece in pieces:
        tokens = tokenizer.encode(piece, out_type=str)
        if not tokens:
            continue
        source = [SOURCE_CODES.get(source_language, 'eng_Latn')] + tokens + ['</s>']
        target = SOURCE_CODES[target_language]
        output = model.translate_batch([source], target_prefix=[[target]], beam_size=4, max_decoding_length=384)[0].hypotheses[0]
        results.append(tokenizer.decode([token for token in output if token not in {target, '</s>', '<s>', '<pad>'}]))
    return ' '.join(results).strip()

def transcribe(video, language, update):
    metadata = probe(video)
    if not metadata['audio']:
        raise ValueError('This video has no audio track to transcribe.')
    audio = video.parent / 'speech.wav'
    update('transcribing', 27, 'Preparing the audio…')
    run([ffmpeg(), '-y', '-i', video, '-vn', '-ac', '1', '-ar', '16000', audio])
    try:
        model = get_speech(lambda text: update('transcribing', 29, text))
        source_language = None if language == 'auto' else language
        segments, info = model.transcribe(str(audio), language=source_language, task='transcribe', vad_filter=True,
                                         word_timestamps=True, beam_size=5, condition_on_previous_text=False)
        detected = info.language
        translation_language = detected
        if detected != 'fa' and detected not in SOURCE_CODES:
            # For other detected languages, Whisper provides an English bridge.
            segments, _ = model.transcribe(str(audio), language=detected, task='translate', vad_filter=True,
                                          word_timestamps=True, beam_size=5, condition_on_previous_text=False)
            translation_language = 'en'
        cues = []
        for segment in segments:
            words = [w for w in (segment.words or []) if w.word.strip()]
            groups = []
            if words:
                group = []
                for word in words:
                    if group and (word.end - group[0].start > 10 or len(''.join(w.word for w in group)) > 160):
                        groups.append(group)
                        group = []
                    group.append(word)
                    if re.search(r'[.!?؟]["\u201d\u2019]*$', word.word.strip()):
                        groups.append(group)
                        group = []
                if group:
                    groups.append(group)
                parts = [(g[0].start, g[-1].end, ''.join(w.word for w in g).strip()) for g in groups]
            else:
                parts = [(segment.start, segment.end, segment.text.strip())]
            for start, end, text in parts:
                if text and end > start:
                    cues.append({'start': max(0, round(start, 3)), 'end': min(metadata['duration'], round(end, 3)),
                                 'source': text, 'text': text if detected == 'fa' else ''})
            update('transcribing', min(65, 30 + 35 * segment.end / metadata['duration']), 'Recognizing speech and matching it to the audio…')
        if not cues:
            raise ValueError('No speech was detected. Try choosing the spoken language manually or a clearer video.')
        if detected != 'fa':
            get_translation(lambda text: update('translating', 67, text))
            for i, cue in enumerate(cues):
                update('translating', 67 + 31 * i / len(cues), f'Translating subtitle {i + 1} of {len(cues)} into Persian…')
                cue['text'] = translate(cue['source'], source_language=translation_language)
        add_english(cues, detected, update)
        return cues, detected, metadata
    finally:
        audio.unlink(missing_ok=True)

def add_english(cues, detected, update):
    source_language = detected if detected in SOURCE_CODES else 'en'
    for i, cue in enumerate(cues):
        if cue.get('english'):
            continue
        source = cue.get('source') or cue['text']
        update('translating', 98 + i / max(1, len(cues)), f'Preparing English subtitle {i + 1} of {len(cues)}…')
        cue['english'] = source if source_language == 'en' else translate(source, source_language=source_language, target_language='en')

def language_cues(cues, language='fa'):
    return [dict(c, text=c.get('english', '') if language == 'en' else c['text']) for c in cues]

def validate_cues(cues, duration):
    if not isinstance(cues, list) or not 1 <= len(cues) <= 25000:
        raise ValueError('Add at least one subtitle. A video can contain up to 25,000 subtitles.')
    normalized = []
    previous_end = 0
    for i, cue in enumerate(cues):
        start, end = float(cue['start']), float(cue['end'])
        text = str(cue['text']).strip()
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start or end > duration + .1:
            raise ValueError(f'Subtitle {i + 1}: the end must follow the start and stay within the video.')
        if i and start < previous_end - .02:
            raise ValueError(f'Subtitle {i + 1} overlaps the previous subtitle. Adjust its start time.')
        if not text or len(text) > 2000:
            raise ValueError(f'Subtitle {i + 1}: enter between 1 and 2,000 characters.')
        normalized.append({'start': round(start, 3), 'end': round(end, 3), 'text': text,
                           'source': str(cue.get('source', ''))[:2000], 'english': str(cue.get('english', ''))[:2000]})
        previous_end = end
    return normalized

def stamp(seconds, srt=False):
    scale = 1000 if srt else 100
    value = round(seconds * scale)
    h, rest = divmod(value, 3600 * scale)
    m, rest = divmod(rest, 60 * scale)
    s, fraction = divmod(rest, scale)
    return f'{h:02}:{m:02}:{s:02},{fraction:03}' if srt else f'{h}:{m:02}:{s:02}.{fraction:02}'

def srt(cues):
    return '\n\n'.join(f"{i + 1}\n{stamp(c['start'], True)} --> {stamp(c['end'], True)}\n{c['text']}" for i, c in enumerate(cues)) + '\n'

def vtt(cues):
    return 'WEBVTT\n\n' + '\n\n'.join(f"{stamp(c['start'], True).replace(',', '.')} --> {stamp(c['end'], True).replace(',', '.')}\n{c['text']}" for c in cues) + '\n'

def write_ass(cues, target, width, height, font_size=38, position='bottom', anchor=None):
    # Fixed reference canvas gives consistent subtitle proportions across resolutions.
    ref_h = 1080
    ref_w = max(360, round(width / height * ref_h))
    align = 2 if position == 'bottom' else 8
    header = f'''[Script Info]
ScriptType: v4.00+
PlayResX: {ref_w}
PlayResY: {ref_h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Persian,Arial,{font_size},&H00FFFFFF,&H000000FF,&H00151920,&H90000000,0,0,0,0,100,100,0,0,1,2,1,{align},50,50,65,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    lines = []
    for cue in cues:
        # Disallow ASS override sequences from editable subtitle text.
        text = cue['text'].replace('\\', '＼').replace('{', '｛').replace('}', '｝')
        wrapped = []
        for paragraph in text.splitlines():
            wrapped.extend(textwrap.wrap(paragraph, width=max(22, int(ref_w / (font_size * .57)))) or [''])
        text = r'\N'.join(('\u200f' if re.search(r'[\u0600-\u06ff]', line) else '') + line for line in wrapped)
        fixed = ('{\\an2\\pos(' + str(round(ref_w * anchor['x'] / 100)) + ',' + str(round(ref_h * anchor['y'] / 100)) + ')}') if anchor else ''
        lines.append(f"Dialogue: 0,{stamp(cue['start'])},{stamp(cue['end'])},Persian,,0,0,0,,{fixed}{text}")
    target.write_text(header + '\n'.join(lines) + '\n', encoding='utf-8-sig')

def render_video(video, cues, metadata, update, font_size=38, position='bottom', anchor=None):
    directory = video.parent
    write_ass(cues, directory / 'captions.ass', metadata['width'], metadata['height'], font_size, position, anchor)
    (directory / 'export-subtitles.srt').write_text(srt(cues), encoding='utf-8-sig')
    partial = directory / 'persian-video.partial.mp4'
    command = [ffmpeg(), '-y', '-i', str(video.resolve()), '-vf', 'ass=captions.ass', '-map', '0:v:0', '-map', '0:a:0?',
               '-c:v', 'libx264', '-preset', 'medium', '-crf', '16',
               '-c:a', 'copy' if metadata.get('audio_codec') == 'aac' else 'aac', '-b:a', '256k',
               '-movflags', '+faststart', '-progress', 'pipe:1', '-nostats', str(partial.resolve())]
    log_path = directory / 'render.log'
    with log_path.open('w', encoding='utf-8') as log:
        process = subprocess.Popen(command, cwd=directory, stdout=subprocess.PIPE, stderr=log, text=True,
                                   encoding='utf-8', errors='replace', creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        for line in process.stdout:
            if line.startswith('out_time_us='):
                try:
                    seconds = int(line.strip().split('=')[1]) / 1_000_000
                    update('exporting', min(99, seconds / metadata['duration'] * 100), 'Burning Persian subtitles into your video…')
                except ValueError:
                    pass
        code = process.wait()
    if code:
        partial.unlink(missing_ok=True)
        raise RuntimeError('Video export failed. Your subtitle edits are saved. ' + log_path.read_text(encoding='utf-8')[-1000:])
    result = directory / 'persian-video.mp4'
    partial.replace(result)
    return result
