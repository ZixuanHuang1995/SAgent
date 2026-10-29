// 基準版本：記錄某個時間點每份 Spec 的版本，之後可以比較、匯出

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router'
import { api, type CompareItem } from '../api'
import { DiffModal } from '../components/DiffView'
import { Badge, ConfirmDialog, Empty, errorText, Field, Modal, Skeleton, Spinner, useToast } from '../components/ui'
import { formatTime } from '../format'
import { keys, useBaselines, useProjectTree } from '../queries'
import { Icon } from '../components/icons'

const COMPARE_STATUS: Record<CompareItem['status'], { text: string; tone: string }> = {
  changed: { text: '有變更', tone: 'amber' },
  added: { text: '新增', tone: 'green' },
  removed: { text: '移除', tone: 'red' },
  unchanged: { text: '未變更', tone: 'neutral' },
}

export function BaselinesPage() {
  const projectId = Number(useParams().projectId)
  const baselines = useBaselines(projectId)
  const [creating, setCreating] = useState(false)
  const [viewing, setViewing] = useState<number | null>(null)
  const [deleting, setDeleting] = useState<number | null>(null)
  const [from, setFrom] = useState<number | ''>('')
  const [to, setTo] = useState<number | ''>('')
  const qc = useQueryClient()
  const toast = useToast()

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>基準版本</h1>
          <p className="muted">
            把目前每份 Spec 的最新版記錄成一個「基準」，例如「需求凍結 v1.0」「第一期驗收版」。之後可以跟目前內容比較，或匯出當時的所有文件。
          </p>
        </div>
        <button className="btn btn-primary" onClick={() => setCreating(true)}>
          + 建立基準版本
        </button>
      </div>

      {baselines.isLoading && <div className="card"><Skeleton lines={3} /></div>}
      {baselines.data?.length === 0 && <Empty title="還沒有基準版本">文件告一段落（例如送客戶確認）時建立一個基準版本。</Empty>}
      {baselines.data && baselines.data.length > 0 && (
        <section className="card">
          <table className="table">
            <thead>
              <tr>
                <th>名稱</th>
                <th>說明</th>
                <th>Spec 數</th>
                <th>建立時間</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {baselines.data.map((b) => (
                <tr key={b.id}>
                  <td>
                    <button className="link-btn strong" onClick={() => setViewing(b.id)}>
                      <Icon name="tag" size={14} /> {b.name}
                    </button>
                  </td>
                  <td className="muted">{b.note}</td>
                  <td>{b.item_count}</td>
                  <td className="muted">{formatTime(b.created_at)}</td>
                  <td className="row gap-sm nowrap">
                    <button className="btn btn-sm" onClick={() => { setFrom(b.id); setTo('') }}>
                      與目前比較
                    </button>
                    <a className="btn btn-sm" href={api.projectExportUrl(projectId, b.id)} download>
                      匯出 zip
                    </a>
                    <button className="btn btn-sm btn-danger-outline" onClick={() => setDeleting(b.id)}>
                      刪除
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {baselines.data && baselines.data.length > 0 && (
        <section className="card">
          <div className="card-title">比較</div>
          <div className="form-row">
            <Field label="從">
              <select className="input" value={from} onChange={(e) => setFrom(e.target.value ? Number(e.target.value) : '')}>
                <option value="">（請選擇基準版本）</option>
                {baselines.data.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="到">
              <select className="input" value={to} onChange={(e) => setTo(e.target.value ? Number(e.target.value) : '')}>
                <option value="">目前最新版</option>
                {baselines.data.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          {from !== '' && <CompareResult projectId={projectId} from={from} to={to === '' ? null : to} />}
        </section>
      )}

      {creating && <CreateBaselineDialog projectId={projectId} onClose={() => setCreating(false)} />}
      {viewing !== null && <BaselineDetail projectId={projectId} baselineId={viewing} onClose={() => setViewing(null)} />}
      {deleting !== null && (
        <ConfirmDialog
          title="刪除基準版本"
          message="只會刪除這個基準紀錄，Spec 和版本都不受影響。"
          confirmText="刪除"
          danger
          onConfirm={async () => {
            try {
              await api.deleteBaseline(deleting)
              qc.invalidateQueries({ queryKey: keys.baselines(projectId) })
              if (from === deleting) setFrom('')
              if (to === deleting) setTo('')
            } catch (e) {
              toast(errorText(e), 'error')
            }
          }}
          onClose={() => setDeleting(null)}
        />
      )}
    </div>
  )
}

function CompareResult({ projectId, from, to }: { projectId: number; from: number; to: number | null }) {
  const q = useQuery({ queryKey: ['compare', projectId, from, to], queryFn: () => api.compare(projectId, from, to) })
  const [diff, setDiff] = useState<CompareItem | null>(null)
  const [showSame, setShowSame] = useState(false)
  if (q.isLoading) return <Spinner />
  if (q.error) return <div className="error-box">{errorText(q.error)}</div>
  const items = q.data!.items
  const changed = items.filter((i) => i.status !== 'unchanged')
  const shown = showSame ? items : changed
  return (
    <div className="compare">
      <div className="row gap-sm mb-sm">
        {(['changed', 'added', 'removed', 'unchanged'] as const).map((s) => (
          <Badge key={s} tone={COMPARE_STATUS[s].tone}>
            {COMPARE_STATUS[s].text} {items.filter((i) => i.status === s).length}
          </Badge>
        ))}
        <span className="spacer" />
        <label className="small">
          <input type="checkbox" checked={showSame} onChange={(e) => setShowSame(e.target.checked)} /> 顯示未變更
        </label>
      </div>
      {shown.length === 0 ? (
        <div className="muted">沒有任何變更。</div>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Spec</th>
              <th>階段</th>
              <th>變化</th>
              <th>版本</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {shown.map((i) => (
              <tr key={i.spec_id}>
                <td>
                  <Link to={`/p/${projectId}/s/${i.spec_id}`}>{i.title}</Link>
                </td>
                <td className="muted">{i.phase_name}</td>
                <td>
                  <Badge tone={COMPARE_STATUS[i.status].tone}>{COMPARE_STATUS[i.status].text}</Badge>
                </td>
                <td className="muted">
                  {i.from_version ? `v${i.from_version}` : '—'} → {i.to_version ? `v${i.to_version}` : '—'}
                </td>
                <td>
                  {i.status === 'changed' && (
                    <button className="link-btn" onClick={() => setDiff(i)}>
                      看差異
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {diff && <DiffModal specId={diff.spec_id} from={diff.from_version ?? 0} to={diff.to_version ?? undefined} onClose={() => setDiff(null)} />}
    </div>
  )
}

function CreateBaselineDialog({ projectId, onClose }: { projectId: number; onClose: () => void }) {
  const tree = useProjectTree(projectId)
  const qc = useQueryClient()
  const toast = useToast()
  const [name, setName] = useState('')
  const [note, setNote] = useState('')
  const [phaseId, setPhaseId] = useState<number | ''>('')
  const create = useMutation({
    mutationFn: () => api.createBaseline(projectId, { name, note, phase_id: phaseId === '' ? null : phaseId }),
    onSuccess: (b) => {
      qc.invalidateQueries({ queryKey: keys.baselines(projectId) })
      toast(`已建立基準版本「${b.name}」，包含 ${b.item_count} 份 Spec`, 'success')
      onClose()
    },
    onError: (e) => toast(errorText(e), 'error'),
  })
  return (
    <Modal
      title="建立基準版本"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            取消
          </button>
          <button className="btn btn-primary" disabled={!name.trim() || create.isPending} onClick={() => create.mutate()}>
            建立
          </button>
        </>
      }
    >
      <Field label="名稱">
        <input className="input" autoFocus value={name} placeholder="例如：需求凍結 v1.0、客戶確認版" onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field label="說明（選填）">
        <textarea className="input textarea" rows={3} value={note} onChange={(e) => setNote(e.target.value)} />
      </Field>
      <Field label="範圍" hint="會記錄範圍內每份 Spec（未封存、至少有一個版本）目前的最新版">
        <select className="input" value={phaseId} onChange={(e) => setPhaseId(e.target.value ? Number(e.target.value) : '')}>
          <option value="">整個專案</option>
          {tree.data?.phases.map((p) => (
            <option key={p.id} value={p.id}>
              只有「{p.name}」階段
            </option>
          ))}
        </select>
      </Field>
    </Modal>
  )
}

function BaselineDetail({ projectId, baselineId, onClose }: { projectId: number; baselineId: number; onClose: () => void }) {
  const q = useQuery({ queryKey: ['baseline', baselineId], queryFn: () => api.baseline(baselineId) })
  return (
    <Modal title={q.data ? q.data.baseline.name : '基準版本'} onClose={onClose} wide>
      {q.isLoading && <Spinner />}
      {q.data && (
        <>
          <p className="muted">
            {q.data.baseline.note || '（沒有說明）'}・建立於 {formatTime(q.data.baseline.created_at)}
          </p>
          <table className="table">
            <thead>
              <tr>
                <th>Spec</th>
                <th>階段</th>
                <th>版本</th>
              </tr>
            </thead>
            <tbody>
              {q.data.items.map((i) => (
                <tr key={i.spec_id}>
                  <td>
                    <Link to={`/p/${projectId}/s/${i.spec_id}`} onClick={onClose}>
                      {i.title}
                    </Link>
                    {i.archived && <Badge tone="neutral">已封存</Badge>}
                  </td>
                  <td className="muted">{i.phase_name}</td>
                  <td>
                    v{i.version_no} {i.label && <Badge tone="green">{i.label}</Badge>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </Modal>
  )
}
