// 深淺色：可以跟著系統，也可以手動指定淺色或深色（記在瀏覽器裡）
//
// 手動指定時在 <html> 加 data-theme="light|dark"，CSS 依這個屬性切換顏色。
// index.html 在載入程式前就先套用，避免畫面先閃一下系統的顏色。

import { useEffect, useState } from 'react'

export type ThemePref = 'system' | 'light' | 'dark'

const KEY = 'sagent:theme'
const EVENT = 'sagent-theme'
const DARK_QUERY = '(prefers-color-scheme: dark)'

export function getThemePref(): ThemePref {
  try {
    const v = localStorage.getItem(KEY)
    return v === 'light' || v === 'dark' ? v : 'system'
  } catch {
    return 'system'
  }
}

export function setThemePref(pref: ThemePref) {
  try {
    if (pref === 'system') localStorage.removeItem(KEY)
    else localStorage.setItem(KEY, pref)
  } catch {
    /* 不能存就只在這次有效 */
  }
  applyTheme(pref)
  window.dispatchEvent(new Event(EVENT))
}

export function applyTheme(pref: ThemePref) {
  const root = document.documentElement
  if (pref === 'system') delete root.dataset.theme
  else root.dataset.theme = pref
}

function isDark(): boolean {
  const pref = getThemePref()
  return pref === 'dark' || (pref === 'system' && window.matchMedia(DARK_QUERY).matches)
}

/** 目前實際是不是深色（手動指定或系統設定） */
export function useIsDark(): boolean {
  const [dark, setDark] = useState(isDark)
  useEffect(() => {
    const update = () => setDark(isDark())
    const mql = window.matchMedia(DARK_QUERY)
    mql.addEventListener('change', update)
    window.addEventListener(EVENT, update)
    return () => {
      mql.removeEventListener('change', update)
      window.removeEventListener(EVENT, update)
    }
  }, [])
  return dark
}

export function useThemePref(): [ThemePref, (p: ThemePref) => void] {
  const [pref, setPref] = useState(getThemePref)
  useEffect(() => {
    const update = () => setPref(getThemePref())
    window.addEventListener(EVENT, update)
    return () => window.removeEventListener(EVENT, update)
  }, [])
  return [pref, setThemePref]
}
