// 版本歷程：檢視、比較、還原、加標籤

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type VersionMeta } from '../api'
import { formatTime, relativeTime, VERSION_SOURCE } from '../format'
import { keys, useVersions } from '../queries'
import { DiffModal } from './DiffView'
import { MarkdownView } from './MarkdownView'
import { Badge, ConfirmDialog, Empty, errorText, Menu, MenuItem, Modal, Skeleton, Spinner, useToast } from './ui'
import { Icon } from './icons'

const SOURCE_TONE: Record<VersionMeta['source'], string> = {
  manual: 'neutral',
  agent: 'violet',
  restore: 'amber',
  import: 'blue',
  template: 'neutral',
}

export function VersionPanel({ specId, headNo, archived, dirty }: { specId: number; headNo: number; archived: boolean; dirty: boolean }) {
  const versions = useVersions(specId)
  const qc = useQueryClient()
  const toast = useToast()
  const [selected, setSelected] = useState<number[]>([])
  const [diff, setDiff] = useState<{ from?: number; to?: number } | null>(null)
  const [viewing, setViewing] = useState<number | null>(null)
  const [restoring, setRestoring] = useState<number | null>(null)
  const [labeling, setLabeling] = useState<VersionMeta | null>(null)

  const refresh = () => {
    qc.invalidateQueries({ queryKey: keys.versions(specId) })
    qc.invalidateQueries({ queryKey: keys.spec(specId) })
    qc.invalidateQueries({ queryKey: ['tree'] })
  }

  const toggle = (no: number) =>
    setSelected((s) => (s.includes(no) ? s.filter((x) => x !== no) : [...s.slice(-1), no]))

  if (versions.isLoading)
    return (
      <>
        <div className="version-item"><Skeleton lines={2} /></div>
        <div className="version-item"><Skeleton lines={2} /></div>
      </>
    )
  if (!versions.data?.length) return <Empty title="還沒有任何版本">存檔或使用 AI 撰寫後，這裡會列出每個版本。</Empty>

  return (
    <div className="version-panel">
      <div className="panel-toolbar">
        <span className="muted small">勾選兩個版本可以比較</span>
        <button
          className="btn btn-sm"
          disabled={selected.length !== 2}
          onClick={() => {
            const [a, b] = [...selected].sort((x, y) => x - y)
            setDiff({ from: a, to: b })
          }}
        >
          比較選取的版本
        </button>
      </div>
      <ul className="version-list">
        {versions.data.map((v) => (
          <li key={v.id} className={`version-item ${v.version_no === headNo ? 'is-head' : ''}`}>
            <input
              type="checkbox"
              checked={selected.includes(v.version_no)}
              onChange={() => toggle(v.version_no)}
              aria-label={`選取 v${v.version_no}`}
            />
            <div className="version-main">
              <div className="version-line">
                <strong>v{v.version_no}</strong>
                {v.version_no === headNo && <Badge tone="blue">最新</Badge>}
                {v.label && <Badge tone="green"><Icon name="tag" size={12} /> {v.label}</Badge>}
                <Badge tone={SOURCE_TONE[v.source]}>{VERSION_SOURCE[v.source]}</Badge>
                <span className="spacer" />
                <LineStats version={v} />
              </div>
              {v.message && <div className="version-msg">{v.message}</div>}
              <div className="version-foot">
                <span className="version-time muted small" title={formatTime(v.created_at)}>
                  {relativeTime(v.created_at)}
                  {v.run_id ? `・AI 工作 #${v.run_id}` : ''}
                </span>
                <span className="spacer" />
                <button className="btn btn-sm" onClick={() => setViewing(v.version_no)}>
                  檢視
                </button>
                {v.version_no > 1 && (
                  <button
                    className="btn btn-sm"
                    title={`v${v.version_no - 1} → v${v.version_no} 改了什麼`}
                    onClick={() => setDiff({ from: v.version_no - 1, to: v.version_no })}
                  >
                    比較前一版
                  </button>
                )}
                <Menu trigger={<Icon name="more" />} label={`v${v.version_no} 的其他操作`} title="其他操作" triggerClassName="btn btn-sm">
                  {v.version_no !== headNo && (
                    <MenuItem icon="diff" onSelect={() => setDiff({ from: v.version_no, to: headNo })}>
                      與最新版比較
                    </MenuItem>
                  )}
                  {v.version_no !== headNo && !archived && (
                    <MenuItem icon="restore" onSelect={() => setRestoring(v.version_no)}>
                      還原成這一版…
                    </MenuItem>
                  )}
                  <MenuItem icon="tag" onSelect={() => setLabeling(v)}>
                    {v.label ? '修改標籤' : '加標籤'}
                  </MenuItem>
                  <MenuItem icon="download" href={api.specExportUrl(specId, v.version_no)} download>
                    下載 Markdown
                  </MenuItem>
                </Menu>
              </div>
            </div>
          </li>
        ))}
      </ul>

      {diff && <DiffModal specId={specId} from={diff.from} to={diff.to} onClose={() => setDiff(null)} />}
      {viewing !== null && <VersionViewer specId={specId} no={viewing} onClose={() => setViewing(null)} />}
      {restoring !== null && (
        <ConfirmDialog
          title={`還原到 v${restoring}`}
          message={
            <>
              會把 v{restoring} 的內容存成新的版本（v{headNo + 1}），原本的版本都會保留。
              {dirty && <p className="warn-text">注意：編輯器裡還沒存檔的修改會被取代。</p>}
            </>
          }
          confirmText="還原"
          onConfirm={async () => {
            try {
              const r = await api.restoreVersion(specId, restoring)
              toast(r.created ? `已還原，存成 v${r.version.version_no}` : '內容跟最新版相同，沒有產生新版本', 'success')
              refresh()
            } catch (e) {
              toast(errorText(e), 'error')
            }
          }}
          onClose={() => setRestoring(null)}
        />
      )}
      {labeling && <LabelDialog specId={specId} version={labeling} onClose={() => setLabeling(null)} onDone={refresh} />}
    </div>
  )
}

/** 這一版跟前一版比較：+新增行數 −刪除行數 */
function LineStats({ version }: { version: VersionMeta }) {
  const { lines_added: added, lines_removed: removed } = version
  if (added === null || removed === null) return null
  const title = version.version_no === 1 ? `第一版，共 ${added} 行` : `跟 v${version.version_no - 1} 比較：新增 ${added} 行、刪除 ${removed} 行`
  return (
    <span className="line-stats small" title={title}>
      <span className="diff-stat-add">+{added}</span> <span className="diff-stat-del">−{removed}</span>
    </span>
  )
}

function VersionViewer({ specId, no, onClose }: { specId: number; no: number; onClose: () => void }) {
  const q = useQuery({ queryKey: ['version', specId, no], queryFn: () => api.version(specId, no) })
  const [raw, setRaw] = useState(false)
  return (
    <Modal
      title={`檢視 v${no}`}
      onClose={onClose}
      full
      footer={
        <button className="btn" onClick={() => setRaw((r) => !r)}>
          {raw ? '顯示預覽' : '顯示原始 Markdown'}
        </button>
      }
    >
      {q.isLoading && <Spinner />}
      {q.data && (raw ? <pre className="raw-markdown">{q.data.content}</pre> : <MarkdownView markdown={q.data.content} />)}
    </Modal>
  )
}

function LabelDialog(props: { specId: number; version: VersionMeta; onClose: () => void; onDone: () => void }) {
  const toast = useToast()
  const [label, setLabel] = useState(props.version.label ?? '')
  const save = useMutation({
    mutationFn: (value: string | null) => api.setLabel(props.specId, props.version.version_no, value),
    onSuccess: () => {
      props.onDone()
      props.onClose()
    },
    onError: (e) => toast(errorText(e), 'error'),
  })
  return (
    <Modal
      title={`v${props.version.version_no} 的標籤`}
      onClose={props.onClose}
      footer={
        <>
          {props.version.label && (
            <button className="btn" onClick={() => save.mutate(null)} disabled={save.isPending}>
              移除標籤
            </button>
          )}
          <button className="btn btn-primary" onClick={() => save.mutate(label.trim() || null)} disabled={save.isPending}>
            儲存
          </button>
        </>
      }
    >
      <p className="muted">標籤用來標記重要的版本，例如「v1.0」「客戶確認版」。同一份 Spec 的標籤不能重複。</p>
      <input
        className="input"
        autoFocus
        value={label}
        maxLength={100}
        placeholder="例如：客戶確認版"
        onChange={(e) => setLabel(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && save.mutate(label.trim() || null)}
      />
    </Modal>
  )
}
