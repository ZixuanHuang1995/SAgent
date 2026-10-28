"""編輯器「插入圖表」選單的圖表骨架。

跟 AI 用的範例（agents/diagram_kinds.py）分開：AI 的範例要完整，小模型才照得來；
插入用的是精簡骨架，文字用佔位字，語法提示寫成註解（Mermaid 用 %%、PlantUML 用 '），
註解不會畫出來。

心智圖、象限圖這些只放在選單，不加進 AI 的圖表種類：種類越多，小模型越容易選錯。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DiagramSnippet:
    key: str  # 跟 AI 圖表種類相同的 key，範本的「常用圖表」才對得起來
    name: str
    hint: str  # 選單上的一行說明
    group: str
    mermaid: str
    plantuml: str = ""  # 空字串：PlantUML 沒有這種圖


FLOW = "流程與互動"
STRUCTURE = "結構與資料"
PLAN = "構想與規劃"

SNIPPETS: list[DiagramSnippet] = [
    DiagramSnippet(
        key="flowchart",
        name="流程圖",
        hint="步驟和判斷",
        group=FLOW,
        mermaid="""flowchart TD
    %% 步驟：代號["文字"]，判斷：代號{"問題？"}，開始和結束：代號(["文字"])
    %% 連線：A --> B，有條件時寫 A -->|是| B
    S(["開始"]) --> A["步驟一"]
    A --> B{"判斷條件？"}
    B -->|是| C["步驟二"]
    B -->|否| A
    C --> E(["結束"])""",
        plantuml="""@startuml
' 步驟寫成 :步驟文字;，判斷寫 if (條件？) then (是) … else (否) … endif
start
:步驟一;
if (判斷條件？) then (是)
  :步驟二;
else (否)
  :步驟三;
endif
stop
@enduml""",
    ),
    DiagramSnippet(
        key="swimlane",
        name="泳道流程圖",
        hint="跨部門、跨角色的流程",
        group=FLOW,
        mermaid="""flowchart LR
    %% 每個角色一個 subgraph，步驟放在負責的角色裡
    %% 步驟：代號["文字"]，判斷：代號{"問題？"}，連線：A --> B
    subgraph R1["角色一"]
        S(["開始"]) --> A["步驟一"]
    end
    subgraph R2["角色二"]
        B{"判斷條件？"}
    end
    subgraph R3["角色三"]
        C["步驟二"] --> E(["結束"])
    end
    A --> B
    B -->|是| C
    B -->|否| A""",
        plantuml="""@startuml
' 每個角色一條泳道：寫 |角色名稱| 之後的步驟都在這條泳道
|角色一|
start
:步驟一;
|角色二|
if (判斷條件？) then (是)
  :步驟二;
else (否)
  :步驟三;
endif
|角色三|
:步驟四;
stop
@enduml""",
    ),
    DiagramSnippet(
        key="sequence",
        name="循序圖",
        hint="系統之間誰呼叫誰",
        group=FLOW,
        mermaid="""sequenceDiagram
    %% 參與者：participant 代號 as 名稱，人用 actor
    %% 訊息：A->>B: 內容，回應：B-->>A: 內容，條件：alt 條件 … else … end
    actor U as 使用者
    participant A as 系統一
    participant B as 系統二
    U->>A: 操作
    A->>B: 呼叫
    B-->>A: 回應
    A-->>U: 顯示結果""",
        plantuml="""@startuml
' 參與者：actor、participant、database "名稱" as 代號
' 訊息：A -> B : 內容，回應：B --> A : 內容
actor "使用者" as U
participant "系統一" as A
participant "系統二" as B
U -> A : 操作
A -> B : 呼叫
B --> A : 回應
A --> U : 顯示結果
@enduml""",
    ),
    DiagramSnippet(
        key="state",
        name="狀態圖",
        hint="資料的狀態怎麼變",
        group=FLOW,
        mermaid="""stateDiagram-v2
    %% 先宣告狀態：state "名稱" as 代號（代號用英文）
    %% 轉換：A --> B : 觸發事件，[*] 是開始和結束
    state "狀態一" as S1
    state "狀態二" as S2
    state "狀態三" as S3
    [*] --> S1
    S1 --> S2 : 事件一
    S2 --> S3 : 事件二
    S2 --> S1 : 事件三
    S3 --> [*]""",
        plantuml="""@startuml
' 先宣告狀態：state "名稱" as 代號，轉換：A --> B : 觸發事件
state "狀態一" as S1
state "狀態二" as S2
state "狀態三" as S3
[*] --> S1
S1 --> S2 : 事件一
S2 --> S3 : 事件二
S2 --> S1 : 事件三
S3 --> [*]
@enduml""",
    ),
    DiagramSnippet(
        key="usecase",
        name="使用案例圖",
        hint="誰能用系統做什麼",
        group=FLOW,
        mermaid="""usecase-beta
    direction LR
    %% 角色：actor 代號("名稱")，使用案例：代號("名稱")
    %% 包含：UC2 ..>:include UC1，延伸：UC3 ..>:extend UC1
    actor U1("角色一")
    actor U2("角色二")
    systemBoundary SYS("系統名稱")
        UC1("使用案例一")
        UC2("使用案例二")
        UC3("使用案例三")
    end
    U1 --> UC1
    U1 --> UC2
    U2 --> UC3""",
        plantuml="""@startuml
left to right direction
' 角色：actor "名稱" as 代號，使用案例：usecase "名稱" as 代號
' 包含：UC2 ..> UC1 : <<include>>
actor "角色一" as U1
actor "角色二" as U2
rectangle "系統名稱" {
  usecase "使用案例一" as UC1
  usecase "使用案例二" as UC2
  usecase "使用案例三" as UC3
}
U1 --> UC1
U1 --> UC2
U2 --> UC3
@enduml""",
    ),
    DiagramSnippet(
        key="er",
        name="實體關聯圖",
        hint="資料表和關聯",
        group=STRUCTURE,
        mermaid="""erDiagram
    %% 資料表名稱用英文大寫，欄位：型別 名稱 PK或FK "說明"
    %% 關聯：||--o{ 一對多、||--|| 一對一、}o--o{ 多對多
    TABLE_A ||--o{ TABLE_B : "關聯說明"
    TABLE_A {
        string a_id PK "編號"
        string name "名稱"
    }
    TABLE_B {
        string b_id PK "編號"
        string a_id FK "TABLE_A 的編號"
        date created_at "建立日期"
    }""",
        plantuml="""@startuml
' 主鍵寫在 -- 上面，關聯：||--o{ 一對多、||--|| 一對一、}o--o{ 多對多
entity "TABLE_A\\n資料表一" as A {
  * a_id : string <<PK>>
  --
  name : string
}
entity "TABLE_B\\n資料表二" as B {
  * b_id : string <<PK>>
  --
  * a_id : string <<FK>>
  created_at : date
}
A ||--o{ B : 關聯說明
@enduml""",
    ),
    DiagramSnippet(
        key="class",
        name="類別圖",
        hint="物件和關係（名稱用英文）",
        group=STRUCTURE,
        mermaid="""classDiagram
    %% 類別、屬性、方法用英文，中文寫在關聯的冒號後面
    %% 關聯：--> 關聯、*-- 組合、o-- 聚合、<|-- 繼承
    class ClassA {
        +String id
        +String name
        +doSomething()
    }
    class ClassB {
        +String id
    }
    ClassA "1" --> "*" ClassB : 關聯說明""",
        plantuml="""@startuml
' 屬性寫 +名稱 : 型別，方法寫 +名稱()
' 關聯：--> 關聯、*-- 組合、o-- 聚合、<|-- 繼承
class ClassA {
  +id : String
  +name : String
  +doSomething()
}
class ClassB {
  +id : String
}
ClassA "1" --> "*" ClassB : 關聯說明
@enduml""",
    ),
    DiagramSnippet(
        key="component",
        name="元件圖",
        hint="系統由哪些部分組成",
        group=STRUCTURE,
        mermaid="""flowchart LR
    %% 元件：代號["名稱"]，資料庫：代號[("名稱")]
    %% 分層用 subgraph 代號["名稱"] … end，連線上寫通訊方式：A -->|HTTPS| B
    subgraph L1["前端"]
        A["元件一"]
    end
    subgraph L2["後端"]
        B["元件二"]
        C["元件三"]
    end
    DB[("資料庫")]
    A -->|HTTPS| B
    B --> C
    C --> DB""",
        plantuml="""@startuml
' 元件：[名稱] as 代號，資料庫：database "名稱" as 代號，分層用 package "名稱" { … }
package "前端" {
  [元件一] as A
}
package "後端" {
  [元件二] as B
  [元件三] as C
}
database "資料庫" as DB
A --> B : HTTPS
B --> C
C --> DB
@enduml""",
    ),
    DiagramSnippet(
        key="deployment",
        name="部署圖",
        hint="程式放在哪些主機",
        group=STRUCTURE,
        mermaid="""flowchart TB
    %% 網路區域用 subgraph 代號["名稱"] … end
    %% 主機：代號["名稱"]，資料庫：代號[("名稱")]，連線上寫協定和埠號：A -->|HTTPS 443| B
    USER(("使用者"))
    subgraph Z1["網路區域一"]
        H1["主機一"]
    end
    subgraph Z2["網路區域二"]
        H2["主機二"]
        DB[("資料庫主機")]
    end
    USER -->|HTTPS 443| H1
    H1 --> H2
    H2 --> DB""",
        plantuml="""@startuml
' 區域和主機用 node "名稱" as 代號 { … }，資料庫用 database，連線上寫協定
actor "使用者" as USER
node "網路區域一" {
  node "主機一" as H1
}
node "網路區域二" {
  node "主機二" as H2
  database "資料庫主機" as DB
}
USER --> H1 : HTTPS 443
H1 --> H2
H2 --> DB
@enduml""",
    ),
    DiagramSnippet(
        key="mindmap",
        name="心智圖",
        hint="把想法分層整理",
        group=PLAN,
        mermaid="""mindmap
    %% 用縮排表示層級，第一個是主題
    root((主題))
        想法一
            細節一
            細節二
        想法二
        想法三""",
        plantuml="""@startmindmap
' 用 * 的數量表示層級
* 主題
** 想法一
*** 細節一
*** 細節二
** 想法二
** 想法三
@endmindmap""",
    ),
    DiagramSnippet(
        key="quadrant",
        name="象限圖",
        hint="依兩個條件排優先順序",
        group=PLAN,
        mermaid="""quadrantChart
    %% 兩軸寫「低 --> 高」，項目座標是 0～1 的 [橫軸, 縱軸]
    %% quadrant-1 右上、quadrant-2 左上、quadrant-3 左下、quadrant-4 右下
    title 優先順序
    x-axis 低成本 --> 高成本
    y-axis 低效益 --> 高效益
    quadrant-1 值得投入
    quadrant-2 優先做
    quadrant-3 有空再做
    quadrant-4 不建議做
    項目一: [0.2, 0.8]
    項目二: [0.7, 0.7]
    項目三: [0.3, 0.3]""",
    ),
    DiagramSnippet(
        key="journey",
        name="使用者旅程圖",
        hint="使用者每一步的感受",
        group=PLAN,
        mermaid="""journey
    %% 每個步驟：步驟名稱: 感受分數（1～5，5 最好）: 角色
    title 使用者旅程
    section 階段一
        步驟一: 5: 使用者
        步驟二: 3: 使用者
    section 階段二
        步驟三: 2: 使用者, 客服人員
        步驟四: 4: 使用者""",
    ),
    DiagramSnippet(
        key="timeline",
        name="時間軸",
        hint="里程碑和版本規劃",
        group=PLAN,
        mermaid="""timeline
    %% 每行一個時間點：時間 : 事件，同一個時間點有多個事件時再接「: 事件」
    title 版本規劃
    2027 Q1 : 里程碑一
    2027 Q2 : 里程碑二 : 里程碑三
    2027 Q3 : 里程碑四""",
    ),
    DiagramSnippet(
        key="gantt",
        name="甘特圖",
        hint="工作排程和天數",
        group=PLAN,
        mermaid="""gantt
    %% 工作：名稱 :代號, 開始日期, 天數d，接在前一個工作後面：名稱 :代號, after 前一個代號, 天數d
    dateFormat YYYY-MM-DD
    section 階段一
    工作一 :a1, 2027-01-04, 10d
    工作二 :a2, after a1, 5d
    section 階段二
    工作三 :b1, after a2, 15d""",
        plantuml="""@startgantt
' 工作寫 [名稱] requires 天數 days，接在後面寫 [B] starts at [A]'s end
Project starts 2027-01-04
[工作一] requires 10 days
[工作二] requires 5 days
[工作二] starts at [工作一]'s end
[工作三] requires 15 days
[工作三] starts at [工作二]'s end
@endgantt""",
    ),
]
