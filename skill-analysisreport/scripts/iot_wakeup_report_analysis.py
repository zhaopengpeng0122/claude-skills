#!/usr/bin/env python3
"""
IOT 唤醒测试报告分析脚本
从 mlops.aispeech.com.cn 的 HTML 测试报告中提取唤醒召回率、串扰率、误唤醒数据，
生成完整的 Markdown 分析报告。

用法:
    python3 iot_wakeup_report_analysis.py <html文件路径> [输出目录]

输出:
    <输出目录>/唤醒测试报告分析_<日期>.md
"""

import sys
import os
import re
import json
from html.parser import HTMLParser
from collections import defaultdict
from urllib.parse import unquote


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_cell = False
        self.in_row = False
        self.in_table = False
        self.skip_next = False
        self.tables = []
        self.current_table = []
        self.current_row = []
        self.current_cell = ''

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self.in_table = True
            self.current_table = []
        elif tag == 'tr' and self.in_table:
            self.in_row = True
            self.current_row = []
        elif tag in ('td', 'th') and self.in_row:
            self.in_cell = True
            self.current_cell = ''
        elif tag == 'button':
            self.skip_next = True

    def handle_endtag(self, tag):
        if tag == 'table':
            self.in_table = False
            if self.current_table:
                self.tables.append(self.current_table)
        elif tag == 'tr':
            self.in_row = False
            if self.current_row:
                self.current_table.append(self.current_row)
        elif tag in ('td', 'th'):
            self.in_cell = False
            if not self.skip_next:
                self.current_row.append(self.current_cell.strip())
            self.skip_next = False
        elif tag == 'button':
            self.skip_next = False

    def handle_data(self, data):
        if self.in_cell and not self.skip_next:
            self.current_cell += data + ' '


def parse_tables(html):
    parser = TableParser()
    parser.feed(html)
    return parser.tables


def extract_title(html):
    m = re.search(r'<title>(.*?)</title>', html)
    return m.group(1).strip() if m else '未知'


def extract_summary_table(tables):
    """提取汇总表 (Table 1)"""
    if not tables:
        return []
    rows = []
    for row in tables[0]:
        cleaned = [' '.join(c.split()) for c in row]
        rows.append(cleaned)
    return rows


def extract_scene_results(tables):
    """提取明细表中场景级汇总行"""
    results = []
    for row in tables[1]:
        if len(row) == 14 and row[6] == '场景平均':
            try:
                results.append({
                    'obj': row[1],
                    'scene': row[2],
                    'total': int(row[7]),
                    'recall': int(row[8]),
                    'standard': float(row[9].replace('%', '')),
                    'rate': float(row[10].replace('%', '')),
                    'crosstalk_count': int(row[11]),
                    'crosstalk_rate': float(row[12].replace('%', '')),
                })
                results[-1]['passed'] = (
                    results[-1]['rate'] >= results[-1]['standard']
                    and results[-1]['crosstalk_rate'] < 5.0
                )
            except (ValueError, IndexError):
                pass
    return results


def extract_far_data(html):
    """从 JavaScript 中提取误唤醒数据 (farDictData)"""
    match = re.search(r'var farDictData\s*=\s*\{', html)
    if not match:
        return {}

    start = match.end() - 1
    depth = 0
    i = start
    while i < len(html):
        if html[i] == '{':
            depth += 1
        elif html[i] == '}':
            depth -= 1
            if depth == 0:
                break
        i += 1

    far_data_str = html[start:i + 1]
    results = {}

    model_sections = re.split(r"(?='[^']+':\s*\{)", far_data_str)
    for section in model_sections:
        name_match = re.match(r"'([^']+)'", section)
        if not name_match:
            continue
        model_name = name_match.group(1)

        value_match = re.search(r"'value':\s*(\d+)", section)
        far_count = int(value_match.group(1)) if value_match else 0

        files_match = re.search(r"'farFileText':\s*\[(.*?)\]", section, re.DOTALL)
        files = re.findall(r"'([^']{10,})'", files_match.group(1)) if files_match else []

        details = []
        for f in files:
            info = {}
            fn_match = re.match(r'record_(\w+)_(\w+)_(\w+)_(\w+)_(\w+)_(.+?)_chunk_(\d+)', f.split('.log')[0])
            if fn_match:
                info['lang'] = fn_match.group(1)
                info['noise_type'] = fn_match.group(2)
                info['distance'] = fn_match.group(3)
                info['mode'] = fn_match.group(4)
                info['chunk'] = fn_match.group(7)

            json_match = re.search(r'info:\{([^}]+)\}', f)
            if json_match:
                for kv in re.findall(r'"(\w+)":([\d.]+)', json_match.group(0)):
                    if kv[0] == 'thresh':
                        info['threshold'] = float(kv[1])
                    elif kv[0] == 'confidence':
                        info['confidence'] = float(kv[1])
                    elif kv[0] == 'wkpIndex':
                        info['wkpIndex'] = int(kv[1])
                    elif kv[0] == 'cc_conf':
                        info['cc_conf'] = float(kv[1])

            details.append(info)

        results[model_name] = {
            'far_count': far_count,
            'details': details
        }

    return results


def get_model_order(results):
    """从结果中提取模型列表（保持出现顺序）"""
    seen = []
    for r in results:
        if r['obj'] not in seen:
            seen.append(r['obj'])
    return seen


def generate_report(title, summary_rows, scene_results, far_data, output_path):
    """生成完整的 Markdown 分析报告"""
    models = get_model_order(scene_results)

    # 提取报告元信息
    info = parse_title_info(title)
    report_date = info.get('date', '')
    product = info.get('product', '')
    wake_word = info.get('wake_word', '')
    test_type = info.get('test_type', '')

    lines = []
    w = lines.append

    w(f'# {product} 唤醒测试报告分析')
    w('')
    w(f'> 报告来源: {title}')
    w(f'> 分析日期: {report_date}')
    w('')
    w('---')
    w('')

    # === 一、测试概况 ===
    w('## 一、测试概况')
    w('')
    total_tests = sum(r['total'] for r in scene_results)
    total_recall = sum(r['recall'] for r in scene_results)
    overall_rate = total_recall / total_tests * 100 if total_tests > 0 else 0
    total_far = sum(v['far_count'] for v in far_data.values())

    w('| 项目 | 内容 |')
    w('|------|------|')
    w(f'| 产品 | {product} |')
    w(f'| 唤醒词 | {wake_word} |')
    w(f'| 测试类型 | {test_type} |')
    w(f'| 模型版本 | {len(models)} 个 |')
    w(f'| 总测试量 | {total_tests:,} 次 |')
    w(f'| 总召回 | {total_recall:,} 次 |')
    w(f'| 整体召回率 | {overall_rate:.2f}% |')
    w(f'| 误唤醒总计 | {total_far} 次 |')
    w('')
    w('---')
    w('')

    # === 二、各模型版本整体表现 ===
    w('## 二、各模型版本整体表现')
    w('')
    w('| 模型 | 场景数 | 总测试 | 总召回 | 整体召回率 | 达标/总数 | 未达标数 | 最低场景召回率 |')
    w('|------|--------|--------|--------|-----------|-----------|---------|---------------|')

    for obj in models:
        obj_rows = [r for r in scene_results if r['obj'] == obj]
        if not obj_rows:
            continue
        total = sum(r['total'] for r in obj_rows)
        recall = sum(r['recall'] for r in obj_rows)
        rate = recall / total * 100 if total > 0 else 0
        failed = [r for r in obj_rows if not r['passed']]
        min_r = min(obj_rows, key=lambda x: x['rate'])
        w(f'| {obj} | {len(obj_rows)} | {total:,} | {recall:,} | {rate:.2f}% | {len(obj_rows) - len(failed)}/{len(obj_rows)} | {len(failed)} | {min_r["rate"]:.2f}% ({min_r["scene"]}) |')

    w(f'| **总计** | **{len(scene_results)}** | **{total_tests:,}** | **{total_recall:,}** | **{overall_rate:.2f}%** | **{len(scene_results) - sum(1 for r in scene_results if not r["passed"])}/{len(scene_results)}** | **{sum(1 for r in scene_results if not r["passed"])}** | |')
    w('')
    w('---')
    w('')

    # === 三、模型表现排名 ===
    w('## 三、模型表现排名（按达标率排序）')
    w('')

    model_stats = []
    for obj in models:
        obj_rows = [r for r in scene_results if r['obj'] == obj]
        if not obj_rows:
            continue
        total = sum(r['total'] for r in obj_rows)
        recall = sum(r['recall'] for r in obj_rows)
        rate = recall / total * 100 if total > 0 else 0
        failed_count = len([r for r in obj_rows if not r['passed']])
        pass_rate = (len(obj_rows) - failed_count) / len(obj_rows) * 100
        far = far_data.get(obj, {}).get('far_count', 0)
        model_stats.append({
            'name': obj, 'rate': rate, 'pass_rate': pass_rate,
            'failed': failed_count, 'total': len(obj_rows), 'far': far
        })

    model_stats.sort(key=lambda x: -x['pass_rate'])

    w('| 排名 | 模型 | 整体召回率 | 达标率 | 误唤醒 | 评价 |')
    w('|------|------|-----------|--------|--------|------|')

    for i, s in enumerate(model_stats):
        if s['pass_rate'] == 100:
            grade = '最优'
        elif s['pass_rate'] >= 95:
            grade = '优秀'
        elif s['pass_rate'] >= 90:
            grade = '良好'
        elif s['pass_rate'] >= 80:
            grade = '一般'
        elif s['pass_rate'] >= 70:
            grade = '较差'
        else:
            grade = '**最差**'
        w(f'| {i + 1} | {s["name"]} | {s["rate"]:.2f}% | {s["pass_rate"]:.2f}% | {s["far"]} | {grade} |')

    w('')
    w('---')
    w('')

    # === 四、未达标场景详情 ===
    failed_all = [r for r in scene_results if not r['passed']]

    w(f'## 四、未达标场景详情（共 {len(failed_all)} 个）')
    w('')

    for obj in models:
        failed = [r for r in failed_all if r['obj'] == obj]
        if not failed:
            continue
        failed.sort(key=lambda x: x['rate'])
        tag = ' — 最差模型' if len(failed) == max(len([f for f in failed_all if f['obj'] == m]) for m in models) else ''
        w(f'### {obj}（{len(failed)} 个未达标）{tag}')
        w('')
        w('| 场景 | 召回率 | 验收标准 | 差距 |')
        w('|------|--------|---------|------|')
        for r in failed:
            diff = r['rate'] - r['standard']
            w(f'| {r["scene"]} | {r["rate"]:.2f}% | {r["standard"]:.0f}% | {diff:+.2f}% |')
        w('')

    w('---')
    w('')

    # === 五、召回率最低的场景 ===
    w('## 五、召回率最低的 10 个场景（全模型）')
    w('')
    worst = sorted(scene_results, key=lambda x: x['rate'])[:10]
    w('| 排名 | 模型 | 场景 | 召回率 | 验收标准 | 差距 |')
    w('|------|------|------|--------|---------|------|')
    for i, r in enumerate(worst):
        diff = r['rate'] - r['standard']
        w(f'| {i + 1} | {r["obj"]} | {r["scene"]} | {r["rate"]:.2f}% | {r["standard"]:.0f}% | {diff:+.2f}% |')
    w('')
    w('---')
    w('')

    # === 六、共性弱点场景 ===
    w('## 六、共性弱点场景分析')
    w('')
    w('以下场景在多个语言版本中同时未达标，属于共性弱点：')
    w('')

    scene_fail_count = defaultdict(list)
    for r in failed_all:
        scene_fail_count[r['scene']].append(r['obj'])

    common_weak = sorted(scene_fail_count.items(), key=lambda x: -len(x[1]))
    w('| 场景 | 未达标模型数 | 涉及模型 |')
    w('|------|-------------|---------|')
    for scene, objs in common_weak:
        w(f'| {scene} | {len(objs)} | {", ".join(objs)} |')

    w('')
    w('---')
    w('')

    # === 七、误唤醒分析 ===
    w('## 七、误唤醒（噪声误唤醒）分析')
    w('')

    far_models = {k: v for k, v in far_data.items() if v['far_count'] > 0}
    zero_far_models = {k: v for k, v in far_data.items() if v['far_count'] == 0}

    w('### 7.1 误唤醒汇总')
    w('')
    w(f'共 {len(far_models)} 个模型版本存在误唤醒，总计 **{total_far} 次**。其余 {len(zero_far_models)} 个模型版本误唤醒为 0。')
    w('')
    w('| 模型 | 误唤醒次数 | 状态 |')
    w('|------|-----------|------|')

    all_far_items = sorted(far_data.items(), key=lambda x: -x[1]['far_count'])
    for name, data in all_far_items:
        count = data['far_count']
        if count >= 5:
            status = '**严重**'
        elif count > 0:
            status = '需关注' if count > 1 else '轻微'
        else:
            status = '正常'
        w(f'| {name} | {count} | {status} |')

    w('')

    # 详细记录
    if far_models:
        w('### 7.2 误唤醒详细记录')
        w('')
        for name, data in sorted(far_models.items()):
            w(f'#### {name}（{data["far_count"]} 次误唤醒）')
            w('')
            w('| # | 噪声源 | 距离 | chunk | 置信度 | 阈值 | 裕量 | cc_conf |')
            w('|---|--------|------|-------|--------|------|------|---------|')
            for idx, d in enumerate(data['details']):
                noise = d.get('noise_type', '?')
                dist = d.get('distance', '?')
                chunk = d.get('chunk', '?')
                conf = d.get('confidence', 0)
                thresh = d.get('threshold', 0)
                margin = conf - thresh
                cc = d.get('cc_conf', 0)
                w(f'| {idx + 1} | {noise} | {dist} | {chunk} | {conf:.4f} | {thresh:.4f} | {margin:+.4f} | {cc:.4f} |')
            w('')

        # 特征分析
        w('### 7.3 误唤醒特征分析')
        w('')

        # 噪声源分布
        noise_dist = defaultdict(int)
        conf_ranges = defaultdict(int)
        for name, data in far_models.items():
            for d in data['details']:
                noise_dist[d.get('noise_type', '未知')] += 1
                conf = d.get('confidence', 0)
                if conf < 0.80:
                    conf_ranges['< 0.80'] += 1
                elif conf < 0.85:
                    conf_ranges['0.80 ~ 0.85'] += 1
                elif conf < 0.90:
                    conf_ranges['0.85 ~ 0.90'] += 1
                else:
                    conf_ranges['0.90+'] += 1

        w('**噪声源分布：**')
        w('')
        w('| 噪声源 | 误唤醒次数 | 占比 |')
        w('|--------|-----------|------|')
        for noise, count in sorted(noise_dist.items(), key=lambda x: -x[1]):
            pct = count / total_far * 100 if total_far > 0 else 0
            w(f'| {noise} | {count} | {pct:.1f}% |')

        w('')
        w('**置信度分布：**')
        w('')
        w('| 置信度区间 | 次数 | 特征 |')
        w('|-----------|------|------|')
        for rng in ['< 0.80', '0.80 ~ 0.85', '0.85 ~ 0.90', '0.90+']:
            count = conf_ranges.get(rng, 0)
            if rng == '< 0.80':
                feat = '低裕量，阈值微调可能消除'
            elif rng == '0.80 ~ 0.85':
                feat = '中等裕量'
            elif rng == '0.85 ~ 0.90':
                feat = '较高置信度'
            else:
                feat = '高置信度，误唤醒信号较强'
            if count > 0:
                w(f'| {rng} | {count} | {feat} |')

        w('')

    w('---')
    w('')

    # === 八、总体结论 ===
    w('## 八、总体结论与建议')
    w('')
    failed_count = len(failed_all)
    passed_count = len(scene_results) - failed_count

    w('### 达标情况')
    w(f'- 共 **{len(scene_results)}** 个场景组合测试')
    w(f'- 达标: **{passed_count}** 个 ({passed_count / len(scene_results) * 100:.1f}%)')
    w(f'- 未达标: **{failed_count}** 个 ({failed_count / len(scene_results) * 100:.1f}%)')
    w(f'- 误唤醒总计 **{total_far}** 次')
    w('')

    # 召回率最差模型
    worst_models = sorted(model_stats, key=lambda x: x['failed'], reverse=True)[:3]
    w('### 召回率 — 重点问题模型（建议优先优化）')
    for i, s in enumerate(worst_models):
        if s['failed'] > 0:
            w(f'{i + 1}. **{s["name"]}**: {s["failed"]}/{s["total"]} 场景未达标 ({s["failed"] / s["total"] * 100:.1f}%)，整体召回率 {s["rate"]:.2f}%')
    w('')

    # 误唤醒最严重模型
    worst_far = sorted(far_models.items(), key=lambda x: -x[1]['far_count'])[:3]
    if worst_far:
        w('### 误唤醒 — 重点问题模型')
        for name, data in worst_far:
            w(f'- **{name}**: {data["far_count"]} 次误唤醒')
        w('')

    # 共性弱点场景建议
    w('### 场景维度建议')
    if common_weak:
        top_weak = common_weak[:3]
        for scene, objs in top_weak:
            w(f'- `{scene}` 在 {len(objs)} 个模型中不达标')
    w('- 建议重点优化共性弱点场景的唤醒模型参数')
    if far_models:
        w('- 对误唤醒较多的模型，建议适当提升唤醒阈值以平衡召回率与误唤醒的矛盾')

    # 写入文件
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    return output_path


def parse_title_info(title):
    """从标题中提取报告元信息"""
    info = {
        'product': '',
        'wake_word': '',
        'test_type': '',
        'date': ''
    }

    # 提取日期
    date_match = re.search(r'(\d{8})', title)
    if date_match:
        d = date_match.group(1)
        info['date'] = f'{d[:4]}-{d[4:6]}-{d[6:8]}'

    # 提取产品名
    if '石头扫地机' in title:
        info['product'] = '石头扫地机'
    elif '海信' in title:
        info['Product'] = '海信'

    # 提取设备型号
    model_match = re.search(r'(RR-\w+)', title)
    if model_match:
        info['product'] += f' {model_match.group(1)}'

    # 唤醒词
    if '唤醒' in title:
        info['wake_word'] = 'Hello-Rocky'

    # 测试类型
    if '摸底测试' in title:
        info['test_type'] = '摸底测试'
    elif '验收测试' in title:
        info['test_type'] = '验收测试'

    # 市场
    if '海外' in title:
        info['product'] += ' (海外版)'

    return info


def main():
    if len(sys.argv) < 2:
        print('用法: python3 iot_wakeup_report_analysis.py <html文件路径> [输出目录]')
        sys.exit(1)

    html_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(html_path)

    if not os.path.exists(html_path):
        print(f'错误: 文件不存在 {html_path}')
        sys.exit(1)

    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()

    title = extract_title(html)
    tables = parse_tables(html)
    scene_results = extract_scene_results(tables)
    far_data = extract_far_data(html)

    # 生成输出文件名
    date_match = re.search(r'(\d{8})', title)
    date_str = date_match.group(1) if date_match else 'unknown'
    info = parse_title_info(title)
    product_slug = info.get('product', 'IOT').replace(' ', '_')
    output_name = f'{product_slug}唤醒测试报告分析_{date_str}.md'
    output_path = os.path.join(output_dir, output_name)

    generate_report(title, [], scene_results, far_data, output_path)
    print(f'报告已生成: {output_path}')


if __name__ == '__main__':
    main()
