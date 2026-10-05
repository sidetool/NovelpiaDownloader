const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const root = path.resolve(__dirname, '..');
  const envFile = process.env.TEST_ENV_FILE || path.join(root, '.env');
  const env = Object.fromEntries(fs.readFileSync(envFile, 'utf8').split('\n').filter(line => line && !line.startsWith('#'))
    .map(line => { const i = line.indexOf('='); return [line.slice(0, i), line.slice(i + 1)]; }));
  const base = process.env.BASE_URL || 'http://127.0.0.1:8797';
  const options = { headless: true };
  if (process.env.BROWSER_EXECUTABLE) options.executablePath = process.env.BROWSER_EXECUTABLE;
  if (process.env.CONNECT_ADDRESS) options.args = [`--host-resolver-rules=MAP ${new URL(base).hostname} ${process.env.CONNECT_ADDRESS}`];
  const browser = await chromium.launch(options);
  try {
    const context = await browser.newContext({httpCredentials: { username: env.WEB_USERNAME, password: env.WEB_PASSWORD }, viewport: {width:1440,height:1100}});
    const page = await context.newPage();
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.goto(base, { waitUntil: 'networkidle' });
    await page.locator('#connection').filter({ hasText: '연결됨' }).waitFor({ timeout:20000 });
    if (await page.locator('canvas, iframe').count()) throw new Error('Native UI must not use remote screen elements');
    await page.locator('#job-form').waitFor({state:'visible'});
    await page.getByText('다운로드 옵션', {exact:true}).click();
    await page.locator('#from-enabled').check();
    if (await page.locator('[name="from"]').isDisabled()) throw new Error('Range input is disabled');
    await page.locator('#from-enabled').uncheck();
    if (!await page.locator('[name="from"]').isDisabled()) throw new Error('Range input is enabled');
    await page.locator('#queue-tab').click();
    await page.locator('#queue-panel').waitFor({state:'visible'});
    await page.locator('#files-tab').click();
    await page.locator('#files-panel').waitFor({state:'visible'});
    await page.locator('#refresh').click();
    await page.locator('#download-tab').click();
    if (process.env.SCREENSHOT_PREFIX) await page.screenshot({path:process.env.SCREENSHOT_PREFIX+'-desktop.png',fullPage:true});
    await page.setViewportSize({width:390,height:844});
    if (await page.evaluate(() => document.documentElement.scrollWidth > 390)) throw new Error('Mobile page overflows');
    if (process.env.SCREENSHOT_PREFIX) await page.screenshot({path:process.env.SCREENSHOT_PREFIX+'-mobile.png',fullPage:true});
    if(errors.length) throw new Error(errors.join('\n'));
    console.log('PASS: native forms, range controls, live state, queue/files panels and mobile layout');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
