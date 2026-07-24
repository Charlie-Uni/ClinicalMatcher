# ClinicalMatcher 真实试点执行指南

状态：转换器与试点数据结构已就绪；患者抽样器和离线标注界面尚待实现。

本指南说明如何从本地受限的 MIMIC-IV-Ext Apixaban 数据，形成一个
可审计的双人多试验标注试点。它不是临床操作规程，项目输出也不能用于
诊断、治疗、自动排除患者或实际招募。

## 1. 总流程

```text
官方受限 CSV
  → 校验哈希、伪匿名化、切分 evidence
  → 固定抽取 4 名患者
  → 固定 2 条公开房颤试验
  → 生成 8 个 patient × trial 任务
  → 两名授权标注者独立标注
  → 裁决全部分歧
  → 生成无行级 ID 的试点汇总
  → 反推正式 benchmark 规模
  → 建设正式 gold
  → 最后才训练和评测模型
```

## 2. 当前数据边界

唯一可信的上游是官方
`MIMIC-IV-Ext-Apixaban-Trial-Criteria-Questions 1.0.0` CSV。旧 Excel、
embedding CSV 和旧模型结果仅作历史对照，不能成为正式数据源或新 gold。

本地目录约定：

```text
<VRI1_ROOT>/原始数据/annotated_apixaban_combined.csv
<VRI1_ROOT>/ClinicalMatcher-local/apixaban-staging-corpus.json
<VRI1_ROOT>/ClinicalMatcher-local/apixaban-staging-corpus.id-map.json
<VRI1_ROOT>/ClinicalMatcher-local/keys/apixaban-pseudonym-v1.key
```

已完成的真实导入包含：

- 100 份患者笔记；
- 23 条 legacy Apixaban 问题；
- 2300 条问答记录；
- 766 个准确、非重叠的 evidence 段；
- 2033 条已回答标签；
- 265 条 `not_specified`；
- 2 条保持未决的源数据异常。

主 staging corpus 不含原始 `note_id` 或 `hadm_id`。原始 ID 只存在于单独
的 `0600` ID map 中。密钥、corpus、ID map、标注文件和裁决文件不得提交
Git、上传聊天、发送邮件或放入无访问控制的共享目录。

官方扩展 CSV 没有可用 `index_date`。在授权的 MIMIC 日期元数据完成本地
关联前，所有依赖“最近 N 天/月”等时间窗的判断必须是 `unknown`；不得
从脱敏占位符猜日期。

## 3. 固定患者抽样

患者抽样由程序完成，用户不手工挑选。试点固定为：

```text
4 名患者 × 2 条试验 = 8 个 patient × trial 单元
```

### 3.1 冻结算法

算法标识：

```text
clinicalmatcher-pilot-patient-hash-v1
```

步骤：

1. 读取 staging corpus 文件字节并计算 `corpus_sha256`。
2. 验证 corpus 中每个 `patient_id` 唯一。
3. 对每名患者计算：

```text
sampling_hash =
  SHA256(
    method_id
    + "\0"
    + corpus_sha256
    + "\0"
    + patient_id
  )
```

4. 按 `(sampling_hash, patient_id)` 升序排列。
5. 取前 4 名；不能因为病例太难、类别不理想或模型表现不好重新抽取。
6. 保存本地受限 selection manifest。

### 3.2 selection manifest 必须记录

```text
manifest_version
manifest_sha256
method_id
corpus_sha256
requested_patient_count
eligible_patient_count
selected pseudonymous patient IDs
每个入选患者的 sampling_hash
生成时间
代码 commit
```

清单含行级伪匿名 ID，仍是受限衍生物，只保存在
`<VRI1_ROOT>/ClinicalMatcher-local/`。

相同 corpus、算法版本和代码必须得到完全相同的 4 名患者。corpus 内容
变化时哈希变化，必须生成新清单，不允许悄悄沿用旧选择。

计划中的命令接口为：

```text
clinical-matcher-pilot select-patients ...
```

该子命令尚待实现；在实现并通过合成测试前，不手工模拟这一步。

## 4. 固定两条公开试验

试点试验来自 ClinicalTrials.gov，不从患者标签或模型结果反向挑选。
筛选政策需要预先冻结，例如：

- condition 为 Atrial Fibrillation；
- study type 为 Interventional；
- 招募状态属于预声明集合；
- 有完整 eligibility 文本；
- 首次发布日期在预声明范围内；
- inclusion/exclusion 极性可保留；
- 不按最近更新时间选择；
- 不因患者更容易匹配而替换试验。

必须记录 NCT ID、registry version、更新时间、eligibility 原文哈希、
criterion ID、检索条件、过滤规则和纳入理由。后续标注只读冻结快照，
不访问实时 API。

Apixaban 的 23 个旧问题只作 regression reference。它们不是两条新试验的
完整 patient–trial gold，也不是双人独立标注结果。

## 5. 生成离线标注包

4 名患者与 2 条试验做笛卡尔积：

```text
患者 1 × 试验 A
患者 1 × 试验 B
患者 2 × 试验 A
患者 2 × 试验 B
患者 3 × 试验 A
患者 3 × 试验 B
患者 4 × 试验 A
患者 4 × 试验 B
```

每个任务应显示：

- 伪匿名患者 ID；
- 可选择的 evidence 段及 evidence ID；
- 冻结的试验版本；
- 完整 inclusion/exclusion criteria；
- criterion 极性、hard/soft 和时间窗提示；
- criterion decision、evidence IDs 和 rationale 输入；
- trial decision、相关性等级和 rationale 输入；
- 单元 active minutes。

标注包只能离线读取本地受限 corpus。界面不得加载外部脚本、分析服务、
遥测、第三方字体或网络资源。

## 6. 两人独立标注

需要两个不同的匿名标注者 ID，例如：

```text
annotator-a
annotator-b
```

真实 MIMIC 文本不能发送给没有相应授权的人。第二位标注者必须在适用的
数据许可与受控环境下访问；如果暂时没有合规的第二人，只能测试界面，
不能把单人结果称为双人 gold。

第一次标注期间，两人不得：

- 查看对方答案；
- 讨论具体病例；
- 查看模型预测；
- 查看 RAG 检索结果；
- 使用旧模型的 patient–trial 结论。

每条 criterion 填写：

### `eligible`

现有证据支持该患者满足当前标准。

### `ineligible`

现有证据支持该患者不满足当前标准。

### `unknown`

病历未提及、日期缺失、单位不兼容、文本冲突或证据不足，无法安全判断。
`unknown` 不等于 `ineligible`。

Evidence 必须真正支持判断，不能只因为出现相同关键词而选择。没有证据时：

```text
decision = unknown
evidence_ids = []
```

每个 patient × trial 单元单独记录 active minutes，不计休息、等待和无关
工作。完成 criteria 后再填写 trial judgment 和 `0–3` 相关性等级。

## 7. 裁决

只有两份独立文件都完成后才能进入裁决。工具自动识别：

- trial decision 分歧；
- trial relevance 分歧；
- criterion decision 分歧；
- criterion evidence 分歧。

无分歧结果锁定为 `agreed_without_dispute`，不能在裁决阶段暗改。有分歧
单元必须写最终判断、最终 evidence、理由和正数
`active_person_minutes`。

总人分钟的例子：

```text
两个人共同讨论 10 分钟 = 20 person-minutes
```

只要存在一个 `unresolved`，就不能生成容量汇总。

## 8. 试点汇总与容量计划

完成裁决后，`clinical-matcher-pilot summarize` 生成：

- 标注总人分钟；
- 单元中位时间和 P75 时间；
- patient–trial 分歧率；
- 裁决总人分钟；
- trial/criterion/evidence 一致率；
- Cohen’s kappa；
- 完成单元数；
- 输入哈希、代码 commit 和汇总哈希。

汇总不包含患者、试验、criterion、evidence 或标注者行级 ID，也不包含
临床文本。它仍应先作为受限数据衍生物保存，治理审核后才能决定是否公开。

容量规划器使用真实 P75 时间和分歧率，枚举：

```text
2 条试验 × N 名患者
3 条试验 × N 名患者
4 条试验 × N 名患者
```

手工填写的时间估计只能用于 provisional planning，不能解锁正式 trial
snapshot。

## 9. 正式 gold

正式规模冻结后，完整 gold 必须覆盖：

```text
patient × trial
patient × trial × criterion
criterion × evidence
```

要求两人独立标注、全部裁决、固定版本、患者与试验泄漏检查、语义近重复
检查以及完整数据/代码血缘。

## 10. 模型开发顺序

只有正式 gold 和评测切分冻结后，才依次开展：

1. 规则基线；
2. BM25；
3. dense retriever；
4. hybrid retrieval；
5. reranker；
6. frozen LLM structured output；
7. 神经符号验证；
8. coverage–risk 与弃权；
9. SFT/LoRA；
10. IB 去噪消融；
11. RAG 与长上下文对比。

不先微调再制作 gold，避免测试泄漏和模型参与定义正确答案。

## 11. 当前分工

用户需要：

1. 安全备份 HMAC 密钥和 ID map；
2. 确认第二位标注者具有相应数据访问授权；
3. 在标注包就绪后完成独立标注和裁决。

代码侧下一步：

1. 实现并测试固定患者抽样器；
2. 冻结两条公开房颤试验；
3. 生成 8 个 unit 的受限 manifest；
4. 实现完全离线的本地标注界面；
5. 生成两份隔离的标注文件；
6. 校验完整性、独立声明和裁决状态。

