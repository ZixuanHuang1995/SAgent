// 顯示用的文字和格式

import type { ActiveRunStatus, PlanTerm, RunStatus, SpecStatus, Template, VersionMeta } from './api'

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
  algorithm: 'teal',
  change: 'green',
}

export const RUN_STATUS: Record<RunStatus, string> = {
  queued: '排隊中',
  running: '執行中',
  planned: '等待確認大綱',
  succeeded: '完成',
  failed: '失敗',
  cancelled: '已取消',
}

/** Spec 清單和標題旁的 AI 狀態標籤 */
export const AI_STATUS: Record<ActiveRunStatus, string> = {
  queued: 'AI 排隊中',
  running: 'AI 撰寫中',
  planned: '待確認大綱',
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

/** 名詞表轉成一行一個的文字：「名稱（類型）：說明」 */
export function termsToText(terms: PlanTerm[]): string {
  return terms.map((t) => `${t.name}${t.kind ? `（${t.kind}）` : ''}${t.note ? `：${t.note}` : ''}`).join('\n')
}

/** 一行一個：「名稱（類型）：說明」，類型和說明可以省略 */
export function textToTerms(text: string): PlanTerm[] {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const m = line.match(/^(.+?)(?:（(.+?)）)?(?:[：:](.*))?$/)
      return { name: (m?.[1] ?? line).trim(), kind: (m?.[2] ?? '').trim(), note: (m?.[3] ?? '').trim() }
    })
}

/** 名詞表的格式說明，專案詞彙表和確認大綱的名詞表共用 */
export const TERMS_FORMAT = '一行一個：名稱（類型）：說明。類型用角色、狀態、資料或其他。'

/** 建立、編輯專案共用的欄位說明 */
export const PROJECT_CODE_HINT = '簡短的英文代號，例如 HR、ORD'
export const PROJECT_DESC_HINT = '可以寫：給誰用、要解決什麼問題、預期帶來什麼效益'

/** AI 撰寫一份文件大概要多久（CPU 推論），各處的說明都用這個 */
export const RUN_TIME_TEXT = '本機 CPU 上一份文件約 30～70 分鐘'

const REQUIREMENT_HELP: Record<Template['style'], { hint: string; placeholder: string }> = {
  idea: {
    hint: '寫下你的想法就好，幾句話也可以：想解決什麼問題、給誰用、大概想怎麼做。AI 會整理成構想提案，不清楚的地方列成待確認事項。',
    placeholder: '例如：\n員工常找不到空的會議室，想做一個手機就能查、能預約的功能，先給總部試用。',
  },
  decision: {
    hint: '寫清楚要做什麼決定、有哪些限制、用什麼標準比較，已經想到的方案也列出來。AI 會補充方案、做比較表並提出建議。',
    placeholder: '例如：\n搜尋功能要用資料庫全文檢索，還是另外架搜尋引擎？三個月內上線，只有兩個人維護。',
  },
  experiment: {
    hint: '寫清楚要評估的工具或做法、想知道的事、可以參與的人和時間、怎樣算成功。AI 會設計假設、指標、分組、流程和分析方法。',
    placeholder: '例如：\n評估 AI 程式碼審查工具，三個團隊參加 8 週，PR 前置時間要縮短 20% 以上才算有效。',
  },
  algorithm: {
    hint: '寫清楚要解決的問題、輸入和輸出、限制條件和效能要求。AI 會設計解法、寫虛擬碼、分析複雜度並列出測試案例。',
    placeholder: '例如：\n解 9×9 數獨，題目用 81 個字元表示，0 是空格，每題要在 1 秒內解完。',
  },
  advice: {
    hint: '寫清楚給誰用、在什麼情況下用、現在哪裡不好用。AI 會提出畫面和操作上的建議，每條都有理由和優先順序。',
    placeholder: '例如：\n報修畫面給現場員工用手機操作，常常邊走邊填，希望 1 分鐘內就能送出。',
  },
  spec: {
    hint: '可以貼會議紀錄、客戶需求、條列重點，寫得越具體越好。',
    placeholder:
      '例如：\n公司要做員工請假系統，取代紙本流程。\n1. 員工在網頁申請請假，選假別、日期、代理人\n2. 主管審核，超過 3 天要經理再審\n3. 自動計算剩餘特休…',
  },
}

/** 需求欄的說明和 placeholder，依文件類型的寫作規則給不同的建議。新增 Spec 和 AI 撰寫表單共用 */
export function requirementHelp(template: Template | undefined, mode: 'draft' | 'revise' = 'draft') {
  if (mode === 'revise')
    return {
      hint: '寫要改什麼就好，例如新增哪條規則、哪一節要調整、哪裡寫錯了。',
      placeholder: '例如：\n請假要支援「半天」為單位，並且新增「補休」假別。',
    }
  return REQUIREMENT_HELP[template?.style ?? 'spec'] ?? REQUIREMENT_HELP.spec
}
