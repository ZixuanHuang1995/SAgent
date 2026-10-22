// 顯示用的文字和格式

import type { RunStatus, SpecStatus, VersionMeta } from './api'

export const SPEC_STATUS: Record<SpecStatus, string> = {
  draft: '草稿',
  reviewing: '審核中',
  approved: '已核准',
  deprecated: '已停用',
}

export const VERSION_SOURCE: Record<VersionMeta['source'], string> = {
  manual: '手動編輯',
  agent: 'AI 產生',
  restore: '還原',
  import: '匯入',
  template: '範本',
}

/** 類型標籤的顏色（Badge 的 tone） */
export const CATEGORY_TONE: Record<string, string> = {
  idea: 'amber',
  feature: 'blue',
  design: 'violet',
  change: 'green',
}

export const RUN_STATUS: Record<RunStatus, string> = {
  queued: '排隊中',
  running: '執行中',
  succeeded: '完成',
  failed: '失敗',
  cancelled: '已取消',
}

export const AGENT_NAME: Record<string, string> = {
  planner: 'Planner',
  writer: 'Writer',
  reviewer: 'Reviewer',
  system: '系統',
}

export const UML_SYNTAX: Record<string, string> = {
  auto: '自動（預設 Mermaid）',
  mermaid: '全部用 Mermaid',
  plantuml: '全部用 PlantUML',
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const diff = (Date.now() - new Date(iso).getTime()) / 1000
  if (diff < 60) return '剛剛'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分鐘前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小時前`
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`
  return formatTime(iso)
}

export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return ''
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s} 秒`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} 分 ${s % 60} 秒`
  return `${Math.floor(m / 60)} 小時 ${m % 60} 分`
}

export function formatBytes(n?: number): string {
  if (!n) return ''
  if (n > 1e9) return `${(n / 1e9).toFixed(1)} GB`
  return `${(n / 1e6).toFixed(0)} MB`
}

export function runDuration(run: { started_at: string | null; finished_at: string | null }): number | null {
  if (!run.started_at) return null
  const end = run.finished_at ? new Date(run.finished_at).getTime() : Date.now()
  return end - new Date(run.started_at).getTime()
}

/** 標題轉成網頁錨點 id */
export function slugify(text: string): string {
  return (
    'h-' +
    text
      .trim()
      .toLowerCase()
      .replace(/[\s]+/g, '-')
      .replace(/[^\p{L}\p{N}-]/gu, '')
  )
}

export interface OutlineItem {
  level: number
  text: string
  line: number
}

/** 從 Markdown 抓出標題（略過程式碼區塊） */
export function extractOutline(markdown: string): OutlineItem[] {
  const items: OutlineItem[] = []
  let fence: string | null = null
  markdown.split('\n').forEach((line, i) => {
    const f = line.match(/^\s{0,3}(`{3,}|~{3,})/)
    if (f) {
      if (fence === null) fence = f[1]
      else if (f[1][0] === fence[0] && f[1].length >= fence.length && line.trim() === f[1]) fence = null
      return
    }
    if (fence !== null) return
    const m = line.match(/^(#{1,3})\s+(.+?)\s*#*\s*$/)
    if (m) items.push({ level: m[1].length, text: m[2], line: i + 1 })
  })
  return items
}
