// 資料查詢（TanStack Query）。key 統一放這裡，存檔後才知道要更新哪些畫面。

import { useQuery } from '@tanstack/react-query'
import { api } from './api'

export const keys = {
  projects: ['projects'] as const,
  tree: (projectId: number, archived = false) => ['tree', projectId, archived] as const,
  spec: (specId: number) => ['spec', specId] as const,
  versions: (specId: number) => ['versions', specId] as const,
  runs: (params: { project_id?: number; spec_id?: number }) => ['runs', params] as const,
  run: (runId: number) => ['run', runId] as const,
  baselines: (projectId: number) => ['baselines', projectId] as const,
  templates: ['templates'] as const,
  templateCategories: ['template-categories'] as const,
  diagramKinds: ['diagram-kinds'] as const,
  models: ['models'] as const,
  settings: ['settings'] as const,
  health: ['health'] as const,
}

export const useProjects = () =>
  useQuery({
    queryKey: keys.projects,
    queryFn: api.projects,
    // 有 AI 工作在跑或排隊時，定時更新首頁卡片上的狀態
    refetchInterval: (query) =>
      query.state.data?.some((p) => p.running_runs > 0 || p.queued_runs > 0) ? 5000 : false,
  })

export const useProjectTree = (projectId: number, archived = false) =>
  useQuery({
    queryKey: keys.tree(projectId, archived),
    queryFn: () => api.projectTree(projectId, archived),
    enabled: projectId > 0,
    // AI 工作可能在別的頁面完成，定期更新版本號
    refetchInterval: 30_000,
  })

export const useSpec = (specId: number) =>
  useQuery({
    queryKey: keys.spec(specId),
    queryFn: () => api.spec(specId),
    // AI 執行中時定期更新，即使 AI 面板沒有打開，結束後也能拿到新版本
    refetchInterval: (query) => (query.state.data?.active_run_id ? 5000 : false),
  })

export const useVersions = (specId: number) =>
  useQuery({ queryKey: keys.versions(specId), queryFn: () => api.versions(specId) })

export const useRuns = (params: { project_id?: number; spec_id?: number }, refetchMs?: number) =>
  useQuery({ queryKey: keys.runs(params), queryFn: () => api.runs(params), refetchInterval: refetchMs })

export const useBaselines = (projectId: number) =>
  useQuery({ queryKey: keys.baselines(projectId), queryFn: () => api.baselines(projectId) })

export const useTemplates = () => useQuery({ queryKey: keys.templates, queryFn: api.templates, staleTime: 60_000 })

export const useTemplateCategories = () =>
  useQuery({ queryKey: keys.templateCategories, queryFn: api.templateCategories, staleTime: Infinity })

export const useDiagramKinds = () =>
  useQuery({ queryKey: keys.diagramKinds, queryFn: api.diagramKinds, staleTime: Infinity })

export const useModels = () => useQuery({ queryKey: keys.models, queryFn: api.models, staleTime: 60_000, retry: false })

export const useSettings = () => useQuery({ queryKey: keys.settings, queryFn: api.settings })

export const useHealth = () =>
  useQuery({ queryKey: keys.health, queryFn: api.health, refetchInterval: 30_000, retry: false })
