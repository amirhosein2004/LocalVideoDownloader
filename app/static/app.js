const $ = id => document.getElementById(id);
const labels = {queued:'در صف',running:'در حال دانلود',completed:'تکمیل‌شده',failed:'ناموفق',cancelled:'لغوشده',interrupted:'متوقف‌شده'};
const phases = {preparing:'دریافت اطلاعات…',downloading:'در حال دانلود',processing:'آماده‌سازی فایل…'};
const cards = new Map();
let info = null, inspectedUrl = '', latestJobs = [], filter = 'all', renderedState = '';
let inputMode = 'single';
let historyPage = 0;
const pageSize = 10;
const fa = value => Number(value).toLocaleString('fa-IR');
function syncQuality() {
  const previousValue = $('quality').value;
  const previous = Number(previousValue);
  const batch = inputMode === 'batch';
  const ready = batch || (info && inspectedUrl === $('url').value.trim());
  $('quality-label').textContent = batch ? 'سقف کیفیت' : 'کیفیت';
  $('quality-hint').textContent = batch ? 'بالاترین کیفیت موجود تا سقف انتخابی.' : ready ? (info.heights.length ? 'کیفیت‌های موجود در منبع.' : 'رزولوشن منبع مشخص نیست.') : 'برای دریافت کیفیت‌ها، لینک را بررسی کنید.';
  $('quality').replaceChildren();
  $('quality').disabled = !ready;
  $('download').disabled = !ready;
  if (!ready) {
    const option = document.createElement('option'); option.value = ''; option.textContent = 'ابتدا لینک را بررسی کنید'; $('quality').append(option);
    return;
  }
  for (const height of batch ? [144,240,360,480,720,1080] : info.heights) {
    const option = document.createElement('option'); option.value = height; option.textContent = `${batch ? 'حداکثر ' : ''}${fa(height)}p`; $('quality').append(option);
  }
  const best = document.createElement('option'); best.value = 0;
  best.textContent = !batch && !info.heights.length ? 'کیفیت همین لینک · رزولوشن نامشخص' : 'بهترین کیفیت موجود';
  $('quality').append(best);
  const available = [...$('quality').options].map(option => Number(option.value));
  $('quality').value = previousValue !== '' && available.includes(previous) ? previous : available.includes(480) ? 480 : 0;
}

function friendlyError(text) {
  if (/sign in.*(bot|confirm)|not a bot/i.test(text)) return 'یوتیوب از این اتصال درخواست ورود به حساب کرده است. این ویدیو فعلاً بدون ورود قابل دریافت نیست؛ ورود به حساب در این نسخه پشتیبانی نمی‌شود.';
  if (/private video|login required|sign in/i.test(text)) return 'این ویدیو به ورود به حساب یا دسترسی خصوصی نیاز دارد. ورود به حساب در این نسخه پشتیبانی نمی‌شود.';
  if (/timed out|timeout/i.test(text)) return 'پاسخ سایت طول کشید؛ اتصال اینترنت را بررسی کن و دوباره تلاش کن.';
  if (/unsupported url/i.test(text)) return 'این لینک پشتیبانی نمی‌شود. لینک صفحه ویدیو یا لینک مستقیم فایل را امتحان کن.';
  if (/HTTP Error 403|Forbidden/i.test(text)) return 'سایت اجازه دریافت این ویدیو را به اتصال فعلی نمی‌دهد؛ ممکن است لینک منقضی شده یا به ورود نیاز داشته باشد.';
  if (/requested format.*not available/i.test(text)) return 'کیفیت انتخابی موجود نیست؛ «بهترین کیفیت موجود» را امتحان کن.';
  return /[\u0600-\u06ff]/.test(text) ? text : 'دریافت ویدیو انجام نشد. لینک و دسترسی به سایت را بررسی کن.';
}

function message(text, error = true) {
  $('message').textContent = error ? friendlyError(text) : text;
  $('message').classList.toggle('success', !error);
  $('message').hidden = !text;
}

async function api(url, body) {
  const response = await fetch(url, {method:body === undefined ? 'GET' : 'POST',headers:body === undefined ? {} : {'Content-Type':'application/json'},body:body === undefined ? undefined : JSON.stringify(body)});
  const value = await response.json();
  if (!response.ok) throw Error(typeof value.detail === 'string' ? value.detail : 'اطلاعات واردشده معتبر نیست');
  return value;
}

function resetFeedback() { $('message').hidden = true; $('batch-results').hidden = true; }
$('url').addEventListener('input', () => { info = null; $('preview').hidden = true; resetFeedback(); syncQuality(); });
$('urls').addEventListener('input', () => {
  const lines = $('urls').value.split(/\r?\n/);
  $('batch-count').textContent = `${fa(lines.filter(line => line.trim()).length)} لینک`;
  resetFeedback();
});
for (const button of document.querySelectorAll('[data-mode]')) button.onclick = () => {
  inputMode = button.dataset.mode;
  syncQuality();
  for (const item of document.querySelectorAll('[data-mode]')) { item.classList.toggle('active',item === button); item.setAttribute('aria-pressed',String(item === button)); }
  $('single-input').hidden = inputMode !== 'single';
  $('batch-input').hidden = inputMode !== 'batch';
  $('url').required = inputMode === 'single'; $('url').disabled = inputMode !== 'single';
  $('urls').required = inputMode === 'batch'; $('urls').disabled = inputMode !== 'batch';
  $('preview').hidden = inputMode !== 'single' || !info;
  $('download').firstChild.textContent = inputMode === 'batch' ? 'افزودن لینک‌ها به صف ' : 'شروع دانلود ';
  resetFeedback();
};
$('paste').onclick = async () => {
  try {
    $('url').value = (await navigator.clipboard.readText()).trim();
    $('url').dispatchEvent(new Event('input'));
    $('url').focus();
  } catch { message('اجازه دسترسی به کلیپ‌بورد داده نشد. با Ctrl+V لینک را بچسبان.'); }
};

$('inspect').onclick = async () => {
  if (!$('url').reportValidity()) return;
  const url = $('url').value.trim();
  const button = $('inspect');
  button.disabled = true;
  button.textContent = 'در حال بررسی…';
  info = null;
  syncQuality();
  message('در حال دریافت اطلاعات و کیفیت‌های ویدیو…', false);
  try {
    const result = await api('/api/inspect', {url});
    if ($('url').value.trim() !== url || inputMode !== 'single') return;
    info = result; inspectedUrl = url;
    $('title').textContent = result.title;
    $('duration').textContent = result.duration ? `مدت: ${fa(Math.floor(result.duration / 60))} دقیقه و ${fa(Math.floor(result.duration % 60))} ثانیه` : 'مدت ویدیو اعلام نشده است';
    $('preview').hidden = false;
    syncQuality();
    message('لینک بررسی شد.', false);
  } catch (error) {
    if ($('url').value.trim() === url && inputMode === 'single') { info = null; syncQuality(); message(error.message); }
  } finally { button.disabled = false; button.textContent = 'بررسی لینک'; }
};

$('form').onsubmit = async event => {
  event.preventDefault();
  const button = $('download'); button.disabled = true;
  try {
    if (inputMode === 'batch') {
      const urls = $('urls').value.trim().split(/\r?\n/);
      if (!urls.some(url => url.trim())) throw Error('حداقل یک لینک وارد کن.');
      if (urls.length > 500) throw Error('در هر نوبت حداکثر ۵۰۰ خط وارد کنید.');
      if (new TextEncoder().encode(JSON.stringify({urls,height:Number($('quality').value),folder:$('folder').value.trim()})).length > 1048576) throw Error('حجم لینک‌ها بیش از یک مگابایت است؛ آن‌ها را در چند دسته کوچک‌تر وارد کنید.');
      message('در حال بررسی لینک‌ها و اضافه‌کردن به صف…', false);
      const result = await api('/api/jobs/batch',{urls,height:Number($('quality').value),folder:$('folder').value.trim()});
      const summary = `${fa(result.accepted.length)} لینک به صف اضافه شد.${result.duplicates ? ` ${fa(result.duplicates)} لینک تکراری نادیده گرفته شد.` : ''}${result.rejected.length ? ` ${fa(result.rejected.length)} لینک اضافه نشد؛ جزئیات را پایین ببین.` : ''}`;
      message(summary, result.accepted.length === 0);
      $('batch-results').replaceChildren();
      $('batch-results').hidden = !result.rejected.length;
      for (const rejected of result.rejected) {
        const row = document.createElement('div');
        const label = document.createElement('strong'); label.textContent = `خط ${fa(rejected.line)}: ${friendlyError(String(rejected.error))}`;
        const url = document.createElement('code'); url.dir = 'ltr'; url.textContent = rejected.url;
        row.append(label,url); $('batch-results').append(row);
      }
      render(await api('/api/jobs'));
      return;
    }
    const url = $('url').value.trim();
    if (!info || inspectedUrl !== url) throw Error('ابتدا همین لینک را بررسی کن تا کیفیت‌های واقعی آن مشخص شوند.');
    await api('/api/jobs', {url,title:info && inspectedUrl === url ? info.title : 'ویدیو',height:Number($('quality').value),folder:$('folder').value.trim()});
    message('به صف دانلود اضافه شد.', false);
    render(await api('/api/jobs'));
  } catch (error) { message(error.message); }
  finally { syncQuality(); }
};

function matches(job) {
  if (filter === 'active') return ['queued','running'].includes(job.status);
  if (filter === 'failed') return ['failed','interrupted','cancelled'].includes(job.status);
  return filter === 'all' || job.status === filter;
}

function makeCard(job) {
  const node = document.createElement('article');
  // Fixed markup only. All data from URLs, titles and errors uses textContent.
  node.innerHTML = '<div class="job-icon"></div><div class="job-body"><div class="job-top"><strong></strong><span class="status"></span></div><div class="job-source"></div><progress max="100"></progress><div class="job-meta"><span></span><bdi dir="ltr"></bdi></div><div class="job-error" hidden></div><details class="error-details" hidden><summary>جزئیات فنی خطا</summary><pre></pre></details><div class="actions"><button type="button"></button><a>دریافت فایل</a></div></div><div class="job-percent"><span></span><small></small></div>';
  const button = node.querySelector('.actions button');
  button.onclick = async () => {
    button.disabled = true;
    try {
      await api(`/api/jobs/${job.id}/${button.dataset.action}`, {});
      render(await api('/api/jobs'));
    } catch (error) { message(error.message); }
    finally { button.disabled = false; }
  };
  node.querySelector('.actions a').href = `/api/jobs/${job.id}/file`;
  return {node,signature:''};
}

function updateCard(card, job) {
  const signature = JSON.stringify(job);
  if (card.signature === signature) return;
  card.signature = signature;
  const node = card.node;
  node.className = `job ${job.status}`;
  node.querySelector('.job-icon').textContent = job.status === 'completed' ? '✓' : job.status === 'failed' ? '!' : '↓';
  node.querySelector('strong').textContent = job.batch_index ? `${String(job.batch_index).padStart(3,'0')} · ${job.title}` : job.title;
  node.querySelector('.status').textContent = job.status === 'running' ? phases[job.phase] || labels.running : labels[job.status] || job.status;
  let source = '';
  try { source = new URL(job.url).hostname; } catch {}
  node.querySelector('.job-source').textContent = source;
  const progress = node.querySelector('progress');
  progress.setAttribute('aria-label', 'پیشرفت دانلود');
  if (job.status === 'running' && (job.phase === 'preparing' || job.progress <= 0)) progress.removeAttribute('value');
  else progress.value = job.progress;
  const percentage = `${fa(Math.floor(job.progress))}٪`;
  node.querySelector('.job-percent>span').textContent = job.status === 'queued' ? '—' : percentage;
  node.querySelector('.job-percent small').textContent = job.status === 'completed' ? 'کامل شد' : 'پیشرفت';
  const speed = job.speed ? ` · ${(job.speed / 1048576).toFixed(1)} MB/s` : '';
  const eta = job.eta && job.status === 'running' ? ` · حدود ${fa(Math.ceil(job.eta / 60))} دقیقه باقی‌مانده` : '';
  node.querySelector('.job-meta>span').textContent = `${percentage} · ${job.height ? fa(job.height) + 'p' : 'بهترین کیفیت'}${speed}${eta}`;
  node.querySelector('.job-meta bdi').textContent = job.folder || 'پوشه اصلی';
  const error = node.querySelector('.job-error');
  error.textContent = job.error ? friendlyError(job.error) : ''; error.hidden = !job.error;
  const details = node.querySelector('.error-details');
  details.hidden = !job.error; details.querySelector('pre').textContent = job.error || '';
  const button = node.querySelector('.actions button');
  button.hidden = job.status === 'completed';
  button.dataset.action = ['queued','running'].includes(job.status) ? 'cancel' : 'retry';
  button.textContent = button.dataset.action === 'cancel' ? 'لغو دانلود' : 'تلاش مجدد / ادامه';
  node.querySelector('.actions a').hidden = job.status !== 'completed';
}

function render(jobs) {
  latestJobs = jobs;
  const filtered = jobs.filter(matches);
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  historyPage = Math.min(historyPage, pages - 1);
  const shown = filtered.slice(historyPage * pageSize, (historyPage + 1) * pageSize);
  const state = JSON.stringify([filter,historyPage,jobs]);
  if (state === renderedState) return;
  renderedState = state;
  $('count').textContent = fa(jobs.length);
  $('page-info').textContent = `صفحه ${fa(historyPage + 1)} از ${fa(pages)}`;
  $('previous-page').disabled = historyPage === 0;
  $('next-page').disabled = historyPage >= pages - 1;
  $('pagination').hidden = pages <= 1;
  const ids = new Set(shown.map(j => j.id));
  for (const [id,card] of cards) if (!ids.has(id)) { card.node.remove(); cards.delete(id); }
  $('jobs').querySelector('.empty')?.remove();
  let visible = 0;
  shown.forEach((job,index) => {
    if (!cards.has(job.id)) cards.set(job.id,makeCard(job));
    const card = cards.get(job.id);
    updateCard(card,job);
    card.node.hidden = !matches(job);
    if (!card.node.hidden) visible++;
    if ($('jobs').children[index] !== card.node) $('jobs').insertBefore(card.node,$('jobs').children[index] || null);
  });
  if (!visible) {
    const empty = document.createElement('div'); empty.className = 'empty';
    empty.innerHTML = '<span>↙</span><strong></strong><p></p>';
    empty.querySelector('strong').textContent = jobs.length ? 'موردی یافت نشد.' : 'دانلودی ثبت نشده است.';
    empty.querySelector('p').textContent = jobs.length ? 'فیلتر دیگری را انتخاب کن.' : 'لینک ویدیو را وارد کنید.';
    $('jobs').append(empty);
  }
}

for (const button of document.querySelectorAll('[data-filter]')) button.onclick = () => {
  historyPage = 0;
  filter = button.dataset.filter;
  for (const item of document.querySelectorAll('[data-filter]')) { item.classList.toggle('active',item === button); item.setAttribute('aria-pressed',String(item === button)); }
  render(latestJobs);
};
$('previous-page').onclick = () => { historyPage--; render(latestJobs); };
$('next-page').onclick = () => { historyPage++; render(latestJobs); };
$('clear-history').onclick = () => { $('clear-confirm').hidden = false; };
$('keep-history').onclick = () => { $('clear-confirm').hidden = true; };
$('confirm-clear').onclick = async () => {
  $('confirm-clear').disabled = true;
  try {
    const result = await api('/api/history/clear', {});
    $('clear-confirm').hidden = true;
    historyPage = 0;
    render(await api('/api/jobs'));
    $('history-message').textContent = `${fa(result.removed)} سابقه پاک شد. فایل‌ها باقی ماندند.`;
  } catch (error) { $('history-message').textContent = friendlyError(error.message); }
  finally { $('confirm-clear').disabled = false; }
};
api('/api/settings').then(s => { $('root').textContent = s.download_root; }).catch(e => message(e.message));
api('/api/jobs').then(render).catch(e => message(e.message));
const stream = new EventSource('/api/events');
document.addEventListener('keydown', event => {
  if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return;
  if (event.target.closest('input, textarea, select, [contenteditable="true"]')) return;
  event.preventDefault();
  (document.querySelector('[data-mode="batch"].active') ? $('urls') : $('url')).focus();
});
stream.onmessage = event => render(JSON.parse(event.data));
stream.onerror = () => { $('connection-dot').classList.add('offline'); $('connection-text').textContent = 'اتصال مجدد…'; };
stream.onopen = () => { $('connection-dot').classList.remove('offline'); $('connection-text').textContent = 'متصل'; };
