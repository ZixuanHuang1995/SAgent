// 從一份 Spec 抽出名詞（名詞定義、角色、狀態、實體），使用者勾選後才加進專案詞彙表
// 程式抽的，不呼叫模型；跟詞彙表說法不一樣的預設不勾，要使用者自己決定用哪一個

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type GlossarySuggestion, type PlanTerm } from '../api'
import { useProjectTree } from '../queries'
import { Badge, Empty, errorText, Modal, Skeleton, useToast } from './ui'

const LIMIT = 40

export function GlossaryExtractDialog(props: { specId: number; projectId: number; title: string; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const tree = useProjectTree(props.projectId)
  const query = useQuery({
    queryKey: ['glossary-suggestions', props.specId],
    queryFn: () => api.glossarySuggestions(props.specId),
    gcTime: 0,
  })
  // 勾選了哪些：沒動過時新名詞預設勾、說法不一樣的預設不勾
  const [picked, setPicked] = useState<Record<string, boolean>>({})
  const suggestions = query.data?.suggestions ?? []
  const isPicked = (s: GlossarySuggestion) => picked[s.name] ?? s.status === 'new'

  const glossary: PlanTerm[] = tree.data?.project.glossary ?? []
  const chosen = suggestions.filter(isPicked)
  const added = chosen.filter((s) => s.status === 'new')
  const room = Math.max(LIMIT - glossary.length, 0)
  const over = added.length > room

  const save = useMutation({
    mutationFn: () => {
      const updates = new Map(chosen.filter((s) => s.status === 'changed').map((s) => [s.name, s]))
      const next = [
        ...glossary.map((t) => (updates.has(t.name) ? { name: t.name, kind: updates.get(t.name)!.kind, note: updates.get(t.name)!.note } : t)),
        ...added.map((s) => ({ name: s.name, kind: s.kind, note: s.note })),
      ]
      return api.updateProject(props.projectId, { glossary: next })
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tree', props.projectId] })
      const parts = [added.length && `新增 ${added.length} 個`, chosen.length - added.length && `更新 ${chosen.length - added.length} 個`]
      toast(`專案詞彙表：${parts.filter(Boolean).join('、')}`, 'success')
      props.onClose()
    },
    onError: (e) => toast(errorText(e), 'error'),
  })

  const render = (status: GlossarySuggestion['status'], title: string, hint: string) => {
    const items = suggestions.filter((s) => s.status === status)
    if (items.length === 0) return null
    return (
      <>
        <div className="subhead">
          {title} <span className="muted small">{hint}</span>
        </div>
        <ul className="check-list">
          {items.map((s) => (
            <li key={s.name}>
              <input
                type="checkbox"
                checked={isPicked(s)}
                onChange={(e) => setPicked({ ...picked, [s.name]: e.target.checked })}
                aria-label={`${status === 'new' ? '加進' : '更新'}專案詞彙表：${s.name}`}
              />
              <div className="check-item">
                <div className="check-item-meta">
                  <strong>{s.name}</strong>
                  <Badge>{s.kind}</Badge>
                </div>
                {s.note && <div className="small">{s.note}</div>}
                {status === 'changed' && (
                  <div className="muted small">
                    專案詞彙表現在是：{s.current_kind || '其他'}
                    {s.current_note && `・${s.current_note}`}
                  </div>
                )}
              </div>
            </li>
          ))}
        </ul>
      </>
    )
  }

  return (
    <Modal
      title="從這份文件抽出名詞"
      onClose={props.onClose}
      footer={
        <>
          <button className="btn" onClick={props.onClose}>
            取消
          </button>
          <button
            className="btn btn-primary"
            disabled={chosen.length === 0 || over || save.isPending || !tree.data}
            onClick={() => save.mutate()}
          >
            {chosen.length ? `更新專案詞彙表（${chosen.length} 個）` : '更新專案詞彙表'}
          </button>
        </>
      }
    >
      <p className="muted small">
        從「{props.title}」{query.data && ` v${query.data.version_no}`}的名詞定義、角色表、狀態和實體抽出來的名詞。加進專案詞彙表後，這個專案之後的 AI
        撰寫、修訂和審查都會照這些名稱寫。只會更新你勾選的。
      </p>
      {query.isLoading && <Skeleton lines={5} />}
      {query.error && <div className="error-box">{errorText(query.error)}</div>}
      {query.data && suggestions.length === 0 && (
        <Empty title="沒有要更新的名詞">文件裡定義的名詞都已經在專案詞彙表裡，或文件沒有名詞定義、角色、狀態的表格。</Empty>
      )}
      {render('new', '新的名詞', '專案詞彙表還沒有')}
      {render('changed', '說法不一樣', '勾選的會改成文件裡的寫法')}
      {query.data && suggestions.length > 0 && (
        <div className={`small ${over ? 'error-box' : 'muted'}`}>
          專案詞彙表 {glossary.length} / {LIMIT} 個，這次新增 {added.length} 個
          {over && `，超過上限，請少勾 ${added.length - room} 個`}
        </div>
      )}
    </Modal>
  )
}
