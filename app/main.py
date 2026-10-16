import asyncio
import json
import os
import queue
import re
import signal
import socket
import ipaddress
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

ROOT = Path(os.getenv('DOWNLOAD_ROOT', './downloads')).resolve()
DATA = Path(os.getenv('DATA_ROOT', './data')).resolve()
HOST_ROOT = os.getenv('HOST_DOWNLOAD_ROOT', str(ROOT)).replace('\\', '/').rstrip('/')
STATIC = Path(__file__).parent / 'static'
ROOT.mkdir(parents=True, exist_ok=True)
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / 'jobs.sqlite3'
MAX_BATCH_LINKS = 500
MAX_ACTIVE_JOBS = 1000
MAX_BATCH_BYTES = 1024 * 1024
HISTORY_LIMIT = 1000
pending = queue.Queue(maxsize=MAX_ACTIVE_JOBS)
processes = {}
lock = threading.RLock()
stopping = threading.Event()
inspect_slots = asyncio.Semaphore(2)
snapshot_lock = threading.Lock()
revision = 0
snapshot_revision = -1
snapshot_rows = []

def changed():
    global revision
    with snapshot_lock:
        revision += 1

@contextmanager
def db():
    con = sqlite3.connect(DB, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA synchronous=NORMAL')
    try:
        with con:
            yield con
    finally:
        con.close()

def stop_process(proc):
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

def update(job_id, **fields):
    with db() as con:
        con.execute('UPDATE jobs SET ' + ','.join(k+'=?' for k in fields) + ' WHERE id=?', [*fields.values(), job_id])
    changed()

def get_job(job_id):
    with db() as con:
        row = con.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    if row is None:
        raise HTTPException(404, 'دانلود پیدا نشد')
    return dict(row)

def validate_url(url):
    try:
        parsed = urlparse(url)
        port = parsed.port
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or any(ord(c) < 32 for c in url):
            raise ValueError()
    except ValueError:
        raise HTTPException(400, 'یک لینک معتبر http یا https وارد کنید')
    if os.getenv('ALLOW_PRIVATE_SOURCES') != '1':
        try:
            addresses = socket.getaddrinfo(parsed.hostname, port or (443 if parsed.scheme == 'https' else 80), type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
                raise HTTPException(400, 'آدرس شبکه خصوصی یا محلی مجاز نیست')
        except socket.gaierror:
            raise HTTPException(400, 'نام میزبان لینک پیدا نشد')
    return url

def destination(value):
    if any(ord(c) < 32 for c in value):
        raise HTTPException(400, 'مسیر شامل نویسه نامعتبر است')
    value = value.strip().replace('\\', '/').rstrip('/')
    # Accept the displayed host path only when it is inside the mounted folder.
    windows_path = bool(re.match(r'^[a-zA-Z]:', HOST_ROOT)) or HOST_ROOT.startswith('//')
    comparable = value.casefold() if windows_path else value
    root_comparable = HOST_ROOT.casefold() if windows_path else HOST_ROOT
    if comparable == root_comparable:
        value = ''
    elif comparable.startswith(root_comparable + '/'):
        value = value[len(HOST_ROOT)+1:]
    if value.startswith('/') or re.match(r'^[a-zA-Z]:', value):
        raise HTTPException(400, 'این مسیر متصل نیست؛ پوشه اصلی را با اسکریپت راه‌اندازی تغییر دهید یا زیرپوشه وارد کنید')
    target = (ROOT / value).resolve()
    if not target.is_relative_to(ROOT):
        raise HTTPException(400, 'مسیر باید داخل پوشه ذخیره باشد')
    return target

def command():
    return [sys.executable, '-m', 'yt_dlp', '--ignore-config', '--no-cache-dir', '--no-playlist', '--no-warnings', '--socket-timeout', '20']

def output_template(job):
    if job.get('batch_index'):
        return f'{job["batch_index"]:03d}-%(title).120B-{job["id"][:8]}.%(ext)s'
    return job['id'][:8] + '-%(title).120B.%(ext)s'

def worker():
    while not stopping.is_set():
        try:
            job_id = pending.get(timeout=0.5)
        except queue.Empty:
            continue
        try:
            with lock:
                job = get_job(job_id)
                if job['status'] != 'queued':
                    continue
                target = destination(job['folder'])
                target.mkdir(parents=True, exist_ok=True)
                update(job_id, status='running', phase='preparing', error='')
                fmt = 'bestvideo+bestaudio/best' if job['height'] == 0 else f'bestvideo[height<=?{job["height"]}]+bestaudio/best[height<=?{job["height"]}]'
                template = str(target / output_template(job))
                args = command() + ['--newline', '--progress', '--progress-template', 'download:PROGRESS:%(progress)j', '--print', 'before_dl:TITLE:%(title)j', '--print', 'after_move:FILE:%(filepath)s', '--no-simulate', '--continue', '--retries', '5', '--fragment-retries', '5', '--abort-on-unavailable-fragments', '--concurrent-fragments', '4', '--merge-output-format', 'mp4', '--remux-video', 'mp4', '-f', fmt, '-o', template, '--', job['url']]
                proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', start_new_session=True)
                processes[job_id] = proc
            errors = deque(maxlen=3)
            last_progress = 0.0
            for line in proc.stdout:
                line = line.strip()
                if line.startswith('PROGRESS:'):
                    now = time.monotonic()
                    if now - last_progress < 0.5:
                        continue
                    last_progress = now
                    try:
                        info = json.loads(line[9:])
                        total = info.get('total_bytes') or info.get('total_bytes_estimate') or 0
                        downloaded = info.get('downloaded_bytes') or 0
                        fragments = info.get('fragment_count') or 0
                        percent = downloaded / total * 100 if total else (info.get('fragment_index') or 0) / fragments * 100 if fragments else 0
                        phase = 'processing' if info.get('status') == 'finished' else 'downloading'
                        update(job_id, progress=min(99, percent), phase=phase, speed=info.get('speed') or 0, eta=info.get('eta') or 0)
                    except (ValueError, TypeError):
                        pass
                elif line.startswith('TITLE:'):
                    try:
                        update(job_id, title=str(json.loads(line[6:]))[:300], phase='downloading')
                    except ValueError:
                        pass
                elif line.startswith('FILE:'):
                    update(job_id, file=line[5:])
                elif 'ERROR:' in line:
                    errors.append(line[-1200:])
            result = proc.wait()
            with lock:
                processes.pop(job_id, None)
                if get_job(job_id)['status'] != 'cancelled':
                    finished = get_job(job_id)
                    output = Path(finished['file']).resolve() if finished['file'] else None
                    if result == 0 and output and output.is_relative_to(ROOT) and output.is_file():
                        update(job_id, status='completed', progress=100, speed=0, eta=0)
                    else:
                        update(job_id, status='failed', error='\n'.join(errors) or 'دانلود کامل نشد؛ لینک یا دسترسی را بررسی کنید', speed=0)
        except Exception as exc:
            update(job_id, status='failed', error=str(exc)[-1500:])
        finally:
            with lock:
                proc = processes.pop(job_id, None)
                if proc:
                    stop_process(proc)
            pending.task_done()

@asynccontextmanager
async def lifespan(app):
    with db() as con:
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, url TEXT, title TEXT, height INTEGER, folder TEXT, status TEXT, progress REAL DEFAULT 0, speed REAL DEFAULT 0, eta REAL DEFAULT 0, error TEXT DEFAULT "", file TEXT DEFAULT "", created TEXT DEFAULT CURRENT_TIMESTAMP)')
        if 'phase' not in {row[1] for row in con.execute('PRAGMA table_info(jobs)')}:
            con.execute("ALTER TABLE jobs ADD COLUMN phase TEXT DEFAULT ''")
        if 'batch_index' not in {row[1] for row in con.execute('PRAGMA table_info(jobs)')}:
            con.execute('ALTER TABLE jobs ADD COLUMN batch_index INTEGER DEFAULT 0')
        con.execute('CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status)')
        con.execute('CREATE INDEX IF NOT EXISTS jobs_created ON jobs(created)')
        con.execute("UPDATE jobs SET status='interrupted', speed=0 WHERE status IN ('queued','running')")
    changed()
    stopping.clear()
    workers = []
    for _ in range(2):
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        workers.append(thread)
    yield
    stopping.set()
    with lock:
        for proc in processes.values():
            stop_process(proc)
    for thread in workers:
        await asyncio.to_thread(thread.join, 3)

app = FastAPI(title='Local Video Downloader', lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', '[::1]'])

@app.middleware('http')
async def origin_check(request: Request, call_next):
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        expected_origin = f'{request.url.scheme}://{request.headers.get("host")}'
        if (origin and origin != expected_origin) or request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'detail': 'درخواست باید از خود برنامه ارسال شود'}, status_code=403)
        if request.headers.get('content-type', '').split(';')[0].strip() != 'application/json':
            return JSONResponse({'detail':'Content-Type must be application/json'}, status_code=415)
        body = bytearray()
        body_limit = MAX_BATCH_BYTES if request.url.path == '/api/jobs/batch' else 16384
        async for chunk in request.stream():
            if len(body) + len(chunk) > body_limit:
                return JSONResponse({'detail':'Request body too large'}, status_code=413)
            body.extend(chunk)
        request._body = bytes(body)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return response

class Inspect(BaseModel):
    url: str = Field(max_length=8192)

class Download(Inspect):
    title: str = Field(default='ویدیو', max_length=300)
    height: int = Field(default=480, ge=0, le=4320)
    folder: str = Field(default='', max_length=1024)

class BatchDownload(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=MAX_BATCH_LINKS)
    height: int = Field(default=480, ge=0, le=4320)
    folder: str = Field(default='', max_length=1024)

def probe(url):
    try:
        result = subprocess.run(command()+['--dump-single-json', '--skip-download', '--', url], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, 'بررسی لینک بیش از حد طول کشید؛ دوباره تلاش کنید')
    if result.returncode:
        raise HTTPException(400, result.stderr[-1500:] or 'لینک پشتیبانی نمی‌شود')
    info = json.loads(result.stdout)
    heights = sorted({int(f['height']) for f in info.get('formats', []) if f.get('height') and f.get('vcodec') != 'none'})
    return {'title': info.get('title', 'ویدیو'), 'duration': info.get('duration'), 'heights': heights}

@app.post('/api/inspect')
async def inspect(body: Inspect):
    async with inspect_slots:
        url = await asyncio.to_thread(validate_url, body.url)
        return await asyncio.to_thread(probe, url)

@app.get('/api/settings')
def settings():
    return {'download_root': HOST_ROOT, 'workers': 2}

@app.get('/api/jobs')
def jobs():
    global snapshot_revision, snapshot_rows
    with snapshot_lock:
        if snapshot_revision != revision:
            with db() as con:
                snapshot_rows = [dict(r) for r in con.execute('SELECT * FROM jobs ORDER BY created DESC, rowid DESC LIMIT ?', (HISTORY_LIMIT,))]
            snapshot_revision = revision
        return snapshot_rows

@app.post('/api/jobs')
def create(body: Download):
    return enqueue(body)

def enqueue(body: Download, batch_index=0):
    validate_url(body.url)
    destination(body.folder)
    job_id = uuid.uuid4().hex
    with lock, db() as con:
        active = con.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
        if active >= MAX_ACTIVE_JOBS or pending.full():
            raise HTTPException(429, 'صف دانلود پر است؛ بعداً تلاش کنید')
        con.execute('INSERT INTO jobs (id,url,title,height,folder,status,batch_index) VALUES (?,?,?,?,?,?,?)', (job_id,body.url,body.title,body.height,body.folder,'queued',batch_index))
        pending.put_nowait(job_id)
    changed()
    return get_job(job_id)

@app.post('/api/history/clear')
def clear_history():
    # Keep active jobs and cancelled jobs whose subprocess is still stopping.
    with lock, db() as con:
        protected = list(processes)
        query = "DELETE FROM jobs WHERE status NOT IN ('queued','running')"
        if protected:
            query += ' AND id NOT IN (' + ','.join('?' for _ in protected) + ')'
        removed = con.execute(query, protected).rowcount
    changed()
    return {'removed': removed}

@app.post('/api/jobs/batch')
def create_batch(body: BatchDownload):
    destination(body.folder)
    accepted, rejected, seen = [], [], set()
    duplicates = 0
    for line, raw_url in enumerate(body.urls, 1):
        url = raw_url.strip()
        if not url:
            continue
        if url in seen:
            duplicates += 1
            continue
        seen.add(url)
        try:
            if len(url) > 8192:
                raise HTTPException(400, 'لینک بیش از حد طولانی است')
            job = enqueue(Download(url=url, height=body.height, folder=body.folder), batch_index=line)
            accepted.append({'line':line, 'id':job['id'], 'url':url})
        except HTTPException as error:
            rejected.append({'line':line, 'url':url, 'error':error.detail})
    return {'accepted':accepted, 'rejected':rejected, 'duplicates':duplicates}

@app.post('/api/jobs/{job_id}/cancel')
def cancel(job_id: str):
    with lock:
        job = get_job(job_id)
        if job['status'] not in ('queued', 'running'):
            raise HTTPException(409, 'این دانلود در حال اجرا نیست')
        update(job_id, status='cancelled', speed=0)
        proc = processes.get(job_id)
        if proc:
            stop_process(proc)
    return get_job(job_id)

@app.post('/api/jobs/{job_id}/retry')
def retry(job_id: str):
    with lock:
        job = get_job(job_id)
        if job['status'] not in ('failed', 'cancelled', 'interrupted') or job_id in processes:
            raise HTTPException(409, 'دانلود هنوز فعال است؛ چند لحظه بعد تلاش کنید')
        if pending.full():
            raise HTTPException(429, 'صف دانلود پر است؛ بعداً تلاش کنید')
        update(job_id, status='queued', phase='', error='', progress=0)
        pending.put_nowait(job_id)
    return get_job(job_id)

@app.get('/api/jobs/{job_id}/file')
def download_file(job_id: str):
    job = get_job(job_id)
    path = Path(job['file']).resolve()
    if job['status'] != 'completed' or not path.is_relative_to(ROOT) or not path.is_file():
        raise HTTPException(404, 'فایل آماده نیست')
    return FileResponse(path, filename=path.name, media_type='video/mp4')

@app.get('/api/events')
async def events(request: Request):
    async def stream():
        previous = None
        heartbeat = time.monotonic()
        while not await request.is_disconnected():
            payload = json.dumps(await asyncio.to_thread(jobs), ensure_ascii=False)
            if payload != previous:
                yield 'data: ' + payload + '\n\n'
                previous = payload
                heartbeat = time.monotonic()
            elif time.monotonic() - heartbeat > 15:
                yield ': heartbeat\n\n'
                heartbeat = time.monotonic()
            await asyncio.sleep(1)
    return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control':'no-cache'})

@app.get('/api/health')
def health():
    return {'ok': True}

app.mount('/', StaticFiles(directory=STATIC, html=True), name='ui')
