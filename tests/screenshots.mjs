// Screenshots of every panel page for design review.
// Usage: node tests/screenshots.mjs [baseUrl] [outDir] [theme]
import { createRequire } from 'node:module';
const { chromium } = createRequire(import.meta.url)('playwright'); // NODE_PATH=$(npm root -g) for a global install
import { mkdirSync } from 'node:fs';

const base = process.argv[2] || 'http://127.0.0.1:8765';
const out = process.argv[3] || 'screenshots';
const theme = process.argv[4] || '';
const pages = [
  ['login', '/login'], ['dashboard', '/'], ['vm', '/vm/web01'], ['create', '/vm/create'], ['help', '/help'], ['operation', '/operations'],
  ['iso', '/iso'], ['disks', '/disk-images'], ['network', '/network'], ['operations', '/operations'],
  ['logs', '/logs'], ['update', '/update'], ['host', '/host'], ['console', '/vm/web01/console'],
];
mkdirSync(out, { recursive: true });
const browser = await chromium.launch();
for (const [width, suffix] of [[1440, ''], [390, '-mobile']]) {
  const context = await browser.newContext({ viewport: { width, height: 900 }, deviceScaleFactor: 1 });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (err) => errors.push(err.message));
  if (theme) await page.addInitScript((t) => localStorage.setItem('virtualityTheme', t), theme);
  await page.goto(base + '/login');
  if (suffix === '') await page.screenshot({ path: `${out}/login${suffix}.png`, fullPage: true });
  await page.fill('input[name=password]', 'x');
  await page.click('button[type=submit]');
  await page.waitForURL(base + '/');
  for (const [name, path] of pages.slice(1)) {
    if (suffix && !['dashboard', 'vm', 'create'].includes(name)) continue;
    await page.goto(base + path);
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${out}/${name}${suffix}.png`, fullPage: true });
  }
  if (errors.length) console.log('JS errors:', errors.join(' | '));
  await context.close();
}
await browser.close();
console.log('saved to', out);
