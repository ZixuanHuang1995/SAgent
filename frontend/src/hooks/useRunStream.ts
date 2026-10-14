// 訂閱 AI 工作的即時進度（Server-Sent Events）。已結束的工作也能用，會重播全部事件。

import { useEffect, useReducer, useRef } from 'react'
import { api, type PlanOut, type ReviewOut, type RunEvent, type RunStatus } from '../api'

export interface StepState {
  id: string
  agent: string
  label: string
  status: 'running' | 'done' | 'failed'
  message: string
  startedAt: number
  durationMs?: number
  promptTokens?: number
  completionTokens?: number
  output?: any
  error?: string
  live: string
}

export interface RunLog {
  agent: string
  message: string
  level: string
}

export interface RunStreamState {
  connected: boolean
  status: RunStatus | 'unknown'
  phase: string
  phaseRound: number
  steps: StepState[]
  logs: RunLog[]
  sections: Record<string, { heading: string; body: string }>
  plan: PlanOut | null
  reviews: ReviewOut[]
  end: { status: RunStatus; message: string; versionNo: number | null; error?: string } | null
  firstEventAt: number | null
}

const initial: RunStreamState = {
  connected: false,
  status: 'unknown',
  phase: '',
  phaseRound: 0,
  steps: [],
  logs: [],
  sections: {},
  plan: null,
  reviews: [],
  end: null,
  firstEventAt: null,
}

const LIVE_LIMIT = 8000

type Action = { type: 'reset' } | { type: 'connected'; value: boolean } | { type: 'event'; event: RunEvent }

function reducer(state: RunStreamState, action: Action): RunStreamState {
  if (action.type === 'reset') return initial
  if (action.type === 'connected') return { ...state, connected: action.value }
  const ev = action.event
  const d = ev.data ?? {}
  const ts = ev.ts ? new Date(ev.ts).getTime() : Date.now()
  const next = { ...state, firstEventAt: state.firstEventAt ?? ts }
  switch (ev.type) {
    case 'run_status':
      return { ...next, status: d.status ?? next.status }
    case 'phase':
      return { ...next, phase: d.phase ?? '', phaseRound: d.round ?? 0 }
    case 'step_start':
      return {
        ...next,
        steps: [
          ...next.steps,
          { id: d.step, agent: ev.agent, label: ev.message, status: 'running', message: '', startedAt: ts, live: '' },
        ],
      }
    case 'token': {
      const steps = next.steps.slice()
      for (let i = steps.length - 1; i >= 0; i--) {
        if (steps[i].id === d.step && steps[i].status === 'running') {
          const live = (steps[i].live + (d.text ?? '')).slice(-LIVE_LIMIT)
          steps[i] = { ...steps[i], live }
          break
        }
      }
      return { ...next, steps }
    }
    case 'step_end': {
      const steps = next.steps.slice()
      for (let i = steps.length - 1; i >= 0; i--) {
        if (steps[i].id === d.step && steps[i].status === 'running') {
          steps[i] = {
            ...steps[i],
            status: d.ok === false ? 'failed' : 'done',
            message: ev.message,
            durationMs: d.duration_ms,
            promptTokens: d.prompt_tokens,
            completionTokens: d.completion_tokens,
            output: d.output,
            error: d.error,
          }
          break
        }
      }
      let { plan, reviews } = next
      if (d.step === 'plan' && d.output) plan = d.output as PlanOut
      if (typeof d.step === 'string' && d.step.startsWith('review:') && d.output) reviews = [...reviews, d.output as ReviewOut]
      return { ...next, steps, plan, reviews }
    }
    case 'section':
      return { ...next, sections: { ...next.sections, [d.section_id]: { heading: d.heading, body: d.body } } }
    case 'log':
      return { ...next, logs: [...next.logs, { agent: ev.agent, message: ev.message, level: d.level ?? 'info' }] }
    case 'run_end':
      return {
        ...next,
        status: d.status ?? next.status,
        end: { status: d.status, message: ev.message, versionNo: d.version_no ?? null, error: d.error },
        steps: next.steps.map((s) => (s.status === 'running' ? { ...s, status: 'failed', message: s.label } : s)),
      }
    default:
      return next
  }
}

export function useRunStream(runId: number | null, onEnd?: (status: RunStatus, versionNo: number | null) => void) {
  const [state, dispatch] = useReducer(reducer, initial)
  // 用 ref 存最新的 onEnd，換 callback 時不必重新連線
  const onEndRef = useRef(onEnd)
  onEndRef.current = onEnd
  useEffect(() => {
    dispatch({ type: 'reset' })
    if (runId === null) return
    const es = new EventSource(api.runEventsUrl(runId))
    es.onopen = () => dispatch({ type: 'connected', value: true })
    es.onmessage = (msg) => {
      let event: RunEvent
      try {
        event = JSON.parse(msg.data)
      } catch {
        return
      }
      dispatch({ type: 'event', event })
      if (event.type === 'run_end') {
        es.close() // 結束後關閉，不然瀏覽器會自動重連
        dispatch({ type: 'connected', value: false })
        onEndRef.current?.(event.data?.status, event.data?.version_no ?? null)
      }
    }
    es.onerror = () => dispatch({ type: 'connected', value: false })
    return () => es.close()
  }, [runId])
  return state
}

/** 把已完成的章節組成一份文件，給「即時預覽」用 */
export function assemblePreview(title: string, state: RunStreamState): string {
  const parts = [`# ${title}`]
  const order = state.plan?.sections.map((s) => s.id) ?? Object.keys(state.sections)
  order.forEach((id, i) => {
    const sec = state.sections[id]
    const heading = sec?.heading ?? state.plan?.sections.find((s) => s.id === id)?.heading ?? id
    parts.push(`## ${i + 1}. ${heading}\n\n${sec ? sec.body : '_（撰寫中…）_'}`)
  })
  return parts.join('\n\n')
}
