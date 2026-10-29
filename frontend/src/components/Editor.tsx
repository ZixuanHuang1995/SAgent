// Markdown 編輯器（CodeMirror）

import { markdown } from '@codemirror/lang-markdown'
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language'
import { EditorSelection, type Text } from '@codemirror/state'
import { EditorView } from '@codemirror/view'
import { tags as t } from '@lezer/highlight'
import CodeMirror, { type ReactCodeMirrorRef } from '@uiw/react-codemirror'
import { forwardRef, useImperativeHandle, useMemo, useRef } from 'react'
import { useIsDark } from '../theme'

/** 編輯器外框的顏色，都用 styles.css 的變數，跟預覽區、整個畫面一致 */
const editorTheme = (dark: boolean) =>
  EditorView.theme(
    {
      '&': { backgroundColor: 'var(--surface)', color: 'var(--text)' },
      '.cm-content': { caretColor: 'var(--accent)' },
      '.cm-cursor, .cm-dropCursor': { borderLeftColor: 'var(--accent)' },
      // 目前這行的底色要半透明，選取的範圍才不會被蓋住
      '.cm-activeLine': { backgroundColor: 'color-mix(in srgb, var(--text) 4%, transparent)' },
      '.cm-selectionBackground, &.cm-focused .cm-selectionBackground, .cm-content ::selection': {
        backgroundColor: 'color-mix(in srgb, var(--accent) 24%, transparent)',
      },
      '.cm-selectionMatch': { backgroundColor: 'color-mix(in srgb, var(--accent) 12%, transparent)' },
      '.cm-gutters': { backgroundColor: 'var(--surface)', color: 'var(--muted)', border: 'none' },
      '.cm-activeLineGutter': { backgroundColor: 'transparent', color: 'var(--text-2)' },
    },
    { dark },
  )
const LIGHT_THEME = editorTheme(false)
const DARK_THEME = editorTheme(true)

/** Markdown 語法顏色：標題用主色，#、*、| 這類符號淡一點，內容本身好讀 */
const markdownHighlight = syntaxHighlighting(
  HighlightStyle.define([
    { tag: t.heading, color: 'var(--accent)', fontWeight: 'var(--fw-bold)' },
    { tag: t.strong, fontWeight: 'var(--fw-bold)' },
    { tag: t.emphasis, fontStyle: 'italic' },
    { tag: t.strikethrough, textDecoration: 'line-through' },
    { tag: t.link, color: 'var(--writer)' },
    { tag: [t.url, t.processingInstruction, t.contentSeparator, t.labelName, t.comment], color: 'var(--muted)' },
    { tag: [t.quote, t.monospace], color: 'var(--text-2)' },
  ]),
)

export interface EditorHandle {
  scrollToLine: (line: number) => void
  /** 在游標位置插入文字（前後自動補空行），select 是插入後要選取的範圍（相對於 text）。編輯器還沒準備好時回傳 false */
  insertBlock: (text: string, select?: [number, number]) => boolean
  /** 會捲動的元素（同步捲動用） */
  scroller: () => HTMLElement | null
  /** 第 line 行在捲動區裡的位置（像素） */
  lineTop: (line: number) => number | null
  /** 目前捲動區最上面是第幾行 */
  topLine: () => number
}

export const MarkdownEditor = forwardRef<
  EditorHandle,
  { value: string; onChange: (v: string) => void; readOnly?: boolean; onScroll?: () => void }
>(function MarkdownEditor({ value, onChange, readOnly, onScroll }, ref) {
  const dark = useIsDark()
  const cm = useRef<ReactCodeMirrorRef>(null)
  const onScrollRef = useRef(onScroll)
  onScrollRef.current = onScroll
  /** 文件開頭在捲動區裡的位置（上方留白） */
  const docOffset = (view: EditorView) =>
    view.documentTop - view.scrollDOM.getBoundingClientRect().top + view.scrollDOM.scrollTop
  const extensions = useMemo(() => [markdown(), EditorView.lineWrapping, markdownHighlight], [])
  useImperativeHandle(ref, () => ({
    scrollToLine(line: number) {
      const view = cm.current?.view
      if (!view) return
      const target = view.state.doc.line(Math.min(Math.max(line, 1), view.state.doc.lines))
      view.dispatch({ selection: { anchor: target.from }, effects: EditorView.scrollIntoView(target.from, { y: 'start' }) })
      view.focus()
    },
    insertBlock(text: string, select?: [number, number]) {
      const view = cm.current?.view
      if (!view) return false
      const pos = insertPos(view.state.doc, view.state.selection.main.head)
      const before = view.state.doc.sliceString(Math.max(0, pos - 2), pos)
      const prefix = pos === 0 || before === '\n\n' ? '' : before.endsWith('\n') ? '\n' : '\n\n'
      const insert = `${prefix}${text}\n`
      const end = pos + insert.length
      const start = pos + prefix.length
      const selection = select ? EditorSelection.range(start + select[0], start + select[1]) : EditorSelection.cursor(end)
      view.dispatch({
        changes: { from: pos, insert },
        selection,
        // 整段都捲進畫面；放不下時以選取的地方為主
        effects: EditorView.scrollIntoView(EditorSelection.range(end, selection.head)),
      })
      view.focus()
      return true
    },
    scroller() {
      return cm.current?.view?.scrollDOM ?? null
    },
    lineTop(line: number) {
      const view = cm.current?.view
      if (!view || line < 1 || line > view.state.doc.lines) return null
      return docOffset(view) + view.lineBlockAt(view.state.doc.line(line).from).top
    },
    topLine() {
      const view = cm.current?.view
      if (!view) return 1
      const block = view.lineBlockAtHeight(Math.max(0, view.scrollDOM.scrollTop - docOffset(view)))
      return view.state.doc.lineAt(block.from).number
    },
  }))
  return (
    <CodeMirror
      ref={cm}
      className="editor"
      value={value}
      onChange={onChange}
      extensions={extensions}
      theme={dark ? DARK_THEME : LIGHT_THEME}
      readOnly={readOnly}
      height="100%"
      onCreateEditor={(view) => view.scrollDOM.addEventListener('scroll', () => onScrollRef.current?.(), { passive: true })}
      basicSetup={{ foldGutter: false, highlightActiveLine: true, autocompletion: false }}
    />
  )
})

/**
 * 插入區塊的位置。游標還在最前面（使用者沒點過編輯器）時插到文件最後；
 * 游標在一行中間時插到這行後面，不把這行切開；這行後面緊接著程式碼區塊（例如圖名後面的圖表）時，插到區塊後面
 */
function insertPos(doc: Text, head: number): number {
  if (head === 0) return doc.length
  const line = doc.lineAt(head)
  if (head === line.from) return head
  let k = line.number + 1
  while (k <= doc.lines && doc.line(k).text.trim() === '') k++
  const fence = k <= doc.lines ? /^\s{0,3}(`{3,}|~{3,})/.exec(doc.line(k).text)?.[1] : undefined
  if (!fence) return line.to
  for (let j = k + 1; j <= doc.lines; j++) {
    const t = doc.line(j).text.trim()
    if (t.startsWith(fence) && /^[`~]+$/.test(t)) return doc.line(j).to
  }
  return doc.length
}
