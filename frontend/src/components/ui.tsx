// 共用的小元件：對話框、選單、通知、確認視窗、標籤

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from 'react'
import { createPortal } from 'react-dom'
import { Link } from 'react-router'
import { CATEGORY_TONE } from '../format'
import { Icon, type IconName } from './icons'

// ---------------------------------------------------------------------------
// 對話框
// ---------------------------------------------------------------------------

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])'

export function Modal(props: {
  title: ReactNode
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
  wide?: boolean
  full?: boolean
}) {
  const { onClose } = props
  const titleId = useId()
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  // 打開時把焦點移進對話框，關掉後回到原本的位置（鍵盤操作才不會迷路）
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    const el = box.current
    if (el && !el.contains(document.activeElement)) {
      const first = el.querySelector<HTMLElement>('.modal-body ' + FOCUSABLE) ?? el.querySelector<HTMLElement>(FOCUSABLE)
      ;(first ?? el).focus()
    }
    return () => previous?.focus?.()
  }, [])
  // Tab 鍵只在對話框裡面循環
  const trapTab = (e: ReactKeyboardEvent) => {
    if (e.key !== 'Tab' || !box.current) return
    const items = [...box.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((x) => x.offsetParent !== null)
    if (items.length === 0) return
    const first = items[0]
    const last = items[items.length - 1]
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault()
      last.focus()
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault()
      first.focus()
    }
  }
  return createPortal(
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={box}
        className={`modal ${props.wide ? 'modal-wide' : ''} ${props.full ? 'modal-full' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onKeyDown={trapTab}
      >
        <div className="modal-head">
          <h2 id={titleId}>{props.title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="關閉">
            ✕
          </button>
        </div>
        <div className="modal-body">{props.children}</div>
        {props.footer && <div className="modal-foot">{props.footer}</div>}
      </div>
    </div>,
    document.body,
  )
}

// ---------------------------------------------------------------------------
// 選單和彈出框
//
// 選單放在 body 底下、固定在畫面上，不會被會捲動的區塊（側欄、右側欄）裁掉。
// 預設在按鈕下方；下方放不下時往上開，左右不超出畫面，太高時選單自己捲動。
// 點外面、按 Esc、外面捲動或視窗大小改變時關掉。
// ---------------------------------------------------------------------------

type Align = 'start' | 'end'
type Side = 'top' | 'bottom'
const MENU_GAP = 4 // 按鈕和選單之間的距離
const MENU_MARGIN = 8 // 選單和畫面邊緣至少留這麼多

function placePanel(trigger: DOMRect, panel: HTMLElement, align: Align): { style: CSSProperties; side: Side } {
  const width = panel.offsetWidth
  const below = window.innerHeight - trigger.bottom - MENU_GAP - MENU_MARGIN
  const above = trigger.top - MENU_GAP - MENU_MARGIN
  const x = align === 'end' ? trigger.right - width : trigger.left
  const left = Math.min(Math.max(MENU_MARGIN, x), window.innerWidth - width - MENU_MARGIN)
  return panel.scrollHeight > below && above > below
    ? { side: 'top', style: { left, bottom: window.innerHeight - trigger.top + MENU_GAP, maxHeight: above } }
    : { side: 'bottom', style: { left, top: trigger.bottom + MENU_GAP, maxHeight: below } }
}

/** 選單和彈出框共用：開關、位置、什麼時候自動關掉 */
function useDropdown(align: Align) {
  const [open, setOpen] = useState(false)
  const [place, setPlace] = useState<{ style: CSSProperties; side: Side } | null>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const panel = useRef<HTMLDivElement>(null)

  /** refocus：焦點回到按鈕（用鍵盤關掉或選了項目時） */
  const close = useCallback((refocus = false) => {
    setOpen(false)
    setPlace(null)
    if (refocus) trigger.current?.focus({ preventScroll: true })
  }, [])

  // 畫面更新前先量好位置，選單才不會先出現在左上角再跳過去
  useLayoutEffect(() => {
    if (open && trigger.current && panel.current) setPlace(placePanel(trigger.current.getBoundingClientRect(), panel.current, align))
  }, [open, align])

  useEffect(() => {
    if (!open) return
    const outside = (t: EventTarget | null) => !(t instanceof Node && (panel.current?.contains(t) || trigger.current?.contains(t)))
    const onPointerDown = (e: PointerEvent) => outside(e.target) && close()
    // 選單固定在畫面上，不會跟著外面捲動，所以外面一捲動就關掉；選單自己捲動不算
    const onScroll = (e: Event) => outside(e.target) && close()
    const onResize = () => close()
    document.addEventListener('pointerdown', onPointerDown, true)
    document.addEventListener('scroll', onScroll, true)
    window.addEventListener('resize', onResize)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true)
      document.removeEventListener('scroll', onScroll, true)
      window.removeEventListener('resize', onResize)
    }
  }, [open, close])

  const panelProps = {
    ref: panel,
    'data-side': place?.side,
    // 還沒量好位置前先藏起來
    style: place ? place.style : ({ top: 0, left: 0, visibility: 'hidden' } as CSSProperties),
    // 選單在 React 裡還是按鈕的子元件，點擊會一路傳到外層（例如整列可以點的表格），在這裡擋掉
    onClick: (e: { stopPropagation: () => void }) => e.stopPropagation(),
  }
  return { open, setOpen, close, trigger, panel, placed: place !== null, panelProps }
}

const MenuContext = createContext<(refocus?: boolean) => void>(() => {})

interface TriggerProps {
  /** 按鈕裡的內容（文字或圖示） */
  trigger: ReactNode
  /** 只有圖示的按鈕要給，讀螢幕軟體會念 */
  label?: string
  title?: string
  /** 按鈕的 class，預設 btn */
  triggerClassName?: string
  /** 選單對齊按鈕的哪一邊，預設右邊（end） */
  align?: Align
  /** 加在選單上的 class，例如調整寬度 */
  className?: string
  children: ReactNode
}

/**
 * 下拉選單。項目用 MenuItem，分組用 MenuLabel、MenuSeparator。
 * 鍵盤：方向鍵移動、Home/End 到頭尾、打字跳到開頭相同的項目、Enter 選取、Esc 或 Tab 關掉
 */
export function Menu(props: TriggerProps) {
  const d = useDropdown(props.align ?? 'end')
  const id = useId()
  // 打開後焦點放哪裡：用鍵盤打開時放在第一個（或最後一個）項目，用滑鼠打開時放在選單本身
  const initialFocus = useRef<'first' | 'last' | 'panel'>('panel')

  const items = () => [...(d.panel.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]:not(:disabled)') ?? [])]

  useEffect(() => {
    if (!d.placed) return
    const list = items()
    const target = initialFocus.current === 'first' ? list[0] : initialFocus.current === 'last' ? list[list.length - 1] : undefined
    ;(target ?? d.panel.current)?.focus({ preventScroll: true })
  }, [d.placed])

  const openWith = (focus: 'first' | 'last' | 'panel') => {
    initialFocus.current = focus
    d.setOpen(true)
  }

  const onKeyDown = (e: ReactKeyboardEvent) => {
    const list = items()
    const i = list.indexOf(document.activeElement as HTMLElement)
    const go = (n: number) => {
      e.preventDefault()
      list[(n + list.length) % list.length]?.focus()
    }
    if (e.key === 'ArrowDown') go(i + 1)
    else if (e.key === 'ArrowUp') go(i < 0 ? -1 : i - 1)
    else if (e.key === 'Home') go(0)
    else if (e.key === 'End') go(-1)
    else if (e.key === 'Escape' || e.key === 'Tab') {
      // 擋掉，不然對話框裡的選單按 Esc 會連對話框一起關掉
      e.preventDefault()
      e.stopPropagation()
      d.close(true)
    } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const key = e.key.toLowerCase()
      const order = [...list.slice(i + 1), ...list.slice(0, i + 1)]
      order.find((el) => el.textContent?.trim().toLowerCase().startsWith(key))?.focus()
    }
  }

  return (
    <>
      <button
        ref={d.trigger}
        type="button"
        className={props.triggerClassName ?? 'btn'}
        aria-label={props.label}
        title={props.title}
        aria-haspopup="menu"
        aria-expanded={d.open}
        aria-controls={d.open ? id : undefined}
        // 用鍵盤按 Enter、空白鍵時 detail 是 0
        onClick={(e) => (d.open ? d.close() : openWith(e.detail === 0 ? 'first' : 'panel'))}
        onKeyDown={(e) => {
          if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
          e.preventDefault()
          openWith(e.key === 'ArrowDown' ? 'first' : 'last')
        }}
      >
        {props.trigger}
      </button>
      {d.open &&
        createPortal(
          <div
            {...d.panelProps}
            id={id}
            role="menu"
            aria-label={props.label}
            tabIndex={-1}
            className={`menu-list ${props.className ?? ''}`}
            onKeyDown={onKeyDown}
            // 滑鼠移到哪個項目，焦點就跟到哪裡，滑鼠和鍵盤的反白才不會各一個
            onPointerMove={(e) => {
              const item = (e.target as Element).closest<HTMLElement>('[role^="menuitem"]:not(:disabled)')
              if (item && item !== document.activeElement) item.focus({ preventScroll: true })
            }}
          >
            <MenuContext.Provider value={d.close}>{props.children}</MenuContext.Provider>
          </div>,
          document.body,
        )}
    </>
  )
}

/** 選單項目。給 onSelect 是按鈕，給 to 是站內連結，給 href 是一般連結（例如下載） */
export function MenuItem(props: {
  icon?: IconName
  danger?: boolean
  disabled?: boolean
  /** 有給就是單選項目，選中的項目右邊打勾 */
  checked?: boolean
  /** 右邊的淡色說明 */
  hint?: ReactNode
  title?: string
  /** 選了之後選單不要關（例如切換選項） */
  keepOpen?: boolean
  onSelect?: () => void
  to?: string
  href?: string
  download?: boolean
  children: ReactNode
}) {
  const close = useContext(MenuContext)
  const select = () => {
    props.onSelect?.()
    if (!props.keepOpen) close(true)
  }
  const common = {
    role: props.checked === undefined ? 'menuitem' : 'menuitemradio',
    'aria-checked': props.checked,
    tabIndex: -1,
    title: props.title,
    className: `menu-item${props.danger ? ' danger' : ''}`,
    onClick: select,
  }
  const body = (
    <>
      {props.icon && <Icon name={props.icon} />}
      <span className="menu-label">{props.children}</span>
      {props.hint && <span className="menu-hint">{props.hint}</span>}
      {props.checked && (
        <span className="menu-check">
          <Icon name="check" />
        </span>
      )}
    </>
  )
  if (props.to)
    return (
      <Link to={props.to} {...common}>
        {body}
      </Link>
    )
  if (props.href)
    return (
      <a href={props.href} download={props.download} {...common}>
        {body}
      </a>
    )
  return (
    <button type="button" disabled={props.disabled} {...common}>
      {body}
    </button>
  )
}

export function MenuSeparator() {
  return <div role="separator" className="menu-sep" />
}

/** 選單裡一組項目的小標題 */
export function MenuLabel({ children }: { children: ReactNode }) {
  return (
    <div role="presentation" className="menu-group">
      {children}
    </div>
  )
}

/** 彈出框：外觀、位置跟選單一樣，裡面放表單之類的內容。焦點移到外面、按 Esc 時關掉 */
export function Popover(props: TriggerProps) {
  const d = useDropdown(props.align ?? 'start')
  const id = useId()
  useEffect(() => {
    if (!d.placed) return
    const el = d.panel.current
    ;(el?.querySelector<HTMLElement>(FOCUSABLE) ?? el)?.focus({ preventScroll: true })
  }, [d.placed])
  return (
    <>
      <button
        ref={d.trigger}
        type="button"
        className={props.triggerClassName ?? 'btn'}
        aria-label={props.label}
        title={props.title}
        aria-haspopup="dialog"
        aria-expanded={d.open}
        aria-controls={d.open ? id : undefined}
        onClick={() => (d.open ? d.close() : d.setOpen(true))}
      >
        {props.trigger}
      </button>
      {d.open &&
        createPortal(
          <div
            {...d.panelProps}
            id={id}
            role="dialog"
            aria-label={props.label}
            tabIndex={-1}
            className={`menu-list ${props.className ?? ''}`}
            onKeyDown={(e) => {
              if (e.key !== 'Escape') return
              e.stopPropagation()
              d.close(true)
            }}
            onBlur={(e) => {
              const next = e.relatedTarget
              if (next instanceof Node && !d.panel.current?.contains(next) && !d.trigger.current?.contains(next)) d.close()
            }}
          >
            {props.children}
          </div>,
          document.body,
        )}
    </>
  )
}

// ---------------------------------------------------------------------------
// 通知
// ---------------------------------------------------------------------------

type ToastKind = 'info' | 'success' | 'error' | 'warn'
interface ToastItem {
  id: number
  kind: ToastKind
  text: string
}

const ToastContext = createContext<(text: string, kind?: ToastKind) => void>(() => {})

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const nextId = useRef(1)
  const push = useCallback((text: string, kind: ToastKind = 'info') => {
    const id = nextId.current++
    setItems((list) => [...list, { id, kind, text }])
    setTimeout(() => setItems((list) => list.filter((t) => t.id !== id)), kind === 'error' ? 8000 : 4000)
  }, [])
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind}`} onClick={() => setItems((l) => l.filter((x) => x.id !== t.id))}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export const useToast = () => useContext(ToastContext)

export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

// ---------------------------------------------------------------------------
// 確認視窗
// ---------------------------------------------------------------------------

export function ConfirmDialog(props: {
  title: string
  message: ReactNode
  confirmText?: string
  danger?: boolean
  onConfirm: () => void | Promise<void>
  onClose: () => void
}) {
  const [busy, setBusy] = useState(false)
  return (
    <Modal
      title={props.title}
      onClose={props.onClose}
      footer={
        <>
          <button className="btn" onClick={props.onClose} disabled={busy}>
            取消
          </button>
          <button
            className={`btn ${props.danger ? 'btn-danger' : 'btn-primary'}`}
            disabled={busy}
            onClick={async () => {
              setBusy(true)
              try {
                await props.onConfirm()
                props.onClose()
              } finally {
                setBusy(false)
              }
            }}
          >
            {props.confirmText ?? '確定'}
          </button>
        </>
      }
    >
      <div className="confirm-message">{props.message}</div>
    </Modal>
  )
}

// ---------------------------------------------------------------------------
// 其他
// ---------------------------------------------------------------------------

export function Spinner({ small }: { small?: boolean }) {
  return <span className={`spinner ${small ? 'spinner-sm' : ''}`} aria-label="處理中" />
}

/** 載入中的灰色方塊，形狀接近之後的內容，畫面才不會跳動 */
export function Skeleton({ lines = 3, title = true }: { lines?: number; title?: boolean }) {
  return (
    <div className="skeleton" role="status" aria-label="載入中">
      {title && <div className="skeleton-bar skeleton-title" />}
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className="skeleton-bar" style={{ width: `${92 - ((i * 17) % 35)}%` }} />
      ))}
    </div>
  )
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <div className="empty-title">{title}</div>
      {children && <div className="empty-body">{children}</div>}
    </div>
  )
}

export function Badge({ children, tone = 'neutral', title }: { children: ReactNode; tone?: string; title?: string }) {
  return (
    <span className={`badge badge-${tone}`} title={title}>
      {children}
    </span>
  )
}

/** 類型標籤（Idea、Functionality…）：依類型上色、粗體 */
export function CategoryBadge({ category, children }: { category: string; children: ReactNode }) {
  return <span className={`badge badge-${CATEGORY_TONE[category] ?? 'neutral'} badge-category`}>{children}</span>
}

export function Field(props: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{props.label}</span>
      {props.children}
      {props.hint && <span className="field-hint">{props.hint}</span>}
    </label>
  )
}

/** 單行輸入框按 Enter 送出對話框。中文輸入法選字時按的 Enter 不算 */
export function submitOnEnter(e: ReactKeyboardEvent, enabled: boolean, submit: () => void) {
  if (e.key !== 'Enter' || e.nativeEvent.isComposing || !enabled) return
  e.preventDefault()
  submit()
}


// ---------------------------------------------------------------------------
// 拖拉分隔線：左右拖動調整欄寬，也可以用方向鍵，雙擊恢復預設
// ---------------------------------------------------------------------------

export function Splitter(props: { label: string; onMove: (clientX: number) => void; onNudge: (delta: number) => void; onReset: () => void }) {
  return (
    <div
      className="splitter"
      role="separator"
      aria-orientation="vertical"
      aria-label={props.label}
      title={`${props.label}（拖拉調整，雙擊恢復預設）`}
      tabIndex={0}
      onPointerDown={(e) => {
        e.preventDefault()
        e.currentTarget.setPointerCapture(e.pointerId)
        document.body.classList.add('is-resizing')
      }}
      onPointerMove={(e) => {
        if (e.currentTarget.hasPointerCapture(e.pointerId)) props.onMove(e.clientX)
      }}
      onPointerUp={(e) => {
        if (e.currentTarget.hasPointerCapture(e.pointerId)) props.onMove(e.clientX)
        e.currentTarget.releasePointerCapture(e.pointerId)
        document.body.classList.remove('is-resizing')
      }}
      onLostPointerCapture={() => document.body.classList.remove('is-resizing')}
      onDoubleClick={props.onReset}
      onKeyDown={(e) => {
        if (e.key === 'ArrowLeft') props.onNudge(-1)
        else if (e.key === 'ArrowRight') props.onNudge(1)
        else return
        e.preventDefault()
      }}
    />
  )
}
