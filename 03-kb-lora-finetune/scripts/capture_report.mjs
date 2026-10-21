// Capture the actual local report. Node 22+ and an installed Chrome/Edge are required.
import { homedir } from 'node:os';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const skill = process.env.TASTE_SKILL_DIR || join(homedir(), '.agents', 'skills', 'design-taste-frontend');
const { Browser } = await import(pathToFileURL(join(skill, 'scripts/lib/cdp.mjs')).href);
const url = process.argv[2] || 'http://127.0.0.1:8003/reports/training-report.html';
if (!['127.0.0.1', 'localhost', '[::1]'].includes(new URL(url).hostname)) throw new Error('Use a local report URL');
const output = root;
const checkDirectory = join(root, 'screenshots');
mkdirSync(checkDirectory, { recursive: true });
const browser = await Browser.launch();
const checks = [];
try {
  for (const width of [1440, 390]) {
    const page = await browser.newPage({ width, height: 1000 });
    await page.goto(url);
    const result = await page.eval(async () => {
      await document.fonts.ready;
      await Promise.all([...document.images].map(img => img.decode()));
      return { title: document.title, width: innerWidth, scrollWidth: document.documentElement.scrollWidth, imagesLoaded: [...document.images].every(img => img.naturalWidth > 0) };
    });
    if (result.scrollWidth > result.width || !result.imagesLoaded || !result.title.includes('LoRA')) throw new Error(JSON.stringify(result));
    const filename = width === 1440 ? '模型微调_桌面测试截图.png' : '模型微调_手机测试截图.png';
    await page.screenshot({ path: join(output, filename), format: 'png', fullPage: true });
    checks.push({ ...result, screenshot: `../${filename}` });
    await page.close();
  }
  writeFileSync(join(checkDirectory, 'report-check.json'), JSON.stringify({ createdAt: new Date().toISOString(), url, passed: true, checks }, null, 2) + '\n', 'utf8');
  console.log(JSON.stringify({ passed: true, checks }));
} finally {
  await browser.close();
}
