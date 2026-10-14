// 專案的 AI 工作紀錄

import { useState } from 'react'
import { Link, useParams } from 'react-router'
import { RunDetailModal } from '../components/RunDetail'
import { Badge, Empty, Skeleton } from '../components/ui'
import { formatDuration, formatTime, RUN_STATUS, runDuration } from '../format'
import { useRuns } from '../queries'

export function RunsPage() {
  const projectId = Number(useParams().projectId)
  const runs = useRuns({ project_id: projectId }, 5000)
  const [detail, setDetail] = useState<number | null>(null)
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>AI 工作紀錄</h1>
          <p className="muted">每次 AI 撰寫都會保留需求內容、大綱、每個步驟和審查結果。點一列可以看詳細過程。</p>
        </div>
      </div>
      {runs.isLoading && <div className="card"><Skeleton lines={5} title={false} /></div>}
      {runs.data?.length === 0 && <Empty title="還沒有 AI 工作">在任何一份 Spec 的「AI 代理」輸入需求就能開始。</Empty>}
      {runs.data && runs.data.length > 0 && (
        <section className="card">
          <table className="table">
            <thead>
              <tr>
                <th>#</th>
                <th>Spec</th>
                <th>模式</th>
                <th>狀態</th>
                <th>模型</th>
                <th>建立時間</th>
                <th>耗時</th>
                <th>結果</th>
              </tr>
            </thead>
            <tbody>
              {runs.data.map((r) => {
                const ms = runDuration(r)
                return (
                  <tr key={r.id} className="clickable" onClick={() => setDetail(r.id)}>
                    <td>{r.id}</td>
                    <td>
                      <Link to={`/p/${projectId}/s/${r.spec_id}`} onClick={(e) => e.stopPropagation()}>
                        {r.spec_title}
                      </Link>
                    </td>
                    <td>{r.mode === 'revise' ? '修訂' : '撰寫'}</td>
                    <td>
                      <Badge tone={r.status === 'succeeded' ? 'green' : r.status === 'failed' ? 'red' : r.status === 'cancelled' ? 'neutral' : 'blue'}>
                        {RUN_STATUS[r.status]}
                      </Badge>
                    </td>
                    <td className="muted">{r.model}</td>
                    <td className="muted">{formatTime(r.created_at)}</td>
                    <td className="muted">{ms !== null ? formatDuration(ms) : ''}</td>
                    <td>{r.result_version_no ? `v${r.result_version_no}` : r.error ? <span className="muted small">{r.error.slice(0, 40)}</span> : ''}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </section>
      )}
      {detail !== null && <RunDetailModal runId={detail} onClose={() => setDetail(null)} />}
    </div>
  )
}
