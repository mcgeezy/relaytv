#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');

function option(name, fallback) {
  const prefix = `--${name}=`;
  const value = process.argv.find((item) => item.startsWith(prefix));
  return value ? value.slice(prefix.length) : fallback;
}

const VIEWPORTS = [
  {label:'720p', width:1280, height:720, expectedScale:0.75},
  {label:'1080p', width:1920, height:1080, expectedScale:1.0},
  {label:'4k', width:3840, height:2160, expectedScale:2.0},
];

const FIXTURE_SETTINGS = {
  device_name:'Living Room TV',
  idle_dashboard_enabled:true,
  idle_qr_enabled:true,
  idle_qr_size:168,
  idle_panels:{
    weather:{enabled:true, layout:'split'},
  },
  weather:{
    location_name:'Austin, TX',
    latitude:30.2672,
    longitude:-97.7431,
    units:'imperial',
    forecast_days:7,
  },
};

const FIXTURE_WEATHER = {
  current:{
    temperature_2m:72.4,
    apparent_temperature:71.1,
    wind_speed_10m:8.6,
    weather_code:1,
    is_day:1,
  },
  daily:{
    time:[
      '2026-11-22',
      '2026-11-23',
      '2026-11-24',
      '2026-11-25',
      '2026-11-26',
      '2026-11-27',
      '2026-11-28',
    ],
    weather_code:[1, 2, 0, 3, 61, 1, 0],
    temperature_2m_max:[75, 73, 76, 70, 66, 71, 74],
    temperature_2m_min:[56, 54, 55, 52, 49, 51, 53],
    precipitation_probability_max:[10, 15, 5, 25, 80, 10, 5],
    wind_speed_10m_max:[11, 12, 9, 14, 18, 10, 8],
  },
};

async function installTvFixtures(page) {
  await page.route('**/x11/host_urls', async (route) => {
    await route.fulfill({
      status:200,
      json:{
        urls:['http://192.168.1.50:8787'],
        public_urls:['http://192.168.1.50:8787'],
      },
    });
  });
  await page.route('**/settings', async (route) => {
    await route.fulfill({status:200, json:FIXTURE_SETTINGS});
  });
  await page.route('**/status', async (route) => {
    await route.fulfill({
      status:200,
      json:{
        device_name:'Living Room TV',
        state:'idle',
        playing:false,
        paused:false,
        idle_dashboard_enabled:true,
        now_playing:null,
      },
    });
  });
  await page.route('**/playback/state', async (route) => {
    await route.fulfill({
      status:200,
      json:{
        state:'idle',
        playing:false,
        paused:false,
        idle_dashboard_enabled:true,
      },
    });
  });
  await page.route('https://api.open-meteo.com/v1/forecast**', async (route) => {
    await route.fulfill({status:200, json:FIXTURE_WEATHER});
  });
}

async function checkIdleViewport(browser, baseUrl, vp) {
  const context = await browser.newContext({
    viewport:{width:vp.width, height:vp.height},
    colorScheme:'dark',
    deviceScaleFactor:1,
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (err) => errors.push(`idle(${vp.label}) pageerror: ${err.message}`));
  try {
    await installTvFixtures(page);
    await page.goto(`${baseUrl}/idle`, {waitUntil:'domcontentloaded'});
    await page.waitForFunction(() => {
      const clock = document.getElementById('clock');
      const qr = document.getElementById('idleQrWrap');
      const days = document.querySelectorAll('.weatherDay');
      return (
        clock &&
        clock.textContent.trim() !== '--:--' &&
        qr &&
        !qr.classList.contains('hidden') &&
        days.length === 7
      );
    }, {timeout:10000});

    const metrics = await page.evaluate(() => {
      const rootStyle = getComputedStyle(document.documentElement);
      const clock = document.getElementById('clock');
      const date = document.getElementById('date');
      const temp = document.querySelector('.weatherTemp');
      const location = document.querySelector('#weatherHeroPanel .cardDesc');
      const qrWrap = document.getElementById('idleQrWrap');
      const qrImg = document.getElementById('idleQrImg');
      const qrLabel = document.getElementById('idleQrLabel');
      const footerStatus = document.getElementById('now');
      const qrRect = qrWrap.getBoundingClientRect();
      const statusRect = footerStatus.getBoundingClientRect();
      const qrOverlap = !(
        statusRect.right <= qrRect.left ||
        statusRect.left >= qrRect.right ||
        statusRect.bottom <= qrRect.top ||
        statusRect.top >= qrRect.bottom
      );
      return {
        viewport:{width:window.innerWidth, height:window.innerHeight},
        scroll:{
          width:document.documentElement.scrollWidth,
          height:document.documentElement.scrollHeight,
        },
        horizontalOverflow:document.documentElement.scrollWidth > document.documentElement.clientWidth,
        verticalOverflow:document.documentElement.scrollHeight > document.documentElement.clientHeight,
        tokens:{
          background:rootStyle.getPropertyValue('--background').trim(),
          surface:rootStyle.getPropertyValue('--surface').trim(),
          textPrimary:rootStyle.getPropertyValue('--text-primary').trim(),
          accent:rootStyle.getPropertyValue('--accent').trim(),
        },
        clock:{
          text:clock.textContent.trim(),
          fontSizePx:Math.round(parseFloat(getComputedStyle(clock).fontSize)),
        },
        date:{
          text:date.textContent.trim(),
          fontSizePx:Math.round(parseFloat(getComputedStyle(date).fontSize)),
        },
        weather:{
          temp:temp ? temp.textContent.trim() : '',
          location:location ? location.textContent.trim() : '',
          forecastDays:document.querySelectorAll('.weatherDay').length,
        },
        qr:{
          visible:!qrWrap.classList.contains('hidden') && qrRect.width > 0 && qrRect.height > 0,
          hasQrClass:document.body.classList.contains('hasQr'),
          src:qrImg.getAttribute('src') || '',
          label:qrLabel.textContent.trim(),
          widthPx:Math.round(qrRect.width),
          heightPx:Math.round(qrRect.height),
          overlapsFooterStatus:qrOverlap,
        },
      };
    });

    if (
      metrics.horizontalOverflow ||
      metrics.verticalOverflow ||
      metrics.tokens.background !== '#0b1017' ||
      metrics.tokens.textPrimary !== '#f3f6fa' ||
      metrics.tokens.accent !== '#60a5fa' ||
      metrics.clock.fontSizePx < 48 ||
      metrics.weather.temp !== '72°F' ||
      metrics.weather.location !== 'Austin, TX' ||
      metrics.weather.forecastDays !== 7 ||
      !metrics.qr.visible ||
      !metrics.qr.hasQrClass ||
      !metrics.qr.src.startsWith('/qr/connect.svg') ||
      metrics.qr.label !== '192.168.1.50:8787' ||
      metrics.qr.overlapsFooterStatus
    ) {
      throw new Error(`Idle verification failed at ${vp.label}: ${JSON.stringify(metrics)}`);
    }
    return {metrics, errors};
  } finally {
    await context.close();
  }
}

async function checkOverlayViewport(browser, baseUrl, vp) {
  const context = await browser.newContext({
    viewport:{width:vp.width, height:vp.height},
    colorScheme:'dark',
    deviceScaleFactor:1,
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (err) => errors.push(`overlay(${vp.label}) pageerror: ${err.message}`));
  try {
    await installTvFixtures(page);
    await page.goto(`${baseUrl}/x11/overlay`, {waitUntil:'domcontentloaded'});
    await page.waitForFunction(() => document.body.classList.contains('idle'), {timeout:10000});

    const metrics = await page.evaluate(() => {
      addToast({
        text:'Now Playing: RelayTV 4K Demo',
        level:'success',
        icon:'play',
        position:'top-right',
        link_url:'https://relaytv.example.test/details',
        link_text:'View stream details',
        duration:15,
      });
      addToast({
        text:'Rejected script link',
        level:'warn',
        position:'top-right',
        link_url:'javascript:alert(1)',
        link_text:'Should not render',
        duration:15,
      });
      addToast({
        text:'Rejected protocol-relative link',
        level:'error',
        position:'top-right',
        link_url:'//evil.example.test/phish',
        link_text:'Should not render either',
        duration:15,
      });

      const rootStyle = getComputedStyle(document.documentElement);
      const bodyStyle = getComputedStyle(document.body);
      const stageStyle = getComputedStyle(document.querySelector('.stage'));
      const toastsRoot = document.getElementById('toasts');
      const toasts = [...toastsRoot.querySelectorAll('.toast')];
      const safeToast = toasts.find((node) => node.textContent.includes('RelayTV 4K Demo'));
      const jsLinkToast = toasts.find((node) => node.textContent.includes('Rejected script link'));
      const protoRelToast = toasts.find((node) => node.textContent.includes('Rejected protocol-relative link'));
      const safeAnchor = safeToast ? safeToast.querySelector('a') : null;
      const safeRect = safeToast ? safeToast.getBoundingClientRect() : null;

      return {
        viewport:{width:window.innerWidth, height:window.innerHeight},
        transparentBackground:bodyStyle.backgroundColor === 'rgba(0, 0, 0, 0)',
        bodyPointerEvents:bodyStyle.pointerEvents,
        stagePointerEvents:stageStyle.pointerEvents,
        idleActive:document.body.classList.contains('idle'),
        overlayScale:Number(rootStyle.getPropertyValue('--overlay-scale').trim()),
        toastWidthVar:rootStyle.getPropertyValue('--toast-width').trim(),
        positionClass:toastsRoot.className,
        toastCount:toasts.length,
        safeToastWidthPx:safeRect ? Math.round(safeRect.width) : 0,
        safeLinkHref:safeAnchor ? safeAnchor.getAttribute('href') : null,
        unsafeJsLinkRendered:Boolean(jsLinkToast && jsLinkToast.querySelector('a')),
        unsafeProtoRelLinkRendered:Boolean(protoRelToast && protoRelToast.querySelector('a')),
      };
    });

    if (
      !metrics.transparentBackground ||
      metrics.bodyPointerEvents !== 'none' ||
      metrics.stagePointerEvents !== 'none' ||
      !metrics.idleActive ||
      Math.abs(metrics.overlayScale - vp.expectedScale) > 0.01 ||
      metrics.positionClass !== 'toasts top-right' ||
      metrics.toastCount !== 3 ||
      metrics.safeToastWidthPx <= 0 ||
      metrics.safeLinkHref !== 'https://relaytv.example.test/details' ||
      metrics.unsafeJsLinkRendered ||
      metrics.unsafeProtoRelLinkRendered
    ) {
      throw new Error(`Overlay verification failed at ${vp.label}: ${JSON.stringify(metrics)}`);
    }
    return {metrics, errors};
  } finally {
    await context.close();
  }
}

async function main() {
  const rootDirectory = path.resolve(__dirname, '..');
  const baseUrl = option('base', 'http://127.0.0.1:8793').replace(/\/$/, '');
  const reportPath = path.resolve(
    option('report', path.join(rootDirectory, 'docs', 'ui-modernization', 'phase-6-tv.json'))
  );
  fs.mkdirSync(path.dirname(reportPath), {recursive:true});

  const browser = await chromium.launch({headless:true});
  const report = {
    schema:1,
    capturedAt:new Date().toISOString(),
    baseUrl,
    runner:{
      playwright:require('playwright/package.json').version,
      browser:'chromium',
      browserVersion:browser.version(),
      platform:`${process.platform}/${process.arch}`,
    },
    idle:{},
    overlay:{},
    errors:[],
  };

  try {
    for (const vp of VIEWPORTS) {
      const idleRes = await checkIdleViewport(browser, baseUrl, vp);
      report.idle[vp.label] = idleRes.metrics;
      report.errors.push(...idleRes.errors);

      const overlayRes = await checkOverlayViewport(browser, baseUrl, vp);
      report.overlay[vp.label] = overlayRes.metrics;
      report.errors.push(...overlayRes.errors);
    }
  } finally {
    await browser.close();
  }

  if (report.errors.length) {
    throw new Error(report.errors.join('\n'));
  }

  fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`);
  process.stdout.write(`${JSON.stringify({ok:true, reportPath, viewports:VIEWPORTS.map((v) => v.label)}, null, 2)}\n`);
}

if (require.main === module) {
  main().catch((error) => {
    process.stderr.write(`UI phase 6 TV check failed: ${error.stack || error}\n`);
    process.exitCode = 1;
  });
}
