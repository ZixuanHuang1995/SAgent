// AI 代理面板：輸入需求、啟動 planner → writer → reviewer，顯示進度和歷史紀錄

import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { api, type RunMode, type SpecDetail } from '../api'
import { formatDuration, relativeTime, requirementHelp, RUN_STATUS, RUN_TIME_TEXT, runDuration } from '../format'
import { useRunStream } from '../hooks/useRunStream'
import { keys, useHealth, useRuns, useTemplates } from '../queries'
import { Icon } from './icons'
import { RunProgress } from './RunProgress'
import { RunDetailModal } from './RunDetail'
import { Badge, errorText, Field, Spinner, useToast } from './ui'

const DRAFT_KEY = (specId: number) => `sagent:requirement:${specId}`

function loadDraft(specId: number): string {
  try {
    return localStorage.getItem(DRAFT_KEY(specId)) ?? ''
  } catch {
    return ''
  }
}

function saveDraft(specId: number, text: string) {
  try {
    if (text) localStorage.setItem(DRAFT_KEY(specId), text)
    else localStorage.removeItem(DRAFT_KEY(specId))
  } catch {
    /* 瀏覽器不允許存就算了 */
  }
}

export function AgentPanel(props: {
  spec: SpecDetail
  onRunFinished: (versionNo: number | null) => void
  onOpenDiff: (from: number, to: number) => void
  onJumpToSection?: (heading: string) => void
}) {
  const { spec } = props
  const qc = useQueryClient()
  const toast = useToast()
  const runs = useRuns({ spec_id: spec.id })
  // 顯示中的工作：執行中的那一個；結束後繼續顯示結果，直到使用者按「回到表單」
  const [shownRunId, setShownRunId] = useState<number | null>(spec.active_run_id)
  const shownRun = runs.data?.find((r) => r.id === shownRunId) ?? null
  const [detailRunId, setDetailRunId] = useState<number | null>(null)
  // 先確認大綱的工作按「繼續撰寫」後加 1，讓進度重新連線
  const [resumeKey, setResumeKey] = useState(0)

  useEffect(() => {
    if (spec.active_run_id) setShownRunId(spec.active_run_id)
  }, [spec.active_run_id])

  const stream = useRunStream(
    shownRunId,
    (status, versionNo) => {
      qc.invalidateQueries({ queryKey: ['runs'] })
      if (status === 'planned') {
        toast('大綱完成，請回答待確認問題、確認大綱後繼續', 'info')
        return
      }
      qc.invalidateQueries({ queryKey: keys.spec(spec.id) })
      qc.invalidateQueries({ queryKey: keys.versions(spec.id) })
      qc.invalidateQueries({ queryKey: ['tree'] })
      if (status === 'succeeded') toast(versionNo ? `AI 已完成，存成 v${versionNo}` : 'AI 已完成，內容沒有變化', 'success')
      else if (status === 'failed') toast('AI 工作失敗，請看錯誤訊息', 'error')
      props.onRunFinished(status === 'succeeded' ? versionNo : null)
    },
    resumeKey,
  )

  // 狀態一變（排隊中 → 執行中 → 等待確認大綱、結束）就更新各處的狀態標籤，不用等定時更新
  useEffect(() => {
    if (stream.status === 'unknown') return
    qc.invalidateQueries({ queryKey: ['runs'] })
    qc.invalidateQueries({ queryKey: keys.spec(spec.id) })
    qc.invalidateQueries({ queryKey: ['tree', spec.project_id] })
    qc.invalidateQueries({ queryKey: keys.projects })
  }, [stream.status, qc, spec.id, spec.project_id])

  // 工作剛建立時清單還沒更新，先重新抓一次
  useEffect(() => {
    if (shownRunId && !shownRun) qc.invalidateQueries({ queryKey: keys.runs({ spec_id: spec.id }) })
  }, [shownRunId, shownRun, qc, spec.id])

  return (
    <div className="agent-panel">
      {shownRunId && shownRun ? (
        <>
          <RunProgress
            run={shownRun}
            state={stream}
            compact
            onDiff={(v) => props.onOpenDiff(v - 1, v)}
            onJumpToSection={props.onJumpToSection}
            onContinue={async (body) => {
              try {
                await api.continueRun(shownRunId, body)
                qc.invalidateQueries({ queryKey: ['runs'] })
                setResumeKey((k) => k + 1)
              } catch (e) {
                toast(errorText(e), 'error')
              }
            }}
            onCancel={async () => {
              try {
                await api.cancelRun(shownRunId)
                toast('已送出取消', 'info')
              } catch (e) {
                toast(errorText(e), 'error')
              }
            }}
          />
          {stream.end && (
            <button className="btn btn-block mb-sm" onClick={() => setShownRunId(null)}>
              回到 AI 撰寫表單
            </button>
          )}
        </>
      ) : shownRunId ? (
        <Spinner />
      ) : (
        <RunForm spec={spec} />
      )}

      <section className="card">
        <div className="card-title">這份 Spec 的 AI 工作紀錄</div>
        {runs.isLoading && <Spinner small />}
        {runs.data?.length === 0 && <div className="muted small">還沒有執行過。</div>}
        <ul className="run-list">
          {runs.data?.map((r) => {
            const ms = runDuration(r)
            return (
              <li key={r.id}>
                <button className="run-list-item" onClick={() => setDetailRunId(r.id)}>
                  <span>#{r.id}</span>
                  <Badge tone={r.status === 'succeeded' ? 'green' : r.status === 'failed' ? 'red' : r.status === 'cancelled' ? 'neutral' : 'blue'}>
                    {RUN_STATUS[r.status]}
                  </Badge>
                  <span className="muted small">
                    {r.mode === 'revise' ? '修訂' : '撰寫'}・{relativeTime(r.created_at)}
                    {ms !== null && r.finished_at ? `・${formatDuration(ms)}` : ''}
                    {r.result_version_no ? `・→ v${r.result_version_no}` : ''}
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
      </section>

      {detailRunId !== null && (
        <RunDetailModal
          runId={detailRunId}
          onClose={() => setDetailRunId(null)}
          onDiff={(v) => {
            setDetailRunId(null)
            props.onOpenDiff(v - 1, v)
          }}
          onJump={
            props.onJumpToSection &&
            ((heading) => {
              setDetailRunId(null)
              props.onJumpToSection!(heading)
            })
          }
        />
      )}
    </div>
  )
}

function RunForm({ spec }: { spec: SpecDetail }) {
  const qc = useQueryClient()
  const toast = useToast()
  const templates = useTemplates()
  const health = useHealth()
  const fileInput = useRef<HTMLInputElement>(null)

  const hasRealContent = spec.head_version_no > 0 && spec.head?.source !== 'template'
  const [mode, setMode] = useState<RunMode>(hasRealContent ? 'revise' : 'draft')
  const [requirement, setRequirement] = useState(() => loadDraft(spec.id))
  // 預設先確認大綱：規劃只花幾分鐘，先回答待確認問題，寫出來的內容比較準
  const [planFirst, setPlanFirst] = useState(true)
  const [busy, setBusy] = useState(false)
  const [showHelp, setShowHelp] = useState(false)

  useEffect(() => saveDraft(spec.id, requirement), [spec.id, requirement])

  const ollamaDown = health.data?.llm_provider === 'ollama' && health.data && !health.data.ollama.reachable
  // 文件類型建立後不能改，AI 一律照這份 Spec 的範本寫
  const template = templates.data?.find((t) => t.key === spec.doc_type)
  const help = requirementHelp(template, mode)

  const start = async () => {
    if (!requirement.trim()) {
      toast('請先輸入需求內容', 'warn')
      return
    }
    setBusy(true)
    try {
      // 模型、圖表、修訂輪數不在這裡選，後端照「設定」頁的值
      const options = { plan_first: mode === 'draft' && planFirst }
      await api.createRun({ spec_id: spec.id, mode, requirement, doc_type: spec.doc_type, options })
      toast('已開始 AI 工作', 'success')
      qc.invalidateQueries({ queryKey: keys.spec(spec.id) })
      qc.invalidateQueries({ queryKey: ['runs'] })
      qc.invalidateQueries({ queryKey: ['tree', spec.project_id] })
      qc.invalidateQueries({ queryKey: keys.projects })
    } catch (e) {
      toast(errorText(e), 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card run-form">
      <div className="card-title">
        AI 撰寫
        <span className="spacer" />
        <button className="icon-btn" aria-expanded={showHelp} aria-label="AI 撰寫怎麼運作" title="AI 撰寫怎麼運作" onClick={() => setShowHelp((v) => !v)}>
          <Icon name="help" />
        </button>
      </div>
      {showHelp && (
        <div className="info-box small">
          三個 agent 合作：<b className="c-planner">Planner</b> 規劃大綱 → <b className="c-writer">Writer</b> 逐節撰寫並畫圖 →{' '}
          <b className="c-reviewer">Reviewer</b> 審查，有問題再交回 Writer 修訂。結果會存成新版本，舊版本都保留。
          <p className="mt-sm">{RUN_TIME_TEXT}。可以離開這個頁面，完成後會自動存成新版本。</p>
        </div>
      )}

      <div className="seg">
        <button className={mode === 'draft' ? 'active' : ''} aria-pressed={mode === 'draft'} onClick={() => setMode('draft')}>
          撰寫整份文件
        </button>
        <button
          className={mode === 'revise' ? 'active' : ''}
          aria-pressed={mode === 'revise'}
          onClick={() => setMode('revise')}
          disabled={spec.head_version_no === 0}
        >
          依需求修訂
        </button>
      </div>
      <div className="muted small mb-sm">
        {mode === 'draft'
          ? `依需求照「${template?.name ?? spec.doc_type}」的章節從頭寫一份完整文件（目前內容會被新版本取代，但仍保留在版本歷程）。`
          : '在目前內容上修改：只改跟需求有關的章節，其他章節保持不變。'}
      </div>

      <Field
        label={mode === 'draft' ? '需求內容' : '修訂需求'}
        hint={
          <span className="row gap-sm">
            <span>{help.hint}</span>
            <button className="link-btn" type="button" onClick={() => fileInput.current?.click()}>
              從檔案載入
            </button>
          </span>
        }
      >
        <textarea
          className="input textarea"
          rows={7}
          value={requirement}
          onChange={(e) => setRequirement(e.target.value)}
          onKeyDown={(e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && !busy) {
              e.preventDefault()
              start()
            }
          }}
          placeholder={help.placeholder}
        />
      </Field>
      <input
        ref={fileInput}
        type="file"
        accept=".txt,.md,.markdown,.csv"
        hidden
        onChange={async (e) => {
          const file = e.target.files?.[0]
          if (file) setRequirement(await file.text())
          e.target.value = ''
        }}
      />

      {mode === 'draft' && (
        <label className="check-row mb-sm">
          <input
            type="checkbox"
            checked={planFirst}
            onChange={(e) => setPlanFirst(e.target.checked)}
          />
          <span>
            先確認大綱再撰寫
            <span className="muted small check-row-hint">Planner 規劃完（約 3～5 分鐘）先停下來，讓你回答待確認問題、調整名詞表和要點。</span>
          </span>
        </label>
      )}

      {/* 按鈕固定在側欄底部，表單很長時也不用捲動才找得到 */}
      <div className="run-form-actions">
        {ollamaDown && <div className="error-box">連不到 Ollama（{health.data?.ollama.url}），請先啟動 Ollama。</div>}
        <button className="btn btn-primary btn-block" onClick={start} disabled={busy || !requirement.trim()} title="Ctrl+Enter">
          {busy ? <Spinner small /> : '開始 AI 撰寫'}
        </button>
        {/* 一行就好，固定在底部的區塊越矮，上面的欄位越不會被蓋住 */}
        <div className="muted small run-form-note">{RUN_TIME_TEXT}，可以離開頁面</div>
      </div>
    </section>
  )
}
