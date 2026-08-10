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
  
  if (request.action === 'createAndDownloadZip') {
    console.log('createAndDownloadZip received, images:', request.images.length);
    createAndDownloadZip(request.images, request.zipName);
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

// Create ZIP archive from images
async function createZipArchive(images) {
  console.log('Creating ZIP with', images.length, 'images');
  
  // Each file gets a local file header
  const files = [];
  
  for (let i = 0; i < images.length; i++) {
    const img = images[i];
    console.log(`Processing ${i}/${images.length}: ${img.name}`);
    
    // Parse data URL
    const matches = img.dataUrl.match(/^data:image\/([a-z]+);base64,(.+)$/i);
    if (!matches) continue;
    
    const ext = matches[1];
    const base64Data = matches[2];
    const binaryData = atob(base64Data);
    const fileData = new Uint8Array(binaryData.length);
    for (let j = 0; j < binaryData.length; j++) {
      fileData[j] = binaryData.charCodeAt(j);
    }
    
    // CRC32
    const crc = calculateCRC32(fileData);
    
    const nameBytes = new TextEncoder().encode(img.name);
    const header = new Uint8Array(30 + nameBytes.length);
    const view = new DataView(header.buffer);
    
    view.setUint32(0, 0x04034b50, true);
    view.setUint16(4, 20, true);
    view.setUint16(6, 0, true);
    view.setUint16(8, 0, true);
    view.setUint16(10, 0, true);
    view.setUint16(12, 0, true);
    view.setUint32(14, crc, true);
    view.setUint32(18, fileData.length, true);
    view.setUint32(22, fileData.length, true);
    view.setUint16(26, nameBytes.length, true);
    view.setUint16(28, 0, true);
    header.set(nameBytes, 30);
    
    // Track offset for central directory
    files.push({ header, data: fileData, nameBytes, crc, nameLen: nameBytes.length });
  }
  
  console.log('Prepared', files.length, 'files for ZIP');
  
  // Calculate sizes
  let localHeadersSize = 0;
  for (const f of files) {
    f.localOffset = localHeadersSize;
    localHeadersSize += f.header.length + f.data.length;
  }
  
  const centralDirSize = files.reduce((sum, f) => sum + 46 + f.nameLen, 0);
  const totalSize = localHeadersSize + centralDirSize + 22; // + EOCD
  
  const result = new Uint8Array(totalSize);
  
  // Write local file entries
  let offset = 0;
  for (const f of files) {
    result.set(f.header, offset);
    result.set(f.data, offset + f.header.length);
    offset += f.header.length + f.data.length;
  }
  
  const cdStartOffset = offset;
  
  // Write central directory
  for (const f of files) {
    const cdHeader = new Uint8Array(46);
    const cdView = new DataView(cdHeader.buffer);
    
    cdView.setUint32(0, 0x02014b50, true);
    cdView.setUint16(4, 20, true);
    cdView.setUint16(6, 20, true);
    cdView.setUint16(8, 0, true);
    cdView.setUint16(10, 0, true);
    cdView.setUint16(12, 0, true);
    cdView.setUint16(14, 0, true);
    cdView.setUint32(16, f.crc, true);
    cdView.setUint32(20, f.data.length, true);
    cdView.setUint32(24, f.data.length, true);
    cdView.setUint16(28, f.nameLen, true);
    cdView.setUint16(30, 0, true);
    cdView.setUint16(32, 0, true);
    cdView.setUint16(34, 0, true);
    cdView.setUint16(36, 0, true);
    cdView.setUint32(38, 0, true);
    cdView.setUint32(42, f.localOffset, true);
    
    result.set(cdHeader, offset);
    result.set(f.nameBytes, offset + 46);
    offset += 46 + f.nameLen;
  }
  
  const cdSize = offset - cdStartOffset;
  
  // Write EOCD (End of Central Directory)
  const eocd = new Uint8Array(22);
  const eocdView = new DataView(eocd.buffer);
  eocdView.setUint32(0, 0x06054b50, true);
  eocdView.setUint16(4, 0, true);
  eocdView.setUint16(6, 0, true);
  eocdView.setUint16(8, files.length, true);
  eocdView.setUint16(10, files.length, true);
  eocdView.setUint32(12, cdSize, true);
  eocdView.setUint32(16, cdStartOffset, true);
  eocdView.setUint16(20, 0, true);
  
  result.set(eocd, offset);
  
  console.log('ZIP created, size:', result.length, 'files:', files.length);
  return new Blob([result], { type: 'application/zip' });
}

// Simple CRC32 calculation
function calculateCRC32(data) {
  const table = new Uint32Array(256);
  for (let i = 0; i < 256; i++) {
    let c = i;
    for (let j = 0; j < 8; j++) {
      c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
    }
    table[i] = c;
  }
  
  let crc = 0xFFFFFFFF;
  for (let i = 0; i < data.length; i++) {
    crc = table[(crc ^ data[i]) & 0xFF] ^ (crc >>> 8);
  }
  return (crc ^ 0xFFFFFFFF) >>> 0;
}

// Create ZIP and download it
async function createAndDownloadZip(images, zipName) {
  try {
    const zipBlob = await createZipArchive(images);
    console.log('ZIP created, size:', zipBlob.size);
    
    // Convert to data URL for reliable download
    const arrayBuffer = await zipBlob.arrayBuffer();
    const bytes = new Uint8Array(arrayBuffer);
    let binary = '';
    for (let i = 0; i < bytes.length; i++) {
      binary += String.fromCharCode(bytes[i]);
    }
    const dataUrl = 'data:application/zip;base64,' + btoa(binary);
    
    // Download via background script
    chrome.downloads.download({
      url: dataUrl,
      filename: zipName || 'scraped-images.zip',
      saveAs: false
    }, (id) => {
      if (chrome.runtime.lastError) {
        console.error('Download failed:', chrome.runtime.lastError);
        showNotification('Download failed: ' + chrome.runtime.lastError.message);
      } else {
        showNotification(`Downloaded ${images.length} images to ${zipName}`);
      }
    });
  } catch (error) {
    console.error('ZIP creation failed:', error);
    showNotification('Error creating ZIP: ' + error.message);
  }
}

async function ensurePackagedZipLibrary(urls, names, dependencies) {
  const deps = dependencies || {
    hasLibrary: () => typeof fflate !== 'undefined'
      && typeof fflate.Zip === 'function'
      && typeof fflate.AsyncZipDeflate === 'function',
    loadLibrary: () => importScripts(chrome.runtime.getURL('libs/fflate.umd.js')),
    downloadIndividually: (items) => downloadFiles(items),
    notify: (message) => showNotification(message),
    reportError: (...args) => console.error(...args)
  };

  if (deps.hasLibrary()) return true;
  try {
    deps.loadLibrary();
    if (!deps.hasLibrary()) {
      throw new Error('Packaged fflate does not expose the required streaming ZIP APIs');
    }
    console.log('fflate loaded from extension libs');
    return true;
  } catch (error) {
    deps.reportError('Packaged ZIP support is unavailable; downloading files individually.', error);
    deps.notify('Packaged ZIP support is unavailable. Downloading files individually.');
    const safeNames = Array.isArray(names) ? names : [];
    await deps.downloadIndividually(urls.map((url, index) => ({
      url,
      filename: safeNames[index] || `image-${index + 1}`,
      defaultExtension: '.jpg'
    })));
    return false;
  }
}

// Create ZIP from URLs (fetch images and create ZIP) using packaged fflate streaming.
async function createZipFromUrls(urls, names, deps = {
  ensureLibrary: ensurePackagedZipLibrary,
  notify: showNotification,
  reportError: console.error,
  fetchUrl: url => fetch(url, { mode: 'cors', credentials: 'omit' }),
  createZip: () => new fflate.Zip(),
  createFile: filename => new fflate.AsyncZipDeflate(filename, { level: 0 }),
  downloadZip: (url, filename) => new Promise((resolve, reject) => {
    chrome.downloads.download({ url, filename, saveAs: false }, id => {
      const runtimeError = chrome.runtime.lastError;
      if (runtimeError) reject(new Error(runtimeError.message));
      else resolve(id);
    });
  })
}) {
  const safeUrls = Array.isArray(urls) ? urls : [];
  const safeNames = Array.isArray(names) ? names : [];
  console.log('createZipFromUrls streaming:', safeUrls.length);
  deps.notify(`Fetching ${safeUrls.length} images and streaming ZIP...`);

  try {
    if (!await deps.ensureLibrary(safeUrls, safeNames)) {
      return { success: false, fallback: true };
    }

    const chunks = [];
    const zip = deps.createZip();
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
        finish({ success: false, error: error.message || 'ZIP stream failed' });
        return;
      }
      chunks.push(chunk);
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
          deps.notify('ZIP download started: ' + zipName);
          finish({ success: true });
        } catch (error) {
          deps.reportError('ZIP download failed:', error);
          deps.notify('ZIP download failed: ' + error.message);
          finish({ success: false, error: error.message });
        }
      })();
    };

    for (let i = 0; i < safeUrls.length; i++) {
      const url = safeUrls[i];
      const rawName = safeNames[i] || `image-${i + 1}`;
      if (!url || url.includes('preview_image.jpg') || url.includes('blank.gif')) continue;

      try {
        const response = await deps.fetchUrl(url);
        if (!response.ok) {
          console.warn('Failed to fetch', url, response.status);
          continue;
        }
        const bytes = new Uint8Array(await response.arrayBuffer());
        let ext = '.jpg';
        const lower = url.toLowerCase();
        if (lower.includes('.png')) ext = '.png';
        else if (lower.includes('.gif')) ext = '.gif';
        else if (lower.includes('.webp')) ext = '.webp';
        else if (lower.includes('.svg')) ext = '.svg';

        const filename = /\.(jpg|jpeg|png|gif|webp|svg)$/i.test(rawName) ? rawName : rawName + ext;
        const streamFile = deps.createFile(filename);
        zip.add(streamFile);
        streamFile.push(bytes, true);
      } catch (error) {
        console.warn('Error fetching or adding to ZIP:', url, error);
      }
    }

    try {
      zip.end();
    } catch (error) {
      deps.reportError('Error finalizing ZIP:', error);
      deps.notify('Error creating ZIP');
      finish({ success: false, error: error.message });
    }
    return await completion;
  } catch (error) {
    deps.reportError('Error creating ZIP:', error);
    deps.notify('Error creating ZIP');
    return { success: false, error: error.message };
  }
}

// Create ZIP from file objects
async function createZipArchiveFromFiles(files) {
  // Calculate sizes
  let localHeadersSize = 0;
  for (const f of files) {
    localHeadersSize += 30 + f.nameBytes.length + f.fileData.length;
  }
  
  const centralDirSize = files.reduce((sum, f) => sum + 46 + f.nameBytes.length, 0);
  const totalSize = localHeadersSize + centralDirSize + 22;
  
  const result = new Uint8Array(totalSize);
  
  // Write local file entries
  let offset = 0;
  for (const f of files) {
    f.localOffset = offset;
    
    const header = new Uint8Array(30 + f.nameBytes.length);
    const view = new DataView(header.buffer);
    
    view.setUint32(0, 0x04034b50, true);
    view.setUint16(4, 20, true);
    view.setUint16(6, 0, true);
    view.setUint16(8, 0, true);
    view.setUint16(10, 0, true);
    view.setUint16(12, 0, true);
    view.setUint32(14, f.crc, true);
    view.setUint32(18, f.fileData.length, true);
    view.setUint32(22, f.fileData.length, true);
    view.setUint16(26, f.nameBytes.length, true);
    view.setUint16(28, 0, true);
    header.set(f.nameBytes, 30);
    
    result.set(header, offset);
    result.set(f.fileData, offset + header.length);
    offset += header.length + f.fileData.length;
  }
  
  const cdStartOffset = offset;
  
  // Write central directory
  for (const f of files) {
    const cdHeader = new Uint8Array(46);
    const cdView = new DataView(cdHeader.buffer);
    
    cdView.setUint32(0, 0x02014b50, true);
    cdView.setUint16(4, 20, true);
    cdView.setUint16(6, 20, true);
    cdView.setUint16(8, 0, true);
    cdView.setUint16(10, 0, true);
    cdView.setUint16(12, 0, true);
    cdView.setUint16(14, 0, true);
    cdView.setUint32(16, f.crc, true);
    cdView.setUint32(20, f.fileData.length, true);
    cdView.setUint32(24, f.fileData.length, true);
    cdView.setUint16(28, f.nameBytes.length, true);
    cdView.setUint16(30, 0, true);
    cdView.setUint16(32, 0, true);
    cdView.setUint16(34, 0, true);
    cdView.setUint16(36, 0, true);
    cdView.setUint32(38, 0, true);
    cdView.setUint32(42, f.localOffset, true);
    
    result.set(cdHeader, offset);
    result.set(f.nameBytes, offset + 46);
    offset += 46 + f.nameBytes.length;
  }
  
  const cdSize = offset - cdStartOffset;
  
  // Write EOCD
  const eocd = new Uint8Array(22);
  const eocdView = new DataView(eocd.buffer);
  eocdView.setUint32(0, 0x06054b50, true);
  eocdView.setUint16(4, 0, true);
  eocdView.setUint16(6, 0, true);
  eocdView.setUint16(8, files.length, true);
  eocdView.setUint16(10, files.length, true);
  eocdView.setUint32(12, cdSize, true);
  eocdView.setUint32(16, cdStartOffset, true);
  eocdView.setUint16(20, 0, true);
  
  result.set(eocd, offset);
  
  return new Blob([result], { type: 'application/zip' });
}
