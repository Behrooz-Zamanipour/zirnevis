import json
from pathlib import Path
import threading
import time
import urllib.request
import webbrowser

URL = 'http://127.0.0.1:9764'

def running():
    try:
        with urllib.request.urlopen(URL + '/health', timeout=1) as response:
            return json.load(response).get('app') == 'persian-subtitle-studio'
    except Exception:
        return False

if __name__ == '__main__':
    if running():
        webbrowser.open(URL)
    else:
        def open_when_ready():
            for _ in range(60):
                if running():
                    webbrowser.open(URL)
                    return
                time.sleep(.5)
        threading.Thread(target=open_when_ready, daemon=True).start()
        print('\nPersian Subtitle Studio\nKeep this window open while using the website.\nClose it to stop the studio.\n\nOpen: ' + URL + '\n', flush=True)
        import uvicorn
        from server import app
        uvicorn.run(app, host='127.0.0.1', port=9764, log_level='warning')
