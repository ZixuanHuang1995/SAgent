// 編輯器的「插入圖表」選單：最上面是這份文件常用的圖，其他依用途分組

import { Fragment, useState } from 'react'
import type { DiagramKind, Template } from '../api'
import { useDiagramKinds, useHealth, useSettings } from '../queries'

export type DiagramLang = 'mermaid' | 'plantuml'

/** 跟 styles.css 的 .insert-menu min-width 一樣 */
const MENU_WIDTH = 280

export function DiagramMenu({
  template,
  onInsert,
}: {
  template?: Template
  onInsert: (name: string, lang: DiagramLang, code: string) => void
}) {
  const kinds = useDiagramKinds()
  const plantumlOn = useHealth().data?.plantuml.available ?? false
  const umlSyntax = useSettings().data?.uml_syntax
  const [picked, setPicked] = useState<DiagramLang | null>(null)
  // 選單預設對齊按鈕右邊；按鈕左邊放不下選單時（窄螢幕工具列換行）改成對齊左邊
  const [alignLeft, setAlignLeft] = useState(false)
  if (!kinds.data) return null

  // 沒有 PlantUML 時只有 Mermaid；有的話預設跟設定的「UML 圖語法」，「全部用 PlantUML」才預設 PlantUML
  const lang: DiagramLang = plantumlOn ? (picked ?? (umlSyntax === 'plantuml' ? 'plantuml' : 'mermaid')) : 'mermaid'
  const shown = kinds.data.filter((k) => k[lang]) // PlantUML 沒有的圖（例如象限圖）不列
  const commonKeys = template?.allow_diagrams ? [...new Set(template.sections.flatMap((s) => s.diagrams))] : []
  const common = commonKeys.flatMap((key) => shown.filter((k) => k.key === key))
  const groups = [...new Set(shown.map((k) => k.group))]

  const item = (k: DiagramKind) => (
    <button key={k.key} className="insert-item" onClick={() => onInsert(k.name, lang, k[lang])}>
      <span>{k.name}</span>
      <span className="insert-hint">{k.hint}</span>
    </button>
  )
  return (
    <details className="menu">
      <summary className="btn btn-sm" onClick={(e) => setAlignLeft(e.currentTarget.getBoundingClientRect().right < MENU_WIDTH + 16)}>
        插入圖表 ▾
      </summary>
      <div className={`menu-list insert-menu${alignLeft ? '' : ' menu-right'}`}>
        {plantumlOn && (
          // data-keep-open：切換語法時選單不要關掉
          <div className="seg seg-sm insert-syntax" role="group" aria-label="圖表語法" data-keep-open>
            {(['mermaid', 'plantuml'] as const).map((l) => (
              <button key={l} className={lang === l ? 'active' : ''} aria-pressed={lang === l} onClick={() => setPicked(l)}>
                {l === 'mermaid' ? 'Mermaid' : 'PlantUML'}
              </button>
            ))}
          </div>
        )}
        {common.length > 0 && (
          <>
            <div className="menu-group">這份文件常用</div>
            {common.map(item)}
            <div className="menu-sep" />
          </>
        )}
        {groups.map((g) => (
          <Fragment key={g}>
            <div className="menu-group">{g}</div>
            {shown.filter((k) => k.group === g).map(item)}
          </Fragment>
        ))}
      </div>
    </details>
  )
}
