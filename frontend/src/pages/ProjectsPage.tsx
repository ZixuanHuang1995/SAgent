// 首頁：專案清單、建立專案

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { api } from '../api'
import { relativeTime } from '../format'
import { keys, useProjects } from '../queries'
import { Empty, errorText, Field, Modal, Skeleton, useToast } from '../components/ui'

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
          <Link key={p.id} to={`/p/${p.id}`} className="project-card">
            <div className="project-card-head">
              <span className="project-name">{p.name}</span>
              {p.code && <span className="project-code">{p.code}</span>}
            </div>
            <p className="project-desc">{p.description || '（沒有說明）'}</p>
            <div className="project-meta muted small">
              {p.phase_count} 個階段・{p.spec_count} 份 Spec・更新於 {relativeTime(p.updated_at)}
            </div>
          </Link>
        ))}
      </div>
      {creating && <CreateProjectDialog onClose={() => setCreating(false)} />}
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
