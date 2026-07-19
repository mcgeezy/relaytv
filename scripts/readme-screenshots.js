#!/usr/bin/env node

/*
 * Capture and compose the README product images from a running RelayTV server.
 *
 * Example:
 *   node scripts/readme-screenshots.js \
 *     --ws=ws://127.0.0.1:3000/ \
 *     --base=http://127.0.0.1:8787 \
 *     --output=docs/images/readme
 */

const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

function option(name, fallback) {
  const prefix = `--${name}=`;
  const found = process.argv.find((arg) => arg.startsWith(prefix));
  return found ? found.slice(prefix.length) : fallback;
}

function pngData(buffer) {
  return `data:image/png;base64,${buffer.toString('base64')}`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
}

async function newProductPage(browser, viewport, colorScheme = 'dark') {
  const context = await browser.newContext({ viewport, colorScheme, deviceScaleFactor: 1 });
  await context.addInitScript(() => {
    try { localStorage.setItem('relaytv_theme', 'dark'); } catch (_error) {}
  });
  const page = await context.newPage();
  return { context, page };
}

async function captureRemote(browser, baseUrl) {
  const { context, page } = await newProductPage(browser, { width: 430, height: 932 });
  try {
    await page.goto(`${baseUrl}/ui`, { waitUntil: 'domcontentloaded' });
    await page.locator('#nowTopCard').waitFor({ state: 'visible', timeout: 15000 });
    await page.waitForFunction(() => {
      const title = String(document.querySelector('#now')?.textContent || '').trim();
      return title && title !== 'Ready';
    });
    await page.waitForTimeout(800);
    await page.evaluate(() => {
      window.scrollTo(0, 0);
      document.querySelector('#menuBackdrop')?.classList.add('hidden');
      document.querySelector('#settingsBackdrop')?.classList.add('hidden');
    });
    return await page.screenshot({ type: 'png' });
  } finally {
    await context.close();
  }
}

async function captureLibrary(browser, baseUrl) {
  const { context, page } = await newProductPage(browser, { width: 430, height: 932 });
  try {
    await page.goto(`${baseUrl}/ui`, { waitUntil: 'domcontentloaded' });
    await page.locator('#jellyfinOpenBtn').waitFor({ state: 'visible', timeout: 15000 });
    await page.locator('#jellyfinOpenBtn').click();
    await page.locator('[data-jf-tab="tv"]').click();
    await page.waitForFunction(() => document.querySelectorAll('[data-row-id="tv_series"] .jfItem').length > 0);
    const preferred = page.locator('[data-row-id="tv_series"] .jfItem', { hasText: 'House of the Dragon' }).first();
    const series = (await preferred.count()) ? preferred : page.locator('[data-row-id="tv_series"] .jfItem').first();
    await series.click();
    await page.waitForFunction(() => document.querySelectorAll('[data-row-id="tv_episodes"] .jfItem').length > 0);
    await page.waitForFunction(() => {
      const image = document.querySelector('.jfSeriesHeroArt img');
      return !!image && image.complete && image.naturalWidth > 0;
    });
    await page.waitForTimeout(300);
    await page.evaluate(() => window.scrollTo(0, 0));
    return await page.screenshot({ type: 'png' });
  } finally {
    await context.close();
  }
}

async function captureTvRuntime(browser, baseUrl) {
  const { context, page } = await newProductPage(browser, { width: 1600, height: 900 });
  try {
    await page.goto(`${baseUrl}/idle`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1500);
    await page.evaluate(() => {
      document.querySelector('#idleQrWrap')?.classList.add('hidden');
      const location = document.querySelector('#weatherHeroPanel .cardDesc');
      if (location) location.textContent = 'Local weather';
    });
    return await page.screenshot({ type: 'png' });
  } finally {
    await context.close();
  }
}

async function captureBanner(baseUrl) {
  const response = await fetch(`${baseUrl}/assets/banner.png`);
  if (!response.ok) {
    throw new Error(`Banner request failed with HTTP ${response.status}`);
  }
  return Buffer.from(await response.arrayBuffer());
}

function phoneStageHtml(image, label, eyebrow) {
  return `<!doctype html>
  <html><head><meta charset="utf-8"><style>
    *{box-sizing:border-box} html,body{margin:0;width:100%;height:100%;overflow:hidden}
    body{
      display:grid;place-items:center;background:
        radial-gradient(circle at 26% 18%,rgba(56,189,248,.28),transparent 32%),
        radial-gradient(circle at 84% 82%,rgba(71,100,255,.25),transparent 36%),
        linear-gradient(145deg,#071426,#08101f 58%,#050912);
      color:#f4f8ff;font-family:Inter,ui-sans-serif,system-ui,-apple-system,sans-serif;
    }
    body:before{content:"";position:absolute;inset:0;opacity:.16;background-image:
      linear-gradient(rgba(255,255,255,.12) 1px,transparent 1px),
      linear-gradient(90deg,rgba(255,255,255,.12) 1px,transparent 1px);background-size:42px 42px}
    .copy{position:absolute;left:44px;top:42px;z-index:3}
    .eyebrow{color:#65ddff;font-size:15px;font-weight:800;letter-spacing:.15em;text-transform:uppercase}
    .label{margin-top:8px;font-size:29px;font-weight:780;letter-spacing:-.04em}
    .glow{position:absolute;width:520px;height:800px;border-radius:50%;background:rgba(41,142,255,.16);filter:blur(60px)}
    .phone{position:relative;z-index:2;width:454px;padding:12px;border:1px solid rgba(255,255,255,.28);
      border-radius:58px;background:#02050b;box-shadow:0 40px 90px rgba(0,0,0,.58),0 0 0 5px rgba(90,178,255,.10)}
    .screen{display:block;width:430px;height:932px;border-radius:46px;object-fit:cover;object-position:top;background:#071426}
    .island{position:absolute;z-index:4;top:22px;left:50%;width:116px;height:28px;transform:translateX(-50%);
      border-radius:18px;background:#02050b;box-shadow:0 1px 0 rgba(255,255,255,.08)}
  </style></head><body>
    <div class="copy"><div class="eyebrow">${escapeHtml(eyebrow)}</div><div class="label">${escapeHtml(label)}</div></div>
    <div class="glow"></div>
    <div class="phone"><div class="island"></div><img class="screen" src="${pngData(image)}" alt=""></div>
  </body></html>`;
}

async function composePhone(browser, image, outputPath, label, eyebrow) {
  const context = await browser.newContext({ viewport: { width: 760, height: 1160 }, deviceScaleFactor: 1 });
  const page = await context.newPage();
  try {
    await page.setContent(phoneStageHtml(image, label, eyebrow), { waitUntil: 'load' });
    await page.screenshot({ path: outputPath, type: 'png' });
  } finally {
    await context.close();
  }
}

function heroHtml(baseUrl, remote, library, tv) {
  return `<!doctype html>
  <html><head><meta charset="utf-8"><base href="${escapeHtml(baseUrl)}/"><style>
    *{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden}
    body{position:relative;background:
      radial-gradient(circle at 76% 12%,rgba(63,205,255,.22),transparent 28%),
      radial-gradient(circle at 18% 86%,rgba(57,104,246,.24),transparent 35%),
      linear-gradient(145deg,#050a13,#08162a 54%,#07101e);font-family:Inter,ui-sans-serif,system-ui,sans-serif;color:#fff}
    .grid{position:absolute;inset:0;opacity:.13;background-image:
      linear-gradient(rgba(255,255,255,.14) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.14) 1px,transparent 1px);background-size:54px 54px}
    .brand{position:absolute;z-index:6;left:64px;top:42px;width:330px;filter:drop-shadow(0 12px 24px rgba(0,0,0,.35))}
    .tagline{position:absolute;z-index:6;left:70px;bottom:42px;font-weight:720;font-size:25px;letter-spacing:-.025em}
    .pills{display:flex;gap:10px;margin-top:13px}.pill{padding:8px 12px;border:1px solid rgba(131,213,255,.24);border-radius:999px;
      background:rgba(8,22,43,.72);color:#c9efff;font-size:12px;font-weight:760;letter-spacing:.08em;text-transform:uppercase}
    .tv{position:absolute;z-index:2;left:64px;top:132px;width:1040px;padding:13px;border:1px solid rgba(164,205,255,.24);border-radius:30px;
      background:#02050a;box-shadow:0 46px 100px rgba(0,0,0,.56),0 0 70px rgba(33,153,255,.12)}
    .tv img{display:block;width:100%;height:575px;object-fit:cover;border-radius:18px}
    .tv:after{content:"";position:absolute;left:50%;bottom:-28px;width:220px;height:20px;transform:translateX(-50%);border-radius:50%;background:rgba(0,0,0,.6);filter:blur(10px)}
    .phone{position:absolute;z-index:5;width:276px;padding:8px;border:1px solid rgba(255,255,255,.28);border-radius:39px;background:#02050a;
      box-shadow:0 38px 80px rgba(0,0,0,.62),0 0 0 4px rgba(97,196,255,.09)}
    .phone img{display:block;width:258px;height:559px;object-fit:cover;object-position:top;border-radius:31px}
    .phone:before{content:"";position:absolute;z-index:2;top:14px;left:50%;width:76px;height:18px;transform:translateX(-50%);border-radius:12px;background:#02050a}
    .remote{right:276px;top:74px;transform:rotate(-4deg)}.library{right:42px;top:166px;transform:rotate(4deg)}
    .flare{position:absolute;z-index:1;right:20px;top:80px;width:530px;height:650px;border-radius:50%;background:rgba(39,155,255,.18);filter:blur(72px)}
  </style></head><body>
    <div class="grid"></div><div class="flare"></div>
    <img class="brand" src="assets/banner.png" alt="RelayTV">
    <div class="tv"><img src="${pngData(tv)}" alt=""></div>
    <div class="phone remote"><img src="${pngData(remote)}" alt=""></div>
    <div class="phone library"><img src="${pngData(library)}" alt=""></div>
    <div class="tagline">One local endpoint. Every way you watch.
      <div class="pills"><span class="pill">Send</span><span class="pill">Browse</span><span class="pill">Automate</span></div>
    </div>
  </body></html>`;
}

async function composeHero(browser, baseUrl, remote, library, tv, outputPath) {
  const context = await browser.newContext({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1 });
  const page = await context.newPage();
  try {
    await page.setContent(heroHtml(baseUrl, remote, library, tv), { waitUntil: 'networkidle' });
    await page.locator('.brand').evaluate((image) => image.decode());
    await page.screenshot({ path: outputPath, type: 'png' });
  } finally {
    await context.close();
  }
}

async function main() {
  const wsEndpoint = option('ws', 'ws://10.55.55.98:3000/');
  const baseUrl = option('base', 'http://10.55.55.2:8787').replace(/\/$/, '');
  const outputDir = path.resolve(option('output', 'docs/images/readme'));
  fs.mkdirSync(outputDir, { recursive: true });

  const browser = await chromium.connect(wsEndpoint);
  try {
    const remote = await captureRemote(browser, baseUrl);
    const library = await captureLibrary(browser, baseUrl);
    const tv = await captureTvRuntime(browser, baseUrl);
    const banner = await captureBanner(baseUrl);

    await composePhone(browser, remote, path.join(outputDir, 'remote-phone.png'), 'Remote and queue', 'Control');
    await composePhone(browser, library, path.join(outputDir, 'library-phone.png'), 'Series and seasons', 'Browse');
    await composeHero(browser, baseUrl, remote, library, tv, path.join(outputDir, 'hero.png'));
    fs.writeFileSync(path.join(outputDir, 'tv-runtime.png'), tv);
    fs.writeFileSync(path.join(outputDir, 'relaytv-banner.png'), banner);

    process.stdout.write(`${JSON.stringify({
      ok: true,
      wsEndpoint,
      baseUrl,
      outputDir,
      files: ['hero.png', 'remote-phone.png', 'library-phone.png', 'tv-runtime.png', 'relaytv-banner.png'],
    }, null, 2)}\n`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  process.stderr.write(`README screenshot generation failed: ${error.stack || error}\n`);
  process.exit(1);
});
