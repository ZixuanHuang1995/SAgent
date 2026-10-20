// 首頁：專案清單、建立專案

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { api, type Project } from '../api'
import { relativeTime } from '../format'
import { keys, useProjects } from '../queries'
import { Empty, errorText, Field, Modal, Skeleton, useToast } from '../components/ui'
import { NewSpecDialog } from './ProjectLayout'

export function ProjectsPage() {
  const projects = useProjects()
  const [creating, setCreating] = useState(false)
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>專案</h1>
          <p className="muted">每個專案底下依「功能階段」整理 Spec，每份 Spec 都有完整的版本歷程。</p>
        </div>
        <button className="btn btn-primary" onClick={() => setCreating(true)}>
          + 建立專案
        </button>
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
      <div className="project-grid">
        {projects.data?.map((p) => (
          <ProjectCard key={p.id} project={p} />
        ))}
      </div>
      {creating && <CreateProjectDialog onClose={() => setCreating(false)} />}
    </div>
  )
}

function ProjectCard({ project: p }: { project: Project }) {
  const [newSpec, setNewSpec] = useState(false)
  const filled = p.phases.filter((ph) => ph.spec_count > 0).length
  const percent = p.phases.length ? Math.round((filled / p.phases.length) * 100) : 0
  return (
    <div className="project-card">
      <Link to={`/p/${p.id}`} className="project-card-main">
        <div className="project-card-head">
          <span className="project-avatar" aria-hidden="true">
            {(p.code || p.name).slice(0, 2).toUpperCase()}
          </span>
          <span className="project-name">{p.name}</span>
        </div>
        <p className="project-desc">{p.description || '（沒有說明）'}</p>
      </Link>
      {p.phases.length > 0 && (
        <>
          <div className="phase-chips">
            {p.phases.map((ph) => (
              <span key={ph.id} className={`phase-chip${ph.spec_count > 0 ? ' phase-chip-filled' : ''}`}>
                {ph.name} {ph.spec_count}
              </span>
            ))}
          </div>
          <div
            className="progress"
            role="progressbar"
            aria-label="有內容的階段"
            aria-valuenow={filled}
            aria-valuemin={0}
            aria-valuemax={p.phases.length}
          >
            <span style={{ width: `${percent}%` }} />
          </div>
        </>
      )}
      <div className="project-meta muted small">
        {filled}/{p.phases.length} 個階段有內容・{p.spec_count} 份 Spec・更新於 {relativeTime(p.updated_at)}
      </div>
      <div className="project-actions">
        <Link to={`/p/${p.id}`} className="btn btn-sm">
          開啟
        </Link>
        <button className="btn btn-sm" onClick={() => setNewSpec(true)} disabled={p.phases.length === 0}>
          + 新增 Spec
        </button>
      </div>
      {newSpec && (
        <NewSpecDialog projectId={p.id} phases={p.phases} phaseId={null} onClose={() => setNewSpec(false)} />
      )}
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
