// 設定：模型連線、AI 預設選項、PlantUML、文件範本

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useBlocker, useNavigate } from 'react-router'
import { api, type Settings, type UmlSyntax } from '../api'
import { KIND_NAMES } from '../kinds'
import { Badge, ConfirmDialog, errorText, Field, Skeleton, Spinner, useToast } from '../components/ui'
import { formatBytes, UML_SYNTAX } from '../format'
import { keys, useHealth, useModels, useSettings, useTemplates } from '../queries'

export function SettingsPage() {
  const settings = useSettings()
  const health = useHealth()
  const models = useModels()
  const templates = useTemplates()
  const qc = useQueryClient()
  const toast = useToast()
  const [form, setForm] = useState<Settings | null>(null)
  const navigate = useNavigate()
  const dirty = !!form && !!settings.data && JSON.stringify(form) !== JSON.stringify(settings.data)

  // 有未儲存的修改時，離開頁面要確認
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname)
  // 確認離開後 ConfirmDialog 還會呼叫 onClose，這時的 blocker 還是舊的，不能再 reset
  const leaving = useRef(false)
  useEffect(() => {
    if (!dirty) return
    const onBeforeUnload = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [dirty])

  /** 回到上一頁；直接開設定頁（沒有上一頁）時回專案清單 */
  const leave = () => {
    const hasHistory = ((window.history.state as { idx?: number } | null)?.idx ?? 0) > 0
    if (hasHistory) navigate(-1)
    else navigate('/')
  }

  useEffect(() => {
    if (settings.data && !form) setForm(settings.data)
  }, [settings.data, form])

  const save = useMutation({
    mutationFn: (body: Settings) => api.updateSettings(body),
    onSuccess: (s) => {
      setForm(s)
      qc.invalidateQueries({ queryKey: keys.settings })
      qc.invalidateQueries({ queryKey: keys.health })
      qc.invalidateQueries({ queryKey: keys.models })
      toast('設定已儲存', 'success')
    },
    onError: (e) => toast(errorText(e), 'error'),
  })

  if (!form)
    return (
      <div className="page page-narrow">
        <div className="card"><Skeleton lines={4} /></div>
        <div className="card"><Skeleton lines={4} /></div>
      </div>
    )
  const set = <K extends keyof Settings>(k: K, v: Settings[K]) => setForm({ ...form, [k]: v })
  const h = health.data

  return (
    <div className="page page-narrow">
      <div className="settings-head">
        <h1>設定</h1>
        {dirty && <Badge tone="amber">有未儲存的修改</Badge>}
        <span className="spacer" />
        <button className="btn" onClick={leave}>
          取消
        </button>
        <button className="btn btn-primary" disabled={save.isPending} onClick={() => save.mutate(form)}>
          儲存設定
        </button>
      </div>

      <section className="card">
        <div className="card-title">系統狀態</div>
        {!h ? (
          <Spinner small />
        ) : (
          <dl className="kv">
            <dt>模型來源</dt>
            <dd>{h.llm_provider === 'fake' ? <Badge tone="amber">示範模式（假模型）</Badge> : 'Ollama（本機）'}</dd>
            {h.llm_provider === 'ollama' && (
              <>
                <dt>Ollama</dt>
                <dd>
                  {h.ollama.reachable ? <Badge tone="green">已連線 {h.ollama.version}</Badge> : <Badge tone="red">未連線</Badge>}{' '}
                  <span className="muted small">{h.ollama.url}</span>
                  {h.ollama.error && <div className="error-text small">{h.ollama.error}</div>}
                </dd>
              </>
            )}
            <dt>PlantUML 渲染</dt>
            <dd>
              {h.plantuml.available ? (
                <Badge tone="green">{h.plantuml.mode === 'server' ? 'PlantUML 伺服器' : '本機 Java + plantuml.jar'}</Badge>
              ) : (
                <Badge tone="neutral">未啟用（PlantUML 圖只顯示原始碼）</Badge>
              )}
            </dd>
            <dt>SAgent 版本</dt>
            <dd>{h.version}</dd>
          </dl>
        )}
      </section>

      <section className="card">
        <div className="card-title">AI 模型</div>
        <Field label="Ollama 網址">
          <input className="input" value={form.ollama_url} onChange={(e) => set('ollama_url', e.target.value)} />
        </Field>
        <Field
          label="預設模型"
          hint="CPU 推論時，MoE 模型（名稱有 a3b 之類）通常比同大小的一般模型快。每次 AI 撰寫時也可以另外選。"
        >
          <select className="input" value={form.default_model} onChange={(e) => set('default_model', e.target.value)}>
            {!models.data?.models.some((m) => m.name === form.default_model) && <option value={form.default_model}>{form.default_model}</option>}
            {models.data?.models.map((m) => (
              <option key={m.name} value={m.name}>
                {m.name}
                {m.parameter_size ? `・${m.parameter_size}` : ''}
                {m.quantization ? `・${m.quantization}` : ''}
                {m.size ? `・${formatBytes(m.size)}` : ''}
              </option>
            ))}
          </select>
        </Field>
        <div className="form-row">
          <Field label="溫度（0～1.5）" hint="越低越穩定，越高越有變化">
            <input className="input" type="number" step={0.1} min={0} max={1.5} value={form.temperature} onChange={(e) => set('temperature', Number(e.target.value))} />
          </Field>
          <Field label="Context 長度" hint="0 = 用 Ollama 的設定。改了會讓 Ollama 重新載入模型">
            <input className="input" type="number" step={1024} min={0} value={form.num_ctx} onChange={(e) => set('num_ctx', Number(e.target.value))} />
          </Field>
        </div>
      </section>

      <section className="card">
        <div className="card-title">AI 撰寫預設值</div>
        <div className="form-row">
          <Field label="審查後最多修訂幾輪" hint="0 = 只審查不修訂（最快）">
            <input className="input" type="number" min={0} max={3} value={form.max_revisions} onChange={(e) => set('max_revisions', Number(e.target.value))} />
          </Field>
          <Field label="畫圖">
            <select className="input" value={String(form.diagrams)} onChange={(e) => set('diagrams', e.target.value === 'true')}>
              <option value="true">要</option>
              <option value="false">不要</option>
            </select>
          </Field>
        </div>
        <Field
          label="UML 圖語法"
          hint="自動：都用 Mermaid（前端直接畫）；有設定 PlantUML 渲染時，使用案例圖、元件圖、部署圖改用 PlantUML。"
        >
          <select className="input" value={form.uml_syntax} onChange={(e) => set('uml_syntax', e.target.value as UmlSyntax)}>
            {Object.entries(UML_SYNTAX).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </Field>
      </section>

      <section className="card">
        <div className="card-title">PlantUML（選用）</div>
        <Field
          label="PlantUML 伺服器網址"
          hint="例如自架的 http://localhost:8080（docker run -p 8080:8080 plantuml/plantuml-server）。空白表示不用伺服器。也可以安裝 Java 並把 plantuml.jar 放在 tools/ 資料夾。注意：填公開的伺服器會把圖的內容送到外部。"
        >
          <input className="input" value={form.plantuml_server} placeholder="http://localhost:8080" onChange={(e) => set('plantuml_server', e.target.value)} />
        </Field>
      </section>

      <section className="card mt">
        <div className="card-title">文件範本</div>
        <p className="muted small">
          AI 撰寫時，Planner 會參考範本的建議章節。可以在 <code>data/templates/</code> 放自己的 YAML 範本（格式同 <code>backend/sagent/doc_templates/</code>），key 相同時會取代內建範本，不用重新啟動。
        </p>
        {templates.data?.map((t) => (
          <details key={t.key} className="template-item">
            <summary>
              <strong>{t.name}</strong> <span className="muted small">{t.key}</span> {!t.builtin && <Badge tone="violet">自訂</Badge>}{' '}
              {t.advice && <Badge tone="blue">建議類</Badge>} {!t.allow_diagrams && <Badge>不畫圖</Badge>}
            </summary>
            <p className="muted small">{t.description}</p>
            <ol className="small">
              {t.sections.map((s) => (
                <li key={s.heading}>
                  <b>{s.heading}</b>：{s.goal}
                  {s.diagrams.length > 0 && <span className="muted">（圖：{s.diagrams.map((d) => KIND_NAMES[d] ?? d).join('、')}）</span>}
                </li>
              ))}
            </ol>
          </details>
        ))}
      </section>

      {blocker.state === 'blocked' && (
        <ConfirmDialog
          title="還沒儲存"
          message="設定有未儲存的修改，離開後修改會不見。確定要離開嗎？"
          confirmText="不儲存，直接離開"
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
