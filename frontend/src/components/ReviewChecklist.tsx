// 審查結果清單：最近一次 AI 工作的審查列出的錯誤和人工覆核項目，逐項標成已處理

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type RunDetail, type SpecDetail } from '../api'
import { relativeTime } from '../format'
import { usePersistent } from '../hooks/layout'
import { keys } from '../queries'
import { RunDetailModal } from './RunDetail'
import { SEVERITY } from './RunProgress'
import { Badge, errorText, Skeleton, useToast } from './ui'

const ORDER = { high: 0, medium: 1, low: 2 }

interface Item {
  key: string
  sectionId: string | null
  severity?: keyof typeof SEVERITY
  text: string
  note: string
  source: 'check' | 'llm'
}

export function ReviewChecklist(props: {
  spec: SpecDetail
  onJump: (heading: string) => void
  onOpenDiff: (from: number, to: number) => void
}) {
  const { spec } = props
  const summary = spec.review!
  const runId = summary.run_id
  const qc = useQueryClient()
  const toast = useToast()
  const run = useQuery({ queryKey: keys.run(runId), queryFn: () => api.run(runId) })
  const [hideDone, setHideDone] = usePersistent<'0' | '1'>('sagent:review-hide-done', '0')
  const [showRun, setShowRun] = useState(false)

  if (run.isLoading) return <Skeleton lines={6} />
  if (run.error || !run.data) return <div className="error-box">{errorText(run.error)}</div>
  const review = run.data.review?.final
  if (!review) return null

  const checked = new Set(run.data.review_checked)
  const issues: Item[] = review.issues
    .map((i, n) => ({
      key: `issue:${n}`,
      sectionId: i.section_id,
      severity: i.severity,
      text: i.problem,
      note: i.suggestion ? `建議：${i.suggestion}` : '',
      source: i.source,
    }))
    .sort((a, b) => ORDER[a.severity] - ORDER[b.severity])
  const points: Item[] = (review.review_points ?? []).map((p, n) => ({
    key: `point:${n}`,
    sectionId: p.section_id,
    text: p.item,
    note: p.reason,
    source: p.source,
  }))
  const total = issues.length + points.length
  const done = [...issues, ...points].filter((i) => checked.has(i.key)).length
  const headingOf = (id: string | null) => (id ? run.data.plan?.sections.find((s) => s.id === id)?.heading ?? id : null)
  const stale = summary.version_no !== null && spec.head_version_no > summary.version_no

  const toggle = async (key: string, value: boolean) => {
    // 先改畫面，失敗再改回來
    qc.setQueryData<RunDetail>(keys.run(runId), (old) =>
      old
        ? { ...old, review_checked: value ? [...old.review_checked, key] : old.review_checked.filter((k) => k !== key) }
        : old,
    )
    try {
      await api.setReviewCheck(runId, key, value)
    } catch (e) {
      toast(errorText(e), 'error')
    } finally {
      qc.invalidateQueries({ queryKey: keys.run(runId) })
      qc.invalidateQueries({ queryKey: keys.spec(spec.id) })
      qc.invalidateQueries({ queryKey: ['tree', spec.project_id] })
    }
  }

  const renderList = (items: Item[], empty: string) => {
    const shown = hideDone === '1' ? items.filter((i) => !checked.has(i.key)) : items
    if (items.length === 0) return <div className="muted small">{empty}</div>
    if (shown.length === 0) return <div className="muted small">都處理完了。</div>
    return (
      <ul className="check-list">
        {shown.map((i) => {
          const isDone = checked.has(i.key)
          const heading = headingOf(i.sectionId)
          return (
            <li key={i.key} className={isDone ? 'is-done' : ''}>
              <input
                type="checkbox"
                checked={isDone}
                disabled={spec.archived}
                onChange={(e) => toggle(i.key, e.target.checked)}
                aria-label={`${isDone ? '取消已處理' : '標成已處理'}：${i.text}`}
              />
              <div className="check-item">
                <div className="check-item-meta">
                  {i.severity && <Badge tone={SEVERITY[i.severity].tone}>{SEVERITY[i.severity].text}</Badge>}
                  {heading ? (
                    <button className="link-btn small" title="跳到文件裡的這一節" onClick={() => props.onJump(heading)}>
                      {heading}
                    </button>
                  ) : (
                    <span className="muted small">整份文件</span>
                  )}
                  {i.source === 'check' && <span className="muted small">・{i.severity ? '程式檢查' : '文件標註'}</span>}
                </div>
                <div className="check-item-text">{i.text}</div>
                {i.note && <div className="muted small">{i.note}</div>}
              </div>
            </li>
          )
        })}
      </ul>
    )
  }

  return (
    <div className="review-checklist">
      <div className="review-progress">
        <div className="review-progress-text">
          <strong>
            已處理 {done} / {total}
          </strong>
          <label className="check-row small muted">
            <input type="checkbox" checked={hideDone === '1'} onChange={(e) => setHideDone(e.target.checked ? '1' : '0')} />
            隱藏已處理
          </label>
        </div>
        <div className="review-progress-bar" role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={done} aria-label="審查項目處理進度">
          <span style={{ width: `${total ? (done / total) * 100 : 0}%` }} />
        </div>
      </div>
      <div className="muted small">
        AI 工作 #{runId}・{relativeTime(run.data.finished_at)}
        {summary.version_no !== null && `・審查 v${summary.version_no}`}・
        <button className="link-btn" onClick={() => setShowRun(true)}>
          看完整過程
        </button>
      </div>
      {stale && (
        <div className="info-box small">
          審查的是 v{summary.version_no}，之後又存了 {spec.head_version_no - summary.version_no!} 個版本，有些問題可能已經改好了。
        </div>
      )}
      {done === total && <div className="success-box small">審查列出的項目都處理完了。</div>}
      {review.summary && <p className="small">{review.summary}</p>}

      <div className="subhead">
        格式、邏輯錯誤 <span className="muted small">{issues.filter((i) => !checked.has(i.key)).length} 項未處理</span>
      </div>
      {renderList(issues, '沒有發現格式或邏輯錯誤。')}
      <div className="subhead">
        需要人工覆核 <span className="muted small">{points.filter((i) => !checked.has(i.key)).length} 項未處理</span>
      </div>
      {renderList(points, '沒有需要特別確認的地方。')}

      {showRun && (
        <RunDetailModal
          runId={runId}
          onClose={() => setShowRun(false)}
          onDiff={(v) => {
            setShowRun(false)
            props.onOpenDiff(v - 1, v)
          }}
          onJump={(h) => {
            setShowRun(false)
            props.onJump(h)
          }}
        />
      )}
    </div>
  )
}
