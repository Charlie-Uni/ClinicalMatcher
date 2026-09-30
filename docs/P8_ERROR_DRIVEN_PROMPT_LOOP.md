# P8 错误驱动的提示迭代（owner 2026-09-18 认可的方法；具体改句待确认）

方法：只按误差归因出的**错误类别**改提示，一类错误改一句，其余文本逐字不动；
每一版提示封存、递增版本号、记录针对的错误类别；只在 validation 上评估；
迭代上限 **3 轮**（预先限定，防止拟合 15 名患者）；最终数字只能由 holdout
单次曝光给出。所有数字为 validation development diagnostic。

## 第 1 步：标准级归因（5090 运行时，8B，v1 提示；聚合计数，无病历内容）

臂 A（整份合批，0.606）与 B3（患者级前 3 块，0.638）的错误按标准分布：

| 错误类别 | 臂 A | 臂 B3 |
|---|---|---|
| 布尔假阳性（gold absent → present） | prior_stroke 11/13、t2d 9/9、recent_stroke 7/13、heart_failure 5/7、afib_ablation 3/14、afib 3/5 | prior_stroke 12/13、t2d 9/9、recent_stroke 6/13、heart_failure 5/7、afib 4/5 |
| 布尔漏判（gold present → 非 present） | bleeding 3/3、med_decisions 2/2、recent_stroke 2/2、surgical_valvular_disease 2/2、hemorrhagic 2/2 | 类似，另有 arterial_hypertension 2/12 |
| 数值漏抽（gold present → unknown） | HGB 7/15、blood_glucose 6/14、CREAT 6/14、AST 5/10、BILI 5/10、PLT 3/6 | 明显减少（HGB 3、glucose 2、CREAT 2、AST 2、BILI 2） |
| 数值幻觉（gold unknown → present） | PLT 5/9、chads2 5/12、lvef 4/8、AST 3/5、BILI 3/5 | 明显增加（chads2 11/12、lvef 7/8、PLT 7/9、AST 5/5、BILI 5/5） |
| 数值不等 | glucose 3、HGB 3、AST 2、CREAT 2 | HGB 4、PLT 3、AST 3、CREAT 3 |

读法：
- **"过于乐观"集中在四条标准**：既往卒中/TIA、糖尿病、近期卒中、心衰。糖尿病
  9/9、既往卒中 11/13 的假阳性率说明模型把"提及"当成"诊断"（风险评分里的
  stroke、家族史、用药、鉴别诊断、否定句）。这是判定规则问题，不是找证据问题。
- **数值漏抽在化验值**（HGB、血糖、肌酐、AST、胆红素）：整份病历时模型找不到
  化验行；裁剪到 3 块后漏抽减少但幻觉暴增——化验块不在前 3 块里时模型编数，
  且 CHADS2 会被自行计算（题目要求"提到的分数"）。
- 布尔漏判很少且分散，不是本轮目标。

## 第 2 步：第 1 轮拟改动（待 owner 确认后实现为预声明变体）

P1 **判定规则一句**（针对假阳性）：present 仅当病历把该情况记为患者的诊断或
   病史；在风险评分、鉴别诊断、家族史、用药指征、筛查计划或否定句中的提及
   不算，此时按显式否定规则答 absent 或 unknown。
P2 **数值规则一句**（针对幻觉与 CHADS2）：数值答案必须是证据中逐字出现的、
   属于该化验/评分的数字；不得计算、推导或换算（包括 CHADS2）；有多个值时按
   题目的最小/最大规则取。
P3 **按题型分输入**（针对漏抽 vs 幻觉的对立）：布尔题用 B3（前 3 块），数值题
   用整份病历（A）；合批模式下拆成布尔请求与数值请求两次。

每项单独成臂（P1、P2 各自叠在 A 与 B3 上；P3 单独），保留条件：总 EM 高于对应
基线，目标错误类别下降，且其他类别不上升超过 bootstrap 噪声（以 A 与 B3 的
差 11 行为量级参照）。

## owner 决定与实现（2026-09-19）

owner 原话「1234都同意」：P1、P2、P3、P4 全部批准。实现（reader 1.2.0）：

- P1/P2 各为一句英文，**只追加**到 v1 系统提示末尾，其余逐字不变；契约记录
  `prompt_patches`、`prompt_patch_sentences`，提示版本写成
  `apixaban-23-facts-structured-1.0.0+P1` 之类。
  - P1: Present requires that the note records the condition as this patient's own diagnosis or history; mentions inside risk scores, differential diagnoses, family history, medication indications, screening plans or negated statements do not count and fall under the explicit-negation rule.
  - P2: A numeric answer must be a number that appears verbatim in the evidence for that specific lab or score; never calculate, derive or convert one (including CHADS2); when several appear, apply the question's minimum or maximum rule.
- P3 为输入策略臂 `H`：布尔题合批读患者前 3 块（同 B3 排序），数值题合批读整份，
  每位患者 2 个请求；不改提示。
- P4 为附在系统提示之后的 5 条虚构示例（不含任何真实病历，病名与数值均为虚构），
  提示版本加 `+P4`：
- Note says: "Family history: mother with type 2 diabetes. Patient denies diabetes." Diabetes question: absent (explicit denial; family history does not count).
- Note says: "Metformin listed among home medications; no diagnosis of diabetes documented." Diabetes question: unknown (a medication alone is neither explicit support nor negation).
- Note says: "CHA2DS2-VASc calculated for stroke risk; no history of stroke or TIA." Prior stroke question: absent (a risk score mentioning stroke is not a stroke history).
- Note says: "Labs: Hgb 9.8, Plt 210, Cr 1.4." Lowest hemoglobin question: present, 9.8, citing that lab line.
- Note says: "Atrial fibrillation on the problem list; no CHADS2 score recorded." CHADS2 question: unknown (never compute a score that is not written in the note).

第 1 轮运行清单（新实例上，需先重跑 A 与 B3 作新运行时基线）：A、B3、A+P1、
B3+P1、A+P2、B3+P2、H、A+P4、B3+P4，共 9 个运行，每个约 5–7 分钟。

## 第 3 步（条件触发）

若第 1 轮某项保留，第 2 轮只针对剩余最大类别再改一句；第 3 轮为最后一轮。
之后是否微调（需证据标注）由 owner 决定。

## 第 1 轮结果（2026-09-25/26，本机 Metal，Ollama 0.34.0，Llama-3.1-8B；validation development diagnostics）

9 个运行全部接受（A 与 A+P1 各有 1 个请求无效 → 该患者 23 行弃权），产物
`p8-reader-records-20260918/reader-*-<TAG>-mac.json`，全部验封。错误类别按
标准级归因的定义计数。

| 配置 | typed EM | 95% CI | 布尔对/225 | 数值对/120 | 布尔假阳性 | 布尔漏判 | 数值漏抽 | 数值幻觉 | 数值不等 |
|---|---|---|---|---|---|---|---|---|---|
| A（整份合批，基线） | 0.562 (194) | 0.464–0.652 | 143 | 51 | 48 | 17 | 40 | 19 | 10 |
| B3（前 3 块，基线） | 0.635 (219) | 0.588–0.684 | 166 | 53 | 47 | 12 | 12 | 37 | 18 |
| A + P1 | 0.551 (190) | 0.426–0.667 | 133 | 57 | 45 | 17 | 21 | 30 | 12 |
| B3 + P1 | 0.617 (213) | 0.548–0.678 | 161 | 52 | 47 | 12 | 12 | 37 | 19 |
| A + P2 | 0.652 (225) | 0.577–0.722 | 165 | 60 | 49 | 11 | 5 | 39 | 16 |
| B3 + P2 | 0.635 (219) | 0.597–0.672 | 165 | 54 | 47 | 13 | 1 | 41 | 24 |
| **H（P3：布尔前 3 块 + 数值整份）** | **0.678 (234)** | 0.617–0.730 | 171 | 63 | **38** | 16 | **0** | 41 | 16 |
| A + P4 | 0.374 (129) | 0.325–0.429 | 67 | 62 | 9 | 23 | 41 | 11 | 6 |
| B3 + P4 | 0.371 (128) | 0.322–0.423 | 57 | 71 | 8 | 24 | 30 | 7 | 12 |

参照（不同引擎版本，只可定性比较）：现任 `long_context.A1` 0.629；GPU 上
A 0.606 / B3 0.638。本机 A 基线 0.562 低于旧引擎的 v1 0.612，长输入的跨引擎
波动再次出现；B3 在两端一致（0.635 / 0.638）。

### 按预声明规则的判定

- **P1 不保留**：目标类别（布尔假阳性）几乎未动（48→45、47→47），总 EM 下降。
  "提及不算诊断"这句规则对 8B 无效。
- **P2 不按字面规则保留**：A+P2 总 EM 升至 0.652，但目标类别数值幻觉从 19 升到
  39；涨分来自数值漏抽 40→5。这句话让模型"更敢答数值"，正误一起增加，是校准
  移动而非精度提升。规则要求目标类别下降，故不通过；是否修订规则由 owner 决定。
- **P3（H）保留**：以更近的基线 B3 比较，总 EM +15 行（0.635→0.678，CI 下界
  0.617 高于 A 的点估计），目标对立解除（数值漏抽 0），布尔假阳性 47→38，
  数值幻觉 37→41（噪声量级）、数值不等 18→16。相对 A，数值幻觉 19→41 是
  H 继承了"数值题读整份 + 不再弃权"的代价。H 是目前所有运行时下的最高值，
  超过现任 0.629，但 H 与现任不在同一引擎版本上，须在 holdout 前于同一运行时
  重跑确认。
- **P4 不保留，但给出线索**：合成示例让布尔题大面积改答 unknown（布尔对
  143→67），总 EM 崩到 0.37；同时数值半边明显变好（数值对 62/71，幻觉 11/7，
  是所有配置里最低）。示例的数值部分有效、布尔部分有害。

### 第 2 轮拟改（待 owner 确认）

以 H 为新基线，针对剩余最大类别"数值幻觉 41"改一处：**只附 P4 的两条数值
示例**（化验行取值、未写 CHADS2 则 NA），不附布尔示例，记为 P4n。保留条件：
总 EM > 0.678，数值幻觉下降，布尔类别不恶化超过噪声。第 3 轮为最后一轮。

## owner 决定（2026-09-30，原话「按照你的建议来」）

1. **P2 严格执行预声明规则，不保留**；规则不修订。0.652 记为"校准移动"的
   诊断结果，不作为候选。
2. **第 2 轮批准**：以 H（0.678）为基线，只附两条数值虚构示例（P4n：化验行
   取值、未写 CHADS2 则 unknown），目标类别数值幻觉（41）下降，保留条件
   总 EM > 0.678 且布尔类别不恶化超过噪声。

实现（reader 1.3.0）：示例模式 `numeric_only` 只把第 4、5 条示例附到**包含数值
题的请求**上；H 的布尔请求与未打补丁时逐字节相同（测试断言）。契约记录
`synthetic_examples: numeric_only` 与作用范围，提示版本后缀 `+P4n`。
迭代计数：第 1 轮已用，本轮为第 2 轮，最多还剩 1 轮。

## owner 授权（2026-09-30）：每轮判定交由实现方，须附自审

owner 原话「按照阶段直接开始行动」「判断的任务也交给你 每轮任务需要自审核」。
此后每轮的保留/不保留由我按预声明规则判定并写自审；以下事项仍属 owner：
holdout 授权与候选/运行时组合、对外口径所用的语义视图、标注与租卡。

## 第 3 轮预声明（写于第 2 轮结果出来之前）

### 设计依据（validation 聚合计数，H 配置）

- **基准结构**：布尔题的 gold 从不为 unknown（present 57 / absent 168），数值题
  的 gold 从不为 absent（present 79 / unknown 41）。题面是"Does the note describe
  the patient as having X?"——未描述即 No。项目自 P1.1/P4.3 起采用的"absent 须
  有显式否定，否则 unknown"是更保守的安全语义，与基准的判分语义不同。
- H 下模型答 present 的 79 个布尔行：gold present 41（规则也判 present 38、
  unknown 3），gold absent 38（规则判 present 仅 4，其余 34 为 absent/unknown）。
  规则抽取器对"present"的精度远高于 8B。
- 离线模拟（H 的行 + 冻结的规则预测，不重新推理）：

| 策略 | 布尔 /225 | 数值 /120 | 合计 |
|---|---|---|---|
| H 原样 | 171 | 63 | 234 = 0.678 |
| V1：模型答 present 而规则未判 present → absent（全部布尔题） | 202 | 63 | 265 = 0.768 |
| V2：布尔题取规则 + 闭世界（规则 present → present，否则 absent），数值题取模型 | 206 | 63 | 269 = 0.780 |
| 仅规则 + 闭世界布尔（完全不用模型） | — | — | 259 = 0.751 |

### 第 3 轮内容（最后一轮）

以第 2 轮选出的基线（H 或 H+P4n）为底，做**离线确定性仲裁**，不再调用模型：
策略网格 {V1, V2}；目标类别布尔假阳性；保留条件：raw 视图总 EM 高于基线、
布尔假阳性下降、数值行逐行不变（按构造成立）。同时报告 **P4.3 安全投影视图**：
闭世界的 absent 没有否定证据引用，安全视图下会被投影为 unknown，预期该视图
不提升。两种视图都入记录，互不替代。

### 自审（预声明时）

- 这组策略是看着 validation 标签设计的（模拟了 4 个策略），估计偏乐观；缓解：
  策略容量极低（一个布尔开关 + 已有规则集，规则集在 P2 基于训练分区开发），
  holdout 单次曝光是唯一的无偏检验。
- 0.75–0.78 的提升来自**语义对齐**（按基准"未描述即 No"判定）而非模型能力
  提升；"仅规则 + 闭世界"就有 0.751，说明布尔题上 8B 的贡献为负。对外表述必须
  写明这是基准协议语义下的 raw 视图，并同时给出安全视图数字。
- 是否把闭世界语义作为对外口径、holdout 用哪个视图的候选，由 owner 在 P8.5
  授权时决定；本轮只产出 validation 上的两视图数字与判定。

## 第 3 轮结果（H 基线上，离线，2026-09-30；validation development diagnostics）

布尔行与第 2 轮无关（P4n 只作用于含数值题的请求，H 的布尔请求逐字节不变），
故先在 H 基线上执行第 3 轮；若第 2 轮保留 P4n，再在其上重算同一策略。产物
`round3-V1-on-H-mac.json`、`round3-V2-on-H-mac.json`，均封存并验封。

| 配置 | raw typed EM | raw 95% CI | 布尔准确率 | 布尔假阳性 | 布尔漏判 | 安全视图 EM |
|---|---|---|---|---|---|---|
| H 基线 | 0.678 (234) | 0.617–0.730 | 0.760 | 38 | 16 | 0.678 |
| **V1（否决无规则支持的 present）** | **0.768 (265)** | 0.716–0.812 | 0.898 | 4 | 19 | 0.678 |
| V2（布尔取规则 + 闭世界） | 0.780 (269) | 0.739–0.812 | 0.916 | 6 | 13 | 0.348 |

数值行逐行未变（代码断言 + 计数：幻觉 41、不等 16）。V1 否决 37 行，其中 34 行
原为假阳性、3 行原为真阳性。

### 判定

- V1、V2 在 raw 视图都满足预声明条件（EM 高于基线、布尔假阳性下降、数值行不变）。
- **保留 V1**。V2 的 raw EM 只高 4 行（在 bootstrap 噪声内），但它把模型带引用
  的布尔答案整体换成无引用的闭世界 absent，安全视图从 0.678 掉到 0.348；V1
  的安全视图与 H 持平。

### 自审

1. V1 与 V2 之间的取舍标准（差距在噪声内时取安全视图不受损者）是结果出来后才写
   明的，属事后决定，预声明里没有；如实记录。两份产物都保留，owner 可改判。
2. 0.768 是 validation 上看着标签设计出来的策略的自评，偏乐观。V1 的 CI 下界
   0.716 高于 H 的点估计与现任 0.629，方向性结论稳健，幅度需 holdout 验证。
3. raw 视图的提升完全来自"未被规则支持的 present 改判 absent"这一基准协议语义；
   安全视图没有任何提升。对外只能写成"基准协议视图 0.77 / 安全视图 0.68"这样
   的双视图表述，不能单报前者。
4. V1 之后剩余 80 个错行里 57 个在数值题（幻觉 41、不等 16），布尔只剩 23 个。
   瓶颈已转移到数值抽取，第 2 轮（P4n）正针对它。
5. 三轮上限：第 3 轮已用完；此后不再在 validation 上做新的策略设计。第 2 轮的
   结果出来后只做一次机械重算（同一 V1 作用于被保留的基线）。

## 第 2 轮结果（本机，2026-09-30/10-01；validation development diagnostics）

| 配置 | typed EM | 95% CI | 布尔对 | 数值对 | 数值幻觉 | 数值漏抽 | 数值不等 |
|---|---|---|---|---|---|---|---|
| H 基线 | 0.678 (234) | 0.617–0.730 | 171 | 63 | 41 | 0 | 16 |
| H + P4n | 0.678 (234) | 0.617–0.733 | 171 | 63 | 35 | 5 | 17 |

30 个请求全部接受。布尔行与 H 逐行相同（225/225，状态、取值、引用），印证了
"布尔请求逐字节不变"的设计，也给出同运行时下布尔半边的可重复性证据。

### 判定

**P4n 不保留**。目标类别数值幻觉下降 6 行（41→35），但 5 行转为漏抽、1 行转为
数值不等，数值正确数不变，总 EM 未超过基线 0.678，不满足保留条件。

### 自审

1. 两条虚构数值示例把一部分"编数"变成了"弃权"，是风险偏好的移动而不是抽取能力
   的提升——与第 1 轮 P2 的教训一致：句子或示例能移动 8B 的校准点，移不动它的
   抽取精度。
2. 数值题剩余 57 个错行（幻觉 41、不等 16）在提示层面已无可用杠杆；再往上需要
   改数值抽取本身（训练，或确定性的化验行解析），不在这三轮范围内。
3. 三轮迭代至此全部用完。validation 选定候选：**H + V1**（raw 0.768 /
   安全视图 0.678，本机运行时，Llama-3.1-8B Q4，v1 提示原文，布尔前 3 块 +
   数值整份，无规则支持的 present 改判闭世界 absent）。此后不再在 validation 上
   做新的设计；仅做一次同配置复跑以确认稳定性。
