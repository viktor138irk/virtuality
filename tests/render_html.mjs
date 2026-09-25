import { createRequire } from 'node:module';
const { chromium } = createRequire(import.meta.url)('playwright');
// Render an HTML file to PNG. Usage: node tests/render_html.mjs in.html out.png [width] [height]
const [,, html, out, width = '1024', height = '768'] = process.argv;
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: +width, height: +height }, deviceScaleFactor: 1 });
await page.goto('file://' + (html.startsWith('/') ? html : process.cwd() + '/' + html));
await page.waitForTimeout(300);
await page.screenshot({ path: out, type: 'png' });
await browser.close();
