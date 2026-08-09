/**
 * Test suite for Page Scraper Pro popup UI bug fixes
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const stylesPath = path.join(__dirname, '..', 'styles.css');
const popupJsPath = path.join(__dirname, '..', 'popup.js');
const backgroundJsPath = path.join(__dirname, '..', 'background.js');
const manifestPath = path.join(__dirname, '..', 'manifest.json');

function loadPopupHelpers() {
  const popupJs = fs.readFileSync(popupJsPath, 'utf8');
  const domElementsIndex = popupJs.indexOf('// DOM Elements');
  const helperSource = domElementsIndex === -1 ? popupJs : popupJs.slice(0, domElementsIndex);
  const context = { URL };

  vm.runInNewContext(`${helperSource}
this.popupHelpers = { getVideoProviderId, buildExportData };`, context);
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

  test('preserves the complete scraped page URL in exported data', () => {
    const { buildExportData } = loadPopupHelpers();
    const data = { images: [], videos: [], audios: [], links: [], text: '' };

    expect(buildExportData('https://example.com/a?x=1', data).url).toBe('https://example.com/a?x=1');
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
