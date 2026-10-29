// AI 代理面板：輸入需求、啟動 planner → writer → reviewer，顯示進度和歷史紀錄

import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { api, type RunMode, type RunOptions, type SpecDetail, type UmlSyntax } from '../api'
import { formatDuration, relativeTime, RUN_STATUS, runDuration, UML_SYNTAX } from '../format'
import { useRunStream } from '../hooks/useRunStream'
import { keys, useHealth, useModels, useRuns, useSettings, useTemplateCategories, useTemplates } from '../queries'
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
  const categories = useTemplateCategories()
  const models = useModels()
  const settings = useSettings()
  const health = useHealth()
  const fileInput = useRef<HTMLInputElement>(null)

  const hasRealContent = spec.head_version_no > 0 && spec.head?.source !== 'template'
  const [mode, setMode] = useState<RunMode>(hasRealContent ? 'revise' : 'draft')
  const [requirement, setRequirement] = useState(() => loadDraft(spec.id))
  const [docType, setDocType] = useState(spec.doc_type)
  const [advanced, setAdvanced] = useState(false)
  const [options, setOptions] = useState<RunOptions>({})
  const [busy, setBusy] = useState(false)
  const [showHelp, setShowHelp] = useState(false)

  useEffect(() => saveDraft(spec.id, requirement), [spec.id, requirement])

  const s = settings.data
  const model = options.model ?? s?.default_model ?? ''
  const ollamaDown = health.data?.llm_provider === 'ollama' && health.data && !health.data.ollama.reachable
  const template = templates.data?.find((t) => t.key === docType)
  // 有些文件類型不畫圖（例如 UI/UX 設計建議），圖表相關選項就不顯示
  const canDraw = template?.allow_diagrams !== false

  const start = async () => {
    if (!requirement.trim()) {
      toast('請先輸入需求內容', 'warn')
      return
    }
    setBusy(true)
    try {
      const runOptions = { ...options, model, plan_first: mode === 'draft' && !!options.plan_first }
      await api.createRun({ spec_id: spec.id, mode, requirement, doc_type: docType, options: runOptions })
      toast('已開始 AI 工作', 'success')
      qc.invalidateQueries({ queryKey: keys.spec(spec.id) })
      qc.invalidateQueries({ queryKey: ['runs'] })
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
          <p className="mt-sm">本機 CPU 推論一份完整文件約需 15～60 分鐘。可以離開這個頁面，完成後會自動存成新版本。</p>
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
          ? '依需求從頭寫一份完整文件（目前內容會被新版本取代，但仍保留在版本歷程）。'
          : '在目前內容上修改：只改跟需求有關的章節，其他章節保持不變。'}
      </div>

      <Field
        label={mode === 'draft' ? '需求內容' : '修訂需求'}
        hint={
          <span className="row gap-sm">
            <span>可以貼會議紀錄、客戶需求、條列重點。寫得越具體，結果越好。</span>
            <button className="link-btn" type="button" onClick={() => fileInput.current?.click()}>
              從檔案載入
            </button>
          </span>
        }
      >
        <textarea
          className="input textarea"
          rows={9}
          value={requirement}
          onChange={(e) => setRequirement(e.target.value)}
          onKeyDown={(e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && !busy) {
              e.preventDefault()
              start()
            }
          }}
          placeholder={
            mode === 'draft'
              ? '例如：\n公司要做員工請假系統，取代紙本流程。\n1. 員工在網頁申請請假，選假別、日期、代理人\n2. 主管審核，超過 3 天要經理再審\n3. 自動計算剩餘特休…'
              : '例如：\n請假要支援「半天」為單位，並且新增「補休」假別。'
          }
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

      <Field label="文件類型" hint={template?.description}>
        <select className="input" value={docType} onChange={(e) => setDocType(e.target.value)}>
          {categories.data?.map((c) => {
            const list = templates.data?.filter((t) => t.category === c.key) ?? []
            return (
              list.length > 0 && (
                <optgroup key={c.key} label={c.name}>
                  {list.map((t) => (
                    <option key={t.key} value={t.key}>
                      {t.name}
                    </option>
                  ))}
                </optgroup>
              )
            )
          })}
        </select>
      </Field>

      {mode === 'draft' && (
        <label className="check-row mb-sm">
          <input
            type="checkbox"
            checked={!!options.plan_first}
            onChange={(e) => setOptions((o) => ({ ...o, plan_first: e.target.checked }))}
          />
          <span>
            先確認大綱再撰寫
            <span className="muted small check-row-hint">Planner 規劃完（約 3～5 分鐘）先停下來，讓你回答待確認問題、調整名詞表和要點。</span>
          </span>
        </label>
      )}

      <button className="link-btn mb-sm" aria-expanded={advanced} onClick={() => setAdvanced((a) => !a)}>
        {advanced ? '▾' : '▸'} 進階選項（{canDraw ? '模型、圖表、審查次數' : '模型、審查次數'}）
      </button>
      {advanced && s && (
        <div className="advanced">
          <Field label="模型" hint={models.error ? `讀不到模型清單：${errorText(models.error)}` : '本機 Ollama 的模型。MoE 模型（例如 a3b）在 CPU 上通常比較快。'}>
            <select className="input" value={model} onChange={(e) => setOptions((o) => ({ ...o, model: e.target.value }))}>
              {!models.data?.models.some((m) => m.name === model) && <option value={model}>{model}</option>}
              {models.data?.models.map((m) => (
                <option key={m.name} value={m.name}>
                  {m.name} {m.parameter_size ? `（${m.parameter_size}）` : ''}
                </option>
              ))}
            </select>
          </Field>
          {canDraw && (
            <Field label="UML 圖語法">
              <select
                className="input"
                value={options.uml_syntax ?? s.uml_syntax}
                onChange={(e) => setOptions((o) => ({ ...o, uml_syntax: e.target.value as UmlSyntax }))}
              >
                {Object.entries(UML_SYNTAX).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </Field>
          )}
          <div className="form-row">
            {canDraw && (
              <>
                <Field label="畫圖">
                  <select
                    className="input"
                    value={String(options.diagrams ?? s.diagrams)}
                    onChange={(e) => setOptions((o) => ({ ...o, diagrams: e.target.value === 'true' }))}
                  >
                    <option value="true">要</option>
                    <option value="false">不要（比較快）</option>
                  </select>
                </Field>
                <Field label="最多幾張圖">
                  <input
                    className="input"
                    type="number"
                    min={0}
                    max={10}
                    value={options.max_diagrams ?? 4}
                    onChange={(e) => setOptions((o) => ({ ...o, max_diagrams: Number(e.target.value) }))}
                  />
                </Field>
              </>
            )}
            <Field label="審查後修訂幾輪">
              <input
                className="input"
                type="number"
                min={0}
                max={3}
                value={options.max_revisions ?? s.max_revisions}
                onChange={(e) => setOptions((o) => ({ ...o, max_revisions: Number(e.target.value) }))}
              />
            </Field>
          </div>
        </div>
      )}

      {/* 按鈕固定在側欄底部，表單很長時也不用捲動才找得到 */}
      <div className="run-form-actions">
        {ollamaDown && <div className="error-box">連不到 Ollama（{health.data?.ollama.url}），請先啟動 Ollama。</div>}
        <button className="btn btn-primary btn-block" onClick={start} disabled={busy || !requirement.trim()} title="Ctrl+Enter">
          {busy ? <Spinner small /> : '開始 AI 撰寫'}
        </button>
        <div className="muted small run-form-note">約需 15～60 分鐘，可以離開頁面・在需求欄按 Ctrl+Enter 也能開始</div>
      </div>
    </section>
  )
}
