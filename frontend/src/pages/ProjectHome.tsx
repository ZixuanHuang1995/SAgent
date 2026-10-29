// 專案總覽：各階段的 Spec、最近的 AI 工作、專案設定

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { api, type PlanTerm } from '../api'
import { relativeTime, RUN_STATUS, SPEC_STATUS, termsToText, textToTerms } from '../format'
import { keys, useBaselines, useProjectTree, useRuns, useTemplateCategories, useTemplates } from '../queries'
import { Badge, CategoryBadge, ConfirmDialog, Empty, errorText, Field, Menu, MenuItem, MenuSeparator, Modal, Skeleton, useToast } from '../components/ui'
import { NewSpecDialog } from './ProjectLayout'
import { Icon } from '../components/icons'

export function ProjectHome() {
  const projectId = Number(useParams().projectId)
  const tree = useProjectTree(projectId)
  const runs = useRuns({ project_id: projectId })
  const baselines = useBaselines(projectId)
  const [editing, setEditing] = useState(false)
  const [editingGlossary, setEditingGlossary] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [newSpec, setNewSpec] = useState(false)
  const navigate = useNavigate()
  const qc = useQueryClient()
  const toast = useToast()

  if (!tree.data)
    return (
      <div className="page">
        <Skeleton lines={1} />
        <div className="card mt"><Skeleton lines={3} /></div>
      </div>
    )
  const { project, phases, specs } = tree.data
  const counts = specs.reduce<Record<string, number>>((acc, s) => ({ ...acc, [s.status]: (acc[s.status] ?? 0) + 1 }), {})

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>
            {project.name} {project.code && <span className="project-code">{project.code}</span>}
          </h1>
          <p className="muted">{project.description || '（沒有說明）'}</p>
        </div>
        <div className="row gap-sm">
          <a className="btn" href={api.projectExportUrl(projectId)} download>
            匯出全部（zip）
          </a>
          <button className="btn btn-primary" onClick={() => setNewSpec(true)} disabled={phases.length === 0}>
            + 新增 Spec
          </button>
          <Menu trigger={<Icon name="more" />} label="專案的其他操作" title="其他操作">
            <MenuItem icon="edit" onSelect={() => setEditing(true)}>
              編輯名稱和說明
            </MenuItem>
            <MenuSeparator />
            <MenuItem icon="trash" danger onSelect={() => setDeleting(true)}>
              刪除專案…
            </MenuItem>
          </Menu>
        </div>
      </div>

      <div className="stat-row">
        <div className="stat">
          <div className="stat-num">{specs.length}</div>
          <div className="stat-label">Spec</div>
        </div>
        <div className="stat">
          <div className="stat-num">{counts.approved ?? 0}</div>
          <div className="stat-label">已核准</div>
        </div>
        <div className="stat">
          <div className="stat-num">{phases.length}</div>
          <div className="stat-label">功能階段</div>
        </div>
        <div className="stat">
          <div className="stat-num">{baselines.data?.length ?? 0}</div>
          <div className="stat-label">基準版本</div>
        </div>
      </div>

      <section className="card">
        <div className="card-title">
          專案詞彙表 <span className="muted small">{project.glossary.length} 個</span>
          <span className="spacer" />
          <button className="btn btn-sm" onClick={() => setEditingGlossary(true)}>
            編輯
          </button>
        </div>
        {project.glossary.length === 0 ? (
          <div className="muted small">
            還沒有名詞。列出這個專案的角色、狀態、主要資料的名稱，AI 撰寫每一份文件時都會照這些名稱寫，文件之間才不會說法不一樣。
          </div>
        ) : (
          <ul className="glossary-list">
            {project.glossary.map((t) => (
              <li key={t.name}>
                <strong>{t.name}</strong>
                {t.kind && <span className="muted">（{t.kind}）</span>}
                {t.note && <span className="muted small">：{t.note}</span>}
              </li>
            ))}
          </ul>
        )}
      </section>

      {specs.length === 0 && (
        <Empty title="這個專案還沒有 Spec">
          <button className="btn btn-primary" onClick={() => setNewSpec(true)} disabled={phases.length === 0}>
            + 新增第一份 Spec
          </button>
        </Empty>
      )}

      {phases.map((phase) => {
        const list = specs.filter((s) => s.phase_id === phase.id)
        if (list.length === 0) return null
        return (
          <section key={phase.id} className="card">
            <div className="card-title">
              {phase.name} <span className="muted small">{list.length} 份</span>
            </div>
            {phase.description && <p className="muted small">{phase.description}</p>}
            <table className="table">
              <thead>
                <tr>
                  <th>標題</th>
                  <th>類型</th>
                  <th>狀態</th>
                  <th title="最近一次 AI 審查還沒處理的項目">待確認</th>
                  <th>版本</th>
                  <th>更新</th>
                </tr>
              </thead>
              <tbody>
                {list.map((s) => (
                  <tr key={s.id} className="clickable" onClick={() => navigate(`/p/${projectId}/s/${s.id}`)}>
                    <td>
                      <Link to={`/p/${projectId}/s/${s.id}`}>{s.title}</Link>
                    </td>
                    <td>
                      <DocTypeCell docType={s.doc_type} />
                    </td>
                    <td>
                      <span className={`status-dot status-${s.status}`} /> {SPEC_STATUS[s.status]}
                    </td>
                    <td>
                      {s.review && s.review.open > 0 ? (
                        <Badge tone="amber">{s.review.open}</Badge>
                      ) : s.review ? (
                        <span className="muted small">已處理</span>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td>{s.head_version_no > 0 ? `v${s.head_version_no}` : '—'}</td>
                    <td className="muted">{relativeTime(s.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )
      })}

      <section className="card">
        <div className="card-title">
          最近的 AI 工作 <Link to={`/p/${projectId}/runs`} className="small">全部</Link>
        </div>
        {runs.data?.length === 0 && <div className="muted small">還沒有 AI 工作。</div>}
        <ul className="plain-list">
          {runs.data?.slice(0, 5).map((r) => (
            <li key={r.id} className="row gap-sm">
              <Badge tone={r.status === 'succeeded' ? 'green' : r.status === 'failed' ? 'red' : 'blue'}>{RUN_STATUS[r.status]}</Badge>
              <Link to={`/p/${projectId}/s/${r.spec_id}`}>{r.spec_title}</Link>
              <span className="muted small">
                {r.mode === 'revise' ? '修訂' : '撰寫'}・{r.model}・{relativeTime(r.created_at)}
              </span>
            </li>
          ))}
        </ul>
      </section>

      {newSpec && <NewSpecDialog projectId={projectId} phases={phases} phaseId={phases[0]?.id ?? null} onClose={() => setNewSpec(false)} />}
      {editing && <EditProjectDialog projectId={projectId} initial={project} onClose={() => setEditing(false)} />}
      {editingGlossary && <GlossaryDialog projectId={projectId} initial={project.glossary} onClose={() => setEditingGlossary(false)} />}
      {deleting && (
        <ConfirmDialog
          title={`刪除專案「${project.name}」`}
          message={`這會永久刪除專案底下所有的階段、${specs.length} 份 Spec、所有版本和 AI 紀錄，無法復原。確定要刪除嗎？`}
          confirmText="永久刪除"
          danger
          onConfirm={async () => {
            try {
              await api.deleteProject(projectId)
              qc.invalidateQueries({ queryKey: keys.projects })
              toast('專案已刪除', 'success')
              navigate('/')
            } catch (e) {
              toast(errorText(e), 'error')
            }
          }}
          onClose={() => setDeleting(false)}
        />
      )}
    </div>
  )
}

function EditProjectDialog(props: { projectId: number; initial: { name: string; code: string; description: string }; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [form, setForm] = useState(props.initial)
  const save = useMutation({
    mutationFn: () => api.updateProject(props.projectId, form),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tree', props.projectId] })
      qc.invalidateQueries({ queryKey: keys.projects })
      props.onClose()
    },
    onError: (e) => toast(errorText(e), 'error'),
  })
  return (
    <Modal
      title="編輯專案"
      onClose={props.onClose}
      footer={
        <>
          <button className="btn" onClick={props.onClose}>
            取消
          </button>
          <button className="btn btn-primary" disabled={!form.name.trim() || save.isPending} onClick={() => save.mutate()}>
            儲存
          </button>
        </>
      }
    >
      <Field label="專案名稱">
        <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
      </Field>
      <Field label="專案代碼">
        <input className="input" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} />
      </Field>
      <Field label="說明" hint="AI 撰寫時會參考這段說明">
        <textarea className="input textarea" rows={4} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
      </Field>
    </Modal>
  )
}

function GlossaryDialog(props: { projectId: number; initial: PlanTerm[]; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [text, setText] = useState(() => termsToText(props.initial))
  const terms = textToTerms(text)
  const save = useMutation({
    mutationFn: () => api.updateProject(props.projectId, { glossary: terms }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tree', props.projectId] })
      props.onClose()
    },
    onError: (e) => toast(errorText(e), 'error'),
  })
  return (
    <Modal
      title="專案詞彙表"
      onClose={props.onClose}
      footer={
        <>
          <button className="btn" onClick={props.onClose}>
            取消
          </button>
          <button className="btn btn-primary" disabled={save.isPending || terms.length > 40} onClick={() => save.mutate()}>
            儲存
          </button>
        </>
      }
    >
      <Field
        label={`名詞（${terms.length} / 40）`}
        hint="一行一個：名稱（類型）：說明。類型用角色、狀態、資料或其他。AI 撰寫和修訂時都會沿用這些名稱，例如狀態名稱在這裡定好，每份文件就會寫一樣的。"
      >
        <textarea
          className="input textarea"
          rows={12}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={'報修人（角色）：提出報修的員工\n已派工（狀態）：總務已指派維修人員'}
        />
      </Field>
    </Modal>
  )
}

function DocTypeCell({ docType }: { docType: string }) {
  const templates = useTemplates()
  const categories = useTemplateCategories()
  const template = templates.data?.find((t) => t.key === docType)
  const category = categories.data?.find((c) => c.key === template?.category)
  return (
    <span className="row gap-sm">
      {category && <CategoryBadge category={category.key}>{category.name}</CategoryBadge>}
      <span className="muted small">{template?.name ?? docType}</span>
    </span>
  )
}
