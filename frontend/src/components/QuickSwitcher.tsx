// 快速切換（Ctrl+K）：輸入幾個字，跳到同一個專案的某份 Spec 或頁面

import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import type { Phase, Spec } from '../api'
import { SPEC_STATUS } from '../format'
import { Icon, type IconName } from './icons'
import { Modal } from './ui'

interface Item {
  key: string
  icon: IconName
  label: string
  detail: string
  to: string
}

export function QuickSwitcher(props: { projectId: number; phases: Phase[]; specs: Spec[]; onClose: () => void }) {
  const { projectId, phases, specs, onClose } = props
  const navigate = useNavigate()
  const [q, setQ] = useState('')
  const [index, setIndex] = useState(0)
  const listRef = useRef<HTMLUListElement>(null)

  const items = useMemo<Item[]>(() => {
    const phaseName = new Map(phases.map((p) => [p.id, p.name]))
    const all: Item[] = [
      ...specs.map((s) => ({
        key: `s${s.id}`,
        icon: 'file' as const,
        label: s.title,
        detail: `${phaseName.get(s.phase_id) ?? ''}・${SPEC_STATUS[s.status]}・v${s.head_version_no}`,
        to: `/p/${projectId}/s/${s.id}`,
      })),
      { key: 'home', icon: 'overview', label: '專案總覽', detail: '頁面', to: `/p/${projectId}` },
      { key: 'baselines', icon: 'tag', label: '基準版本', detail: '頁面', to: `/p/${projectId}/baselines` },
      { key: 'runs', icon: 'bot', label: 'AI 工作紀錄', detail: '頁面', to: `/p/${projectId}/runs` },
    ]
    const words = q.trim().toLowerCase().split(/\s+/).filter(Boolean)
    return words.length === 0 ? all : all.filter((it) => words.every((w) => `${it.label} ${it.detail}`.toLowerCase().includes(w)))
  }, [q, specs, phases, projectId])

  useEffect(() => setIndex(0), [q])
  useEffect(() => {
    listRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' })
  }, [index])

  const go = (it: Item | undefined) => {
    if (!it) return
    onClose()
    navigate(it.to)
  }

  return (
    <Modal title="快速切換" onClose={onClose}>
      <div className="switcher">
        <div className="switcher-input">
          <Icon name="search" />
          <input
            className="input"
            autoFocus
            value={q}
            placeholder="輸入 Spec 標題、階段或狀態…"
            role="combobox"
            aria-expanded="true"
            aria-controls="switcher-list"
            aria-activedescendant={items[index] ? `switcher-${items[index].key}` : undefined}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'ArrowDown') setIndex((i) => Math.min(i + 1, items.length - 1))
              else if (e.key === 'ArrowUp') setIndex((i) => Math.max(i - 1, 0))
              else if (e.key === 'Enter') go(items[index])
              else return
              e.preventDefault()
            }}
          />
        </div>
        <ul className="switcher-list" id="switcher-list" role="listbox" ref={listRef} aria-label="搜尋結果">
          {items.length === 0 && <li className="muted small pad">找不到符合的項目</li>}
          {items.map((it, i) => (
            <li
              key={it.key}
              id={`switcher-${it.key}`}
              role="option"
              aria-selected={i === index}
              className={i === index ? 'is-active' : ''}
              onMouseMove={() => setIndex(i)}
              onClick={() => go(it)}
            >
              <Icon name={it.icon} />
              <span className="switcher-label">{it.label}</span>
              <span className="muted small">{it.detail}</span>
            </li>
          ))}
        </ul>
        <div className="muted small">↑↓ 選擇・Enter 開啟・Esc 關閉</div>
      </div>
    </Modal>
  )
}
