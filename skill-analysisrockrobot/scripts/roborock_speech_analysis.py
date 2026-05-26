#!/usr/bin/env python3
"""
石头扫地机（Roborock）思必驰语音识别测试结果分析工具

用法:
  python3 roborock_speech_analysis.py <测试目录> [--all|--report|--compare|--extract]

功能:
  --all     (默认) 执行全部四项分析
  --report  仅输出 Excel 测试结果分析报告 (analysis_report.md)，含档位分析
  --compare 仅输出 Log vs Excel 对比报告 (log_vs_excel_comparison.md)
  --extract 仅输出 recordId/fileId/status (recordId_fileId_status.txt)

档位映射:
  0=待机 1=单拖 2=安静档 3=标准档 4=强劲档 5=MAX 6=MAX+
"""

import argparse
import glob
import os
import re
import subprocess
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import pandas as pd


# ============================================================
# 档位名称映射
# ============================================================

GEAR_NAMES = {
    0: "待机(0)", 1: "单拖(1)", 2: "安静档(2)",
    3: "标准档(3)", 4: "强劲档(4)", 5: "MAX(5)", 6: "MAX+(6)",
}

# 档位关键词映射（从文件名/目录名解析预期档位，顺序重要：MAX+ 先于 MAX）
GEAR_KEYWORDS = [
    ("MAX+", 6), ("MAX", 5), ("强劲", 4), ("强力", 4), ("标准", 3),
    ("安静", 2), ("单拖", 1), ("待机", 0),
]


def parse_expected_gear(dir_name):
    """从目录名或 xlsx 文件名中解析预期档位"""
    for keyword, gear in GEAR_KEYWORDS:
        if keyword in dir_name:
            return gear
    return None


# ============================================================
# 通用工具函数
# ============================================================

def find_log_files(base_dir):
    """查找目录下所有 SPEECH_normal.log.pl9.rrzipped.tmp.new 文件"""
    pattern = os.path.join(base_dir, "**", "SPEECH_normal.log.pl9.rrzipped.tmp.new")
    files = glob.glob(pattern, recursive=True)
    return sorted(files)


def find_xlsx(base_dir):
    """查找目录下的 xlsx 文件"""
    pattern = os.path.join(base_dir, "*.xlsx")
    files = [f for f in glob.glob(pattern) if not f.endswith('~')]
    if not files:
        raise FileNotFoundError(f"目录 {base_dir} 下未找到 xlsx 文件")
    return files[0]


def unzip_if_needed(base_dir):
    """如果目录下有 zip 文件且尚未解压，则解压"""
    zip_files = glob.glob(os.path.join(base_dir, "*.zip"))
    for zf in zip_files:
        subprocess.run(["unzip", "-o", "-q", zf, "-d", base_dir], check=False)


def parse_log_ts(line):
    """解析 log 行的时间戳，格式: 2026/4/15 21:32:34"""
    m = re.match(r'(\d{4}/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{1,2}:\d{1,2})', line)
    if m:
        return datetime.strptime(m.group(1), "%Y/%m/%d %H:%M:%S")
    return None


def parse_excel_dt(val):
    """解析 Excel 时间字段，兼容有无毫秒、有无时区"""
    if pd.isna(val):
        return None
    s = str(val)
    s_clean = re.sub(r'[+-]\d{2}:\d{2}$', '', s)
    for fmt in ["%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"]:
        try:
            return datetime.strptime(s_clean, fmt)
        except ValueError:
            pass
    return None


def normalize_asr(s, lang=None):
    """ASR 文本归一化：统一大写，去连字符/句号/空格"""
    s = s.upper().replace('-', ' ').replace('.', ' ').replace('  ', ' ').strip()
    s = s.replace(' ', '')
    return s


# ============================================================
# Part 0: 档位切换分析
# ============================================================

def analyze_gear(base_dir):
    """分析档位切换和唤醒时档位分布，返回报告行列表"""
    log_files = find_log_files(base_dir)
    if not log_files:
        return ["\n## 六、档位切换分析\n\n> 未找到日志文件，跳过档位分析。\n"]

    all_gear_changes = []
    all_wakeups = []

    for lf in log_files:
        subdir = os.path.basename(os.path.dirname(lf))
        short_sub = subdir.split(".")[0]
        current_gear = None

        with open(lf, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                # 档位切换事件
                m = re.search(r'aiplus_core_set_gear.*gear:\s*(\d+)', line)
                if m:
                    new_gear = int(m.group(1))
                    ts_match = re.search(r'(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2}):(\d{2})', line)
                    if ts_match:
                        ts = datetime.strptime(
                            f"{ts_match.group(1)}-{ts_match.group(2)}-{ts_match.group(3)} "
                            f"{ts_match.group(4)}:{ts_match.group(5)}:{ts_match.group(6)}",
                            "%Y-%m-%d %H:%M:%S")
                    else:
                        ts = None
                    if current_gear != new_gear:
                        all_gear_changes.append((ts, short_sub, current_gear, new_gear))
                    current_gear = new_gear

                # 唤醒时档位
                m3 = re.search(r'_on_wakeup_result.*"gear":(\d+)', line)
                if m3:
                    wkgear = int(m3.group(1))
                    ts_match = re.search(r'(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2}):(\d{2})', line)
                    ts = datetime.strptime(
                        f"{ts_match.group(1)}-{ts_match.group(2)}-{ts_match.group(3)} "
                        f"{ts_match.group(4)}:{ts_match.group(5)}:{ts_match.group(6)}",
                        "%Y-%m-%d %H:%M:%S") if ts_match else None
                    all_wakeups.append((ts, short_sub, wkgear))

    L = []
    L.append("\n## 六、档位切换分析\n")

    # 切换类型统计
    gear_transitions = Counter()
    for _, _, old, new in all_gear_changes:
        old_s = str(old) if old is not None else "init"
        gear_transitions[(old_s, new)] += 1

    L.append(f"共 {len(all_gear_changes)} 次档位切换。\n")
    L.append("| 切换 | 次数 |")
    L.append("|------|------|")
    for (old, new), cnt in sorted(gear_transitions.items(), key=lambda x: -x[1]):
        old_name = GEAR_NAMES.get(int(old), f"档位({old})") if old != "init" else "初始"
        new_name = GEAR_NAMES.get(new, f"档位({new})")
        L.append(f"| {old_name} → {new_name} | {cnt} |")
    L.append("")

    # 唤醒时档位统计
    L.append(f"### 唤醒时档位分布（共 {len(all_wakeups)} 次唤醒）\n")
    gear_counter = Counter()
    for _, _, g in all_wakeups:
        gear_counter[g] += 1

    L.append("| 档位 | 唤醒次数 | 占比 |")
    L.append("|------|----------|------|")
    for g in sorted(gear_counter.keys()):
        name = GEAR_NAMES.get(g, f"档位({g})")
        cnt = gear_counter[g]
        pct = cnt / len(all_wakeups) * 100 if all_wakeups else 0
        L.append(f"| {name} | {cnt} | {pct:.1f}% |")
    L.append("")

    # 按子目录统计
    subdir_gears = defaultdict(Counter)
    subdir_changes = Counter()
    for _, sub, old, new in all_gear_changes:
        subdir_changes[sub] += 1
    for _, sub, g in all_wakeups:
        subdir_gears[sub][g] += 1

    L.append("### 按子目录统计\n")
    L.append("| 子目录 | 档位切换 | 总唤醒 | 强劲档(4) | 标准档(3) | 单拖(1) | 待机(0) |")
    L.append("|--------|---------|--------|-----------|-----------|---------|---------|")
    for sub in sorted(subdir_gears.keys()):
        gears = subdir_gears[sub]
        total = sum(gears.values())
        L.append(
            f"| {sub} | {subdir_changes.get(sub, 0)} | {total} "
            f"| {gears.get(4, 0)} | {gears.get(3, 0)} | {gears.get(1, 0)} | {gears.get(0, 0)} |")
    L.append("")

    # 预期 vs 实际（从目录名或 xlsx 文件名解析预期档位）
    test_dir_name = os.path.basename(os.path.normpath(base_dir))
    expected = parse_expected_gear(test_dir_name)
    if expected is None:
        xlsx_name = os.path.basename(find_xlsx(base_dir))
        expected = parse_expected_gear(xlsx_name)
    exp_name = GEAR_NAMES.get(expected, f"未知({expected})") if expected is not None else "未知"
    L.append(f"### 预期档位({exp_name}) vs 实际\n")
    total_w = len(all_wakeups) if all_wakeups else 1
    if expected is not None:
        match = sum(1 for _, _, g in all_wakeups if g == expected)
        L.append(f"- 匹配{exp_name}: **{match}** / {total_w} ({match / total_w * 100:.1f}%)")
        mismatch = total_w - match
        L.append(f"- 非{exp_name}: **{mismatch}** / {total_w} ({mismatch / total_w * 100:.1f}%)")

        if mismatch > 0:
            non_exp_counter = Counter()
            for _, sub, g in all_wakeups:
                if g != expected:
                    non_exp_counter[(sub, g)] += 1
            L.append(f"\n| 子目录 | 实际档位 | 次数 |")
            L.append(f"|--------|----------|------|")
            for (sub, g), cnt in sorted(non_exp_counter.items()):
                L.append(f"| {sub} | {GEAR_NAMES.get(g, g)} | {cnt} |")
    else:
        L.append("- 无法从文件名解析预期档位，跳过匹配分析")
    L.append("")

    return L


# ============================================================
# Part 1: Excel 测试结果分析
# ============================================================

def analyze_xlsx(base_dir):
    """分析 Excel 测试结果，输出 analysis_report.md"""
    xlsx_path = find_xlsx(base_dir)
    df = pd.read_excel(xlsx_path)

    # 适配不同列名
    wake_col = '是否唤醒成功'
    success_col = '是否成功'
    result_src_col = '结果来源'
    asr_col = 'ASR 是否正确'
    intent_exp_col = '期望 intent'
    intent_act_col = '实际 intent'
    slots_exp_col = '期望 slots(参考)'
    slots_act_col = '实际 slots'

    total = len(df)
    wake_ok = df[df[wake_col] == '成功']
    wake_fail = df[df[wake_col] != '成功']
    cmd_ok = df[df[success_col] == '成功']
    cmd_fail = df[df[success_col] != '成功']
    has_asr = df[df[result_src_col] == '离线']

    wake_rate = len(wake_ok) / total * 100
    cmd_rate = len(cmd_ok) / total * 100

    # 提取测试基本信息
    test_name = os.path.basename(base_dir)
    n_commands = df['指令词'].nunique()
    n_speakers = df['SPEAKER'].nunique()

    lines = []
    lines.append(f"# {test_name} 测试分析报告\n")
    lines.append("## 一、基本概况\n")
    lines.append("| 项目 | 数值 |")
    lines.append("|------|------|")
    lines.append(f"| 总测试用例 | {total} |")
    lines.append(f"| 指令词数 | {n_commands} |")
    lines.append(f"| 说话人数 | {n_speakers} |")
    lines.append(f"| 结果来源 | 离线 {len(has_asr)} 条，无结果(Na) {total - len(has_asr)} 条 |")
    lines.append("")

    lines.append("## 二、核心指标\n")
    lines.append("| 指标 | 数值 |")
    lines.append("|------|------|")
    lines.append(f"| **唤醒成功率** | **{wake_rate:.1f}%**（{len(wake_ok)}/{total}） |")
    lines.append(f"| **指令成功率** | **{cmd_rate:.1f}%**（{len(cmd_ok)}/{total}） |")
    lines.append(f"| **指令失败数** | {len(cmd_fail)} |")
    lines.append("")

    # 失败原因分类
    lines.append("## 三、失败原因分类\n")
    lines.append("| 失败类型 | 数量 | 占比 | 说明 |")
    lines.append("|----------|------|------|------|")
    n_wake_fail = len(wake_fail)
    n_no_recog = len(df[(df[wake_col] == '成功') & (df[result_src_col] != '离线')])
    n_slot_err = len(df[(df[wake_col] == '成功') & (df[asr_col] == '错误') & (df[intent_exp_col] == df[intent_act_col])])
    n_intent_err = len(df[(df[wake_col] == '成功') & (df[asr_col] == '错误') & (df[intent_exp_col] != df[intent_act_col])])
    n_fail = max(len(cmd_fail), 1)
    lines.append(f"| 唤醒失败 | {n_wake_fail} | {n_wake_fail/n_fail*100:.1f}% | 设备未被唤醒 |")
    lines.append(f"| Slot 识别错误 | {n_slot_err} | {n_slot_err/n_fail*100:.1f}% | intent 正确但参数识别错 |")
    lines.append(f"| Intent 识别错误 | {n_intent_err} | {n_intent_err/n_fail*100:.1f}% | 识别成了完全不同的指令 |")
    lines.append(f"| 唤醒成功但无识别结果 | {n_no_recog} | {n_no_recog/n_fail*100:.1f}% | 唤醒了但未返回识别 |")
    lines.append("")

    # 按指令词成功率
    lines.append("## 四、按指令词成功率（从低到高）\n")
    cmd_stats = df.groupby('指令词').agg(
        总数=(success_col, 'count'),
        成功数=(success_col, lambda x: (x == '成功').sum())
    ).reset_index()
    cmd_stats['成功率'] = (cmd_stats['成功数'] / cmd_stats['总数'] * 100).round(1)
    cmd_stats = cmd_stats.sort_values('成功率')
    lines.append("| 指令词 | 成功数/总数 | 成功率 |")
    lines.append("|--------|------------|--------|")
    for _, row in cmd_stats.iterrows():
        lines.append(f"| {row['指令词']} | {row['成功数']}/{row['总数']} | {row['成功率']}% |")
    lines.append("")

    # 按说话人成功率
    lines.append("## 五、按说话人成功率（从低到高）\n")
    spk_stats = df.groupby('SPEAKER').agg(
        总数=(success_col, 'count'),
        成功数=(success_col, lambda x: (x == '成功').sum()),
        失败数=(success_col, lambda x: (x != '成功').sum())
    ).reset_index()
    spk_stats['成功率'] = (spk_stats['成功数'] / spk_stats['总数'] * 100).round(1)
    spk_stats = spk_stats.sort_values('成功率')
    lines.append("| 说话人 | 总数 | 成功数 | 失败数 | 成功率 |")
    lines.append("|--------|------|--------|--------|--------|")
    for _, row in spk_stats.iterrows():
        lines.append(f"| {row['SPEAKER']} | {row['总数']} | {row['成功数']} | {row['失败数']} | {row['成功率']}% |")
    lines.append("")

    # 档位分析
    lines.extend(analyze_gear(base_dir))

    dir_name = os.path.basename(os.path.normpath(base_dir))
    out_path = os.path.join(base_dir, f"{dir_name}_analysis_report.md")
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f"[分析报告] 已保存到: {out_path}")
    return out_path


# ============================================================
# Part 2: Log vs Excel 对比
# ============================================================

def extract_log_events_simple(log_files):
    """提取 event:102 和 event:114 列表"""
    log_102 = []  # (ts, rid, conf, doa, gear, status)
    log_114 = []  # (ts, rid, text)
    for lf in sorted(log_files):
        with open(lf, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                if 'Recv AI speech event:102' in line:
                    ts = parse_log_ts(line)
                    m_rid = re.search(r'recordId":"([^"]+)"', line)
                    m_conf = re.search(r'"conf":([\d.]+)', line)
                    m_doa = re.search(r'accurate_doa":(\d+)', line)
                    m_gear = re.search(r'"gear":(\d+)', line)
                    m_st = re.search(r'"status":(\d+)', line)
                    if ts and m_rid:
                        log_102.append((ts, m_rid.group(1),
                                        m_conf.group(1) if m_conf else '',
                                        m_doa.group(1) if m_doa else '',
                                        m_gear.group(1) if m_gear else '',
                                        m_st.group(1) if m_st else ''))
                elif 'Recv AI speech event:114' in line:
                    ts = parse_log_ts(line)
                    m_rid = re.search(r'recordId":"([^"]+)"', line)
                    m_txt = re.search(r'"text":"([^"]*)"', line)
                    if ts and m_rid:
                        log_114.append((ts, m_rid.group(1), m_txt.group(1) if m_txt else ''))
    return log_102, log_114


def compare_log_excel(base_dir):
    """对比 Log 与 Excel 差异，输出 log_vs_excel_comparison.md"""
    log_files = find_log_files(base_dir)
    xlsx_path = find_xlsx(base_dir)
    test_name = os.path.basename(base_dir)

    log_102, log_114 = extract_log_events_simple(log_files)
    df = pd.read_excel(xlsx_path)
    df['wake_dt'] = df['唤醒时间'].apply(parse_excel_dt)
    df['recog_dt'] = df['识别时间'].apply(parse_excel_dt)

    ew = df[df['是否唤醒成功'] == '成功']
    ea = df[df['结果来源'] == '离线']

    # Match 102
    ewl = [(r['wake_dt'], r['ID'], r['指令词'], r['是否成功'])
           for _, r in ew.iterrows() if r['wake_dt']]
    mi102 = set()
    um102 = []
    for i, item in enumerate(log_102):
        lts, conf, doa, gear = item[0], item[2], item[3], item[4]
        bd, bj = None, None
        for j, (ets, eid, ec, er) in enumerate(ewl):
            if j in mi102:
                continue
            d = abs((lts - ets).total_seconds())
            if d < 3 and (bd is None or d < bd):
                bd, bj = d, j
        if bj is not None:
            mi102.add(bj)
        else:
            um102.append((i, lts, conf, doa, gear))

    # Match 114
    eal = []
    for _, r in ea.iterrows():
        if r['recog_dt']:
            t = str(r['原始识别结果'])
            m = re.search(r'"context":"([^"]*)"', t)
            if m:
                t = m.group(1)
            eal.append((r['recog_dt'], r['ID'], t, str(r['指令词'])))

    mi114 = set()
    asr_diffs = []
    for i, (lts, ltxt) in enumerate([(t[0], t[2]) for t in log_114]):
        bd, bj = None, None
        for j, (ets, eid, easr, ec) in enumerate(eal):
            if j in mi114:
                continue
            d = abs((lts - ets).total_seconds())
            if d < 5 and (bd is None or d < bd):
                bd, bj = d, j
        if bj is not None:
            mi114.add(bj)
            _, eid, easr, ec = eal[bj]
            if normalize_asr(ltxt) != normalize_asr(easr):
                asr_diffs.append((eid, ec, easr, ltxt))

    # Format diff count
    case_only = hyphen_only = punct_only = 0
    for j in mi114:
        ets, eid, easr, ec = eal[j]
        for lts, ltxt in [(t[0], t[2]) for t in log_114]:
            if abs((lts - ets).total_seconds()) < 5:
                if ltxt != easr and normalize_asr(ltxt) == normalize_asr(easr):
                    if '-' in easr and '-' not in ltxt:
                        hyphen_only += 1
                    elif '.' in easr and '.' not in ltxt:
                        punct_only += 1
                    else:
                        case_only += 1
                break

    # Write report
    L = []
    L.append(f"# {test_name} Log vs Excel 对比报告\n")
    L.append("## 一、总体数据量对比\n")
    L.append("| 数据源 | 唤醒事件(102) | 识别事件(114) |")
    L.append("|--------|-------------|-------------|")
    L.append(f"| **Log 原始记录** | {len(log_102)} | {len(log_114)} |")
    L.append(f"| **Excel 测试结果** | {len(ew)}（唤醒成功） | {len(ea)}（有识别结果） |")
    diff_wake = len(log_102) - len(ew)
    diff_asr = len(log_114) - len(ea)
    L.append(f"| **差值** | Log 多 {diff_wake} 条 | {'完全一致' if diff_asr == 0 else f'差 {diff_asr} 条'} |")
    L.append("")

    L.append("## 二、唤醒事件差异\n")
    if um102:
        L.append(f"Log 中有 {len(um102)} 条唤醒事件无法匹配到 Excel 的唤醒成功记录。\n")
        L.append("| # | Log 102 时间 | conf | doa | gear | 后续114 | 最近 Excel 行 | Excel指令词 | 唤醒 | 结果 | 时间差 |")
        L.append("|---|-------------|------|-----|------|---------|-------------|-----------|------|------|--------|")
        for idx, (i, lts, conf, doa, gear) in enumerate(um102):
            h114, t114 = False, ''
            for (t1, t2) in [(t[0], t[2]) for t in log_114]:
                if 0 < (t1 - lts).total_seconds() < 30:
                    h114, t114 = True, t2
                    break
            br, bd = None, 999
            for _, r in df.iterrows():
                if r['wake_dt']:
                    d = abs((lts - r['wake_dt']).total_seconds())
                    if d < bd:
                        bd, br = d, r
            info = f"有({t114})" if h114 else "无"
            if br is not None:
                L.append(f"| {idx+1} | {lts.strftime('%H:%M:%S')} | {conf} | {doa}° | {gear} | {info} | ID={br['ID']} | {br['指令词']} | {br['是否唤醒成功']} | {br['是否成功']} | {bd:.1f}s |")
            else:
                L.append(f"| {idx+1} | {lts.strftime('%H:%M:%S')} | {conf} | {doa}° | {gear} | {info} | - | - | - | - | - |")
        L.append("")
    else:
        L.append("Log 和 Excel 唤醒事件数量完全一致，无额外唤醒。\n")

    L.append("## 三、ASR 识别结果对比\n")
    L.append("| 指标 | 数量 |")
    L.append("|------|------|")
    L.append(f"| Excel 有识别结果 | {len(ea)} |")
    L.append(f"| Log 114 事件 | {len(log_114)} |")
    match_ok = len(mi114) - len(asr_diffs)
    L.append(f"| 内容完全一致（归一化后） | **{match_ok}（{match_ok / max(len(mi114), 1) * 100:.1f}%）** |")
    L.append(f"| 真实 ASR 内容差异 | **{len(asr_diffs)}** |")
    L.append("")

    if asr_diffs:
        L.append("### ASR 差异详情\n")
        L.append("| ID | Excel指令词 | Excel ASR | Log ASR |")
        L.append("|----|-----------|----------|---------|")
        for eid, ec, ea2, la in asr_diffs:
            L.append(f"| {eid} | {ec} | {ea2} | {la} |")
        L.append("")

    L.append("### 差异分类\n")
    L.append("| 差异类型 | 数量 | 说明 |")
    L.append("|----------|------|------|")
    if case_only > 0:
        L.append(f"| 仅大小写不同 | {case_only} | Log 全大写，Excel 混合大小写 |")
    if hyphen_only > 0:
        L.append(f"| 大小写 + 连字符差异 | {hyphen_only} | Excel 带连字符，Log 为空格 |")
    if punct_only > 0:
        L.append(f"| 大小写 + 标点差异 | {punct_only} | Excel 带末尾句号，Log 不带 |")
    L.append("")

    L.append("## 四、总结\n")
    L.append(f"1. **唤醒事件**：Log {len(log_102)} 条，Excel {len(ew)} 条，差值 {diff_wake} 条")
    L.append(f"2. **ASR 识别**：归一化后 {len(asr_diffs)} 条真实差异")
    L.append(f"3. **额外唤醒**：{len(um102)} 条")
    L.append("")

    dir_name = os.path.basename(os.path.normpath(base_dir))
    out_path = os.path.join(base_dir, f"{dir_name}_log_vs_excel_comparison.md")
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))
    print(f"[对比报告] 已保存到: {out_path}")
    return out_path


# ============================================================
# Part 3: recordId/fileId/status 提取
# ============================================================

def extract_all_events(log_files):
    """提取全部 4 类事件"""
    pre_wakeup = []  # (ts, recordId)
    event102 = []    # (ts, rid, conf, doa, gear, status)
    event114 = []    # (ts, rid, text, eof)
    uploads = []     # (ts, file_id, status)

    for lf in sorted(log_files):
        with open(lf, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                if 'send pre wakeup event' in line:
                    ts = parse_log_ts(line)
                    m = re.search(r'recordId":"([^"]+)"', line)
                    if ts and m:
                        pre_wakeup.append((ts, m.group(1)))

                elif 'Recv AI speech event:102' in line:
                    ts = parse_log_ts(line)
                    m_rid = re.search(r'recordId":"([^"]+)"', line)
                    m_conf = re.search(r'"conf":([\d.]+)', line)
                    m_doa = re.search(r'accurate_doa":(\d+)', line)
                    m_gear = re.search(r'"gear":(\d+)', line)
                    m_st = re.search(r'"status":(\d+)', line)
                    if ts and m_rid:
                        event102.append((ts, m_rid.group(1),
                                         m_conf.group(1) if m_conf else '',
                                         m_doa.group(1) if m_doa else '',
                                         m_gear.group(1) if m_gear else '',
                                         m_st.group(1) if m_st else ''))

                elif 'Recv AI speech event:114' in line:
                    ts = parse_log_ts(line)
                    m_rid = re.search(r'recordId":"([^"]+)"', line)
                    m_txt = re.search(r'"text":"([^"]*)"', line)
                    m_eof = re.search(r'"eof":(\d+)', line)
                    if ts and m_rid:
                        event114.append((ts, m_rid.group(1),
                                         m_txt.group(1) if m_txt else '',
                                         m_eof.group(1) if m_eof else ''))

                elif 'AI-localUpload onCallback errno: 0, frame result:' in line:
                    ts = parse_log_ts(line)
                    m_fid = re.search(r'file_id":"([^"]+)"', line)
                    m_st = re.search(r'"status":"([^"]*)"', line)
                    if ts and m_fid:
                        uploads.append((ts, m_fid.group(1), m_st.group(1) if m_st else ''))

    return pre_wakeup, event102, event114, uploads


def group_by_prewakeup(pre_wakeup, event102, event114, uploads):
    """按 pre-wakeup 分组关联 4 类事件"""
    results = []
    u_idx = e102_idx = e114_idx = 0

    for i, (pw_ts, pw_rid) in enumerate(pre_wakeup):
        end_ts = pre_wakeup[i + 1][0] if i + 1 < len(pre_wakeup) else pw_ts + timedelta(seconds=120)

        # event:102
        m102 = None
        while e102_idx < len(event102) and event102[e102_idx][0] < pw_ts:
            e102_idx += 1
        if e102_idx < len(event102) and 0 <= (event102[e102_idx][0] - pw_ts).total_seconds() < 30:
            m102 = event102[e102_idx]
            e102_idx += 1

        # event:114
        m114 = None
        j = e114_idx
        while j < len(event114):
            d = (event114[j][0] - pw_ts).total_seconds()
            if d < 0:
                j += 1
                continue
            if d > 60:
                break
            if m102 and event114[j][0] < m102[0]:
                j += 1
                continue
            m114 = event114[j]
            e114_idx = j + 1
            break

        # uploads
        mu = []
        k = u_idx
        while k < len(uploads) and uploads[k][0] < pw_ts:
            k += 1
        u_idx = k
        while k < len(uploads) and uploads[k][0] <= end_ts:
            if 0 <= (uploads[k][0] - pw_ts).total_seconds() < 120:
                mu.append(uploads[k])
            k += 1

        results.append({
            'seq': i + 1, 'pw_ts': pw_ts, 'pw_rid': pw_rid,
            'e102': m102, 'e114': m114, 'uploads': mu,
        })

    return results


def extract_recordIds(base_dir):
    """提取 recordId/fileId/status 并输出到文件"""
    log_files = find_log_files(base_dir)
    test_name = os.path.basename(base_dir)

    pw, e102, e114, ups = extract_all_events(log_files)
    results = group_by_prewakeup(pw, e102, e114, ups)

    dir_name = os.path.basename(os.path.normpath(base_dir))
    out_path = os.path.join(base_dir, f"{dir_name}_recordId_fileId_status.txt")
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(f"{test_name} 关键字段对应关系：send pre wakeup event → event:102 → event:114 → AI-localUpload\n")
        f.write(f"共 {len(pw)} 条 pre-wakeup，{len(e102)} 条 event:102，{len(e114)} 条 event:114，{len(ups)} 条 upload\n")
        f.write("=" * 130 + "\n\n")

        for r in results:
            f.write(f"#{r['seq']:03d}  时间: {r['pw_ts'].strftime('%Y/%m/%d %H:%M:%S')}\n")
            f.write(f"  [send pre wakeup event]\n    recordId: {r['pw_rid']}\n")
            if r['e102']:
                f.write(f"  [Recv AI speech event:102]\n")
                f.write(f"    recordId: {r['e102'][1]}\n")
                f.write(f"    conf: {r['e102'][2]}  doa: {r['e102'][3]}°  gear: {r['e102'][4]}  status: {r['e102'][5]}\n")
            if r['e114']:
                f.write(f"  [Recv AI speech event:114]\n")
                f.write(f"    recordId: {r['e114'][1]}\n")
                f.write(f"    text: {r['e114'][2]}  eof: {r['e114'][3]}\n")
            if r['uploads']:
                f.write(f"  [AI-localUpload]\n")
                for u in r['uploads']:
                    f.write(f"    file_id: {u[1]}  status: {u[2]}\n")
            f.write("\n")

        f.write("=" * 130 + "\n统计\n" + "=" * 130 + "\n")
        full = sum(1 for r in results if r['e102'] and r['e114'])
        wake_only = sum(1 for r in results if r['e102'] and not r['e114'])
        pre_only = sum(1 for r in results if not r['e102'] and not r['e114'])
        f.write(f"pre-wakeup (event:103): {len(results)}\n")
        f.write(f"  完整链 (pre→102→114): {full}\n")
        f.write(f"  唤醒但无识别 (pre→102): {wake_only}\n")
        f.write(f"  仅预唤醒 (pre only):   {pre_only}\n")
        f.write(f"event:102 总数: {len(e102)}\n")
        f.write(f"event:114 总数: {len(e114)}\n")
        f.write(f"upload 总数: {len(ups)}\n")

    print(f"[recordId] 已保存到: {out_path}")
    return out_path


# ============================================================
# Part 4: 空识别结果捞取（conf:0, rec:"", eof:1）
# ============================================================

def extract_empty_asr(base_dir):
    """捞取 log 中识别结果为空的记录（conf:0, rec:"", eof:1）"""
    log_files = find_log_files(base_dir)
    test_name = os.path.basename(base_dir)
    dir_name = os.path.basename(os.path.normpath(base_dir))

    results = []  # (ts, recordId, subdir)
    for lf in sorted(log_files):
        subdir = os.path.basename(os.path.dirname(lf))
        with open(lf, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                if '"conf":0,"rec":"","eof":1' in line:
                    ts = parse_log_ts(line)
                    rid = re.search(r'recordId":"([^"]+)"', line)
                    if ts and rid:
                        results.append((ts, rid.group(1), subdir))

    L = []
    L.append(f"# {test_name} 空识别结果捞取报告\n")
    L.append(f"共发现 **{len(results)}** 条空识别结果（conf:0, rec:\"\", eof:1）\n")

    if not results:
        L.append("未发现空识别结果。\n")
    else:
        # 按子目录统计
        sub_counter = Counter()
        for _, _, sub in results:
            sub_counter[sub] += 1

        L.append("### 按子目录统计\n")
        L.append("| 子目录 | 空识别数 |")
        L.append("|--------|---------|")
        for sub in sorted(sub_counter.keys()):
            L.append(f"| {sub} | {sub_counter[sub]} |")
        L.append("")

        # 明细
        L.append("### 空识别明细\n")
        L.append("| # | 时间 | recordId | 子目录 |")
        L.append("|---|------|----------|--------|")
        for idx, (ts, rid, sub) in enumerate(results):
            L.append(f"| {idx+1} | {ts.strftime('%Y/%m/%d %H:%M:%S')} | {rid} | {sub} |")
        L.append("")

    out_path = os.path.join(base_dir, f"{dir_name}_empty_asr_report.md")
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))
    print(f"[空识别捞取] 已保存到: {out_path}")
    return out_path


# ============================================================
# Part 5: ASR 不一致分析（识别结果 vs 预期，关联 log recordId）
# ============================================================

def compare_asr_mismatch(base_dir):
    """识别结果与预期不一致分析，关联 log event:114 recordId"""
    log_files = find_log_files(base_dir)
    xlsx_path = find_xlsx(base_dir)
    test_name = os.path.basename(base_dir)
    dir_name = os.path.basename(os.path.normpath(base_dir))

    log_102, log_114 = extract_log_events_simple(log_files)

    df = pd.read_excel(xlsx_path)
    df['recog_dt'] = df['识别时间'].apply(parse_excel_dt)
    df['wake_dt'] = df['唤醒时间'].apply(parse_excel_dt)

    failed = df[df['是否成功'] != '成功'].copy()
    wake_fail = failed[failed['是否唤醒成功'] != '成功']
    asr_fail = failed[
        (failed['是否唤醒成功'] == '成功') & (failed['ASR 是否正确'] == '错误')
    ]

    L = []
    L.append(f"# {test_name} 识别不一致分析报告\n")
    L.append("## 概要\n")
    L.append("| 分类 | 数量 |")
    L.append("|------|------|")
    L.append(f"| 总测试 | {len(df)} |")
    L.append(f"| 总失败 | {len(failed)} |")
    L.append(f"| 唤醒失败 | {len(wake_fail)} |")
    L.append(f"| ASR 识别错误 | {len(asr_fail)} |")
    L.append("")

    if asr_fail.empty:
        L.append("所有识别结果与预期一致，无不一致条目。\n")
    else:
        # Intent 错误 vs Slot 错误
        intent_err = asr_fail[asr_fail['期望 intent'] != asr_fail['实际 intent']]
        slot_err = asr_fail[asr_fail['期望 intent'] == asr_fail['实际 intent']]

        L.append(f"### 错误分类\n")
        L.append("| 类型 | 数量 | 说明 |")
        L.append("|------|------|------|")
        L.append(f"| Intent 错误 | {len(intent_err)} | 识别成了不同的指令 |")
        L.append(f"| Slot 错误 | {len(slot_err)} | intent 正确但参数识别错 |")
        L.append("")

        # 匹配 log event:114
        matched_log = set()

        def find_log_114(recog_dt):
            if pd.isna(recog_dt):
                return '-', '-'
            best_j = None
            best_d = 999
            for j, (lts, lrid, ltxt) in enumerate(log_114):
                if j in matched_log:
                    continue
                d = abs((lts - recog_dt).total_seconds())
                if d < 5 and d < best_d:
                    best_d = d
                    best_j = j
            if best_j is not None:
                matched_log.add(best_j)
                return log_114[best_j][1], log_114[best_j][2]
            return '-', '-'

        # Intent 错误明细
        if not intent_err.empty:
            L.append("## Intent 错误明细\n")
            L.append("| # | ID | 指令词(预期) | 实际识别 | 期望intent | 实际intent | Log recordId | Log ASR文本 | 识别时间 |")
            L.append("|---|----|-------------|---------|-----------|-----------|-------------|------------|---------|")
            for idx, (_, row) in enumerate(intent_err.iterrows()):
                log_rid, log_text = find_log_114(row['recog_dt'])
                act_cmd = str(row['实际识别的指令词']) if pd.notna(row['实际识别的指令词']) else '-'
                exp_int = str(row['期望 intent']) if pd.notna(row['期望 intent']) else '-'
                act_int = str(row['实际 intent']) if pd.notna(row['实际 intent']) else '-'
                time_str = row['recog_dt'].strftime('%H:%M:%S') if pd.notna(row['recog_dt']) else '-'
                L.append(
                    f"| {idx+1} | {row['ID']} | {row['指令词']} | {act_cmd} "
                    f"| {exp_int} | {act_int} | {log_rid} | {log_text} | {time_str} |")
            L.append("")

        # Slot 错误明细
        if not slot_err.empty:
            L.append("## Slot 错误明细\n")
            L.append("| # | ID | 指令词(预期) | 实际识别 | 期望intent | 期望slots | 实际slots | Log recordId | Log ASR文本 | 识别时间 |")
            L.append("|---|----|-------------|---------|-----------|---------|---------|-------------|------------|---------|")
            for idx, (_, row) in enumerate(slot_err.iterrows()):
                log_rid, log_text = find_log_114(row['recog_dt'])
                act_cmd = str(row['实际识别的指令词']) if pd.notna(row['实际识别的指令词']) else '-'
                exp_int = str(row['期望 intent']) if pd.notna(row['期望 intent']) else '-'
                exp_slots = str(row['期望 slots(参考)']) if pd.notna(row.get('期望 slots(参考)', None)) else '-'
                act_slots = str(row['实际 slots']) if pd.notna(row.get('实际 slots', None)) else '-'
                time_str = row['recog_dt'].strftime('%H:%M:%S') if pd.notna(row['recog_dt']) else '-'
                L.append(
                    f"| {idx+1} | {row['ID']} | {row['指令词']} | {act_cmd} "
                    f"| {exp_int} | {exp_slots} | {act_slots} | {log_rid} | {log_text} | {time_str} |")
            L.append("")

    out_path = os.path.join(base_dir, f"{dir_name}_asr_mismatch_report.md")
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))
    print(f"[ASR不一致报告] 已保存到: {out_path}")
    return out_path


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='石头扫地机思必驰语音识别测试结果分析')
    parser.add_argument('test_dir', help='测试结果目录路径')
    parser.add_argument('--mode', choices=['all', 'report', 'compare', 'extract', 'mismatch', 'empty'],
                        default='all', help='分析模式 (默认: all)')

    args = parser.parse_args()
    base_dir = os.path.abspath(args.test_dir)

    if not os.path.isdir(base_dir):
        print(f"错误: 目录不存在 - {base_dir}")
        sys.exit(1)

    # 自动解压 zip
    unzip_if_needed(base_dir)

    if args.mode in ('all', 'report'):
        print("=" * 60)
        print("Part 1: Excel 测试结果分析")
        print("=" * 60)
        analyze_xlsx(base_dir)
        print()

    if args.mode in ('all', 'compare'):
        print("=" * 60)
        print("Part 2: Log vs Excel 对比")
        print("=" * 60)
        compare_log_excel(base_dir)
        print()

    if args.mode in ('all', 'extract'):
        print("=" * 60)
        print("Part 3: recordId/fileId/status 提取")
        print("=" * 60)
        extract_recordIds(base_dir)
        print()

    if args.mode in ('all', 'mismatch'):
        print("=" * 60)
        print("Part 4: ASR 识别不一致分析")
        print("=" * 60)
        compare_asr_mismatch(base_dir)
        print()

    if args.mode in ('all', 'empty'):
        print("=" * 60)
        print("Part 5: 空识别结果捞取")
        print("=" * 60)
        extract_empty_asr(base_dir)
        print()

    print("全部完成!")


if __name__ == '__main__':
    main()
