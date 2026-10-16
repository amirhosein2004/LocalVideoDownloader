import functools
import http.server
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path


class Integration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.base=Path(cls.temp.name)
        with socket.socket() as s:
            s.bind(('127.0.0.1',0))
            port=s.getsockname()[1]
        cls.url=f'http://127.0.0.1:{port}'
        env=dict(os.environ, DOWNLOAD_ROOT=str(cls.base/'downloads'), DATA_ROOT=str(cls.base/'data'), HOST_DOWNLOAD_ROOT='C:/Users/Test/Downloads', ALLOW_PRIVATE_SOURCES='1')
        cls.server=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(port)],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                cls.request('/api/health')
                break
            except (OSError, urllib.error.URLError):
                if cls.server.poll() is not None:
                    raise RuntimeError('Test server exited')
                time.sleep(0.1)
        else:
            raise RuntimeError('Test server did not start')
        cls.media=cls.base/'media'
        cls.media.mkdir()
        # Real HLS AES-128 fixture; key is served like the browser playback key.
        (cls.media/'key.bin').write_bytes(bytes(range(16)))
        (cls.media/'keyinfo').write_text('key.bin\n'+str(cls.media/'key.bin')+'\n')
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=854x480:rate=25','-f','lavfi','-i','sine=frequency=440','-t','6','-c:v','libx264','-preset','ultrafast','-g','25','-c:a','aac','-f','hls','-hls_time','1','-hls_key_info_file',str(cls.media/'keyinfo'),'-hls_list_size','0',str(cls.media/'test.m3u8')],check=True)
        class QuietHandler(http.server.SimpleHTTPRequestHandler):
            def log_message(self,*args):
                pass
            def copyfile(self, source, output):
                if not self.path.endswith('.ts'):
                    return super().copyfile(source,output)
                while block := source.read(32768):
                    output.write(block)
                    output.flush()
                    time.sleep(0.05)
        cls.http=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(QuietHandler,directory=str(cls.media)))
        threading.Thread(target=cls.http.serve_forever,daemon=True).start()
        cls.source=f'http://127.0.0.1:{cls.http.server_port}/test.m3u8'

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.server.terminate()
        cls.server.wait(timeout=10)
        cls.temp.cleanup()

    @classmethod
    def request(cls,path,body=None,headers=None):
        req=urllib.request.Request(cls.url+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json',**(headers or {})})
        return urllib.request.urlopen(req,timeout=70)

    @classmethod
    def api(cls,path,body=None,headers=None):
        with cls.request(path,body,headers) as response:
            return json.load(response)

    def test_cross_origin_host_and_content_type(self):
        cases=[({'Origin':'https://evil.example'},403),({'Origin':'null'},403),({'Sec-Fetch-Site':'cross-site'},403),({'Content-Type':'text/plain'},415),({'Host':'evil.example'},400)]
        for headers,status in cases:
            with self.subTest(headers=headers), self.assertRaises(urllib.error.HTTPError) as error:
                self.api('/api/inspect',{'url':self.source},headers)
            self.assertEqual(error.exception.code,status)
        with self.request('/api/health') as response:
            self.assertIn("frame-ancestors 'none'",response.headers['Content-Security-Policy'])
            self.assertEqual(response.headers['X-Content-Type-Options'],'nosniff')

    def wait_job(self,job_id,require_progress=False):
        intermediate=False
        for _ in range(600):
            job=next(j for j in self.api('/api/jobs') if j['id']==job_id)
            if job['status']=='running' and 1 < job['progress'] < 100:
                intermediate=True
            if job['status'] in ('completed','failed'):
                if require_progress:
                    self.assertTrue(intermediate,'No progress was reported during encrypted HLS download')
                return job
            time.sleep(0.05)
        self.fail('Download timeout')

    def test_encrypted_hls_mp4_and_absolute_destination(self):
        info=self.api('/api/inspect',{'url':self.source})
        self.assertIn('heights',info)
        job=self.api('/api/jobs',{'url':self.source,'height':480,'folder':'C:/Users/Test/Downloads/My Videos'})
        job=self.wait_job(job['id'],require_progress=True)
        self.assertEqual(job['status'],'completed',job.get('error'))
        self.assertTrue(Path(job['file']).is_relative_to(self.base/'downloads'/'My Videos'))
        info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',job['file']]))
        self.assertEqual(next(s for s in info['streams'] if s['codec_type']=='video')['height'],480)
        self.assertTrue(any(s['codec_type']=='audio' for s in info['streams']))
        with self.request(f'/api/jobs/{job["id"]}/file') as response:
            self.assertEqual(response.status,200)
            self.assertEqual(len(response.read(1024)),1024)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.api(f'/api/jobs/{job["id"]}/retry',{})
        self.assertEqual(error.exception.code,409)

    def test_cancel_and_retry(self):
        job=self.api('/api/jobs',{'url':self.source,'folder':'Retry'})
        self.assertEqual(self.api(f'/api/jobs/{job["id"]}/cancel',{})['status'],'cancelled')
        for _ in range(30):
            try:
                self.api(f'/api/jobs/{job["id"]}/retry',{})
                break
            except urllib.error.HTTPError as error:
                self.assertEqual(error.code,409)
                time.sleep(0.1)
        self.assertEqual(self.wait_job(job['id'])['status'],'completed')

    def test_bad_destination_and_unknown_job(self):
        for folder in ['../escape','D:/Unmapped','C:/Users/Test/Downloads/../../escape']:
            with self.subTest(folder=folder), self.assertRaises(urllib.error.HTTPError) as error:
                self.api('/api/jobs',{'url':self.source,'folder':folder})
            self.assertEqual(error.exception.code,400)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.api('/api/jobs/missing/file')
        self.assertEqual(error.exception.code,404)

    def test_queue_limit(self):
        with sqlite3.connect(self.base/'data'/'jobs.sqlite3') as con:
            con.executemany("INSERT INTO jobs(id,status) VALUES (?,'queued')",[(f'limit-{n}',) for n in range(1000)])
        try:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.api('/api/jobs',{'url':self.source})
            self.assertEqual(error.exception.code,429)
        finally:
            with sqlite3.connect(self.base/'data'/'jobs.sqlite3') as con:
                con.execute("DELETE FROM jobs WHERE id LIKE 'limit-%'")

    def test_large_body_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.api('/api/inspect',{'url':'https://example.com/'+('a'*20000)})
        self.assertEqual(error.exception.code,413)

    def test_file_endpoint_rejects_external_file(self):
        secret=self.base/'outside.txt'
        secret.write_text('not a download')
        with sqlite3.connect(self.base/'data'/'jobs.sqlite3') as con:
            con.execute("INSERT INTO jobs(id,status,file) VALUES ('external','completed',?)",(str(secret),))
        try:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.api('/api/jobs/external/file')
            self.assertEqual(error.exception.code,404)
        finally:
            with sqlite3.connect(self.base/'data'/'jobs.sqlite3') as con:
                con.execute("DELETE FROM jobs WHERE id='external'")

    def test_batch_partial_success_duplicates_and_real_downloads(self):
        result=self.api('/api/jobs/batch',{'urls':[self.source,'not a url',self.source,'',self.source+'?second=1'],'height':480,'folder':'Batch Videos'})
        self.assertEqual([job['line'] for job in result['accepted']],[1,5])
        self.assertEqual([job['line'] for job in result['rejected']],[2])
        self.assertEqual(result['duplicates'],1)
        self.assertNotEqual(result['accepted'][0]['id'],result['accepted'][1]['id'])
        for accepted in result['accepted']:
            job=self.wait_job(accepted['id'])
            self.assertEqual(job['status'],'completed',job.get('error'))
            self.assertEqual(job['height'],480)
            self.assertTrue(Path(job['file']).is_relative_to(self.base/'downloads'/'Batch Videos'))

    def test_batch_limit_and_shared_path_validation(self):
        before=len(self.api('/api/jobs'))
        for body,status in [({'urls':[self.source]*501},422),({'urls':[]},422),({'urls':[self.source],'folder':'../outside'},400)]:
            with self.subTest(body=body), self.assertRaises(urllib.error.HTTPError) as error:
                self.api('/api/jobs/batch',body)
            self.assertEqual(error.exception.code,status)
        self.assertEqual(len(self.api('/api/jobs')),before)

    def test_large_batch_request_and_body_limit(self):
        # Exercise all 500 lines and the former 16 KiB boundary without downloads.
        urls = ['invalid-' + str(n) + '-' + 'x'*80 for n in range(500)]
        result = self.api('/api/jobs/batch', {'urls': urls})
        self.assertEqual(len(result['rejected']), 500)
        self.assertEqual(result['accepted'], [])
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.api('/api/jobs/batch', {'urls': ['x' * (1024*1024)]})
        self.assertEqual(error.exception.code, 413)


if __name__=='__main__':
    unittest.main()
