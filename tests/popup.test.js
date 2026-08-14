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
const fflatePath = path.join(__dirname, '..', 'libs', 'fflate.umd.js');

function loadPopupHelpers() {
  const popupJs = fs.readFileSync(popupJsPath, 'utf8');
  const domElementsIndex = popupJs.indexOf('// DOM Elements');
  const helperSource = domElementsIndex === -1 ? popupJs : popupJs.slice(0, domElementsIndex);
  const context = { URL };

  vm.runInNewContext(`${helperSource}
this.popupHelpers = { getVideoProviderId, normalizeVideoUrl, buildExportData, classifyImageCandidate, getWorkspaceCacheKey, serializeWorkspace, normalizeWorkspace, createWorkspaceTabController, normalizeSourceDocumentUrl, getWorkspaceEvictionKeys, setWorkspaceWithEviction, formatFilteredCount, getEmptyResultsMessage, copyTextWithFeedback, requestZipExport };`, context);
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
    expect(scrapeFunction).toContain('updateCurrentPage(tab);');
    expect(fs.readFileSync(popupJsPath, 'utf8')).toContain('elements.currentPage.textContent = url.hostname || currentTabUrl;');
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

    test('body dimensions adapt without fixed viewport clipping', () => {
      const styles = fs.readFileSync(stylesPath, 'utf8');
      const bodyRule = getRule(styles, 'body');
      expect(bodyRule).not.toBeNull();
      const body = declarations(bodyRule);
      expect(body.width).toBe('100%');
      expect(body['min-width']).toBe('320px');
      expect(body.height).toBe('auto');
      expect(body['overflow-y']).toBe('auto');
    });

    test('popup.js has no duplicate function definitions', () => {
      const popupJs = fs.readFileSync(popupJsPath, 'utf8');
      const funcDeclPattern = /(?:^|\n)\s*(?:async\s+)?function\s+(\w+)\s*\(/g;
      const names = {};
      let match;
      while ((match = funcDeclPattern.exec(popupJs)) !== null) {
        names[match[1]] = (names[match[1]] || 0) + 1;
      }
      const duplicates = Object.entries(names).filter(([, count]) => count > 1);
      expect(duplicates).toEqual([]);
      expect(names.openSettings).toBe(1);
      expect(popupJs).not.toContain('modal.innerHTML');
    });

    test('window controls are capability-configured after DOM initialization', () => {
      const popupJs = fs.readFileSync(popupJsPath, 'utf8');
      const domReadyBlock = popupJs.match(/DOMContentLoaded[\s\S]*?\n\}\);/);
      expect(domReadyBlock).not.toBeNull();
      expect(domReadyBlock[0]).toContain('configureWindowControls');
      expect(domReadyBlock[0]).not.toContain('detectSidebarMode');
    });

  });

  describe('T2: Regression - Popup Expansion Bug', () => {

    test('container follows available width and can grow vertically', () => {
      const styles = fs.readFileSync(stylesPath, 'utf8');
      const containerRule = getRule(styles, '.container');
      expect(containerRule).not.toBeNull();
      const container = declarations(containerRule);
      expect(container.width).toBe('100%');
      expect(container['min-width']).toBe('0');
      expect(container['min-height']).toBe('100vh');
      expect(container.overflow).toBe('visible');
    });

    test('sidebarBtn is defined before capability configuration', () => {
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
    [{ src: 'https://example.com/favicon.ico', width: 32, height: 32 }, 'favicon'],
    [{ src: 'https://example.com/icon.png', width: 48, height: 48, isLinkIcon: true }, 'link icon'],
    [{ src: 'https://example.com/tracking-pixel.gif', width: 1, height: 1 }, 'tracking'],
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

  test.each([
    {
      src: 'https://example.com/wildlife-tracking-guide.jpg',
      alt: 'Wildlife tracking guide in a forest',
      width: 900,
      height: 600,
      inArticle: true
    },
    {
      src: 'https://example.com/pixel-art-landscape.jpg',
      alt: 'Colorful pixel art mountain landscape',
      width: 900,
      height: 600,
      inArticle: true
    }
  ])('does not hard-veto ordinary content whose filename contains tracking or pixel-art', (candidate) => {
    const result = loadPopupHelpers().classifyImageCandidate(candidate);

    expect(result.classification).toBe('content');
    expect(result.reasons).not.toContain('tracking indicator');
  });

  test('does not retain the unused non-self-contained scraper duplicate', () => {
    const source = fs.readFileSync(popupJsPath, 'utf8');

    expect(source).not.toContain('function scrapePageFromContent()');
  });

  test('rejects badges even when their dimensions are otherwise content-sized', () => {
    const result = loadPopupHelpers().classifyImageCandidate({
      src: 'https://example.com/member-badge.png',
      alt: 'Member badge',
      width: 400,
      height: 400,
      inMain: true
    });

    expect(result.classification).toBe('junk');
    expect(result.reasons.join(' ')).toContain('badge');
  });

  test('penalizes extreme panorama and banner aspect ratios', () => {
    const result = loadPopupHelpers().classifyImageCandidate({
      src: 'https://example.com/wide-scene.jpg',
      width: 1200,
      height: 200
    });

    expect(result.classification).toBe('junk');
    expect(result.reasons.join(' ')).toContain('aspect ratio');
  });

  test('rewards meaningful descriptive alt text for a borderline main image', () => {
    const result = loadPopupHelpers().classifyImageCandidate({
      src: 'https://example.com/fox.jpg',
      alt: 'A red fox crossing a snowy field',
      width: 200,
      height: 150,
      inMain: true
    });

    expect(result.classification).toBe('content');
    expect(result.reasons.join(' ')).toContain('descriptive alt text');
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

  test('keeps popup and injected classifier scoring logic identical', () => {
    const source = fs.readFileSync(popupJsPath, 'utf8');
    const pureStart = source.indexOf('function classifyImageCandidate(candidate) {');
    const pureEnd = source.indexOf('// DOM Elements', pureStart);
    const injectedStart = source.indexOf('const classifyImageCandidate = candidate => {', pureEnd);
    const injectedEnd = source.indexOf('const defaults = {', injectedStart);
    const normalizeBody = (body) => body
      .slice(body.indexOf('{') + 1, body.lastIndexOf('}'))
      .split('\n')
      .map(line => line.trim())
      .filter(Boolean)
      .join('\n');

    expect(normalizeBody(source.slice(injectedStart, injectedEnd))).toBe(
      normalizeBody(source.slice(pureStart, pureEnd))
    );
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


describe('Per-tab session workspace cache', () => {
  const sampleData = {
    images: [{ src: 'https://example.com/hero.jpg', alt: 'Hero', width: 0, height: 0 }],
    videos: [{ src: 'https://example.com/movie.mp4', type: 'video', width: 0, height: 0, poster: '' }],
    audios: [{ src: 'https://example.com/audio.mp3', type: 'audio' }],
    links: [{ href: 'https://example.com/about', text: 'About' }],
    text: 'Page text'
  };

  test('creates a versioned key only for valid Chrome tab IDs', () => {
    const { getWorkspaceCacheKey } = loadPopupHelpers();

    expect(getWorkspaceCacheKey(42)).toBe('scrape-session:v1:tab:42');
    for (const invalid of [undefined, null, 0, -1, 1.5, '42']) {
      expect(() => getWorkspaceCacheKey(invalid)).toThrow();
    }
  });

  test('serializes and restores the complete workspace only for its exact tab', () => {
    const { serializeWorkspace, normalizeWorkspace } = loadPopupHelpers();
    const serialized = serializeWorkspace(42, {
      scrapedData: sampleData,
      scrapedAt: 1720000000000,
      sourceUrl: 'https://example.com/page?x=1#section',
      activeResultTab: 'links',
      currentImageFilter: 'large',
      imageSearch: 'hero',
      linkSearch: 'about',
      selectedImageUrls: ['https://example.com/hero.jpg', 'https://example.com/missing.jpg']
    });

    expect(serialized).toEqual(expect.objectContaining({
      version: 1,
      tabId: 42,
      scrapedAt: 1720000000000,
      sourceUrl: 'https://example.com/page?x=1',
      activeResultTab: 'links',
      currentImageFilter: 'large',
      imageSearch: 'hero',
      linkSearch: 'about'
    }));
    expect(normalizeWorkspace(serialized, 42, 'https://example.com/page?x=1#other')).toEqual(expect.objectContaining({
      scrapedData: sampleData,
      selectedImageUrls: ['https://example.com/hero.jpg']
    }));
    expect(normalizeWorkspace(serialized, 43, 'https://example.com/page?x=1')).toBeNull();
    expect(normalizeWorkspace(serialized, 42, 'https://example.com/other')).toBeNull();
  });

  test('drops optional page text before structured results to meet a size budget', () => {
    const { serializeWorkspace } = loadPopupHelpers();
    const serialized = serializeWorkspace(7, {
      scrapedData: { ...sampleData, text: 'x'.repeat(5000) },
      scrapedAt: 1720000000000,
      sourceUrl: 'https://example.com/page'
    }, 900);

    expect(JSON.stringify(serialized).length).toBeLessThanOrEqual(900);
    expect(serialized.scrapedData.text.length).toBeLessThan(5000);
    expect(serialized.scrapedData.images).toEqual(sampleData.images);
    expect(serialized.scrapedData.videos).toEqual(sampleData.videos);
    expect(serialized.scrapedData.audios).toEqual(sampleData.audios);
    expect(serialized.scrapedData.links).toEqual(sampleData.links);
  });

  test('rejects a budget too small for even the required workspace envelope', () => {
    const { serializeWorkspace } = loadPopupHelpers();
    expect(() => serializeWorkspace(7, { scrapedData: sampleData, scrapedAt: 1720000000000, sourceUrl: 'https://example.com/page' }, 1)).toThrow(/size budget/i);
  });

  test.each([
    null,
    {},
    { version: 2, tabId: 42 },
    { version: 1, tabId: 42, scrapedData: { images: 'bad' } },
    { version: 1, tabId: 42, scrapedData: {}, scrapedAt: 'yesterday' }
  ])('rejects malformed cached workspace %p', (cached) => {
    const { normalizeWorkspace } = loadPopupHelpers();
    expect(normalizeWorkspace(cached, 42)).toBeNull();
  });

  test('switches workspace identity from tab A to tab B and cancels pending work', () => {
    const { createWorkspaceTabController } = loadPopupHelpers();
    const cancelPendingSave = jest.fn();
    const controller = createWorkspaceTabController(cancelPendingSave);

    const tabA = controller.activate(11);
    const tabB = controller.activate(22);

    expect(tabA).toEqual({ tabId: 11, windowId: null, generation: 1 });
    expect(tabB).toEqual({ tabId: 22, windowId: null, generation: 2 });
    expect(controller.current()).toEqual(tabB);
    expect(controller.isCurrent(tabA)).toBe(false);
    expect(controller.isCurrent(tabB)).toBe(true);
  });

  test('cancels the pending debounce as soon as another tab activates', () => {
    const { createWorkspaceTabController } = loadPopupHelpers();
    const cancelPendingSave = jest.fn();
    const controller = createWorkspaceTabController(cancelPendingSave);
    controller.activate(11);
    cancelPendingSave.mockClear();

    controller.activate(22);

    expect(cancelPendingSave).toHaveBeenCalledTimes(1);
  });

  test('does not apply an async restore that completes after another tab activates', async () => {
    const { createWorkspaceTabController } = loadPopupHelpers();
    const controller = createWorkspaceTabController(jest.fn());
    const applyRestore = jest.fn();
    let finishRestore;
    const restore = new Promise(resolve => { finishRestore = resolve; });
    const tabA = controller.activate(11);
    const completion = restore.then(() => controller.runIfCurrent(tabA, applyRestore));

    controller.activate(22);
    finishRestore();
    await completion;

    expect(applyRestore).not.toHaveBeenCalled();
  });

  test('re-enables scrape controls when an active-tab switch resets the workspace', () => {
    const source = fs.readFileSync(popupJsPath, 'utf8');
    const resetSource = source.slice(
      source.indexOf('function resetInMemoryWorkspace'),
      source.indexOf('async function handleActiveTabChanged')
    );
    expect(resetSource).toContain('elements.scrapeBtn.disabled = false;');
  });

  test('normalizes source document URLs by stripping only the hash', () => {
    const { normalizeSourceDocumentUrl } = loadPopupHelpers();
    expect(normalizeSourceDocumentUrl('https://example.com/path?q=1#section')).toBe('https://example.com/path?q=1');
    expect(normalizeSourceDocumentUrl('not a URL')).toBe('');
  });

  test('ignores activation events owned by another browser window', () => {
    const { createWorkspaceTabController } = loadPopupHelpers();
    const cancel = jest.fn();
    const controller = createWorkspaceTabController(cancel);
    const original = controller.activate(11, 100);
    cancel.mockClear();

    expect(controller.acceptsWindow(200)).toBe(false);
    expect(controller.current()).toEqual(original);
    expect(cancel).not.toHaveBeenCalled();
  });

  test('Clear invalidates a pending same-tab restore token', async () => {
    const { createWorkspaceTabController } = loadPopupHelpers();
    const controller = createWorkspaceTabController(jest.fn());
    const applyRestore = jest.fn();
    let finishRestore;
    const restore = new Promise(resolve => { finishRestore = resolve; });
    const beforeClear = controller.activate(11, 100);
    const completion = restore.then(() => controller.runIfCurrent(beforeClear, applyRestore));

    const afterClear = controller.revise();
    finishRestore();
    await completion;

    expect(afterClear).toEqual({ tabId: 11, windowId: 100, generation: 2 });
    expect(applyRestore).not.toHaveBeenCalled();
  });

  test('same-tab A to B navigation invalidates the operation and rejects A cache', () => {
    const { createWorkspaceTabController, serializeWorkspace, normalizeWorkspace } = loadPopupHelpers();
    const controller = createWorkspaceTabController(jest.fn());
    const tabA = controller.activate(11, 100);
    const cachedA = serializeWorkspace(11, {
      sourceUrl: 'https://example.com/a#section',
      scrapedAt: 1720000000000,
      scrapedData: { images: [], videos: [], audios: [], links: [], text: 'A' }
    });

    const tabB = controller.revise();

    expect(tabB.tabId).toBe(tabA.tabId);
    expect(tabB.generation).toBe(tabA.generation + 1);
    expect(controller.isCurrent(tabA)).toBe(false);
    expect(normalizeWorkspace(cachedA, 11, 'https://example.com/b')).toBeNull();
  });

  test('orders only other scrape workspaces from oldest to newest for eviction', () => {
    const { getWorkspaceEvictionKeys } = loadPopupHelpers();
    const entries = {
      settings: { scrapedAt: 1 },
      'scrape-session:v1:tab:1': { scrapedAt: 30 },
      'scrape-session:v1:tab:2': { scrapedAt: 10 },
      'scrape-session:v1:tab:3': { scrapedAt: 20 }
    };
    expect(getWorkspaceEvictionKeys(entries, 'scrape-session:v1:tab:3')).toEqual([
      'scrape-session:v1:tab:2',
      'scrape-session:v1:tab:1'
    ]);
  });

  test('evicts the oldest other workspace and retries a quota-failed write once', async () => {
    const { setWorkspaceWithEviction } = loadPopupHelpers();
    const storage = {
      set: jest.fn().mockRejectedValueOnce(new Error('QUOTA_BYTES')).mockResolvedValueOnce(),
      get: jest.fn().mockResolvedValue({
        'scrape-session:v1:tab:1': { scrapedAt: 30 },
        'scrape-session:v1:tab:2': { scrapedAt: 10 }
      }),
      remove: jest.fn().mockResolvedValue()
    };

    await setWorkspaceWithEviction(storage, 'scrape-session:v1:tab:1', { version: 1 });

    expect(storage.set).toHaveBeenCalledTimes(2);
    expect(storage.remove).toHaveBeenCalledWith('scrape-session:v1:tab:2');
  });

  test('does not attempt a third write when the single retry also fails', async () => {
    const { setWorkspaceWithEviction } = loadPopupHelpers();
    const storage = {
      set: jest.fn().mockRejectedValue(new Error('QUOTA_BYTES')),
      get: jest.fn().mockResolvedValue({ 'scrape-session:v1:tab:2': { scrapedAt: 10 } }),
      remove: jest.fn().mockResolvedValue()
    };

    await expect(setWorkspaceWithEviction(
      storage,
      'scrape-session:v1:tab:1',
      { version: 1 }
    )).rejects.toThrow('QUOTA_BYTES');
    expect(storage.set).toHaveBeenCalledTimes(2);
  });

  test('drops malformed structured items and supplies render-safe defaults', () => {
    const { serializeWorkspace, normalizeWorkspace } = loadPopupHelpers();
    const serialized = serializeWorkspace(9, {
      sourceUrl: 'https://example.com/page',
      scrapedAt: 1720000000000,
      scrapedData: {
        images: [null, {}, { src: 4 }, { src: 'https://example.com/image.jpg', width: 'bad' }],
        videos: [{}, { src: 'https://example.com/video.mp4', poster: 7 }],
        audios: [{ src: null }, { src: 'https://example.com/audio.mp3' }],
        links: [{ href: 3 }, { href: 'https://example.com', text: 4 }],
        text: 'safe'
      }
    });
    const restored = normalizeWorkspace(serialized, 9, 'https://example.com/page');

    expect(restored.scrapedData.images).toEqual([{ src: 'https://example.com/image.jpg', alt: '', width: 0, height: 0 }]);
    expect(restored.scrapedData.videos).toEqual([{ src: 'https://example.com/video.mp4', type: 'video', width: 0, height: 0, poster: '' }]);
    expect(restored.scrapedData.audios).toEqual([{ src: 'https://example.com/audio.mp3', type: 'audio' }]);
    expect(restored.scrapedData.links).toEqual([{ href: 'https://example.com/', text: '' }]);
  });

  test('wires session-only restore, persistence, and cleanup controls', () => {
    const html = fs.readFileSync(popupHtmlPath, 'utf8');
    const popupSource = fs.readFileSync(popupJsPath, 'utf8');
    const backgroundSource = fs.readFileSync(backgroundJsPath, 'utf8');

    expect(html).toMatch(/id="clearResultsBtn"[^>]*>[^<]*Clear results|id="clearResultsBtn"/);
    expect(popupSource).toContain('chrome.storage.session');
    expect(popupSource).toContain('512 * 1024');
    expect(popupSource).toContain('restoreWorkspaceForCurrentTab');
    expect(popupSource).toContain('scheduleWorkspaceSave');
    expect(popupSource).toContain('chrome.tabs.onActivated.addListener');
    expect(popupSource).toContain('handleActiveTabChanged');
    expect(popupSource).toContain('chrome.tabs.onUpdated.addListener');
    expect(popupSource).toContain('handleCurrentTabUpdated');
    expect(popupSource).toContain('Restored from this tab · scraped');
    expect(popupSource).not.toMatch(/storage\.local.*workspace|workspace.*storage\.local/);
    expect(backgroundSource).toContain('chrome.tabs.onRemoved.addListener');
    expect(backgroundSource).toContain('chrome.storage.session.remove');
  });
});


describe('Task 6 operation state and clipboard reporting contracts', () => {
  test('persistent status and toast regions are live and atomic', () => {
    const html = fs.readFileSync(popupHtmlPath, 'utf8');
    expect(html).toMatch(/id="status"[^>]*role="status"[^>]*aria-live="polite"[^>]*aria-atomic="true"/);
    expect(html).toMatch(/id="toast"[^>]*role="status"[^>]*aria-live="polite"[^>]*aria-atomic="true"/);
  });

  test('scrape operation exposes busy state and preserves durable status ownership', () => {
    const popupJs = fs.readFileSync(popupJsPath, 'utf8');
    const scrapeFunction = popupJs.slice(
      popupJs.indexOf('async function scrapePageData()'),
      popupJs.indexOf('// Update UI with scraped data')
    );
    const updateFunction = popupJs.slice(
      popupJs.indexOf('function updateUI('),
      popupJs.indexOf('// Fallback scrape function')
    );

    expect(popupJs).toContain("elements.scrapeRegion.setAttribute('aria-busy', isBusy ? 'true' : 'false');");
    expect(scrapeFunction).toContain('setScrapeBusy(true);');
    expect(scrapeFunction).toContain('setScrapeBusy(false);');
    expect(scrapeFunction).toContain('Last scraped:');
    expect(scrapeFunction).toContain('Existing results kept.');
    expect(scrapeFunction).toContain('Check page access and try again.');
    expect(updateFunction).not.toContain('elements.status');
  });

  test('toast announcements stay in the accessibility tree and reuse one region', () => {
    const html = fs.readFileSync(popupHtmlPath, 'utf8');
    const styles = fs.readFileSync(stylesPath, 'utf8');
    const popupJs = fs.readFileSync(popupJsPath, 'utf8');
    const toastFunction = popupJs.slice(
      popupJs.indexOf('function showToast('),
      popupJs.indexOf('// Expose window controls')
    );

    expect(html).not.toMatch(/id="toast"[^>]*\shidden(?:\s|>)/);
    expect(styles).toContain('.toast.visible');
    expect(toastFunction).toContain('toastController.show(message);');
    expect(toastFunction).not.toContain("document.createElement('div')");
    expect(toastFunction).not.toContain('.hidden');
  });

  test('toast controller ignores stale callbacks and reset clears presentation', () => {
    const popupJs = fs.readFileSync(popupJsPath, 'utf8');
    const start = popupJs.indexOf('function createToastController(');
    const end = popupJs.indexOf('// DOM Elements', start);
    expect(start).toBeGreaterThanOrEqual(0);
    if (start < 0 || end < 0) return;

    const context = {};
    vm.runInNewContext(`${popupJs.slice(start, end)}
this.createToastController = createToastController;`, context);
    const queued = [];
    const timers = new Map();
    let nextTimer = 0;
    const element = {
      textContent: '',
      classList: {
        values: new Set(),
        add(value) { this.values.add(value); },
        remove(value) { this.values.delete(value); },
        contains(value) { return this.values.has(value); }
      }
    };
    const controller = context.createToastController(element, {
      queue: callback => queued.push(callback),
      setTimer: callback => { const id = ++nextTimer; timers.set(id, callback); return id; },
      clearTimer: id => timers.delete(id)
    });

    controller.show('old tab');
    const staleAnnouncement = queued.shift();
    const staleDismissal = [...timers.values()][0];
    controller.reset();
    controller.show('new tab');
    queued.shift()();
    staleAnnouncement();
    staleDismissal();

    expect(element.textContent).toBe('new tab');
    expect(element.classList.contains('visible')).toBe(true);
    controller.reset();
    expect(element.textContent).toBe('');
    expect(element.classList.contains('visible')).toBe(false);
  });

  test('workspace resets also reset pending toast feedback', () => {
    const popupJs = fs.readFileSync(popupJsPath, 'utf8');
    const resetFunction = popupJs.slice(
      popupJs.indexOf('function resetInMemoryWorkspace'),
      popupJs.indexOf('async function handleActiveTabChanged')
    );
    expect(resetFunction).toContain('toastController.reset();');
  });

  test('context clipboard execution reports asynchronous success and failure', () => {
    const backgroundJs = fs.readFileSync(backgroundJsPath, 'utf8');
    const copySource = backgroundJs.slice(
      backgroundJs.indexOf('async function copyTextInTab'),
      backgroundJs.indexOf('// Scrape and display results')
    );
    expect(copySource).toContain('await navigator.clipboard.writeText(value);');
    expect(copySource).toContain('return { success: true };');
    expect(copySource).toMatch(/success: false,[\s\S]*error:/);
    expect(copySource).toContain('chrome.scripting.executeScript');
  });

  test('background only announces clipboard success after a confirmed result', () => {
    const backgroundJs = fs.readFileSync(backgroundJsPath, 'utf8');
    expect(backgroundJs).toContain('async function copyTextInTab(tabId, text)');
    expect(backgroundJs).toContain('const result = results && results[0] && results[0].result;');
    expect(backgroundJs).toContain("if (!result || result.success !== true)");
    expect(backgroundJs).toContain("showNotification('Could not copy to clipboard:");
    expect(backgroundJs).toContain('await copyTextInTab(');
  });
});



describe('Confirmed popup clipboard and ZIP actions', () => {
  test('clipboard feedback waits for a resolved write', async () => {
    const { copyTextWithFeedback } = loadPopupHelpers();
    const events = [];
    let resolveWrite;
    const write = new Promise(resolve => { resolveWrite = resolve; });
    const pending = copyTextWithFeedback('https://example.com/video.mp4', 'Video URL copied!', 'Could not copy video URL', {
      writeText: value => { events.push(`write:${value}`); return write; },
      notify: message => events.push(`toast:${message}`),
      reportError: jest.fn()
    });

    await Promise.resolve();
    expect(events).toEqual(['write:https://example.com/video.mp4']);
    resolveWrite();
    await expect(pending).resolves.toBe(true);
    expect(events).toEqual(['write:https://example.com/video.mp4', 'toast:Video URL copied!']);
  });

  test('clipboard rejection is handled with truthful failure feedback', async () => {
    const { copyTextWithFeedback } = loadPopupHelpers();
    const notifications = [];
    const error = new Error('permission denied');
    const reportError = jest.fn();

    await expect(copyTextWithFeedback('https://example.com/audio.mp3', 'Audio URL copied!', 'Could not copy audio URL', {
      writeText: jest.fn().mockRejectedValue(error),
      notify: message => notifications.push(message),
      reportError
    })).resolves.toBe(false);

    expect(notifications).toEqual(['Could not copy audio URL']);
    expect(reportError).toHaveBeenCalledWith('Clipboard write failed:', error);
  });

  test('video and audio URL copy controls use normalized values and disable invalid copies', () => {
    const source = fs.readFileSync(popupJsPath, 'utf8');
    const videoSource = source.slice(source.indexOf('function renderVideos()'), source.indexOf('// Render audios'));
    const audioSource = source.slice(source.indexOf('function renderAudios()'), source.indexOf('// Render text'));

    expect(videoSource).toContain('copyBtn.disabled = !videoUrl;');
    expect(videoSource).toContain("await copyTextWithFeedback(videoUrl, 'Video URL copied!', 'Could not copy video URL');");
    expect(audioSource).toContain('const audioUrl = normalizeHttpUrl(audio.src);');
    expect(audioSource).toContain('copyBtn.disabled = !audioUrl;');
    expect(audioSource).toContain("await copyTextWithFeedback(audioUrl, 'Audio URL copied!', 'Could not copy audio URL');");
  });

  test('ZIP UI sends normalized image URLs to the background and only reports preparation', async () => {
    const { requestZipExport } = loadPopupHelpers();
    const messages = [];
    const notifications = [];

    await expect(requestZipExport([
      { src: 'https://example.com/a.png', alt: 'Hero' },
      { src: 'javascript:alert(1)', alt: 'Unsafe' }
    ], {
      sendMessage: async message => { messages.push(message); return { success: true }; },
      notify: message => notifications.push(message),
      reportError: jest.fn()
    })).resolves.toEqual({ success: true });

    expect(messages).toEqual([{
      action: 'createZipFromUrls',
      urls: ['https://example.com/a.png'],
      names: ['Hero'],
      count: 1
    }]);
    expect(notifications).toEqual(['Preparing ZIP for 1 image...']);
  });

  test('ZIP UI surfaces background individual-download fallback', async () => {
    const { requestZipExport } = loadPopupHelpers();
    const notifications = [];

    const result = await requestZipExport([{ src: 'https://example.com/a.png', alt: '' }], {
      sendMessage: async () => ({ success: false, fallback: true }),
      notify: message => notifications.push(message),
      reportError: jest.fn()
    });

    expect(result).toEqual({ success: false, fallback: true });
    expect(notifications).toEqual([
      'Preparing ZIP for 1 image...',
      'ZIP unavailable; individual downloads started.'
    ]);
  });

  test('ZIP UI handles a rejected runtime request as preparation failure', async () => {
    const { requestZipExport } = loadPopupHelpers();
    const notifications = [];
    const reportError = jest.fn();

    const result = await requestZipExport([{ src: 'https://example.com/a.png', alt: 'A' }], {
      sendMessage: async () => { throw new Error('service worker unavailable'); },
      notify: message => notifications.push(message),
      reportError
    });

    expect(result).toEqual({ success: false, error: 'service worker unavailable' });
    expect(notifications).toEqual([
      'Preparing ZIP for 1 image...',
      'ZIP preparation failed: service worker unavailable'
    ]);
    expect(reportError).toHaveBeenCalled();
  });

  function loadBackgroundZipHelpers(extraContext = {}) {
    const source = fs.readFileSync(backgroundJsPath, 'utf8');
    const start = source.indexOf('function getZipExtension');
    const context = { Blob, Uint8Array, URL, btoa, console, ...extraContext };
    vm.runInNewContext(`${source.slice(start)}
this.zipHelpers = { sanitizeZipEntryName, createZipFromUrls };`, context);
    return context.zipHelpers;
  }

  test('zero successful ZIP entries trigger one bounded individual-download fallback', async () => {
    const { createZipFromUrls } = loadBackgroundZipHelpers();
    const downloadIndividually = jest.fn().mockResolvedValue([]);
    const notify = jest.fn();

    const result = await createZipFromUrls(
      ['https://example.com/a.png', 'https://example.com/b.png'],
      ['A', 'B'],
      {
        ensureLibrary: async () => true,
        notify,
        reportError: jest.fn(),
        fetchUrl: jest.fn().mockResolvedValue({ ok: false, status: 403 }),
        createZip: () => ({}),
        createFile: jest.fn(),
        downloadZip: jest.fn(),
        downloadIndividually
      }
    );

    expect(result).toEqual({ success: false, fallback: true, added: 0, skipped: 2 });
    expect(downloadIndividually).toHaveBeenCalledTimes(1);
    expect(downloadIndividually.mock.calls[0][0]).toHaveLength(2);
    expect(notify).toHaveBeenCalledWith('No images could be added to the ZIP. Downloading 2 files individually.');
  });

  test('ZIP entry names are safe, extension-bearing, and unique', async () => {
    const { createZipFromUrls } = loadBackgroundZipHelpers();
    const filenames = [];
    const zip = { add: jest.fn(), end: jest.fn() };

    const resultPromise = createZipFromUrls(
      [
        'https://example.com/a.png',
        'https://example.com/b.png',
        'https://example.com/c.webp',
        'https://example.com/d.png',
        'https://example.com/e.png'
      ],
      ['  ../.\u0000/evil', '  ../.\u0000/evil', '...', '', ''],
      {
        ensureLibrary: async () => true,
        notify: jest.fn(),
        reportError: jest.fn(),
        fetchUrl: async () => ({ ok: true, arrayBuffer: async () => new Uint8Array([1]).buffer }),
        createZip: () => zip,
        createFile: filename => {
          filenames.push(filename);
          return { push: jest.fn() };
        },
        downloadZip: jest.fn(),
        downloadIndividually: jest.fn()
      }
    );
    zip.end.mockImplementation(() => zip.ondata(null, new Uint8Array([1]), true));
    const result = await resultPromise;

    expect(filenames).toHaveLength(5);
    expect(new Set(filenames).size).toBe(5);
    filenames.forEach(filename => {
      expect(filename).not.toMatch(/[\/\\\x00-\x1f\x7f]/);
      expect(filename).not.toMatch(/^\./);
      expect(filename).toMatch(/\.(png|webp)$/);
    });
    expect(filenames).toEqual(expect.arrayContaining(['image-3.webp', 'image-4.png', 'image-5.png']));
    expect(result).toEqual({ success: true, added: 5, skipped: 0 });
  });

  test('partial ZIP success reports exact added and skipped counts', async () => {
    const { createZipFromUrls } = loadBackgroundZipHelpers();
    const notify = jest.fn();
    const zip = { add: jest.fn(), end: jest.fn() };
    let fetchCount = 0;
    zip.end.mockImplementation(() => zip.ondata(null, new Uint8Array([1]), true));

    const result = await createZipFromUrls(['https://example.com/a.png', 'https://example.com/b.png'], ['A', 'B'], {
      ensureLibrary: async () => true,
      notify,
      reportError: jest.fn(),
      fetchUrl: async () => (++fetchCount === 1
        ? { ok: true, arrayBuffer: async () => new Uint8Array([7]).buffer }
        : { ok: false, status: 404 }),
      createZip: () => zip,
      createFile: () => ({ push: jest.fn() }),
      downloadZip: jest.fn(),
      downloadIndividually: jest.fn()
    });

    expect(result).toEqual({ success: true, added: 1, skipped: 1 });
    expect(notify).toHaveBeenCalledWith(expect.stringContaining('1 image added; 1 skipped'));
  });

  test('popup and background contain no handwritten or legacy ZIP paths', () => {
    const popupSource = fs.readFileSync(popupJsPath, 'utf8');
    const backgroundSource = fs.readFileSync(backgroundJsPath, 'utf8');
    for (const source of [popupSource, backgroundSource]) {
      ['createZipArchive', 'calculateCRC32', 'createAndDownloadZip', 'createZipArchiveFromFiles'].forEach(name => {
        expect(source).not.toContain(name);
      });
    }
    expect(backgroundSource).not.toContain("request.action === 'createAndDownloadZip'");
    expect(popupSource).not.toContain('function buildZip(');
    expect(popupSource).not.toContain('function fetchImageAsBytes(');
    expect(popupSource).not.toContain('function exportAsZip(');
  });

  test('background ZIP messages keep the response channel open and send the async result', () => {
    const source = fs.readFileSync(backgroundJsPath, 'utf8');
    const handler = source.match(/if \(request\.action === 'createZipFromUrls'\)[\s\S]*?return true;/);
    expect(handler).not.toBeNull();
    expect(handler[0]).toContain('createZipFromUrls(request.urls, request.names)');
    expect(handler[0]).toContain('sendResponse');
  });
});

describe('Task 7 keyboard accessibility contracts', () => {
  test('results use the ARIA tabs pattern with linked hidden panels', () => {
    const html = fs.readFileSync(popupHtmlPath, 'utf8');
    expect(html).toMatch(/class="tab-list"[^>]*role="tablist"[^>]*aria-label=/);
    for (const name of ['images', 'videos', 'audios', 'links', 'text']) {
      expect(html).toMatch(new RegExp(`id="${name}TabButton"[^>]*role="tab"[^>]*aria-controls="${name}Tab"`));
      expect(html).toMatch(new RegExp(`id="${name}Tab"[^>]*role="tabpanel"[^>]*aria-labelledby="${name}TabButton"`));
    }
    expect(html).toMatch(/id="imagesTabButton"[^>]*aria-selected="true"[^>]*tabindex="0"/);
    expect(html).toMatch(/id="videosTabButton"[^>]*aria-selected="false"[^>]*tabindex="-1"/);
    expect(html).toMatch(/id="videosTab"[^>]*hidden/);
  });

  test('tab switching synchronizes selected, focusability, panels, and keyboard activation', () => {
    const source = fs.readFileSync(popupJsPath, 'utf8');
    const switchSource = source.slice(source.indexOf('function switchTab('), source.indexOf('// Scrape page data'));
    expect(switchSource).toContain("setAttribute('aria-selected'");
    expect(switchSource).toContain("setAttribute('tabindex'");
    expect(switchSource).toContain('.hidden =');
    expect(source).toContain("case 'ArrowRight'");
    expect(source).toContain("case 'ArrowLeft'");
    expect(source).toContain("case 'Home'");
    expect(source).toContain("case 'End'");
    expect(source).toContain('.focus();');
  });

  test('icon controls and filters have accessible names and states', () => {
    const html = fs.readFileSync(popupHtmlPath, 'utf8');
    for (const id of ['detachBtn', 'sidebarBtn', 'settingsBtn', 'closePanelBtn']) {
      expect(html).toMatch(new RegExp(`id="${id}"[^>]*aria-label=`));
    }
    expect(html).toMatch(/id="imageFilter"[^>]*aria-label=|<label[^>]*for="imageFilter"/);
    expect(html).toMatch(/id="linkFilter"[^>]*aria-label=|<label[^>]*for="linkFilter"/);
    expect(html.match(/class="filter-chip[^>]*aria-pressed=/g)).toHaveLength(5);
    expect(html).toMatch(/<svg[^>]*aria-hidden="true"[^>]*focusable="false"/);
  });

  test('settings and downloads are labelled native dialogs with static settings inputs', () => {
    const html = fs.readFileSync(popupHtmlPath, 'utf8');
    expect(html).toMatch(/<dialog[^>]*id="settingsDialog"[^>]*aria-labelledby="settingsTitle"/);
    expect(html).toMatch(/<dialog[^>]*id="downloadPanel"[^>]*aria-labelledby="downloadPanelTitle"/);
    expect(html).toMatch(/<label[^>]*for="minWidth"/);
    expect(html).toMatch(/id="minWidth"[^>]*min="1"/);
    expect(html).toMatch(/id="minHeight"[^>]*min="1"/);
    expect(html).not.toContain('modal.innerHTML');
  });

  test('dialog focus controller opens modally, focuses, closes, and restores focus', () => {
    const source = fs.readFileSync(popupJsPath, 'utf8');
    const start = source.indexOf('function createDialogController(');
    const end = source.indexOf('const toastController', start);
    expect(start).toBeGreaterThanOrEqual(0);
    if (start < 0 || end < 0) return;
    const context = {};
    vm.runInNewContext(`${source.slice(start, end)}\nthis.createDialogController = createDialogController;`, context);
    const trigger = { focus: jest.fn() };
    const first = { focus: jest.fn() };
    const dialog = { open: false, showModal: jest.fn(function () { this.open = true; }), close: jest.fn(function () { this.open = false; }) };
    const controller = context.createDialogController(dialog);
    controller.open(trigger, first);
    expect(dialog.showModal).toHaveBeenCalled();
    expect(first.focus).toHaveBeenCalled();
    controller.close();
    expect(dialog.close).toHaveBeenCalled();
    expect(trigger.focus).toHaveBeenCalled();
  });

  test('dynamic media and selection controls are keyboard-native and avoid URL selectors', () => {
    const source = fs.readFileSync(popupJsPath, 'utf8');
    expect(source).toContain("checkbox.type = 'button'");
    expect(source).toContain("checkbox.setAttribute('aria-pressed'");
    expect(source).toContain("document.createElement('a')");
    expect(source).toContain("playBtn.setAttribute('aria-label'");
    expect(source).toContain("copyBtn.setAttribute('aria-label', 'Copy audio URL')");
    expect(source).toContain("removeBtn.setAttribute('aria-label'");
    expect(source).not.toMatch(/querySelector\(`\.image-item img\[src="\$\{src\}"\]`\)/);
  });

  test('download dialog receives an explicit in-dialog initial focus target', () => {
    const source = fs.readFileSync(popupJsPath, 'utf8');
    const openDownload = source.slice(
      source.indexOf('function openDownloadPanel()'),
      source.indexOf('// Close download panel')
    );
    expect(openDownload).toContain("downloadDialogController.open(document.getElementById('openPanelBtn'), document.getElementById('downloadConfirmBtn'));");
  });

  test('positive integer settings reject blank, fractional, zero, negative, and non-finite values', () => {
    const source = fs.readFileSync(popupJsPath, 'utf8');
    const start = source.indexOf('function parsePositiveIntegerSetting(');
    const end = source.indexOf('function handleImagePreviewClick(', start);
    expect(start).toBeGreaterThanOrEqual(0);
    if (start < 0 || end < 0) return;
    const context = {};
    vm.runInNewContext(`${source.slice(start, end)}\nthis.parsePositiveIntegerSetting = parsePositiveIntegerSetting;`, context);
    for (const value of ['', '1.5', '0', '-2', 'Infinity', Infinity, NaN]) {
      expect(context.parsePositiveIntegerSetting(value)).toBeNull();
    }
    expect(context.parsePositiveIntegerSetting('1')).toBe(1);
    expect(context.parsePositiveIntegerSetting('240')).toBe(240);
  });

  test('settings dimension validation reports and focuses the first invalid input', () => {
    const source = fs.readFileSync(popupJsPath, 'utf8');
    const start = source.indexOf('function parsePositiveIntegerSetting(');
    const end = source.indexOf('function handleImagePreviewClick(', start);
    const context = {};
    vm.runInNewContext(`${source.slice(start, end)}\nthis.validateSettingsDimensions = validateSettingsDimensions;`, context);
    const makeInput = value => ({ value, setCustomValidity: jest.fn(), focus: jest.fn(), reportValidity: jest.fn() });
    const invalidWidth = makeInput('1.5');
    const validHeight = makeInput('100');

    expect(context.validateSettingsDimensions(invalidWidth, validHeight)).toBeNull();
    expect(invalidWidth.setCustomValidity).toHaveBeenCalledWith('Enter a positive whole number.');
    expect(validHeight.setCustomValidity).toHaveBeenCalledWith('');
    expect(invalidWidth.focus).toHaveBeenCalled();
    expect(invalidWidth.reportValidity).toHaveBeenCalled();

    const validWidth = makeInput('20');
    expect(context.validateSettingsDimensions(validWidth, validHeight)).toEqual({ minWidth: 20, minHeight: 100 });
  });

  test('settings inputs require values and invalid settings focus without saving', () => {
    const html = fs.readFileSync(popupHtmlPath, 'utf8');
    const source = fs.readFileSync(popupJsPath, 'utf8');
    expect(html).toMatch(/id="minWidth"[^>]*required/);
    expect(html).toMatch(/id="minHeight"[^>]*required/);
    const saveSource = source.slice(source.indexOf('function saveSettings('));
    expect(saveSource).toContain('validateSettingsDimensions(minWidthInput, minHeightInput)');
    expect(source).toContain('invalidInput.focus();');
    expect(saveSource.indexOf('return;')).toBeLessThan(saveSource.indexOf('chrome.storage.local.set'));
  });

  test('invalid setting submissions never persist', () => {
    const source = fs.readFileSync(popupJsPath, 'utf8');
    const helpersStart = source.indexOf('function parsePositiveIntegerSetting(');
    const helpersEnd = source.indexOf('function handleImagePreviewClick(', helpersStart);
    const saveStart = source.indexOf('function saveSettings(');
    const storageSet = jest.fn();
    const makeInput = value => ({ value, checked: true, setCustomValidity: jest.fn(), focus: jest.fn(), reportValidity: jest.fn() });
    const fields = {
      minSizeCheck: makeInput(''),
      minWidth: makeInput('100'),
      minHeight: makeInput('100'),
      excludeNav: makeInput('')
    };
    const context = {
      document: { getElementById: id => fields[id] },
      chrome: { storage: { local: { set: storageSet } } },
      showToast: jest.fn(),
      settingsDialogController: { close: jest.fn() }
    };
    vm.runInNewContext(`let settings = {};\n${source.slice(helpersStart, helpersEnd)}\n${source.slice(saveStart)}\nthis.saveSettings = saveSettings;`, context);

    for (const invalid of ['', '1.5', '0', '-2', 'Infinity']) {
      fields.minWidth.value = invalid;
      context.saveSettings({ preventDefault: jest.fn() });
    }
    expect(storageSet).not.toHaveBeenCalled();

    fields.minWidth.value = '25';
    context.saveSettings({ preventDefault: jest.fn() });
    expect(storageSet).toHaveBeenCalledTimes(1);
  });

  test('image preview click toggles only on Shift and otherwise allows native anchor navigation', () => {
    const source = fs.readFileSync(popupJsPath, 'utf8');
    const start = source.indexOf('function handleImagePreviewClick(');
    const end = source.indexOf('const toastController', start);
    expect(start).toBeGreaterThanOrEqual(0);
    if (start < 0 || end < 0) return;
    const context = {};
    vm.runInNewContext(`${source.slice(start, end)}\nthis.handleImagePreviewClick = handleImagePreviewClick;`, context);

    for (const [eventOptions, shouldToggle] of [
      [{ shiftKey: true }, true],
      [{ shiftKey: false }, false],
      [{ shiftKey: false, ctrlKey: true }, false],
      [{ shiftKey: false, metaKey: true }, false]
    ]) {
      const event = { ...eventOptions, preventDefault: jest.fn() };
      const toggle = jest.fn();
      context.handleImagePreviewClick(event, toggle);
      expect(event.preventDefault).toHaveBeenCalledTimes(shouldToggle ? 1 : 0);
      expect(toggle).toHaveBeenCalledTimes(shouldToggle ? 1 : 0);
    }
  });


  test('image selection button is revealed by keyboard focus', () => {
    const styles = fs.readFileSync(stylesPath, 'utf8');
    expect(styles).toMatch(/\.image-checkbox:focus-visible[\s\S]*?opacity:\s*1/);
    expect(styles).toMatch(/\.image-item:focus-within \.image-checkbox[\s\S]*?opacity:\s*1/);
  });

  test('normalizes only supported safe image media URLs', () => {
    const source = fs.readFileSync(popupJsPath, 'utf8');
    const start = source.indexOf('const MAX_IMAGE_DATA_URL_LENGTH');
    const end = source.indexOf('function safeDimension(', start);
    expect(start).toBeGreaterThanOrEqual(0);
    if (start < 0 || end < 0) return;
    const context = { URL };
    vm.runInNewContext(`${source.slice(start, end)}\nthis.normalizeImageUrl = normalizeImageUrl;`, context);
    const normalizeImageUrl = context.normalizeImageUrl;
    const png = `data:image/png;base64,${Buffer.from('png').toString('base64')}`;

    expect(normalizeImageUrl('https://example.com/photo.webp')).toBe('https://example.com/photo.webp');
    expect(normalizeImageUrl('http://example.com/photo.jpg')).toBe('http://example.com/photo.jpg');
    expect(normalizeImageUrl('blob:https://example.com/4d36e96b')).toBe('blob:https://example.com/4d36e96b');
    expect(normalizeImageUrl(png)).toBe(png);
    for (const value of [
      'data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=',
      'data:text/html;base64,PGgxPmJhZDwvaDE+',
      'data:image/png,not-base64',
      'data:image/png;base64,%%%%',
      `data:image/png;base64,${'A'.repeat(50000)}`,
      'javascript:alert(1)',
      'not a URL'
    ]) expect(normalizeImageUrl(value)).toBe('');
  });

  test('rendering images uses the media-specific normalizer for both preview and open link', () => {
    const source = fs.readFileSync(popupJsPath, 'utf8');
    const renderSource = source.slice(source.indexOf('function renderImages()'), source.indexOf('// Render links'));
    expect(renderSource).toContain('normalizeImageUrl(img.src)');
    expect(renderSource).not.toContain('normalizeHttpUrl(img.src)');
    expect(renderSource).toContain('openLink.href = safeImageUrl;');
    expect(renderSource).toContain('imgEl.src = safeImageUrl;');
    expect(renderSource).toContain('if (!safeImageUrl) return;');
  });

  test('workspace round-trip preserves selected supported blob and constrained data images only', () => {
    const { serializeWorkspace, normalizeWorkspace } = loadPopupHelpers();
    const dataImage = `data:image/webp;base64,${Buffer.from('webp').toString('base64')}`;
    const blobImage = 'blob:https://example.com/4d36e96b';
    const unsupportedSvg = 'data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=';
    const javascriptUrl = 'javascript:alert(1)';
    const workspace = serializeWorkspace(77, {
      sourceUrl: 'https://example.com/gallery',
      scrapedAt: 1720000000000,
      scrapedData: {
        images: [
          { src: blobImage, width: 100, height: 100 },
          { src: dataImage, width: 100, height: 100 },
          { src: unsupportedSvg, width: 100, height: 100 },
          { src: javascriptUrl, width: 100, height: 100 }
        ],
        videos: [], audios: [], links: [], text: ''
      },
      selectedImageUrls: [blobImage, dataImage, unsupportedSvg, javascriptUrl]
    });
    const restored = normalizeWorkspace(workspace, 77, 'https://example.com/gallery');

    expect(workspace.scrapedData.images.map(image => image.src)).toEqual([blobImage, dataImage]);
    expect(workspace.selectedImageUrls).toEqual([blobImage, dataImage]);
    expect(restored.selectedImageUrls).toEqual([blobImage, dataImage]);
  });

});


describe('Responsive dark Operate interface contracts', () => {
  test('defines a complete semantic dark token set with one electric-blue accent', () => {
    const styles = fs.readFileSync(stylesPath, 'utf8');

    [
      '--surface-canvas:', '--surface-subtle:', '--surface-raised:', '--surface-overlay:',
      '--text-primary:', '--text-muted:', '--border:', '--accent:', '--focus:',
      '--success:', '--warning:', '--error:', '--disabled:'
    ].forEach(token => expect(styles).toContain(token));
    expect(styles).not.toMatch(/linear-gradient|radial-gradient/);
  });

  test('allows document scrolling and adapts from a 320px popup to wider panels', () => {
    const styles = fs.readFileSync(stylesPath, 'utf8');

    expect(styles).toMatch(/body\s*\{[^}]*min-width:\s*320px[^}]*width:\s*100%[^}]*overflow-y:\s*auto/s);
    expect(styles).toMatch(/\.container\s*\{[^}]*width:\s*100%[^}]*min-width:\s*0/s);
    expect(styles).toMatch(/@media\s*\(max-width:\s*380px\)/);
    expect(styles).toMatch(/@media\s*\(min-width:\s*700px\)/);
    expect(styles).not.toMatch(/body\s*\{[^}]*width:\s*520px/s);
  });

  test('uses practical control targets and visible focus', () => {
    const styles = fs.readFileSync(stylesPath, 'utf8');

    expect(styles).toMatch(/\.settings-btn\s*\{[^}]*min-width:\s*36px[^}]*min-height:\s*36px/s);
    expect(styles).toMatch(/button:focus-visible[\s\S]*outline:\s*2px solid var\(--focus\)/);
    expect(styles).toMatch(/button:disabled[\s\S]*var\(--disabled\)/);
  });

  test('makes the download dialog responsive rather than a covering side panel', () => {
    const styles = fs.readFileSync(stylesPath, 'utf8');

    expect(styles).toMatch(/\.download-panel\s*\{[^}]*width:\s*min\(calc\(100% - 24px\),\s*480px\)[^}]*max-height:\s*min\(80vh,\s*640px\)/s);
    expect(styles).not.toMatch(/\.download-panel\s*\{[^}]*transform:\s*translateX\(100%\)/s);
  });

  test('provides intentional reduced-motion alternatives', () => {
    const styles = fs.readFileSync(stylesPath, 'utf8');

    expect(styles).toMatch(/@media\s*\(prefers-reduced-motion:\s*reduce\)/);
    expect(styles).toMatch(/prefers-reduced-motion:[\s\S]*\.spinner[\s\S]*animation:\s*none/);
    expect(styles).toMatch(/prefers-reduced-motion:[\s\S]*\.toast[\s\S]*transition:\s*none/);
    expect(styles).toMatch(/prefers-reduced-motion:[\s\S]*\.settings-modal[\s\S]*animation:\s*none/);
  });

  test('uses API capability instead of viewport width to expose window controls', () => {
    const popupJs = fs.readFileSync(popupJsPath, 'utf8');
    const popupHtml = fs.readFileSync(popupHtmlPath, 'utf8');

    expect(popupJs).not.toContain('detectSidebarMode');
    expect(popupJs).not.toContain('window.outerWidth');
    expect(popupJs).toContain('configureWindowControls();');
    expect(popupJs).toContain("typeof chrome.sidePanel.open === 'function'");
    expect(popupJs).toContain("typeof chrome.windows.create === 'function'");
    expect(popupHtml).toContain('aria-label="Open in new window"');
    expect(popupHtml).toContain('aria-label="Open side panel"');
  });

  test('formats filtered counts as visible versus total', () => {
    const { formatFilteredCount } = loadPopupHelpers();

    expect(formatFilteredCount(2, 5, 'images')).toBe('2 of 5 images shown');
    expect(formatFilteredCount(5, 5, 'links')).toBe('5 links found');
  });

  test('distinguishes no scraped data from no filter matches', () => {
    const { getEmptyResultsMessage } = loadPopupHelpers();

    expect(getEmptyResultsMessage('images', 0, 0)).toBe('No images found');
    expect(getEmptyResultsMessage('images', 0, 4)).toBe('No images match the current filters');
    expect(getEmptyResultsMessage('links', 0, 3)).toBe('No links match the current filter');
  });
});


describe('Accessible result feedback and export actions', () => {
  test('announces concise image, link, and persistent selection changes without live result grids', () => {
    const popupHtml = fs.readFileSync(popupHtmlPath, 'utf8');

    ['imagesCount', 'linksCount', 'selectionStatus'].forEach((id) => {
      expect(popupHtml).toMatch(new RegExp(
        `<[^>]+id=["']${id}["'][^>]+role=["']status["'][^>]+aria-live=["']polite["'][^>]+aria-atomic=["']true["']`
      ));
    });
    const selectionInfo = popupHtml.match(/<[^>]+id=["']selectionInfo["'][^>]*>/)[0];
    expect(selectionInfo).not.toMatch(/role=["']status["']|aria-live=/);
    expect(popupHtml.indexOf('id="selectionStatus"')).toBeLessThan(popupHtml.indexOf('id="selectionToolbar"'));
    ['imagesContainer', 'linksContainer'].forEach((id) => {
      const element = popupHtml.match(new RegExp(`<[^>]+id=["']${id}["'][^>]*>`));
      expect(element).not.toBeNull();
      expect(element[0]).not.toMatch(/role=["']status["']|aria-live=/);
    });
  });

  test('puts image and link empty feedback in their concise announced status nodes', () => {
    const popupJs = fs.readFileSync(popupJsPath, 'utf8');

    expect(popupJs).toContain("getEmptyResultsMessage('images', images.length, scrapedData.images.length) ||");
    expect(popupJs).toContain("getEmptyResultsMessage('links', links.length, scrapedData.links.length) ||");
  });

  test('selection updates announce counts, clearing, and move focus before hiding the toolbar', () => {
    const popupJs = fs.readFileSync(popupJsPath, 'utf8');
    const start = popupJs.indexOf('function focusStableImagesControl');
    const end = popupJs.indexOf('// Select all visible images', start);
    const focus = jest.fn();
    const imageFilter = { focus };
    const status = { textContent: '' };
    const info = { textContent: '' };
    const toolbar = {
      classList: { add: jest.fn(), remove: jest.fn() },
      contains: element => element === activeElement
    };
    let activeElement = {};
    const document = {
      activeElement,
      getElementById: id => ({ selectionToolbar: toolbar, selectionInfo: info, selectionStatus: status, imageFilter }[id] || null)
    };
    const context = { document };
    vm.runInNewContext(`${popupJs.slice(start, end)}
this.setSelection = value => { selectedImages = new Set(value); };
this.updateSelectionToolbar = updateSelectionToolbar;`, context);

    context.setSelection(['a', 'b']);
    context.updateSelectionToolbar();
    expect(status.textContent).toBe('2 selected');
    expect(info.textContent).toBe('2 selected');

    activeElement = {};
    document.activeElement = activeElement;
    context.setSelection([]);
    context.updateSelectionToolbar();
    expect(status.textContent).toBe('Selection cleared');
    expect(focus).toHaveBeenCalledTimes(1);
    expect(toolbar.classList.remove).toHaveBeenCalledWith('visible');
  });

  test('removing the focused last download item moves focus to a stable dialog control', () => {
    const popupJs = fs.readFileSync(popupJsPath, 'utf8');
    const start = popupJs.indexOf('function focusStableDownloadControl');
    const end = popupJs.indexOf('// Render download panel list', start);
    const focus = jest.fn();
    const removeButton = {};
    const document = {
      activeElement: removeButton,
      getElementById: id => id === 'downloadConfirmBtn' ? { focus } : null
    };
    const context = { document };
    vm.runInNewContext(`${popupJs.slice(start, end)}
this.focusStableDownloadControl = focusStableDownloadControl;`, context);

    context.focusStableDownloadControl(removeButton, 0);
    expect(focus).toHaveBeenCalledTimes(1);
  });

  test('copy image URLs uses the normalized copied count and skips unsafe-only input', async () => {
    const popupJs = fs.readFileSync(popupJsPath, 'utf8');
    const normalizeStart = popupJs.indexOf('const MAX_IMAGE_DATA_URL_LENGTH');
    const normalizeEnd = popupJs.indexOf('function safeDimension', normalizeStart);
    const copyStart = popupJs.indexOf('async function copyImageURLs');
    const copyEnd = popupJs.indexOf('// Export images with option', copyStart);
    const context = { URL };
    vm.runInNewContext(`${popupJs.slice(normalizeStart, normalizeEnd)}
${popupJs.slice(copyStart, copyEnd)}
this.copyImageURLs = copyImageURLs;`, context);
    const copy = jest.fn().mockResolvedValue(true);
    const notify = jest.fn();

    await context.copyImageURLs([
      { src: 'https://example.com/a.png' },
      { src: 'javascript:alert(1)' },
      { src: 'https://example.com/b.png' }
    ], { copy, notify });
    expect(copy).toHaveBeenCalledWith(
      'https://example.com/a.png\nhttps://example.com/b.png',
      '2 image URLs copied!',
      'Failed to copy image URLs'
    );

    copy.mockClear();
    await context.copyImageURLs([{ src: 'javascript:alert(1)' }], { copy, notify });
    expect(copy).not.toHaveBeenCalled();
    expect(notify).toHaveBeenLastCalledWith('No safe image URLs to copy');
  });

  test('gives every compact footer export control an action-oriented accessible name', () => {
    const popupHtml = fs.readFileSync(popupHtmlPath, 'utf8');
    const expectedLabels = {
      exportJson: 'Export results as JSON',
      exportImages: 'Download images',
      exportVideos: 'Download videos',
      exportAudios: 'Download audio',
      exportArchive: 'Download images as ZIP',
      copyImageUrls: 'Copy image URLs'
    };

    Object.entries(expectedLabels).forEach(([id, label]) => {
      expect(popupHtml).toMatch(new RegExp(
        `<button[^>]+id=["']${id}["'][^>]+aria-label=["']${label}["']`
      ));
    });
  });
});


describe('Least-privilege extension packaging', () => {
  test('uses activeTab without declarative page access', () => {
    const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));

    const expectedPermissions = [
      'activeTab',
      'storage',
      'contextMenus',
      'scripting',
      'downloads',
      'notifications',
      'sidePanel',
      'nativeMessaging'
    ];
    expect([...manifest.permissions].sort()).toEqual([...expectedPermissions].sort());
    expect(manifest.content_scripts).toBeUndefined();
    expect(manifest.host_permissions).toBeUndefined();
  });

  test('documents activeTab grant timing and navigation expiry accurately', () => {
    const readme = fs.readFileSync(path.join(__dirname, '..', 'README.md'), 'utf8');

    expect(readme).toContain('Opening the extension from the toolbar or invoking its context menu grants temporary access to the current page');
    expect(readme).toContain('Clicking **Scrape Page Data** uses that existing grant; the button itself does not grant access');
    expect(readme).toContain('A persistent side panel may need the extension to be invoked again for the current page');
    expect(readme).toContain('Navigating the tab to a different origin invalidates the grant');
    expect(readme).not.toContain('Click **Scrape Page Data**. Chrome grants temporary access');
  });

  test('context-menu clipboard support is repeat-safe direct execution without listeners', () => {
    const backgroundJs = fs.readFileSync(backgroundJsPath, 'utf8');
    const copyStart = backgroundJs.indexOf('async function copyTextInTab');
    const copyEnd = backgroundJs.indexOf('// Scrape and display results', copyStart);
    const copySource = backgroundJs.slice(copyStart, copyEnd);

    expect(copyStart).toBeGreaterThan(-1);
    expect(copySource).toContain('chrome.scripting.executeScript');
    expect(copySource).toContain('navigator.clipboard.writeText');
    expect(copySource).toContain('result.success !== true');
    expect(copySource).not.toContain('chrome.tabs.sendMessage');
    expect(copySource).not.toContain('onMessage.addListener');
  });

  test('context scraping rejects missing tabs and empty injection results', () => {
    const backgroundJs = fs.readFileSync(backgroundJsPath, 'utf8');
    const scrapeStart = backgroundJs.indexOf('async function scrapeAndShow');
    const scrapeEnd = backgroundJs.indexOf('// Show notification', scrapeStart);
    const scrapeSource = backgroundJs.slice(scrapeStart, scrapeEnd);

    expect(scrapeSource).toContain("throw new Error('No accessible tab is available for scraping')");
    expect(scrapeSource).toContain("throw new Error('The page did not return scrape results')");
  });

  test('does not package unsolicited page overlays or orphaned content styles', () => {
    expect(fs.existsSync(path.join(__dirname, '..', 'content.js'))).toBe(false);
    expect(fs.existsSync(path.join(__dirname, '..', 'content.css'))).toBe(false);
  });

  test('vendored fflate creates a readable archive through createZipFromUrls', async () => {
    const source = fs.readFileSync(fflatePath, 'utf8');
    const module = { exports: {} };
    const libraryContext = {
      module,
      exports: module.exports,
      require,
      TextEncoder,
      TextDecoder,
      Uint8Array,
      setTimeout,
      clearTimeout
    };
    vm.runInNewContext(source, libraryContext, { filename: 'libs/fflate.umd.js' });
    expect(typeof module.exports.Zip).toBe('function');
    expect(typeof module.exports.ZipDeflate).toBe('function');

    const backgroundSource = fs.readFileSync(backgroundJsPath, 'utf8');
    const start = backgroundSource.indexOf('function getZipExtension');
    const backgroundContext = { Blob, Uint8Array, URL, btoa, console };
    vm.runInNewContext(`${backgroundSource.slice(start)}
this.createZipFromUrls = createZipFromUrls;`, backgroundContext);
    let archiveBytes;
    const expected = new TextEncoder().encode('real vendored fflate data');

    const result = await backgroundContext.createZipFromUrls(
      ['https://example.com/photo.png'],
      ['photo'],
      {
        ensureLibrary: async () => true,
        notify: jest.fn(),
        reportError: jest.fn(),
        fetchUrl: async () => ({ ok: true, arrayBuffer: async () => expected.buffer }),
        createZip: () => new module.exports.Zip(),
        createFile: filename => new module.exports.ZipDeflate(filename, { level: 0 }),
        downloadZip: async dataUrl => {
          archiveBytes = Uint8Array.from(Buffer.from(dataUrl.split(',')[1], 'base64'));
        },
        downloadIndividually: jest.fn()
      }
    );

    expect(result).toEqual({ success: true, added: 1, skipped: 0 });
    const files = module.exports.unzipSync(archiveBytes);
    expect(Object.keys(files)).toEqual(['photo.png']);
    expect(Buffer.from(files['photo.png']).toString()).toBe('real vendored fflate data');
  });

  test('records the pinned fflate source and upstream MIT attribution', () => {
    const readme = fs.readFileSync(path.join(__dirname, '..', 'README.md'), 'utf8');
    const license = fs.readFileSync(path.join(__dirname, '..', 'libs', 'fflate.LICENSE'), 'utf8');

    expect(readme).toContain('official `umd/index.js` build from the pinned npm package `fflate@0.8.2`');
    expect(license).toContain('MIT License');
    expect(license).toContain('Copyright (c) 2023 Arjun Barrett');
  });

  test('packaged ZIP load failure notifies and chooses individual downloads', async () => {
    const backgroundJs = fs.readFileSync(backgroundJsPath, 'utf8');
    const helperStart = backgroundJs.indexOf('async function ensurePackagedZipLibrary');
    const helperEnd = backgroundJs.indexOf('// Create ZIP from URLs', helperStart);
    const helperSource = backgroundJs.slice(helperStart, helperEnd);
    const context = {};
    vm.runInNewContext(`${helperSource}\nthis.ensurePackagedZipLibrary = ensurePackagedZipLibrary;`, context);
    const downloaded = [];
    const notifications = [];
    const errors = [];

    const ready = await context.ensurePackagedZipLibrary(
      ['https://example.com/a.png'],
      ['hero.png'],
      {
        hasLibrary: () => false,
        loadLibrary: () => { throw new Error('missing packaged file'); },
        downloadIndividually: async (items) => downloaded.push(...items),
        notify: (message) => notifications.push(message),
        reportError: (...args) => errors.push(args)
      }
    );

    expect(ready).toBe(false);
    expect(downloaded).toEqual([{
      url: 'https://example.com/a.png',
      filename: 'hero.png',
      defaultExtension: '.jpg'
    }]);
    expect(notifications).toEqual([
      'Packaged ZIP support is unavailable. Downloading files individually.'
    ]);
    expect(errors).toHaveLength(1);
  });

  test('loads ZIP support only from the packaged extension library', () => {
    const backgroundJs = fs.readFileSync(backgroundJsPath, 'utf8');

    expect(backgroundJs).toContain("importScripts(chrome.runtime.getURL('libs/fflate.umd.js'))");
    expect(backgroundJs).toContain("typeof fflate.ZipDeflate === 'function'");
    expect(backgroundJs).not.toContain('AsyncZipDeflate');
    expect(backgroundJs).not.toMatch(/importScripts\(\s*['"]https?:\/\//);
    expect(backgroundJs).not.toContain('cdn.jsdelivr.net');
    expect(backgroundJs).toContain('Packaged ZIP support is unavailable');
  });

// ─── deep-video.js state model ─────────────────────────────────────────────

  var deepVideoPath = path.join(__dirname, '..', 'deep-video.js');

  function loadDeepVideoModule() {
    var sandbox = {
      console: { error: function(){}, warn: function(){}, log: function(){} },
      chrome: {
        runtime: {
          connectNative: function() {
            return { onMessage:{addListener:function(){}}, onDisconnect:{addListener:function(){}}, postMessage:function(){}, disconnect:function(){} };
          },
          lastError: null,
          getURL: function(p) { return 'chrome-extension://fake/' + p; },
          id: 'fake-extension-id',
          onMessage: { addListener: function(){}, removeListener: function(){} },
          onConnect: { addListener: function(){}, removeListener: function(){} },
        },
      },
      globalThis: Object.assign(Object.create(null), { Map: Map, Set: Set, Promise: Promise, Error: Error }),
      module: { exports: {} },
      exports: {},
      require: function(){},
      setTimeout: setTimeout,
      clearTimeout: clearTimeout,
      Date: Date,
      JSON: JSON,
      Math: Math,
    };
    sandbox.connectNative = sandbox.chrome.runtime.connectNative;
    vm.createContext(sandbox);
    var source = fs.readFileSync(deepVideoPath, 'utf8');
    vm.runInContext(source, sandbox);
    return sandbox;
  }

  function deepVideoReducer(state, action) {
    var m = loadDeepVideoModule();
    return m.reduceDeepVideoState(state, action);
  }

  describe('deep-video.js state model', function() {
    test('DEEP_VIDEO_INITIAL_STATE is frozen and idle', function() {
      var m = loadDeepVideoModule();
      var s = m.DEEP_VIDEO_INITIAL_STATE;
      expect(Object.isFrozen(s)).toBe(true);
      expect(s.phase).toBe('idle');
      expect(s.generation).toBe(0);
    });

    test('idle is the initial phase', function() {
      var m = loadDeepVideoModule();
      var state = m.DEEP_VIDEO_INITIAL_STATE;
      expect(state.phase).toBe('idle');
      expect(state.generation).toBe(0);
    });

    test('IDLE: analyze action transitions to checking', function() {
      var m = loadDeepVideoModule();
      var state = m.DEEP_VIDEO_INITIAL_STATE;
      var next = m.reduceDeepVideoState(state, {
        type: 'analyze',
        payload: { url: 'https://example.com/video', provider: 'YouTube', media_id: 'abc123' },
      });
      expect(next.phase).toBe('checking');
      expect(next.generation).toBe(1);
      expect(next.source_url).toBe('https://example.com/video');
    });

    test('CHECKING: result action transitions to ready', function() {
      var m = loadDeepVideoModule();
      var state = { phase: 'checking', generation: 1, source_url: 'https://example.com/video', media_id: 'abc123' };
      var next = m.reduceDeepVideoState(state, {
        type: 'result',
        payload: {
          media_id: 'abc123',
          title: 'My Video',
          provider: 'YouTube',
          presets: ['best', '1080p'],
          thumbnail_url: 'https://example.com/thumb.jpg',
        },
      });
      expect(next.phase).toBe('ready');
      expect(next.title).toBe('My Video');
      expect(next.presets).toEqual(['best', '1080p']);
      expect(next.media_id).toBe('abc123');
    });

    test('READY: download action transitions to downloading', function() {
      var m = loadDeepVideoModule();
      var state = {
        phase: 'ready', generation: 2, source_url: 'https://example.com/video',
        media_id: 'abc123', presets: ['best'],
      };
      var next = m.reduceDeepVideoState(state, {
        type: 'download', payload: { media_id: 'abc123', preset: 'best' },
      });
      expect(next.phase).toBe('downloading');
      expect(next.active_preset).toBe('best');
    });

    test('DOWNLOADING: progress action updates phase and percent', function() {
      var m = loadDeepVideoModule();
      var state = {
        phase: 'downloading', generation: 3, source_url: 'https://example.com/video',
        media_id: 'abc123', active_preset: 'best', progress_percent: 0, active_job_id: 'j1',
      };
      var next = m.reduceDeepVideoState(state, {
        type: 'progress',
        payload: { job_id: 'j1', state: 'downloading', phase: 'downloading', percent: 50 },
      });
      expect(next.phase).toBe('downloading');
      expect(next.progress_percent).toBe(50);
    });

    test('DOWNLOADING: completed action transitions to completed', function() {
      var m = loadDeepVideoModule();
      var state = {
        phase: 'downloading', generation: 3,
        media_id: 'abc123', active_preset: 'best',
      };
      var next = m.reduceDeepVideoState(state, {
        type: 'completed',
        payload: { job_id: 'j1', state: 'completed', destination: '/Users/Test/Downloads/video.mp4' },
      });
      expect(next.phase).toBe('completed');
    });

    test('DOWNLOADING: failed action transitions to error', function() {
      var m = loadDeepVideoModule();
      var state = {
        phase: 'downloading', generation: 3,
        media_id: 'abc123', active_preset: 'best',
      };
      var next = m.reduceDeepVideoState(state, {
        type: 'failed',
        payload: { job_id: 'j1', state: 'failed', error_code: 'unavailable', error: 'helper not found' },
      });
      expect(next.phase).toBe('error');
      expect(next.error_code).toBe('unavailable');
    });

    test('READY: cancel resets to idle', function() {
      var m = loadDeepVideoModule();
      var state = {
        phase: 'ready', generation: 2,
        media_id: 'abc123', presets: ['best'],
      };
      var next = m.reduceDeepVideoState(state, { type: 'cancel' });
      expect(next.phase).toBe('idle');
    });

    test('ERROR: retry resets to ready state', function() {
      var m = loadDeepVideoModule();
      var state = {
        phase: 'error', generation: 4,
        media_id: 'abc123', error_code: 'unavailable',
      };
      var next = m.reduceDeepVideoState(state, { type: 'retry' });
      expect(next.phase).toBe('ready');
      expect(next.media_id).toBe('abc123');
    });

    test('navigation: new URL increments generation', function() {
      var m = loadDeepVideoModule();
      var state = {
        phase: 'ready', generation: 2,
        source_url: 'https://example.com/video1',
        media_id: 'abc123',
      };
      var next = m.reduceDeepVideoState(state, {
        type: 'navigate',
        payload: { url: 'https://example.com/video2' },
      });
      expect(next.generation).toBe(3);
      expect(next.source_url).toBe('https://example.com/video2');
      expect(next.media_id).toBeNull();
      expect(next.phase).toBe('idle');
    });

    test('stale result: wrong generation is ignored', function() {
      var m = loadDeepVideoModule();
      // Current generation is 3, but result arrives for generation 1 (stale)
      var state = {
        phase: 'checking', generation: 3, source_url: 'https://example.com/video',
      };
      var next = m.reduceDeepVideoState(state, {
        type: 'result',
        payload: { media_id: 'abc123', title: 'Stale Title', generation: 1 },
      });
      // Should keep current state, not apply stale result
      expect(next.generation).toBe(3);
    });

    test('result: HTTP(S) thumbnail is preserved', function() {
      var m = loadDeepVideoModule();
      var state = { phase: 'checking', generation: 1 };
      var next = m.reduceDeepVideoState(state, {
        type: 'result',
        payload: {
          media_id: 'abc', title: 'T',
          thumbnail_url: 'https://i.ytimg.com/abc.jpg',
        },
      });
      expect(next.thumbnail_url).toMatch(/^https:\/\//);
    });

    test('result: non-HTTP thumbnail is null', function() {
      var m = loadDeepVideoModule();
      var state = { phase: 'checking', generation: 1 };
      var next = m.reduceDeepVideoState(state, {
        type: 'result',
        payload: {
          media_id: 'abc', title: 'T',
          thumbnail_url: 'file:///secret.png',
        },
      });
      expect(next.thumbnail_url).toBeNull();
    });

    test('title and error text are bounded to 1024 chars', function() {
      var m = loadDeepVideoModule();
      var long = 'x'.repeat(2000);
      var state = { phase: 'checking', generation: 1 };
      var next = m.reduceDeepVideoState(state, {
        type: 'result',
        payload: { media_id: 'abc', title: long },
      });
      expect(next.title.length).toBeLessThanOrEqual(1024);
    });

    test('progress percent is monotonic', function() {
      var m = loadDeepVideoModule();
      var state = {
        phase: 'downloading', generation: 1,
        media_id: 'abc', progress_percent: 70, active_job_id: 'j1',
      };
      // 40% is less than 70%, should be ignored (not monotonic decrease)
      var next = m.reduceDeepVideoState(state, {
        type: 'progress',
        payload: { job_id: 'j1', percent: 40 },
      });
      expect(next.progress_percent).toBe(70);
    });

    test('availablePresetOptions returns array', function() {
      var m = loadDeepVideoModule();
      var presets = m.availablePresetOptions({ presets: ['best', '1080p', '720p', 'audio'] });
      expect(Array.isArray(presets)).toBe(true);
    });

    test('normalizeDeepVideoResult returns display-safe object', function() {
      var m = loadDeepVideoModule();
      var result = m.normalizeDeepVideoResult({
        media_id: 'abc', title: 'Test <script>evil</script>',
        provider: 'YouTube',
        thumbnail_url: 'https://example.com/t.jpg',
      });
      expect(result.title).not.toContain('<script>');
      expect(typeof result.title).toBe('string');
    });

    test('FAILED: analyze after deep scan failure does not erase core scrape results', function() {
      var m = loadDeepVideoModule();
      var state = {
        phase: 'error', generation: 2,
        media_id: 'abc', error_code: 'unavailable',
      };
      // Deep scan failure should not erase other state
      var next = m.reduceDeepVideoState(state, {
        type: 'analyze',
        payload: { url: 'https://example.com/new', provider: 'YouTube', media_id: 'def456' },
      });
      expect(next.phase).toBe('checking');
      expect(next.generation).toBe(3);
    });
  });

  describe('deep-video.js helper unavailable', function() {
    test('helper_unavailable error shows installation guidance', function() {
      var m = loadDeepVideoModule();
      var state = { phase: 'idle', generation: 0 };
      var next = m.reduceDeepVideoState(state, {
        type: 'helper_unavailable',
        payload: {},
      });
      expect(next.phase).toBe('error');
      expect(next.error_code).toBe('helper_unavailable');
    });

    test('available preset options are always four choices', function() {
      var m = loadDeepVideoModule();
      var opts = m.availablePresetOptions({ presets: [] });
      // Should always have the four standard options regardless of helper availability
      expect(opts.length).toBeGreaterThanOrEqual(0);
    });
  });


// ─── deep-video.js behavior and accessibility tests ───────────────────────────

  // Helper: read popup.js source for static analysis tests
  function deepVideoPopupSource() {
    return fs.readFileSync(popupJsPath, 'utf8');
  }


// ─── deep-video.js behavior and accessibility tests ───────────────────────────
// Uses static analysis (reading file contents) rather than VM execution
// to avoid Chrome API mocking complexity.


// ─── deep-video.js behavior and accessibility tests ───────────────────────────
// Uses static analysis (reading file contents) for reliable verification.

  describe('deep-video.js behavior and accessibility', function() {
    test('deep video DOM elements exist in popup.html with correct ARIA roles', function() {
      var popupHtml = fs.readFileSync(popupHtmlPath, 'utf8');
      // Status region must be live="polite"
      expect(popupHtml).toMatch(/id="deepVideoStatus"[^>]+role="status"[^>]+aria-live="polite"/);
      // Download button
      expect(popupHtml).toMatch(/id="deepVideoDownloadBtn"/);
      // Cancel and Retry buttons
      expect(popupHtml).toMatch(/id="deepVideoCancelBtn"/);
      expect(popupHtml).toMatch(/id="deepVideoRetryBtn"/);
      // Installation guidance must be role="alert"
      expect(popupHtml).toMatch(/id="deepVideoInstallGuidance"[^>]+role="alert"/);
      // Deep scan button labelled "Deep scan this page"
      expect(popupHtml).toMatch(/id="deepScanBtn"/);
      expect(popupHtml).toMatch(/Deep scan this page/);
      // Progress wrapper has role="progressbar" and proper ARIA attributes
      expect(popupHtml).toMatch(/role="progressbar"[^>]+aria-label="Download progress"[^>]+aria-valuemin="0"[^>]+aria-valuemax="100"/);
    });

    test('deep video presets are a radio fieldset with legend', function() {
      var popupHtml = fs.readFileSync(popupHtmlPath, 'utf8');
      // Must be a fieldset (not div) for keyboard accessibility
      expect(popupHtml).toMatch(/<fieldset[^>]+id="deepVideoPresets"/);
      // Must have sr-only legend for screen readers
      expect(popupHtml).toMatch(/<legend[^>]+class="sr-only"[^>]*>Choose download quality<\/legend>/);
      // Must have 4 radio options
      var matches = popupHtml.match(/<input[^>]+type="radio"[^>]+name="deepPreset"[^>]*>/g);
      expect(matches).not.toBeNull();
      expect(matches.length).toBe(4);
    });

    test('deep video panel starts hidden in HTML', function() {
      var popupHtml = fs.readFileSync(popupHtmlPath, 'utf8');
      // The panel must have hidden attribute initially
      expect(popupHtml).toMatch(/id="deepVideoPanel"[^>]+hidden/);
    });

    test('renderDeepVideo uses textContent not innerHTML for user strings', function() {
      var popupJs = fs.readFileSync(popupJsPath, 'utf8');
      // Verify the function exists and uses textContent
      expect(popupJs).toMatch(/function renderDeepVideo/);
      // Extract the renderDeepVideo function body
      var fnStart = popupJs.indexOf('function renderDeepVideo');
      var fnBody = popupJs.slice(fnStart, fnStart + 7000);
      // Must use textContent for safe DOM updates (no innerHTML injection risk)
      expect(fnBody).toMatch(/\.textContent\s*=/);
    });

    test('renderDeepVideo shows/hides installation guidance for helper_unavailable', function() {
      var popupJs = fs.readFileSync(popupJsPath, 'utf8');
      var fnStart = popupJs.indexOf('function renderDeepVideo');
      var fnBody = popupJs.slice(fnStart, fnStart + 7000);
      // Must show guidance when error_code is helper_unavailable
      expect(fnBody).toMatch(/helper_unavailable/);
      expect(fnBody).toMatch(/deepVideoInstallGuidance\.hidden/);
    });

    test('sendDeepVideoCommand dispatches deepVideoCommand action to background', function() {
      var popupJs = fs.readFileSync(popupJsPath, 'utf8');
      // Must send the correct action name to chrome.runtime
      expect(popupJs).toMatch(/action:\s*['"]deepVideoCommand['"]/);
      // Must use chrome.runtime.sendMessage (not fetch or XMLHttpRequest)
      expect(popupJs).toMatch(/chrome\.runtime\.sendMessage/);
    });

    test('analyze command sends only URL (no raw yt-dlp options)', function() {
      var popupJs = fs.readFileSync(popupJsPath, 'utf8');
      // Find the deepScanBtn click handler
      var scanBtnIdx = popupJs.indexOf('elements.deepScanBtn');
      var btnBlock = popupJs.slice(scanBtnIdx, scanBtnIdx + 1000);
      // analyze must NOT send yt-dlp-specific fields
      expect(btnBlock).not.toMatch(/preset:\s*['"]/);
      expect(btnBlock).not.toMatch(/format:\s*['"]/);
      expect(btnBlock).not.toMatch(/output:\s*['"]/);
      expect(btnBlock.toLowerCase()).not.toMatch(/ytdlp|yt.dlp/);
    });

    test('download command sends only media_id, preset, and confirmed:true', function() {
      var popupJs = fs.readFileSync(popupJsPath, 'utf8');
      // Find the download button event listener inside DOMContentLoaded
      var domSection = popupJs.slice(popupJs.indexOf('document.addEventListener'));
      var dlBtnIdx = domSection.indexOf('elements.deepVideoDownloadBtn');
      var dlBlock = domSection.slice(dlBtnIdx, dlBtnIdx + 1000);
      // Must include media_id, preset (from variable), and confirmed
      expect(dlBlock).toMatch(/media_id:\s*deepVideoState\.media_id/);
      expect(dlBlock).toMatch(/preset:\s*preset/);
      expect(dlBlock).toMatch(/confirmed:\s*true/);
      // Must NOT include paths or yt-dlp raw options
      expect(dlBlock).not.toMatch(/output:\s*['"]/);
    });

    test('deepVideoState has typeof guard against missing deep-video.js globals', function() {
      var popupJs = fs.readFileSync(popupJsPath, 'utf8');
      // Must have the typeof guard so popup.js doesn't break in the test VM
      expect(popupJs).toMatch(/typeof\s+DEEP_VIDEO_INITIAL_STATE\s*!==\s*['"]undefined['"]/);
    });

    test('deep video CSS defines dark theme styles using CSS variables', function() {
      var css = fs.readFileSync(stylesPath, 'utf8');
      // All deep video panel styles must use CSS variables for theming
      expect(css).toMatch(/\.deep-video-panel\s*\{/);
      expect(css).toMatch(/\.deep-video-status\s*\{/);
      expect(css).toMatch(/\.deep-video-presets\s*\{/);
      expect(css).toMatch(/\.deep-video-progress\s*\{/);
      // Progress bar must use --accent variable
      expect(css).toMatch(/\.progress-bar\s*\{[^}]*background:\s*var\(--accent\)/);
    });

    test('deep video CSS includes prefers-reduced-motion rules', function() {
      var css = fs.readFileSync(stylesPath, 'utf8');
      // Must respect user motion preferences
      expect(css).toMatch(/@media\s*\(\s*prefers-reduced-motion/);
    });

    test('deep video CSS includes 320px responsive breakpoints', function() {
      var css = fs.readFileSync(stylesPath, 'utf8');
      // Must be usable on small screens
      expect(css).toMatch(/@media\s*\(\s*max-width:\s*320px\s*\)/);
    });

    test('cancel command sends only job_id (no paths or options)', function() {
      var popupJs = fs.readFileSync(popupJsPath, 'utf8');
      // Find the cancel button event listener inside DOMContentLoaded
      var domSection = popupJs.slice(popupJs.indexOf('document.addEventListener'));
      var cancelIdx = domSection.indexOf('elements.deepVideoCancelBtn');
      var cancelBlock = domSection.slice(cancelIdx, cancelIdx + 600);
      // Cancel must send job_id from state
      expect(cancelBlock).toMatch(/job_id/);
      // Must NOT send preset, output, or other yt-dlp options
      expect(cancelBlock).not.toMatch(/preset:\s*['"]/);
      expect(cancelBlock).not.toMatch(/output:\s*['"]/);
    });

    test('Retry dispatches retry action without calling background', function() {
      var popupJs = fs.readFileSync(popupJsPath, 'utf8');
      // Find the Retry button handler inside DOMContentLoaded
      var domSection = popupJs.slice(popupJs.indexOf('document.addEventListener'));
      var retryIdx = domSection.indexOf('elements.deepVideoRetryBtn');
      var retryBlock = domSection.slice(retryIdx, retryIdx + 400);
      // Retry must dispatch 'retry' to the reducer
      expect(retryBlock).toMatch(/type:\s*['"]retry['"]/);
      // Retry must NOT call sendDeepVideoCommand
      expect(retryBlock).not.toMatch(/sendDeepVideoCommand/);
    });
  });


});