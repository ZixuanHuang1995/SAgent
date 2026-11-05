// 設定：系統狀態、模型連線、AI 撰寫選項、文件範本

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useBlocker, useNavigate } from 'react-router'
import { api, type ModelInfo, type OllamaCheck, type Settings, type Template, type UmlSyntax } from '../api'
import { KIND_NAMES } from '../kinds'
import { Badge, ConfirmDialog, errorText, Field, Skeleton, Spinner, useToast } from '../components/ui'
import { formatBytes, UML_SYNTAX } from '../format'
import { keys, useHealth, useModels, useSettings, useTemplateCategories, useTemplates } from '../queries'

/** 審查要讀整份文件，上下文低於這個值時常不夠用 */
const SUGGESTED_CTX = 16384
const CTX_PRESETS = [0, 8192, 16384, 32768]
const MAX_CTX = 262144

/** 數字欄位用字串存，清空時才看得出來，不會悄悄變成 0 */
type Form = Omit<Settings, 'temperature' | 'num_ctx'> & { temperature: string; num_ctx: string }
type Errors = Partial<Record<'ollama_url' | 'temperature' | 'num_ctx', string>>

const toForm = (s: Settings): Form => ({ ...s, temperature: String(s.temperature), num_ctx: String(s.num_ctx) })
const normalizeUrl = (url: string) => url.trim().replace(/\/+$/, '')

function validate(f: Form): Errors {
  const errors: Errors = {}
  if (!/^https?:\/\/[^/\s]+/i.test(f.ollama_url.trim())) errors.ollama_url = '要用 http:// 或 https:// 開頭，例如 http://127.0.0.1:11434'
  const t = f.temperature.trim() === '' ? NaN : Number(f.temperature)
  if (Number.isNaN(t)) errors.temperature = '請填數字'
  else if (t < 0 || t > 1.5) errors.temperature = '要在 0～1.5 之間'
  const c = f.num_ctx.trim() === '' ? NaN : Number(f.num_ctx)
  if (!Number.isInteger(c)) errors.num_ctx = '請填整數'
  else if (c < 0 || c > MAX_CTX) errors.num_ctx = `要在 0～${MAX_CTX} 之間`
  return errors
}

function toSettings(f: Form): Settings {
  return { ...f, ollama_url: normalizeUrl(f.ollama_url), temperature: Number(f.temperature), num_ctx: Number(f.num_ctx) }
}

/** 欄位名稱和一組按鈕：不能用 Field（label），點名稱會按到第一個按鈕 */
function FieldGroup(props: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="field" role="group" aria-label={props.label}>
      <span className="field-label">{props.label}</span>
      {props.children}
      {props.hint && <span className="field-hint">{props.hint}</span>}
    </div>
  )
}

function modelLabel(m: ModelInfo) {
  return [m.name, m.parameter_size, m.quantization, m.size ? formatBytes(m.size) : ''].filter(Boolean).join('・')
}

export function SettingsPage() {
  const settings = useSettings()
  const health = useHealth()
  const models = useModels()
  const templates = useTemplates()
  const categories = useTemplateCategories()
  const qc = useQueryClient()
  const toast = useToast()
  const [form, setForm] = useState<Form | null>(null)
  // Context 長度選了「自訂」：數字剛好等於預設選項時也維持自訂，輸入框才不會打到一半消失
  const [ctxCustom, setCtxCustom] = useState(false)
  const [check, setCheck] = useState<OllamaCheck | null>(null)
  const navigate = useNavigate()
  const saved = settings.data ? toForm(settings.data) : null
  const dirty = !!form && !!saved && JSON.stringify(form) !== JSON.stringify(saved)
  const errors = form ? validate(form) : {}
  const hasErrors = Object.keys(errors).length > 0

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
    if (settings.data && !form) {
      setForm(toForm(settings.data))
      setCtxCustom(!CTX_PRESETS.includes(settings.data.num_ctx))
    }
  }, [settings.data, form])

  const save = useMutation({
    mutationFn: (body: Settings) => api.updateSettings(body),
    onSuccess: (s) => {
      setForm(toForm(s))
      setCtxCustom(!CTX_PRESETS.includes(s.num_ctx))
      qc.invalidateQueries({ queryKey: keys.settings })
      qc.invalidateQueries({ queryKey: keys.health })
      qc.invalidateQueries({ queryKey: keys.models })
      toast('設定已儲存', 'success')
    },
    onError: (e) => toast(errorText(e), 'error'),
  })

  const testConnection = useMutation({
    mutationFn: (url: string) => api.checkOllama(url),
    onSuccess: (r) => setCheck(r),
    onError: (e) => toast(errorText(e), 'error'),
  })

  const canSave = dirty && !hasErrors && !save.isPending
  const submit = () => {
    if (form && canSave) save.mutate(toSettings(form))
  }
  // Ctrl+S 儲存，跟 Spec 頁一樣
  const submitRef = useRef(submit)
  submitRef.current = submit
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault()
        submitRef.current()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  if (!form || !saved)
    return (
      <div className="page page-narrow">
        <div className="card"><Skeleton lines={4} /></div>
        <div className="card"><Skeleton lines={4} /></div>
      </div>
    )
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setForm({ ...form, [k]: v })
  const h = health.data
  const fake = h?.llm_provider === 'fake'
  const ollamaUp = h?.llm_provider === 'ollama' && h.ollama.reachable
  const plantumlOn = !!h?.plantuml.available

  // 模型清單：網址改過、測試連線成功時用新網址的清單，否則用已存的網址
  const url = normalizeUrl(form.ollama_url)
  const urlChanged = url !== saved.ollama_url
  const checked = check && check.url === url ? check : null
  const modelList = checked?.reachable ? checked.models : urlChanged ? null : (models.data?.models ?? null)
  const modelMissing = !!modelList && !fake && !modelList.some((m) => m.name === form.default_model)

  // 上下文長度：設 0 時看已載入的模型實際用多少
  const loaded = h?.ollama.loaded?.find((m) => m.name === form.default_model)
  const ctxSetting = Number(form.num_ctx)
  const ctxActual = ctxSetting > 0 ? ctxSetting : loaded?.context_length || 0
  const ctxTooSmall = !fake && !errors.num_ctx && ctxActual > 0 && ctxActual < SUGGESTED_CTX
  const ctxSelect = ctxCustom ? 'custom' : form.num_ctx

  const revisions = form.max_revisions
  const groups = groupTemplates(templates.data ?? [], categories.data ?? [])

  return (
    <>
      <div className="settings-bar">
        <div className="settings-head">
          <h1>設定</h1>
          {dirty && <Badge tone="amber">有未儲存的修改</Badge>}
          <span className="spacer" />
          <button className="btn" onClick={leave}>
            返回
          </button>
          {dirty && (
            <button
              className="btn"
              onClick={() => {
                setForm(saved)
                setCtxCustom(!CTX_PRESETS.includes(Number(saved.num_ctx)))
              }}
            >
              還原
            </button>
          )}
          <button
            className="btn btn-primary"
            disabled={!canSave}
            title={hasErrors ? '有欄位要修正' : 'Ctrl+S'}
            onClick={submit}
          >
            {save.isPending ? <Spinner small /> : '儲存設定'}
          </button>
        </div>
      </div>
      <div className="page page-narrow">
        <section className="card">
          <div className="card-title">系統狀態</div>
          {!h ? (
            <Spinner small />
          ) : (
            <dl className="kv">
              <dt>模型來源</dt>
              <dd>{fake ? <Badge tone="amber">示範模式（假模型）</Badge> : 'Ollama（本機）'}</dd>
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
              {ollamaUp && (
                <>
                  <dt>已載入的模型</dt>
                  <dd>
                    {h.ollama.loaded?.length ? (
                      h.ollama.loaded.map((m) => (
                        <div key={m.name}>
                          {m.name}
                          <span className="muted small">
                            ・上下文 {m.context_length || 'Ollama 沒有回報'}
                          </span>{' '}
                          {m.context_length > 0 && m.context_length < SUGGESTED_CTX && <Badge tone="amber">可能不夠用</Badge>}
                        </div>
                      ))
                    ) : (
                      <span className="muted">沒有（跑 AI 工作時才會載入，之後才看得到實際的上下文長度）</span>
                    )}
                  </dd>
                </>
              )}
              <dt>PlantUML 渲染</dt>
              <dd>
                {plantumlOn ? (
                  <Badge tone="green">{h.plantuml.mode === 'server' ? 'PlantUML 伺服器' : '本機 Java + plantuml.jar'}</Badge>
                ) : (
                  <>
                    <Badge tone="neutral">未啟用（PlantUML 圖只顯示原始碼）</Badge>
                    <div className="muted small mt-xs">
                      要啟用：用環境變數 <code>SAGENT_PLANTUML_SERVER</code> 指定自架的 PlantUML 伺服器後重新啟動，或安裝 Java 並放{' '}
                      <code>tools/plantuml.jar</code>。不要用公開的伺服器，圖的內容會送到外部。
                    </div>
                  </>
                )}
              </dd>
              <dt>資料資料夾</dt>
              <dd className="mono small path">{h.data_dir}</dd>
              <dt>SAgent 版本</dt>
              <dd>{h.version}</dd>
            </dl>
          )}
        </section>

        <section className="card">
          <div className="card-title">AI 模型</div>
          {fake && (
            <div className="info-box small">
              示範模式用假模型，這張卡的設定不會用到。要連 Ollama，啟動時不要加 <code>--fake-llm</code>。
            </div>
          )}
          <Field
            label="Ollama 網址"
            error={errors.ollama_url}
            hint={
              checked ? (
                checked.reachable ? (
                  <Badge tone="green">
                    已連線 {checked.version}・{checked.models.length} 個模型
                  </Badge>
                ) : (
                  <span className="error-text">{checked.error}</span>
                )
              ) : urlChanged ? (
                '網址改了：按「測試連線」確認連得到，預設模型的清單也會換成這個網址的'
              ) : undefined
            }
          >
            <span className="input-with-btn">
              <input
                className={`input${errors.ollama_url ? ' invalid' : ''}`}
                value={form.ollama_url}
                onChange={(e) => set('ollama_url', e.target.value)}
              />
              <button
                className="btn"
                type="button"
                disabled={!!errors.ollama_url || testConnection.isPending}
                onClick={() => testConnection.mutate(url)}
              >
                {testConnection.isPending ? <Spinner small /> : '測試連線'}
              </button>
            </span>
          </Field>
          <Field
            label="預設模型"
            error={modelMissing ? '這個 Ollama 沒有這個模型，請換一個，或先用 ollama pull 下載' : undefined}
            hint="預設是 MoE 模型 qwen3.6:35b-a3b，在 CPU 上比 8B 快、品質也比較好，但需要約 22GB 記憶體；記憶體不到 32GB 改用 qwen3:8b。"
          >
            <select className="input" value={form.default_model} onChange={(e) => set('default_model', e.target.value)}>
              {!modelList?.some((m) => m.name === form.default_model) && <option value={form.default_model}>{form.default_model}</option>}
              {modelList?.map((m) => (
                <option key={m.name} value={m.name}>
                  {modelLabel(m)}
                </option>
              ))}
            </select>
          </Field>
          <Field
            label="審查模型"
            error={form.reviewer_model && modelList && !fake && !modelList.some((m) => m.name === form.reviewer_model) ? '這個 Ollama 沒有這個模型，請換一個，或先用 ollama pull 下載' : undefined}
            hint="Reviewer 用的模型。選「跟預設模型相同」就跟撰寫用同一個；用不同的模型審查，比較不會偏好自己寫的內容，但兩個模型都要載入，記憶體要夠。"
          >
            <select className="input" value={form.reviewer_model} onChange={(e) => set('reviewer_model', e.target.value)}>
              <option value="">跟預設模型相同</option>
              {form.reviewer_model && !modelList?.some((m) => m.name === form.reviewer_model) && <option value={form.reviewer_model}>{form.reviewer_model}</option>}
              {modelList?.map((m) => (
                <option key={m.name} value={m.name}>
                  {modelLabel(m)}
                </option>
              ))}
            </select>
          </Field>
          <div className="form-row">
            <Field label="溫度（0～1.5）" error={errors.temperature} hint="越低越穩定，越高越有變化">
              <input
                className={`input${errors.temperature ? ' invalid' : ''}`}
                type="number"
                step={0.1}
                min={0}
                max={1.5}
                value={form.temperature}
                onChange={(e) => set('temperature', e.target.value)}
              />
            </Field>
            <Field
              label="Context 長度"
              error={errors.num_ctx}
              hint={
                ctxTooSmall ? (
                  <span className="warn-text">
                    {ctxSetting > 0 ? '' : '目前實際 '}
                    {ctxActual}，審查要讀整份文件，常常不夠用，建議選 {SUGGESTED_CTX}。
                  </span>
                ) : (
                  `審查要讀整份文件，建議 ${SUGGESTED_CTX} 以上。改了之後 Ollama 要重新載入模型，下一次 AI 撰寫會慢一點。`
                )
              }
            >
              <select
                className="input"
                value={ctxSelect}
                onChange={(e) => {
                  const v = e.target.value
                  if (v === 'custom') {
                    setCtxCustom(true)
                    if (form.num_ctx === '0') set('num_ctx', String(SUGGESTED_CTX))
                  } else {
                    setCtxCustom(false)
                    set('num_ctx', v)
                  }
                }}
              >
                <option value="0">由 Ollama 決定（沒有 GPU 時通常只有 4096）</option>
                <option value="8192">8192</option>
                <option value="16384">16384（建議）</option>
                <option value="32768">32768</option>
                <option value="custom">自訂…</option>
              </select>
              {ctxCustom && (
                <input
                  className={`input mt-xs${errors.num_ctx ? ' invalid' : ''}`}
                  type="number"
                  step={1024}
                  min={0}
                  max={MAX_CTX}
                  aria-label="自訂 Context 長度"
                  value={form.num_ctx}
                  onChange={(e) => set('num_ctx', e.target.value)}
                />
              )}
            </Field>
          </div>
        </section>

        <section className="card">
          <div className="card-title">AI 撰寫選項</div>
          <p className="muted small mt-0">
            每次 AI 撰寫都用這裡的值，Spec 頁的 AI 面板不另外選。改了之後，下一個開始的 AI 工作才會套用。
          </p>
          <FieldGroup
            label="審查後最多修訂幾輪"
            hint={
              revisions === 0
                ? '只審查不修訂，最快。審查找到的問題留在審查報告，自己改'
                : `審查最多 ${revisions + 1} 次。每多一輪，CPU 上約多十幾分鐘；修訂後錯誤沒有變少時會提早停止`
            }
          >
            <div className="seg seg-sm">
              {[0, 1, 2, 3].map((n) => (
                <button key={n} type="button" className={revisions === n ? 'active' : ''} aria-pressed={revisions === n} onClick={() => set('max_revisions', n)}>
                  {n === 0 ? '不修訂' : `${n} 輪`}
                </button>
              ))}
            </div>
          </FieldGroup>
          <label className="check-row mb">
            <input type="checkbox" checked={form.diagrams} onChange={(e) => set('diagrams', e.target.checked)} />
            <span>
              AI 撰寫時畫圖
              <span className="muted small check-row-hint">
                不畫圖可以省下不少時間。範本設定不畫圖的文件（例如 UI/UX 設計建議）不管這裡怎麼選都不畫。
              </span>
            </span>
          </label>
          <Field
            label="UML 圖語法"
            error={
              form.diagrams && form.uml_syntax === 'plantuml' && !plantumlOn
                ? 'PlantUML 沒有啟用，選這個 UML 圖只會顯示原始碼'
                : undefined
            }
            hint={
              !form.diagrams
                ? '不畫圖時用不到。'
                : plantumlOn
                  ? '自動：都用 Mermaid（前端直接畫）；使用案例圖、元件圖、部署圖改用 PlantUML。'
                  : 'PlantUML 沒有啟用，「自動」等於全部用 Mermaid（前端直接畫）。'
            }
          >
            <select
              className="input"
              value={form.uml_syntax}
              disabled={!form.diagrams}
              onChange={(e) => set('uml_syntax', e.target.value as UmlSyntax)}
            >
              {Object.entries(UML_SYNTAX).map(([k, v]) => (
                <option key={k} value={k} disabled={k === 'plantuml' && !plantumlOn && form.uml_syntax !== 'plantuml'}>
                  {v}
                  {k === 'plantuml' && !plantumlOn ? '（未啟用）' : ''}
                </option>
              ))}
            </select>
          </Field>
        </section>

        <section className="card">
          <div className="card-title">文件範本</div>
          <p className="muted small mt-0">
            自訂範本放在資料資料夾的 <code>templates/</code>，格式見 README 的「自訂範本」。
          </p>
          {groups.map((g) => (
            <div key={g.key} className="template-group">
              <div className="subhead">{g.name}</div>
              {g.templates.map((t) => (
                <TemplateItem key={t.key} t={t} />
              ))}
            </div>
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
    </>
  )
}

function TemplateItem({ t }: { t: Template }) {
  return (
    <details className="template-item">
      <summary>
        {t.name}
        {!t.builtin && <span className="muted small">（自訂）</span>}
      </summary>
      <p className="muted small">{t.description}</p>
      <ol className="small">
        {t.sections.map((s) => (
          <li key={s.heading}>
            <b>{s.heading}</b>：{s.goal}
            {s.ids && <span className="muted">（編號 {s.ids}-01）</span>}
            {s.diagrams.length > 0 && <span className="muted">（圖：{s.diagrams.map((d) => KIND_NAMES[d] ?? d).join('、')}）</span>}
          </li>
        ))}
      </ol>
    </details>
  )
}

/** 範本照類型分組，類型照 CATEGORIES 的順序；不認得的類型放最後 */
function groupTemplates(templates: Template[], categories: { key: string; name: string; description: string }[]) {
  const groups = categories
    .map((c) => ({ ...c, templates: templates.filter((t) => t.category === c.key) }))
    .filter((g) => g.templates.length > 0)
  const known = new Set(categories.map((c) => c.key))
  const others = templates.filter((t) => !known.has(t.category))
  if (others.length) groups.push({ key: 'other', name: '其他', description: '', templates: others })
  return groups
}
