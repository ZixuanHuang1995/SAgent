// 版本差異：章節變更摘要 + 逐行比較

import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { api, type DiffLine } from '../api'
import { Badge, Modal, Spinner } from './ui'

const SECTION_STATUS = {
  added: { text: '新增', tone: 'green' },
  removed: { text: '刪除', tone: 'red' },
  modified: { text: '修改', tone: 'amber' },
  unchanged: { text: '未變更', tone: 'neutral' },
} as const

type Row = { kind: 'line'; line: DiffLine } | { kind: 'fold'; lines: DiffLine[]; key: string }

function buildRows(lines: DiffLine[], context: number): Row[] {
  const rows: Row[] = []
  let i = 0
  while (i < lines.length) {
    if (lines[i].t !== 'eq') {
      rows.push({ kind: 'line', line: lines[i] })
      i++
      continue
    }
    let j = i
    while (j < lines.length && lines[j].t === 'eq') j++
    const run = lines.slice(i, j)
    const head = i === 0 ? 0 : context
    const tail = j === lines.length ? 0 : context
    if (run.length > head + tail + 2) {
      run.slice(0, head).forEach((line) => rows.push({ kind: 'line', line }))
      rows.push({ kind: 'fold', lines: run.slice(head, run.length - tail), key: `fold-${i}` })
      run.slice(run.length - tail).forEach((line) => rows.push({ kind: 'line', line }))
    } else {
      run.forEach((line) => rows.push({ kind: 'line', line }))
    }
    i = j
  }
  return rows
}

export function DiffView({ specId, from, to }: { specId: number; from?: number; to?: number }) {
  const q = useQuery({ queryKey: ['diff', specId, from, to], queryFn: () => api.diff(specId, from, to) })
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const rows = useMemo(() => (q.data ? buildRows(q.data.lines, 3) : []), [q.data])
  if (q.isLoading) return <Spinner />
  if (q.error) return <div className="error-box">{(q.error as Error).message}</div>
  const d = q.data!
  const changedSections = d.sections.filter((s) => s.status !== 'unchanged')
  return (
    <div className="diff">
      <div className="diff-summary">
        <span className="diff-stat-add">+{d.stats.added}</span>
        <span className="diff-stat-del">−{d.stats.removed}</span>
        <span className="muted">
          {d.from_version === 0 ? '空白' : `v${d.from_version}`} → v{d.to_version}
        </span>
      </div>
      {changedSections.length > 0 && (
        <div className="diff-sections">
          <span className="muted">章節變更：</span>
          {changedSections.map((s) => (
            <Badge key={s.heading + s.status} tone={SECTION_STATUS[s.status].tone}>
              {SECTION_STATUS[s.status].text}・{s.heading}
            </Badge>
          ))}
        </div>
      )}
      {d.stats.added + d.stats.removed === 0 ? (
        <div className="muted pad">兩個版本內容相同。</div>
      ) : (
        <table className="diff-table">
          <tbody>
            {rows.map((row, idx) =>
              row.kind === 'fold' && !expanded.has(row.key) ? (
                <tr key={row.key} className="diff-fold">
                  <td colSpan={3}>
                    <button className="link-btn" onClick={() => setExpanded((s) => new Set(s).add(row.key))}>
                      顯示 {row.lines.length} 行未變更的內容
                    </button>
                  </td>
                </tr>
              ) : row.kind === 'fold' ? (
                row.lines.map((line, k) => <DiffRow key={`${row.key}-${k}`} line={line} />)
              ) : (
                <DiffRow key={idx} line={row.line} />
              ),
            )}
          </tbody>
        </table>
      )}
    </div>
  )
}

function DiffRow({ line }: { line: DiffLine }) {
  const mark = line.t === 'add' ? '+' : line.t === 'del' ? '−' : ' '
  return (
    <tr className={`diff-${line.t}`}>
      <td className="diff-no">{line.a ?? ''}</td>
      <td className="diff-no">{line.b ?? ''}</td>
      <td className="diff-text">
        <span className="diff-mark">{mark}</span>
        {line.text || ' '}
      </td>
    </tr>
  )
}

export function DiffModal(props: { specId: number; from?: number; to?: number; onClose: () => void }) {
  return (
    <Modal title="版本比較" onClose={props.onClose} full>
      <DiffView specId={props.specId} from={props.from} to={props.to} />
    </Modal>
  )
}
