#!/usr/bin/env python3
"""
监控 start.py (bge-m3 embedding 服务) 的高 CPU 周期性启动
记录触发频率、持续时间、PID 变化，输出到日志文件
"""

import psutil
import time
import json
from datetime import datetime
from pathlib import Path

LOG_FILE = Path("/Users/duguke/.openclaw/workspace/logs/embedding_cpu_monitor.jsonl")
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

CPU_THRESHOLD = 50.0  # CPU% 超过此值视为"高负载"
CHECK_INTERVAL = 10   # 秒
MIN_DURATION = 30     # 秒，持续高负载超过此值才记录为一次事件

def find_embedding_processes():
    """找到所有 start.py 进程"""
    procs = []
    for p in psutil.process_iter(['pid', 'name', 'cmdline', 'cpu_percent', 'memory_info', 'create_time']):
        try:
            cmdline = p.info.get('cmdline') or []
            if any('start.py' in c for c in cmdline):
                procs.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return procs

def log_event(event):
    """写入 JSONL 日志"""
    with open(LOG_FILE, 'a') as f:
        f.write(json.dumps(event, ensure_ascii=False) + '\n')

def main():
    print(f"🔍 启动监控: {LOG_FILE}")
    print(f"   阈值: CPU > {CPU_THRESHOLD}%")
    print(f"   检查间隔: {CHECK_INTERVAL}s")
    print(f"   最小持续: {MIN_DURATION}s")
    print("   Ctrl+C 停止\n")

    # 状态跟踪: pid -> {start_time, peak_cpu, samples}
    active_high = {}
    last_pids = set()

    try:
        while True:
            now = time.time()
            now_str = datetime.now().isoformat()
            procs = find_embedding_processes()
            current_pids = set()

            for p in procs:
                pid = p.pid
                current_pids.add(pid)
                try:
                    cpu = p.cpu_percent(interval=0.1)
                    mem_mb = p.memory_info().rss / 1024 / 1024
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

                if cpu > CPU_THRESHOLD:
                    # 高负载
                    if pid not in active_high:
                        active_high[pid] = {
                            'start': now,
                            'peak_cpu': cpu,
                            'samples': 1,
                            'cmdline': ' '.join(p.cmdline()),
                        }
                    else:
                        active_high[pid]['peak_cpu'] = max(active_high[pid]['peak_cpu'], cpu)
                        active_high[pid]['samples'] += 1
                else:
                    # 低负载，检查是否刚结束一个高负载事件
                    if pid in active_high:
                        duration = now - active_high[pid]['start']
                        if duration >= MIN_DURATION:
                            event = {
                                'type': 'high_cpu_event_end',
                                'timestamp': now_str,
                                'pid': pid,
                                'duration_sec': round(duration, 1),
                                'peak_cpu': round(active_high[pid]['peak_cpu'], 1),
                                'samples': active_high[pid]['samples'],
                                'cmdline': active_high[pid]['cmdline'],
                                'memory_mb': round(mem_mb, 1),
                            }
                            log_event(event)
                            print(f"📝 [{now_str}] PID {pid} 高负载结束: 耗时 {duration:.0f}s, 峰值 CPU {active_high[pid]['peak_cpu']:.0f}%, 内存 {mem_mb:.0f}MB")
                        del active_high[pid]

            # 检测新进程启动
            new_pids = current_pids - last_pids
            for pid in new_pids:
                try:
                    p = psutil.Process(pid)
                    event = {
                        'type': 'process_start',
                        'timestamp': now_str,
                        'pid': pid,
                        'cmdline': ' '.join(p.cmdline()),
                        'create_time': datetime.fromtimestamp(p.create_time()).isoformat(),
                    }
                    log_event(event)
                    print(f"🆕 [{now_str}] 新进程启动: PID {pid}")
                except:
                    pass

            # 检测进程退出
            exited_pids = last_pids - current_pids
            for pid in exited_pids:
                event = {
                    'type': 'process_exit',
                    'timestamp': now_str,
                    'pid': pid,
                }
                log_event(event)
                print(f"💀 [{now_str}] 进程退出: PID {pid}")

            last_pids = current_pids

            # 定期心跳
            if int(now) % 60 == 0:
                high_count = len(active_high)
                print(f"💓 [{now_str}] 心跳: 当前进程 {len(current_pids)} 个, 高负载 {high_count} 个")

            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        print("\n🛑 监控停止")
        # 记录当前进行中的高负载事件
        for pid, info in active_high.items():
            duration = time.time() - info['start']
            if duration >= MIN_DURATION:
                event = {
                    'type': 'high_cpu_event_interrupted',
                    'timestamp': datetime.now().isoformat(),
                    'pid': pid,
                    'duration_sec': round(duration, 1),
                    'peak_cpu': round(info['peak_cpu'], 1),
                    'samples': info['samples'],
                }
                log_event(event)

if __name__ == '__main__':
    main()