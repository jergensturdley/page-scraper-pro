// ─── Deep Video Native Helper Bridge ───────────────────────────────────────────

// Load the native client module synchronously at the top of the service worker.
importScripts('native-client.js');

var _nativeClient = null;

function getNativeClient() {
  if (!_nativeClient) {
    _nativeClient = new PageScraperNative.NativeHostClient({
      runtime: chrome.runtime,
      hostName: 'com.pagescraperpro.ytdlp',
      timeoutMs: 60000,
    });
  }
  return _nativeClient;
}

/**
 * Forward a validated deep video command from the popup.
 * Only the six allowlisted command types are accepted.
 */
chrome.runtime.onMessage.addListener(function(message, sender, sendResponse) {
  if (message && message.action === 'deepVideoCommand') {
    var cmd = message.command;
    var payload = message.payload || {};
    if (!PageScraperNative.ALLOWED_COMMANDS ||
        !PageScraperNative.ALLOWED_COMMANDS.includes(cmd)) {
      sendResponse({ error: 'unknown_command' });
      return;
    }
    getNativeClient().send(cmd, payload).then(function(result) {
      sendResponse({ ok: true, data: result });
    }).catch(function(err) {
      sendResponse({ ok: false, error: err.message || String(err) });
    });
    return true; // async sendResponse
  }

  if (message && message.action === 'deepVideoStatus') {
    var snap = getNativeClient().snapshot();
    sendResponse({ ok: true, snapshot: snap });
    return false;
  }
});

// Broadcast snapshot updates to all popup instances.
function broadcastDeepVideoEvent(event) {
  chrome.runtime.sendMessage({ action: 'deepVideoEvent', event }).catch(function() {
    // Silently ignore "receiving end does not exist" when popup is closed.
  });
}

getNativeClient().subscribe(broadcastDeepVideoEvent);

// Background script for Page Scraper Pro

// Track active downloads
const activeDownloads = new Map();

const WORKSPACE_CACHE_VERSION = 1;
const WORKSPACE_CACHE_PREFIX = `scrape-session:v${WORKSPACE_CACHE_VERSION}:tab:`;

chrome.tabs.onRemoved.addListener(tabId => {
  if (!Number.isInteger(tabId) || tabId <= 0 || !chrome.storage || !chrome.storage.session) return;
  try {
    const removal = chrome.storage.session.remove(`${WORKSPACE_CACHE_PREFIX}${tabId}`);
    if (removal && typeof removal.catch === 'function') {
      removal.catch(error => console.warn('Failed to remove closed tab workspace:', error));
    }
  } catch (error) {
    console.warn('Failed to remove closed tab workspace:', error);
  }
});

// Create context menu on installation
chrome.runtime.onInstalled.addListener(() => {
  // Main context menu
  chrome.contextMenus.create({
    id: 'scraper-parent',
    title: 'Page Scraper Pro',
    contexts: ['page', 'selection']
  });
  
  // Scrape images
  chrome.contextMenus.create({
    id: 'scrape-images',
    parentId: 'scraper-parent',
    title: 'Scrape Images',
    contexts: ['page']
  });
  
  // Scrape links
  chrome.contextMenus.create({
    id: 'scrape-links',
    parentId: 'scraper-parent',
    title: 'Scrape Links',
    contexts: ['page']
  });
  
  // Scrape selection
  chrome.contextMenus.create({
    id: 'scrape-selection',
    parentId: 'scraper-parent',
    title: 'Scrape Selection',
    contexts: ['selection']
  });
});

// Handle context menu clicks
chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  switch (info.menuItemId) {
    case 'scrape-images':
      await scrapeAndShow(tab, 'images');
      break;
    case 'scrape-links':
      await scrapeAndShow(tab, 'links');
      break;
    case 'scrape-selection':
      if (info.selectionText) {
        try {
          await copyTextInTab(tab.id, info.selectionText);
          showNotification('Selection copied to clipboard!');
        } catch (error) {
          console.error('Clipboard error:', error);
          showNotification('Could not copy to clipboard: ' + error.message);
        }
      }
      break;
  }
});

async function copyTextInTab(tabId, text) {
  if (!Number.isInteger(tabId) || tabId <= 0) {
    throw new Error('No accessible tab is available for the clipboard write');
  }

  const results = await chrome.scripting.executeScript({
    target: { tabId },
    func: async (value) => {
      try {
        await navigator.clipboard.writeText(value);
        return { success: true };
      } catch (error) {
        return {
          success: false,
          error: error && error.message ? error.message : 'Clipboard write failed'
        };
      }
    },
    args: [String(text ?? '')]
  });
  const result = results && results[0] && results[0].result;
  if (!result || result.success !== true) {
    throw new Error(result && result.error ? result.error : 'The page did not confirm the clipboard write');
  }
  return result;
}

// Scrape and display results
async function scrapeAndShow(tab, type) {
  try {
    if (!tab || !Number.isInteger(tab.id) || tab.id <= 0) {
      throw new Error('No accessible tab is available for scraping');
    }
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => {
        const images = [];
        const links = [];
        
        document.querySelectorAll('img[src]').forEach(img => {
          if (!images.some(i => i.src === img.src)) {
            images.push({
              src: img.src,
              alt: img.alt || '',
              width: img.naturalWidth || 0,
              height: img.naturalHeight || 0
            });
          }
        });
        
        document.querySelectorAll('a[href]').forEach(link => {
          if (!links.some(l => l.href === link.href)) {
            links.push({
              href: link.href,
              text: link.innerText.trim().substring(0, 100)
            });
          }
        });
        
        return { images, links };
      }
    });
    
    const data = results && results[0] && results[0].result;
    if (!data || !Array.isArray(data.images) || !Array.isArray(data.links)) {
      throw new Error('The page did not return scrape results');
    }
    const content = type === 'images'
      ? data.images.map(img => img.src).join('\n')
      : data.links.map(link => link.href).join('\n');

    // Confirm the page completed the clipboard write before reporting success.
    await copyTextInTab(tab.id, content);
    showNotification(`${type === 'images' ? 'Images' : 'Links'} copied to clipboard!`);
  } catch (error) {
    console.error('Scraping error:', error);
    showNotification(`Could not scrape or copy page data: ${error.message || 'Unknown error'}`);
  }
}

// Show notification
function showNotification(message) {
  chrome.notifications.create({
    type: 'basic',
    iconUrl: 'icons/icon48.png',
    title: 'Page Scraper Pro',
    message: message,
    silent: true
  });
}

// Listen for download events
chrome.downloads.onChanged.addListener((delta) => {
  console.log('Download state change:', delta);
  if (delta.state?.current === 'complete') {
    console.log('Download completed:', delta.id);
    showNotification('Download completed');
  } else if (delta.state?.current === 'interrupted') {
    console.error('Download interrupted:', delta.error?.current);
    showNotification(`Failed: ${delta.error?.current || 'Unknown error'}`);
  } else if (delta.state?.current === 'in_progress') {
    console.log('Download in progress:', delta.id);
  }
});

function downloadFile(item, index) {
  if (!item?.url) {
    return Promise.resolve({ success: false, error: 'Missing download URL' });
  }

  return new Promise((resolve) => {
    let filename = item.filename;
    if (!filename) {
      try {
        filename = decodeURIComponent(new URL(item.url).pathname.split('/').pop());
      } catch {}
    }
    filename = (filename || `image-${index + 1}.jpg`)
      .replace(/[^a-z0-9._-]/gi, '_')
      .slice(0, 180);
    if (!/\.[a-z0-9]{2,5}$/i.test(filename)) {
      let extension = '';
      try {
        extension = new URL(item.url).pathname.match(/\.[a-z0-9]{2,5}$/i)?.[0] || '';
      } catch {}
      filename += extension || item.defaultExtension || '';
    }

    try {
      chrome.downloads.download({ url: item.url, filename, saveAs: false }, (id) => {
        const error = chrome.runtime.lastError?.message;
        resolve(error ? { success: false, error } : { success: true, id });
      });
    } catch (error) {
      resolve({ success: false, error: error.message });
    }
  });
}

async function downloadFiles(items) {
  const results = [];
  for (let i = 0; i < items.length; i++) {
    results.push(await downloadFile(items[i], i));
  }
  return results;
}

// Listen for messages from the popup
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'showNotification') {
    showNotification(request.message);
  }
  
  if (request.action === 'downloadZip') {
    chrome.downloads.download({
      url: request.url,
      filename: request.filename,
      saveAs: false
    }, (id) => {
      if (chrome.runtime.lastError) {
        console.error('Download error:', chrome.runtime.lastError);
        showNotification('Download failed: ' + chrome.runtime.lastError.message);
      } else {
        showNotification(`Download started: ${request.filename}`);
      }
    });
  }
  
  if (request.action === 'createZipFromUrls') {
    console.log('createZipFromUrls received, count:', request.count);
    createZipFromUrls(request.urls, request.names)
      .then(sendResponse)
      .catch(error => sendResponse({ success: false, error: error.message }));
    return true;
  }
  
  if (request.action === 'downloadImage') {
    downloadFile({ url: request.url, filename: request.filename }, 0)
      .then(sendResponse);
    return true;
  }

  if (request.action === 'downloadFiles' || request.action === 'downloadImages') {
    const requestedItems = request.files || request.images;
    const items = Array.isArray(requestedItems)
      ? requestedItems.map(item => typeof item === 'string' ? { url: item } : item)
      : [];
    downloadFiles(items).then((results) => {
      const failed = results.filter(result => !result.success);
      sendResponse({
        success: failed.length === 0,
        downloaded: results.length - failed.length,
        failed: failed.length,
        error: failed[0]?.error
      });
    });
    return true;
  }
  
  if (request.action === 'scrapePage') {
    const tabId = request.tabId || sender.tab?.id;
    if (!tabId) {
      sendResponse({ success: false, error: 'No tab ID available' });
      return false;
    }
    scrapePage(tabId)
      .then(result => sendResponse({ success: true, data: result }))
      .catch(error => sendResponse({ success: false, error: error.message }));
    return true; // Keep message channel open for async response
  }
  
  return false;
});

// Scrape page function for background
async function scrapePage(tabId) {
  const results = await chrome.scripting.executeScript({
    target: { tabId: tabId },
    func: () => {
      const images = [];
      const links = [];
      let text = '';
      
      // Scrape images
      document.querySelectorAll('img[src]').forEach(img => {
        const src = img.src;
        const alt = img.alt || '';
        const width = img.naturalWidth || img.width || 0;
        const height = img.naturalHeight || img.height || 0;
        
        if (src.startsWith('data:') && src.length > 100000) return;
        if (width < 10 || height < 10) return;
        
        if (!images.some(i => i.src === src)) {
          images.push({ src, alt, width, height });
        }
      });
      
      // Scrape links
      document.querySelectorAll('a[href]').forEach(link => {
        const href = link.href;
        const linkText = link.innerText.trim();
        
        if (!href || href.startsWith('javascript:') || href.startsWith('#')) return;
        if (href.startsWith('mailto:') || href.startsWith('tel:')) return;
        
        if (!links.some(l => l.href === href)) {
          links.push({ href, text: linkText.substring(0, 100) });
        }
      });
      
      // Scrape text
      const bodyClone = document.body.cloneNode(true);
      const unwanted = bodyClone.querySelectorAll('script, style, nav, header, footer, aside, .nav, .header, .footer, .sidebar, [role="navigation"]');
      unwanted.forEach(el => el.remove());
      
      text = (bodyClone.innerText || bodyClone.textContent || '')
        .replace(/\s+/g, ' ')
        .replace(/\n{3,}/g, '\n\n')
        .trim();
      
      return { images, links, text };
    }
  });
  
  return results[0].result;
}

function getZipExtension(rawName, url) {
  const rawMatch = String(rawName || '').match(/\.([a-z0-9]+)$/i);
  if (rawMatch && ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg'].includes(rawMatch[1].toLowerCase())) {
    return `.${rawMatch[1].toLowerCase()}`;
  }
  try {
    const urlMatch = new URL(url).pathname.match(/\.([a-z0-9]+)$/i);
    if (urlMatch && ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg'].includes(urlMatch[1].toLowerCase())) {
      return `.${urlMatch[1].toLowerCase()}`;
    }
  } catch {}
  return '.jpg';
}

function sanitizeZipEntryName(rawName, url, index, usedNames) {
  const extension = getZipExtension(rawName, url);
  const raw = String(rawName || '').replace(/\.[a-z0-9]+$/i, '');
  let base = raw
    .replace(/[\x00-\x1f\x7f/\\]/g, '_')
    .replace(/[^a-z0-9._ -]/gi, '_')
    .trim()
    .replace(/^\.+/, '')
    .replace(/[ .]+$/g, '')
    .slice(0, 140);
  if (!base || /^_+$/.test(base)) base = `image-${index + 1}`;

  let filename = `${base}${extension}`;
  let suffix = 2;
  while (usedNames.has(filename.toLowerCase())) {
    filename = `${base}-${suffix}${extension}`;
    suffix++;
  }
  usedNames.add(filename.toLowerCase());
  return filename;
}

async function ensurePackagedZipLibrary(urls, names, dependencies) {
  const deps = dependencies || {
    hasLibrary: () => typeof fflate !== 'undefined'
      && typeof fflate.Zip === 'function'
      && typeof fflate.ZipDeflate === 'function',
    loadLibrary: () => importScripts(chrome.runtime.getURL('libs/fflate.umd.js')),
    downloadIndividually: (items) => downloadFiles(items),
    notify: (message) => showNotification(message),
    reportError: (...args) => console.error(...args)
  };

  if (deps.hasLibrary()) return true;
  try {
    deps.loadLibrary();
    if (!deps.hasLibrary()) {
      throw new Error('Packaged fflate does not expose Zip and synchronous ZipDeflate');
    }
    console.log('fflate loaded from extension libs');
    return true;
  } catch (error) {
    deps.reportError('Packaged ZIP support is unavailable; downloading files individually.', error);
    deps.notify('Packaged ZIP support is unavailable. Downloading files individually.');
    const safeNames = Array.isArray(names) ? names : [];
    await deps.downloadIndividually(urls.slice(0, 200).map((url, index) => ({
      url,
      filename: safeNames[index] || `image-${index + 1}`,
      defaultExtension: '.jpg'
    })));
    return false;
  }
}

// Create ZIP from URLs with packaged fflate's worker-free streaming APIs.
async function createZipFromUrls(urls, names, deps = {
  ensureLibrary: ensurePackagedZipLibrary,
  notify: showNotification,
  reportError: console.error,
  fetchUrl: url => fetch(url, { mode: 'cors', credentials: 'omit' }),
  createZip: () => new fflate.Zip(),
  createFile: filename => new fflate.ZipDeflate(filename, { level: 0 }),
  downloadIndividually: items => downloadFiles(items),
  downloadZip: (url, filename) => new Promise((resolve, reject) => {
    chrome.downloads.download({ url, filename, saveAs: false }, id => {
      const runtimeError = chrome.runtime.lastError;
      if (runtimeError) reject(new Error(runtimeError.message));
      else resolve(id);
    });
  })
}) {
  const suppliedUrls = Array.isArray(urls) ? urls : [];
  const safeNames = Array.isArray(names) ? names : [];
  const candidates = suppliedUrls
    .map((url, index) => ({ url, rawName: safeNames[index], index }))
    .filter(item => typeof item.url === 'string'
      && item.url
      && !item.url.includes('preview_image.jpg')
      && !item.url.includes('blank.gif'))
    .slice(0, 200);
  let added = 0;
  let skipped = suppliedUrls.length - candidates.length;
  let fallbackAttempted = false;
  const fallbackToIndividualDownloads = async () => {
    fallbackAttempted = true;
    const fallbackNames = new Set();
    const fallbackItems = candidates.map(item => ({
      url: item.url,
      filename: sanitizeZipEntryName(item.rawName, item.url, item.index, fallbackNames),
      defaultExtension: '.jpg'
    }));
    deps.notify(`No images could be added to the ZIP. Downloading ${fallbackItems.length} files individually.`);
    await deps.downloadIndividually(fallbackItems);
    return { success: false, fallback: true, added, skipped };
  };
  deps.notify(`Fetching ${candidates.length} images and streaming ZIP...`);

  try {
    if (!await deps.ensureLibrary(
      candidates.map(item => item.url),
      candidates.map(item => item.rawName)
    )) {
      return { success: false, fallback: true, added: 0, skipped: suppliedUrls.length };
    }

    const chunks = [];
    const zip = deps.createZip();
    const usedNames = new Set();
    let resolveCompletion;
    const completion = new Promise(resolve => { resolveCompletion = resolve; });
    let completed = false;
    const finish = result => {
      if (!completed) {
        completed = true;
        resolveCompletion(result);
      }
    };

    zip.ondata = (error, chunk, final) => {
      if (error) {
        deps.reportError('fflate ZIP error:', error);
        deps.notify('Error creating ZIP');
        finish({ success: false, error: error.message || 'ZIP stream failed', added, skipped });
        return;
      }
      if (chunk) chunks.push(chunk);
      if (!final) return;

      (async () => {
        try {
          const blob = new Blob(chunks, { type: 'application/zip' });
          const bytes = new Uint8Array(await blob.arrayBuffer());
          let binary = '';
          for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
          const dataUrl = 'data:application/zip;base64,' + btoa(binary);
          const zipName = `scraped-images-${new Date().toISOString().slice(0, 10)}.zip`;
          await deps.downloadZip(dataUrl, zipName);
          const summary = skipped > 0
            ? `ZIP download started: ${zipName} (${added} image${added === 1 ? '' : 's'} added; ${skipped} skipped)`
            : `ZIP download started: ${zipName}`;
          deps.notify(summary);
          finish({ success: true, added, skipped });
        } catch (downloadError) {
          deps.reportError('ZIP download failed:', downloadError);
          deps.notify('ZIP download failed: ' + downloadError.message);
          finish({ success: false, error: downloadError.message, added, skipped });
        }
      })();
    };

    for (const item of candidates) {
      try {
        const response = await deps.fetchUrl(item.url);
        if (!response.ok) {
          skipped++;
          continue;
        }
        const bytes = new Uint8Array(await response.arrayBuffer());
        const filename = sanitizeZipEntryName(item.rawName, item.url, item.index, usedNames);
        const streamFile = deps.createFile(filename);
        zip.add(streamFile);
        streamFile.push(bytes, true);
        added++;
      } catch (error) {
        skipped++;
        deps.reportError('Error fetching or adding ZIP entry:', item.url, error);
      }
    }

    if (added === 0) return await fallbackToIndividualDownloads();

    try {
      zip.end();
    } catch (error) {
      deps.reportError('Error finalizing ZIP:', error);
      deps.notify('Error creating ZIP');
      finish({ success: false, error: error.message, added, skipped });
    }
    return await completion;
  } catch (error) {
    deps.reportError('Error creating ZIP:', error);
    if (added === 0 && !fallbackAttempted && typeof deps.downloadIndividually === 'function') {
      skipped = suppliedUrls.length;
      try {
        return await fallbackToIndividualDownloads();
      } catch (fallbackError) {
        deps.reportError('Individual download fallback failed:', fallbackError);
      }
    }
    deps.notify('Error creating ZIP');
    return { success: false, error: error.message, added, skipped };
  }
}
