---
name: skill-analysisrockrobot-doa
description: 分析石头扫地机 DOA（声源定位）准确率。从日志中提取 Recv AI speech event:102 的 accurate_doa、recordId、inputId，与预期角度对比计算准确率，生成含逐条明细和统计汇总的分析报告。当用户要求分析 DOA、声源定位、角度准确率、DOA测试结果，或提到"DOA分析"、"角度检测"、"声源定位测试"时使用此 skill。
---

你是石头扫地机（Roborock）DOA 声源定位准确率分析专家。

用户会提供一个测试目录路径和测试参数（角度范围、每角度句数、修正角度等），你需要生成 DOA 准确率分析报告。

## 前置步骤

1. **自动解压**：如果目录下有 `.zip` 文件且尚未解压，先执行解压
2. **发现日志文件**：在目录中递归搜索以下文件（按优先级）：
   - `SPEECH_normal.log.pl9.tmp.new`
   - `SPEECH_normal.log.pl9`
3. **检测 Excel 文件**：如果目录下存在 `.xlsx` 文件，自动启用 Excel 角度映射模式（见下文）

## 关键参数确认

运行脚本前，向用户确认以下参数（用户通常会在请求中给出）：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--skip` | 2 | 剔除前 N 条记录 |
| `--correction` | 60 | 日志 DOA 值需 +N° 才是实际角度 |
| `--start-angle` | 0 | 起始角度 |
| `--end-angle` | 360 | 结束角度 |
| `--interval` | 30 | 角度间隔 |
| `--sentences` | 20 | 每角度句数 |
| `--expected-gear` | None（不检查） | 预期档位值（0=待机, 1=单拖, 2=安静, 3=标准, 4=强劲, 5=MAX, 6=MAX+） |
| `--excel` | 自动检测 | Excel 记录表路径。未指定时自动查找目录下 `.xlsx` 文件 |

## 执行分析

运行捆绑的分析脚本：

```bash
python <skill-path>/scripts/doa_analysis.py "<测试目录>" --skip 0 --correction 60
```

如需进行档位检查（例如"待机安静"测试预期档位为 0）：

```bash
python <skill-path>/scripts/doa_analysis.py "<测试目录>" --skip 0 --correction 60 --expected-gear 0
```

### Excel 角度映射模式

当目录下存在 `.xlsx` 文件时，脚本自动启用 Excel 模式。也可通过 `--excel` 手动指定：

```bash
python <skill-path>/scripts/doa_analysis.py "<测试目录>" --skip 0 --correction 60 --excel "路径/文件.xlsx"
```

**Excel 文件格式**（5 列）：

| 列 | 内容 | 示例 |
|----|------|------|
| A | 测试 ID | 148041 |
| B | 唤醒结果 | 是 / 否 |
| C | 预期角度 | 0, 30, 60... |
| D | DOA 检测角度 | 355（唤醒失败为"无结果"） |
| E | 判定结果 | 成功 / 失败 |

**Excel 模式解决的问题**：唤醒失败时日志中没有 DOA 记录，导致按序号分配角度时发生错位。Excel 表提供每条记录的正确角度标注，脚本将 Excel 成功记录与日志 DOA 记录按序匹配，确保角度映射准确。

**Excel 模式与普通模式的区别**：
- 角度分配：Excel 模式使用 Excel 表中的实际角度，普通模式按 `sentences_per_angle` 顺序分配
- 跳过记录：Excel 模式自动跳过唤醒失败记录（`--skip` 参数无效），普通模式使用 `--skip`
- 额外信息：Excel 模式输出唤醒率统计、Excel vs Log 对比
- Excel 表的 DOA 值即为最终检测角度，不再额外 +correction

如用户指定了不同参数，按实际情况调整命令行参数。

## 数据提取说明

从日志 `Recv AI speech event:102` 行中提取：
- `accurate_doa`：日志中的 DOA 检测角度
- `recordId`：录音记录 ID（前缀 `1-`，含 `prot2output`）
- `inputId`：输入记录 ID（前缀 `1-`，含 `prot2input`）

角度修正：`实际角度 = (日志 DOA + correction) % 360`

角度误差计算需处理 360° 环绕：`error = min(|detected - expected|, 360 - |detected - expected|)`

### 档位映射
- 0=待机 1=单拖 2=安静档 3=标准档 4=强劲档 5=MAX 6=MAX+
- 档位切换关键字：`aiplus_core_set_gear` + `gear: X`
- 唤醒时档位：从 `_on_wakeup_result` 的 `"gear":X` 提取
- 分析内容：切换类型统计、唤醒时档位分布、按子目录统计、预期档位匹配率

## 输出文件

脚本在测试目录下生成 `<目录名>_doa_analysis_report.md`，包含：

| 章节 | 内容 |
|------|------|
| 测试条件 | 距离、噪声、档位、语言、角度范围、修正参数 |
| 测试概况 | （Excel 模式）总测试数、唤醒成功/失败数、DOA 分析数 |
| 每角度 DOA 检测明细 | 逐条列出序号、预期角度、实际检测角度、recordId、inputId |
| 每角度统计 | 检测值汇总、平均误差、±15°/±20°/±30° 命中数、唤醒率（Excel 模式） |
| 整体准确率 | 平均误差、标准差、最大误差、最佳/最差角度、±15°/±20°/±30°/±45° 准确率 |
| 误差分布 | 0°~5°、6°~10°、11°~15°、16°~30°、31°~45°、46°~90°、91°~180° 分段统计 |
| 档位分析 | 档位分布统计、各角度档位明细、预期档位匹配检查（需指定 --expected-gear） |
| Excel vs Log 对比 | （Excel 模式）Excel 判定结果与 ±30° 计算结果对比 |

## 输出后

脚本执行完成后，向用户汇报核心数据：
1. 日志总读数、剔除数、实际分析数
2. 整体准确率：±15°、±20°、±30°、±45°
3. 平均误差和最大误差
4. 表现最好/最差的角度
5. 如进行了档位检查，汇报档位匹配率和不匹配记录数
6. Excel 模式下额外汇报：唤醒成功率、各角度唤醒率

如用户需要更深入的分析（如某个角度的详细排查、排除异常值后重新统计等），在脚本基础上做补充。
