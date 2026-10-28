# 训练数据规范（Data Specification）

> 适用范围：AgentForge 数据流水线全阶段。任何进入 `datasets` 的数据都必须满足本文约定，
> 质检规则与导出格式均以此为准。

## 1. 样本字段规范

### 1.1 统一结构

```json
{
  "instruction": "用户问题或任务指令（必填）",
  "input": "补充上下文，可为空",
  "output": "期望的标准答案或执行结果（必填）",
  "system": "系统提示词，可为空",
  "meta": { "任意附加信息": "不参与训练，仅用于追溯" }
}
```

### 1.2 字段命名兼容

采集到的原始数据可能存在多种命名，入湖时统一归一（`Sample.from_raw`）：

| 统一字段 | 可接受的原始键名（按优先级） |
|---|---|
| `instruction` | `instruction` → `question` → `prompt` |
| `output` | `output` → `answer` → `completion` |
| `input` | `input` |
| `system` | `system` |

未被识别的键一律收进 `meta`，不丢弃。

### 1.3 硬性约束

| 约束 | 值 | 违反后果 |
|---|---|---|
| `instruction` 非空 | 必填 | 清洗阶段拒收（`missing_instruction`） |
| `output` 非空 | 必填 | 清洗阶段拒收（`missing_output`） |
| 样本总长度 | ≤ 2000 字符 | 清洗阶段拒收（`too_long`） |
| 占位符 | 禁止 `TODO`/`FIXME`/`XXX`/`待补充`/`lorem ipsum` | 清洗阶段拒收（`placeholder_content`） |
| 敏感信息 | 禁止身份证/银行卡/密码/密钥明文 | 清洗阶段拒收（`sensitive_word`） |

## 2. 样本状态机

```
raw ──clean──► cleaned ──dedup──► cleaned ──label──► labeled ──qc──► passed
  │                                    │                        └─► rejected
  └────────────── 任一阶段不通过 ───────┴────────────────────────────► rejected
```

- `rejected` 必须写入 `reject_reason`，便于按原因聚合分析；
- 状态只能单向推进，`passed` 不可被后续阶段改回，除人工重置；
- 每阶段只处理上一阶段完成的样本，因此流水线可重复调用且幂等。

## 3. 清洗规则

| 规则 | 动作 | 记录键 |
|---|---|---|
| Unicode NFKC 归一 | 全角转半角、兼容字符统一 | `unicode_nfkc` |
| 控制字符移除 | 删除 `\x00-\x1f` 中不可见字符 | `remove_control_chars` |
| HTML 标签移除 | `<...>` → 空格 | `strip_html_tags` |
| HTML 实体反转义 | `&nbsp;` → 空格 | `unescape_html_entities` |
| 连续空白压缩 | 多空格 → 单空格 | `collapse_spaces` |
| 连续换行压缩 | ≥3 换行 → 2 换行 | `collapse_newlines` |
| PII 脱敏 | 手机/身份证/邮箱 → `[已脱敏]` | `mask_phone` / `mask_id_card` / `mask_email` |
| URL 脱敏（仅 output） | 链接 → `[链接]` | `mask_url` |
| 首尾空白 | strip | `strip_whitespace` |
| 问句标点补全 | 缺标点时按疑问词补 `？` 或 `。` | `fix_instruction_punct` |

所有变更写入 `cleaned.meta.clean_changes`，可逐条回溯。

## 4. 去重规则

- 算法：SimHash（64 位，基于字符 3-gram 分片 + blake2b 哈希）
- 判定：汉明距离 ≤ 阈值即视为近重复，保留首次出现者，后者置 `rejected`（`duplicate`）
- 阈值标定（中文短文本实测）：改一字 10~12，主题无关 24~30 → **默认阈值 14**
- 长文档建议下调至 6~8；生产环境应按实际语料重新标定并在 `meta` 记录阈值

## 5. 标注规范

| 标签 | 取值 | 说明 |
|---|---|---|
| `intent` | `knowledge_qa` / `computation` / `data_query` / `tool_use` / `chitchat` / `other` | 意图分类 |
| `difficulty` | `easy` / `medium` / `hard` | 按推理步数与领域复杂度 |
| `tools` | 工具名数组，仅限已注册工具 | 仅在实际需要外部工具时给出 |
| `tags` | 1-3 个主题词 | 用于聚类与检索 |
| `quality` | `high` / `medium` / `low` | 粗粒度质量判断 |
| `source` | `llm` / `rule` | 标注来源，用于对比人工抽检结果 |

- 有可用模型时走批量 LLM 标注（每批 6 条）；失败自动降级规则标注；
- **两种来源必须可区分**：`source` 字段是抽检与质量归因的前提。

## 6. 质检规则

| 规则 | 级别 | 权重 | 触发条件 |
|---|---|---|---|
| `output_present` | error | 1.0 | `output` 为空 |
| `output_min_length` | error | 1.0 | `output` 长度 < 10 |
| `prompt_not_empty` | error | 1.0 | `instruction` 为空 |
| `no_placeholder` | error | 1.0 | 命中占位符词表 |
| `no_sensitive` | error | 1.0 | 命中敏感词表 |
| `not_echo` | error | 0.8 | `output` 与 `instruction` 完全相同 |
| `no_html` | warning | 0.3 | `output` 残留 HTML 标签 |
| `length_ratio` | warning | 0.3 | 输出/输入长度比不在 [0.05, 30] |
| `language_consistent` | warning | 0.2 | 问句含中文而答案不含（或反之） |
| `label_present` | warning | 0.4 | 缺少 `intent` 标签 |

**判定逻辑**：
- 任一 `error` 命中 → `rejected`，`quality_score` 仍按权重计算（保留信息量）；
- 仅 `warning` 命中 → `passed`，但 `quality_score` 下降；
- `quality_score = 通过规则权重和 / 总权重`，用于样本排序与抽样复核。

**设计取舍**：不把所有规则设为 error，否则一条弱规则会拦掉整批好样本；warning 机制让弱信号只降分不否决。

### 6.1 答案忠实度校验（groundedness，阶段级）

形式规则判不出「格式合格但事实错误」的样本，因此提供独立的校验阶段（默认不在全量流水线中执行）：

| 项 | 规则 |
|---|---|
| 输入 | `status = passed` 的样本 |
| 依据 | 用样本的问题检索平台知识库，取 Top3 片段 |
| 前置门槛 | 最高相关度 < 0.35 或检索为空 → 直接判 `insufficient`，**不调用模型**（控成本） |
| 判定 | 模型输出 `supported` / `contradicted` / `insufficient` |
| 处置 | `contradicted` → 拒收（`groundedness_contradicted`），质量分压到 ≤0.3；其余保留 |
| 留痕 | 写入 `qc_records`（rule=`groundedness`），并记入 `label.groundedness` |
| 幂等 | 已判定过的样本默认跳过，重复执行不重复计费 |
| 局限 | 只判定「与知识库是否一致」，库里没有相关文档时无法判断事实对错 |

## 7. 导出格式

### 7.1 chat_jsonl（默认，OpenAI / DeepSeek 微调格式）

```json
{"messages":[{"role":"system","content":"..."},{"role":"user","content":"问题+输入"},{"role":"assistant","content":"答案"}]}
```

### 7.2 sharegpt（LLaMA-Factory 等）

```json
{"conversations":[{"from":"system","value":"..."},{"from":"human","value":"..."},{"from":"gpt","value":"..."}]}
```

### 7.3 alpaca（单轮指令微调）

```json
[{"instruction":"...","input":"...","output":"...","system":"..."}]
```

**导出约束**：
- 仅导出 `status = passed` 的样本（`only_passed=true`）；
- 可选按 `split`（train/val/test）过滤，避免验证集混入训练集；
- 每次导出生成 `dataset_versions` 快照，冻结 `item_count` / `pass_rate` / 完整质量指标；
- 文件落盘至 `data/exports/dataset_{id}_v{version}_{format}.jsonl`。

## 8. 质量指标定义

| 指标 | 定义 | 用途 |
|---|---|---|
| `pass_rate` | passed / total | 数据集整体可用率 |
| `avg_quality` | 平均 `quality_score` | 样本平均质量水平 |
| `drop_reasons` | 各拒收原因计数 | 定位采集源质量问题 |
| `rule_hits` | 各规则命中次数 | 定位最需治理的短板 |
| `intent_distribution` | 通过样本的意图分布 | 判断类别均衡性 |
| `duplicates` | 去重命中数 | 评估采集去重效果 |

> 指标全部随 `dataset_versions.quality` 冻结，使「换了清洗规则后效果是否变好」可以用数据回答，而不是靠感觉。
