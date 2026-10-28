// 後端 API 的型別和呼叫方式

export type SpecStatus = 'draft' | 'reviewing' | 'approved' | 'deprecated'
export type RunStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
export type RunMode = 'draft' | 'revise'
export type UmlSyntax = 'auto' | 'mermaid' | 'plantuml'

export interface Project {
  id: number
  name: string
  code: string
  description: string
  created_at: string
  updated_at: string
  phase_count: number
  spec_count: number
  /** 各階段和底下的 Spec 數量（不含封存），依階段順序 */
  phases: PhaseSummary[]
  /** 執行中、排隊中的 AI 工作數量 */
  running_runs: number
  queued_runs: number
}

export interface Phase {
  id: number
  project_id: number
  name: string
  description: string
  sort_order: number
}

export interface PhaseSummary extends Phase {
  spec_count: number
}

export interface Spec {
  id: number
  project_id: number
  phase_id: number
  title: string
  doc_type: string
  status: SpecStatus
  head_version_no: number
  archived: boolean
  /** 從哪一份 Spec 轉過來的（例如構想 → 功能規格） */
  source_spec_id: number | null
  created_at: string
  updated_at: string
}

export interface SpecLink {
  id: number
  title: string
  doc_type: string
  archived: boolean
}

export interface VersionMeta {
  id: number
  spec_id: number
  version_no: number
  message: string
  source: 'manual' | 'agent' | 'restore' | 'import' | 'template'
  run_id: number | null
  label: string | null
  created_at: string
  content_hash: string
  /** 跟前一版比較新增、刪除幾行（舊資料可能沒有） */
  lines_added: number | null
  lines_removed: number | null
}

export interface VersionDetail extends VersionMeta {
  content: string
}

export interface SpecDetail extends Spec {
  content: string
  head: VersionMeta | null
  active_run_id: number | null
  source_spec: SpecLink | null
  derived_specs: SpecLink[]
}

export interface ProjectTree {
  project: Project
  phases: Phase[]
  specs: Spec[]
}

export interface SaveResult {
  created: boolean
  version: VersionMeta
  spec: Spec
}

export interface DiffLine {
  t: 'eq' | 'add' | 'del'
  a: number | null
  b: number | null
  text: string
}

export interface DiffResult {
  from_version: number
  to_version: number
  stats: { added: number; removed: number }
  lines: DiffLine[]
  sections: { heading: string; status: 'added' | 'removed' | 'modified' | 'unchanged' }[]
}

export interface Baseline {
  id: number
  project_id: number
  name: string
  note: string
  phase_id: number | null
  created_at: string
  item_count: number
}

export interface BaselineItem {
  spec_id: number
  title: string
  phase_id: number
  phase_name: string
  version_no: number
  label: string | null
  archived: boolean
}

export interface CompareItem {
  spec_id: number
  title: string
  phase_name: string
  status: 'added' | 'removed' | 'changed' | 'unchanged'
  from_version: number | null
  to_version: number | null
}

/** 類型：Concept、Functionality、Technical、Algorithm、Change。新增 Spec 時先選類型，再選文件類型 */
export interface TemplateCategory {
  key: string
  name: string
  description: string
}

export interface Template {
  key: string
  name: string
  description: string
  builtin: boolean
  category: string
  /** 寫作規則：spec 規格文件、advice 建議類文件、idea 構想文件、algorithm 演算法設計文件、experiment 實驗設計文件 */
  style: 'spec' | 'advice' | 'idea' | 'algorithm' | 'experiment'
  /** 建議類文件（例如 UI/UX 設計建議）：可以提出需求沒寫的做法 */
  advice: boolean
  /** false：這種文件不畫圖 */
  allow_diagrams: boolean
  sections: { heading: string; goal: string; diagrams: string[] }[]
}

/** 「插入圖表」選單的一種圖 */
export interface DiagramKind {
  key: string
  name: string
  /** 選單上的一行說明 */
  hint: string
  /** 分組名稱，例如「流程與互動」 */
  group: string
  mermaid: string
  /** 空字串：PlantUML 沒有這種圖 */
  plantuml: string
}

export interface ModelInfo {
  name: string
  size?: number
  parameter_size?: string
  quantization?: string
  family?: string
  capabilities: string[]
}

export interface AgentStat {
  calls: number
  prompt_tokens: number
  completion_tokens: number
  seconds: number
}

export interface RunOptions {
  model?: string
  uml_syntax?: UmlSyntax
  max_revisions?: number
  temperature?: number
  diagrams?: boolean
  max_diagrams?: number
}

export interface Run {
  id: number
  project_id: number
  spec_id: number
  mode: RunMode
  status: RunStatus
  model: string
  doc_type: string
  requirement: string
  options: RunOptions & Record<string, unknown>
  base_version_no: number
  result_version_no: number | null
  error: string
  created_at: string
  started_at: string | null
  finished_at: string | null
  stats: Record<string, AgentStat> | null
  spec_title: string
  queue_position: number | null
}

export interface PlanSection {
  id: string
  heading: string
  goal: string
  key_points: string[]
  action: 'write' | 'keep' | 'update'
  instruction: string
  diagrams: { kind: string; title: string; description: string; syntax: string }[]
}

export interface PlanOut {
  title: string
  summary: string
  fallback: boolean
  open_questions: string[]
  sections: PlanSection[]
}

export interface ReviewIssue {
  section_id: string | null
  severity: 'high' | 'medium' | 'low'
  problem: string
  suggestion: string
  source: 'check' | 'llm'
  diagram_index: number | null
}

/** 需要人工覆核的地方：只列給使用者確認，不會自動修改。source 是 check 表示文件標註了（假設）、（待確認） */
export interface ReviewPoint {
  section_id: string | null
  item: string
  reason: string
  source: 'check' | 'llm'
}

/** 審查結果。issues 是格式、邏輯錯誤，會交給 Writer 修正 */
export interface ReviewOut {
  round: number
  /** 舊版審查才有（1～10 分），現在不打分數 */
  score?: number | null
  summary: string
  verdict: 'pass' | 'revise'
  llm_ok: boolean
  issues: ReviewIssue[]
  /** 舊版審查沒有這個欄位 */
  review_points?: ReviewPoint[]
}

export interface RunEvent {
  seq?: number
  type: string
  agent: string
  message: string
  data: any
  ts?: string
}

export interface RunDetail extends Run {
  plan: PlanOut | null
  review: { final: ReviewOut | null; rounds: ReviewOut[] } | null
  events: RunEvent[]
}

export interface Settings {
  ollama_url: string
  default_model: string
  temperature: number
  max_revisions: number
  uml_syntax: UmlSyntax
  diagrams: boolean
  num_ctx: number
  plantuml_server: string
}

export interface Health {
  status: string
  version: string
  llm_provider: string
  default_model: string
  ollama: { url: string; reachable: boolean; version: string; error: string }
  plantuml: { mode: 'server' | 'jar' | 'off'; available: boolean }
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

// 後端要求修改資料的請求都帶這個標頭（其他網站送不出自訂標頭，用來防止跨站請求）
const CLIENT_HEADER = { 'X-SAgent-Client': 'web' }

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  let res: Response
  try {
    res = await fetch(url, {
      method,
      headers: body === undefined ? CLIENT_HEADER : { ...CLIENT_HEADER, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, '連不到 SAgent 後端服務，請確認服務有啟動')
  }
  if (res.status === 204) return undefined as T
  const text = await res.text()
  let data: unknown = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = null
  }
  if (!res.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail
    throw new ApiError(res.status, typeof detail === 'string' ? detail : `伺服器錯誤（HTTP ${res.status}）`)
  }
  return data as T
}

function qs(params: Record<string, string | number | boolean | null | undefined>) {
  const s = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') s.set(k, String(v))
  const out = s.toString()
  return out ? `?${out}` : ''
}

export const api = {
  // 專案
  projects: () => request<Project[]>('GET', '/api/projects'),
  createProject: (body: { name: string; code?: string; description?: string; phases?: string[] }) =>
    request<Project>('POST', '/api/projects', body),
  projectTree: (id: number, archived = false) => request<ProjectTree>('GET', `/api/projects/${id}${qs({ archived })}`),
  updateProject: (id: number, body: Partial<Pick<Project, 'name' | 'code' | 'description'>>) =>
    request<Project>('PATCH', `/api/projects/${id}`, body),
  deleteProject: (id: number) => request<void>('DELETE', `/api/projects/${id}`),

  // 階段
  createPhase: (projectId: number, body: { name: string; description?: string }) =>
    request<Phase>('POST', `/api/projects/${projectId}/phases`, body),
  updatePhase: (id: number, body: Partial<Pick<Phase, 'name' | 'description' | 'sort_order'>>) =>
    request<Phase>('PATCH', `/api/phases/${id}`, body),
  deletePhase: (id: number) => request<void>('DELETE', `/api/phases/${id}`),
  reorderPhases: (projectId: number, phaseIds: number[]) =>
    request<void>('POST', `/api/projects/${projectId}/phases/reorder`, { phase_ids: phaseIds }),

  // Spec
  searchSpecs: (projectId: number, q: string) => request<Spec[]>('GET', `/api/projects/${projectId}/specs${qs({ q })}`),
  createSpec: (
    projectId: number,
    body: {
      phase_id: number
      title: string
      doc_type: string
      init: 'blank' | 'template' | 'content' | 'none'
      content?: string
      source_spec_id?: number | null
    },
  ) => request<SpecDetail>('POST', `/api/projects/${projectId}/specs`, body),
  spec: (id: number) => request<SpecDetail>('GET', `/api/specs/${id}`),
  updateSpec: (id: number, body: Partial<Pick<Spec, 'title' | 'phase_id' | 'status' | 'doc_type'>>) =>
    request<Spec>('PATCH', `/api/specs/${id}`, body),
  saveContent: (id: number, body: { content: string; message?: string; base_version_no?: number | null }) =>
    request<SaveResult>('PUT', `/api/specs/${id}/content`, body),
  archiveSpec: (id: number) => request<Spec>('POST', `/api/specs/${id}/archive`),
  unarchiveSpec: (id: number) => request<Spec>('POST', `/api/specs/${id}/unarchive`),

  // 版本
  versions: (specId: number) => request<VersionMeta[]>('GET', `/api/specs/${specId}/versions`),
  version: (specId: number, no: number) => request<VersionDetail>('GET', `/api/specs/${specId}/versions/${no}`),
  restoreVersion: (specId: number, no: number, message = '') =>
    request<SaveResult>('POST', `/api/specs/${specId}/versions/${no}/restore`, { message }),
  setLabel: (specId: number, no: number, label: string | null) =>
    request<VersionMeta>('PUT', `/api/specs/${specId}/versions/${no}/label`, { label }),
  diff: (specId: number, fromVersion?: number, toVersion?: number) =>
    request<DiffResult>('GET', `/api/specs/${specId}/diff${qs({ from_version: fromVersion, to_version: toVersion })}`),
  specExportUrl: (specId: number, version?: number) => `/api/specs/${specId}/export${qs({ version })}`,

  // 基準版本
  baselines: (projectId: number) => request<Baseline[]>('GET', `/api/projects/${projectId}/baselines`),
  createBaseline: (projectId: number, body: { name: string; note?: string; phase_id?: number | null }) =>
    request<Baseline>('POST', `/api/projects/${projectId}/baselines`, body),
  baseline: (id: number) => request<{ baseline: Baseline; items: BaselineItem[] }>('GET', `/api/baselines/${id}`),
  deleteBaseline: (id: number) => request<void>('DELETE', `/api/baselines/${id}`),
  compare: (projectId: number, fromBaseline?: number | null, toBaseline?: number | null) =>
    request<{ from: string | null; to: string; items: CompareItem[] }>(
      'GET',
      `/api/projects/${projectId}/compare${qs({ from_baseline: fromBaseline, to_baseline: toBaseline })}`,
    ),
  projectExportUrl: (projectId: number, baselineId?: number) =>
    `/api/projects/${projectId}/export${qs({ baseline_id: baselineId })}`,

  // AI 代理
  templates: () => request<Template[]>('GET', '/api/agent/templates'),
  templateCategories: () => request<TemplateCategory[]>('GET', '/api/agent/template-categories'),
  diagramKinds: () => request<DiagramKind[]>('GET', '/api/agent/diagram-kinds'),
  models: () => request<{ provider: string; default_model: string; models: ModelInfo[] }>('GET', '/api/agent/models'),
  createRun: (body: { spec_id: number; mode: RunMode; requirement: string; doc_type?: string; options?: RunOptions }) =>
    request<Run>('POST', '/api/agent/runs', body),
  runs: (params: { project_id?: number; spec_id?: number; limit?: number }) =>
    request<Run[]>('GET', `/api/agent/runs${qs(params)}`),
  run: (id: number) => request<RunDetail>('GET', `/api/agent/runs/${id}`),
  cancelRun: (id: number) => request<{ cancelled: boolean }>('POST', `/api/agent/runs/${id}/cancel`),
  runEventsUrl: (id: number) => `/api/agent/runs/${id}/events`,

  // 系統
  health: () => request<Health>('GET', '/api/health'),
  settings: () => request<Settings>('GET', '/api/settings'),
  updateSettings: (body: Partial<Settings>) => request<Settings>('PUT', '/api/settings', body),
  renderPlantUml: async (source: string): Promise<string> => {
    const res = await fetch('/api/render/plantuml', {
      method: 'POST',
      headers: { ...CLIENT_HEADER, 'Content-Type': 'application/json' },
      body: JSON.stringify({ source }),
    })
    if (!res.ok) {
      let detail = `HTTP ${res.status}`
      try {
        detail = (await res.json()).detail ?? detail
      } catch {
        /* 不是 JSON */
      }
      throw new ApiError(res.status, detail)
    }
    return res.text()
  },
}
