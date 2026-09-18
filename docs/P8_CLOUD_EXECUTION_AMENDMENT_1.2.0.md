# P8 修订 1.2.0：租用 GPU 实例执行推理侧实验

状态：owner 决定已记录（2026-09-17，原话"数据传输确认"）；本修订只放开
"仅本地"这一条执行位置约束，其余约束全部保留。

## 修订内容

[P8 规格](P8_INFERENCE_OPTIMIZATION_SPEC.md) 第 2 节第 2 条原文要求 MIMIC
衍生数据仅在本地授权环境使用。1.2.0 允许在 owner 租用并单独控制的一台
GPU 实例上执行推理侧实验，条件如下：

1. **数据协议由 owner 负责。** owner 声明已核对 PhysioNet credentialed DUA
   中关于云端使用的条款并接受该平台的安全条件；本仓库不对合规性做判断，
   只记录该声明。
2. **最小传输集。** 只传 access manifest 登记且当前链路实际打开的文件：
   validation 分区的 evidence 与 gold、E3 所需的 `rules` validation 预测、
   access manifest、E1 决定记录与选例集（零示例）。**不传**：二级 holdout
   分区（`~/.clinicalmatcher-p8-lifetime-state/`）、train-fit 分区、locked
   test、P7 封存件、`keys/`、id-map、任何原始语料。
3. **路径与权限不变。** 实例上重建与本机相同的绝对路径，文件 0600、目录
   0700，pin 逐字节校验；holdout 生命周期状态目录在实例上为空，holdout
   仍未授权、单次曝光的规则不变。
4. **传输与销毁。** rsync over SSH（专用 ed25519 密钥，仅公钥登录），
   由 owner 在本机终端执行；结果产物 rsync 回本机 owner-only 目录后，
   实例上的数据、模型输出与实例本身一并销毁。
5. **运行时身份重 pin。** 实例上的每个契约由 `prepare-e1` 现场探测引擎
   版本并记录与父契约的偏差；模型 blob 必须与父契约 digest 一致
   （`46e0c10c…`，已核对）。硬件与平台身份在本修订与运行记录中登记，
   并作为后续 runner 版本把它写入契约 `runtime_identity` 的待办。
6. **跨硬件可比性控制。** 在实例上先复现本机已完成的试验（同一契约语义、
   同一患者），比较行级输出是否一致；不一致则在运行记录中如实登记为
   硬件偏差，不得据此改写本机结果。
7. **输出纪律不变。** 终端、日志、Git、在线会话只出现聚合数与哈希；
   Ollama 仅绑定 127.0.0.1；实例不开放其他端口。

## 实例登记（2026-09-17）

- 平台：AutoDL（seetacloud），容器实例，主机名
  `autodl-container-0eab4a8ba4-e8046412`；SSH 端口 43016，root，公钥登录。
- GPU：`nvidia-smi` 报 NVIDIA GeForce RTX 4080 SUPER，32,760 MiB（vGPU-32GB
  规格），驱动 580.76.05；Ubuntu 22.04.4；128 逻辑核（共享）；数据盘
  `/root/autodl-tmp` 50 GB。
- 运行时：Ollama 0.34.1（本机 0.34.0；父契约 0.32.6），模型
  `llama3.1:latest` manifest digest `46e0c10c039e0191…` 与父契约一致，
  `OLLAMA_MODELS=/root/autodl-tmp/ollama`，`OLLAMA_HOST=127.0.0.1:11434`。
- 代码：仓库 `bf5ed45`（CI 绿），uv 0.12.15，CPython 3.11.16，哈希锁定依赖，
  非可编辑安装，runner 1.0.3。
- 安装脚本未能自动检测 GPU（缺 lspci/lshw）；首次运行前须确认推理落在
  GPU 上（`ollama ps` 显示 100% GPU 或服务日志的 inference compute 行）。

## 传输与首次校验记录（2026-09-18）

- 传输由 owner 在本机终端用 scp 逐文件完成（6 个文件，总计约 785 KB），
  目录 0700、文件 0600，实例上文件计数 6。
- 首次 `check-access` 被拒，原因是 manifest 校验要求二级 holdout 的登记
  路径位于当前用户主目录下的守护金库（`~/.clinicalmatcher-p8-lifetime-state`）
  之内，而实例上的主目录是 `/root`。处理方式不改代码：实例上的运行器进程
  以 `HOME=/Users/leaf` 执行，使金库路径与本机逐字相同；该目录在实例上不
  存在且永远不会被创建（holdout 文件不传输、任何步骤不打开）。对齐后
  `check-access` 通过："P8.1 metadata gate passed"。
- 实例上的契约文件以 `-gpu` 后缀命名（`e1-contract-v2-a24-gpu.json` 等），
  结果拷回本机后与本机产物并列保存。

## 第二台实例登记（2026-09-18）

- 第一台实例（vGPU-32GB）数据目录已删除后由 owner 释放。
- 第二台：AutoDL 容器 `autodl-container-txexpr46b5-52d3c69c`，SSH
  `connect.weste.seetacloud.com:13746`（root，公钥登录，本机别名 `autodl`）。
- GPU：NVIDIA GeForce RTX 5090 D，32,607 MiB，驱动 595.71.05；Ollama 日志
  `library=CUDA compute=12.0`；208 逻辑核（共享）；数据盘 50 GB。
- 运行时：Ollama 0.34.2（第一台 0.34.1，本机 0.34.0，父契约 0.32.6），模型
  digest `46e0c10c039e0191…` 与父契约一致；仓库 `44915de`，runner 1.0.5，
  CPython 3.11.16（因代理截断 GitHub 下载，改由本机下载构建包后 scp 到实例，
  以 `UV_PYTHON_INSTALL_MIRROR=file://` 本地镜像安装；解释器版本不变）。
- 最小传输集与目录/权限规则同第一台；`HOME=/Users/leaf` 对齐规则同前。

## 跨硬件对照结果（2026-09-18）

单患者试验在两台机器上逐字一致，但同一契约语义下的整轮 a24（runner
1.0.3）在 5 位双方都接受的患者上只有 1 位逐行一致；115 行中类型化答案一致
75 行、引用一致 71 行；另有 2 位患者本机无效、GPU 接受。结论：temperature 0
与固定 seed 不能保证长批量生成跨 Metal/CUDA 一致，且差异足以改变请求结局。
因此：两种运行时的结果不合并；每个运行记录其运行时；GPU 结果与本机产生的现任
只能做定性比较，任何跨运行时的"超过现任"宣称都必须先在 GPU 上重跑冻结的
v1 长上下文候选作为同运行时基线。E2 在 GPU 实例上执行时，该基线是前置条件。

## 不改变的事项

locked test 永久关闭；P7 封存件禁止读取；二级 holdout 未授权、单次曝光、
只能由 owner 显式授权；validation 数字仍是 development diagnostic；
E2 与恢复 F2 仍是 owner 决策；`docs/PROJECT_TODO.md` 不进入任何流程。
