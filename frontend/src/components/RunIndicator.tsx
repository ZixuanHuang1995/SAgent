// 頂端列的 AI 工作指示：在任何頁面都看得到哪些文件正在撰寫、排隊或等待確認大綱。
// 工作結束或停下來等確認大綱時跳提示；分頁在背景時改分頁標題，開了桌面通知的話也會通知

import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router'
import { api, type ActiveRunStatus, type Run } from '../api'
import { AI_STATUS, formatDuration, runDuration } from '../format'
import { Menu, MenuItem, MenuLabel, MenuSeparator, useToast } from './ui'

const ACTIVE: ActiveRunStatus[] = ['running', 'planned', 'queued']
const isActive = (r: Run): r is Run & { status: ActiveRunStatus } => (ACTIVE as string[]).includes(r.status)
const specPath = (r: Run) => `/p/${r.project_id}/s/${r.spec_id}`

function canNotify() {
  return typeof Notification !== 'undefined'
}

export function RunIndicator() {
  const runs = useQuery({
    // key 用 runs 開頭，其他地方更新 AI 工作清單時這裡也會跟著更新
    queryKey: ['runs', 'indicator'],
    queryFn: () => api.runs({ limit: 30 }),
    refetchInterval: (q) => (q.state.data?.some(isActive) ? 5000 : 30_000),
  })
  const toast = useToast()
  const location = useLocation()
  const pathRef = useRef(location.pathname)
  pathRef.current = location.pathname
  const [permission, setPermission] = useState(() => (canNotify() ? Notification.permission : 'denied'))
  // 分頁在背景時發生的事，回到分頁前一直顯示在分頁標題
  const [unseen, setUnseen] = useState<string | null>(null)
  const baseTitle = useRef(document.title)
  const previous = useRef<Map<number, string> | null>(null)

  const active = (runs.data ?? []).filter(isActive).sort((a, b) => ACTIVE.indexOf(a.status) - ACTIVE.indexOf(b.status))

  // 跟上一次比較：原本進行中的工作結束了，或停下來等確認大綱
  useEffect(() => {
    if (!runs.data) return
    const now = new Map(runs.data.filter(isActive).map((r) => [r.id, r.status as string]))
    const before = previous.current
    previous.current = now
    if (!before) return // 第一次載入不提示
    for (const [id, status] of before) {
      const run = runs.data.find((r) => r.id === id)
      if (!run || run.status === status) continue
      let text: string | null = null
      let kind: 'success' | 'error' | 'info' = 'info'
      if (run.status === 'succeeded') {
        text = `AI 已完成「${run.spec_title}」${run.result_version_no ? `，存成 v${run.result_version_no}` : ''}`
        kind = 'success'
      } else if (run.status === 'failed') {
        text = `AI 工作失敗：「${run.spec_title}」`
        kind = 'error'
      } else if (run.status === 'planned') {
        text = `「${run.spec_title}」的大綱完成，等你確認後繼續撰寫`
      }
      if (!text) continue // 取消是使用者自己按的，不用提示
      // 正在看這份文件時，AI 面板自己會提示
      if (pathRef.current !== specPath(run)) toast(text, kind)
      if (document.hidden) {
        setUnseen(kind === 'error' ? '✕ AI 工作失敗' : run.status === 'planned' ? '● 待確認大綱' : '✓ AI 已完成')
        if (canNotify() && Notification.permission === 'granted') {
          const n = new Notification('SAgent', { body: text, tag: `sagent-run-${run.id}` })
          n.onclick = () => window.focus()
        }
      }
    }
  }, [runs.data, toast])

  // 回到分頁就清掉
  useEffect(() => {
    const clear = () => {
      if (!document.hidden) setUnseen(null)
    }
    document.addEventListener('visibilitychange', clear)
    window.addEventListener('focus', clear)
    return () => {
      document.removeEventListener('visibilitychange', clear)
      window.removeEventListener('focus', clear)
    }
  }, [])

  const running = active.some((r) => r.status === 'running' || r.status === 'queued')
  const planned = active.some((r) => r.status === 'planned')
  useEffect(() => {
    const prefix = unseen ?? (planned ? '● 待確認大綱' : running ? '● AI 撰寫中' : null)
    document.title = prefix ? `${prefix}｜${baseTitle.current}` : baseTitle.current
  }, [unseen, running, planned])

  if (active.length === 0) return null
  const first = active[0]
  const askPermission = async () => {
    if (!canNotify()) return
    setPermission(await Notification.requestPermission())
  }

  return (
    <Menu
      trigger={
        <>
          <span className={`badge badge-ai ai-${first.status}`}>{AI_STATUS[first.status]}</span>
          <span className="run-indicator-title">{first.spec_title}</span>
          {active.length > 1 && <span className="muted small">+{active.length - 1}</span>}
        </>
      }
      label={`進行中的 AI 工作 ${active.length} 個`}
      title="進行中的 AI 工作"
      triggerClassName="run-indicator"
      className="run-indicator-menu"
    >
      <MenuLabel>進行中的 AI 工作</MenuLabel>
      {active.map((r) => {
        const ms = r.status === 'running' ? runDuration(r) : null
        return (
          <MenuItem
            key={r.id}
            icon="bot"
            to={specPath(r)}
            hint={`${AI_STATUS[r.status]}${ms ? `・${formatDuration(ms)}` : ''}${r.queue_position ? `・第 ${r.queue_position} 個` : ''}`}
          >
            {r.spec_title}
          </MenuItem>
        )
      })}
      {canNotify() && permission !== 'granted' && (
        <>
          <MenuSeparator />
          <MenuItem
            icon="help"
            disabled={permission === 'denied'}
            title={permission === 'denied' ? '瀏覽器已經封鎖這個網站的通知，要從網址列左邊的設定打開' : undefined}
            keepOpen
            onSelect={askPermission}
          >
            {permission === 'denied' ? '桌面通知已被瀏覽器封鎖' : '完成時用桌面通知提醒我'}
          </MenuItem>
        </>
      )}
    </Menu>
  )
}
