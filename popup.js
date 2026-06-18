// Popup JavaScript for Page Scraper Pro

// Settings - initialized first since it's used by other functions
let settings = {
  minSize: false,
  minWidth: 100,
  minHeight: 100,
  excludeNav: true
};

// Current filter state
let currentImageFilter = 'all';

// Selection state
let selectedImages = new Set();

// Global scraped data object (used by injected function)
let scrapedData = {
  images: [],
  videos: [],
  audios: [],
  links: [],
  text: ''
};

// Function that can be injected via scripting API
function scrapePageFromContent() {
  const images = [];
  const videos = [];
  const audios = [];
  const links = [];
  
  // Scrape images - comprehensive selection
  document.querySelectorAll('img[src], img[data-src], img[data-lazy], picture img').forEach(img => {
    let src = img.src || img.dataset.src || img.dataset.lazy || img.dataset.original;
    if (!src) return;
    
    const alt = img.alt || img.title || '';
    const width = img.naturalWidth || img.width || 0;
    const height = img.naturalHeight || img.height || 0;
    
    // Skip very small images
    if (width < 10 || height < 10) return;
    
    // Skip overly large base64 data URIs
    if (src.startsWith('data:') && src.length > 50000) return;
    
    // Skip duplicates
    if (!images.some(i => i.src === src)) {
      images.push({ src, alt, width, height });
    }
  });
  
  // Scrape videos - comprehensive selection
  const videoElements = document.querySelectorAll('video, source[src$=".mp4"], source[src$=".webm"], source[src$=".ogg"], source[src$=".mov"], iframe[src*="youtube"], iframe[src*="vimeo"], iframe[src*="dailymotion"], video source');
  
  videoElements.forEach(video => {
    let src = '';
    let type = '';
    let width = 0;
    let height = 0;
    let poster = '';
    
    if (video.tagName === 'VIDEO') {
      src = video.src || video.querySelector('source')?.src || '';
      width = video.videoWidth || video.width || video.getAttribute('width') || 0;
      height = video.videoHeight || video.height || video.getAttribute('height') || 0;
      poster = video.poster || '';
      type = 'video';
    } else if (video.tagName === 'SOURCE') {
      src = video.src;
      const parentVideo = video.closest('video');
      if (parentVideo) {
        width = parentVideo.videoWidth || parentVideo.width || 0;
        height = parentVideo.videoHeight || parentVideo.height || 0;
        poster = parentVideo.poster || '';
      }
      type = 'video';
    } else if (video.tagName === 'IFRAME') {
      src = video.src;
      width = video.width || video.getAttribute('width') || 0;
      height = video.height || video.getAttribute('height') || 0;
      type = 'embed';
    }
    
    if (!src) return;
    
    if (!videos.some(v => v.src === src)) {
      videos.push({ src, type, width, height, poster });
    }
  });
  
  // Scrape audio
  document.querySelectorAll('audio, audio source, source[src$=".mp3"], source[src$=".wav"], source[src$=".ogg"], source[src$=".flac"], source[src$=".aac"], source[src$=".m4a"], source[src$=".opus"], source[src$=".wma"]').forEach(el => {
    let src = '';
    let type = 'audio';

    if (el.tagName === 'AUDIO') {
      src = el.src || el.querySelector('source')?.src || '';
    } else if (el.tagName === 'SOURCE') {
      src = el.src;
    }

    if (!src) return;

    if (!audios.some(a => a.src === src)) {
      audios.push({ src, type });
    }
  });

  document.querySelectorAll('a[href$=".mp3"], a[href$=".wav"], a[href$=".ogg"], a[href$=".flac"], a[href$=".aac"], a[href$=".m4a"], a[href$=".opus"]').forEach(link => {
    const src = link.href;
    if (!audios.some(a => a.src === src)) {
      audios.push({ src, type: 'link' });
    }
  });

  // Scrape links
  document.querySelectorAll('a[href]').forEach(link => {
    const href = link.href;
    const text = link.innerText.trim();
    
    if (!href || href.startsWith('javascript:') || href.startsWith('#')) return;
    if (href.startsWith('mailto:') || href.startsWith('tel:')) return;
    
    if (!links.some(l => l.href === href)) {
      links.push({ href, text });
    }
  });
  
  // Scrape text
  const bodyClone = document.body.cloneNode(true);
  const unwanted = bodyClone.querySelectorAll('script, style, nav, header, footer, aside, .nav, .header, .footer, .sidebar, [role="navigation"]');
  unwanted.forEach(el => el.remove());
  
  const text = (bodyClone.innerText || bodyClone.textContent || '')
    .replace(/\s+/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
  
  return { images, videos, audios, links, text };
}

// DOM Elements
const elements = {
  currentPage: document.getElementById('currentPage'),
  imageCount: document.getElementById('imageCount'),
  videoCount: document.getElementById('videoCount'),
  audioCount: document.getElementById('audioCount'),
  linkCount: document.getElementById('linkCount'),
  scrapeBtn: document.getElementById('scrapeBtn'),
  copyBtn: document.getElementById('copyBtn'),
  exportJson: document.getElementById('exportJson'),
  exportImages: document.getElementById('exportImages'),
  exportVideos: document.getElementById('exportVideos'),
  exportAudios: document.getElementById('exportAudios'),
  exportArchive: document.getElementById('exportArchive'),
  copyImageUrls: document.getElementById('copyImageUrls'),
  status: document.getElementById('status'),
  imagesContainer: document.getElementById('imagesContainer'),
  videosContainer: document.getElementById('videosContainer'),
  audiosContainer: document.getElementById('audiosContainer'),
  linksContainer: document.getElementById('linksContainer'),
  textContainer: document.getElementById('textContainer'),
  imagesCount: document.getElementById('imagesCount'),
  videosCount: document.getElementById('videosCount'),
  audiosCount: document.getElementById('audiosCount'),
  linksCount: document.getElementById('linksCount'),
  textResultsCount: document.getElementById('textResultsCount'),
  tabBtns: document.querySelectorAll('.tab-btn'),
  settingsBtn: document.getElementById('settingsBtn'),
  sidebarBtn: document.getElementById('sidebarBtn'),
  imageFilter: document.getElementById('imageFilter'),
  linkFilter: document.getElementById('linkFilter'),
  filterChips: document.querySelectorAll('.filter-chip'),
  detachBtn: document.getElementById('detachBtn')
};

// Initialize
document.addEventListener('DOMContentLoaded', async () => {
  await getCurrentTab();
  setupEventListeners();
  loadSettings();
  detectSidebarMode();
});

// Get current tab info
async function getCurrentTab() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab) {
      elements.currentPage.textContent = 'No active tab';
      return;
    }

    try {
      const url = new URL(tab.url);
      elements.currentPage.textContent = url.hostname || tab.url;
    } catch (urlError) {
      elements.currentPage.textContent = tab.url || 'Unknown page';
    }
  } catch (error) {
    console.error('Failed to get current tab:', error);
    elements.currentPage.textContent = 'Unknown page';
  }
}

// Setup event listeners
function setupEventListeners() {
  elements.scrapeBtn.addEventListener('click', scrapePageData);
  elements.copyBtn.addEventListener('click', copyAllData);
  elements.exportJson.addEventListener('click', exportJson);
  elements.exportImages.addEventListener('click', exportWithImages);
  elements.exportVideos.addEventListener('click', exportVideos);
  elements.exportAudios.addEventListener('click', exportAudios);
  elements.exportArchive.addEventListener('click', exportArchive);
  elements.copyImageUrls.addEventListener('click', copyImageURLs);
  elements.settingsBtn.addEventListener('click', openSettings);
  if (elements.sidebarBtn) {
    elements.sidebarBtn.addEventListener('click', openInSidebar);
  }
  if (elements.detachBtn) {
    elements.detachBtn.addEventListener('click', detachToPopup);
  }
  
  // Tab switching
  elements.tabBtns.forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });
  
  // Copy buttons for each tab
  document.querySelectorAll('.copy-tab-btn').forEach(btn => {
    btn.addEventListener('click', () => copyTabData(btn.dataset.type));
  });
  
  // Filter inputs
  if (elements.imageFilter) {
    elements.imageFilter.addEventListener('input', () => renderImages());
  }
  if (elements.linkFilter) {
    elements.linkFilter.addEventListener('input', () => renderLinks());
  }
  
  // Filter chips
  elements.filterChips.forEach(chip => {
    chip.addEventListener('click', () => {
      // Update active state
      elements.filterChips.forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
      // Re-render with filter
      currentImageFilter = chip.dataset.filter;
      renderImages();
    });
  });

  // Selection toolbar buttons
  const selectAllBtn = document.getElementById('selectAllBtn');
  const openPanelBtn = document.getElementById('openPanelBtn');
  const closePanelBtn = document.getElementById('closePanelBtn');
  const clearSelectionBtn = document.getElementById('clearSelectionBtn');
  const downloadConfirmBtn = document.getElementById('downloadConfirmBtn');

  if (selectAllBtn) selectAllBtn.addEventListener('click', selectAllImages);
  if (openPanelBtn) openPanelBtn.addEventListener('click', openDownloadPanel);
  if (closePanelBtn) closePanelBtn.addEventListener('click', closeDownloadPanel);
  if (clearSelectionBtn) clearSelectionBtn.addEventListener('click', clearSelection);
  if (downloadConfirmBtn) downloadConfirmBtn.addEventListener('click', downloadSelectedImages);
}

// Switch tabs
function switchTab(tabName) {
  elements.tabBtns.forEach(btn => btn.classList.remove('active'));
  document.querySelectorAll('.tab-pane').forEach(pane => pane.classList.remove('active'));
  
  // Reset image filter when switching away from images tab
  if (tabName !== 'images' && elements.filterChips) {
    elements.filterChips.forEach(c => c.classList.remove('active'));
    const allChip = document.querySelector('.filter-chip[data-filter="all"]');
    if (allChip) allChip.classList.add('active');
    currentImageFilter = 'all';
    if (elements.imageFilter) elements.imageFilter.value = '';
  }
  
  const tabBtn = document.querySelector(`[data-tab="${tabName}"]`);
  const tabPane = document.getElementById(`${tabName}Tab`);
  if (tabBtn) tabBtn.classList.add('active');
  if (tabPane) tabPane.classList.add('active');
}

// Scrape page data
async function scrapePageData() {
  try {
    elements.scrapeBtn.disabled = true;
    elements.status.innerHTML = '<span class="spinner"></span> Scraping...';
    
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) {
      throw new Error('No active tab');
    }
    
    // Use chrome.scripting.executeScript directly (Manifest V3)
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: scrapePageFromContentFallback
    });
    
    if (results && results[0] && results[0].result) {
      scrapedData = results[0].result;
      updateUI();
      showToast('Page scraped successfully!');
    } else {
      throw new Error('No results returned');
    }
  } catch (error) {
    console.error('Scraping error:', error);
    showToast('Error scraping page: ' + error.message);
    // Set empty data
    scrapedData = { images: [], videos: [], audios: [], links: [], text: '' };
    updateUI();
  } finally {
    elements.scrapeBtn.disabled = false;
    elements.scrapeBtn.innerHTML = `
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M21 16V8a2 2 0 0 0-1-1.73l-8-5a2 2 0 0 0-2 0l-8 5a2 2 0 0 0-1 1.73v8a2 2 0 0 0 1 1.73l8 5a2 2 0 0 0 2 0l8-5a2 2 0 0 0 1-1.73z"/>
        <polyline points="7.5 12 12 16 16.5 12"/>
      </svg>
      Scrape Again
    `;
  }
}

// Update UI with scraped data
function updateUI() {
  // Update stats
  elements.imageCount.textContent = scrapedData.images.length;
  elements.videoCount.textContent = scrapedData.videos.length;
  elements.audioCount.textContent = scrapedData.audios.length;
  elements.linkCount.textContent = scrapedData.links.length;
  
  // Update copy button
  elements.copyBtn.disabled = scrapedData.images.length === 0 && scrapedData.videos.length === 0 && scrapedData.audios.length === 0 && scrapedData.links.length === 0;
  
  // Update results
  renderImages();
  renderVideos();
  renderAudios();
  renderLinks();
  renderText();
  
  elements.status.textContent = `Last scraped: ${new Date().toLocaleTimeString()}`;
}

// Fallback scrape function for scripting API
function scrapePageFromContentFallback() {
  const images = [];
  const videos = [];
  const audios = [];
  const links = [];
  
  // Comprehensive image selection
  document.querySelectorAll('img[src], img[data-src], img[data-lazy], picture img').forEach(img => {
    let src = img.src || img.dataset.src || img.dataset.lazy || img.dataset.original;
    if (!src) return;
    
    const alt = img.alt || img.title || '';
    const width = img.naturalWidth || img.width || 0;
    const height = img.naturalHeight || img.height || 0;
    
    if (width < 10 || height < 10) return;
    if (src.startsWith('data:') && src.length > 50000) return;
    
    if (!images.some(i => i.src === src)) {
      images.push({ src, alt, width, height });
    }
  });
  
  // Comprehensive video selection
  const videoElements = document.querySelectorAll('video, source[src$=".mp4"], source[src$=".webm"], source[src$=".ogg"], source[src$=".mov"], iframe[src*="youtube"], iframe[src*="vimeo"], iframe[src*="dailymotion"], video source');
  
  videoElements.forEach(video => {
    let src = '';
    let type = '';
    let width = 0;
    let height = 0;
    let poster = '';
    
    if (video.tagName === 'VIDEO') {
      src = video.src || video.querySelector('source')?.src || '';
      width = video.videoWidth || video.width || video.getAttribute('width') || 0;
      height = video.videoHeight || video.height || video.getAttribute('height') || 0;
      poster = video.poster || '';
      type = 'video';
    } else if (video.tagName === 'SOURCE') {
      src = video.src;
      const parentVideo = video.closest('video');
      if (parentVideo) {
        width = parentVideo.videoWidth || parentVideo.width || 0;
        height = parentVideo.videoHeight || parentVideo.height || 0;
        poster = parentVideo.poster || '';
      }
      type = 'video';
    } else if (video.tagName === 'IFRAME') {
      src = video.src;
      width = video.width || video.getAttribute('width') || 0;
      height = video.height || video.getAttribute('height') || 0;
      type = 'embed';
    }
    
    if (!src) return;
    
    if (!videos.some(v => v.src === src)) {
      videos.push({ src, type, width, height, poster });
    }
  });

  // Comprehensive audio selection
  document.querySelectorAll('audio, audio source, source[src$=".mp3"], source[src$=".wav"], source[src$=".ogg"], source[src$=".flac"], source[src$=".aac"], source[src$=".m4a"], source[src$=".opus"], source[src$=".wma"]').forEach(el => {
    let src = '';
    let type = 'audio';

    if (el.tagName === 'AUDIO') {
      src = el.src || el.querySelector('source')?.src || '';
    } else if (el.tagName === 'SOURCE') {
      src = el.src;
    }

    if (!src) return;

    if (!audios.some(a => a.src === src)) {
      audios.push({ src, type });
    }
  });

  document.querySelectorAll('a[href$=".mp3"], a[href$=".wav"], a[href$=".ogg"], a[href$=".flac"], a[href$=".aac"], a[href$=".m4a"], a[href$=".opus"]').forEach(link => {
    const src = link.href;
    if (!audios.some(a => a.src === src)) {
      audios.push({ src, type: 'link' });
    }
  });
  
  document.querySelectorAll('a[href]').forEach(link => {
    const href = link.href;
    const text = link.innerText.trim();
    
    if (!href || href.startsWith('javascript:') || href.startsWith('#')) return;
    if (href.startsWith('mailto:') || href.startsWith('tel:')) return;
    
    if (!links.some(l => l.href === href)) {
      links.push({ href, text });
    }
  });
  
  const bodyClone = document.body.cloneNode(true);
  const unwanted = bodyClone.querySelectorAll('script, style, nav, header, footer, aside, .nav, .header, .footer, .sidebar, [role="navigation"]');
  unwanted.forEach(el => el.remove());
  
  const text = (bodyClone.innerText || bodyClone.textContent || '')
    .replace(/\s+/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
  
  return { images, videos, audios, links, text };
}

// Render images
function renderImages() {
  const textFilter = elements.imageFilter ? elements.imageFilter.value.toLowerCase() : '';
  let images = scrapedData.images;
  
  // Apply size filter based on currentImageFilter
  if (currentImageFilter === 'large') {
    images = images.filter(img => img.width >= 200 && img.height >= 200);
  } else if (currentImageFilter === 'xlarge') {
    images = images.filter(img => img.width >= 500 && img.height >= 500);
  } else if (currentImageFilter === 'domain' && images.length > 0) {
    // Get the most common domain
    const domainCounts = {};
    images.forEach(img => {
      try {
        const domain = new URL(img.src).hostname;
        domainCounts[domain] = (domainCounts[domain] || 0) + 1;
      } catch (e) {}
    });
    const topDomain = Object.entries(domainCounts).sort((a, b) => b[1] - a[1])[0]?.[0];
    if (topDomain) {
      images = images.filter(img => {
        try {
          return new URL(img.src).hostname === topDomain;
        } catch (e) {
          return false;
        }
      });
    }
  }
  
  // Apply text filter if provided
  if (textFilter) {
    images = images.filter(img => 
      (img.alt && img.alt.toLowerCase().includes(textFilter)) || 
      img.src.toLowerCase().includes(textFilter)
    );
  }
  
  elements.imagesCount.textContent = `${scrapedData.images.length} images found`;
  
  if (images.length === 0) {
    elements.imagesContainer.innerHTML = `
      <div class="empty-state">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1">
          <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
          <circle cx="8.5" cy="8.5" r="1.5"/>
          <polyline points="21 15 16 10 5 21"/>
        </svg>
        <p>No images found</p>
      </div>
    `;
    return;
  }
  
  const grid = document.createElement('div');
  grid.className = 'image-grid';
  
  images.forEach((img, index) => {
    const item = document.createElement('div');
    item.className = 'image-item';
    if (selectedImages.has(img.src)) item.classList.add('selected');

    const checkbox = document.createElement('div');
    checkbox.className = 'image-checkbox';
    checkbox.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>';
    checkbox.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleImageSelection(img.src, item);
    });
    item.appendChild(checkbox);

    const imgEl = document.createElement('img');
    imgEl.src = img.src;
    imgEl.alt = img.alt || 'Image ' + (index + 1);
    imgEl.loading = 'lazy';
    imgEl.addEventListener('click', (e) => {
      if (e.shiftKey || selectedImages.size > 0) {
        e.preventDefault();
        toggleImageSelection(img.src, item);
      } else {
        window.open(img.src, '_blank');
      }
    });
    item.appendChild(imgEl);

    const overlay = document.createElement('div');
    overlay.className = 'image-overlay';
    const sizeSpan = document.createElement('span');
    sizeSpan.className = 'image-size';
    sizeSpan.textContent = `${img.width}×${img.height}`;
    overlay.appendChild(sizeSpan);
    item.appendChild(overlay);
    grid.appendChild(item);
  });
  
  elements.imagesContainer.innerHTML = '';
  elements.imagesContainer.appendChild(grid);
}

// Render links
function renderLinks() {
  const filter = elements.linkFilter ? elements.linkFilter.value.toLowerCase() : '';
  let links = scrapedData.links;
  
  // Apply filter if provided
  if (filter) {
    links = links.filter(link => 
      (link.text && link.text.toLowerCase().includes(filter)) || 
      link.href.toLowerCase().includes(filter)
    );
  }
  
  elements.linksCount.textContent = `${scrapedData.links.length} links found`;
  
  if (links.length === 0) {
    elements.linksContainer.innerHTML = `
      <div class="empty-state">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1">
          <path d="M10 6H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/>
          <path d="M14 10l8-8"/>
          <path d="M14 2v6h6"/>
        </svg>
        <p>No links found</p>
      </div>
    `;
    return;
  }
  
  const list = document.createElement('div');
  list.className = 'links-list';
  
  links.forEach(link => {
    const item = document.createElement('div');
    item.className = 'link-item';

    const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    icon.classList.add('link-icon');
    icon.setAttribute('width', '16');
    icon.setAttribute('height', '16');
    icon.setAttribute('viewBox', '0 0 24 24');
    icon.setAttribute('fill', 'none');
    icon.setAttribute('stroke', 'currentColor');
    icon.setAttribute('stroke-width', '2');
    icon.innerHTML = `
      <path d="M10 6H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/>
      <path d="M14 10l8-8"/>
      <path d="M14 2v6h6"/>
    `;
    item.appendChild(icon);

    const textSpan = document.createElement('span');
    textSpan.className = 'link-text';
    textSpan.title = link.href;
    textSpan.textContent = link.text || link.href;
    item.appendChild(textSpan);

    item.addEventListener('click', () => window.open(link.href, '_blank'));
    list.appendChild(item);
  });
  
  elements.linksContainer.innerHTML = '';
  elements.linksContainer.appendChild(list);
}

// Render videos
function renderVideos() {
  elements.videosCount.textContent = `${scrapedData.videos.length} videos found`;
  
  if (scrapedData.videos.length === 0) {
    elements.videosContainer.innerHTML = `
      <div class="empty-state">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1">
          <polygon points="23 7 16 12 23 17 23 7"/>
          <rect x="1" y="5" width="15" height="14" rx="2" ry="2"/>
        </svg>
        <p>No videos found</p>
      </div>
    `;
    return;
  }
  
  const grid = document.createElement('div');
  grid.className = 'video-grid';
  
  scrapedData.videos.forEach((video, index) => {
    const item = document.createElement('div');
    item.className = 'video-item';

    let videoPreviewEl = null;
    if (video.type === 'embed') {
      const embedUrl = video.src;
      const isYouTube = embedUrl.includes('youtube');
      const isVimeo = embedUrl.includes('vimeo');

      if (isYouTube) {
        const videoId = embedUrl.match(/[?&]v=([^&#]*)/) || embedUrl.match(/youtu\.be\/([^?&#]*)/);
        if (videoId) {
          videoPreviewEl = document.createElement('img');
          videoPreviewEl.src = `https://img.youtube.com/vi/${videoId[1]}/mqdefault.jpg`;
          videoPreviewEl.alt = 'Video thumbnail';
          videoPreviewEl.style.cursor = 'pointer';
          videoPreviewEl.addEventListener('click', () => window.open(embedUrl, '_blank'));
        }
      } else if (isVimeo) {
        const videoId = embedUrl.match(/vimeo\.com\/(\d+)/);
        if (videoId) {
          videoPreviewEl = document.createElement('img');
          videoPreviewEl.src = `https://vumbnail.com/${videoId[1]}.jpg`;
          videoPreviewEl.alt = 'Video thumbnail';
          videoPreviewEl.style.cursor = 'pointer';
          videoPreviewEl.addEventListener('click', () => window.open(embedUrl, '_blank'));
        }
      } else if (video.poster) {
        videoPreviewEl = document.createElement('img');
        videoPreviewEl.src = video.poster;
        videoPreviewEl.alt = 'Video thumbnail';
        videoPreviewEl.style.cursor = 'pointer';
        videoPreviewEl.addEventListener('click', () => window.open(embedUrl, '_blank'));
      }
    } else {
      videoPreviewEl = document.createElement('video');
      videoPreviewEl.src = video.src;
      videoPreviewEl.poster = video.poster || '';
      videoPreviewEl.preload = 'metadata';
      videoPreviewEl.style.cursor = 'pointer';
      videoPreviewEl.addEventListener('click', () => window.open(video.src, '_blank'));
    }

    const sizeInfo = video.width > 0 && video.height > 0 ? `<span class="video-size">${video.width}×${video.height}</span>` : '';
    const truncatedUrl = video.src.length > 40 ? video.src.substring(0, 40) + '...' : video.src;

    item.innerHTML = `
      <div class="video-wrapper">
        <div class="video-overlay">
          <span class="video-type">${video.type}</span>
          ${sizeInfo}
        </div>
      </div>
      <div class="video-info">
        <span class="video-url" title="${video.src}">${truncatedUrl}</span>
        <button class="copy-video-btn">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
          </svg>
        </button>
      </div>
    `;

    if (videoPreviewEl) {
      item.querySelector('.video-wrapper').insertBefore(videoPreviewEl, item.querySelector('.video-overlay'));
    }

    const copyBtn = item.querySelector('.copy-video-btn');
    copyBtn.addEventListener('click', () => {
      navigator.clipboard.writeText(video.src);
      showToast('Video URL copied!');
    });

    grid.appendChild(item);
  });
  
  elements.videosContainer.innerHTML = '';
  elements.videosContainer.appendChild(grid);
}

// Render audios
function renderAudios() {
  elements.audiosCount.textContent = `${scrapedData.audios.length} audio files found`;
  
  if (scrapedData.audios.length === 0) {
    elements.audiosContainer.innerHTML = `
      <div class="empty-state">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1">
          <path d="M9 18V5l12-2v13"/>
          <circle cx="6" cy="18" r="3"/>
          <circle cx="18" cy="16" r="3"/>
        </svg>
        <p>No audio files found</p>
      </div>
    `;
    return;
  }
  
  const list = document.createElement('div');
  list.className = 'audio-list';
  
  scrapedData.audios.forEach((audio, index) => {
    const item = document.createElement('div');
    item.className = 'audio-item';

    const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    icon.classList.add('audio-icon');
    icon.setAttribute('width', '20');
    icon.setAttribute('height', '20');
    icon.setAttribute('viewBox', '0 0 24 24');
    icon.setAttribute('fill', 'none');
    icon.setAttribute('stroke', 'currentColor');
    icon.setAttribute('stroke-width', '2');
    icon.innerHTML = `
      <path d="M9 18V5l12-2v13"/>
      <circle cx="6" cy="18" r="3"/>
      <circle cx="18" cy="16" r="3"/>
    `;

    const info = document.createElement('div');
    info.className = 'audio-info';

    const nameSpan = document.createElement('span');
    nameSpan.className = 'audio-name';
    nameSpan.title = audio.src;
    try {
      const url = new URL(audio.src);
      const pathParts = url.pathname.split('/');
      nameSpan.textContent = pathParts[pathParts.length - 1] || audio.src;
    } catch (e) {
      nameSpan.textContent = audio.src.length > 40 ? audio.src.substring(0, 40) + '...' : audio.src;
    }

    const typeSpan = document.createElement('span');
    typeSpan.className = 'audio-type';
    typeSpan.textContent = audio.type;

    info.appendChild(nameSpan);
    info.appendChild(typeSpan);

    const actions = document.createElement('div');
    actions.className = 'audio-actions';

    const playBtn = document.createElement('button');
    playBtn.className = 'audio-play-btn';
    playBtn.innerHTML = `
      <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none">
        <polygon points="5 3 19 12 5 21 5 3"/>
      </svg>
    `;
    playBtn.addEventListener('click', () => {
      const audioEl = new Audio(audio.src);
      audioEl.play().catch(() => showToast('Could not play audio'));
    });

    const copyBtn = document.createElement('button');
    copyBtn.className = 'copy-audio-btn';
    copyBtn.innerHTML = `
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
      </svg>
    `;
    copyBtn.addEventListener('click', () => {
      navigator.clipboard.writeText(audio.src);
      showToast('Audio URL copied!');
    });

    actions.appendChild(playBtn);
    actions.appendChild(copyBtn);

    item.appendChild(icon);
    item.appendChild(info);
    item.appendChild(actions);
    list.appendChild(item);
  });
  
  elements.audiosContainer.innerHTML = '';
  elements.audiosContainer.appendChild(list);
}

// Render text
function renderText() {
  const wordCount = scrapedData.text.split(/\s+/).filter(w => w.length > 0).length;
  elements.textResultsCount.textContent = `${wordCount} words found`;
  
  if (!scrapedData.text || scrapedData.text.trim().length === 0) {
    elements.textContainer.innerHTML = `
      <div class="empty-state">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1">
          <line x1="17" y1="10" x2="3" y2="10"/>
          <line x1="21" y1="6" x2="3" y2="6"/>
          <line x1="21" y1="14" x2="3" y2="14"/>
          <line x1="17" y1="18" x2="3" y2="18"/>
        </svg>
        <p>No text content</p>
      </div>
    `;
    return;
  }
  
  elements.textContainer.innerHTML = '';
  const textDiv = document.createElement('div');
  textDiv.className = 'text-content';
  textDiv.textContent = scrapedData.text;
  elements.textContainer.appendChild(textDiv);
}

// Copy all data
async function copyAllData() {
  const data = {
    images: scrapedData.images.map(img => img.src),
    videos: scrapedData.videos.map(video => video.src),
    audios: scrapedData.audios.map(audio => audio.src),
    links: scrapedData.links.map(link => ({ text: link.text, href: link.href })),
    text: scrapedData.text
  };
  
  try {
    await navigator.clipboard.writeText(JSON.stringify(data, null, 2));
    showToast('All data copied to clipboard!');
  } catch (error) {
    console.error('Copy error:', error);
    showToast('Failed to copy data');
  }
}

// Copy specific tab data
async function copyTabData(type) {
  let data = '';
  
  switch (type) {
    case 'images':
      data = scrapedData.images.map(img => img.src).join('\n');
      break;
    case 'videos':
      data = scrapedData.videos.map(video => video.src).join('\n');
      break;
    case 'audios':
      data = scrapedData.audios.map(audio => audio.src).join('\n');
      break;
    case 'links':
      data = scrapedData.links.map(link => `${link.text || link.href}: ${link.href}`).join('\n');
      break;
    case 'text':
      data = scrapedData.text;
      break;
  }
  
  try {
    await navigator.clipboard.writeText(data);
    showToast(`${type.charAt(0).toUpperCase() + type.slice(1)} copied to clipboard!`);
  } catch (error) {
    console.error('Copy error:', error);
    showToast('Failed to copy data');
  }
}

// Export as JSON
async function exportJson() {
  const data = {
    url: elements.currentPage.textContent,
    timestamp: new Date().toISOString(),
    ...scrapedData
  };
  
  const json = JSON.stringify(data, null, 2);
  const blob = new Blob([json], { type: 'application/json;charset=utf-8' });
  const blobUrl = URL.createObjectURL(blob);
  
  chrome.downloads.download({
    url: blobUrl,
    filename: `scraped-data-${Date.now()}.json`,
    saveAs: false
  }, (downloadId) => {
    if (chrome.runtime.lastError) {
      URL.revokeObjectURL(blobUrl);
      console.error('Export error:', chrome.runtime.lastError);
      showToast('Export failed: ' + chrome.runtime.lastError.message);
    } else {
      setTimeout(() => URL.revokeObjectURL(blobUrl), 60000);
      showToast('Data exported as JSON!');
    }
  });
}

// Download videos individually
async function exportVideos() {
  if (scrapedData.videos.length === 0) {
    showToast('No videos to download');
    return;
  }
  
  showToast(`Downloading ${scrapedData.videos.length} videos...`);
  
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const baseUrl = tab.url;
    
    for (let i = 0; i < scrapedData.videos.length; i++) {
      const video = scrapedData.videos[i];
      let videoUrl = video.src;
      
      // Skip embedded videos (YouTube, Vimeo, etc.) - can't download those directly
      if (video.type === 'embed') {
        continue;
      }
      
      // Resolve relative URLs
      if (!videoUrl.startsWith('http') && !videoUrl.startsWith('data:')) {
        try {
          videoUrl = new URL(videoUrl, baseUrl).href;
        } catch (e) {
          continue;
        }
      }
      
      // Skip if it's a data URL or invalid
      if (videoUrl.startsWith('data:')) {
        continue;
      }
      
      const filename = `video-${i + 1}.mp4`;
      
      chrome.downloads.download({
        url: videoUrl,
        filename: filename,
        saveAs: false
      });
      
      // Small delay to prevent overwhelming
      await new Promise(resolve => setTimeout(resolve, 200));
    }
    
    showToast(`${scrapedData.videos.length} videos downloaded!`);
  } catch (error) {
    console.error('Video download error:', error);
    showToast('Error downloading videos: ' + error.message);
  }
}

// Download audios individually
async function exportAudios() {
  if (scrapedData.audios.length === 0) {
    showToast('No audio files to download');
    return;
  }
  
  showToast(`Downloading ${scrapedData.audios.length} audio files...`);
  
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const baseUrl = tab.url;
    
    for (let i = 0; i < scrapedData.audios.length; i++) {
      const audio = scrapedData.audios[i];
      let audioUrl = audio.src;
      
      if (!audioUrl.startsWith('http') && !audioUrl.startsWith('data:')) {
        try {
          audioUrl = new URL(audioUrl, baseUrl).href;
        } catch (e) {
          continue;
        }
      }
      
      if (audioUrl.startsWith('data:')) {
        continue;
      }
      
      let ext = '.mp3';
      const lower = audioUrl.toLowerCase();
      if (lower.includes('.wav')) ext = '.wav';
      else if (lower.includes('.ogg')) ext = '.ogg';
      else if (lower.includes('.flac')) ext = '.flac';
      else if (lower.includes('.aac')) ext = '.aac';
      else if (lower.includes('.m4a')) ext = '.m4a';
      else if (lower.includes('.opus')) ext = '.opus';
      
      const filename = `audio-${i + 1}${ext}`;
      
      chrome.downloads.download({
        url: audioUrl,
        filename: filename,
        saveAs: false
      });
      
      await new Promise(resolve => setTimeout(resolve, 200));
    }
    
    showToast(`${scrapedData.audios.length} audio files downloaded!`);
  } catch (error) {
    console.error('Audio download error:', error);
    showToast('Error downloading audio: ' + error.message);
  }
}

// Download images individually
async function downloadImagesIndividually() {
  if (scrapedData.images.length === 0) {
    showToast('No images to download');
    return;
  }
  
  showToast(`Downloading ${scrapedData.images.length} images...`);
  
  try {
    for (let i = 0; i < scrapedData.images.length; i++) {
      const img = scrapedData.images[i];
      const filename = img.alt ? `${sanitizeFilename(img.alt.substring(0, 50))}.jpg` : `image-${i}.jpg`;
      
      chrome.downloads.download({
        url: img.src,
        filename: filename,
        saveAs: false
      });
      
      // Small delay to prevent overwhelming
      await new Promise(resolve => setTimeout(resolve, 200));
    }
    
    showToast(`${scrapedData.images.length} images downloaded!`);
  } catch (error) {
    console.error('Download error:', error);
    showToast('Error downloading images: ' + error.message);
  }
}

// Export images as a single ZIP file
async function exportArchive() {
  if (scrapedData.images.length === 0) {
    showToast('No images to download');
    return;
  }

  showToast(`Fetching ${scrapedData.images.length} images for ZIP...`);
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const baseUrl = tab?.url;

  const files = [];
  let failed = 0;
  const BATCH_SIZE = 5;

  async function fetchImageEntry(img, index) {
    let imageUrl = img.src;
    if (!imageUrl) {
      failed++;
      return;
    }

    if (!imageUrl.startsWith('http') && !imageUrl.startsWith('data:') && baseUrl) {
      try {
        imageUrl = new URL(imageUrl, baseUrl).href;
      } catch (e) {
        failed++;
        return;
      }
    }

    if (imageUrl.includes('preview_image.jpg') || imageUrl.includes('blank.gif')) {
      failed++;
      return;
    }

    try {
      const { bytes, mime } = await fetchImageAsBytes(imageUrl);
      let ext = '.jpg';
      const lowerUrl = imageUrl.toLowerCase();
      if (lowerUrl.includes('.png') || mime === 'image/png') ext = '.png';
      else if (lowerUrl.includes('.gif') || mime === 'image/gif') ext = '.gif';
      else if (lowerUrl.includes('.webp') || mime === 'image/webp') ext = '.webp';
      else if (lowerUrl.includes('.svg') || mime === 'image/svg+xml') ext = '.svg';
      else if (lowerUrl.includes('.bmp') || mime === 'image/bmp') ext = '.bmp';

      const filenameBase = img.alt ? sanitizeFilename(img.alt.substring(0, 50)) : `image-${index + 1}`;
      const filename = `${filenameBase}${ext}`;
      files.push({ name: filename, data: bytes, crc: calculateCRC32(bytes) });
    } catch (e) {
      console.warn('Failed to fetch image for ZIP:', imageUrl, e);
      failed++;
    }
  }

  for (let i = 0; i < scrapedData.images.length; i += BATCH_SIZE) {
    const batch = scrapedData.images.slice(i, i + BATCH_SIZE);
    await Promise.all(batch.map((img, batchIndex) => fetchImageEntry(img, i + batchIndex)));
    showToast(`Fetching images... ${Math.min(i + BATCH_SIZE, scrapedData.images.length)}/${scrapedData.images.length}`);
  }

  if (files.length === 0) {
    showToast('Could not fetch any images for ZIP (CORS/network). Falling back to individual downloads.');
    // Fallback: start individual downloads via background (more likely to succeed)
    try {
      for (let i = 0; i < scrapedData.images.length; i++) {
        const img = scrapedData.images[i];
        let imageUrl = img.src;
        if (imageUrl && !imageUrl.startsWith('http') && !imageUrl.startsWith('data:') && baseUrl) {
          try { imageUrl = new URL(imageUrl, baseUrl).href; } catch (e) { continue; }
        }
        const filename = img.alt ? `${sanitizeFilename(img.alt.substring(0,50))}.jpg` : `image-${i+1}.jpg`;
        chrome.runtime.sendMessage({ action: 'downloadImage', url: imageUrl, filename });
        await new Promise(r => setTimeout(r, 150));
      }
      showToast('Started individual downloads as fallback');
    } catch (e) {
      console.error('Fallback downloads failed:', e);
      showToast('Fallback downloads failed');
    }
    return;
  }

  showToast(`Creating ZIP with ${files.length} images...`);

  try {
    const zipBlob = buildZip(files);
    const blobUrl = URL.createObjectURL(zipBlob);
    const dateStr = new Date().toISOString().slice(0, 10);
    chrome.downloads.download({
      url: blobUrl,
      filename: `scraped-images-${dateStr}.zip`,
      saveAs: false
    }, (downloadId) => {
      if (chrome.runtime.lastError) {
        console.error('ZIP download failed:', chrome.runtime.lastError);
        showToast('ZIP download failed: ' + chrome.runtime.lastError.message);
        URL.revokeObjectURL(blobUrl);
      } else {
        showToast(`ZIP created with ${files.length} images!`);
        setTimeout(() => URL.revokeObjectURL(blobUrl), 60000);
      }
    });
  } catch (error) {
    console.error('ZIP creation failed:', error);
    showToast('Error creating ZIP: ' + error.message);
  }
}

function fetchImageAsBytes(url) {
  return new Promise((resolve, reject) => {
    if (!url) {
      return reject(new Error('No URL provided'));
    }

    if (url.startsWith('data:')) {
      const match = url.match(/^data:([^;]+);base64,(.+)$/);
      if (!match) {
        return reject(new Error('Invalid data URL'));
      }
      const mime = match[1];
      const binary = atob(match[2]);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) {
        bytes[i] = binary.charCodeAt(i);
      }
      return resolve({ bytes, mime });
    }

    const xhr = new XMLHttpRequest();
    xhr.open('GET', url, true);
    xhr.responseType = 'arraybuffer';
    xhr.timeout = 15000;

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        const mime = xhr.getResponseHeader('content-type') || 'image/jpeg';
        resolve({ bytes: new Uint8Array(xhr.response), mime });
      } else {
        reject(new Error('HTTP ' + xhr.status));
      }
    };

    xhr.onerror = () => reject(new Error('Network error'));
    xhr.ontimeout = () => reject(new Error('Request timed out'));
    xhr.send();
  });
}

function buildZip(entries) {
  const parts = [];
  const offsets = [];
  let offset = 0;

  for (const entry of entries) {
    offsets.push(offset);
    const nameBytes = new TextEncoder().encode(entry.name);
    const header = new Uint8Array(30);
    const dv = new DataView(header.buffer);
    dv.setUint32(0, 0x04034b50, true);
    dv.setUint16(4, 20, true);
    dv.setUint16(6, 0, true);
    dv.setUint16(8, 0, true);
    dv.setUint16(10, 0, true);
    dv.setUint16(12, 0, true);
    dv.setUint32(14, entry.crc, true);
    dv.setUint32(18, entry.data.length, true);
    dv.setUint32(22, entry.data.length, true);
    dv.setUint16(26, nameBytes.length, true);
    dv.setUint16(28, 0, true);

    parts.push(header);
    parts.push(nameBytes);
    parts.push(entry.data);
    offset += header.length + nameBytes.length + entry.data.length;
  }

  const cdStart = offset;
  let cdSize = 0;

  for (let i = 0; i < entries.length; i++) {
    const entry = entries[i];
    const nameBytes = new TextEncoder().encode(entry.name);
    const cdHeader = new Uint8Array(46);
    const dv = new DataView(cdHeader.buffer);
    dv.setUint32(0, 0x02014b50, true);
    dv.setUint16(4, 20, true);
    dv.setUint16(6, 20, true);
    dv.setUint16(8, 0, true);
    dv.setUint16(10, 0, true);
    dv.setUint16(12, 0, true);
    dv.setUint16(14, 0, true);
    dv.setUint32(16, entry.crc, true);
    dv.setUint32(20, entry.data.length, true);
    dv.setUint32(24, entry.data.length, true);
    dv.setUint16(28, nameBytes.length, true);
    dv.setUint16(30, 0, true);
    dv.setUint16(32, 0, true);
    dv.setUint16(34, 0, true);
    dv.setUint16(36, 0, true);
    dv.setUint32(38, 0, true);
    dv.setUint32(42, offsets[i], true);

    parts.push(cdHeader);
    parts.push(nameBytes);
    offset += cdHeader.length + nameBytes.length;
    cdSize += cdHeader.length + nameBytes.length;
  }

  const eocd = new Uint8Array(22);
  const dv2 = new DataView(eocd.buffer);
  dv2.setUint32(0, 0x06054b50, true);
  dv2.setUint16(4, 0, true);
  dv2.setUint16(6, 0, true);
  dv2.setUint16(8, entries.length, true);
  dv2.setUint16(10, entries.length, true);
  dv2.setUint32(12, cdSize, true);
  dv2.setUint32(16, cdStart, true);
  dv2.setUint16(20, 0, true);

  parts.push(eocd);

  let totalSize = 0;
  for (const part of parts) totalSize += part.length;
  const zipBytes = new Uint8Array(totalSize);
  let position = 0;
  for (const part of parts) {
    zipBytes.set(part, position);
    position += part.length;
  }

  return new Blob([zipBytes], { type: 'application/zip' });
}

// Copy all image URLs to clipboard
function copyImageURLs() {
  if (scrapedData.images.length === 0) {
    showToast('No images to copy');
    return;
  }
  
  const urls = scrapedData.images.map(img => img.src).join('\n');
  navigator.clipboard.writeText(urls).then(() => {
    showToast(`${scrapedData.images.length} image URLs copied!`);
  }).catch(err => {
    console.error('Copy failed:', err);
    showToast('Copy failed');
  });
}

// Export images with option
async function exportWithImages() {
  if (scrapedData.images.length === 0) {
    showToast('No images to export');
    return;
  }
  
  const choice = confirm('Download individual images or create ZIP?\n\nOK = Individual download\nCancel = Create ZIP file');
  if (choice) {
    downloadImagesIndividually();
  } else {
    // Create ZIP in-popup (more reliable and avoids many separate downloads)
    exportArchive();
  }
}

// Export as ZIP using chrome.downloads API with background
async function exportAsZip() {
  if (scrapedData.images.length === 0) {
    showToast('No images to archive');
    return;
  }
  
  showToast('Preparing image URLs for ZIP...');
  
  // Send to background for download
  chrome.runtime.sendMessage({
    action: 'createZipFromUrls',
    urls: scrapedData.images.map(img => img.src),
    names: scrapedData.images.map(img => img.alt || 'image'),
    count: scrapedData.images.length
  });
  
  showToast('Creating ZIP in background...');
}

function sanitizeFilename(name) {
  return name.replace(/[^a-z0-9._-]/gi, '_').substring(0, 100);
}

// Create a proper ZIP archive - simpler implementation
async function createZipArchive(files) {
  // Collect file data first
  const entries = [];
  for (const file of files) {
    let filename = file.name;
    const ext = file.ext || '.jpg';
    if (!filename.toLowerCase().match(/\.(jpg|jpeg|png|gif|webp|bmp)$/i)) {
      filename = filename + ext;
    }
    
    const data = await file.blob.arrayBuffer();
    entries.push({
      name: filename,
      data: new Uint8Array(data),
      crc: calculateCRC32(new Uint8Array(data))
    });
  }
  
  // Use a simpler approach - concatenate all parts
  const parts = [];
  
  // Local file headers + data
  const localHeaderOffsets = [];
  let offset = 0;
  
  for (const entry of entries) {
    localHeaderOffsets.push(offset);
    
    // 30-byte local header
    const header = new Uint8Array(30);
    const dv = new DataView(header.buffer);
    dv.setUint32(0, 0x04034b50, true); // signature
    dv.setUint16(4, 20, true); // version
    dv.setUint16(6, 0, true); // flags
    dv.setUint16(8, 0, true); // compression
    dv.setUint16(10, 0, true); // mod time
    dv.setUint16(12, 0, true); // mod date
    dv.setUint32(14, entry.crc, true); // crc
    dv.setUint32(18, entry.data.length, true); // compressed size
    dv.setUint32(22, entry.data.length, true); // uncompressed size
    dv.setUint16(26, entry.name.length, true); // name length
    dv.setUint16(28, 0, true); // extra length
    parts.push(header);
    
    // filename
    const nameBytes = new TextEncoder().encode(entry.name);
    parts.push(nameBytes);
    
    // data
    parts.push(entry.data);
    
    offset += 30 + nameBytes.length + entry.data.length;
  }
  
  const cdOffset = offset;
  
  // Central directory
  let cdSize = 0;
  for (let i = 0; i < entries.length; i++) {
    const entry = entries[i];
    const nameBytes = new TextEncoder().encode(entry.name);
    cdSize += 46 + nameBytes.length;
    
    const cdHeader = new Uint8Array(46);
    const dv = new DataView(cdHeader.buffer);
    dv.setUint32(0, 0x02014b50, true); // signature
    dv.setUint16(4, 20, true); // version made by
    dv.setUint16(6, 20, true); // version needed
    dv.setUint16(8, 0, true); // flags
    dv.setUint16(10, 0, true); // compression
    dv.setUint16(12, 0, true); // mod time
    dv.setUint16(14, 0, true); // mod date
    dv.setUint32(16, entry.crc, true); // crc
    dv.setUint32(20, entry.data.length, true); // compressed size
    dv.setUint32(24, entry.data.length, true); // uncompressed size
    dv.setUint16(28, nameBytes.length, true); // name length
    dv.setUint16(30, 0, true); // extra length
    dv.setUint16(32, 0, true); // comment length
    dv.setUint16(34, 0, true); // disk number
    dv.setUint16(36, 0, true); // internal attributes
    dv.setUint32(40, 0, true); // external attributes
    dv.setUint32(42, localHeaderOffsets[i], true); // relative offset
    parts.push(cdHeader);
    parts.push(nameBytes);
  }
  
  // EOCD
  const eocd = new Uint8Array(22);
  const dv = new DataView(eocd.buffer);
  dv.setUint32(0, 0x06054b50, true); // signature
  dv.setUint16(4, 0, true); // disk number
  dv.setUint16(6, 0, true); // disk with cd
  dv.setUint16(8, entries.length, true); // entries on disk
  dv.setUint16(10, entries.length, true); // total entries
  dv.setUint32(12, cdSize, true); // cd size
  dv.setUint32(16, cdOffset, true); // cd offset
  dv.setUint16(20, 0, true); // comment length
  parts.push(eocd);
  
  // Calculate total size
  let totalSize = 0;
  for (const p of parts) totalSize += p.length;
  
  // Concatenate
  const result = new Uint8Array(totalSize);
  let pos = 0;
  for (const p of parts) {
    result.set(p, pos);
    pos += p.length;
  }
  
  return new Blob([result], { type: 'application/zip' });
}

// Simple CRC32 calculation
function calculateCRC32(data) {
  const crcTable = new Uint32Array(256);
  for (let i = 0; i < 256; i++) {
    let c = i;
    for (let j = 0; j < 8; j++) {
      c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
    }
    crcTable[i] = c;
  }
  
  let crc = 0xFFFFFFFF;
  for (const byte of data) {
    crc = crcTable[(crc ^ byte) & 0xFF] ^ (crc >>> 8);
  }
  return (crc ^ 0xFFFFFFFF) >>> 0;
}

// Show toast notification
function showToast(message) {
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.textContent = message;
  document.body.appendChild(toast);
  
  setTimeout(() => {
    toast.remove();
  }, 3000);
}

// Detect if running as a side panel vs popup and apply appropriate mode
// Side panels have large outerWidth (fills sidebar), popups are typically < 400px wide
function detectSidebarMode() {
  // Check if we're in a side panel by examining window dimensions
  // Side panels are typically > 400px wide, popups are usually smaller
  const isWideWindow = window.outerWidth >= 400;
  
  if (!isWideWindow) {
    // This is a popup - hide sidebar button, allow popup-specific sizing
    document.body.classList.remove('sidebar-mode');
    document.body.classList.add('popup-mode');
    if (elements.sidebarBtn) {
      elements.sidebarBtn.style.display = 'none';
    }
    // Hide detachable button in popup mode
    const detachBtn = document.getElementById('detachBtn');
    if (detachBtn) {
      detachBtn.style.display = 'none';
    }
  } else {
    // This is likely a side panel or wider context
    document.body.classList.add('sidebar-mode');
    document.body.classList.remove('popup-mode');
    // Hide popup-specific button in sidebar
    if (elements.sidebarBtn) {
      elements.sidebarBtn.style.display = 'none';
    }
    const detachBtn = document.getElementById('detachBtn');
    if (detachBtn) {
      detachBtn.style.display = 'flex';
    }
  }
}

// Open in sidebar (for popup mode only)
async function openInSidebar() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab && chrome.sidePanel) {
      await chrome.sidePanel.open({ windowId: tab.windowId });
      window.close();
    }
  } catch (err) {
    showToast('Could not open sidebar');
  }
}

// Detach from sidebar to popup (for sidebar mode only)
function detachToPopup() {
  // In sidebar, we can't truly "detach" but we can provide a helpful message
  // or open the popup in a new window if supported
  showToast('Close sidebar, then click extension icon for popup');
  // Alternative: use chrome.windows.create to open popup.html in a small window
  chrome.windows.create({
    url: chrome.runtime.getURL('popup.html'),
    type: 'popup',
    width: 540,
    height: 700
  }, (win) => {
    if (chrome.runtime.lastError) {
      console.error('Window creation failed:', chrome.runtime.lastError);
    }
  });
}

// Open settings modal
// Toggle image selection
function toggleImageSelection(src, itemEl) {
  if (selectedImages.has(src)) {
    selectedImages.delete(src);
    itemEl.classList.remove('selected');
  } else {
    selectedImages.add(src);
    itemEl.classList.add('selected');
  }
  updateSelectionToolbar();
}

// Update selection toolbar visibility and count
function updateSelectionToolbar() {
  const toolbar = document.getElementById('selectionToolbar');
  const info = document.getElementById('selectionInfo');
  if (!toolbar || !info) return;

  if (selectedImages.size > 0) {
    toolbar.classList.add('visible');
    info.textContent = `${selectedImages.size} selected`;
  } else {
    toolbar.classList.remove('visible');
  }
}

// Select all visible images
function selectAllImages() {
  const items = document.querySelectorAll('#imagesContainer .image-item');
  items.forEach(item => {
    const img = item.querySelector('img');
    if (img && img.src) {
      selectedImages.add(img.src);
      item.classList.add('selected');
    }
  });
  updateSelectionToolbar();
}

// Clear selection
function clearSelection() {
  selectedImages.clear();
  document.querySelectorAll('#imagesContainer .image-item.selected').forEach(item => {
    item.classList.remove('selected');
  });
  updateSelectionToolbar();
  renderDownloadPanel();
}

// Open download panel
function openDownloadPanel() {
  const panel = document.getElementById('downloadPanel');
  if (panel) {
    panel.classList.add('open');
    renderDownloadPanel();
  }
}

// Close download panel
function closeDownloadPanel() {
  const panel = document.getElementById('downloadPanel');
  if (panel) panel.classList.remove('open');
}

// Render download panel list
function renderDownloadPanel() {
  const list = document.getElementById('downloadPanelList');
  if (!list) return;

  if (selectedImages.size === 0) {
    list.innerHTML = '<div class="empty-state"><p>No items selected</p></div>';
    return;
  }

  list.innerHTML = '';
  selectedImages.forEach(src => {
    const item = document.createElement('div');
    item.className = 'download-item';

    const thumb = document.createElement('img');
    thumb.className = 'download-item-thumb';
    thumb.src = src;
    thumb.loading = 'lazy';
    item.appendChild(thumb);

    const info = document.createElement('div');
    info.className = 'download-item-info';
    const name = document.createElement('div');
    name.className = 'download-item-name';
    try {
      name.textContent = new URL(src).pathname.split('/').pop() || src;
    } catch {
      name.textContent = src.substring(0, 40);
    }
    info.appendChild(name);
    item.appendChild(info);

    const removeBtn = document.createElement('button');
    removeBtn.className = 'download-item-remove';
    removeBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
    removeBtn.addEventListener('click', () => {
      selectedImages.delete(src);
      const gridItem = document.querySelector(`.image-item img[src="${src}"]`)?.closest('.image-item');
      if (gridItem) gridItem.classList.remove('selected');
      updateSelectionToolbar();
      renderDownloadPanel();
    });
    item.appendChild(removeBtn);

    list.appendChild(item);
  });
}

// Download selected images
async function downloadSelectedImages() {
  if (selectedImages.size === 0) return;

  const urls = Array.from(selectedImages);
  try {
    const response = await chrome.runtime.sendMessage({
      action: 'downloadImages',
      images: urls
    });
    if (response?.success) {
      showToast(`Downloading ${urls.length} images`);
    } else {
      showToast('Download started');
    }
  } catch {
    urls.forEach(url => window.open(url, '_blank'));
    showToast(`Opening ${urls.length} images`);
  }
}

function openSettings() {
  if (document.querySelector('.settings-modal')) return;

  const modal = document.createElement('div');
  modal.className = 'settings-modal';
  modal.innerHTML = `
    <div class="settings-content">
      <div class="settings-header">
        <h2>Settings</h2>
        <button class="close-settings">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18"/>
            <line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        </button>
      </div>
      <div class="settings-option">
        <label>
          <input type="checkbox" id="minSizeCheck" ${settings.minSize ? 'checked' : ''}>
          Filter small images (min size)
        </label>
        <input type="number" id="minWidth" placeholder="Min width (px)" value="${settings.minWidth || 100}">
        <input type="number" id="minHeight" placeholder="Min height (px)" value="${settings.minHeight || 100}">
      </div>
      <div class="settings-option">
        <label>
          <input type="checkbox" id="excludeNav" ${settings.excludeNav ? 'checked' : ''}>
          Exclude navigation links
        </label>
      </div>
      <button class="btn btn-primary save-settings-btn" style="width: 100%; margin-top: 10px;">Save Settings</button>
    </div>
  `;
  document.body.appendChild(modal);

  modal.querySelector('.close-settings').addEventListener('click', () => modal.remove());
  modal.querySelector('.save-settings-btn').addEventListener('click', saveSettings);
}

function loadSettings() {
  chrome.storage.local.get('scraperSettings', (data) => {
    if (data.scraperSettings) {
      settings = { ...settings, ...data.scraperSettings };
    }
  });
}

function saveSettings() {
  settings = {
    minSize: document.getElementById('minSizeCheck').checked,
    minWidth: parseInt(document.getElementById('minWidth').value) || 100,
    minHeight: parseInt(document.getElementById('minHeight').value) || 100,
    excludeNav: document.getElementById('excludeNav').checked
  };
  
  chrome.storage.local.set({ scraperSettings: settings });
  showToast('Settings saved!');
  document.querySelector('.settings-modal').remove();
}