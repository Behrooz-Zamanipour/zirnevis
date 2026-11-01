from engine import get_speech, get_translation, translate, ffmpeg, run
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

if __name__ == '__main__':
    print('Preparing Persian Subtitle Studio. Downloads are saved on this laptop.', flush=True)
    print('Video engine:', ffmpeg(), flush=True)
    filters = run([ffmpeg(), '-hide_banner', '-filters']).stdout
    if ' ass ' not in filters:
        raise RuntimeError('This video engine does not support subtitles.')
    get_translation(lambda message: print(message, flush=True))
    print('Persian test:', translate('Hello. Welcome to our video.').encode('unicode_escape').decode(), flush=True)
    get_speech(lambda message: print(message, flush=True))
    print('READY: speech model, Persian translation, and subtitle rendering are available.', flush=True)
