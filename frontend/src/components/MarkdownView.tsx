// Markdown 預覽：一般 Markdown + 表格（GFM）+ Mermaid 圖 + PlantUML 圖

import { Children, isValidElement, memo, useEffect, useState, type ReactElement, type ReactNode } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api, ApiError } from '../api'
import { slugify } from '../format'
import { useIsDark } from '../theme'

// ---------------------------------------------------------------------------
// Mermaid（第一次用到才載入，因為檔案很大）
// ---------------------------------------------------------------------------

type MermaidApi = typeof import('mermaid').default
let mermaidReady: Promise<MermaidApi> | null = null
let mermaidTheme = ''
let renderSeq = 0

async function getMermaid(dark: boolean): Promise<MermaidApi> {
  mermaidReady ??= import('mermaid').then((m) => m.default)
  const mermaid = await mermaidReady
  const theme = dark ? 'dark' : 'default'
  if (theme !== mermaidTheme) {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
      theme,
      fontFamily: 'var(--font-sans)',
    })
    mermaidTheme = theme
  }
  return mermaid
}

function DiagramError({ title, message, code }: { title: string; message: string; code: string }) {
  return (
    <div className="diagram-error">
      <div className="diagram-error-title">{title}</div>
      <div className="diagram-error-msg">{message}</div>
      <pre>
        <code>{code}</code>
      </pre>
    </div>
  )
}

export const MermaidDiagram = memo(function MermaidDiagram({ code }: { code: string }) {
  const dark = useIsDark()
  const [svg, setSvg] = useState('')
  const [error, setError] = useState('')
  useEffect(() => {
    let cancelled = false
    const id = `mmd-${++renderSeq}`
    // 編輯時每打一個字都會變，稍等一下再畫
    const timer = setTimeout(async () => {
      try {
        const mermaid = await getMermaid(dark)
        const result = await mermaid.render(id, code)
        if (!cancelled) {
          setSvg(result.svg)
          setError('')
        }
      } catch (e) {
        document.getElementById(`d${id}`)?.remove()
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      }
    }, 250)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [code, dark])
  if (error) return <DiagramError title="Mermaid 圖表語法錯誤" message={error.split('\n').slice(0, 4).join('\n')} code={code} />
  if (!svg) return <div className="diagram diagram-loading">圖表繪製中…</div>
  // Mermaid 在 strict 模式下輸出的 SVG 已經過濾過
  return <div className="diagram" dangerouslySetInnerHTML={{ __html: svg }} />
})

// ---------------------------------------------------------------------------
// PlantUML（由後端轉給 PlantUML 伺服器或本機 jar）
// ---------------------------------------------------------------------------

const plantumlCache = new Map<string, string>()

export const PlantUmlDiagram = memo(function PlantUmlDiagram({ code }: { code: string }) {
  const [url, setUrl] = useState(() => plantumlCache.get(code) ?? '')
  const [error, setError] = useState<{ off: boolean; message: string } | null>(null)
  useEffect(() => {
    if (plantumlCache.has(code)) {
      setUrl(plantumlCache.get(code)!)
      setError(null)
      return
    }
    let cancelled = false
    const timer = setTimeout(async () => {
      try {
        const svg = await api.renderPlantUml(code)
        const objectUrl = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }))
        plantumlCache.set(code, objectUrl)
        if (!cancelled) {
          setUrl(objectUrl)
          setError(null)
        }
      } catch (e) {
        if (!cancelled) setError({ off: e instanceof ApiError && e.status === 503, message: e instanceof Error ? e.message : String(e) })
      }
    }, 400)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [code])
  if (error)
    return (
      <div className="diagram-plantuml-source">
        <div className="diagram-note">
          {error.off ? 'PlantUML 圖：目前沒有設定 PlantUML 渲染，先顯示原始碼（可在「設定」填 PlantUML 伺服器）' : `PlantUML 渲染失敗：${error.message}`}
        </div>
        <pre>
          <code>{code}</code>
        </pre>
      </div>
    )
  if (!url) return <div className="diagram diagram-loading">PlantUML 圖繪製中…</div>
  return (
    <div className="diagram">
      <img src={url} alt="PlantUML 圖" />
    </div>
  )
})

// ---------------------------------------------------------------------------

function textOf(node: ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(textOf).join('')
  if (isValidElement(node)) return textOf((node.props as { children?: ReactNode }).children)
  return ''
}

function heading(level: 1 | 2 | 3 | 4) {
  const Tag = `h${level}` as const
  return function Heading({ children }: { children?: ReactNode }) {
    return <Tag id={slugify(textOf(children))}>{children}</Tag>
  }
}

const components: Components = {
  h1: heading(1),
  h2: heading(2),
  h3: heading(3),
  h4: heading(4),
  pre({ children }) {
    const child = Children.toArray(children)[0]
    if (isValidElement(child)) {
      const props = (child as ReactElement<{ className?: string; children?: ReactNode }>).props
      const lang = /language-([\w-]+)/.exec(props.className ?? '')?.[1]?.toLowerCase()
      const code = textOf(props.children).replace(/\n$/, '')
      if (lang === 'mermaid') return <MermaidDiagram code={code} />
      if (lang === 'plantuml' || lang === 'puml' || lang === 'uml') return <PlantUmlDiagram code={code} />
    }
    return <pre>{children}</pre>
  },
  a({ href, children }) {
    const external = href && /^https?:/.test(href)
    return (
      <a href={href} target={external ? '_blank' : undefined} rel={external ? 'noreferrer' : undefined}>
        {children}
      </a>
    )
  },
  table({ children }) {
    return (
      <div className="table-wrap">
        <table>{children}</table>
      </div>
    )
  },
}

export const MarkdownView = memo(function MarkdownView({ markdown, className }: { markdown: string; className?: string }) {
  return (
    <div className={`markdown ${className ?? ''}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {markdown}
      </ReactMarkdown>
    </div>
  )
})
