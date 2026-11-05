// 單一 AI 工作的詳細內容：需求、選項、重播過程、大綱、審查

import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { formatDuration, formatTime, STOP_REASON, UML_SYNTAX } from '../format'
import { useRunStream } from '../hooks/useRunStream'
import { keys, useTemplates } from '../queries'
import { RunProgress } from './RunProgress'
import { RunVerdict } from './RunVerdict'
import { Modal, Spinner } from './ui'

export function RunDetailModal(props: {
  runId: number
  onClose: () => void
  onDiff?: (versionNo: number) => void
  /** 點審查結果裡的章節名稱：關掉對話框、跳到文件裡的這一節 */
  onJump?: (heading: string) => void
}) {
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
                {!!run.options.reviewer_model && run.options.reviewer_model !== run.model && (
                  <>
                    <dt>審查模型</dt>
                    <dd>{String(run.options.reviewer_model)}</dd>
                  </>
                )}
                <dt>文件類型</dt>
                <dd>{templateName}</dd>
                <dt>UML 語法</dt>
                <dd>{UML_SYNTAX[String(run.options.uml_syntax ?? 'auto')] ?? String(run.options.uml_syntax)}</dd>
                <dt>畫圖</dt>
                <dd>{/* 舊的 AI 工作有記張數上限；新的工作不限制張數，沒有這個值 */}
                  {run.options.diagrams ? (run.options.max_diagrams != null ? `是（最多 ${run.options.max_diagrams} 張）` : '是') : '否'}</dd>
                {Array.isArray(run.options.reference_specs) && run.options.reference_specs.length > 0 && (
                  <>
                    <dt>參考文件</dt>
                    <dd>{(run.options.reference_specs as { title: string }[]).map((r) => r.title).join('、')}</dd>
                  </>
                )}
                <dt>修訂輪數上限</dt>
                <dd>{String(run.options.max_revisions ?? '')}</dd>
                {Number(run.options.max_run_minutes) > 0 && (
                  <>
                    <dt>時間上限</dt>
                    <dd>{String(run.options.max_run_minutes)} 分鐘</dd>
                  </>
                )}
                {run.stop_reason && (
                  <>
                    <dt>停止原因</dt>
                    <dd>{STOP_REASON[run.stop_reason] ?? run.stop_reason}</dd>
                  </>
                )}
                {run.wall_seconds != null && (
                  <>
                    <dt>實際經過時間</dt>
                    <dd title="從開始到結束的時間，電腦睡眠也算；等使用者確認大綱的時間不算">{formatDuration(run.wall_seconds * 1000)}</dd>
                  </>
                )}
                <dt>開始版本</dt>
                <dd>v{run.base_version_no}</dd>
                <dt>結果版本</dt>
                <dd>{run.result_version_no ? `v${run.result_version_no}` : '—'}</dd>
                <dt>建立時間</dt>
                <dd>{formatTime(run.created_at)}</dd>
              </dl>
            </div>
            {run.status === 'succeeded' && (
              <div className="card">
                <div className="card-title">人工判斷</div>
                <RunVerdict run={run} />
              </div>
            )}
            {run.error && <div className="error-box">{run.error}</div>}
          </aside>
          <div className="run-detail-main">
            <RunProgress run={run} state={stream} onDiff={props.onDiff} onJumpToSection={props.onJump} />
          </div>
        </div>
      )}
    </Modal>
  )
}
