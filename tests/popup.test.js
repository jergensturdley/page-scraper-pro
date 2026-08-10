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
this.popupHelpers = { getVideoProviderId, normalizeVideoUrl, buildExportData, classifyImageCandidate, getWorkspaceCacheKey, serializeWorkspace, normalizeWorkspace, createWorkspaceTabController, normalizeSourceDocumentUrl, getWorkspaceEvictionKeys, setWorkspaceWithEviction };`, context);
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
      popupJs.indexOf('// Detect if running as a side panel')
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

  test('content clipboard listener reports asynchronous success and failure', () => {
    const contentJs = fs.readFileSync(path.join(__dirname, '..', 'content.js'), 'utf8');
    const listener = contentJs.slice(contentJs.indexOf('chrome.runtime.onMessage.addListener'));
    expect(listener).toContain('await navigator.clipboard.writeText(request.text);');
    expect(listener).toContain('sendResponse({ success: true });');
    expect(listener).toMatch(/sendResponse\(\{ success: false, error:/);
    expect(listener).toContain('return true;');
  });

  test('background only announces clipboard success after a confirmed response', () => {
    const backgroundJs = fs.readFileSync(backgroundJsPath, 'utf8');
    expect(backgroundJs).toContain('async function copyTextInTab(tabId, text)');
    expect(backgroundJs).toContain('chrome.runtime.lastError');
    expect(backgroundJs).toContain("if (!response || response.success !== true)");
    expect(backgroundJs).toContain("showNotification('Could not copy to clipboard:");
    expect(backgroundJs).toContain('await copyTextInTab(');
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
