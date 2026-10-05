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
    if (!await page.locator('[name="optimizeImages"]').count()) throw new Error('Missing image optimization option');
    await page.locator('[name="format"][value="txt"]').check();
    if (!await page.locator('[name="optimizeImages"]').isDisabled()) throw new Error('Optimization must apply only to EPUB');
    await page.locator('[name="format"][value="epub"]').check();
    if (await page.locator('[name="optimizeImages"]').isDisabled()) throw new Error('EPUB optimization option is disabled');
    await page.locator('#from-enabled').check();
    if (await page.locator('[name="from"]').isDisabled()) throw new Error('Range input is disabled');
    await page.locator('#from-enabled').uncheck();
    if (!await page.locator('[name="from"]').isDisabled()) throw new Error('Range input is enabled');
    await page.locator('#queue-tab').click();
    await page.locator('#queue-panel').waitFor({state:'visible'});
    await page.locator('#files-tab').click();
    await page.locator('#files-panel').waitFor({state:'visible'});
    await page.locator('#refresh').click();
    if (process.env.OPTIMIZATION_TEST === '1') {
      const name = process.env.OPTIMIZATION_FIXTURE;
      if (!name?.startsWith('UI-') || !process.env.TEST_ENV_FILE || !process.env.BASE_URL) throw new Error('Optimization tests require disposable fixtures');
      const optimize = page.getByRole('button', {name:name + ' 이미지 최적화', exact:true});
      await optimize.waitFor();
      await page.setViewportSize({width:390,height:844});
      if (await page.evaluate(() => document.documentElement.scrollWidth > 390)) throw new Error('Mobile optimize/file actions overflow');
      const response = page.waitForResponse(response => response.url().endsWith('/api/files/optimize') && response.request().method() === 'POST');
      await optimize.click();
      if ((await response).status() !== 200) throw new Error('UI optimization request failed');
      await page.locator('#optimization-message').filter({hasText:'→'}).waitFor({timeout:30000});
      const optimizedName = name.replace(/\.epub$/, ' [최적화].epub');
      await page.locator('#files li strong').filter({hasText:optimizedName}).waitFor({timeout:20000});
      let listing = await (await context.request.get(base + '/api/files')).json();
      const original = listing.find(file => file.name === name);
      const copy = listing.find(file => file.name === optimizedName);
      if (!original || !copy || copy.size >= original.size) throw new Error('Original and smaller copy must appear together');
      if (!await page.locator('#optimization-status').isVisible()) throw new Error('Missing optimization result display');
      await page.locator('#download-tab').click();
      await page.locator('[name="optimizeImages"]').check();
      const saved = page.waitForResponse(response => response.url().endsWith('/api/settings') && response.request().method() === 'POST');
      await page.locator('#save-settings').click();
      if ((await saved).status() !== 200) throw new Error('Optimization option save failed');
      await page.reload({waitUntil:'networkidle'});
      await page.locator('#connection').filter({hasText:'연결됨'}).waitFor();
      if (!await page.locator('[name="optimizeImages"]').isChecked()) throw new Error('Saved optimization option did not reload');
      console.log('PASS: existing-file optimization button, result/size display, original preservation, saved option and mobile actions');
      await page.setViewportSize({width:1440,height:1100});
    }
    if (process.env.FILE_DELETE_TEST === '1') {
      const prefix = process.env.FILE_FIXTURE_PREFIX;
      if (!prefix || !process.env.TEST_ENV_FILE || !process.env.BASE_URL) throw new Error('Deletion tests require a disposable fixture service');
      await page.waitForFunction(() => document.querySelectorAll('#files li').length === 3 && !document.querySelector('#files-clear').disabled);
      const names = await page.locator('#files li strong').allTextContents();
      if (!names.every(name => name.startsWith(prefix))) throw new Error('Refusing to delete files outside the test fixtures');
      await page.setViewportSize({width:390,height:844});
      if (await page.evaluate(() => document.documentElement.scrollWidth > 390)) throw new Error('Mobile file list overflows');

      const singleName = names.find(name => name.endsWith('.epub'));
      const single = page.getByRole('button', {name:singleName + ' 삭제', exact:true});
      page.once('dialog', dialog => dialog.dismiss());
      await single.click();
      let listing = await (await context.request.get(base + '/api/files')).json();
      if (!listing.some(file => file.name === singleName)) throw new Error('Cancel must keep the file');
      page.once('dialog', dialog => dialog.accept());
      const deleted = page.waitForResponse(response => response.url().endsWith('/api/files/delete') && response.request().method() === 'POST');
      await single.click();
      if ((await deleted).status() !== 200) throw new Error('Single deletion failed');
      await page.waitForFunction(() => document.querySelectorAll('#files li').length === 2);

      const snapshot = await (await context.request.get(base + '/api/state')).json();
      await page.route('**/api/state', route => route.fulfill({json:{...snapshot, running:true}}));
      await page.waitForFunction(() => document.querySelector('#file-delete-hint').textContent.includes('끝나면'));
      if (!await page.locator('#files-clear').isDisabled() || !await page.locator('[data-file-delete]').first().isDisabled()) throw new Error('Running downloads must disable file deletion');
      await page.unroute('**/api/state');
      await page.waitForFunction(() => !document.querySelector('#files-clear').disabled);

      page.once('dialog', dialog => dialog.dismiss());
      await page.locator('#files-clear').click();
      listing = await (await context.request.get(base + '/api/files')).json();
      if (listing.length !== 2) throw new Error('Cancel must keep all remaining files');
      page.once('dialog', dialog => dialog.accept());
      const cleared = page.waitForResponse(response => response.url().endsWith('/api/files/clear') && response.request().method() === 'POST');
      await page.locator('#files-clear').click();
      if ((await cleared).status() !== 200) throw new Error('All deletion failed');
      await page.waitForFunction(() => document.querySelectorAll('#files li').length === 0 && document.querySelector('#files-clear').disabled);
      if (!await page.locator('#files-status').isVisible()) throw new Error('Missing empty-state message');
      console.log('PASS: delete confirmation/cancel, single/all removal, running guard and mobile file layout');
      await page.setViewportSize({width:1440,height:1100});
    }
    await page.locator('#download-tab').click();
    if (process.env.SCREENSHOT_PREFIX) await page.screenshot({path:process.env.SCREENSHOT_PREFIX+'-desktop.png',fullPage:true});
    await page.setViewportSize({width:390,height:844});
    if (await page.evaluate(() => document.documentElement.scrollWidth > 390)) throw new Error('Mobile page overflows');
    if (process.env.SCREENSHOT_PREFIX) await page.screenshot({path:process.env.SCREENSHOT_PREFIX+'-mobile.png',fullPage:true});
    if(errors.length) throw new Error(errors.join('\n'));
    console.log('PASS: native forms, range controls, live state, queue/files panels and mobile layout');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
