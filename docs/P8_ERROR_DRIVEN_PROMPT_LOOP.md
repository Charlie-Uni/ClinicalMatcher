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

## 第 3 步（条件触发）

若第 1 轮某项保留，第 2 轮只针对剩余最大类别再改一句；第 3 轮为最后一轮。
之后是否微调（需证据标注）由 owner 决定。
