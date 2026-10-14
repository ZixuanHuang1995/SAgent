// 開發工具：用前端同一版 Mermaid 檢查圖表語法。
// 用法：node scripts/mermaid-validate.mjs < input.json
// input.json 是字串陣列（每個元素是一段 Mermaid 程式碼），輸出每段的檢查結果。
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { pretendToBeVisual: true });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
for (const key of ['DOMParser', 'Element', 'HTMLElement', 'SVGElement', 'Node', 'navigator']) {
  if (!(key in globalThis)) Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
}

const { default: mermaid } = await import('mermaid');
mermaid.initialize({ startOnLoad: false });

let raw = '';
for await (const chunk of process.stdin) raw += chunk;
const codes = JSON.parse(raw);
const results = [];
for (const code of codes) {
  try {
    const r = await mermaid.parse(code);
    results.push({ ok: true, type: r?.diagramType ?? '' });
  } catch (e) {
    results.push({ ok: false, error: String(e?.message ?? e).split('\n').slice(0, 4).join(' ') });
  }
}
console.log(JSON.stringify(results, null, 1));
