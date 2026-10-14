// Markdown 編輯器（CodeMirror）

import { markdown } from '@codemirror/lang-markdown'
import { EditorView } from '@codemirror/view'
import CodeMirror, { type ReactCodeMirrorRef } from '@uiw/react-codemirror'
import { forwardRef, useImperativeHandle, useMemo, useRef } from 'react'
import { useIsDark } from '../theme'

export interface EditorHandle {
  scrollToLine: (line: number) => void
  /** 在游標位置插入文字（前後自動補空行）。編輯器還沒準備好時回傳 false */
  insertBlock: (text: string) => boolean
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
  const extensions = useMemo(() => [markdown(), EditorView.lineWrapping], [])
  useImperativeHandle(ref, () => ({
    scrollToLine(line: number) {
      const view = cm.current?.view
      if (!view) return
      const target = view.state.doc.line(Math.min(Math.max(line, 1), view.state.doc.lines))
      view.dispatch({ selection: { anchor: target.from }, effects: EditorView.scrollIntoView(target.from, { y: 'start' }) })
      view.focus()
    },
    insertBlock(text: string) {
      const view = cm.current?.view
      if (!view) return false
      // 游標還在最前面（使用者沒點過編輯器）時，插到文件最後
      const head = view.state.selection.main.head
      const pos = head === 0 ? view.state.doc.length : head
      const before = view.state.doc.sliceString(Math.max(0, pos - 2), pos)
      const prefix = pos === 0 || before === '\n\n' ? '' : before.endsWith('\n') ? '\n' : '\n\n'
      const insert = `${prefix}${text}\n`
      view.dispatch({
        changes: { from: pos, insert },
        selection: { anchor: pos + insert.length },
        effects: EditorView.scrollIntoView(pos + insert.length),
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
      theme={dark ? 'dark' : 'light'}
      readOnly={readOnly}
      height="100%"
      onCreateEditor={(view) => view.scrollDOM.addEventListener('scroll', () => onScrollRef.current?.(), { passive: true })}
      basicSetup={{ foldGutter: false, highlightActiveLine: true, autocompletion: false }}
    />
  )
})
