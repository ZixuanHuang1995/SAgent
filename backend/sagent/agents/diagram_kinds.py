"""圖表種類，以及給小模型看的語法規則和範例。

小模型照著範例寫，比只給規則可靠很多，所以每種圖都附一個完整、可正確渲染的範例。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DiagramKind:
    key: str
    name: str
    mermaid_guide: str
    mermaid_example: str
    plantuml_guide: str
    plantuml_example: str
    # auto 模式下，有 PlantUML 渲染時改用 PlantUML（Mermaid 沒有這種原生圖）
    prefer_plantuml: bool = False


_FLOW_COMMON = """- 節點寫成「代號["文字"]」，代號只用英文字母和數字，文字一律用雙引號包起來
- 連線寫「A --> B」，有條件時寫「A -->|是| B」
- 不要用 end 當代號"""

KINDS: dict[str, DiagramKind] = {
    "flowchart": DiagramKind(
        key="flowchart",
        name="流程圖",
        mermaid_guide=f"""- 第一行寫 flowchart TD（由上到下）
{_FLOW_COMMON}
- 判斷寫成「代號{{"問題？"}}」，開始和結束寫成「代號(["文字"])」
- 需要分組時用「subgraph 代號["名稱"]」開始，用 end 結束""",
        mermaid_example="""flowchart TD
    A(["開始"]) --> B["使用者輸入帳號密碼"]
    B --> C{"帳號密碼正確？"}
    C -->|是| D["建立登入工作階段"]
    C -->|否| E["顯示錯誤訊息"]
    E --> B
    D --> F(["結束"])""",
        plantuml_guide="""- 用 PlantUML 活動圖：start 開始、stop 結束
- 每個步驟寫成「:步驟文字;」（冒號開頭、分號結尾）
- 判斷寫 if (條件？) then (是) … else (否) … endif""",
        plantuml_example="""@startuml
start
:使用者輸入帳號密碼;
if (帳號密碼正確？) then (是)
  :建立登入工作階段;
else (否)
  :顯示錯誤訊息;
endif
stop
@enduml""",
    ),
    "sequence": DiagramKind(
        key="sequence",
        name="循序圖",
        mermaid_guide="""- 第一行寫 sequenceDiagram
- 先宣告參與者：「participant 代號 as 中文名稱」，人用 actor。代號只用英文
- 訊息寫成「A->>B: 內容」，回應寫成「B-->>A: 內容」，冒號一定要有
- 條件用「alt 條件」…「else 條件」…「end」，重複用「loop 說明」…「end」""",
        mermaid_example="""sequenceDiagram
    actor U as 使用者
    participant W as 網頁前端
    participant S as 後端服務
    participant D as 資料庫
    U->>W: 輸入帳號密碼
    W->>S: POST /api/login
    S->>D: 查詢使用者資料
    D-->>S: 回傳使用者資料
    alt 驗證成功
        S-->>W: 回傳 token
        W-->>U: 進入首頁
    else 驗證失敗
        S-->>W: 401 錯誤
        W-->>U: 顯示錯誤訊息
    end""",
        plantuml_guide="""- 先宣告參與者：actor "中文名稱" as 代號、participant "中文名稱" as 代號、database "中文名稱" as 代號
- 訊息寫成「A -> B : 內容」，回應寫成「B --> A : 內容」
- 條件用 alt … else … end""",
        plantuml_example="""@startuml
actor "使用者" as U
participant "網頁前端" as W
participant "後端服務" as S
database "資料庫" as D
U -> W : 輸入帳號密碼
W -> S : POST /api/login
S -> D : 查詢使用者資料
D --> S : 回傳使用者資料
alt 驗證成功
  S --> W : 回傳 token
else 驗證失敗
  S --> W : 401 錯誤
end
@enduml""",
    ),
    "class": DiagramKind(
        key="class",
        name="類別圖",
        mermaid_guide="""- 第一行寫 classDiagram
- 類別名稱、屬性、方法都用英文；中文說明寫在關聯的冒號後面
- 屬性寫「+型別 名稱」，方法寫「+名稱()」
- 關聯：A --> B（關聯）、A *-- B（組合）、A o-- B（聚合）、A <|-- B（B 繼承 A）、A ..> B（相依）""",
        mermaid_example="""classDiagram
    class Customer {
        +String customerId
        +String name
    }
    class Order {
        +String orderId
        +Date orderDate
        +String status
        +submit()
        +cancel()
    }
    class OrderItem {
        +String productId
        +int quantity
    }
    Customer "1" --> "*" Order : 下訂
    Order "1" *-- "1..*" OrderItem : 包含""",
        plantuml_guide="""- 類別名稱、屬性、方法用英文
- 屬性寫「+名稱 : 型別」，方法寫「+名稱()」
- 關聯：A --> B、A *-- B（組合）、A o-- B（聚合）、A <|-- B（繼承），說明寫在冒號後面""",
        plantuml_example="""@startuml
class Customer {
  +customerId : String
  +name : String
}
class Order {
  +orderId : String
  +orderDate : Date
  +submit()
}
class OrderItem {
  +productId : String
  +quantity : int
}
Customer "1" --> "*" Order : 下訂
Order "1" *-- "1..*" OrderItem : 包含
@enduml""",
    ),
    "state": DiagramKind(
        key="state",
        name="狀態圖",
        mermaid_guide="""- 第一行寫 stateDiagram-v2
- 狀態代號只用英文，先用「state "中文名稱" as 代號」宣告
- 轉換寫成「A --> B : 觸發事件」，開始和結束用 [*]""",
        mermaid_example="""stateDiagram-v2
    state "草稿" as Draft
    state "審核中" as Submitted
    state "已核准" as Approved
    state "已退回" as Rejected
    [*] --> Draft
    Draft --> Submitted : 送出
    Submitted --> Approved : 核准
    Submitted --> Rejected : 退回
    Rejected --> Draft : 修改後重送
    Approved --> [*]""",
        plantuml_guide="""- 先用「state "中文名稱" as 代號」宣告狀態，代號用英文
- 轉換寫成「A --> B : 觸發事件」，開始和結束用 [*]""",
        plantuml_example="""@startuml
state "草稿" as Draft
state "審核中" as Submitted
state "已核准" as Approved
state "已退回" as Rejected
[*] --> Draft
Draft --> Submitted : 送出
Submitted --> Approved : 核准
Submitted --> Rejected : 退回
Rejected --> Draft : 修改後重送
Approved --> [*]
@enduml""",
    ),
    "er": DiagramKind(
        key="er",
        name="實體關聯圖",
        mermaid_guide="""- 第一行寫 erDiagram
- 實體名稱用英文大寫，例如 ORDER_ITEM
- 屬性寫成「型別 英文名稱 PK或FK "中文說明"」，型別只用 string、int、decimal、date、datetime、boolean
- 關聯寫成「A ||--o{ B : "說明"」（一對多）、「A ||--|| B : "說明"」（一對一）、「A }o--o{ B : "說明"」（多對多）""",
        mermaid_example="""erDiagram
    CUSTOMER ||--o{ ORDERS : "下訂"
    ORDERS ||--|{ ORDER_ITEM : "包含"
    PRODUCT ||--o{ ORDER_ITEM : "被訂購"
    CUSTOMER {
        string customer_id PK "客戶編號"
        string name "客戶名稱"
        string email "電子郵件"
    }
    ORDERS {
        string order_id PK "訂單編號"
        string customer_id FK "客戶編號"
        date order_date "訂單日期"
        string status "訂單狀態"
    }
    ORDER_ITEM {
        string order_id FK "訂單編號"
        string product_id FK "商品編號"
        int quantity "數量"
    }
    PRODUCT {
        string product_id PK "商品編號"
        string name "商品名稱"
        decimal price "單價"
    }""",
        plantuml_guide="""- 用 entity "英文名稱\\n中文名稱" as 代號 { … } 宣告實體
- 主鍵寫在 -- 上面，格式「* 欄位 : 型別 <<PK>>」
- 關聯寫成「A ||--o{ B : 說明」""",
        plantuml_example="""@startuml
entity "CUSTOMER\\n客戶" as CUSTOMER {
  * customer_id : string <<PK>>
  --
  name : string
  email : string
}
entity "ORDERS\\n訂單" as ORDERS {
  * order_id : string <<PK>>
  --
  * customer_id : string <<FK>>
  order_date : date
}
CUSTOMER ||--o{ ORDERS : 下訂
@enduml""",
    ),
    "usecase": DiagramKind(
        key="usecase",
        name="使用案例圖",
        # Mermaid 12 起有原生的使用案例圖（usecase-beta）
        mermaid_guide="""- 第一行寫 usecase-beta，第二行寫 direction LR
- 角色寫成「actor 代號("角色名稱")」，代號只用英文字母和數字
- 系統邊界寫成「systemBoundary SYS("系統名稱")」，裡面列出使用案例，最後寫 end
- 使用案例寫成「代號("使用案例名稱")」，例如 UC1("查詢訂單")
- 角色連到使用案例寫成「U1 --> UC1」
- 包含關係寫成「UC2 ..>:include UC1」，延伸關係寫成「UC3 ..>:extend UC1」""",
        mermaid_example="""usecase-beta
    direction LR
    actor U1("一般使用者")
    actor U2("管理員")
    systemBoundary SYS("訂單系統")
        UC1("查詢訂單")
        UC2("建立訂單")
        UC3("審核訂單")
    end
    U1 --> UC1
    U1 --> UC2
    U2 --> UC3
    UC2 ..>:include UC1""",
        plantuml_guide="""- 第二行寫 left to right direction
- 角色寫「actor "角色名稱" as 代號」，使用案例寫「usecase "名稱" as 代號」
- 系統邊界用 rectangle "系統名稱" { … } 包住使用案例
- 角色連到使用案例寫「U1 --> UC1」，包含關係寫「UC1 ..> UC2 : <<include>>」""",
        plantuml_example="""@startuml
left to right direction
actor "一般使用者" as U1
actor "管理員" as U2
rectangle "訂單系統" {
  usecase "查詢訂單" as UC1
  usecase "建立訂單" as UC2
  usecase "審核訂單" as UC3
}
U1 --> UC1
U1 --> UC2
U2 --> UC3
UC2 ..> UC1 : <<include>>
@enduml""",
        prefer_plantuml=True,
    ),
    "component": DiagramKind(
        key="component",
        name="元件圖",
        mermaid_guide=f"""- 用 flowchart LR 表示元件圖
- 每個元件寫成「代號["元件名稱"]」，資料庫寫成「代號[("資料庫名稱")]」
- 用「subgraph 代號["分層名稱"]」把元件分組，用 end 結束
- 連線上寫通訊方式，例如「A -->|HTTPS| B」
{_FLOW_COMMON}""",
        mermaid_example="""flowchart LR
    subgraph FE["前端"]
        WEB["Web 介面"]
    end
    subgraph BE["後端服務"]
        API["API 閘道"]
        SVC["訂單服務"]
    end
    DB[("訂單資料庫")]
    EXT["外部金流系統"]
    WEB -->|HTTPS| API
    API --> SVC
    SVC --> DB
    SVC -->|REST| EXT""",
        plantuml_guide="""- 元件寫成「[元件名稱] as 代號」，資料庫寫成「database "名稱" as 代號」
- 用 package "分層名稱" { … } 分組
- 連線寫「A --> B : 通訊方式」""",
        plantuml_example="""@startuml
package "前端" {
  [Web 介面] as WEB
}
package "後端服務" {
  [API 閘道] as API
  [訂單服務] as SVC
}
database "訂單資料庫" as DB
[外部金流系統] as EXT
WEB --> API : HTTPS
API --> SVC
SVC --> DB
SVC --> EXT : REST
@enduml""",
        prefer_plantuml=True,
    ),
    "deployment": DiagramKind(
        key="deployment",
        name="部署圖",
        mermaid_guide=f"""- 用 flowchart TB 表示部署圖
- 用「subgraph 代號["網路區域名稱"]」表示區域，用 end 結束
- 主機寫成「代號["主機名稱"]」，資料庫主機寫成「代號[("名稱")]」，使用者寫成「代號(("使用者"))」
- 連線上寫協定和埠號，例如「A -->|HTTPS 443| B」
{_FLOW_COMMON}""",
        mermaid_example="""flowchart TB
    USER(("使用者"))
    subgraph DMZ["DMZ 區"]
        LB["負載平衡器"]
    end
    subgraph APP["應用程式區"]
        AP1["AP 主機 1"]
        AP2["AP 主機 2"]
    end
    subgraph DATA["資料區"]
        DB[("資料庫主機")]
    end
    USER -->|HTTPS 443| LB
    LB --> AP1
    LB --> AP2
    AP1 --> DB
    AP2 --> DB""",
        plantuml_guide="""- 區域和主機用 node "名稱" as 代號 { … }，資料庫用 database "名稱" as 代號
- 使用者用 actor "名稱" as 代號
- 連線寫「A --> B : 協定」""",
        plantuml_example="""@startuml
actor "使用者" as USER
node "DMZ 區" {
  node "負載平衡器" as LB
}
node "應用程式區" {
  node "AP 主機 1" as AP1
  node "AP 主機 2" as AP2
}
node "資料區" {
  database "資料庫主機" as DB
}
USER --> LB : HTTPS 443
LB --> AP1
LB --> AP2
AP1 --> DB
AP2 --> DB
@enduml""",
        prefer_plantuml=True,
    ),
    "gantt": DiagramKind(
        key="gantt",
        name="甘特圖",
        mermaid_guide="""- 第一行寫 gantt，第二行寫 dateFormat YYYY-MM-DD
- 用「section 階段名稱」分段
- 工作寫成「工作名稱 :代號, 開始日期, 天數d」，接續上一個工作寫「工作名稱 :代號, after 前一個代號, 天數d」""",
        mermaid_example="""gantt
    title 專案時程
    dateFormat YYYY-MM-DD
    section 需求
    需求訪談 :a1, 2026-01-05, 10d
    需求確認 :a2, after a1, 5d
    section 開發
    系統開發 :b1, after a2, 30d
    section 測試
    整合測試 :c1, after b1, 10d""",
        plantuml_guide="""- 用 @startgantt 和 @endgantt
- 工作寫「[工作名稱] requires 天數 days」，接續寫「[B] starts at [A]'s end」""",
        plantuml_example="""@startgantt
Project starts 2026-01-05
[需求訪談] requires 10 days
[需求確認] requires 5 days
[需求確認] starts at [需求訪談]'s end
[系統開發] requires 30 days
[系統開發] starts at [需求確認]'s end
@endgantt""",
    ),
}

KIND_KEYS = list(KINDS)


def resolve_syntax(kind: str, uml_syntax: str, plantuml_available: bool) -> str:
    """決定這張圖用 Mermaid 還是 PlantUML。"""
    if uml_syntax == "mermaid":
        return "mermaid"
    if uml_syntax == "plantuml":
        return "plantuml"
    info = KINDS.get(kind)
    if info and info.prefer_plantuml and plantuml_available:
        return "plantuml"
    return "mermaid"


def kinds_text() -> str:
    return "、".join(f"{k.key}（{k.name}）" for k in KINDS.values())
