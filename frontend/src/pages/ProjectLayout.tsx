// 專案頁面的外框：左邊是「階段 → Spec」的樹狀清單

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate, useParams } from 'react-router'
import { QuickSwitcher } from '../components/QuickSwitcher'
import { useMediaQuery, usePersistent, usePersistentNumber } from '../hooks/layout'
import { api, type Phase, type Spec } from '../api'
import { SPEC_STATUS } from '../format'
import { keys, useProjectTree, useTemplateCategories, useTemplates } from '../queries'
import {
  CategoryBadge,
  ConfirmDialog,
  Empty,
  errorText,
  Field,
  Menu,
  MenuItem,
  MenuSeparator,
  Modal,
  Skeleton,
  Spinner,
  Splitter,
  useToast,
} from '../components/ui'
import { Icon, type IconName } from '../components/icons'

const PROJECT_NAV: { to: string; icon: IconName; label: string }[] = [
  { to: '', icon: 'overview', label: '專案總覽' },
  { to: '/baselines', icon: 'tag', label: '基準版本' },
  { to: '/runs', icon: 'bot', label: 'AI 工作紀錄' },
]

// 左側欄寬度：可以拖拉調整，標題太長被截掉時拉寬一點
const SIDEBAR_DEFAULT = 270
const SIDEBAR_MIN = 220
const SIDEBAR_MAX = 440

export function ProjectLayout() {
  const projectId = Number(useParams().projectId)
  const tree = useProjectTree(projectId)
  const [showArchived, setShowArchived] = useState(false)
  // 寬螢幕：可以收合成一排圖示（記住選擇）；窄螢幕：平常收合，展開時蓋在內容上面
  const narrow = useMediaQuery('(max-width: 800px)')
  const [collapsed, setCollapsed] = usePersistent<'0' | '1'>('sagent:sidebar-collapsed', '0')
  const [sidebarWidth, setSidebarWidth] = usePersistentNumber('sagent:sidebar-width', SIDEBAR_DEFAULT, SIDEBAR_MIN, SIDEBAR_MAX)
  const sidebarStyle = { '--sidebar-w': `${sidebarWidth}px` } as CSSProperties
  const sidebarEl = useRef<HTMLElement>(null)
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
        <aside className={`sidebar ${narrow || collapsed === '1' ? 'is-collapsed' : ''}`} style={sidebarStyle}>
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
  const resizable = expanded && !narrow

  return (
    <div className="workspace">
      <aside
        ref={sidebarEl}
        className={`sidebar ${expanded ? '' : 'is-collapsed'} ${narrow && expanded ? 'is-drawer' : ''} ${resizable ? 'has-splitter' : ''}`}
        style={sidebarStyle}
      >
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
            {/* 窄螢幕的選單按鈕放最上面；寬螢幕的展開按鈕放底部，跟展開時的收合按鈕同一個位置 */}
            {narrow && (
              <button className="icon-btn rail-btn" title="展開側欄" aria-label="展開側欄" onClick={expand}>
                <Icon name="menu" size={18} />
              </button>
            )}
            {PROJECT_NAV.map((n) => (
              <NavLink key={n.to} end={n.to === ''} to={`/p/${projectId}${n.to}`} className="rail-btn" title={n.label} aria-label={n.label}>
                <Icon name={n.icon} size={18} />
              </NavLink>
            ))}
            {!narrow && (
              <button className="icon-btn rail-btn rail-expand" title="展開側欄" aria-label="展開側欄" onClick={expand}>
                <Icon name="expand" size={18} />
              </button>
            )}
          </nav>
        )}
      </aside>
      {resizable && (
        <Splitter
          label="調整左側欄的寬度"
          onMove={(x) => {
            const r = sidebarEl.current?.getBoundingClientRect()
            if (r) setSidebarWidth(x - r.left)
          }}
          onNudge={(d) => setSidebarWidth((w) => w + d * 20)}
          onReset={() => setSidebarWidth(SIDEBAR_DEFAULT)}
        />
      )}
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
        {PROJECT_NAV.map((n) => (
          <NavLink key={n.to} end={n.to === ''} to={`/p/${projectId}${n.to}`}>
            <Icon name={n.icon} /> {n.label}
          </NavLink>
        ))}
      </nav>

      <div className="sidebar-search">
        <input
          className="input input-sm"
          placeholder="搜尋標題或內容…"
          aria-label="搜尋標題或內容"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        {/* 快速切換的提示放在搜尋框裡，找 Spec 只有一個地方。Ctrl+K 在哪裡按都可以 */}
        {!q && (
          <button className="search-kbd" onClick={props.onOpenSwitcher} title="快速切換到其他 Spec（Ctrl+K）" aria-label="快速切換到其他 Spec">
            <kbd>Ctrl</kbd>
            <kbd>K</kbd>
          </button>
        )}
      </div>

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
                    <Menu
                      trigger={<Icon name="more" size={14} />}
                      label={`「${phase.name}」的其他操作`}
                      title="更多"
                      triggerClassName="icon-btn tree-menu-btn"
                    >
                      <MenuItem icon="edit" onSelect={() => setPhaseDialog(phase)}>
                        重新命名
                      </MenuItem>
                      <MenuSeparator />
                      <MenuItem icon="arrow-up" disabled={idx === 0} onSelect={() => movePhase(phase, -1)}>
                        往上移
                      </MenuItem>
                      <MenuItem icon="arrow-down" disabled={idx === phases.length - 1} onSelect={() => movePhase(phase, 1)}>
                        往下移
                      </MenuItem>
                      <MenuSeparator />
                      <MenuItem icon="trash" danger onSelect={() => setDeletingPhase(phase)}>
                        刪除階段…
                      </MenuItem>
                    </Menu>
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
          {/* 放在清單最後，新的階段就出現在這裡 */}
          <button className="tree-add" onClick={() => setPhaseDialog('new')}>
            <Icon name="plus" size={14} /> 新增階段
          </button>
        </div>
      )}

      {/* 底部只放不常用的：已封存、收合 */}
      <div className="sidebar-foot">
        <button className="icon-btn sidebar-archived" onClick={props.onShowArchived} title="查看已封存的 Spec，可以取消封存">
          <Icon name="archive" size={14} /> 已封存
        </button>
        {/* 收合後的展開按鈕也在底部，兩個位置一樣 */}
        <button className="icon-btn sidebar-collapse" title="收合側欄" aria-label="收合側欄" onClick={props.onCollapse}>
          <Icon name="collapse" />
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
  const templates = useTemplates()
  const categories = useTemplateCategories()
  // Functionality 是大多數，不標；其他類型（Concept、Technical、Algorithm、Change）在標題前加小標籤
  const category = templates.data?.find((t) => t.key === spec.doc_type)?.category
  const tag = category && category !== 'feature' ? categories.data?.find((c) => c.key === category)?.name : undefined
  return (
    <NavLink to={`/p/${projectId}/s/${spec.id}`} className="tree-spec" title={spec.title}>
      <span className={`status-dot status-${spec.status}`} title={SPEC_STATUS[spec.status]} />
      {tag && <span className={`tree-spec-cat cat-${category}`}>{tag}</span>}
      <span className="tree-spec-title">{spec.title}</span>
      {spec.review && spec.review.open > 0 && (
        <span className="tree-spec-review" title={`AI 審查還有 ${spec.review.open} 項沒處理`}>
          {spec.review.open}
        </span>
      )}
      <span className="tree-spec-ver">{spec.head_version_no > 0 ? `v${spec.head_version_no}` : '—'}</span>
    </NavLink>
  )
}

/** 從另一份 Spec 轉過來時（例如構想 → 功能規格）帶入的內容 */
export interface NewSpecPreset {
  category: string
  title: string
  requirement: string
  source: { id: number; title: string }
}

export function NewSpecDialog(props: {
  projectId: number
  phases: Phase[]
  phaseId: number | null
  preset?: NewSpecPreset
  onClose: () => void
}) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const toast = useToast()
  const templates = useTemplates()
  const categories = useTemplateCategories()
  const preset = props.preset
  // 第一步選類型，第二步填其他欄位。從其他 Spec 轉過來時直接到第二步
  const [category, setCategory] = useState<string | null>(preset?.category ?? null)
  const [title, setTitle] = useState(preset?.title ?? '')
  const [phaseId, setPhaseId] = useState(props.phaseId ?? props.phases[0]?.id)
  const [docType, setDocType] = useState('')
  const [init, setInit] = useState<'template' | 'blank' | 'content' | 'ai'>('ai')
  const [content, setContent] = useState('')
  const [requirement, setRequirement] = useState(preset?.requirement ?? '')

  const inCategory = (templates.data ?? []).filter((t) => t.category === category)
  // 換類型時，文件類型改成這個類型的第一個
  const template = inCategory.find((t) => t.key === docType) ?? inCategory[0]

  const create = useMutation({
    mutationFn: async () => {
      const spec = await api.createSpec(props.projectId, {
        phase_id: phaseId!,
        title,
        doc_type: template!.key,
        init: init === 'ai' ? 'none' : init,
        content: init === 'content' ? content : undefined,
        source_spec_id: preset?.source.id ?? null,
      })
      if (init === 'ai') {
        try {
          await api.createRun({ spec_id: spec.id, mode: 'draft', requirement, doc_type: template!.key })
        } catch (e) {
          toast(`Spec 已建立，但 AI 工作沒有啟動：${errorText(e)}`, 'error')
        }
      }
      return spec
    },
    onSuccess: (spec) => {
      qc.invalidateQueries({ queryKey: ['tree', props.projectId] })
      qc.invalidateQueries({ queryKey: keys.projects })
      if (preset) qc.invalidateQueries({ queryKey: keys.spec(preset.source.id) })
      props.onClose()
      navigate(`/p/${props.projectId}/s/${spec.id}`)
    },
    onError: (e) => toast(errorText(e), 'error'),
  })

  const ready = title.trim() && phaseId && template && (init !== 'ai' || requirement.trim())
  const current = categories.data?.find((c) => c.key === category)

  if (!category) {
    return (
      <Modal
        title="新增 Spec：先選類型"
        onClose={props.onClose}
        wide
        footer={
          <button className="btn" onClick={props.onClose}>
            取消
          </button>
        }
      >
        <p className="muted small mt-0">不同類型的文件，章節和寫法不一樣。選最接近你現在狀況的一種。</p>
        {categories.isLoading && <Spinner />}
        <div className="category-grid">
          {categories.data?.map((c) => {
            const names = (templates.data ?? []).filter((t) => t.category === c.key).map((t) => t.name)
            return (
              <button key={c.key} type="button" className="category-card" disabled={names.length === 0} onClick={() => setCategory(c.key)}>
                <span className="category-card-name">
                  <CategoryBadge category={c.key}>{c.name}</CategoryBadge>
                </span>
                <span className="category-card-desc">{c.description}</span>
                <span className="category-card-docs muted small">{names.length ? names.join('、') : '沒有這個類型的文件範本'}</span>
              </button>
            )
          })}
        </div>
      </Modal>
    )
  }

  return (
    <Modal
      title={
        <>
          新增 Spec <CategoryBadge category={category}>{current?.name ?? category}</CategoryBadge>
        </>
      }
      onClose={props.onClose}
      wide
      footer={
        <>
          {!preset && (
            <button className="btn" onClick={() => setCategory(null)}>
              ← 換類型
            </button>
          )}
          <span className="spacer" />
          <button className="btn" onClick={props.onClose}>
            取消
          </button>
          <button className="btn btn-primary" disabled={!ready || create.isPending} onClick={() => create.mutate()}>
            {init === 'ai' ? '建立並開始 AI 撰寫' : '建立'}
          </button>
        </>
      }
    >
      {preset && (
        <p className="muted small mt-0">
          從「{preset.source.title}」轉過來。需求內容已經帶入那份文件目前的內容，新的 Spec 會記錄來源。
        </p>
      )}
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
        <select className="input" value={template?.key ?? ''} onChange={(e) => setDocType(e.target.value)}>
          {inCategory.map((t) => (
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
        <Field
          label="需求內容"
          hint={
            category === 'idea'
              ? '寫下你的想法就好，幾句話也可以：想解決什麼問題、給誰用、大概想怎麼做。AI 會整理成構想提案，包含使用者故事和怎樣算成功，不清楚的地方列成待確認事項。'
              : template?.style === 'decision'
                ? '寫清楚要做什麼決定、有哪些限制、用什麼標準比較，已經想到的方案也可以列出來（例如：「搜尋功能要用資料庫全文檢索還是另外架搜尋引擎，三個月內上線，只有兩個人維護」）。AI 會補充方案、做比較表並提出建議。'
              : template?.style === 'experiment'
                ? '寫清楚要評估的工具或做法、想知道的事、可以參與的人和時間、怎樣算成功（例如：「評估 AI 程式碼審查工具，三個團隊參加 8 週，PR 前置時間要縮短 20% 以上」）。AI 會設計假設、指標、分組、流程和分析方法。'
                : category === 'algorithm'
                ? '寫清楚要解決的問題、輸入和輸出、限制條件和效能要求（例如：「解 9×9 數獨，題目用 81 個字元表示，0 是空格，每題要在 1 秒內解完」）。AI 會設計解法、寫虛擬碼、分析複雜度並列出測試案例。'
                :'可以貼會議紀錄、客戶需求、條列重點。AI 會依「文件類型」的章節規劃並撰寫。'
          }
        >
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
