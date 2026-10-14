// 單一 AI 工作的詳細內容：需求、選項、重播過程、大綱、審查

import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { formatTime, UML_SYNTAX } from '../format'
import { useRunStream } from '../hooks/useRunStream'
import { keys, useTemplates } from '../queries'
import { RunProgress } from './RunProgress'
import { Modal, Spinner } from './ui'

export function RunDetailModal(props: { runId: number; onClose: () => void; onDiff?: (versionNo: number) => void }) {
  const q = useQuery({ queryKey: keys.run(props.runId), queryFn: () => api.run(props.runId) })
  const stream = useRunStream(props.runId)
  const templates = useTemplates()
  const run = q.data
  const templateName = templates.data?.find((t) => t.key === run?.doc_type)?.name ?? run?.doc_type
  return (
    <Modal title={`AI 工作 #${props.runId}${run ? `：${run.spec_title}` : ''}`} onClose={props.onClose} full>
      {!run ? (
        <Spinner />
      ) : (
        <div className="run-detail">
          <aside className="run-detail-side">
            <div className="card">
              <div className="card-title">{run.mode === 'revise' ? '修訂需求' : '需求內容'}</div>
              <pre className="requirement">{run.requirement}</pre>
            </div>
            <div className="card">
              <div className="card-title">設定</div>
              <dl className="kv">
                <dt>模型</dt>
                <dd>{run.model}</dd>
                <dt>文件類型</dt>
                <dd>{templateName}</dd>
                <dt>UML 語法</dt>
                <dd>{UML_SYNTAX[String(run.options.uml_syntax ?? 'auto')] ?? String(run.options.uml_syntax)}</dd>
                <dt>畫圖</dt>
                <dd>{run.options.diagrams ? `是（最多 ${run.options.max_diagrams ?? 4} 張）` : '否'}</dd>
                <dt>修訂輪數上限</dt>
                <dd>{String(run.options.max_revisions ?? '')}</dd>
                <dt>開始版本</dt>
                <dd>v{run.base_version_no}</dd>
                <dt>結果版本</dt>
                <dd>{run.result_version_no ? `v${run.result_version_no}` : '—'}</dd>
                <dt>建立時間</dt>
                <dd>{formatTime(run.created_at)}</dd>
              </dl>
            </div>
            {run.error && <div className="error-box">{run.error}</div>}
          </aside>
          <div className="run-detail-main">
            <RunProgress run={run} state={stream} onDiff={props.onDiff} />
          </div>
        </div>
      )}
    </Modal>
  )
}
