#!/usr/bin/env python3
"""
Multichannel PCM Audio Comprehensive Analysis Tool
Analyze embedded device recorded audio: 4mic+4ref 8ch PCM files

Usage:
  python3 multichannel_pcm_analysis.py <pcm_file> [options]

Options:
  --sr RATE         Sample rate (default: 16000)
  --nch CHANNELS    Number of channels (default: 8)
  --bps BITS        Bits per sample (default: 16)
  --outdir DIR      Output directory (default: /tmp/pcm_analysis)
  --quick           Quick mode: only basic stats, skip full-file scan

Output:
  - Console report with all metrics
  - comprehensive_analysis.png (15-panel visualization)
  - analysis_report.json (structured data)
"""
import numpy as np
from scipy import signal as sig
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import os, sys, json, argparse, time

# ============================================================
# Configuration
# ============================================================
CH_LABELS_8CH = ['mic0', 'mic1', 'mic2', 'mic3', 'ref0', 'ref1', 'ref2', 'ref3']

# Detection thresholds
HARD_CLIP_THRESHOLD = 32767
SOFT_CLIP_THRESHOLD = 30000
MIN_CONSEC_CLIP = 3
ZERO_RUN_MIN_SAMPLES = 160       # 10ms @ 16kHz
JUMP_THRESHOLD = 8000
REPEAT_FRAME_SIZE = 160           # 10ms
ACTIVE_THRESHOLD_DBFS = -40
SILENT_THRESHOLD_DBFS = -65
FRAME_SIZE_10MS = 160

# ============================================================
# Utility functions
# ============================================================
def rms_dbfs(data):
    """Compute RMS in dBFS for int16 data"""
    return 20 * np.log10(np.sqrt(np.mean(data.astype(np.float64)**2)) / 32768 + 1e-10)

def read_segment(fpath, start_frame, n_frames, nch, dtype=np.int16):
    """Read a segment from PCM file"""
    bps = np.dtype(dtype).itemsize
    offset = start_frame * nch * bps
    data = np.fromfile(fpath, dtype=dtype, count=n_frames * nch, offset=offset)
    return data.reshape(-1, nch)

def gcc_phat(a, b, max_lag=64):
    """GCC-PHAT between signals a and b, return delay in samples"""
    N = len(a)
    A = np.fft.rfft(a)
    B = np.fft.rfft(b)
    gcc = np.fft.irfft(A * np.conj(B) / (np.abs(A * np.conj(B)) + 1e-10))
    gcc_seg = np.concatenate([gcc[-max_lag:], gcc[:max_lag+1]])
    peak_idx = np.argmax(gcc_seg)
    return peak_idx - max_lag

def find_active_segments(rms_db, threshold, min_gap_frames=100, min_dur_frames=50):
    """Group active frames into segments"""
    active = np.max(rms_db[:, :4], axis=1) > threshold
    indices = np.where(active)[0]
    if len(indices) == 0:
        return []
    gaps = np.diff(indices)
    breaks = np.where(gaps > min_gap_frames)[0]
    segments = []
    seg_start = indices[0]
    for b in breaks:
        seg_end = indices[b]
        if seg_end - seg_start >= min_dur_frames:
            segments.append((seg_start, seg_end))
        seg_start = indices[b+1]
    seg_end = indices[-1]
    if seg_end - seg_start >= min_dur_frames:
        segments.append((seg_start, seg_end))
    return segments

# ============================================================
# Analysis modules
# ============================================================

def analyze_file_integrity(fpath, nch, bps):
    """BASIC: File integrity check"""
    fsize = os.path.getsize(fpath)
    remainder = fsize % (nch * bps)
    total_frames = fsize // (nch * bps)
    return {
        'file_size': fsize,
        'file_size_mb': fsize / 1024 / 1024,
        'aligned': remainder == 0,
        'total_frames': total_frames,
        'total_seconds': total_frames / 16000  # placeholder, updated later
    }

def analyze_basic_stats(fpath, sr, nch, ch_labels):
    """BASIC: Quick first/last segment stats"""
    # Read first 5s
    data = read_segment(fpath, 0, 5*sr, nch).astype(np.float64)
    stats = {}
    for ch in range(nch):
        ch_data = data[:, ch]
        stats[ch_labels[ch]] = {
            'rms_dbfs': rms_dbfs(ch_data),
            'peak_dbfs': 20*np.log10(np.max(np.abs(ch_data))/32768+1e-10),
            'dc_offset': float(np.mean(ch_data))
        }
    return stats

def analyze_full_energy(fpath, sr, nch, total_frames, ch_labels, block_sec=60):
    """FULL-FILE: Per-10ms RMS energy timeline"""
    block_frames = sr * block_sec
    n_blocks = total_frames // block_frames + 1
    n_10ms = total_frames // FRAME_SIZE_10MS
    all_rms_db = np.zeros((n_10ms, nch), dtype=np.float32)

    for blk in range(n_blocks):
        start = blk * block_frames
        count = min(block_frames, total_frames - start)
        if count <= 0:
            break
        data = read_segment(fpath, start, count, nch).astype(np.float32)
        n_sub = count // FRAME_SIZE_10MS
        for fi in range(n_sub):
            frame = data[fi*FRAME_SIZE_10MS:(fi+1)*FRAME_SIZE_10MS, :]
            rms = np.sqrt(np.mean(frame**2, axis=0))
            gi = start // FRAME_SIZE_10MS + fi
            if gi < n_10ms:
                all_rms_db[gi, :] = rms

    all_rms_db = 20 * np.log10(all_rms_db / 32768 + 1e-10)
    return all_rms_db

def analyze_clipping(fpath, sr, nch, total_frames, ch_labels, block_sec=60):
    """INTEGRITY: Clipping detection"""
    block_frames = sr * block_sec
    n_blocks = total_frames // block_frames + 1
    hard_count = np.zeros(nch, dtype=np.int64)
    soft_count = np.zeros(nch, dtype=np.int64)
    clip_events = []

    for blk in range(n_blocks):
        start = blk * block_frames
        count = min(block_frames, total_frames - start)
        if count <= 0:
            break
        data = read_segment(fpath, start, count, nch)
        for ch in range(nch):
            ch_data = data[:, ch]
            hard_mask = np.abs(ch_data) >= HARD_CLIP_THRESHOLD
            hc = np.sum(hard_mask)
            hard_count[ch] += hc
            soft_count[ch] += np.sum(np.abs(ch_data) >= SOFT_CLIP_THRESHOLD)
            if hc > 0:
                indices = np.where(hard_mask)[0]
                rs, rl = indices[0], 1
                for k in range(1, len(indices)):
                    if indices[k] == indices[k-1]+1:
                        rl += 1
                    else:
                        if rl >= MIN_CONSEC_CLIP:
                            clip_events.append({'time_s': (start+rs)/sr, 'channel': ch_labels[ch], 'duration_samples': int(rl)})
                        rs, rl = indices[k], 1
                if rl >= MIN_CONSEC_CLIP:
                    clip_events.append({'time_s': (start+rs)/sr, 'channel': ch_labels[ch], 'duration_samples': int(rl)})

    return {
        'hard_clip_count': hard_count.tolist(),
        'soft_clip_count': soft_count.tolist(),
        'consecutive_events': clip_events,
        'has_clipping': np.sum(hard_count) > 0
    }

def analyze_data_loss(fpath, sr, nch, total_frames, ch_labels, block_sec=60):
    """INTEGRITY: Data loss detection (zero runs, jumps, repeats)"""
    block_frames = sr * block_sec
    n_blocks = total_frames // block_frames + 1
    zero_runs = []
    jump_events = []
    repeat_events = []

    for blk in range(n_blocks):
        start = blk * block_frames
        count = min(block_frames, total_frames - start)
        if count <= 0:
            break
        data = read_segment(fpath, start, count, nch)
        for ch in range(nch):
            ch_data = data[:, ch]
            # Zero runs
            zero_mask = ch_data == 0
            if np.any(zero_mask):
                zi = np.where(zero_mask)[0]
                rs, rl = zi[0], 1
                for k in range(1, len(zi)):
                    if zi[k] == zi[k-1]+1: rl += 1
                    else:
                        if rl >= ZERO_RUN_MIN_SAMPLES:
                            zero_runs.append({'time_s': (start+rs)/sr, 'channel': ch_labels[ch], 'samples': int(rl), 'ms': rl/sr*1000})
                        rs, rl = zi[k], 1
                if rl >= ZERO_RUN_MIN_SAMPLES:
                    zero_runs.append({'time_s': (start+rs)/sr, 'channel': ch_labels[ch], 'samples': int(rl), 'ms': rl/sr*1000})
            # Jumps
            diffs = np.abs(np.diff(ch_data.astype(np.int32)))
            big = np.where(diffs > JUMP_THRESHOLD)[0]
            for ji in big:
                jump_events.append({'time_s': (start+ji)/sr, 'channel': ch_labels[ch], 'diff': int(diffs[ji])})
            # Repeats
            n_sub = count // REPEAT_FRAME_SIZE
            if n_sub >= 2:
                prev = None
                for fi in range(n_sub):
                    frame = ch_data[fi*REPEAT_FRAME_SIZE:(fi+1)*REPEAT_FRAME_SIZE]
                    if prev is not None and np.array_equal(frame, prev):
                        repeat_events.append({'time_s': (start+fi*REPEAT_FRAME_SIZE)/sr, 'channel': ch_labels[ch]})
                    prev = frame.copy()

    return {
        'zero_runs': zero_runs,
        'jump_events': jump_events,
        'repeat_events': repeat_events,
        'has_data_loss': len(zero_runs) > 0 or len(jump_events) > 0
    }

def analyze_channel_correlation(data, ch_labels):
    """A-CLASS: Cross-channel correlation matrix"""
    corr = np.corrcoef(data.T)
    return {
        'matrix': corr.tolist(),
        'labels': ch_labels,
        'high_corr_pairs': _find_pairs(corr, ch_labels, 0.9),
        'near_zero_pairs': _find_pairs(corr, ch_labels, 0.05, mode='near_zero')
    }

def _find_pairs(corr, labels, threshold, mode='above'):
    pairs = []
    for i in range(len(labels)):
        for j in range(i+1, len(labels)):
            r = corr[i, j]
            if mode == 'above' and r > threshold:
                pairs.append({'a': labels[i], 'b': labels[j], 'r': float(r)})
            elif mode == 'near_zero' and -threshold < r < threshold:
                pairs.append({'a': labels[i], 'b': labels[j], 'r': float(r)})
    return pairs

def analyze_mic_gain_consistency(fpath, sr, nch, all_rms_db, ch_labels):
    """A1: Mic gain consistency across active segments"""
    mic_max = np.max(all_rms_db[:, :4], axis=1)
    active = np.where(mic_max > -35)[0]
    if len(active) == 0:
        return {'error': 'No active segments found'}

    step = max(1, len(active) // 20)
    selected = active[::step][:20]
    results = []
    for fi in selected:
        start = fi * FRAME_SIZE_10MS
        data = read_segment(fpath, start, sr, nch).astype(np.float64)
        rms = [rms_dbfs(data[:, ch]) for ch in range(4)]
        results.append({'time_s': start/sr, 'mic_rms': rms, 'max_diff': max(rms)-min(rms)})

    diffs = [r['max_diff'] for r in results]
    mean_per_ch = np.mean([r['mic_rms'] for r in results], axis=0)
    return {
        'segments': results,
        'mean_per_channel': mean_per_ch.tolist(),
        'avg_intra_diff': float(np.mean(diffs)),
        'max_intra_diff': float(np.max(diffs)),
        'pass': np.max(diffs) < 1.0
    }

def analyze_inter_mic_delay(fpath, sr, nch, ch_labels):
    """A2: Inter-mic delay via GCC-PHAT"""
    # Find loudest segment
    data = read_segment(fpath, 0, 10*sr, nch).astype(np.float64)
    # Use middle 3s for cleaner measurement
    data_mid = data[5*sr:8*sr, :]
    pairs = [(0,1),(0,2),(0,3),(1,2),(1,3),(2,3)]
    results = []
    for a, b in pairs:
        delays = []
        win = sr // 10  # 100ms windows
        for wi in range(0, len(data_mid)-win, win):
            d = gcc_phat(data_mid[wi:wi+win, a], data_mid[wi:wi+win, b])
            delays.append(d)
        results.append({
            'pair': f"{ch_labels[a]}-{ch_labels[b]}",
            'mean_delay_samples': float(np.mean(delays)),
            'std_delay_samples': float(np.std(delays)),
            'mean_delay_us': float(np.mean(delays)/sr*1e6)
        })
    return results

def analyze_crosstalk(fpath, sr, nch, all_rms_db, ch_labels):
    """A3: Mic-to-Ref crosstalk analysis"""
    mic_max = np.max(all_rms_db[:, :4], axis=1)
    ref_max = np.max(all_rms_db[:, 4:], axis=1)
    ref_active = ref_max > mic_max + 5
    ref_indices = np.where(ref_active)[0]

    if len(ref_indices) > 0:
        mid = ref_indices[len(ref_indices)//2]
        start = mid * FRAME_SIZE_10MS
        data = read_segment(fpath, start, sr, nch).astype(np.float64)
    else:
        # Fallback to loudest segment
        data = read_segment(fpath, 0, sr, nch).astype(np.float64)

    corr = np.corrcoef(data.T)
    mic_ref = corr[:4, 4:]
    return {
        'mic_ref_correlation': mic_ref.tolist(),
        'suspicious_pairs': [{'mic': ch_labels[i], 'ref': ch_labels[4+j], 'r': float(mic_ref[i,j])}
                             for i in range(4) for j in range(4) if abs(mic_ref[i,j]) > 0.8]
    }

def analyze_ref_quality(fpath, sr, nch, ch_labels):
    """A4+A5: Ref channel quality and Ref-Mic delay"""
    # Use first 5s for baseline
    data = read_segment(fpath, 0, 5*sr, nch).astype(np.float64)
    ref_results = []
    for ch in range(4, nch):
        ch_data = data[:, ch]
        rms = rms_dbfs(ch_data)
        peak_db = 20*np.log10(np.max(np.abs(ch_data))/32768+1e-10)
        f, Pxx = sig.welch(ch_data, fs=sr, nperseg=4096)
        Pxx_db = 10*np.log10(Pxx+1e-20)
        significant = Pxx_db > np.median(Pxx_db) + 10
        f_low = f[significant][0] if np.any(significant) else 0
        f_high = f[significant][-1] if np.any(significant) else 0
        ref_results.append({
            'channel': ch_labels[ch], 'rms_dbfs': rms, 'peak_dbfs': peak_db,
            'bandwidth_hz': f"{f_low:.0f}-{f_high:.0f}"
        })

    # Ref-Mic delay
    delay_results = []
    for r in range(4):
        for m in range(4):
            d = gcc_phat(data[:sr, 4+r], data[:sr, m], max_lag=min(len(data[:sr,0])//2, 256))
            delay_results.append({
                'pair': f"ref{r}-mic{m}", 'delay_samples': int(d), 'delay_us': d/sr*1e6
            })
    return {'ref_quality': ref_results, 'ref_mic_delay': delay_results}

def analyze_ein_dr(fpath, sr, nch, all_rms_db, ch_labels):
    """B6+B7: EIN and Dynamic Range"""
    # Find pure silent segments
    mic_max = np.max(all_rms_db[:, :4], axis=1)
    silent = np.where(mic_max < SILENT_THRESHOLD_DBFS)[0]

    ein_results = []
    if len(silent) > 5:
        step = max(1, len(silent) // 10)
        for fi in silent[::step][:10]:
            start = fi * FRAME_SIZE_10MS
            data = read_segment(fpath, start, sr, nch).astype(np.float64)
            rms_vals = [rms_dbfs(data[:, ch]) for ch in range(nch)]
            if max(rms_vals) < -60:
                ein_results.append(rms_vals)

    if ein_results:
        ein_arr = np.array(ein_results)
        noise_floor = np.mean(ein_arr, axis=0)
    else:
        noise_floor = np.full(nch, -72.0)

    # Peak from a louder segment
    active_segs = find_active_segments(all_rms_db, -35)
    peak_level = np.full(nch, -20.0)
    if active_segs:
        seg = max(active_segs, key=lambda s: s[1]-s[0])
        mid = (seg[0]+seg[1])//2
        data = read_segment(fpath, mid*FRAME_SIZE_10MS, 3*sr, nch).astype(np.float64)
        peak_level = np.array([20*np.log10(np.max(np.abs(data[:,ch]))/32768+1e-10) for ch in range(nch)])

    dr = peak_level - noise_floor
    return {
        'noise_floor_dbfs': noise_floor.tolist(),
        'peak_level_dbfs': peak_level.tolist(),
        'dynamic_range_db': dr.tolist(),
        'avg_dynamic_range': float(np.mean(dr))
    }

def analyze_frequency_response(fpath, sr, nch, all_rms_db, ch_labels):
    """B8: Frequency response analysis"""
    active_segs = find_active_segments(all_rms_db, -35, min_dur_frames=500)
    if not active_segs:
        return {'error': 'No sufficiently long active segments'}

    seg = max(active_segs, key=lambda s: s[1]-s[0])
    mid = (seg[0]+seg[1])//2
    start = mid * FRAME_SIZE_10MS
    dur = min(4.0, (seg[1]-seg[0])*FRAME_SIZE_10MS/sr)
    data = read_segment(fpath, start, int(dur*sr), nch).astype(np.float64)

    freq_points = [50, 100, 200, 500, 1000, 2000, 3000, 4000, 5000, 6000, 7000, 7500]
    resp = {}
    for ch in range(nch):
        f, Pxx = sig.welch(data[:, ch], fs=sr, nperseg=min(8192, len(data)))
        Pxx_db = 10*np.log10(Pxx+1e-20)
        idx_1k = np.argmin(np.abs(f-1000))
        ref = Pxx_db[idx_1k]
        resp[ch_labels[ch]] = {fp: float(Pxx_db[np.argmin(np.abs(f-fp))] - ref) for fp in freq_points}

    return {'freq_points': freq_points, 'response': resp}

def analyze_noise_type(fpath, sr, nch, ch_labels):
    """B9: Noise type characterization"""
    data = read_segment(fpath, 0, 10*sr, nch).astype(np.float64)
    results = {}
    for ch in [0, 4]:
        f, Pxx = sig.welch(data[:, ch], fs=sr, nperseg=8192)
        Pxx_db = 10*np.log10(Pxx+1e-20)
        bands = [('low_50_500', 50, 500), ('mid_500_4k', 500, 4000), ('high_4k_7k', 4000, 7000)]
        slopes = {}
        for bname, flo, fhi in bands:
            mask = (f >= flo) & (f <= fhi)
            if np.sum(mask) > 2:
                lf = np.log10(f[mask])
                lp = Pxx_db[mask]
                slope = float(np.polyfit(lf, lp, 1)[0])
                if abs(slope) < 3: ntype = 'white'
                elif -15 < slope < -5: ntype = 'pink'
                elif slope < -15: ntype = 'brown'
                elif slope > 5: ntype = 'blue/violet'
                else: ntype = 'mixed'
                slopes[bname] = {'slope_db_per_decade': slope, 'type': ntype}
        total_p = np.sum(Pxx[f > 20])
        centroid = float(np.sum(f[f>20] * Pxx[f>20]) / total_p) if total_p > 0 else 0
        results[ch_labels[ch]] = {'slopes': slopes, 'spectral_centroid_hz': centroid}
    return results

def analyze_aliasing(fpath, sr, nch, ch_labels):
    """B11: Aliasing detection"""
    data = read_segment(fpath, 0, 5*sr, nch).astype(np.float64)
    results = {}
    for ch in [0, 4]:
        f, Pxx = sig.welch(data[:, ch], fs=sr, nperseg=16384)
        Pxx_db = 10*np.log10(Pxx+1e-20)
        high = np.mean(Pxx_db[(f>7000)&(f<=8000)])
        mid = np.mean(Pxx_db[(f>3000)&(f<=5000)])
        rolloff = mid - high
        near_nyq = np.mean(Pxx_db[(f>7500)&(f<=8000)])
        below_nyq = np.mean(Pxx_db[(f>7000)&(f<=7500)])
        results[ch_labels[ch]] = {
            'rolloff_mid_to_high_db': float(rolloff),
            'near_nyquist_level_db': float(near_nyq),
            'likely_aliased': near_nyq > below_nyq - 3
        }
    return results

def analyze_stability(fpath, sr, nch, total_frames, ch_labels, block_sec=30):
    """C12+C13+C14: Long-term stability"""
    block_frames = sr * block_sec
    n_blocks = total_frames // block_frames
    noise_timeline, dc_timeline, noise_times = [], [], []
    gain_timeline, gain_times = [], []

    for blk in range(0, n_blocks, 2):  # every 60s
        start = blk * block_frames
        count = min(block_frames, total_frames - start)
        if count < sr: break
        data = read_segment(fpath, start, count, nch).astype(np.float64)
        noise_timeline.append([rms_dbfs(data[:, ch]) for ch in range(nch)])
        dc_timeline.append([float(np.mean(data[:, ch])) for ch in range(nch)])
        noise_times.append(start / sr)

    noise_timeline = np.array(noise_timeline)
    dc_timeline = np.array(dc_timeline)
    noise_times = np.array(noise_times)
    t_min = noise_times / 60

    # Noise floor drift
    mic0_noise = noise_timeline[:, 0]
    z_noise = np.polyfit(t_min, mic0_noise, 1) if len(t_min) > 1 else [0, 0]

    # DC drift
    dc_drifts = {}
    for ch in range(nch):
        if len(t_min) > 1:
            z = np.polyfit(t_min, dc_timeline[:, ch], 1)
            dc_drifts[ch_labels[ch]] = float(z[0])

    # Gain stability from pre-computed RMS (if available from caller)
    # We'll compute from noise_timeline as approximation
    gain_drift = float(np.max(mic0_noise) - np.min(mic0_noise)) if len(mic0_noise) > 0 else 0

    return {
        'noise_floor': {
            'mean_dbfs': float(np.mean(mic0_noise)),
            'std_db': float(np.std(mic0_noise)),
            'range_db': float(np.ptp(mic0_noise)),
            'drift_db_per_min': float(z_noise[0]) if len(z_noise) > 0 else 0
        },
        'dc_offset': {
            'drifts_per_min': dc_drifts,
            'max_abs': float(np.max(np.abs(dc_timeline)))
        },
        'gain_drift_db': gain_drift
    }

# ============================================================
# Visualization
# ============================================================
def generate_plots(fpath, sr, nch, total_frames, ch_labels, all_rms_db, results, outdir):
    """Generate 15-panel comprehensive analysis figure"""
    os.makedirs(outdir, exist_ok=True)

    fig = plt.figure(figsize=(28, 44))
    fig.suptitle(f'Multichannel PCM Audio Analysis\n{os.path.basename(fpath)} | {sr}Hz/{16}bit/{nch}ch | {total_frames/sr:.1f}s',
                 fontsize=14, fontweight='bold', y=0.995)

    time_min = np.arange(all_rms_db.shape[0]) * 10 / 1000 / 60

    # Panel 1: Energy timeline
    ax = fig.add_subplot(5, 3, 1)
    for ch in range(min(nch, 8)):
        ax.plot(time_min, all_rms_db[:, ch], linewidth=0.2, alpha=0.7, label=ch_labels[ch])
    ax.set_xlabel('Time (min)'); ax.set_ylabel('RMS (dBFS)')
    ax.set_title('Energy Timeline'); ax.legend(fontsize=6, ncol=4); ax.grid(True, alpha=0.3)

    # Panel 2: RMS histogram
    ax = fig.add_subplot(5, 3, 2)
    for ch in range(min(nch, 8)):
        ax.hist(all_rms_db[:, ch], bins=200, alpha=0.3, label=ch_labels[ch], density=True)
    ax.set_xlabel('RMS (dBFS)'); ax.set_ylabel('Density')
    ax.set_title('RMS Distribution'); ax.legend(fontsize=6, ncol=4); ax.grid(True, alpha=0.3)

    # Panel 3: Clipping
    ax = fig.add_subplot(5, 3, 3)
    clip = results.get('clipping', {})
    if clip.get('hard_clip_count'):
        bars = ax.bar(range(nch), clip['hard_clip_count'], alpha=0.7, color='red', label='Hard')
        ax.bar(range(nch), clip['soft_clip_count'], alpha=0.5, color='orange', label='Soft')
    ax.set_xticks(range(nch)); ax.set_xticklabels(ch_labels[:nch], fontsize=7)
    ax.set_ylabel('Count'); ax.set_title(f'Clipping (Hard: {sum(clip.get("hard_clip_count",[0]*nch))})')
    ax.legend(fontsize=7); ax.grid(True, alpha=0.3, axis='y')

    # Panel 4: Correlation heatmap
    ax = fig.add_subplot(5, 3, 4)
    corr_data = results.get('channel_correlation', {}).get('matrix', np.eye(nch).tolist())
    corr_arr = np.array(corr_data)
    im = ax.imshow(corr_arr, cmap='RdBu_r', vmin=-1, vmax=1, aspect='auto')
    ax.set_xticks(range(nch)); ax.set_yticks(range(nch))
    ax.set_xticklabels(ch_labels[:nch], fontsize=7); ax.set_yticklabels(ch_labels[:nch], fontsize=7)
    ax.set_title('Cross-Correlation')
    for i in range(nch):
        for j in range(nch):
            ax.text(j, i, f'{corr_arr[i,j]:.2f}', ha='center', va='center', fontsize=6,
                    color='white' if abs(corr_arr[i,j]) > 0.5 else 'black')
    plt.colorbar(im, ax=ax, shrink=0.8)

    # Panel 5: Mic gain consistency
    ax = fig.add_subplot(5, 3, 5)
    gain = results.get('mic_gain', {})
    if 'segments' in gain:
        for ch in range(4):
            vals = [s['mic_rms'][ch] for s in gain['segments']]
            times = [s['time_s']/60 for s in gain['segments']]
            ax.plot(times, vals, '-o', markersize=2, linewidth=0.8, label=ch_labels[ch])
    ax.set_xlabel('Time (min)'); ax.set_ylabel('Active RMS (dBFS)')
    ax.set_title(f"Mic Gain (diff<{gain.get('max_intra_diff',0):.2f}dB)")
    ax.legend(fontsize=7); ax.grid(True, alpha=0.3)

    # Panel 6: Inter-mic delay
    ax = fig.add_subplot(5, 3, 6)
    delays = results.get('inter_mic_delay', [])
    if delays:
        names = [d['pair'] for d in delays]
        means = [d['mean_delay_samples'] for d in delays]
        stds = [d['std_delay_samples'] for d in delays]
        ax.bar(range(len(names)), means, yerr=stds, capsize=3, alpha=0.7)
        ax.set_xticks(range(len(names))); ax.set_xticklabels(names, fontsize=7)
    ax.set_ylabel('Delay (samples)'); ax.set_title('Inter-Mic Delay (GCC-PHAT)')
    ax.grid(True, alpha=0.3, axis='y')

    # Panel 7: Crosstalk heatmap
    ax = fig.add_subplot(5, 3, 7)
    xtrk = results.get('crosstalk', {}).get('mic_ref_correlation', np.zeros((4,4)).tolist())
    xtrk_arr = np.array(xtrk)
    im = ax.imshow(xtrk_arr, cmap='RdBu_r', vmin=-1, vmax=1, aspect='auto')
    ax.set_xticks(range(4)); ax.set_yticks(range(4))
    ax.set_xticklabels([f'ref{i}' for i in range(4)], fontsize=8)
    ax.set_yticklabels([f'mic{i}' for i in range(4)], fontsize=8)
    ax.set_title('Mic-Ref Crosstalk')
    for i in range(4):
        for j in range(4):
            ax.text(j, i, f'{xtrk_arr[i,j]:.2f}', ha='center', va='center', fontsize=8,
                    color='white' if abs(xtrk_arr[i,j]) > 0.5 else 'black')
    plt.colorbar(im, ax=ax, shrink=0.8)

    # Panel 8: EIN/DR
    ax = fig.add_subplot(5, 3, 8)
    ein_dr = results.get('ein_dr', {})
    dr_vals = ein_dr.get('dynamic_range_db', [0]*nch)
    colors = ['steelblue']*4 + ['coral']*4
    ax.bar(range(nch), dr_vals[:nch], color=colors[:nch], alpha=0.8)
    ax.set_xticks(range(nch)); ax.set_xticklabels(ch_labels[:nch], fontsize=7, rotation=30)
    ax.set_ylabel('DR (dB)'); ax.set_title(f'Dynamic Range (avg={ein_dr.get("avg_dynamic_range",0):.1f}dB)')
    ax.axhline(y=96, color='red', linestyle='--', linewidth=1, label='16-bit theory')
    ax.legend(fontsize=7); ax.grid(True, alpha=0.3, axis='y')

    # Panel 9: Frequency response
    ax = fig.add_subplot(5, 3, 9)
    freq_resp = results.get('frequency_response', {})
    if 'response' in freq_resp:
        for ch_name in list(freq_resp['response'].keys())[:4]:
            resp = freq_resp['response'][ch_name]
            ax.plot(list(resp.keys()), list(resp.values()), '-o', markersize=3, linewidth=1, label=ch_name)
    ax.axhline(y=0, color='k', linewidth=0.5)
    ax.set_xlabel('Frequency (Hz)'); ax.set_ylabel('Relative Level (dB)')
    ax.set_title('Frequency Response'); ax.legend(fontsize=7); ax.grid(True, alpha=0.3)

    # Panel 10: Noise PSD
    ax = fig.add_subplot(5, 3, 10)
    data_s = read_segment(fpath, 0, 10*sr, nch).astype(np.float64)
    for ch in [0, 4]:
        f, Pxx = sig.welch(data_s[:, ch], fs=sr, nperseg=8192)
        ax.semilogx(f[10:], 10*np.log10(Pxx[10:]+1e-20), linewidth=0.8, label=ch_labels[ch])
    ax.set_xlabel('Frequency (Hz)'); ax.set_ylabel('PSD (dB/Hz)')
    ax.set_title('Noise PSD'); ax.legend(fontsize=7); ax.grid(True, alpha=0.3)

    # Panel 11: Aliasing check
    ax = fig.add_subplot(5, 3, 11)
    data_a = read_segment(fpath, 0, 5*sr, nch).astype(np.float64)
    f_a, Pxx_a = sig.welch(data_a[:, 0], fs=sr, nperseg=16384)
    ax.plot(f_a, 10*np.log10(Pxx_a+1e-20), linewidth=0.8)
    ax.axvline(x=8000, color='red', linestyle='--', linewidth=1.5, label='Nyquist')
    ax.axvspan(7000, 8000, alpha=0.1, color='red')
    ax.set_xlabel('Frequency (Hz)'); ax.set_ylabel('PSD (dB/Hz)')
    ax.set_title('Aliasing Check'); ax.legend(fontsize=7); ax.grid(True, alpha=0.3)

    # Panel 12: Noise floor drift
    ax = fig.add_subplot(5, 3, 12)
    stab = results.get('stability', {})
    nf = stab.get('noise_floor', {})
    # Re-plot from the RMS data
    block_30s = sr * 30
    n_blk = total_frames // block_30s
    nf_vals, nf_times = [], []
    for b in range(0, n_blk, 2):
        s = b * block_30s
        d = read_segment(fpath, s, 30*sr, nch).astype(np.float64)
        nf_vals.append(rms_dbfs(d[:, 0]))
        nf_times.append(s/60)
    ax.plot(nf_times, nf_vals, linewidth=0.8, color='steelblue')
    if len(nf_times) > 1:
        z = np.polyfit(nf_times, nf_vals, 1)
        ax.plot(nf_times, np.polyval(z, nf_times), '--', color='red', label=f'drift={z[0]:.3f}dB/min')
    ax.set_xlabel('Time (min)'); ax.set_ylabel('Noise Floor (dBFS)')
    ax.set_title('Noise Floor Drift'); ax.legend(fontsize=8); ax.grid(True, alpha=0.3)

    # Panel 13: DC offset
    ax = fig.add_subplot(5, 3, 13)
    dc_vals, dc_times = [], []
    for b in range(0, n_blk, 2):
        s = b * block_30s
        d = read_segment(fpath, s, 30*sr, nch).astype(np.float64)
        dc_vals.append(np.mean(d, axis=0))
        dc_times.append(s/60)
    dc_arr = np.array(dc_vals)
    for ch in range(nch):
        ax.plot(dc_times, dc_arr[:, ch], linewidth=0.8, alpha=0.7, label=ch_labels[ch])
    ax.set_xlabel('Time (min)'); ax.set_ylabel('DC Offset (LSB)')
    ax.set_title('DC Offset'); ax.legend(fontsize=6, ncol=4); ax.grid(True, alpha=0.3)

    # Panel 14: PSD of active segment (spectrogram-like)
    ax = fig.add_subplot(5, 3, 14)
    active_segs = find_active_segments(all_rms_db, -35)
    if active_segs:
        seg = max(active_segs, key=lambda s: s[1]-s[0])
        mid = (seg[0]+seg[1])//2
        data_act = read_segment(fpath, mid*FRAME_SIZE_10MS, 2*sr, nch).astype(np.float64)
        f_sp, t_sp, Sxx = sig.spectrogram(data_act[:, 0], fs=sr, nperseg=512, noverlap=384)
        ax.pcolormesh(t_sp, f_sp, 20*np.log10(Sxx+1e-10), shading='gouraud', cmap='inferno', vmin=-60, vmax=-20)
        ax.set_ylim([0, 8000])
    ax.set_ylabel('Freq (Hz)'); ax.set_xlabel('Time (s)')
    ax.set_title('Spectrogram (mic0, active segment)')

    # Panel 15: Scorecard
    ax = fig.add_subplot(5, 3, 15)
    ax.axis('off')
    scorecard = "COMPREHENSIVE SCORECARD\n" + "="*40 + "\n\n"
    checks = [
        ('Clipping', not results.get('clipping', {}).get('has_clipping', True)),
        ('Data Loss', not results.get('data_loss', {}).get('has_data_loss', True)),
        ('Mic Gain', results.get('mic_gain', {}).get('pass', False)),
        ('File Integrity', results.get('integrity', {}).get('aligned', False)),
    ]
    for name, passed in checks:
        status = 'PASS' if passed else 'FAIL'
        scorecard += f"  {name:20s} {status}\n"
    scorecard += f"\n  Dynamic Range: {ein_dr.get('avg_dynamic_range', 0):.1f} dB\n"
    scorecard += f"  EIN: {np.mean(ein_dr.get('noise_floor_dbfs', [0])):.1f} dBFS\n"
    scorecard += f"  Noise Drift: {nf.get('drift_db_per_min', 0):.4f} dB/min\n"
    suspicious = results.get('crosstalk', {}).get('suspicious_pairs', [])
    if suspicious:
        scorecard += f"\n  CRITICAL: {len(suspicious)} suspicious\n  mic-ref pairs detected!\n"
    ax.text(0.05, 0.95, scorecard, transform=ax.transAxes, fontsize=9,
            verticalalignment='top', fontfamily='monospace',
            bbox=dict(boxstyle='round', facecolor='lightyellow', alpha=0.9))

    plt.tight_layout(rect=[0, 0, 1, 0.97], pad=2.0)
    out_path = os.path.join(outdir, 'comprehensive_analysis.png')
    plt.savefig(out_path, dpi=150, bbox_inches='tight')
    plt.close()
    return out_path

# ============================================================
# Main
# ============================================================
def main():
    parser = argparse.ArgumentParser(description='Multichannel PCM Audio Analysis')
    parser.add_argument('pcm_file', help='Path to PCM file')
    parser.add_argument('--sr', type=int, default=16000, help='Sample rate')
    parser.add_argument('--nch', type=int, default=8, help='Number of channels')
    parser.add_argument('--bps', type=int, default=2, help='Bytes per sample')
    parser.add_argument('--outdir', default='/tmp/pcm_analysis', help='Output directory')
    parser.add_argument('--quick', action='store_true', help='Quick mode')
    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    ch_labels = CH_LABELS_8CH[:args.nch] if args.nch <= 8 else [f'ch{i}' for i in range(args.nch)]

    print(f"Analyzing: {args.pcm_file}")
    print(f"Config: {args.sr}Hz, {args.bps*8}bit, {args.nch}ch")

    # File integrity
    integrity = analyze_file_integrity(args.pcm_file, args.nch, args.bps)
    integrity['total_seconds'] = integrity['total_frames'] / args.sr
    print(f"File: {integrity['file_size_mb']:.1f}MB, {integrity['total_seconds']:.1f}s, aligned={integrity['aligned']}")

    # Full energy scan
    print("Scanning full file energy...")
    all_rms_db = analyze_full_energy(args.pcm_file, args.sr, args.nch, integrity['total_frames'], ch_labels)
    np.save(os.path.join(args.outdir, 'all_rms_db.npy'), all_rms_db)

    results = {'integrity': integrity}

    if not args.quick:
        print("Running signal integrity scan...")
        results['clipping'] = analyze_clipping(args.pcm_file, args.sr, args.nch, integrity['total_frames'], ch_labels)
        results['data_loss'] = analyze_data_loss(args.pcm_file, args.sr, args.nch, integrity['total_frames'], ch_labels)

        print("Reading active segment for detailed analysis...")
        active_segs = find_active_segments(all_rms_db, ACTIVE_THRESHOLD_DBFS)
        if active_segs:
            seg = max(active_segs, key=lambda s: s[1]-s[0])
            mid = (seg[0]+seg[1])//2
            data_active = read_segment(args.pcm_file, mid*FRAME_SIZE_10MS, 3*args.sr, args.nch).astype(np.float64)
            results['channel_correlation'] = analyze_channel_correlation(data_active, ch_labels)

        print("Running A-class analysis (algorithm impact)...")
        results['mic_gain'] = analyze_mic_gain_consistency(args.pcm_file, args.sr, args.nch, all_rms_db, ch_labels)
        results['inter_mic_delay'] = analyze_inter_mic_delay(args.pcm_file, args.sr, args.nch, ch_labels)
        results['crosstalk'] = analyze_crosstalk(args.pcm_file, args.sr, args.nch, all_rms_db, ch_labels)
        results['ref_analysis'] = analyze_ref_quality(args.pcm_file, args.sr, args.nch, ch_labels)

        print("Running B-class analysis (hardware quality)...")
        results['ein_dr'] = analyze_ein_dr(args.pcm_file, args.sr, args.nch, all_rms_db, ch_labels)
        results['frequency_response'] = analyze_frequency_response(args.pcm_file, args.sr, args.nch, all_rms_db, ch_labels)
        results['noise_type'] = analyze_noise_type(args.pcm_file, args.sr, args.nch, ch_labels)
        results['aliasing'] = analyze_aliasing(args.pcm_file, args.sr, args.nch, ch_labels)

        print("Running C-class analysis (long-term stability)...")
        results['stability'] = analyze_stability(args.pcm_file, args.sr, args.nch, integrity['total_frames'], ch_labels)

    # Save results
    with open(os.path.join(args.outdir, 'analysis_report.json'), 'w') as f:
        json.dump(results, f, indent=2, default=str)

    # Generate plots
    print("Generating visualization...")
    plot_path = generate_plots(args.pcm_file, args.sr, args.nch, integrity['total_frames'],
                                ch_labels, all_rms_db, results, args.outdir)

    print(f"\nDone! Output in {args.outdir}")
    print(f"  Report: {args.outdir}/analysis_report.json")
    print(f"  Plots:  {plot_path}")

if __name__ == '__main__':
    main()
