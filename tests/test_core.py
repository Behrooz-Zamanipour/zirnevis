import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LIBRARY = tempfile.TemporaryDirectory(prefix='zirnevis-tests-')
os.environ['STUDIO_JOBS_DIR'] = str(Path(LIBRARY.name) / 'jobs')
import engine
import server
from fastapi import HTTPException

class Request:
    headers = {'content-length': str(3 * 1024**3)}
    def __init__(self, body=None): self.body = body
    async def json(self): return self.body
    async def stream(self):
        # Test the old limit without allocating a multi-GB fixture.
        class BoundaryChunk(bytes):
            def __len__(self): return 3 * 1024**3
        yield BoundaryChunk(b'fixture')

class CoreTests(unittest.TestCase):
    def test_fixed_export_anchor_and_languages(self):
        cues = [dict(start=0, end=1, text='سلام', english='Hello', source='Hello')]
        self.assertEqual(engine.language_cues(cues, 'en')[0]['text'], 'Hello')
        self.assertEqual(engine.validate_cues(cues, 2)[0]['english'], 'Hello')
        target = Path(LIBRARY.name) / 'captions.ass'
        engine.write_ass(cues, target, 1920, 1080, anchor={'x':35,'y':65})
        self.assertIn(r'\an2\pos(672,702)', target.read_text(encoding='utf-8-sig'))

    def test_invalid_timing_and_overlap(self):
        for cues in ([dict(start=float('nan'),end=1,text='x')],
                     [dict(start=0,end=2,text='x'),dict(start=1,end=3,text='y')]):
            with self.assertRaises(ValueError): engine.validate_cues(cues, 5)

    def test_named_folders_and_rename(self):
        first = server.new_job('lesson.mp4','en',folder_name='درس')
        second = server.new_job('lesson.mp4','en',folder_name='درس')
        self.assertNotEqual(first['folder_name'],second['folder_name'])
        folder = server.job_folder(first)
        (folder/'source.mp4').write_bytes(b'example')
        first['state']='review';server.persist(first)
        result = asyncio.run(server.rename_folder(first['id'],Request({'folder_name':'Renamed lesson'})))
        self.assertEqual(result['id'],first['id'])
        self.assertEqual((Path(result['folder_path'])/'source.mp4').read_bytes(),b'example')
        second['state']='review'
        with self.assertRaises(HTTPException):
            asyncio.run(server.rename_folder(second['id'],Request({'folder_name':'Renamed lesson'})))
        first['state']='transcribing'
        with self.assertRaises(HTTPException):
            asyncio.run(server.rename_folder(first['id'],Request({'folder_name':'Busy lesson'})))
        first['state']='review';server.persist(first)

    def test_folder_boundary(self):
        job = server.new_job('sample.mp4','en',folder_name='../outside:folder')
        self.assertEqual(server.checked_job_folder(job['id']).parent,server.JOBS)
        self.assertEqual(server.clean_folder_name('CON'),'_CON')
        bad = dict(job,folder_name='../outside')
        with self.assertRaises(HTTPException): server.job_folder(bad)

    def test_upload_has_no_old_size_limit(self):
        with patch.object(server,'queue_job') as queue:
            result = asyncio.run(server.upload(Request(),name='large.mp4',language='en',folder_name='Large upload'))
        self.assertEqual((Path(result['folder_path'])/'source.mp4').read_bytes(),b'fixture')
        queue.assert_called_once_with(result['id'])
        job = server.jobs[result['id']];job['state']='review';server.persist(job)

    def test_legacy_folder_loading(self):
        legacy_id = 'b'*32
        folder = server.JOBS/legacy_id;folder.mkdir(exist_ok=True)
        (folder/'job.json').write_text(json.dumps(dict(id=legacy_id,name='Legacy',state='review',created=0,cues=[])),encoding='utf-8')
        env = dict(os.environ,STUDIO_WORKER='1')
        code = f"import server; j=server.jobs['{legacy_id}']; assert server.job_folder(j).name == '{legacy_id}'"
        result = subprocess.run([sys.executable,'-c',code],cwd=ROOT,env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

if __name__ == '__main__': unittest.main()
