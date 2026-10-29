// 編輯器的「插入圖表」選單：最上面是這份文件常用的圖，其他依用途分組

import { Fragment, useState } from 'react'
import type { DiagramKind, Template } from '../api'
import { useDiagramKinds, useHealth, useSettings } from '../queries'
import { Icon } from './icons'
import { Menu, MenuItem, MenuLabel, MenuSeparator } from './ui'

export type DiagramLang = 'mermaid' | 'plantuml'

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
  if (!kinds.data) return null

  // 沒有 PlantUML 時只有 Mermaid；有的話預設跟設定的「UML 圖語法」，「全部用 PlantUML」才預設 PlantUML
  const lang: DiagramLang = plantumlOn ? (picked ?? (umlSyntax === 'plantuml' ? 'plantuml' : 'mermaid')) : 'mermaid'
  const shown = kinds.data.filter((k) => k[lang]) // PlantUML 沒有的圖（例如象限圖）不列
  const commonKeys = template?.allow_diagrams ? [...new Set(template.sections.flatMap((s) => s.diagrams))] : []
  const common = commonKeys.flatMap((key) => shown.filter((k) => k.key === key))
  const groups = [...new Set(shown.map((k) => k.group))]

  const item = (k: DiagramKind) => (
    <MenuItem key={k.key} hint={k.hint} onSelect={() => onInsert(k.name, lang, k[lang])}>
      {k.name}
    </MenuItem>
  )
  return (
    <Menu
      trigger={
        <>
          插入圖表 <Icon name="chevron-down" size={14} />
        </>
      }
      triggerClassName="btn btn-sm"
      className="insert-menu"
    >
      {plantumlOn && (
        <>
          <MenuLabel>圖表語法</MenuLabel>
          {/* 切換語法時選單不要關掉 */}
          {(['mermaid', 'plantuml'] as const).map((l) => (
            <MenuItem key={l} checked={lang === l} keepOpen onSelect={() => setPicked(l)}>
              {l === 'mermaid' ? 'Mermaid' : 'PlantUML'}
            </MenuItem>
          ))}
          <MenuSeparator />
        </>
      )}
      {common.length > 0 && (
        <>
          <MenuLabel>這份文件常用</MenuLabel>
          {common.map(item)}
          <MenuSeparator />
        </>
      )}
      {groups.map((g) => (
        <Fragment key={g}>
          <MenuLabel>{g}</MenuLabel>
          {shown.filter((k) => k.group === g).map(item)}
        </Fragment>
      ))}
    </Menu>
  )
}
