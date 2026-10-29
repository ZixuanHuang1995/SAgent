import { createBrowserRouter, Link, Outlet, RouterProvider, useMatch, useParams, useRouteError } from 'react-router'
import { Icon } from './components/icons'
import logoUrl from './logo.svg'
import { useHealth, useProjectTree } from './queries'
import { useThemePref, type ThemePref } from './theme'
import { BaselinesPage } from './pages/BaselinesPage'
import { ProjectHome } from './pages/ProjectHome'
import { ProjectLayout } from './pages/ProjectLayout'
import { ProjectsPage } from './pages/ProjectsPage'
import { RunsPage } from './pages/RunsPage'
import { SettingsPage } from './pages/SettingsPage'
import { SpecPage } from './pages/SpecPage'

function HealthIndicator() {
  const health = useHealth()
  if (!health.data) return <span className="health health-unknown">檢查中…</span>
  const h = health.data
  if (h.llm_provider === 'fake')
    return (
      <Link to="/settings" className="health health-warn" title="SAGENT_LLM_PROVIDER=fake：不接模型，用固定內容展示流程">
        示範模式（假模型）
      </Link>
    )
  return h.ollama.reachable ? (
    <Link to="/settings" className="health health-ok" title={`Ollama ${h.ollama.version}（${h.ollama.url}）`}>
      Ollama 已連線・{h.default_model}
    </Link>
  ) : (
    <Link to="/settings" className="health health-bad" title={h.ollama.error}>
      Ollama 未連線
    </Link>
  )
}

const THEME_NEXT: Record<ThemePref, ThemePref> = { system: 'light', light: 'dark', dark: 'system' }
const THEME_LABEL: Record<ThemePref, string> = { system: '跟著系統', light: '淺色', dark: '深色' }
const THEME_ICON = { system: 'monitor', light: 'sun', dark: 'moon' } as const

/** 深淺色切換：跟著系統 → 淺色 → 深色 */
function ThemeToggle() {
  const [pref, setPref] = useThemePref()
  const next = THEME_NEXT[pref]
  return (
    <button
      className="icon-btn topbar-icon"
      onClick={() => setPref(next)}
      title={`顏色：${THEME_LABEL[pref]}（按一下改成${THEME_LABEL[next]}）`}
      aria-label={`顏色：${THEME_LABEL[pref]}，按一下改成${THEME_LABEL[next]}`}
    >
      <Icon name={THEME_ICON[pref]} />
    </button>
  )
}

function Breadcrumb() {
  const { projectId } = useParams()
  const onSettings = useMatch('/settings')
  const tree = useProjectTree(Number(projectId ?? 0))
  if (onSettings)
    return (
      <>
        <span className="crumb-sep">/</span>
        <span className="crumb" aria-current="page">
          設定
        </span>
      </>
    )
  if (!projectId || !tree.data) return null
  return (
    <>
      <span className="crumb-sep">/</span>
      <Link to={`/p/${projectId}`} className="crumb">
        {tree.data.project.name}
      </Link>
    </>
  )
}

function Shell() {
  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand">
          <img src={logoUrl} alt="" width={22} height={22} />
          <span>SAgent</span>
          <span className="brand-sub">系統分析代理</span>
        </Link>
        <Breadcrumb />
        <span className="spacer" />
        <HealthIndicator />
        <ThemeToggle />
        <Link to="/settings" className="topbar-link">
          設定
        </Link>
      </header>
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  )
}

function ErrorPage() {
  const error = useRouteError() as Error | undefined
  return (
    <div className="page">
      <h1>畫面發生錯誤</h1>
      <p className="error-box">{error?.message ?? String(error)}</p>
      <Link to="/" className="btn">
        回首頁
      </Link>
    </div>
  )
}

function SpecRoute() {
  const { specId } = useParams()
  // 換文件時重新建立元件，避免上一份文件的編輯狀態留著
  return <SpecPage key={specId} />
}

const router = createBrowserRouter([
  {
    element: <Shell />,
    errorElement: <ErrorPage />,
    children: [
      { path: '/', element: <ProjectsPage /> },
      { path: '/settings', element: <SettingsPage /> },
      {
        path: '/p/:projectId',
        element: <ProjectLayout />,
        children: [
          { index: true, element: <ProjectHome /> },
          { path: 's/:specId', element: <SpecRoute /> },
          { path: 'baselines', element: <BaselinesPage /> },
          { path: 'runs', element: <RunsPage /> },
        ],
      },
      { path: '*', element: <ErrorPage /> },
    ],
  },
])

export function App() {
  return <RouterProvider router={router} />
}
