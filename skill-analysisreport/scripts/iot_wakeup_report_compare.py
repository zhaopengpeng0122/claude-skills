#!/usr/bin/env python3
"""
IOT 唤醒测试报告对比分析脚本
对比两份 mlops.aispeech.com.cn HTML 测试报告，生成跨版本对比 Markdown 报告。

用法:
    python3 iot_wakeup_report_compare.py <html_A> <html_B> [输出目录]

输出:
    <输出目录>/<产品名>唤醒测试报告对比分析.md
"""

import sys
import os
import re
from collections import defaultdict

# 复用现有脚本的解析函数
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from iot_wakeup_report_analysis import (
    extract_title, parse_tables, extract_scene_results,
    extract_far_data, parse_title_info, get_model_order,
)


def pct_change(a, b):
    """计算百分比变化，返回 (差值, 格式化字符串)"""
    diff = b - a
    sign = '+' if diff >= 0 else ''
    return diff, f'{sign}{diff:.2f}%'


def fmt_change(diff, invert=False):
    """格式化变化值，invert=True 时负数为改善"""
    val = -diff if invert else diff
    sign = '+' if diff >= 0 else ''
    if val > 0:
        return f'**{sign}{diff:.2f}%**'
    return f'{sign}{diff:.2f}%'


def generate_compare_report(title_a, title_b, results_a, results_b, far_a, far_b, output_path):
    """生成对比分析 Markdown 报告"""
    info_a = parse_title_info(title_a)
    info_b = parse_title_info(title_b)
    date_a = info_a.get('date', '')
    date_b = info_b.get('date', '')
    product = info_a.get('product', '') or info_b.get('product', '')
    wake_word = info_a.get('wake_word', '') or info_b.get('wake_word', '')

    models_a = get_model_order(results_a)
    models_b = get_model_order(results_b)

    # 统计数据
    total_tests_a = sum(r['total'] for r in results_a)
    total_tests_b = sum(r['total'] for r in results_b)
    total_recall_a = sum(r['recall'] for r in results_a)
    total_recall_b = sum(r['recall'] for r in results_b)
    rate_a = total_recall_a / total_tests_a * 100 if total_tests_a else 0
    rate_b = total_recall_b / total_tests_b * 100 if total_tests_b else 0
    failed_a = [r for r in results_a if not r['passed']]
    failed_b = [r for r in results_b if not r['passed']]
    total_far_a = sum(v['far_count'] for v in far_a.values())
    total_far_b = sum(v['far_count'] for v in far_b.values())

    lines = []
    w = lines.append

    # 标题
    w(f'# {product} 唤醒测试报告对比分析')
    w('')
    w(f'> 报告 A: {title_a}')
    w(f'> 报告 B: {title_b}')
    w('')
    w('---')
    w('')

    # === 一、对比总览 ===
    w('## 一、对比总览')
    w('')
    w('| 指标 | A (' + date_a + ') | B (' + date_b + ') | 变化 |')
    w('|------|-------------|-------------|------|')
    w(f'| 模型版本数 | {len(models_a)} | {len(models_b)} | {"不变" if len(models_a) == len(models_b) else "变化"} |')
    w(f'| 模型版本 | {", ".join(models_a)} | {", ".join(models_b)} | {"更新" if models_a != models_b else "不变"} |')
    w(f'| 场景数 | {len(results_a)} | {len(results_b)} | {"不变" if len(results_a) == len(results_b) else "变化"} |')
    w(f'| 总测试量 | {total_tests_a:,} | {total_tests_b:,} | {"不变" if total_tests_a == total_tests_b else "变化"} |')

    rate_diff = rate_b - rate_a
    w(f'| 整体召回率 | {rate_a:.2f}% | {rate_b:.2f}% | {fmt_change(rate_diff)} |')

    fail_diff = len(failed_b) - len(failed_a)
    fail_sign = '+' if fail_diff >= 0 else ''
    if len(failed_b) == 0:
        fail_status = '**全部达标**'
    else:
        fail_status = f'{fail_sign}{fail_diff}'
    w(f'| 未达标场景 | {len(failed_a)} | {len(failed_b)} | {fail_status} |')

    # 误唤醒按模型平均比较，避免不同模型数量导致误判
    far_models_a_count = len([v for v in far_a.values() if v['far_count'] > 0 or True])
    far_models_b_count = len([v for v in far_b.values() if v['far_count'] > 0 or True])
    avg_far_a = total_far_a / far_models_a_count if far_models_a_count else 0
    avg_far_b = total_far_b / far_models_b_count if far_models_b_count else 0

    if len(models_a) != len(models_b):
        far_note = f'(A {len(models_a)}个版本, B {len(models_b)}个版本)'
        w(f'| 误唤醒 | {total_far_a} 次 ({len(models_a)}版本) | {total_far_b} 次 ({len(models_b)}版本) | 单版本均值 {avg_far_a:.1f} vs {avg_far_b:.1f} {far_note} |')
    else:
        far_diff = total_far_b - total_far_a
        far_pct = (total_far_a - total_far_b) / total_far_a * 100 if total_far_a else 0
        far_sign = '+' if far_diff >= 0 else ''
        if total_far_b < total_far_a:
            far_status = f'{far_sign}{far_diff} (改善 {abs(far_pct):.0f}%)'
        elif total_far_b > total_far_a:
            far_status = f'{far_sign}{far_diff} (恶化 {abs(far_pct):.0f}%)'
        else:
            far_status = '不变'
        w(f'| 误唤醒 | {total_far_a} 次 | {total_far_b} 次 | {far_status} |')

    w('')
    w('---')
    w('')

    # === 二、模型版本说明 ===
    w('## 二、模型版本说明')
    w('')
    w('| 报告 | 模型 | 整体召回率 | 未达标 |')
    w('|------|------|-----------|--------|')

    for obj in models_a:
        obj_rows = [r for r in results_a if r['obj'] == obj]
        total = sum(r['total'] for r in obj_rows)
        recall = sum(r['recall'] for r in obj_rows)
        rate = recall / total * 100 if total else 0
        failed_count = len([r for r in obj_rows if not r['passed']])
        w(f'| A ({date_a}) | {obj} | {rate:.2f}% | {failed_count} |')

    for obj in models_b:
        obj_rows = [r for r in results_b if r['obj'] == obj]
        total = sum(r['total'] for r in obj_rows)
        recall = sum(r['recall'] for r in obj_rows)
        rate = recall / total * 100 if total else 0
        failed_count = len([r for r in obj_rows if not r['passed']])
        w(f'| B ({date_b}) | {obj} | {rate:.2f}% | {failed_count} |')

    w('')
    w('---')
    w('')

    # === 三、场景级召回率对比 ===
    w('## 三、场景级召回率对比')
    w('')

    # 构建场景索引：(scene, obj) -> result
    idx_a = {(r['scene'], r['obj']): r for r in results_a}
    idx_b = {(r['scene'], r['obj']): r for r in results_b}

    # 所有场景列表
    all_scenes = sorted(set(r['scene'] for r in results_a + results_b))
    all_model_pairs = set()
    for r in results_a:
        all_model_pairs.add(r['obj'])
    for r in results_b:
        all_model_pairs.add(r['obj'])
    all_models = sorted(all_model_pairs)

    # 3.1 有变化的场景
    w('### 3.1 有变化的场景（同模型同场景召回率差异 > 0.5%）')
    w('')

    # 找出两份报告都有的模型，按场景对比
    common_models = set(models_a) & set(models_b)

    changed = []
    improved = []
    degraded = []
    no_change = []

    for obj in sorted(common_models):
        for scene in all_scenes:
            ra = idx_a.get((scene, obj))
            rb = idx_b.get((scene, obj))
            if ra and rb:
                diff = rb['rate'] - ra['rate']
                entry = {
                    'obj': obj, 'scene': scene,
                    'rate_a': ra['rate'], 'rate_b': rb['rate'],
                    'standard': ra['standard'],
                    'diff': diff,
                    'passed_a': ra['passed'], 'passed_b': rb['passed'],
                }
                if abs(diff) > 0.5:
                    changed.append(entry)
                if not ra['passed'] and rb['passed']:
                    improved.append(entry)
                if ra['passed'] and not rb['passed']:
                    degraded.append(entry)
                if abs(diff) <= 0.5:
                    no_change.append(entry)

    # 对无共同模型的情况，做全场景对比
    if not common_models:
        # 取每个报告的第一个模型作为代表
        obj_a = models_a[0] if models_a else ''
        obj_b = models_b[0] if models_b else ''
        for scene in all_scenes:
            ra = idx_a.get((scene, obj_a))
            rb = idx_b.get((scene, obj_b))
            if ra and rb:
                diff = rb['rate'] - ra['rate']
                entry = {
                    'obj_a': obj_a, 'obj_b': obj_b, 'scene': scene,
                    'rate_a': ra['rate'], 'rate_b': rb['rate'],
                    'standard': ra['standard'],
                    'diff': diff,
                    'passed_a': ra['passed'], 'passed_b': rb['passed'],
                }
                if abs(diff) > 0.5:
                    changed.append(entry)
                if not ra['passed'] and rb['passed']:
                    improved.append(entry)
                if ra['passed'] and not rb['passed']:
                    degraded.append(entry)
                if abs(diff) <= 0.5:
                    no_change.append(entry)

    if changed:
        changed.sort(key=lambda x: -abs(x['diff']))
        if common_models:
            w('| 场景 | 模型 | A 召回率 | B 召回率 | 变化 | 验收标准 |')
            w('|------|------|---------|---------|------|---------|')
            for e in changed:
                sign = '+' if e['diff'] >= 0 else ''
                w(f'| {e["scene"]} | {e["obj"]} | {e["rate_a"]:.2f}% | {e["rate_b"]:.2f}% | {sign}{e["diff"]:.2f}% | {e["standard"]:.0f}% |')
        else:
            w('| 场景 | A 模型 | B 模型 | A 召回率 | B 召回率 | 变化 | 验收标准 |')
            w('|------|--------|--------|---------|---------|------|---------|')
            for e in changed:
                sign = '+' if e['diff'] >= 0 else ''
                w(f'| {e["scene"]} | {e["obj_a"]} | {e["obj_b"]} | {e["rate_a"]:.2f}% | {e["rate_b"]:.2f}% | {sign}{e["diff"]:.2f}% | {e["standard"]:.0f}% |')
        w('')
    else:
        w('> 无显著变化（差异均 ≤ 0.5%）')
        w('')

    # 3.2 改善的场景
    if improved:
        w('### 3.2 改善的场景（未达标 → 达标）')
        w('')
        if common_models:
            w('| 场景 | 模型 | A 召回率 | 验收标准 | B 召回率 | 状态 |')
            w('|------|------|---------|---------|---------|------|')
            for e in improved:
                w(f'| {e["scene"]} | {e["obj"]} | {e["rate_a"]:.2f}% ❌ | {e["standard"]:.0f}% | {e["rate_b"]:.2f}% ✅ | 已达标 |')
        else:
            w('| 场景 | A 模型 | B 模型 | A 召回率 | 验收标准 | B 召回率 | 状态 |')
            w('|------|--------|--------|---------|---------|---------|------|')
            for e in improved:
                w(f'| {e["scene"]} | {e["obj_a"]} | {e["obj_b"]} | {e["rate_a"]:.2f}% ❌ | {e["standard"]:.0f}% | {e["rate_b"]:.2f}% ✅ | 已达标 |')
        w('')

    # 3.3 恶化的场景
    if degraded:
        w('### 3.3 恶化的场景（达标 → 未达标）')
        w('')
        if common_models:
            w('| 场景 | 模型 | A 召回率 | 验收标准 | B 召回率 | 差距 |')
            w('|------|------|---------|---------|---------|------|')
            for e in degraded:
                gap = e['rate_b'] - e['standard']
                w(f'| {e["scene"]} | {e["obj"]} | {e["rate_a"]:.2f}% ✅ | {e["standard"]:.0f}% | {e["rate_b"]:.2f}% ❌ | {gap:+.2f}% |')
        else:
            w('| 场景 | A 模型 | B 模型 | A 召回率 | 验收标准 | B 召回率 | 差距 |')
            w('|------|--------|--------|---------|---------|---------|------|')
            for e in degraded:
                gap = e['rate_b'] - e['standard']
                w(f'| {e["scene"]} | {e["obj_a"]} | {e["obj_b"]} | {e["rate_a"]:.2f}% ✅ | {e["standard"]:.0f}% | {e["rate_b"]:.2f}% ❌ | {gap:+.2f}% |')
        w('')

    # 3.4 全场景对比表
    w('### 3.4 全场景召回率对比')
    w('')
    if common_models:
        for obj in sorted(common_models):
            w(f'#### {obj}')
            w('')
            w('| 场景 | A 召回率 | B 召回率 | 变化 | 验收标准 | 达标 |')
            w('|------|---------|---------|------|---------|------|')
            for scene in all_scenes:
                ra = idx_a.get((scene, obj))
                rb = idx_b.get((scene, obj))
                if ra and rb:
                    diff = rb['rate'] - ra['rate']
                    sign = '+' if diff >= 0 else ''
                    passed = '✅' if rb['passed'] else '❌'
                    w(f'| {scene} | {ra["rate"]:.2f}% | {rb["rate"]:.2f}% | {sign}{diff:.2f}% | {ra["standard"]:.0f}% | {passed} |')
            w('')
    else:
        # 无共同模型时，做全场景 A vs B 对比
        obj_a = models_a[0] if models_a else ''
        obj_b = models_b[0] if models_b else ''
        w(f'| 场景 | A ({obj_a}) | B ({obj_b}) | 变化 | 验收标准 | B 达标 |')
        w('|------|-----------|-----------|------|---------|--------|')
        for scene in all_scenes:
            ra = idx_a.get((scene, obj_a))
            rb = idx_b.get((scene, obj_b))
            if ra and rb:
                diff = rb['rate'] - ra['rate']
                sign = '+' if diff >= 0 else ''
                passed = '✅' if rb['passed'] else '❌'
                w(f'| {scene} | {ra["rate"]:.2f}% | {rb["rate"]:.2f}% | {sign}{diff:.2f}% | {ra["standard"]:.0f}% | {passed} |')
        w('')

    w('---')
    w('')

    # === 四、召回率最低场景 ===
    w('## 四、召回率最低场景')
    w('')
    top_n = 5
    worst_a = sorted(results_a, key=lambda x: x['rate'])[:top_n]
    worst_b = sorted(results_b, key=lambda x: x['rate'])[:top_n]

    w(f'| 排名 | A ({date_a}) | 召回率 | B ({date_b}) | 召回率 |')
    w(f'|------|---------|--------|---------|--------|')
    for i in range(top_n):
        a_label = f'{worst_a[i]["scene"]} ({worst_a[i]["obj"]})' if i < len(worst_a) else '—'
        a_rate = f'{worst_a[i]["rate"]:.2f}% {"❌" if not worst_a[i]["passed"] else ""}' if i < len(worst_a) else '—'
        b_label = f'{worst_b[i]["scene"]} ({worst_b[i]["obj"]})' if i < len(worst_b) else '—'
        b_rate = f'{worst_b[i]["rate"]:.2f}% {"❌" if not worst_b[i]["passed"] else ""}' if i < len(worst_b) else '—'
        w(f'| {i + 1} | {a_label} | {a_rate} | {b_label} | {b_rate} |')

    w('')
    w('---')
    w('')

    # === 五、误唤醒对比 ===
    w('## 五、误唤醒对比')
    w('')

    # 5.1 汇总
    w('### 5.1 误唤醒汇总')
    w('')
    w('| 模型 | A 误唤醒 | B 误唤醒 | 变化 |')
    w('|------|---------|---------|------|')

    all_models_far = sorted(set(list(far_a.keys()) + list(far_b.keys())))
    for name in all_models_far:
        ca = far_a.get(name, {}).get('far_count', 0)
        cb = far_b.get(name, {}).get('far_count', 0)
        if ca == 0 and cb == 0:
            continue
        diff = cb - ca
        sign = '+' if diff >= 0 else ''
        in_a = name in far_a
        in_b = name in far_b
        if not in_a:
            status = f'{cb} (新增)'
        elif not in_b:
            status = f'— (已淘汰)'
        else:
            status = f'{sign}{diff}'
        w(f'| {name} | {ca if in_a else "—"} | {cb if in_b else "—"} | {status} |')

    w(f'| **合计** | **{total_far_a}** | **{total_far_b}** | **{total_far_b - total_far_a:+d}** |')
    w('')

    # 5.2 详细记录
    far_models_a = {k: v for k, v in far_a.items() if v['far_count'] > 0}
    far_models_b = {k: v for k, v in far_b.items() if v['far_count'] > 0}

    if far_models_a or far_models_b:
        w('### 5.2 误唤醒详细记录')
        w('')

        for label, far_models, date in [('A', far_models_a, date_a), ('B', far_models_b, date_b)]:
            for name, data in sorted(far_models.items()):
                w(f'#### {label} ({date}) - {name}（{data["far_count"]} 次）')
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

    # 5.3 特征分析
    all_far_details = []
    for data in far_a.values():
        all_far_details.extend(data['details'])
    for data in far_b.values():
        all_far_details.extend(data['details'])

    if all_far_details:
        w('### 5.3 误唤醒特征分析')
        w('')

        noise_dist = defaultdict(int)
        conf_ranges = defaultdict(int)
        for d in all_far_details:
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
        total_all_far = len(all_far_details)
        for noise, count in sorted(noise_dist.items(), key=lambda x: -x[1]):
            pct = count / total_all_far * 100 if total_all_far else 0
            w(f'| {noise} | {count} | {pct:.1f}% |')

        w('')
        w('**置信度分布：**')
        w('')
        w('| 置信度区间 | 次数 | 特征 |')
        w('|-----------|------|------|')
        for rng, feat in [('< 0.80', '低裕量，阈值微调可能消除'),
                          ('0.80 ~ 0.85', '中等裕量'),
                          ('0.85 ~ 0.90', '较高置信度'),
                          ('0.90+', '高置信度，误唤醒信号较强')]:
            count = conf_ranges.get(rng, 0)
            if count > 0:
                w(f'| {rng} | {count} | {feat} |')

        w('')

    w('---')
    w('')

    # === 六、结论与建议 ===
    w('## 六、结论与建议')
    w('')

    # 总结性分析
    conclusions = []

    # 召回率趋势
    if rate_b > rate_a:
        conclusions.append(f'整体召回率从 {rate_a:.2f}% 提升至 {rate_b:.2f}%（+{rate_b - rate_a:.2f}%）')
    elif rate_b < rate_a:
        conclusions.append(f'整体召回率从 {rate_a:.2f}% 下降至 {rate_b:.2f}%（{rate_b - rate_a:.2f}%）')
    else:
        conclusions.append(f'整体召回率持平 {rate_a:.2f}%')

    # 未达标趋势
    if len(failed_b) < len(failed_a):
        conclusions.append(f'未达标场景从 {len(failed_a)} 降至 {len(failed_b)}')
    elif len(failed_b) > len(failed_a):
        conclusions.append(f'未达标场景从 {len(failed_a)} 增至 {len(failed_b)}')

    # 误唤醒趋势（按单版本均值比较）
    if len(models_a) != len(models_b):
        if avg_far_b < avg_far_a:
            conclusions.append(f'误唤醒单版本均值从 {avg_far_a:.1f} 次降至 {avg_far_b:.1f} 次（A {len(models_a)}版本共{total_far_a}次, B {len(models_b)}版本共{total_far_b}次）')
        elif avg_far_b > avg_far_a:
            conclusions.append(f'误唤醒单版本均值从 {avg_far_a:.1f} 次增至 {avg_far_b:.1f} 次（A {len(models_a)}版本共{total_far_a}次, B {len(models_b)}版本共{total_far_b}次）')
        else:
            conclusions.append(f'误唤醒单版本均值持平 {avg_far_a:.1f} 次（A {len(models_a)}版本共{total_far_a}次, B {len(models_b)}版本共{total_far_b}次）')
    else:
        if total_far_b < total_far_a:
            pct = (total_far_a - total_far_b) / total_far_a * 100 if total_far_a else 0
            conclusions.append(f'误唤醒从 {total_far_a} 次降至 {total_far_b} 次（-{pct:.0f}%）')
        elif total_far_b > total_far_a:
            conclusions.append(f'误唤醒从 {total_far_a} 次增至 {total_far_b} 次')

    for i, c in enumerate(conclusions):
        w(f'{i + 1}. {c}')

    w('')

    # 场景维度建议
    if degraded:
        w('### 恶化场景需关注')
        for e in degraded:
            if 'obj' in e:
                w(f'- `{e["scene"]}`（{e["obj"]}）从 {e["rate_a"]:.2f}% 降至 {e["rate_b"]:.2f}%，已不达标')
            else:
                w(f'- `{e["scene"]}` 从 {e["rate_a"]:.2f}% 降至 {e["rate_b"]:.2f}%，已不达标')
        w('')

    if improved:
        w('### 改善场景')
        for e in improved:
            if 'obj' in e:
                w(f'- `{e["scene"]}`（{e["obj"]}）从 {e["rate_a"]:.2f}% 提升至 {e["rate_b"]:.2f}%，已达标')
            else:
                w(f'- `{e["scene"]}` 从 {e["rate_a"]:.2f}% 提升至 {e["rate_b"]:.2f}%，已达标')
        w('')

    # 未达标场景汇总
    if failed_b:
        w('### 当前未达标场景（B 报告）')
        w('')
        w('| 场景 | 模型 | 召回率 | 验收标准 | 差距 |')
        w('|------|------|--------|---------|------|')
        for r in sorted(failed_b, key=lambda x: x['rate']):
            diff = r['rate'] - r['standard']
            w(f'| {r["scene"]} | {r["obj"]} | {r["rate"]:.2f}% | {r["standard"]:.0f}% | {diff:+.2f}% |')
        w('')
        w('- 建议重点优化未达标场景的唤醒模型参数')
        if total_far_b > 0:
            w('- 对误唤醒较多的模型，建议适当提升唤醒阈值以平衡召回率与误唤醒的矛盾')
        w('')

    # 写入文件
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    return output_path


def main():
    if len(sys.argv) < 3:
        print('用法: python3 iot_wakeup_report_compare.py <html_A> <html_B> [输出目录]')
        sys.exit(1)

    html_a_path = sys.argv[1]
    html_b_path = sys.argv[2]
    output_dir = sys.argv[3] if len(sys.argv) > 3 else os.path.dirname(html_a_path)

    for p in [html_a_path, html_b_path]:
        if not os.path.exists(p):
            print(f'错误: 文件不存在 {p}')
            sys.exit(1)

    with open(html_a_path, 'r', encoding='utf-8') as f:
        html_a = f.read()
    with open(html_b_path, 'r', encoding='utf-8') as f:
        html_b = f.read()

    title_a = extract_title(html_a)
    title_b = extract_title(html_b)
    tables_a = parse_tables(html_a)
    tables_b = parse_tables(html_b)
    results_a = extract_scene_results(tables_a)
    results_b = extract_scene_results(tables_b)
    far_a = extract_far_data(html_a)
    far_b = extract_far_data(html_b)

    # 生成输出文件名
    info = parse_title_info(title_a)
    product_slug = info.get('product', 'IOT').replace(' ', '_')
    output_name = f'{product_slug}唤醒测试报告对比分析.md'
    output_path = os.path.join(output_dir, output_name)

    generate_compare_report(title_a, title_b, results_a, results_b, far_a, far_b, output_path)
    print(f'对比报告已生成: {output_path}')


if __name__ == '__main__':
    main()
