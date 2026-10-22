// AI 工作的進度畫面：階段、每個步驟、即時輸出、大綱、審查結果

import { useEffect, useRef, useState } from 'react'
import type { PlanOut, ReviewIssue, ReviewOut, ReviewPoint, Run } from '../api'
import { AGENT_NAME, formatDuration, RUN_STATUS } from '../format'
import { assemblePreview, type RunStreamState, type StepState } from '../hooks/useRunStream'
import { MarkdownView } from './MarkdownView'
import { Badge, Modal, Spinner } from './ui'
import { Icon } from './icons'

const PHASES = [
  { key: 'plan', label: '規劃', agent: 'planner' },
  { key: 'write', label: '撰寫', agent: 'writer' },
  { key: 'review', label: '審查', agent: 'reviewer' },
  { key: 'revise', label: '修訂', agent: 'writer' },
  { key: 'done', label: '完成', agent: 'system' },
]

export function AgentBadge({ agent }: { agent: string }) {
  return <span className={`agent-badge agent-${agent}`}>{AGENT_NAME[agent] ?? agent}</span>
}

function PhaseStepper({ state }: { state: RunStreamState }) {
  const current = state.end ? 'done' : state.phase
  const idx = PHASES.findIndex((p) => p.key === current)
  return (
    <ol className="stepper">
      {PHASES.map((p, i) => {
        const status = state.end && state.end.status !== 'succeeded' && i === PHASES.length - 1
          ? 'failed'
          : i < idx || (current === 'done' && i === idx)
            ? 'done'
            : i === idx
              ? 'active'
              : 'todo'
        return (
          <li key={p.key} className={`stepper-item stepper-${status} agent-${p.agent}`}>
            <span className="stepper-dot" />
            <span>{p.key === 'done' && state.end && state.end.status !== 'succeeded' ? RUN_STATUS[state.end.status] : p.label}</span>
          </li>
        )
      })}
    </ol>
  )
}

function useElapsed(since: number | null, running: boolean) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!running) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [running])
  return since ? now - since : 0
}

function LiveOutput({ step }: { step: StepState }) {
  const ref = useRef<HTMLPreElement>(null)
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight
  }, [step.live])
  const tokensPerSec = useTokenRate(step)
  return (
    <div className="live-card">
      <div className="live-head">
        <AgentBadge agent={step.agent} />
        <span className="live-label">{step.label}</span>
        <Spinner small />
      </div>
      <pre ref={ref} className="live-output">
        {step.live || '模型讀取提示詞中…（CPU 推論時這一步可能要等一陣子）'}
      </pre>
      {tokensPerSec && <div className="muted small">約 {tokensPerSec} 字/秒</div>}
    </div>
  )
}

function useTokenRate(step: StepState) {
  const elapsed = useElapsed(step.startedAt, step.status === 'running')
  if (!step.live || elapsed < 3000) return null
  return (step.live.length / (elapsed / 1000)).toFixed(1)
}

function StepRow({ step }: { step: StepState }) {
  const [open, setOpen] = useState(false)
  const hasDetail = step.status !== 'running' && (step.output || step.error)
  return (
    <li className={`step step-${step.status}`}>
      <div
        className="step-line"
        onClick={() => hasDetail && setOpen((o) => !o)}
        role={hasDetail ? 'button' : undefined}
        tabIndex={hasDetail ? 0 : undefined}
        aria-expanded={hasDetail ? open : undefined}
        onKeyDown={(e) => {
          if (hasDetail && (e.key === 'Enter' || e.key === ' ')) {
            e.preventDefault()
            setOpen((o) => !o)
          }
        }}
      >
        <span className="step-icon">{step.status === 'running' ? <Spinner small /> : step.status === 'done' ? '✓' : '✕'}</span>
        <AgentBadge agent={step.agent} />
        <span className="step-label">{step.status === 'running' ? step.label : step.message || step.label}</span>
        <span className="step-meta muted small">
          {step.durationMs !== undefined && formatDuration(step.durationMs)}
          {step.completionTokens ? `・${step.completionTokens} tokens` : ''}
        </span>
        {hasDetail && <span className="step-toggle">{open ? '▾' : '▸'}</span>}
      </div>
      {open && hasDetail && <StepDetail step={step} />}
    </li>
  )
}

function StepDetail({ step }: { step: StepState }) {
  if (step.error) return <div className="error-box">{step.error}</div>
  const o = step.output ?? {}
  if (step.id === 'plan') return <PlanView plan={o as PlanOut} />
  if (step.id.startsWith('review:')) return <ReviewView review={o as ReviewOut} />
  if (o.code)
    return (
      <div className="step-detail">
        {o.errors?.length > 0 && <div className="warn-box">仍有語法問題：{o.errors.join('；')}</div>}
        <MarkdownView markdown={'```' + (o.lang ?? 'mermaid') + '\n' + o.code + '\n```'} />
      </div>
    )
  return null
}

const ACTION_TEXT = { write: '新寫', keep: '保留', update: '修改' } as const

export function PlanView({ plan }: { plan: PlanOut }) {
  return (
    <div className="plan-view">
      {plan.fallback && <div className="warn-box">模型沒有給出可用的大綱，改用範本章節。</div>}
      {plan.summary && <p>{plan.summary}</p>}
      <ol className="plan-sections">
        {plan.sections.map((s) => (
          <li key={s.id}>
            <div>
              <strong>{s.heading}</strong> {s.action !== 'write' && <Badge tone={s.action === 'keep' ? 'neutral' : 'amber'}>{ACTION_TEXT[s.action]}</Badge>}
            </div>
            {(s.instruction || s.goal) && <div className="muted small">{s.action === 'update' ? s.instruction : s.goal}</div>}
            {s.key_points.length > 0 && (
              <ul className="plan-points">
                {s.key_points.map((p, i) => (
                  <li key={i}>{p}</li>
                ))}
              </ul>
            )}
            {s.diagrams.map((d, i) => (
              <div key={i} className="small">
                <Icon name="chart" size={13} /> {d.title}（{d.syntax === 'plantuml' ? 'PlantUML' : 'Mermaid'}）
              </div>
            ))}
          </li>
        ))}
      </ol>
      {plan.open_questions.length > 0 && (
        <>
          <div className="subhead">待確認事項</div>
          <ul>
            {plan.open_questions.map((q, i) => (
              <li key={i}>{q}</li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}

const SEVERITY = {
  high: { text: '重要', tone: 'red' },
  medium: { text: '一般', tone: 'amber' },
  low: { text: '建議', tone: 'neutral' },
} as const

export function ReviewView(props: {
  review: ReviewOut
  plan?: PlanOut | null
  /** AI 工作已經結束：沒通過的問題不會再自動修訂，要改成「待人工確認」 */
  final?: boolean
  onJump?: (heading: string) => void
}) {
  const { review, plan, onJump } = props
  const headingOf = (id: string | null) => (id ? plan?.sections.find((s) => s.id === id)?.heading ?? id : null)
  const order = { high: 0, medium: 1, low: 2 }
  const issues = [...review.issues].sort((a, b) => order[a.severity] - order[b.severity])
  const verdict = review.verdict === 'pass' ? '沒有錯誤' : props.final ? '錯誤待人工確認' : '需要修訂'
  // 舊版的審查結果沒有 review_points
  const points = review.review_points ?? []
  const sectionLink = (id: string | null) => {
    const heading = headingOf(id)
    return heading && onJump ? (
      <button className="link-btn small" title="跳到文件裡的這一節" onClick={() => onJump(heading)}>
        {heading}
      </button>
    ) : (
      <span className="muted small">{heading ?? '整份文件'}</span>
    )
  }
  return (
    <div className="review-view">
      <div className="review-head">
        {review.score != null && <span className="review-score">{review.score}<small>/10</small></span>}
        <Badge tone={review.verdict === 'pass' ? 'green' : 'amber'}>{verdict}</Badge>
        {points.length > 0 && <Badge tone="blue">人工覆核 {points.length} 項</Badge>}
        {!review.llm_ok && <Badge tone="neutral">只有程式檢查</Badge>}
      </div>
      {review.summary && <p>{review.summary}</p>}
      <div className="subhead">格式、邏輯錯誤</div>
      {issues.length === 0 ? (
        <div className="muted small">沒有發現格式或邏輯錯誤。</div>
      ) : (
        <ul className="issue-list">
          {issues.map((i: ReviewIssue, k) => (
            <li key={k}>
              <Badge tone={SEVERITY[i.severity].tone}>{SEVERITY[i.severity].text}</Badge> {sectionLink(i.section_id)}
              {i.source === 'check' && <span className="muted small">・程式檢查</span>}
              <div>{i.problem}</div>
              {i.suggestion && <div className="muted small">建議：{i.suggestion}</div>}
            </li>
          ))}
        </ul>
      )}
      {(points.length > 0 || review.review_points) && (
        <>
          <div className="subhead">需要人工覆核</div>
          {points.length === 0 ? (
            <div className="muted small">沒有需要特別確認的地方。</div>
          ) : (
            <ul className="issue-list">
              {points.map((p: ReviewPoint, k) => (
                <li key={k}>
                  {sectionLink(p.section_id)}
                  {p.source === 'check' && <span className="muted small">・文件標註</span>}
                  <div>{p.item}</div>
                  {p.reason && <div className="muted small">{p.reason}</div>}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}

/** AI 已完成，但最後一次審查還有問題：說明為什麼沒有再修，提醒人工確認 */
function UnresolvedNote(props: { run: Run; state: RunStreamState; review: ReviewOut; canJump: boolean }) {
  const { run, state, review } = props
  const count = review.issues.filter((i) => i.severity !== 'low').length
  const maxRounds = run.options.max_revisions
  const noFix = state.logs.some((l) => l.message.includes('沒有可以自動修訂'))
  const reason = noFix
    ? 'Writer 沒辦法自動修改這些問題'
    : maxRounds === 0
      ? '這次設定不自動修訂'
      : maxRounds
        ? `已經自動修訂 ${maxRounds} 輪，到了上限`
        : '已經到了自動修訂的上限'
  return (
    <div className="warn-box">
      {reason}，審查還有 {count} 個問題沒解決，請人工確認。
      {props.canJump && '點下方審查結果裡的章節名稱，可以跳到文件對應的位置。'}
    </div>
  )
}

export function RunProgress(props: {
  run: Run
  state: RunStreamState
  onCancel?: () => void
  onOpenVersion?: (versionNo: number) => void
  onDiff?: (versionNo: number) => void
  onJumpToSection?: (heading: string) => void
  compact?: boolean
}) {
  const { run, state } = props
  const [preview, setPreview] = useState(false)
  const running = !state.end && (state.status === 'running' || state.status === 'unknown' || run.status === 'queued' || run.status === 'running')
  const elapsed = useElapsed(state.firstEventAt, running)
  const current = [...state.steps].reverse().find((s) => s.status === 'running')
  const lastReview = state.reviews[state.reviews.length - 1]
  const warnings = state.logs.filter((l) => l.level === 'warn')
  const stats = run.stats

  return (
    <div className={`run-progress ${props.compact ? 'is-compact' : ''}`}>
      <div className="run-head">
        <Badge tone={state.end ? (state.end.status === 'succeeded' ? 'green' : 'red') : 'blue'}>
          {state.end ? RUN_STATUS[state.end.status] : run.status === 'queued' && state.status === 'unknown' ? RUN_STATUS.queued : RUN_STATUS.running}
        </Badge>
        <span className="muted small">
          {run.mode === 'revise' ? '修訂' : '撰寫'}・{run.model}
          {elapsed > 0 && `・${formatDuration(elapsed)}`}
        </span>
        <span className="spacer" />
        {Object.keys(state.sections).length > 0 && (
          <button className="btn btn-sm" onClick={() => setPreview(true)}>
            即時預覽文件
          </button>
        )}
        {running && props.onCancel && (
          <button className="btn btn-sm btn-danger-outline" onClick={props.onCancel}>
            取消
          </button>
        )}
      </div>

      {run.status === 'queued' && state.steps.length === 0 && !state.end && (
        <div className="info-box">
          排隊中{run.queue_position ? `（前面還有 ${run.queue_position - 1} 個工作）` : ''}。一次只執行一個 AI 工作，CPU 推論同時跑多個只會更慢。
        </div>
      )}

      <PhaseStepper state={state} />

      {state.end && (
        <div className={state.end.status === 'succeeded' ? 'success-box' : 'error-box'}>
          <div>{state.end.message}</div>
          {state.end.status === 'succeeded' && state.end.versionNo && (
            <div className="row gap-sm mt-sm">
              {props.onOpenVersion && (
                <button className="btn btn-sm btn-primary" onClick={() => props.onOpenVersion!(state.end!.versionNo!)}>
                  查看文件
                </button>
              )}
              {props.onDiff && state.end.versionNo > 1 && (
                <button className="btn btn-sm" onClick={() => props.onDiff!(state.end!.versionNo!)}>
                  與前一版比較
                </button>
              )}
            </div>
          )}
        </div>
      )}
      {state.end?.status === 'succeeded' && lastReview && lastReview.verdict !== 'pass' && (
        <UnresolvedNote run={run} state={state} review={lastReview} canJump={!!props.onJumpToSection} />
      )}

      {current && <LiveOutput step={current} />}

      {warnings.length > 0 && (
        <details className="warn-list">
          <summary>提醒 {warnings.length} 則</summary>
          <ul>
            {warnings.map((w, i) => (
              <li key={i}>
                <AgentBadge agent={w.agent} /> {w.message}
              </li>
            ))}
          </ul>
        </details>
      )}

      {lastReview && (
        <section className="card">
          <div className="card-title">審查結果{state.reviews.length > 1 ? `（第 ${state.reviews.length} 次）` : ''}</div>
          <ReviewView review={lastReview} plan={state.plan} final={!!state.end} onJump={props.onJumpToSection} />
        </section>
      )}

      <section className="card">
        <div className="card-title">執行步驟</div>
        {state.steps.length === 0 ? (
          <div className="muted small">{running ? '等待開始…' : '沒有步驟紀錄'}</div>
        ) : (
          <ol className="steps">
            {state.steps.map((s, i) => (
              <StepRow key={`${s.id}-${i}`} step={s} />
            ))}
          </ol>
        )}
      </section>

      {state.plan && (
        <details className="card" open={!props.compact}>
          <summary className="card-title">文件大綱（Planner）</summary>
          <PlanView plan={state.plan} />
        </details>
      )}

      {stats && state.end && (
        <section className="card">
          <div className="card-title">用量</div>
          <table className="mini-table">
            <thead>
              <tr>
                <th>Agent</th>
                <th>呼叫</th>
                <th>輸入</th>
                <th>輸出</th>
                <th>時間</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(stats).map(([agent, s]) => (
                <tr key={agent}>
                  <td>
                    <AgentBadge agent={agent} />
                  </td>
                  <td>{s.calls}</td>
                  <td>{s.prompt_tokens}</td>
                  <td>{s.completion_tokens}</td>
                  <td>{formatDuration(s.seconds * 1000)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {preview && (
        <Modal title={`即時預覽：${run.spec_title}`} onClose={() => setPreview(false)} full>
          <MarkdownView markdown={assemblePreview(run.spec_title, state)} />
        </Modal>
      )}
    </div>
  )
}
