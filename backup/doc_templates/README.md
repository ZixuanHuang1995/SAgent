# 內建文件範本的備份

2026-10-30 精簡內建範本之前的全部 14 個範本，內容和當時 `backend/sagent/doc_templates/` 一樣（`order` 是當時的值）。

內建範本現在只留 8 個：構想提案、功能規格書、測試計畫、技術決策、系統架構設計、資料庫設計、演算法設計、變更需求說明。移除的是：

| 檔案 | 文件類型 | style |
|---|---|---|
| `use_case.yaml` | 使用案例規格 | spec（章節固定） |
| `sdd_five.yaml` | SDD 功能規格（五節體例） | spec（章節固定） |
| `uiux_advice.yaml` | UI/UX 設計建議 | advice |
| `api_spec.yaml` | API 規格 | spec |
| `experiment_design.yaml` | 實驗設計 | experiment |
| `change_record.yaml` | 變更紀錄 | spec（章節固定、不畫圖） |

要再用其中一種：把檔案複製到 `data/templates/`，當成自訂範本，不用重新啟動。

後端測試也用這裡的檔案測 `advice`、`experiment` 寫作規則和章節固定的範本，刪掉前先改測試。
