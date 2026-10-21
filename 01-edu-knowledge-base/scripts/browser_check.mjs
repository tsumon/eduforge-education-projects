#!/usr/bin/env node
/** Real browser regression checks. Requires Node 22+, installed Chrome/Edge, no npm dependencies.
 * Uses the skill's zero-dependency CDP client. Run only against the isolated runtime-review service.
 * The two questions stay in review history; the uniquely named temporary document is always removed.
 */
import { homedir } from 'node:os';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
if (args.includes('--help') || args.includes('-h')) {
  console.log(`Usage: node scripts/browser_check.mjs [--base-url URL] [--out DIR] [--token TOKEN]

Defaults: http://127.0.0.1:8001, Chinese screenshot names in the project root.
Environment: TASTE_SKILL_DIR, EDU_BROWSER_BASE_URL, EDU_API_TOKEN.
TASTE_SKILL_DIR must contain scripts/lib/cdp.mjs; no packages are installed.
The service data directory must be named runtime-review and already contain sample documents.
Checks real typing/clicks, supported and unsupported questions, original evidence, citation navigation,
document search/create/details, history filters, exports, keyboard use, token save, mobile overflow.
Writes Chinese desktop/mobile screenshots to the project root and screenshots/browser-check.json. Deletes only its temporary document.
Questions generated during this run remain in the isolated review history.`);
  process.exit(0);
}
function flag(name, fallback) {
  const i = args.indexOf(name);
  if (i < 0) return fallback;
  if (!args[i + 1] || args[i + 1].startsWith('--')) throw new Error(`Missing value for ${name}`);
  return args[i + 1];
}
for (let i = 0; i < args.length; i += 2) {
  if (!['--base-url', '--out', '--token'].includes(args[i])) throw new Error(`Unknown option: ${args[i]}`);
}
const baseUrl = new URL(flag('--base-url', process.env.EDU_BROWSER_BASE_URL || 'http://127.0.0.1:8001'));
assert(['127.0.0.1', 'localhost', '[::1]'].includes(baseUrl.hostname), 'Use a local isolated review service');
const output = resolve(root, flag('--out', '.'));
const reportOutput = join(root, 'screenshots');
const token = flag('--token', process.env.EDU_API_TOKEN || '');
const skillDir = process.env.TASTE_SKILL_DIR || join(homedir(), '.agents', 'skills', 'design-taste-frontend');
const { Browser, sleep } = await import(pathToFileURL(join(skillDir, 'scripts', 'lib', 'cdp.mjs')).href);
const temporaryTitle = `浏览器回归验证 ${randomUUID().slice(0, 8)}`;
const temporaryContent = '# 界面回归资料\n\n本条资料仅用于验证知识库的资料新建、搜索与详情查看流程。完成浏览器检查后会自动删除，不属于正式课程资料。';
const report = { startedAt: new Date().toISOString(), baseUrl: baseUrl.origin, screenshotDirectory: output === root ? '.' : output, checks: [], screenshots: [], layouts: [], errors: [] };
let browser, page, temporaryId, baselineIds, answeredRecord, refusedRecord;
let createdDocumentAttempted = false;
let askRequests = 0;
const apiErrors = [];
const exportRequests = [];

async function api(path, { method = 'GET', raw = false } = {}) {
  const response = await fetch(new URL(`/api/knowledge${path}`, baseUrl), {
    method, headers: token ? { Authorization: `Bearer ${token}` } : {}, signal: AbortSignal.timeout(20000),
  });
  if (!response.ok) throw new Error(`${method} ${path}: HTTP ${response.status}`);
  return raw ? { text: await response.text(), type: response.headers.get('content-type'), disposition: response.headers.get('content-disposition') } : response.json();
}
async function check(name, run) {
  const start = Date.now();
  try {
    const detail = await run();
    report.checks.push({ name, passed: true, ms: Date.now() - start, ...(detail === undefined ? {} : { detail }) });
    console.log(`PASS ${name}`);
    return detail;
  } catch (error) {
    report.checks.push({ name, passed: false, ms: Date.now() - start, error: error.message });
    throw error;
  }
}
async function waitFor(predicate, arg, label = 'page condition', timeout = 12000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const result = await page.eval(predicate, arg);
    if (result) return result;
    await sleep(100);
  }
  throw new Error(`Timed out waiting for ${label}`);
}
async function click(selector) {
  const box = await page.eval(selector => {
    const element = document.querySelector(selector);
    if (!element) throw new Error(`Missing element: ${selector}`);
    element.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' });
    const r = element.getBoundingClientRect();
    if (!r.width || !r.height || element.disabled) throw new Error(`Element is not interactive: ${selector}`);
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    const at = document.elementFromPoint(x, y);
    if (!(at === element || element.contains(at))) throw new Error(`Element is covered: ${selector} by ${at?.tagName}`);
    return { x, y };
  }, selector);
  await page.send('Input.dispatchMouseEvent', { type: 'mouseMoved', ...box });
  await page.send('Input.dispatchMouseEvent', { type: 'mousePressed', button: 'left', clickCount: 1, ...box });
  await page.send('Input.dispatchMouseEvent', { type: 'mouseReleased', button: 'left', clickCount: 1, ...box });
}
async function key(name, modifiers = 0) {
  const definitions = { Enter: ['Enter', 13, '\r'], Escape: ['Escape', 27], Tab: ['Tab', 9], Backspace: ['Backspace', 8], a: ['KeyA', 65] };
  const [code, codeNumber, text] = definitions[name];
  await page.send('Input.dispatchKeyEvent', { type: 'keyDown', key: name, code, windowsVirtualKeyCode: codeNumber, nativeVirtualKeyCode: codeNumber, modifiers, ...(text ? { text, unmodifiedText: text } : {}) });
  await page.send('Input.dispatchKeyEvent', { type: 'keyUp', key: name, code, windowsVirtualKeyCode: codeNumber, nativeVirtualKeyCode: codeNumber, modifiers });
}
async function fill(selector, text) {
  await click(selector);
  await key('a', 2);
  await key('Backspace');
  if (text) await page.send('Input.insertText', { text });
  assert((await page.eval(selector => document.querySelector(selector).value, selector)) === text, `Typing into ${selector}`);
}
async function view(name) {
  await click(`.tabs a[data-view="${name}"]`);
  await waitFor(name => {
    const element = document.querySelector(`.view[data-view="${name}"]`);
    return element && !element.hidden && element.getBoundingClientRect().height > 0;
  }, name, `${name} view`);
}
async function shot(name) {
  await waitFor(() => !document.querySelector('#toast') || document.querySelector('#toast').hidden, undefined, 'transient toast to clear', 8000);
  await page.eval(() => { window.scrollTo({ top: 0, behavior: 'instant' }); });
  await sleep(180);
  await page.screenshot({ path: join(output, name), format: 'png', fullPage: true });
  report.screenshots.push(name);
}
async function layout(name) {
  const result = await page.eval(() => ({
    viewport: innerWidth,
    pageWidth: document.documentElement.scrollWidth,
    nav: [...document.querySelectorAll('.tabs a[data-view]')].map(a => {
      const r = a.getBoundingClientRect();
      return { view: a.dataset.view, x: r.x, right: r.right, width: r.width, height: r.height };
    }),
  }));
  report.layouts.push({ name, ...result });
  assert(result.pageWidth <= result.viewport + 2, `${name} page overflows: ${result.pageWidth}px in ${result.viewport}px`);
  assert(result.nav.every(a => a.width > 0 && a.height > 0 && a.x >= -1 && a.right <= result.viewport + 1), `${name} navigation is clipped`);
}
async function currentRecord() {
  const href = await page.eval(() => document.querySelector('#answer-area .answer .export')?.getAttribute('href'));
  assert(href && /\/export\/[^/]+$/.test(href), 'Rendered answer has an export link with a real record ID');
  const id = decodeURIComponent(href.split('/').at(-1));
  return api(`/history/${encodeURIComponent(id)}`);
}
async function ask(question, keyboard = false) {
  const previousHref = await page.eval(() => document.querySelector('#answer-area .export')?.getAttribute('href'));
  await fill('#question', question);
  if (keyboard) await key('Enter'); else await click('#btn-ask');
  await waitFor(({ question, previousHref }) => {
    const a = document.querySelector('#answer-area .answer');
    return a?.querySelector('.answer-q')?.textContent === question &&
      a.querySelector('.export')?.getAttribute('href') !== previousHref && !document.querySelector('#btn-ask').disabled;
  }, { question, previousHref }, 'new answer', 30000);
  return currentRecord();
}
async function assertAnswerEvidence(record) {
  assert.equal(record.refused, false, 'A supported question must be answered');
  assert(record.citations.length > 0, 'Answer includes citations');
  const rendered = await page.eval(() => ({
    points: [...document.querySelectorAll('#answer-area .points .point-text')].map(e => e.textContent),
    citations: [...document.querySelectorAll('#answer-area .citation-list > li')].map(e => ({ text: e.textContent, highlighted: [...e.querySelectorAll('mark')].map(mark => mark.textContent).join('') })),
    answerText: document.querySelector('#answer-area .answer-text')?.textContent,
  }));
  assert.equal(rendered.citations.length, record.citations.length, 'All actual citations appear on the page');
  for (const citation of record.citations) {
    const original = await api(`/documents/${encodeURIComponent(citation.document_id)}`);
    const chunk = original.chunks.find(c => c.id === citation.chunk_id);
    assert(chunk, 'Citation points to a real original chunk');
    assert.equal(chunk.text, citation.text, 'Citation reproduces original source text');
    assert(citation.excerpt && citation.text.includes(citation.excerpt), 'Excerpt belongs to original evidence');
    const visibleCitation = rendered.citations.find(item => item.text.includes(citation.text) && item.text.includes(citation.title));
    assert(visibleCitation, 'Page displays source and its original text');
    if (record.mode === 'extractive') {
      assert(rendered.points.includes(citation.excerpt), 'Page answer displays the real original excerpt');
      assert.equal(visibleCitation.highlighted.replace(/\s/g, ''), citation.excerpt.replace(/\s/g, ''), 'Source highlights the excerpt used in the answer');
    }
  }
  return { recordId: record.id, mode: record.mode, citations: record.citations.length };
}

try {
  await check('isolated review data is ready', async () => {
    const stats = await api('/stats');
    assert.equal(String(stats.data_dir).replaceAll('\\', '/').replace(/\/$/, '').split('/').at(-1), 'runtime-review', 'Refusing to mutate a service outside runtime-review');
    const documents = (await api('/documents')).documents;
    assert(documents.length > 0, 'Load example documents into runtime-review before running');
    baselineIds = new Set(documents.map(d => d.id));
    return { documents: documents.length, dataDir: stats.data_dir };
  });
  mkdirSync(output, { recursive: true });
  browser = await Browser.launch({ width: 1440, height: 1050 });
  // Exercise the browser download action without writing exported review history to disk.
  await browser.send('Browser.setDownloadBehavior', { behavior: 'deny' });
  page = await browser.newPage({ width: 1440, height: 1050, reducedMotion: true });
  await page.send('Network.enable');
  browser.on(page.sessionId, 'Network.requestWillBeSent', ({ request }) => {
    if (request.method === 'POST' && new URL(request.url).pathname === '/api/knowledge/ask') askRequests++;
    if (/\/api\/knowledge\/(?:export\/|history\/export\.jsonl)/.test(new URL(request.url).pathname)) {
      exportRequests.push({ url: request.url, authorization: Object.entries(request.headers).find(([name]) => name.toLowerCase() === 'authorization')?.[1] || '' });
    }
  });
  browser.on(page.sessionId, 'Network.responseReceived', ({ response }) => {
    if (response.status >= 400 && response.url.startsWith(new URL('/api/', baseUrl).href)) apiErrors.push({ url: response.url, status: response.status });
  });
  if (token) {
    await page.send('Page.addScriptToEvaluateOnNewDocument', { source: `localStorage.setItem('eduforge.kb.token', ${JSON.stringify(token)});` });
  }
  await page.goto(baseUrl.href, { settleMs: 350 });
  await waitFor(() => document.querySelector('#status-dot')?.classList.contains('ok'), undefined, 'API ready');
  const sample = (await api('/sample')).questions;
  const supported = sample.find(q => q.expect === 'answer' && q.question.includes('递归深度')) || sample.find(q => q.expect === 'answer');
  const unsupported = sample.find(q => q.expect === 'refuse');
  assert(supported && unsupported, 'Review fixture must provide answerable and unsupported questions');

  await check('keyboard newline and Enter submit', async () => {
    await fill('#question', '换行验证');
    const requestsBefore = askRequests;
    await key('Enter', 8);
    await sleep(180);
    assert.equal(await page.eval(() => document.querySelector('#question').value), '换行验证\n', 'Shift+Enter inserts a newline');
    assert.equal(askRequests, requestsBefore, 'Shift+Enter does not submit');
    answeredRecord = await ask(supported.question, true);
    assert.equal(askRequests, requestsBefore + 1, 'Enter submits exactly one real request');
    return assertAnswerEvidence(answeredRecord);
  });
  await check('citation link preserves answer and route', async () => {
    const before = await page.eval(() => ({ hash: location.hash, href: document.querySelector('#answer-area .export').getAttribute('href'), question: document.querySelector('#question').value }));
    await click('#answer-area .points a[href^="#cite-"], #answer-area .answer-text a[href^="#cite-"]');
    await sleep(160);
    const after = await page.eval(() => ({ hash: location.hash, href: document.querySelector('#answer-area .export')?.getAttribute('href'), question: document.querySelector('#question').value, visible: !document.querySelector('.view[data-view="ask"]').hidden, citations: document.querySelectorAll('#answer-area .citation-list > li').length }));
    assert.equal(after.hash, before.hash, 'Evidence navigation does not replace the application route');
    assert.equal(after.href, before.href, 'Evidence navigation keeps the answer');
    assert.equal(after.question, before.question);
    assert(after.visible && after.citations > 0, 'Answer view and evidence stay visible');
  });
  await check('answer feedback is persisted and can be changed', async () => {
    await click('#answer-area [data-helpful="true"]');
    await waitFor(() => document.querySelector('#answer-area [data-helpful="true"]')?.classList.contains('on'), undefined, 'helpful feedback');
    let saved = await api(`/history/${answeredRecord.id}`);
    assert.equal(saved.feedback.helpful, true, 'Helpful feedback is saved on the actual record');
    await click('#answer-area [data-helpful="false"]');
    await waitFor(() => document.querySelector('#answer-area [data-helpful="false"]')?.classList.contains('on'), undefined, 'changed feedback');
    saved = await api(`/history/${answeredRecord.id}`);
    assert.equal(saved.feedback.helpful, false, 'Feedback can be revised without a duplicate answer');
  });
  await check('Markdown export link resolves to this answer', async () => {
    const href = await page.eval(() => document.querySelector('#answer-area .export').getAttribute('href'));
    const result = await api(new URL(href, baseUrl).pathname.replace('/api/knowledge', ''), { raw: true });
    assert.match(result.type, /text\/markdown/);
    assert.match(result.disposition, /attachment/);
    assert(result.text.includes(answeredRecord.question) && result.text.includes(answeredRecord.citations[0].title), 'Export contains the current question and source');
  });
  await check('desktop answer layout', async () => { await layout('ask-desktop'); await shot('01_桌面_知识问答.png'); });
  await check('mobile answer and citations', async () => {
    await page.setViewport({ width: 390, height: 844, mobile: true });
    await layout('ask-mobile');
    assert(await page.eval(() => document.querySelector('#answer-area .citation-list > li')?.getBoundingClientRect().width > 0), 'Mobile citations are visible');
    await shot('02_手机_知识问答.png');
    await page.setViewport({ width: 1440, height: 1050 });
  });
  await check('unsupported question refuses without fabricated citations', async () => {
    refusedRecord = await ask(unsupported.question);
    assert.equal(refusedRecord.refused, true);
    assert.equal(refusedRecord.citations.length, 0);
    const state = await page.eval(() => ({ refusal: document.querySelector('#answer-area .refusal')?.textContent, citations: document.querySelectorAll('#answer-area .citation-list > li').length }));
    assert(state.refusal?.includes('资料不足'), 'Page clearly explains insufficient evidence');
    assert.equal(state.citations, 0);
    return { recordId: refusedRecord.id };
  });
  await check('keyboard tab navigation', async () => {
    await view('docs');
    await key('Tab');
    const focus = await page.eval(() => ({ view: document.activeElement?.dataset.view, rects: document.activeElement?.getClientRects().length, style: getComputedStyle(document.activeElement).outlineStyle, width: getComputedStyle(document.activeElement).outlineWidth, shadow: getComputedStyle(document.activeElement).boxShadow }));
    assert.equal(focus.view, 'history', 'Tab reaches the next navigation link');
    assert(focus.rects > 0 && ((focus.style !== 'none' && focus.width !== '0px') || focus.shadow !== 'none'), 'Keyboard focus has a visible indicator');
    await key('Enter');
    await waitFor(() => !document.querySelector('.view[data-view="history"]').hidden, undefined, 'keyboard navigation');
  });
  await check('document dialog keyboard and validation', async () => {
    await view('docs');
    await waitFor(() => document.querySelectorAll('#doc-rows tr').length > 0, undefined, 'document rows');
    await click('#btn-new-text');
    await waitFor(() => document.querySelector('#dlg-text').open, undefined, 'text dialog');
    assert(await page.eval(() => document.querySelector('#dlg-text').contains(document.activeElement)), 'Modal moves focus inside');
    await key('Escape');
    assert.equal(await page.eval(() => document.querySelector('#dlg-text').open), false);
    assert.equal(await page.eval(() => document.activeElement.id), 'btn-new-text', 'Escape returns focus to the opener');
    await click('#btn-new-text');
    await click('#form-text button[type="submit"]');
    assert(await page.eval(() => document.querySelector('#dlg-text').open && !document.querySelector('#form-text [name="title"]').validity.valid), 'Empty form remains open and is rejected');
  });
  await check('new text is saved and searchable', async () => {
    await fill('#form-text [name="title"]', temporaryTitle);
    await fill('#form-text [name="content"]', temporaryContent);
    createdDocumentAttempted = true;
    await click('#form-text button[type="submit"]');
    await waitFor(() => !document.querySelector('#dlg-text').open, undefined, 'document saved');
    const matches = (await api('/documents')).documents.filter(d => d.title === temporaryTitle && !baselineIds.has(d.id));
    assert.equal(matches.length, 1, 'Exactly one new document was persisted');
    temporaryId = matches[0].id;
    await fill('#doc-search', temporaryTitle);
    await waitFor(title => {
      const rows = [...document.querySelectorAll('#doc-rows tr')];
      return rows.length === 1 && rows[0].textContent.includes(title);
    }, temporaryTitle, 'document title search');
    await fill('#doc-search', 'no-such-document-93861');
    await waitFor(() => document.querySelectorAll('#doc-rows tr button').length === 0 && /没有|未找到|无匹配/.test(document.querySelector('#doc-rows').textContent), undefined, 'empty search results');
    await fill('#doc-search', temporaryTitle);
    await waitFor(title => {
      const rows = [...document.querySelectorAll('#doc-rows tr')];
      return rows.length === 1 && rows[0].textContent.includes(title) && !!rows[0].querySelector('button');
    }, temporaryTitle, 'temporary document row');
  });
  await check('document detail drawer contains the saved source', async () => {
    await click('#doc-rows tr button');
    await waitFor(() => !document.querySelector('#drawer').hidden, undefined, 'document drawer');
    const detail = await page.eval(() => ({ title: document.querySelector('#drawer-title').textContent, chunks: document.querySelector('#drawer-chunks').textContent, focused: document.activeElement.id }));
    assert.equal(detail.title, temporaryTitle);
    assert(detail.chunks.includes('本条资料仅用于验证知识库'));
    assert.equal(detail.focused, 'drawer-close');
    await shot('05_桌面_资料详情.png');
    await key('Escape');
    assert.equal(await page.eval(() => document.querySelector('#drawer').hidden), true);
    await fill('#doc-search', '');
    await waitFor(() => document.querySelectorAll('#doc-rows tr').length > 1, undefined, 'all documents restored');
  });
  await check('document layouts', async () => {
    await layout('docs-desktop'); await shot('03_桌面_资料管理.png');
    await page.setViewport({ width: 390, height: 844, mobile: true });
    await layout('docs-mobile'); await shot('04_手机_资料管理.png');
    await page.setViewport({ width: 1440, height: 1050 });
  });
  await check('history searches and refused filters', async () => {
    await view('history');
    await fill('#history-search', refusedRecord.question);
    await click('[data-refused="true"]');
    await waitFor(question => {
      const rows = [...document.querySelectorAll('#history-rows tr')];
      return rows.length > 0 && rows.every(row => row.textContent.includes(question) && row.textContent.includes('资料不足'));
    }, refusedRecord.question, 'refused history search');
    await fill('#history-search', answeredRecord.question);
    await click('[data-refused="false"]');
    await waitFor(question => {
      const rows = [...document.querySelectorAll('#history-rows tr')];
      return rows.length > 0 && rows.every(row => row.textContent.includes(question) && row.textContent.includes('已回答'));
    }, answeredRecord.question, 'answered history search');
    await click('#history-rows tr');
    await waitFor(question => !document.querySelector('.view[data-view="ask"]').hidden && document.querySelector('#answer-area .answer-q')?.textContent === question, answeredRecord.question, 'history replay');
    assert.equal((await currentRecord()).id, answeredRecord.id, 'Opening history replays the saved record');
    await view('history');
    await fill('#history-search', '');
    await click('[data-refused=""]');
    await waitFor(() => document.querySelectorAll('#history-rows tr').length >= 2, undefined, 'all history');
    await layout('history-desktop'); await shot('06_桌面_问答记录.png');
    await page.setViewport({ width: 390, height: 844, mobile: true });
    await layout('history-mobile'); await shot('07_手机_问答记录.png');
    await page.setViewport({ width: 1440, height: 1050 });
  });
  await check('JSONL export link contains both real records', async () => {
    const href = await page.eval(() => document.querySelector('#btn-export-jsonl').getAttribute('href'));
    const result = await api(new URL(href, baseUrl).pathname.replace('/api/knowledge', ''), { raw: true });
    assert.match(result.type, /x-ndjson/);
    assert.match(result.disposition, /attachment/);
    const records = result.text.trim().split('\n').map(line => JSON.parse(line));
    assert(records.some(r => r.id === answeredRecord.id) && records.some(r => r.id === refusedRecord.id));
    return { records: records.length };
  });
  await check('settings token save and layouts', async () => {
    await view('settings');
    await waitFor(() => document.querySelectorAll('#policy-table tr').length > 0 && document.querySelectorAll('#runtime-table tr').length > 0, undefined, 'real settings');
    const value = token || 'browser-check-local-only';
    await fill('#token-input', value);
    await click('#form-token button[type="submit"]');
    assert((await page.eval(() => localStorage.getItem('eduforge.kb.token'))) === value, 'Saving persists the token in this browser');
    await view('history');
    const previousExports = exportRequests.length;
    await click('#btn-export-jsonl');
    const deadline = Date.now() + 5000;
    while (exportRequests.length === previousExports && Date.now() < deadline) await sleep(50);
    assert(exportRequests.length > previousExports, 'Actual export click sends a browser request');
    assert(exportRequests[previousExports].authorization === `Bearer ${value}`, 'Actual export fetch carries the saved browser token');
    await waitFor(() => !document.querySelector('#btn-export-jsonl').dataset.busy, undefined, 'export download response');
    assert.equal(exportRequests.length, previousExports + 1, 'One export click issues exactly one request');
    await view('settings');
    if (!token) await click('#token-clear');
    assert((await page.eval(() => localStorage.getItem('eduforge.kb.token') || '')) === token, 'Original token restored');
    await layout('settings-desktop'); await shot('08_桌面_系统设置.png');
    await page.setViewport({ width: 390, height: 844, mobile: true });
    await layout('settings-mobile'); await shot('09_手机_系统设置.png');
  });
  await check('browser has no uncaught JavaScript or API errors', async () => {
    assert.deepEqual(page.consoleErrors, []);
    assert.deepEqual(apiErrors, []);
    return { askRequests, javascriptErrors: 0, apiErrors: 0 };
  });
} catch (error) {
  report.errors.push(error.stack || error.message);
  console.error(`FAIL ${error.message}`);
  process.exitCode = 1;
  if (page) {
    try { await shot('检查失败截图.png'); } catch (screenshotError) { report.errors.push(`Failure screenshot: ${screenshotError.message}`); }
  }
} finally {
  if (createdDocumentAttempted && baselineIds) {
    try {
      const candidates = temporaryId ? [{ id: temporaryId }] : (await api('/documents')).documents.filter(d => d.title === temporaryTitle && !baselineIds.has(d.id));
      for (const document of candidates) {
        assert(!baselineIds.has(document.id), 'Never delete a baseline document');
        await api(`/documents/${encodeURIComponent(document.id)}`, { method: 'DELETE' });
      }
      report.cleanup = { removedTemporaryDocuments: candidates.length };
      console.log(`CLEANUP removed ${candidates.length} temporary document(s)`);
    } catch (error) {
      report.errors.push(`Temporary document cleanup: ${error.message}`);
      process.exitCode = 1;
    }
  }
  if (browser) await browser.close();
  report.finishedAt = new Date().toISOString();
  report.passed = !process.exitCode;
  mkdirSync(reportOutput, { recursive: true });
  writeFileSync(join(reportOutput, 'browser-check.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(`${report.passed ? 'PASS' : 'FAIL'} ${report.checks.filter(c => c.passed).length}/${report.checks.length} checks; report: ${join(reportOutput, 'browser-check.json')}`);
}
