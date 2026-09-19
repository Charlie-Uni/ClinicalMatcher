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
