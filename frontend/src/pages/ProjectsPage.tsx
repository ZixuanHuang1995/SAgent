// 首頁：專案清單（卡片或列表）、搜尋、排序、建立專案

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { api, type PhaseSummary, type Project } from '../api'
import { relativeTime } from '../format'
import { usePersistent } from '../hooks/layout'
import { keys, useProjects } from '../queries'
import { Badge, Empty, errorText, Field, Modal, Skeleton, useToast } from '../components/ui'
import { Icon } from '../components/icons'
import { NewSpecDialog } from './ProjectLayout'

type View = 'grid' | 'list'
type Sort = 'updated' | 'name'

export function ProjectsPage() {
  const projects = useProjects()
  const [creating, setCreating] = useState(false)
  const [query, setQuery] = useState('')
  const [view, setView] = usePersistent<View>('sagent.projects.view', 'grid')
  const [sort, setSort] = usePersistent<Sort>('sagent.projects.sort', 'updated')

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    const list = (projects.data ?? []).filter(
      (p) => !q || [p.name, p.code, p.description].some((s) => s.toLowerCase().includes(q)),
    )
    // API 已經依更新時間排好，名稱排序才要另外排
    return sort === 'name' ? [...list].sort((a, b) => a.name.localeCompare(b.name, 'zh-Hant')) : list
  }, [projects.data, query, sort])

  const hasProjects = (projects.data?.length ?? 0) > 0
  return (
    <div className="page">
      <div className="page-head projects-head">
        <h1>專案</h1>
        <div className="projects-tools">
          {hasProjects && (
            <>
              <label className="projects-search">
                <Icon name="search" size={14} />
                <input
                  className="input"
                  type="search"
                  placeholder="搜尋名稱、代碼、說明"
                  aria-label="搜尋專案"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
              </label>
              <select className="input projects-sort" aria-label="排序" value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
                <option value="updated">最近更新</option>
                <option value="name">名稱</option>
              </select>
              <div className="seg view-toggle" role="group" aria-label="檢視方式">
                {(
                  [
                    ['grid', 'overview', '卡片'],
                    ['list', 'list', '列表'],
                  ] as const
                ).map(([k, icon, label]) => (
                  <button
                    key={k}
                    className={view === k ? 'active' : ''}
                    title={label}
                    aria-label={label}
                    aria-pressed={view === k}
                    onClick={() => setView(k)}
                  >
                    <Icon name={icon} size={16} />
                  </button>
                ))}
              </div>
            </>
          )}
          <button className="btn btn-primary" onClick={() => setCreating(true)}>
            + 建立專案
          </button>
        </div>
      </div>
      {projects.isLoading && (
        <div className="project-grid">
          <div className="project-card"><Skeleton lines={2} /></div>
          <div className="project-card"><Skeleton lines={2} /></div>
        </div>
      )}
      {projects.error && <div className="error-box">{errorText(projects.error)}</div>}
      {projects.data?.length === 0 && (
        <Empty title="還沒有專案">
          <p>建立第一個專案，開始撰寫系統分析文件。</p>
          <button className="btn btn-primary" onClick={() => setCreating(true)}>
            + 建立專案
          </button>
        </Empty>
      )}
      {hasProjects && shown.length === 0 && <p className="muted">找不到符合「{query.trim()}」的專案。</p>}
      {view === 'grid' ? (
        <div className="project-grid">
          {shown.map((p) => (
            <ProjectItem key={p.id} project={p} layout="card" />
          ))}
        </div>
      ) : (
        shown.length > 0 && (
          <div className="project-list">
            {shown.map((p) => (
              <ProjectItem key={p.id} project={p} layout="row" />
            ))}
          </div>
        )
      )}
      {creating && <CreateProjectDialog onClose={() => setCreating(false)} />}
    </div>
  )
}

/** 一個專案：卡片或列表的一列。整塊都可以點進專案，右上角的「⋯」是其他操作 */
function ProjectItem({ project: p, layout }: { project: Project; layout: 'card' | 'row' }) {
  const [newSpec, setNewSpec] = useState(false)
  const meta = `${p.spec_count} 份 Spec・更新於 ${relativeTime(p.updated_at)}`
  const head = (
    <>
      <Link to={`/p/${p.id}`} className="project-name project-link">
        {p.name}
      </Link>
      {p.code && <span className="project-code">{p.code}</span>}
      <RunBadge project={p} />
    </>
  )
  const menu = (
    <details className="menu project-menu">
      <summary className="icon-btn" title="更多" aria-label={`「${p.name}」的其他操作`}>
        <Icon name="more" size={16} />
      </summary>
      <div className="menu-list menu-right menu-narrow">
        <button disabled={p.phases.length === 0} onClick={() => setNewSpec(true)}>
          新增 Spec
        </button>
        <Link to={`/p/${p.id}/baselines`}>基準版本</Link>
        <Link to={`/p/${p.id}/runs`}>AI 工作紀錄</Link>
      </div>
    </details>
  )
  return (
    <div className={layout === 'card' ? 'project-card' : 'project-row'}>
      {layout === 'card' ? (
        <>
          <div className="project-card-head">
            {head}
            {menu}
          </div>
          <p className="project-desc">{p.description || '（沒有說明）'}</p>
          <div className="project-card-foot">
            <PhaseStrip phases={p.phases} />
            <div className="muted small">{meta}</div>
          </div>
        </>
      ) : (
        <>
          <div className="project-row-main">
            <div className="project-card-head">{head}</div>
            <div className="project-row-desc muted small">{p.description}</div>
          </div>
          <PhaseStrip phases={p.phases} />
          <div className="muted small project-row-meta">{meta}</div>
          {menu}
        </>
      )}
      {newSpec && (
        <NewSpecDialog projectId={p.id} phases={p.phases} phaseId={null} onClose={() => setNewSpec(false)} />
      )}
    </div>
  )
}

/** 有 AI 工作在跑或排隊時才顯示 */
function RunBadge({ project: p }: { project: Project }) {
  if (p.running_runs > 0) return <Badge tone="amber">AI 撰寫中</Badge>
  if (p.queued_runs > 0) return <Badge>AI 排隊中</Badge>
  return null
}

/** 一個階段一格，有 Spec 的階段上色。滑鼠移到格子上顯示階段名稱和數量 */
function PhaseStrip({ phases }: { phases: PhaseSummary[] }) {
  if (phases.length === 0) return <div className="phase-strip" />
  const label = phases.map((ph) => `${ph.name} ${ph.spec_count}`).join('・')
  return (
    <div className="phase-strip" role="img" aria-label={`各階段的 Spec 數量：${label}`}>
      {phases.map((ph) => (
        <span key={ph.id} className={ph.spec_count > 0 ? 'filled' : ''} title={`${ph.name}：${ph.spec_count} 份`} />
      ))}
    </div>
  )
}

function CreateProjectDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const toast = useToast()
  const [name, setName] = useState('')
  const [code, setCode] = useState('')
  const [description, setDescription] = useState('')
  const [phases, setPhases] = useState('需求分析\n系統設計\n詳細設計')
  const create = useMutation({
    mutationFn: () =>
      api.createProject({
        name,
        code,
        description,
        phases: phases.split('\n').map((s) => s.trim()).filter(Boolean),
      }),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: keys.projects })
      onClose()
      navigate(`/p/${p.id}`)
    },
    onError: (e) => toast(errorText(e), 'error'),
  })
  return (
    <Modal
      title="建立專案"
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
      <Field label="專案名稱">
        <input className="input" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：員工請假系統" />
      </Field>
      <Field label="專案代碼（選填）" hint="簡短的英文代號，例如 HR、ORD">
        <input className="input" value={code} maxLength={40} onChange={(e) => setCode(e.target.value)} />
      </Field>
      <Field label="說明（選填）" hint="AI 撰寫時會參考這段說明">
        <textarea className="input textarea" rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
      </Field>
      <Field label="功能階段" hint="一行一個，之後還可以新增、改名、調整順序">
        <textarea className="input textarea" rows={4} value={phases} onChange={(e) => setPhases(e.target.value)} />
      </Field>
    </Modal>
  )
}
