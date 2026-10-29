// Spec 工作區：編輯 / 預覽、存檔（每次存檔都是新版本）、AI 代理、版本歷程、大綱

import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { Link, useBlocker, useParams } from 'react-router'
import { api, ApiError, type SpecDetail, type SpecStatus } from '../api'
import { AgentPanel } from '../components/AgentPanel'
import { DiagramMenu, type DiagramLang } from '../components/DiagramMenu'
import { DiffModal } from '../components/DiffView'
import { MarkdownEditor, type EditorHandle } from '../components/Editor'
import { MarkdownView } from '../components/MarkdownView'
import { ReviewChecklist } from '../components/ReviewChecklist'
import { VersionPanel } from '../components/VersionPanel'
import { Badge, CategoryBadge, ConfirmDialog, Empty, errorText, Menu, MenuItem, MenuSeparator, Popover, Skeleton, Spinner, Splitter, useToast } from '../components/ui'
import { useElementWidth, useMediaQuery, usePersistent, usePersistentNumber } from '../hooks/layout'
import { AI_STATUS, extractOutline, slugify, SPEC_STATUS, VERSION_SOURCE } from '../format'
import { keys, useProjectTree, useSpec, useTemplateCategories, useTemplates } from '../queries'
import { NewSpecDialog } from './ProjectLayout'
import { Icon, type IconName } from '../components/icons'

type ViewMode = 'edit' | 'split' | 'preview'

const VIEW_MODES: { key: ViewMode; icon: IconName; label: string }[] = [
  { key: 'edit', icon: 'edit', label: '編輯' },
  { key: 'split', icon: 'columns', label: '左右分割（編輯和預覽）' },
  { key: 'preview', icon: 'eye', label: '預覽' },
]

/** 跟後端存檔時的整理方式一樣：統一換行符號、去掉結尾空白、最後留一個換行 */
function normalizeContent(text: string): string {
  const t = text.replace(/\r\n?/g, '\n').trimEnd()
  return t ? `${t}\n` : ''
}

type PanelTab = 'agent' | 'review' | 'versions' | 'outline'

// 欄寬設定
const STATUS_TONE: Record<SpecStatus, 'neutral' | 'amber' | 'green' | 'red'> = {
  draft: 'neutral',
  reviewing: 'amber',
  approved: 'green',
  deprecated: 'red',
}

const PANEL_TABS: { key: PanelTab; icon: IconName; label: string }[] = [
  { key: 'agent', icon: 'bot', label: 'AI 代理' },
  // 只有 AI 審查列出項目的文件才有這個分頁
  { key: 'review', icon: 'review', label: '審查' },
  { key: 'versions', icon: 'history', label: '版本' },
  { key: 'outline', icon: 'list', label: '大綱' },
]

const PANEL_DEFAULT = 360
const PANEL_MIN = 300
const PANEL_MAX = 720
const EDITOR_DEFAULT = 50 // 分割模式下編輯區佔的百分比
/** 文件區至少要這麼寬才能左右分割；不夠寬時只能切換編輯或預覽。1366 寬的筆電開著左右側欄也要能分割 */
const SPLIT_MIN_WIDTH = 680

export function SpecPage() {
  const params = useParams()
  const projectId = Number(params.projectId)
  const specId = Number(params.specId)
  const spec = useSpec(specId)
  const tree = useProjectTree(projectId)
  const templates = useTemplates()
  const categories = useTemplateCategories()
  const qc = useQueryClient()
  const toast = useToast()

  // draft 是 null 表示沒有修改，直接顯示最新版內容
  const [draft, setDraft] = useState<string | null>(null)
  const draftRef = useRef(draft)
  draftRef.current = draft
  const [baseNo, setBaseNo] = useState<number | null>(null)
  const [message, setMessage] = useState('')
  const [saving, setSaving] = useState(false)
  const [conflict, setConflict] = useState<string | null>(null)
  const [diff, setDiff] = useState<{ from?: number; to?: number } | null>(null)
  // 構想 → 功能規格：開新增 Spec 對話框，帶入這份文件的內容
  const [converting, setConverting] = useState(false)
  const [savedMode, setMode] = usePersistent<ViewMode>('sagent:view', window.innerWidth > 1280 ? 'split' : 'preview')
  const [savedTab, setTab] = usePersistent<PanelTab>('sagent:panel', 'agent')
  const [panelOpen, setPanelOpen] = useState(true)
  const [panelWidth, setPanelWidth] = usePersistentNumber('sagent:panel-width', PANEL_DEFAULT, PANEL_MIN, PANEL_MAX)
  const [editorPct, setEditorPct] = usePersistentNumber('sagent:editor-pct', EDITOR_DEFAULT, 20, 80)
  const narrow = useMediaQuery('(max-width: 800px)')
  const [docWidth, measureDoc] = useElementWidth<HTMLDivElement>()
  const docEl = useRef<HTMLDivElement | null>(null)
  const setDocEl = useCallback(
    (el: HTMLDivElement | null) => {
      docEl.current = el
      measureDoc(el)
    },
    [measureDoc],
  )
  const bodyRef = useRef<HTMLDivElement>(null)
  // 文件區太窄時不能分割，暫時改用編輯模式（不改使用者記住的選擇，變寬後自動回到分割）
  const canSplit = !narrow && (docWidth === 0 || docWidth >= SPLIT_MIN_WIDTH)
  const mode: ViewMode = savedMode === 'split' && !canSplit ? 'edit' : savedMode
  const [archiving, setArchiving] = useState(false)
  const [editingTitle, setEditingTitle] = useState(false)
  const editor = useRef<EditorHandle>(null)
  // 預覽模式下插入圖表時，要等編輯器出現後才能插入
  const [pendingInsert, setPendingInsert] = useState<{ text: string; select: [number, number] } | null>(null)

  const data = spec.data
  const content = draft ?? data?.content ?? ''
  const review = data?.review ?? null
  const panelTabs = PANEL_TABS.filter((t) => t.key !== 'review' || review)
  const tab: PanelTab = savedTab === 'review' && !review ? 'agent' : savedTab
  const openReview = () => {
    setPanelOpen(true)
    setTab('review')
  }
  const dirty = draft !== null && draft !== data?.content
  const headChanged = dirty && baseNo !== null && data !== undefined && data.head_version_no !== baseNo

  const onChange = (value: string) => {
    if (baseNo === null) setBaseNo(data?.head_version_no ?? 0)
    setDraft(value)
  }

  const refreshAll = useCallback(() => {
    qc.invalidateQueries({ queryKey: keys.spec(specId) })
    qc.invalidateQueries({ queryKey: keys.versions(specId) })
    qc.invalidateQueries({ queryKey: ['tree', projectId] })
  }, [qc, specId, projectId])

  const save = useCallback(
    async (force = false) => {
      if (draft === null || !data) return
      const saved = draft
      setSaving(true)
      try {
        const r = await api.saveContent(specId, {
          content: saved,
          message,
          base_version_no: force ? data.head_version_no : baseNo,
        })
        toast(r.created ? `已存成 v${r.version.version_no}` : '內容沒有變化，不用存新版本', r.created ? 'success' : 'info')
        qc.setQueryData<SpecDetail>(keys.spec(specId), (old) =>
          old ? { ...old, ...r.spec, content: r.created ? normalizeContent(saved) : old.content, head: r.version } : old,
        )
        // 存檔期間如果又繼續打字，保留新打的內容，並改成以剛存的版本為基準
        if (draftRef.current !== null && draftRef.current !== saved) {
          setBaseNo(r.version.version_no)
        } else {
          setDraft(null)
          setBaseNo(null)
        }
        setMessage('')
        setConflict(null)
        refreshAll()
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) {
          setConflict(e.message)
          refreshAll() // 取得最新版本，提示列的按鈕才會用最新的版本號
        } else toast(errorText(e), 'error')
      } finally {
        setSaving(false)
      }
    },
    [draft, data, specId, message, baseNo, qc, toast, refreshAll],
  )

  // Ctrl+S 存檔
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault()
        if (dirty && !saving) save()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [dirty, saving, save])

  // 有未存檔的修改時，離開頁面要確認
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname)
  // 確認離開後 ConfirmDialog 還會呼叫 onClose，這時的 blocker 還是舊的，不能再 reset
  const leaving = useRef(false)
  useEffect(() => {
    if (!dirty) return
    const onBeforeUnload = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [dirty])

  useEffect(() => {
    if (pendingInsert === null) return
    // 剛從預覽切到編輯時，CodeMirror 要等一下才建立好
    let tries = 0
    const timer = setInterval(() => {
      if (editor.current?.insertBlock(pendingInsert.text, pendingInsert.select) || ++tries > 40) {
        clearInterval(timer)
        setPendingInsert(null)
      }
    }, 50)
    return () => clearInterval(timer)
  }, [pendingInsert])

  const insertDiagram = (title: string, lang: DiagramLang, code: string) => {
    if (mode === 'preview') setMode('split')
    if (window.innerWidth <= 800) setPanelOpen(false) // 窄螢幕時側欄會蓋住編輯器
    const fence = '```'
    const caption = '**圖：'
    // 插入後選取圖名，直接打字就能改名
    setPendingInsert({
      text: `${caption}${title}**\n\n${fence}${lang}\n${code}\n${fence}`,
      select: [caption.length, caption.length + title.length],
    })
  }

  const outline = useMemo(() => extractOutline(content), [content])
  const phases = tree.data?.phases ?? []
  const template = templates.data?.find((t) => t.key === data?.doc_type)
  const category = categories.data?.find((c) => c.key === template?.category)

  const updateMeta = async (body: { title?: string; phase_id?: number; status?: SpecStatus }) => {
    try {
      await api.updateSpec(specId, body)
      refreshAll()
    } catch (e) {
      toast(errorText(e), 'error')
    }
  }

  // ---- 編輯區和預覽區同步捲動、大綱標示目前位置 ----
  const previewEl = useRef<HTMLDivElement>(null)
  const [activeHeading, setActiveHeading] = useState(0)
  // 程式自己設定 scrollTop 也會觸發 scroll 事件，這段時間內不要反過來同步
  const ignoreUntil = useRef({ editor: 0, preview: 0 })
  const scrollFrame = useRef(0)

  const previewHeadings = () => [...(previewEl.current?.querySelectorAll<HTMLElement>('.markdown h1, .markdown h2, .markdown h3') ?? [])]

  /** 每個標題在編輯區、預覽區的位置，配成一對一對，捲動時照比例對齊 */
  const scrollAnchors = () => {
    const pane = previewEl.current
    const ed = editor.current
    const scroller = ed?.scroller()
    if (!pane || !ed || !scroller) return null
    const headings = previewHeadings()
    const paneTop = pane.getBoundingClientRect().top - pane.scrollTop
    const pts: [number, number][] = [[0, 0]]
    outline.forEach((item, i) => {
      const e = ed.lineTop(item.line)
      const h = headings[i]
      if (e === null || !h) return
      const p = h.getBoundingClientRect().top - paneTop
      const [le, lp] = pts[pts.length - 1]
      if (e > le && p > lp) pts.push([e, p])
    })
    const eMax = scroller.scrollHeight - scroller.clientHeight
    const pMax = pane.scrollHeight - pane.clientHeight
    const [le, lp] = pts[pts.length - 1]
    if (eMax > le && pMax > lp) pts.push([eMax, pMax])
    return { pts, scroller, pane }
  }

  const mapScroll = (pts: [number, number][], x: number, from: 0 | 1) => {
    const to = from === 0 ? 1 : 0
    for (let i = 0; i < pts.length - 1; i++) {
      const a = pts[i]
      const b = pts[i + 1]
      if (x <= b[from]) return a[to] + ((x - a[from]) / (b[from] - a[from] || 1)) * (b[to] - a[to])
    }
    return pts[pts.length - 1][to]
  }

  const updateActiveHeading = (source: 'editor' | 'preview') => {
    // 還沒捲到任何標題時（例如在最上面），算在第一個標題
    let idx = outline.length > 0 ? 0 : -1
    if (source === 'editor') {
      const top = editor.current?.topLine() ?? 1
      outline.forEach((item, i) => {
        if (item.line <= top + 1) idx = i
      })
    } else {
      const pane = previewEl.current
      if (!pane) return
      const paneTop = pane.getBoundingClientRect().top
      previewHeadings().forEach((h, i) => {
        if (h.getBoundingClientRect().top - paneTop <= 12) idx = i
      })
    }
    setActiveHeading(idx)
  }

  const onPaneScroll = (source: 'editor' | 'preview') => {
    cancelAnimationFrame(scrollFrame.current)
    scrollFrame.current = requestAnimationFrame(() => {
      updateActiveHeading(source)
      if (mode !== 'split' || Date.now() < ignoreUntil.current[source]) return
      const a = scrollAnchors()
      if (!a) return
      const [from, target] = source === 'editor' ? [a.scroller, a.pane] : [a.pane, a.scroller]
      const y = mapScroll(a.pts, from.scrollTop, source === 'editor' ? 0 : 1)
      if (Math.abs(target.scrollTop - y) < 2) return
      ignoreUntil.current[source === 'editor' ? 'preview' : 'editor'] = Date.now() + 120
      target.scrollTop = y
    })
  }

  const jumpTo = (line: number, text: string) => {
    // 兩邊各自捲到標題，這段時間不要互相同步
    ignoreUntil.current = { editor: Date.now() + 800, preview: Date.now() + 800 }
    if (mode !== 'preview') editor.current?.scrollToLine(line)
    if (mode !== 'edit') {
      const clean = text.replace(/[*_`]/g, '').replace(/\[(.*?)\]\(.*?\)/g, '$1')
      document.getElementById(slugify(clean))?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }

  // 審查意見裡的章節名稱 → 跳到文件裡對應的標題（比對時忽略編號和粗體符號）
  const jumpToHeading = (heading: string) => {
    const norm = (s: string) => s.replace(/[*_`]/g, '').replace(/^[\d.、\s]+/, '').trim()
    const target = norm(heading)
    const item = outline.find((o) => norm(o.text) === target) ?? outline.find((o) => norm(o.text).includes(target))
    if (!item) {
      toast('文件裡找不到這一節，可能已經改名', 'info')
      return
    }
    if (narrow) {
      // 窄螢幕時側欄蓋住整個文件，先收起來
      setPanelOpen(false)
      setTimeout(() => jumpTo(item.line, item.text), 50)
    } else jumpTo(item.line, item.text)
  }

  if (spec.isLoading)
    return (
      <div className="spec-page">
        <div className="spec-head">
          <Skeleton lines={1} />
        </div>
        <div className="pad">
          <Skeleton lines={8} />
        </div>
      </div>
    )
  if (spec.error || !data) return <div className="page error-box">{errorText(spec.error)}</div>

  return (
    <div className="spec-page">
      <div className="spec-head">
        <div className="spec-title-row">
          {editingTitle ? (
            <input
              className="input title-input"
              autoFocus
              defaultValue={data.title}
              onBlur={(e) => {
                setEditingTitle(false)
                if (e.target.value.trim() && e.target.value !== data.title) updateMeta({ title: e.target.value })
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
                if (e.key === 'Escape') setEditingTitle(false)
              }}
            />
          ) : (
            <h1 className="spec-title" title="點一下修改標題" onClick={() => setEditingTitle(true)}>
              {data.title}
            </h1>
          )}
          <Badge tone="blue" title={data.head ? `${VERSION_SOURCE[data.head.source]}：${data.head.message}` : ''}>
            {data.head_version_no > 0 ? `v${data.head_version_no}` : '尚無版本'}
          </Badge>
          <Badge tone={STATUS_TONE[data.status]} title="文件狀態，可以在下方「屬性」修改">
            {SPEC_STATUS[data.status]}
          </Badge>
          {data.head?.label && <Badge tone="green"><Icon name="tag" size={12} /> {data.head.label}</Badge>}
          {dirty && <Badge tone="amber">未儲存</Badge>}
          {/* AI 工作進行中時，上一次審查的數字已經不準，改顯示 AI 狀態 */}
          {data.ai_status ? (
            <span className={`badge badge-ai ai-${data.ai_status}`}>{AI_STATUS[data.ai_status]}</span>
          ) : review && review.open > 0 && (
            <button
              className="badge badge-amber badge-btn"
              title={`最近一次 AI 審查還有 ${review.open} 項沒處理，按一下查看`}
              onClick={openReview}
            >
              <Icon name="review" size={12} /> 待確認 {review.open}
            </button>
          )}
          <span className="spacer" />
          {dirty && (
            <input
              className="input input-sm save-message"
              placeholder="變更說明（選填）"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && save()}
            />
          )}
          <button className="btn btn-primary" disabled={!dirty || saving || data.archived} onClick={() => save()} title="Ctrl+S">
            {saving ? <Spinner small /> : '儲存新版本'}
          </button>
          {dirty && (
            <button className="btn" onClick={() => { setDraft(null); setBaseNo(null) }} title="放棄未儲存的修改">
              放棄修改
            </button>
          )}
          <Menu trigger={<Icon name="more" />} label="這份 Spec 的其他操作" title="其他操作">
            {data.head_version_no > 0 && (
              <MenuItem icon="download" href={api.specExportUrl(specId)} download>
                下載 Markdown
              </MenuItem>
            )}
            {data.head_version_no > 1 && (
              <MenuItem icon="diff" onSelect={() => setDiff({})}>
                與前一版比較
              </MenuItem>
            )}
            {data.head_version_no > 0 && <MenuSeparator />}
            {data.archived ? (
              <MenuItem icon="restore" onSelect={async () => { await api.unarchiveSpec(specId); refreshAll() }}>
                取消封存
              </MenuItem>
            ) : (
              <MenuItem icon="archive" danger onSelect={() => setArchiving(true)}>
                封存這份 Spec…
              </MenuItem>
            )}
          </Menu>
        </div>
        <div className="spec-meta-row">
          <Popover
            trigger={
              <>
                <span className="muted">屬性</span> {phases.find((p) => p.id === data.phase_id)?.name ?? ''}・
                {category ? `${category.name}・` : ''}
                {template?.name ?? data.doc_type} <Icon name="chevron-down" size={14} />
              </>
            }
            label="修改階段、狀態"
            title="修改階段、狀態"
            triggerClassName="btn btn-sm meta-summary"
            className="props-menu"
          >
            <label className="field">
              <span className="field-label">階段</span>
              <select className="input input-sm" value={data.phase_id} onChange={(e) => updateMeta({ phase_id: Number(e.target.value) })}>
                {phases.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="field-label">狀態</span>
              <select className="input input-sm" value={data.status} onChange={(e) => updateMeta({ status: e.target.value as SpecStatus })}>
                {Object.entries(SPEC_STATUS).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
            <div className="field">
              <span className="field-label">文件類型</span>
              <span>
                {category && <CategoryBadge category={category.key}>{category.name}</CategoryBadge>} {template?.name ?? data.doc_type}
              </span>
              <span className="field-hint">建立後不能改。AI 撰寫時會照這個類型的範本規劃章節。</span>
            </div>
          </Popover>
          {(data.source_spec || data.derived_specs.length > 0) && (
            <span className="spec-links small">
              {data.source_spec && (
                <span>
                  <span className="muted">來源：</span>
                  <Link to={`/p/${projectId}/s/${data.source_spec.id}`}>{data.source_spec.title}</Link>
                </span>
              )}
              {data.derived_specs.length > 0 && (
                <span>
                  <span className="muted">已轉成：</span>
                  {data.derived_specs.map((d, i) => (
                    <span key={d.id}>
                      {i > 0 && '、'}
                      <Link to={`/p/${projectId}/s/${d.id}`}>{d.title}</Link>
                    </span>
                  ))}
                </span>
              )}
            </span>
          )}
          {template?.category === 'idea' && !data.archived && (
            <button
              className="btn btn-sm"
              disabled={data.head_version_no === 0 || !!data.active_run_id}
              title={
                data.head_version_no === 0
                  ? '構想文件還沒有內容'
                  : '確定要做之後，用這份構想的內容當需求，建立一份功能規格（會另外建立新的 Spec，這份構想保留不動）'
              }
              onClick={() => setConverting(true)}
            >
              轉成功能規格
            </button>
          )}
          <span className="spacer" />
          {!data.archived && <DiagramMenu template={template} onInsert={insertDiagram} />}
          {/* 只有圖示，名稱放在滑鼠提示和 aria-label */}
          <div className="seg seg-sm seg-icon" role="group" aria-label="顯示方式">
            {VIEW_MODES.map(({ key, icon, label }) => {
              const blocked = key === 'split' && !canSplit
              return (
                <button
                  key={key}
                  className={mode === key ? 'active' : ''}
                  aria-pressed={mode === key}
                  aria-label={label}
                  disabled={blocked}
                  title={blocked ? (narrow ? '螢幕太窄，不能左右分割' : '畫面太窄，收起左右側欄後可以分割') : label}
                  onClick={() => setMode(key)}
                >
                  <Icon name={icon} />
                </button>
              )
            })}
          </div>
          <button
            className="btn btn-sm btn-icon"
            aria-expanded={panelOpen}
            aria-label={panelOpen ? '收起右側欄' : '展開右側欄'}
            title={panelOpen ? '收起右側欄' : '展開右側欄'}
            onClick={() => setPanelOpen((o) => !o)}
          >
            <Icon name={panelOpen ? 'panel-close' : 'panel-open'} />
          </button>
        </div>
      </div>

      {data.archived && <div className="banner banner-warn">這份 Spec 已封存，不能修改。可以從「⋯」取消封存。</div>}
      {data.active_run_id && (
        <div className="banner banner-info">
          AI 正在撰寫這份文件，完成後會存成新版本。
          {dirty && ' 你現在的修改請先存檔，之後再用版本比較確認差異。'}
          {!panelOpen || tab !== 'agent' ? (
            <button className="link-btn" onClick={() => { setPanelOpen(true); setTab('agent') }}>
              看進度
            </button>
          ) : null}
        </div>
      )}
      {(headChanged || conflict) && (
        <div className="banner banner-warn">
          <span>
            {conflict ?? `這份文件已經有新版本 v${data.head_version_no}（${data.head ? VERSION_SOURCE[data.head.source] : ''}），你的修改是根據 v${baseNo}。`}
          </span>
          <button className="btn btn-sm" onClick={() => setDiff({ from: baseNo ?? undefined, to: data.head_version_no })}>
            看新版本改了什麼
          </button>
          <button className="btn btn-sm" onClick={() => { setDraft(null); setBaseNo(null); setConflict(null) }}>
            放棄我的修改，載入新版
          </button>
          <button className="btn btn-sm btn-primary" onClick={() => save(true)}>
            仍然存成新版本
          </button>
        </div>
      )}

      <div
        ref={bodyRef}
        className={`spec-body ${panelOpen ? 'with-panel' : ''}`}
        style={{ '--panel-w': `${panelWidth}px`, '--editor-pct': `${editorPct}%` } as CSSProperties}
      >
        <div ref={setDocEl} className={`doc-area mode-${mode}`}>
          {mode !== 'preview' && (
            <div className="editor-pane">
              <MarkdownEditor
                ref={editor}
                value={content}
                onChange={onChange}
                readOnly={data.archived}
                onScroll={() => onPaneScroll('editor')}
              />
            </div>
          )}
          {mode === 'split' && (
            <Splitter
              label="調整編輯區和預覽區的寬度"
              onMove={(x) => {
                const r = docEl.current?.getBoundingClientRect()
                if (r) setEditorPct(((x - r.left) / r.width) * 100)
              }}
              onNudge={(d) => setEditorPct((p) => p + d * 2)}
              onReset={() => setEditorPct(EDITOR_DEFAULT)}
            />
          )}
          {mode !== 'edit' && (
            <div className="preview-pane" ref={previewEl} onScroll={() => onPaneScroll('preview')}>
              {content.trim() ? (
                <MarkdownView markdown={content} />
              ) : (
                <Empty title={data.active_run_id ? 'AI 撰寫中' : '文件是空的'}>
                  {data.active_run_id
                    ? '完成後內容會出現在這裡，可以在右邊的「AI 代理」看進度。'
                    : '可以直接編輯，或在右邊的「AI 代理」輸入需求讓 AI 撰寫。'}
                </Empty>
              )}
            </div>
          )}
        </div>

        {panelOpen && !narrow && (
          <Splitter
            label="調整右側欄的寬度"
            onMove={(x) => {
              const r = bodyRef.current?.getBoundingClientRect()
              if (r) setPanelWidth(r.right - x)
            }}
            onNudge={(d) => setPanelWidth((w) => w - d * 20)}
            onReset={() => setPanelWidth(PANEL_DEFAULT)}
          />
        )}
        {panelOpen && (
          <aside className="side-panel">
            <div
              className="tabs"
              role="tablist"
              aria-label="右側欄"
              onKeyDown={(e) => {
                // 左右方向鍵切換分頁
                const i = panelTabs.findIndex((t) => t.key === tab)
                const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0
                if (!d) return
                const next = panelTabs[(i + d + panelTabs.length) % panelTabs.length]
                setTab(next.key)
                document.getElementById(`tab-${next.key}`)?.focus()
              }}
            >
              {panelTabs.map((t) => (
                <button
                  key={t.key}
                  id={`tab-${t.key}`}
                  role="tab"
                  aria-selected={tab === t.key}
                  aria-controls="side-panel-body"
                  tabIndex={tab === t.key ? 0 : -1}
                  className={tab === t.key ? 'active' : ''}
                  onClick={() => setTab(t.key)}
                >
                  <Icon name={t.icon} /> {t.label}
                  {t.key === 'review' && review && review.open > 0 && (
                    <span className="tab-count" aria-label={`${review.open} 項沒處理`}>
                      {review.open}
                    </span>
                  )}
                </button>
              ))}
            </div>
            <div className="side-panel-body" id="side-panel-body" role="tabpanel" aria-labelledby={`tab-${tab}`}>
              {tab === 'agent' && (
                <AgentPanel
                  spec={data}
                  onRunFinished={() => refreshAll()}
                  onOpenDiff={(from, to) => setDiff({ from, to })}
                  onJumpToSection={jumpToHeading}
                />
              )}
              {tab === 'review' && review && (
                <ReviewChecklist key={review.run_id} spec={data} onJump={jumpToHeading} onOpenDiff={(from, to) => setDiff({ from, to })} />
              )}
              {tab === 'versions' && (
                <VersionPanel specId={specId} headNo={data.head_version_no} archived={data.archived} dirty={dirty} />
              )}
              {tab === 'outline' &&
                (outline.length === 0 ? (
                  <div className="muted small pad">沒有標題。用 # / ## / ### 開頭的行會列在這裡。</div>
                ) : (
                  <ul className="outline">
                    {outline.map((item, i) => (
                      <li
                        key={i}
                        className={`outline-l${item.level} ${i === activeHeading ? 'is-active' : ''}`}
                        aria-current={i === activeHeading ? 'location' : undefined}
                      >
                        <button className="link-btn" onClick={() => jumpTo(item.line, item.text)}>
                          {item.text}
                        </button>
                      </li>
                    ))}
                  </ul>
                ))}
            </div>
          </aside>
        )}
      </div>

      {diff && <DiffModal specId={specId} from={diff.from} to={diff.to} onClose={() => setDiff(null)} />}
      {converting && (
        <NewSpecDialog
          projectId={projectId}
          phases={phases}
          phaseId={data.phase_id}
          preset={{
            category: 'feature',
            title: data.title.replace(/構想(提案)?/g, '').trim() || data.title,
            requirement: data.content,
            source: { id: data.id, title: data.title },
          }}
          onClose={() => setConverting(false)}
        />
      )}
      {archiving && (
        <ConfirmDialog
          title="封存 Spec"
          message="封存後不會出現在清單和基準版本裡，版本歷程都會保留，之後可以取消封存。"
          confirmText="封存"
          onConfirm={async () => {
            try {
              await api.archiveSpec(specId)
              refreshAll()
              toast('已封存', 'success')
            } catch (e) {
              toast(errorText(e), 'error')
            }
          }}
          onClose={() => setArchiving(false)}
        />
      )}
      {blocker.state === 'blocked' && (
        <ConfirmDialog
          title="還沒存檔"
          message="這份文件有未儲存的修改，離開後修改會不見。確定要離開嗎？"
          confirmText="不存檔，直接離開"
          danger
          onConfirm={() => {
            leaving.current = true
            blocker.proceed()
          }}
          onClose={() => {
            if (leaving.current) leaving.current = false
            else if (blocker.state === 'blocked') blocker.reset()
          }}
        />
      )}
    </div>
  )
}
