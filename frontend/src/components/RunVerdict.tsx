// 文件層級的人工判斷（研究計劃 C-10）：這份 AI 版可以直接交付嗎？
// 累積下來算 AI 版的可交付率，也拿來校準評審模型（scripts/speceval/judge.py --calibrate --from-db）

import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api, type Run, type Verdict } from '../api'
import { keys } from '../queries'
import { errorText, useToast } from './ui'

const OPTIONS: { value: Verdict; text: string; hint: string }[] = [
  { value: 'yes', text: '可以', hint: '不用改或只改錯字，就可以交付' },
  { value: 'partial', text: '要小改', hint: '大致對，有幾處要改' },
  { value: 'no', text: '不行', hint: '要大改或重寫' },
]

export function RunVerdict(props: { run: Run; disabled?: boolean }) {
  const { run } = props
  const qc = useQueryClient()
  const toast = useToast()
  const [note, setNote] = useState(run.human_verdict_note ?? '')
  const [saving, setSaving] = useState(false)
  useEffect(() => setNote(run.human_verdict_note ?? ''), [run.id, run.human_verdict_note])

  const save = async (verdict: Verdict | null, nextNote: string) => {
    setSaving(true)
    try {
      await api.setVerdict(run.id, verdict, nextNote)
      await qc.invalidateQueries({ queryKey: keys.run(run.id) })
    } catch (e) {
      toast(errorText(e), 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="verdict-box">
      <div className="verdict-row">
        <span className="verdict-label">這份 AI 版可以直接交付嗎？</span>
        <div className="seg seg-sm" role="group" aria-label="這份 AI 版可以直接交付嗎">
          {OPTIONS.map((o) => {
            const active = run.human_verdict === o.value
            return (
              <button
                key={o.value}
                type="button"
                title={active ? `${o.hint}（再按一次取消）` : o.hint}
                className={active ? 'active' : ''}
                aria-pressed={active}
                disabled={props.disabled || saving}
                onClick={() => save(active ? null : o.value, note)}
              >
                {o.text}
              </button>
            )
          })}
        </div>
      </div>
      {run.human_verdict && (
        <input
          className="input"
          aria-label="判斷的理由"
          placeholder="一句理由（選填），例如：狀態流程漏了退回"
          maxLength={500}
          value={note}
          disabled={props.disabled || saving}
          onChange={(e) => setNote(e.target.value)}
          onBlur={() => {
            if (note.trim() !== (run.human_verdict_note ?? '')) save(run.human_verdict, note)
          }}
        />
      )}
      <div className="muted small">用來統計 AI 版有幾成可以直接交付，也用來校準評審模型。</div>
    </div>
  )
}
