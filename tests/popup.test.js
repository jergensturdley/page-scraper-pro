/**
 * Test suite for Page Scraper Pro popup UI bug fixes
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const stylesPath = path.join(__dirname, '..', 'styles.css');
const popupJsPath = path.join(__dirname, '..', 'popup.js');
const popupHtmlPath = path.join(__dirname, '..', 'popup.html');
const backgroundJsPath = path.join(__dirname, '..', 'background.js');
const manifestPath = path.join(__dirname, '..', 'manifest.json');

function loadPopupHelpers() {
  const popupJs = fs.readFileSync(popupJsPath, 'utf8');
  const domElementsIndex = popupJs.indexOf('// DOM Elements');
  const helperSource = domElementsIndex === -1 ? popupJs : popupJs.slice(0, domElementsIndex);
  const context = { URL };

  vm.runInNewContext(`${helperSource}
this.popupHelpers = { getVideoProviderId, normalizeVideoUrl, buildExportData, classifyImageCandidate };`, context);
  return context.popupHelpers;
}

function getRule(css, selector) {
  const blocks = css.split('}');
  for (let block of blocks) {
    const braceIdx = block.indexOf('{');
    if (braceIdx === -1) continue;
    const selPart = block.substring(0, braceIdx).trim();
    const selParts = selPart.split(',').map(s => s.trim());
    if (selParts.length === 1 && selParts[0] === selector) {
      return block.substring(braceIdx + 1).trim();
    }
  }
  return null;
}

function declarations(rule) {
  return Object.fromEntries(
    rule
      .split(';')
      .map((declaration) => declaration.trim())
      .filter(Boolean)
      .map((declaration) => {
        const colonIdx = declaration.indexOf(':');
        if (colonIdx === -1) return [null, null];
        return [
          declaration.slice(0, colonIdx).trim(),
          declaration.slice(colonIdx + 1).trim()
        ];
      })
      .filter(([k]) => k !== null)
  );
}

describe('Popup URL helper regressions', () => {
  test('identifies a YouTube embed video ID', () => {
    const { getVideoProviderId } = loadPopupHelpers();
    expect(getVideoProviderId('https://www.youtube.com/embed/abc123')).toEqual({
      provider: 'youtube',
      id: 'abc123'
    });
  });

  test('identifies a Vimeo player video ID', () => {
    const { getVideoProviderId } = loadPopupHelpers();
    expect(getVideoProviderId('https://player.vimeo.com/video/42')).toEqual({
      provider: 'vimeo',
      id: '42'
    });
  });

  test('rejects lookalike video provider hosts', () => {
    const { getVideoProviderId } = loadPopupHelpers();

    expect(getVideoProviderId('https://notyoutube.com/embed/abc123')).toBeNull();
    expect(getVideoProviderId('https://notvimeo.com/video/42')).toBeNull();
  });

  test('preserves the complete scraped page URL in exported data', () => {
    const { buildExportData } = loadPopupHelpers();
    const data = { images: [], videos: [], audios: [], links: [], text: '' };

    expect(buildExportData('https://example.com/a?x=1', data).url).toBe('https://example.com/a?x=1');
  });

  test('uses the video URL helper when rendering embed previews', () => {
    const popupJs = fs.readFileSync(popupJsPath, 'utf8');

    expect(popupJs).toContain('const videoProvider = getVideoProviderId(embedUrl);');
  });

  test('refreshes the current URL before re-scraping the active tab', () => {
    const popupJs = fs.readFileSync(popupJsPath, 'utf8');
    const scrapeFunction = popupJs.slice(
      popupJs.indexOf('async function scrapePageData()'),
      popupJs.indexOf('// Update UI with scraped data')
    );

    expect(scrapeFunction).toContain("currentTabUrl = tab.url || '';");
    expect(scrapeFunction).toContain('elements.currentPage.textContent = url.hostname || currentTabUrl;');
    expect(scrapeFunction.indexOf("currentTabUrl = tab.url || '';"))
      .toBeLessThan(scrapeFunction.indexOf('chrome.scripting.executeScript'));
  });
});

describe('Popup UI Bug Fixes', () => {

  describe('T1: Unit Happy Path - Core structure valid', () => {

    test('manifest.json defines popup action', () => {
      const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
      expect(manifest.action).toBeDefined();
      expect(manifest.action.default_popup).toBe('popup.html');
    });

    test('body popup dimensions are stable', () => {
      const styles = fs.readFileSync(stylesPath, 'utf8');
      const bodyRule = getRule(styles, 'body');
      expect(bodyRule).not.toBeNull();
      const body = declarations(bodyRule);
      expect(body.width).toBe('520px');
      expect(body.height).toBe('600px');
      expect(body['min-height']).toBe('0');
    });

    test('popup.js has no duplicate function definitions', () => {
      const popupJs = fs.readFileSync(popupJsPath, 'utf8');
      const funcDeclPattern = /function\s+(\w+)\s*\(/g;
      const names = {};
      let match;
      while ((match = funcDeclPattern.exec(popupJs)) !== null) {
        names[match[1]] = (names[match[1]] || 0) + 1;
      }
      const duplicates = Object.entries(names).filter(([, count]) => count > 1);
      expect(duplicates).toEqual([]);
    });

    test('detectSidebarMode called after DOMContentLoaded and elements init', () => {
      const popupJs = fs.readFileSync(popupJsPath, 'utf8');
      const domReadyBlock = popupJs.match(/DOMContentLoaded[\s\S]*?\n\}\);/);
      expect(domReadyBlock).not.toBeNull();
      expect(domReadyBlock[0]).toContain('detectSidebarMode');
    });

  });

  describe('T2: Regression - Popup Expansion Bug', () => {

    test('container is constrained to the popup viewport', () => {
      const styles = fs.readFileSync(stylesPath, 'utf8');
      const containerRule = getRule(styles, '.container');
      expect(containerRule).not.toBeNull();
      const container = declarations(containerRule);
      expect(container.height).toBe('100%');
      expect(container['min-height']).toBe('0');
    });

    test('sidebarBtn is defined in elements object before detectSidebarMode call', () => {
      const popupJs = fs.readFileSync(popupJsPath, 'utf8');
      const elementsBlock = popupJs.match(/const elements = \{[\s\S]*?\};/);
      expect(elementsBlock).not.toBeNull();
      expect(elementsBlock[0]).toContain('sidebarBtn');
    });

  });

  describe('T3: Regression - Image Downloads', () => {

    test('background handles image batches and keeps the response channel open', () => {
      const backgroundJs = fs.readFileSync(backgroundJsPath, 'utf8');
      const handler = backgroundJs.match(/if \(request\.action === 'downloadFiles'[\s\S]*?return true;/);
      expect(handler).not.toBeNull();
      expect(handler[0]).toContain('downloadFiles(items)');
      expect(handler[0]).toContain('sendResponse');
    });

  });

});



describe('Rendered video security and controls', () => {
  function renderVideosSource() {
    const popupJs = fs.readFileSync(popupJsPath, 'utf8');
    const start = popupJs.indexOf('function renderVideos()');
    const end = popupJs.indexOf('// Render audios', start);
    return popupJs.slice(start, end);
  }

  test('builds video result markup without unsafe HTML interpolation', () => {
    const source = renderVideosSource();

    expect(source).not.toMatch(/\.innerHTML\s*=/);
    expect(source).not.toContain('item.innerHTML');
  });

  test('labels native video open and copy controls', () => {
    const source = renderVideosSource();

    expect(source).toContain("document.createElement('a')");
    expect(source).toContain("openLink.textContent = 'Open video'");
    expect(source).toContain("openLink.target = '_blank'");
    expect(source).toContain("openLink.rel = 'noopener'");
    expect(source).toContain("copyBtn.setAttribute('aria-label', 'Copy video URL')");
  });

  test('provides an Open video link for every rendered video by default', () => {
    const source = renderVideosSource();
    const openLinkIndex = source.indexOf("openLink.textContent = 'Open video'");
    const previewBranchIndex = source.indexOf('let videoPreviewEl = null;');

    expect(openLinkIndex).toBeGreaterThan(-1);
    expect(previewBranchIndex).toBeGreaterThan(-1);
    expect(openLinkIndex).toBeLessThan(previewBranchIndex);
  });

  test('shows an accessible fallback when no video preview can be resolved', () => {
    const source = renderVideosSource();

    expect(source).toContain("fallback.className = 'video-preview-fallback'");
    expect(source).toContain("fallback.textContent = 'Video preview unavailable'");
    expect(source).toContain('wrapper.appendChild(fallback)');
    const styles = fs.readFileSync(stylesPath, 'utf8');
    const fallbackRule = getRule(styles, '.video-preview-fallback');
    expect(fallbackRule).not.toBeNull();
    expect(declarations(fallbackRule).display).toBe('flex');
  });

  test('accepts only HTTP(S) URLs for video links', () => {
    const { normalizeVideoUrl } = loadPopupHelpers();

    expect(normalizeVideoUrl('https://example.com/video.mp4')).toBe('https://example.com/video.mp4');
    expect(normalizeVideoUrl('http://example.com/video.mp4')).toBe('http://example.com/video.mp4');
    expect(normalizeVideoUrl('javascript:alert(1)')).toBe('');
    expect(normalizeVideoUrl('data:text/html,unsafe')).toBe('');
    expect(normalizeVideoUrl('not a URL')).toBe('');
    expect(normalizeVideoUrl(null)).toBe('');
  });

  test('uses normalized video URLs before assigning anchor hrefs', () => {
    const source = renderVideosSource();

    expect(source).toContain('const videoUrl = normalizeVideoUrl(video.src);');
    expect(source).toContain('if (videoUrl) {');
    expect(source).toContain('openLink.href = videoUrl;');
    expect(source).toContain('previewLink.href = videoUrl;');
  });

  test('video overlays do not intercept preview link clicks', () => {
    const styles = fs.readFileSync(stylesPath, 'utf8');
    const overlayRule = getRule(styles, '.video-overlay');

    expect(overlayRule).not.toBeNull();
    expect(declarations(overlayRule)['pointer-events']).toBe('none');
  });

});

describe('Image content classification', () => {
  test.each([
    [{ src: 'https://example.com/favicon.ico', width: 32, height: 32, isFavicon: true }, 'favicon'],
    [{ src: 'https://example.com/icon.png', width: 48, height: 48, isLinkIcon: true }, 'link icon'],
    [{ src: 'https://example.com/pixel.gif', width: 1, height: 1, isTracking: true }, 'tracking'],
    [{ src: 'https://example.com/sprites.png', width: 512, height: 512, isSprite: true }, 'sprite']
  ])('rejects obvious non-content assets %#', (candidate, reason) => {
    const { classifyImageCandidate } = loadPopupHelpers();
    const result = classifyImageCandidate(candidate);

    expect(result.classification).toBe('junk');
    expect(Number.isFinite(result.score)).toBe(true);
    expect(result.reasons.join(' ')).toContain(reason);
  });

  test.each([
    { src: 'https://example.com/icon-home.png', alt: 'Home icon', width: 40, height: 40 },
    { src: 'https://example.com/brand-logo.png', alt: 'Brand logo', width: 180, height: 60 },
    { src: 'https://example.com/avatar.jpg', alt: 'User avatar', width: 96, height: 96 },
    { src: 'https://example.com/nav-item.jpg', width: 240, height: 120, inNav: true }
  ])('classifies small icon, logo, avatar, and navigation candidates as junk', (candidate) => {
    expect(loadPopupHelpers().classifyImageCandidate(candidate).classification).toBe('junk');
  });

  test.each([
    { src: 'https://example.com/story.jpg', width: 900, height: 600, inMain: true, inArticle: true },
    { src: 'https://example.com/figure.jpg', width: 640, height: 480, inFigure: true, inPicture: true },
    { src: 'https://example.com/gallery/1.jpg', width: 500, height: 500, inGallery: true },
    { src: 'https://example.com/products/shoe.jpg', width: 800, height: 800, inProduct: true }
  ])('keeps large editorial, gallery, and product imagery', (candidate) => {
    const result = loadPopupHelpers().classifyImageCandidate(candidate);
    expect(result.classification).toBe('content');
    expect(result.score).toBeGreaterThanOrEqual(2);
    expect(result.reasons.length).toBeGreaterThan(0);
  });

  test('returns an explainable deterministic result for uncertain and malformed candidates', () => {
    const { classifyImageCandidate } = loadPopupHelpers();
    expect(() => classifyImageCandidate(null)).not.toThrow();
    expect(classifyImageCandidate(null)).toEqual(classifyImageCandidate(null));
    expect(classifyImageCandidate({ src: {}, alt: [], width: 'bad', height: Infinity })).toEqual(
      expect.objectContaining({ classification: expect.stringMatching(/^(content|junk)$/), score: expect.any(Number) })
    );
    expect(classifyImageCandidate({ src: 'https://example.com/photo.jpg', width: 150, height: 150 }).reasons.length).toBeGreaterThan(0);
  });

  test('ships Content as the active default with an All assets escape hatch', () => {
    const html = fs.readFileSync(popupHtmlPath, 'utf8');
    const source = fs.readFileSync(popupJsPath, 'utf8');

    expect(html).toMatch(/class="filter-chip active" data-filter="content"[^>]*>Content<\/button>/);
    expect(html).toMatch(/class="filter-chip" data-filter="all"[^>]*>All assets<\/button>/);
    expect(html).toContain('data-filter="large"');
    expect(html).toContain('data-filter="xlarge"');
    expect(html).toContain('data-filter="domain"');
    expect(source).toContain("let currentImageFilter = 'content';");
    expect(source).toContain(".filter-chip[data-filter=\"content\"]");
  });
});

describe('Scraper settings injection', () => {
  function loadFallbackScraper(document) {
    const popupJs = fs.readFileSync(popupJsPath, 'utf8');
    const start = popupJs.indexOf('function scrapePageFromContentFallback(');
    const end = popupJs.indexOf('// Render images', start);
    const context = { document };
    vm.runInNewContext(`${popupJs.slice(start, end)}\nthis.scrape = scrapePageFromContentFallback;`, context);
    return context.scrape;
  }

  function createDocument({ images = [], links = [] } = {}) {
    return {
      querySelectorAll(selector) {
        if (selector.startsWith('img[')) return images;
        if (selector === 'a[href]') return links;
        return [];
      },
      body: {
        cloneNode() {
          return {
            querySelectorAll() { return []; },
            innerText: ''
          };
        }
      }
    };
  }

  function image(src, width, height) {
    return { src, dataset: {}, naturalWidth: width, naturalHeight: height, alt: '', title: '' };
  }

  function link(href, inNavigation = false) {
    return {
      href,
      innerText: href,
      closest() { return inNavigation ? {} : null; }
    };
  }

  test('passes a snapshot of persisted settings to the injected fallback scraper', () => {
    const popupJs = fs.readFileSync(popupJsPath, 'utf8');
    const scrapeFunction = popupJs.slice(
      popupJs.indexOf('async function scrapePageData()'),
      popupJs.indexOf('// Update UI with scraped data')
    );

    expect(scrapeFunction).toContain('const scrapeSettings = { ...settings };');
    expect(scrapeFunction).toContain('func: scrapePageFromContentFallback');
    expect(scrapeFunction).toContain('args: [scrapeSettings]');
  });

  test('enforces the technical image minimum and enabled configured minimum dimensions', () => {
    const scrape = loadFallbackScraper(createDocument({
      images: [image('https://example.com/tiny.png', 9, 100), image('https://example.com/technical.png', 10, 10), image('https://example.com/small.png', 99, 100), image('https://example.com/large.png', 100, 100)]
    }));

    expect(scrape({ minSize: false }).images.map(({ src }) => src)).toEqual([
      'https://example.com/technical.png',
      'https://example.com/small.png',
      'https://example.com/large.png'
    ]);
    expect(scrape({ minSize: true, minWidth: 100, minHeight: 100 }).images.map(({ src }) => src)).toEqual([
      'https://example.com/large.png'
    ]);
  });

  test('normalizes missing scraper settings before extracting page data', () => {
    const scrape = loadFallbackScraper(createDocument({
      images: [image('https://example.com/valid.png', 10, 10)]
    }));

    expect(scrape(null).images).toHaveLength(1);
    expect(scrape({ minSize: true, minWidth: 'bad', minHeight: null }).images).toHaveLength(0);
  });

  test('accepts a nonempty numeric-string image threshold', () => {
    const scrape = loadFallbackScraper(createDocument({
      images: [image('https://example.com/below.png', 99, 100), image('https://example.com/at-threshold.png', 100, 100)]
    }));

    expect(scrape({ minSize: true, minWidth: '100', minHeight: '100' }).images.map(({ src }) => src)).toEqual([
      'https://example.com/at-threshold.png'
    ]);
  });

  test.each([true, [25], ''])('defaults invalid minWidth %p to 100', (minWidth) => {
    const scrape = loadFallbackScraper(createDocument({
      images: [image('https://example.com/below-default.png', 50, 100), image('https://example.com/at-default.png', 100, 100)]
    }));

    expect(scrape({ minSize: true, minWidth, minHeight: 100 }).images.map(({ src }) => src)).toEqual([
      'https://example.com/at-default.png'
    ]);
  });

  test('attaches classification and captured DOM context without dropping heuristic junk', () => {
    const navLogo = image('https://example.com/logo.png', 120, 40);
    navLogo.alt = 'Site logo';
    navLogo.closest = (selector) => selector.includes('nav') ? {} : null;
    const articleImage = image('https://example.com/article.jpg', 900, 600);
    articleImage.closest = (selector) => selector.includes('article') || selector.includes('main') ? {} : null;
    const scrape = loadFallbackScraper(createDocument({ images: [navLogo, articleImage] }));

    const result = scrape({ minSize: false }).images;
    expect(result).toHaveLength(2);
    expect(result[0]).toEqual(expect.objectContaining({
      classification: 'junk',
      classificationScore: expect.any(Number),
      classificationReasons: expect.arrayContaining([expect.any(String)]),
      context: expect.objectContaining({ inNav: true })
    }));
    expect(result[1]).toEqual(expect.objectContaining({
      classification: 'content',
      context: expect.objectContaining({ inMain: true, inArticle: true })
    }));
  });

  test('excludes only navigation links when excludeNav is enabled', () => {
    const scrape = loadFallbackScraper(createDocument({
      links: [link('https://example.com/nav', true), link('https://example.com/content')]
    }));

    expect(scrape({ excludeNav: true }).links.map(({ href }) => href)).toEqual(['https://example.com/content']);
    expect(scrape({ excludeNav: false }).links.map(({ href }) => href)).toEqual([
      'https://example.com/nav',
      'https://example.com/content'
    ]);
  });
});
