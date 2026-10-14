// 專案頁面的外框：左邊是「階段 → Spec」的樹狀清單

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate, useParams } from 'react-router'
import { QuickSwitcher } from '../components/QuickSwitcher'
import { useMediaQuery, usePersistent } from '../hooks/layout'
import { api, type Phase, type Spec } from '../api'
import { SPEC_STATUS } from '../format'
import { keys, useProjectTree, useTemplates } from '../queries'
import { ConfirmDialog, Empty, errorText, Field, Modal, Skeleton, Spinner, useToast } from '../components/ui'
import { Icon, type IconName } from '../components/icons'

const PROJECT_NAV: { to: string; icon: IconName; label: string }[] = [
  { to: '', icon: 'overview', label: '專案總覽' },
  { to: '/baselines', icon: 'tag', label: '基準版本' },
  { to: '/runs', icon: 'bot', label: 'AI 工作紀錄' },
]

export function ProjectLayout() {
  const projectId = Number(useParams().projectId)
  const tree = useProjectTree(projectId)
  const [showArchived, setShowArchived] = useState(false)
  // 寬螢幕：可以收合成一排圖示（記住選擇）；窄螢幕：平常收合，展開時蓋在內容上面
  const narrow = useMediaQuery('(max-width: 800px)')
  const [collapsed, setCollapsed] = usePersistent<'0' | '1'>('sagent:sidebar-collapsed', '0')
  const [drawerOpen, setDrawerOpen] = useState(false)
  const location = useLocation()
  useEffect(() => setDrawerOpen(false), [location.pathname, narrow])
  // Ctrl+K（Mac 是 ⌘K）快速切換
  const [switcher, setSwitcher] = useState(false)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setSwitcher(true)
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [])

  if (tree.isLoading)
    return (
      <div className="workspace">
        <aside className={`sidebar ${narrow || collapsed === '1' ? 'is-collapsed' : ''}`}>
          {!(narrow || collapsed === '1') && (
            <div className="sidebar-inner">
              <Skeleton lines={6} title={false} />
            </div>
          )}
        </aside>
        <section className="workspace-main">
          <div className="page">
            <Skeleton lines={3} />
          </div>
        </section>
      </div>
    )
  if (tree.error) return <div className="page error-box">{errorText(tree.error)}</div>
  const data = tree.data!
  const expanded = narrow ? drawerOpen : collapsed === '0'
  const expand = () => (narrow ? setDrawerOpen(true) : setCollapsed('0'))
  const collapse = () => (narrow ? setDrawerOpen(false) : setCollapsed('1'))

  return (
    <div className="workspace">
      <aside className={`sidebar ${expanded ? '' : 'is-collapsed'} ${narrow && expanded ? 'is-drawer' : ''}`}>
        {expanded ? (
          <ProjectSidebar
            projectId={projectId}
            phases={data.phases}
            specs={data.specs}
            onShowArchived={() => setShowArchived(true)}
            onCollapse={collapse}
            onOpenSwitcher={() => setSwitcher(true)}
          />
        ) : (
          <nav className="sidebar-rail">
            <button className="icon-btn rail-btn" title="展開側欄" aria-label="展開側欄" onClick={expand}>
              <Icon name="menu" size={18} />
            </button>
            {PROJECT_NAV.map((n) => (
              <NavLink key={n.to} end={n.to === ''} to={`/p/${projectId}${n.to}`} className="rail-btn" title={n.label} aria-label={n.label}>
                <Icon name={n.icon} size={18} />
              </NavLink>
            ))}
          </nav>
        )}
      </aside>
      {narrow && drawerOpen && (
        <>
          <div className="sidebar-spacer" />
          <div className="sidebar-backdrop" onClick={() => setDrawerOpen(false)} />
        </>
      )}
      <section className="workspace-main">
        <Outlet />
      </section>
      {showArchived && <ArchivedDialog projectId={projectId} onClose={() => setShowArchived(false)} />}
      {switcher && <QuickSwitcher projectId={projectId} phases={data.phases} specs={data.specs} onClose={() => setSwitcher(false)} />}
    </div>
  )
}

function ProjectSidebar(props: {
  projectId: number
  phases: Phase[]
  specs: Spec[]
  onShowArchived: () => void
  onCollapse: () => void
  onOpenSwitcher: () => void
}) {
  const { projectId, phases, specs } = props
  const [q, setQ] = useState('')
  const [debounced, setDebounced] = useState('')
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set())
  const [newSpecPhase, setNewSpecPhase] = useState<number | null>(null)
  const [phaseDialog, setPhaseDialog] = useState<Phase | 'new' | null>(null)
  const [deletingPhase, setDeletingPhase] = useState<Phase | null>(null)
  const qc = useQueryClient()
  const toast = useToast()

  useEffect(() => {
    const t = setTimeout(() => setDebounced(q.trim()), 300)
    return () => clearTimeout(t)
  }, [q])
  const search = useQuery({
    queryKey: ['search', projectId, debounced],
    queryFn: () => api.searchSpecs(projectId, debounced),
    enabled: debounced.length > 0,
  })

  const byPhase = useMemo(() => {
    const map = new Map<number, Spec[]>()
    for (const s of specs) map.set(s.phase_id, [...(map.get(s.phase_id) ?? []), s])
    return map
  }, [specs])

  const movePhase = async (phase: Phase, delta: number) => {
    const ids = phases.map((p) => p.id)
    const i = ids.indexOf(phase.id)
    const j = i + delta
    if (j < 0 || j >= ids.length) return
    ;[ids[i], ids[j]] = [ids[j], ids[i]]
    try {
      await api.reorderPhases(projectId, ids)
      qc.invalidateQueries({ queryKey: ['tree', projectId] })
    } catch (e) {
      toast(errorText(e), 'error')
    }
  }

  return (
    <div className="sidebar-inner">
      <nav className="sidebar-nav">
        {PROJECT_NAV.map((n, i) => (
          <div key={n.to} className="sidebar-nav-row">
            <NavLink end={n.to === ''} to={`/p/${projectId}${n.to}`}>
              <Icon name={n.icon} /> {n.label}
            </NavLink>
            {i === 0 && (
              <button className="icon-btn" title="收合側欄" aria-label="收合側欄" onClick={props.onCollapse}>
                <Icon name="collapse" />
              </button>
            )}
          </div>
        ))}
      </nav>

      <input className="input input-sm sidebar-search" placeholder="搜尋標題或內容…" value={q} onChange={(e) => setQ(e.target.value)} />

      {debounced ? (
        <div className="tree">
          <div className="tree-phase-head muted small">搜尋結果</div>
          {search.isLoading && <Spinner small />}
          {search.data?.length === 0 && <div className="muted small pad-sm">找不到符合的 Spec</div>}
          {search.data?.map((s) => <SpecLink key={s.id} projectId={projectId} spec={s} />)}
        </div>
      ) : (
        <div className="tree">
          {phases.length === 0 && <Empty title="還沒有階段">先新增一個功能階段。</Empty>}
          {phases.map((phase, idx) => {
            const list = byPhase.get(phase.id) ?? []
            const closed = collapsed.has(phase.id)
            return (
              <div key={phase.id} className="tree-phase">
                <div className="tree-phase-head">
                  <button
                    className="tree-toggle"
                    aria-expanded={!closed}
                    onClick={() =>
                      setCollapsed((s) => {
                        const n = new Set(s)
                        if (n.has(phase.id)) n.delete(phase.id)
                        else n.add(phase.id)
                        return n
                      })
                    }
                  >
                    {closed ? '▸' : '▾'} {phase.name} <span className="muted">({list.length})</span>
                  </button>
                  <span className="tree-actions">
                    <button
                      className="icon-btn"
                      title="在這個階段新增 Spec"
                      aria-label={`在「${phase.name}」新增 Spec`}
                      onClick={() => setNewSpecPhase(phase.id)}
                    >
                      <Icon name="plus" size={14} />
                    </button>
                    <details className="menu">
                      <summary className="icon-btn" title="更多" aria-label={`「${phase.name}」的其他操作`}>
                        <Icon name="more" size={14} />
                      </summary>
                      <div className="menu-list">
                        <button onClick={() => setPhaseDialog(phase)}>改名稱／說明</button>
                        <button disabled={idx === 0} onClick={() => movePhase(phase, -1)}>
                          往上移
                        </button>
                        <button disabled={idx === phases.length - 1} onClick={() => movePhase(phase, 1)}>
                          往下移
                        </button>
                        <button className="danger" onClick={() => setDeletingPhase(phase)}>
                          刪除階段
                        </button>
                      </div>
                    </details>
                  </span>
                </div>
                {!closed && (
                  <div className="tree-specs">
                    {list.length === 0 && <div className="tree-empty muted small">還沒有 Spec</div>}
                    {list.map((s) => (
                      <SpecLink key={s.id} projectId={projectId} spec={s} />
                    ))}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      <div className="sidebar-foot">
        <button className="btn btn-sm" onClick={() => setPhaseDialog('new')}>
          + 新增階段
        </button>
        <button className="link-btn small" onClick={props.onShowArchived}>
          已封存的 Spec
        </button>
        <button className="link-btn small muted-link" onClick={props.onOpenSwitcher} title="快速切換到其他 Spec">
          <kbd>Ctrl</kbd>+<kbd>K</kbd> 快速切換
        </button>
      </div>

      {newSpecPhase !== null && (
        <NewSpecDialog projectId={projectId} phases={phases} phaseId={newSpecPhase} onClose={() => setNewSpecPhase(null)} />
      )}
      {phaseDialog && <PhaseDialog projectId={projectId} phase={phaseDialog === 'new' ? null : phaseDialog} onClose={() => setPhaseDialog(null)} />}
      {deletingPhase && (
        <ConfirmDialog
          title={`刪除階段「${deletingPhase.name}」`}
          message="只有沒有任何 Spec（含已封存）的階段可以刪除。"
          confirmText="刪除"
          danger
          onConfirm={async () => {
            try {
              await api.deletePhase(deletingPhase.id)
              qc.invalidateQueries({ queryKey: ['tree', projectId] })
              toast('已刪除階段', 'success')
            } catch (e) {
              toast(errorText(e), 'error')
            }
          }}
          onClose={() => setDeletingPhase(null)}
        />
      )}
    </div>
  )
}

function SpecLink({ projectId, spec }: { projectId: number; spec: Spec }) {
  return (
    <NavLink to={`/p/${projectId}/s/${spec.id}`} className="tree-spec" title={spec.title}>
      <span className={`status-dot status-${spec.status}`} title={SPEC_STATUS[spec.status]} />
      <span className="tree-spec-title">{spec.title}</span>
      <span className="tree-spec-ver">{spec.head_version_no > 0 ? `v${spec.head_version_no}` : '—'}</span>
    </NavLink>
  )
}

export function NewSpecDialog(props: { projectId: number; phases: Phase[]; phaseId: number | null; onClose: () => void }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const toast = useToast()
  const templates = useTemplates()
  const [title, setTitle] = useState('')
  const [phaseId, setPhaseId] = useState(props.phaseId ?? props.phases[0]?.id)
  const [docType, setDocType] = useState('functional_spec')
  const [init, setInit] = useState<'template' | 'blank' | 'content' | 'ai'>('ai')
  const [content, setContent] = useState('')
  const [requirement, setRequirement] = useState('')

  const create = useMutation({
    mutationFn: async () => {
      const spec = await api.createSpec(props.projectId, {
        phase_id: phaseId!,
        title,
        doc_type: docType,
        init: init === 'ai' ? 'none' : init,
        content: init === 'content' ? content : undefined,
      })
      if (init === 'ai') {
        try {
          await api.createRun({ spec_id: spec.id, mode: 'draft', requirement, doc_type: docType })
        } catch (e) {
          toast(`Spec 已建立，但 AI 工作沒有啟動：${errorText(e)}`, 'error')
        }
      }
      return spec
    },
    onSuccess: (spec) => {
      qc.invalidateQueries({ queryKey: ['tree', props.projectId] })
      qc.invalidateQueries({ queryKey: keys.projects })
      props.onClose()
      navigate(`/p/${props.projectId}/s/${spec.id}`)
    },
    onError: (e) => toast(errorText(e), 'error'),
  })

  const template = templates.data?.find((t) => t.key === docType)
  const ready = title.trim() && phaseId && (init !== 'ai' || requirement.trim())

  return (
    <Modal
      title="新增 Spec"
      onClose={props.onClose}
      wide
      footer={
        <>
          <button className="btn" onClick={props.onClose}>
            取消
          </button>
          <button className="btn btn-primary" disabled={!ready || create.isPending} onClick={() => create.mutate()}>
            {init === 'ai' ? '建立並開始 AI 撰寫' : '建立'}
          </button>
        </>
      }
    >
      <div className="form-row">
        <Field label="標題">
          <input className="input" autoFocus value={title} onChange={(e) => setTitle(e.target.value)} placeholder="例如：請假申請功能" />
        </Field>
        <Field label="階段">
          <select className="input" value={phaseId} onChange={(e) => setPhaseId(Number(e.target.value))}>
            {props.phases.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <Field label="文件類型" hint={template?.description}>
        <select className="input" value={docType} onChange={(e) => setDocType(e.target.value)}>
          {templates.data?.map((t) => (
            <option key={t.key} value={t.key}>
              {t.name}
            </option>
          ))}
        </select>
      </Field>
      <Field label="內容來源">
        <div className="seg seg-wrap">
          {(
            [
              ['ai', '用 AI 撰寫'],
              ['template', '範本骨架'],
              ['blank', '空白文件'],
              ['content', '匯入 Markdown'],
            ] as const
          ).map(([k, label]) => (
            <button key={k} type="button" className={init === k ? 'active' : ''} aria-pressed={init === k} onClick={() => setInit(k)}>
              {label}
            </button>
          ))}
        </div>
      </Field>
      {init === 'ai' && (
        <Field label="需求內容" hint="可以貼會議紀錄、客戶需求、條列重點。AI 會依「文件類型」的章節規劃並撰寫。">
          <textarea className="input textarea" rows={8} value={requirement} onChange={(e) => setRequirement(e.target.value)} />
        </Field>
      )}
      {init === 'template' && template && (
        <div className="muted small">會建立以下章節：{template.sections.map((s) => s.heading).join('、')}</div>
      )}
      {init === 'content' && (
        <Field label="Markdown 內容" hint={<FileLoader onLoad={(text, name) => { setContent(text); if (!title) setTitle(name.replace(/\.(md|markdown|txt)$/i, '')) }} />}>
          <textarea className="input textarea mono" rows={10} value={content} onChange={(e) => setContent(e.target.value)} />
        </Field>
      )}
    </Modal>
  )
}

function FileLoader({ onLoad }: { onLoad: (text: string, name: string) => void }) {
  return (
    <label className="link-btn">
      從檔案載入（.md）
      <input
        type="file"
        accept=".md,.markdown,.txt"
        hidden
        onChange={async (e) => {
          const f = e.target.files?.[0]
          if (f) onLoad(await f.text(), f.name)
          e.target.value = ''
        }}
      />
    </label>
  )
}

function PhaseDialog({ projectId, phase, onClose }: { projectId: number; phase: Phase | null; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [name, setName] = useState(phase?.name ?? '')
  const [description, setDescription] = useState(phase?.description ?? '')
  const save = useMutation({
    mutationFn: () =>
      phase ? api.updatePhase(phase.id, { name, description }) : api.createPhase(projectId, { name, description }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tree', projectId] })
      onClose()
    },
    onError: (e) => toast(errorText(e), 'error'),
  })
  return (
    <Modal
      title={phase ? '編輯階段' : '新增階段'}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            取消
          </button>
          <button className="btn btn-primary" disabled={!name.trim() || save.isPending} onClick={() => save.mutate()}>
            儲存
          </button>
        </>
      }
    >
      <Field label="名稱" hint="例如：需求分析、系統設計、第二期">
        <input className="input" autoFocus value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field label="說明（選填）">
        <textarea className="input textarea" rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
      </Field>
    </Modal>
  )
}

function ArchivedDialog({ projectId, onClose }: { projectId: number; onClose: () => void }) {
  const tree = useProjectTree(projectId, true)
  const qc = useQueryClient()
  const toast = useToast()
  return (
    <Modal title="已封存的 Spec" onClose={onClose} wide>
      {tree.isLoading && <Spinner />}
      {tree.data?.specs.length === 0 && <div className="muted">沒有封存的 Spec。</div>}
      <ul className="plain-list">
        {tree.data?.specs.map((s) => (
          <li key={s.id} className="row gap">
            <Link to={`/p/${projectId}/s/${s.id}`} onClick={onClose}>
              {s.title}
            </Link>
            <span className="muted small">v{s.head_version_no}</span>
            <span className="spacer" />
            <button
              className="btn btn-sm"
              onClick={async () => {
                try {
                  await api.unarchiveSpec(s.id)
                  qc.invalidateQueries({ queryKey: ['tree', projectId] })
                  toast('已取消封存', 'success')
                } catch (e) {
                  toast(errorText(e), 'error')
                }
              }}
            >
              取消封存
            </button>
          </li>
        ))}
      </ul>
    </Modal>
  )
}
