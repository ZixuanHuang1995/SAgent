// 版面用的小工具：記住使用者的選擇、偵測螢幕寬度、量元素寬度

import { useCallback, useEffect, useRef, useState } from 'react'

function readStorage(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeStorage(key: string, value: string) {
  try {
    localStorage.setItem(key, value)
  } catch {
    /* 不能存就算了 */
  }
}

/** 存在瀏覽器裡的字串設定（例如檢視模式），重新整理後還在 */
export function usePersistent<T extends string>(key: string, fallback: T): [T, (v: T) => void] {
  const [value, setValue] = useState<T>(() => (readStorage(key) as T | null) ?? fallback)
  const set = useCallback(
    (v: T) => {
      setValue(v)
      writeStorage(key, v)
    },
    [key],
  )
  return [value, set]
}

/** 存在瀏覽器裡的數字設定（例如欄寬），超出範圍時自動限制在 min～max */
export function usePersistentNumber(
  key: string,
  fallback: number,
  min: number,
  max: number,
): [number, (v: number | ((prev: number) => number)) => void] {
  const clamp = useCallback((v: number) => Math.min(max, Math.max(min, v)), [min, max])
  const [value, setValue] = useState(() => {
    const raw = readStorage(key)
    const n = Number(raw)
    return raw !== null && Number.isFinite(n) ? clamp(n) : fallback
  })
  // 連續按方向鍵時，要用最新的值計算，不能用畫面上一次的值
  const latest = useRef(value)
  const set = useCallback(
    (v: number | ((prev: number) => number)) => {
      const c = clamp(typeof v === 'function' ? v(latest.current) : v)
      latest.current = c
      setValue(c)
      writeStorage(key, String(c))
    },
    [key, clamp],
  )
  return [value, set]
}

/** 螢幕寬度是否符合條件，例如 useMediaQuery('(max-width: 800px)') */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const mql = window.matchMedia(query)
    const onChange = () => setMatches(mql.matches)
    onChange()
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [query])
  return matches
}

/** 元素目前的寬度（像素）。把回傳的 ref 掛到元素上；還沒量到時是 0 */
export function useElementWidth<T extends HTMLElement>(): [number, (el: T | null) => void] {
  const [width, setWidth] = useState(0)
  const [el, setEl] = useState<T | null>(null)
  useEffect(() => {
    if (!el) return
    const ro = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width))
    ro.observe(el)
    return () => ro.disconnect()
  }, [el])
  return [width, setEl]
}
