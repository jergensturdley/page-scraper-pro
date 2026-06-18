// Background script for Page Scraper Pro

// Track active downloads
const activeDownloads = new Map();

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
chrome.contextMenus.onClicked.addListener((info, tab) => {
  switch (info.menuItemId) {
    case 'scrape-images':
      scrapeAndShow(tab, 'images');
      break;
    case 'scrape-links':
      scrapeAndShow(tab, 'links');
      break;
    case 'scrape-selection':
      if (info.selectionText) {
        // Send to content script to copy since background can't access clipboard
        chrome.tabs.sendMessage(tab.id, {
          action: 'copyToClipboard',
          text: info.selectionText
        });
        showNotification('Selection copied to clipboard!');
      }
      break;
  }
});

// Scrape and display results
async function scrapeAndShow(tab, type) {
  try {
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
    
    if (results && results[0]) {
      const data = results[0].result;
      const content = type === 'images' 
        ? data.images.map(img => img.src).join('\n')
        : data.links.map(link => link.href).join('\n');
      
      // Send to content script for clipboard access
      chrome.tabs.sendMessage(tab.id, {
        action: 'copyToClipboard',
        text: content
      });
      showNotification(`${type === 'images' ? 'Images' : 'Links'} copied to clipboard!`);
    }
  } catch (error) {
    console.error('Scraping error:', error);
    showNotification('Error scraping page');
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

// Listen for messages from popup or content script
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
    createZipFromUrls(request.urls, request.names);
  }
  
  if (request.action === 'downloadImage') {
    console.log('downloadImage received:', request.url);
    chrome.downloads.download({
      url: request.url,
      filename: request.filename,
      saveAs: false
    }, (id) => {
      if (chrome.runtime.lastError) {
        console.error('Download failed:', chrome.runtime.lastError);
      } else {
        console.log('Download started:', id);
      }
    });
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

// Create ZIP from URLs (fetch images and create ZIP) using fflate streaming
async function createZipFromUrls(urls, names) {
  console.log('createZipFromUrls streaming:', urls.length);
  showNotification(`Fetching ${urls.length} images and streaming ZIP...`);

  // Try to load fflate via CDN if not present
  if (typeof fflate === 'undefined') {
    try {
      importScripts(chrome.runtime.getURL('libs/fflate.umd.js'));
      console.log('fflate loaded from extension libs');
    } catch (e) {
      try {
        importScripts('https://cdn.jsdelivr.net/npm/fflate@0.8.3/umd/index.js');
        console.log('fflate loaded from CDN');
      } catch (err) {
        console.warn('Failed to import fflate, falling back to batch download', e, err);
        for (let i = 0; i < urls.length; i++) {
          const url = urls[i];
          const filename = (names[i] || `image-${i + 1}`).replace(/[^a-z0-9._-]/gi, '_') + '.jpg';
          chrome.downloads.download({ url, filename, saveAs: false });
          await new Promise(r => setTimeout(r, 150));
        }
        return;
      }
    }
  }

  // Create Zip stream
  const chunks = [];
  const zip = new fflate.Zip();
  zip.ondata = (err, chunk, final) => {
    if (err) {
      console.error('fflate zip error', err);
      return;
    }
    chunks.push(chunk);
    if (final) {
      const blob = new Blob(chunks, { type: 'application/zip' });
      // Trigger download
      const readerPromise = blob.arrayBuffer();
      readerPromise.then(ab => {
        const bytes = new Uint8Array(ab);
        let binary = '';
        for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
        const dataUrl = 'data:application/zip;base64,' + btoa(binary);
        const zipName = `scraped-images-${new Date().toISOString().slice(0,10)}.zip`;
        chrome.downloads.download({ url: dataUrl, filename: zipName, saveAs: false }, id => {
          if (chrome.runtime.lastError) {
            console.error('Download failed', chrome.runtime.lastError);
            showNotification('ZIP download failed: ' + chrome.runtime.lastError.message);
          } else {
            showNotification('ZIP download started: ' + zipName);
          }
        });
      }).catch(err => {
        console.error('Failed to read zip blob', err);
      });
    }
  };

  // For each URL, fetch and add to zip as an async stream deflate (no compression for images)
  for (let i = 0; i < urls.length; i++) {
    const url = urls[i];
    const rawName = names[i] || `image-${i + 1}`;
    if (!url || url.includes('preview_image.jpg') || url.includes('blank.gif')) {
      continue;
    }

    try {
      const resp = await fetch(url, { mode: 'cors', credentials: 'omit' });
      if (!resp.ok) {
        console.warn('Failed to fetch', url, resp.status);
        continue;
      }
      const ab = await resp.arrayBuffer();
      const bytes = new Uint8Array(ab);
      // Determine filename extension
      let ext = '.jpg';
      const lower = url.toLowerCase();
      if (lower.includes('.png')) ext = '.png';
      else if (lower.includes('.gif')) ext = '.gif';
      else if (lower.includes('.webp')) ext = '.webp';
      else if (lower.includes('.svg')) ext = '.svg';

      const filename = /\.(jpg|jpeg|png|gif|webp|svg)$/i.test(rawName) ? rawName : (rawName + ext);
      // Use AsyncZipDeflate with level 0 (store) for already-compressed files
      const streamFile = new fflate.AsyncZipDeflate(filename, { level: 0 });
      zip.add(streamFile);
      // Push file data and close the stream (true finalized)
      streamFile.push(bytes, true);

    } catch (e) {
      console.warn('Error fetching or adding to zip:', url, e);
    }
  }

  // Finalize ZIP
  try {
    zip.end();
  } catch (e) {
    console.error('Error finalizing zip', e);
    showNotification('Error creating ZIP');
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