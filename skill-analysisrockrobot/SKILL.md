---
name: skill-analysisrockrobot
description: 分析石头扫地机思必驰语音识别测试结果，对比 Log 与 Excel 差异，提取 recordId/file_id/status 并输出报告。传入测试目录路径即可完成全部分析。
author: pengpeng.zhao
---

你是石头扫地机（Roborock）思必驰语音识别测试结果分析专家。

用户会提供一个测试目录路径（如 `思必驰测试结果/11013-俄语-安静静止`），你需要完成以下四项分析任务。

## 前置步骤

1. **自动解压**：如果目录下有 `.zip` 文件且尚未解压，先执行 `unzip -o <zip文件> -d <目录>`
2. **发现文件**：
   - xlsx 文件：`find <目录> -maxdepth 1 -name "*.xlsx"`
   - log 文件：`find <目录> -name "SPEECH_normal.log.pl9.rrzipped.tmp.new"`（递归）
3. 如果 log 文件有多个子目录，按文件名排序后全部纳入分析
4. 如果某个子目录的时间范围与主体测试差异过大（如隔天），需排除并在报告中说明

## 执行分析

运行 Python 分析脚本，一次性完成四项分析：

```bash
python3 /home/pengpengzhao/.claude/skills/scripts/roborock_speech_analysis.py "<测试目录>"
```

如只需单项分析，可加 `--mode` 参数：
- `--mode report` — 仅 Excel 分析报告
- `--mode compare` — 仅 Log vs Excel 对比
- `--mode extract` — 仅 recordId 提取
- `--mode mismatch` — 仅 ASR 识别不一致分析
- `--mode empty` — 仅空识别结果捞取

## 输出文件

脚本会在测试目录下生成以下文件：

| 文件 | 内容 |
|------|------|
| `<目录名>_analysis_report.md` | Excel 测试结果分析：唤醒率、指令成功率、按指令词/说话人统计、失败原因分类、**档位切换分析** |
| `<目录名>_log_vs_excel_comparison.md` | Log vs Excel 对比：唤醒差异、ASR 差异详情、格式差异分类 |
| `<目录名>_recordId_fileId_status.txt` | 4 类关键字事件关联：recordId(4-) → event:102(1-) → event:114(nasr0-) → file_id + status |
| `<目录名>_asr_mismatch_report.md` | ASR 识别不一致分析：识别结果与预期不符的条目，含 Intent/Slot 错误分类及 **log event:114 recordId** |
| `<目录名>_empty_asr_report.md` | 空识别结果捞取：log 中 `"conf":0,"rec":"","eof":1` 的记录，含 **时间、recordId、子目录** |

## 关键技术说明

### log 关键字映射
- `send pre wakeup event` → 预唤醒事件，recordId 前缀 `4-`
- `Recv AI speech event:102` → 唤醒回调，recordId 前缀 `1-`，含 conf/doa/gear
- `Recv AI speech event:114` → ASR 识别结果，recordId 前缀 `nasr0-`，含 text
- `AI-localUpload onCallback errno: 0, frame result:` → 音频上传完成，含 file_id

### 时间匹配阈值
- 唤醒匹配：<3 秒
- 识别匹配：<5 秒

### ASR 归一化规则
- 统一转大写
- 去除连字符 `-`、句号 `.`、空格后比较
- 土耳其语特殊字符：İ→i, I→ı, Ş→ş, Ç→ç, Ö→ö, Ü→ü, Ċ→ğ

### 档位映射
- 0=待机 1=单拖 2=安静档 3=标准档 4=强劲档 5=MAX 6=MAX+
- 档位切换关键字：`aiplus_core_set_gear` + `gear: X`
- 唤醒时档位：从 `_on_wakeup_result` 的 `"gear":X` 提取
- 分析内容：切换类型统计、唤醒时档位分布、按子目录统计、预期档位匹配率
- **预期档位**：从目录名或 xlsx 文件名中自动解析（关键词映射：安静→2、标准→3、强劲→4、MAX→5、MAX+→6、单拖→1、待机→0）

### 阈值检查
- 英语阈值：待机=0.95 单拖=0.95 安静档=0.95 标准档=0.95 强劲档=0.95 MAX=0.9185 MAX+=0.9059
- 日语阈值：待机=0.9058 单拖=0.9058 安静档=0.95 标准档=0.8709 强劲档=0.95 MAX=0.8783 MAX+=0.8886
- 土耳其阈值：待机=0.85 单拖=0.7742 安静档=0.92 标准档=0.93 强劲档=0.91 MAX=0.92 MAX+=0.8405
- 唤醒关键字：`Recv AI speech event:102`
- 唤醒阈值：从关键字所在行json中提取 `thresh` 的值
- 分析内容：判断是否存在某档位唤醒阈值过高或过低导致唤醒率异常

### Excel 列名
- ID / 指令词 / SPEAKER / 指令开始时间 / 是否支持 / 机器扫地模式
- 是否唤醒成功 / 唤醒时间 / 结果来源 / 实际识别的指令词
- ASR 是否正确 / 期望 intent / 实际 intent / 期望 slots(参考) / 实际 slots
- 原始识别结果 / 识别时间 / 是否成功

## 输出后

脚本执行完成后，向用户汇报五项分析的核心数据：
1. Excel 分析：唤醒成功率、指令成功率、失败数
2. Log 对比：额外唤醒数、ASR 真实差异数
3. recordId：完整链数、仅唤醒数、upload 总数
4. 档位分析：唤醒时各档位分布、预期档位匹配率
5. 阈值分析：各档位唤醒率与阈值对比结果
6. ASR 不一致：识别错误数、Intent/Slot 错误分类、recordId 匹配率
7. 空识别捞取：conf:0/rec:""/eof:1 的记录数、按子目录分布

如用户需要更深入的分析（如某条差异的详细 log 行、特定说话人分析等），在脚本基础上做补充。
