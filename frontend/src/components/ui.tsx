// 共用的小元件：對話框、通知、確認視窗、標籤

import { createContext, useCallback, useContext, useEffect, useId, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

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

export function Field(props: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{props.label}</span>
      {props.children}
      {props.hint && <span className="field-hint">{props.hint}</span>}
    </label>
  )
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
