// 先確認大綱：Planner 規劃完先停下來，使用者回答待確認問題、調整名詞表和各節要點，再繼續撰寫

import { useState } from 'react'
import type { PlanOut, RunContinue } from '../api'
import { TERMS_FORMAT, termsToText, textToTerms } from '../format'
import { Field, Spinner } from './ui'

export function PlanConfirm(props: { plan: PlanOut; onContinue: (body: RunContinue) => Promise<void>; onCancel: () => void }) {
  const { plan } = props
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [terms, setTerms] = useState(() => termsToText(plan.terms ?? []))
  const [points, setPoints] = useState<Record<string, string>>(() =>
    Object.fromEntries(plan.sections.map((s) => [s.id, s.key_points.join('\n')])),
  )
  const [busy, setBusy] = useState(false)
  const answeredCount = Object.values(answers).filter((a) => a.trim()).length

  const submit = async () => {
    setBusy(true)
    try {
      await props.onContinue({
        answers: plan.open_questions.map((q) => ({ question: q, answer: answers[q] ?? '' })).filter((a) => a.answer.trim()),
        terms: textToTerms(terms),
        sections: plan.sections.map((s) => ({ id: s.id, key_points: (points[s.id] ?? '').split('\n') })),
      })
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card plan-confirm">
      <div className="card-title">確認大綱</div>
      <div className="info-box small mb-sm">
        Planner 規劃完了。回答待確認問題、調整名詞表和各節要點後，按「繼續撰寫」。沒回答的問題會留在文件最後的「待確認事項」。
      </div>

      {plan.open_questions.length > 0 && (
        <>
          <div className="subhead">待確認問題（{plan.open_questions.length}）</div>
          {plan.open_questions.map((q, i) => (
            <Field key={i} label={q}>
              <textarea
                className="input textarea"
                rows={2}
                placeholder="不確定可以留空"
                value={answers[q] ?? ''}
                onChange={(e) => setAnswers((a) => ({ ...a, [q]: e.target.value }))}
              />
            </Field>
          ))}
        </>
      )}

      <Field label="名詞表" hint={`${TERMS_FORMAT}這份文件的每一節都會用這些名稱，例如狀態名稱要在這裡定好。`}>
        <textarea className="input textarea" rows={Math.min(Math.max((plan.terms?.length ?? 0) + 1, 3), 10)} value={terms} onChange={(e) => setTerms(e.target.value)} />
      </Field>

      <details className="mb-sm">
        <summary className="subhead">各節要點（{plan.sections.length} 節）</summary>
        {plan.sections.map((s, i) => (
          <Field key={s.id} label={`${i + 1}. ${s.heading}`} hint={s.goal}>
            <textarea
              className="input textarea"
              rows={Math.max(s.key_points.length, 2)}
              value={points[s.id] ?? ''}
              onChange={(e) => setPoints((p) => ({ ...p, [s.id]: e.target.value }))}
            />
          </Field>
        ))}
      </details>

      <div className="row gap-sm">
        <button className="btn btn-primary" onClick={submit} disabled={busy}>
          {busy ? <Spinner small /> : answeredCount ? `繼續撰寫（已回答 ${answeredCount} 題）` : '繼續撰寫'}
        </button>
        <button className="btn btn-danger-outline" onClick={props.onCancel} disabled={busy}>
          取消工作
        </button>
      </div>
    </section>
  )
}
