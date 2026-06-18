// Content script for Page Scraper Pro

// Scrape page function - called by popup
window.scrapePage = function scrapePage() {
  const images = [];
  const videos = [];
  const audios = [];
  const links = [];
  let text = '';
  
  // Scrape images - get actual image src from various sources
  document.querySelectorAll('img[src], img[data-src], img[data-lazy], source[src]').forEach(img => {
    let src = img.src || img.dataset.src || img.dataset.lazy || img.dataset.original;
    if (!src && img.tagName === 'source') {
      src = img.src;
    }
    
    if (!src) return;
    
    // Skip very small images and data URIs (unless they're small icons)
    if (src.startsWith('data:')) {
      // Only include small data URIs that are likely to be meaningful
      if (src.length > 50000) return; // Skip large base64 images
    }
    
    const alt = img.alt || img.title || '';
    const width = img.naturalWidth || img.width || 0;
    const height = img.naturalHeight || img.height || 0;
    
    // Skip very small images
    if (width < 10 || height < 10) return;
    
    // Skip duplicate images
    if (!images.some(i => i.src === src)) {
      images.push({
        src: src,
        alt: alt,
        width: width,
        height: height
      });
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
      // Handle <video> elements
      src = video.src || video.querySelector('source')?.src || '';
      width = video.videoWidth || video.width || video.getAttribute('width') || 0;
      height = video.videoHeight || video.height || video.getAttribute('height') || 0;
      poster = video.poster || '';
      type = 'video';
    } else if (video.tagName === 'SOURCE') {
      // Handle <source> elements
      src = video.src;
      const parentVideo = video.closest('video');
      if (parentVideo) {
        width = parentVideo.videoWidth || parentVideo.width || 0;
        height = parentVideo.videoHeight || parentVideo.height || 0;
        poster = parentVideo.poster || '';
      }
      type = 'video';
    } else if (video.tagName === 'IFRAME') {
      // Handle embedded videos (YouTube, Vimeo, etc.)
      src = video.src;
      width = video.width || video.getAttribute('width') || 0;
      height = video.height || video.getAttribute('height') || 0;
      type = 'embed';
    }
    
    if (!src) return;
    
    // Skip duplicate videos
    if (!videos.some(v => v.src === src)) {
      videos.push({
        src: src,
        type: type,
        width: width,
        height: height,
        poster: poster
      });
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
  const linkElements = document.querySelectorAll('a[href]');
  linkElements.forEach(link => {
    const href = link.href;
    const text = link.innerText.trim();
    
    // Skip empty links, javascript links, and anchors
    if (!href || href.startsWith('javascript:') || href.startsWith('#')) return;
    if (href.startsWith('mailto:') || href.startsWith('tel:')) return;
    
    // Skip duplicate links
    if (!links.some(l => l.href === href)) {
      links.push({
        href: href,
        text: text.substring(0, 100) // Limit text length
      });
    }
  });
  
  // Scrape text content
  // Clone body to avoid modifying the page
  const bodyClone = document.body.cloneNode(true);
  
  // Remove script and style elements
  const unwanted = bodyClone.querySelectorAll('script, style, nav, header, footer, aside, .nav, .header, .footer, .sidebar, [role="navigation"]');
  unwanted.forEach(el => el.remove());
  
  // Get text content
  text = bodyClone.innerText || bodyClone.textContent || '';
  
  // Clean up text
  text = text
    .replace(/\s+/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
  
  return {
    images: images,
    videos: videos,
    audios: audios,
    links: links,
    text: text
  };
};

// Message listener for background script
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'scrapePage') {
    sendResponse(scrapePage());
  }
  
  if (request.action === 'copyToClipboard') {
    navigator.clipboard.writeText(request.text).then(() => {
      // Success - no response needed
    }).catch(err => {
      console.error('Clipboard write failed:', err);
    });
    return true; // Keep message channel open for async response
  }
});

// Highlight images on hover when extension is active
let highlighted = false;

function toggleHighlight() {
  const images = document.querySelectorAll('img[src]');
  
  images.forEach(img => {
    if (!highlighted) {
      img.style.outline = '2px solid #6366f1';
      img.style.outlineOffset = '2px';
      img.title = `Click to scrape: ${img.src.substring(0, 50)}...`;
    } else {
      img.style.outline = '';
      img.style.outlineOffset = '';
      img.title = '';
    }
  });
  
  highlighted = !highlighted;
}

// Add keyboard shortcut hint
const hint = document.createElement('div');
hint.id = 'scraper-hint';
hint.style.cssText = `
  position: fixed;
  bottom: 20px;
  right: 20px;
  background: rgba(15, 23, 42, 0.9);
  color: #f8fafc;
  padding: 12px 16px;
  border-radius: 8px;
  font-size: 13px;
  z-index: 10000;
  border: 1px solid #334155;
  backdrop-filter: blur(10px);
  display: none;
`;
hint.innerHTML = `
  <div style="display: flex; align-items: center; gap: 8px;">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#6366f1" stroke-width="2">
      <path d="M21 16V8a2 2 0 0 0-1-1.73l-8-5a2 2 0 0 0-2 0l-8 5a2 2 0 0 0-1 1.73v8a2 2 0 0 0 1 1.73l8 5a2 2 0 0 0 2 0l8-5a2 2 0 0 0 1-1.73z"/>
      <polyline points="7.5 12 12 16 16.5 12"/>
    </svg>
    <span>Page Scraper Pro Active</span>
  </div>
`;
document.body.appendChild(hint);

// Show hint on certain pages
if (window.location.hostname !== 'chrome://newtab') {
  setTimeout(() => {
    hint.style.display = 'block';
    setTimeout(() => {
      hint.style.display = 'none';
    }, 3000);
  }, 1000);
}