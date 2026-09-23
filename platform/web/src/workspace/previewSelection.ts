// Selection is an untrusted editing hint, never approval or executable input.
export type PreviewSelection = { selector: string; label: string };
export function readPreviewSelection(value: unknown): PreviewSelection | null {
  if (!value || typeof value !== 'object') return null;
  const item = value as Record<string, unknown>;
  if (typeof item.selector !== 'string' || !item.selector || item.selector.length > 240 ||
      typeof item.label !== 'string' || !item.label || item.label.length > 160 ||
      /[\0\r\n]/.test(item.selector + item.label)) return null;
  return { selector: item.selector, label: item.label };
}
// These scripts stay in the existing opaque, network-blocked preview frames.
export function selectionScript(channel: string): string {
  return `(() => {
    const channel = ${JSON.stringify(channel)};
    let enabled = false, picked;
    const clear = () => { if (picked) { picked.style.outline = picked.dataset.studioOutline || ''; delete picked.dataset.studioOutline; picked = null; } };
    addEventListener('message', event => {
      if (event.source !== parent || event.data?.channel !== channel || event.data?.type !== 'studio-select-mode') return;
      enabled = event.data.enabled === true;
      if (!enabled) clear();
    });
    document.addEventListener('click', event => {
      if (!enabled || !(event.target instanceof HTMLElement)) return;
      event.preventDefault(); event.stopImmediatePropagation();
      const element = event.target;
      let selector = element.tagName.toLowerCase();
      if (element.id) selector = '#' + CSS.escape(element.id);
      else if (element.getAttribute('data-testid')) selector = '[data-testid="' + CSS.escape(element.getAttribute('data-testid')) + '"]';
      else {
        let current = element, parts = [];
        while (current && current !== document.body && parts.length < 5) {
          parts.unshift(current.tagName.toLowerCase() + ':nth-child(' + (Array.from(current.parentElement?.children || []).indexOf(current) + 1) + ')');
          current = current.parentElement;
        }
        selector = parts.join(' > ') || selector;
      }
      const text = element.getAttribute('aria-label') || element.textContent || element.tagName.toLowerCase();
      const label = (element.tagName.toLowerCase() + ' · ' + text.trim().replace(/\\s+/g, ' ')).slice(0, 160);
      clear(); picked = element; picked.dataset.studioOutline = picked.style.outline; picked.style.outline = '3px solid #008485';
      parent.postMessage({ type: 'studio-element', channel, selection: { selector: selector.slice(0, 240), label } }, '*');
    }, true);
  })();`;
}
export function selectionRelayScript(channel: string): string {
  return `(() => {
    const channel = ${JSON.stringify(channel)}, frame = document.querySelector('[data-workspace-preview-content]');
    let enabled = false;
    const sync = () => frame.contentWindow.postMessage({ type: 'studio-select-mode', channel, enabled }, '*');
    frame.addEventListener('load', sync);
    addEventListener('message', event => {
      if (event.data?.channel !== channel) return;
      if (event.source === parent && event.data.type === 'studio-select-mode') { enabled = event.data.enabled === true; sync(); }
      if (event.source === frame.contentWindow && event.data.type === 'studio-element')
        parent.postMessage({ type: 'studio-element', channel, selection: event.data.selection }, '*');
    });
  })();`;
}
