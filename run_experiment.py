#!/usr/bin/env python3
"""
run_experiments.py -- Person C: automated experiments
"""

import argparse
import csv
import glob
import importlib.util
import os
import statistics
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

import network_config as cfg

MODES = ['static', 'priority', 'dynamic']

SCENARIOS = {
    'baseline': (40, 0.05),
    'congestion': (100, 0.05),
    'linkfail': (60, 0.2),
    'loss': (100, 0.05)
}

CLIENTS = ['h2', 'h3', 'h4']

FAIL_DOWN_AT = 3.0
FAIL_UP_AT = 15.0
LOSS_PCT = 10


def read(path):
    try:
        with open(path, newline='') as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def analyze(rundir, window=None):
    rows = read(os.path.join(rundir, 'server_log.csv'))

    sent = len(rows)

    ack = [r for r in rows if r['status'] == 'ACKED']

    retried = [r for r in ack if int(r['attempts']) > 1]

    lat = sorted(
        float(r['latency_ms'])
        for f in glob.glob(os.path.join(rundir, 'client_log_*.csv'))
        for r in read(f)
    )

    pct = lambda n: round(100.0 * n / sent, 2) if sent else 0.0

    out = {
        'sent': sent,
        'acked': len(ack),
        'retransmitted': len(retried),
        'unreachable': sent - len(ack),
        'loss_pct': pct(sent - len(ack)),
        'first_try_loss_pct': pct(len(retried)),
        'lat_avg': '',
        'lat_median': '',
        'lat_p95': '',
        'lat_max': '',
        'outage_loss_pct': ''
    }

    if window:
        inside = [
            r for r in rows
            if window[0] <= float(r['send_time']) <= window[1]
        ]

        out['outage_loss_pct'] = (
            round(
                100.0 * sum(r['status'] != 'ACKED' for r in inside) / len(inside),
                2
            ) if inside else ''
        )

    if lat:
        out.update(
            lat_avg=round(statistics.mean(lat), 2),
            lat_median=round(statistics.median(lat), 2),
            lat_p95=round(lat[max(0, int(len(lat) * .95) - 1)], 2),
            lat_max=round(lat[-1], 2)
        )

    return out


def load_topo(path):
    spec = importlib.util.spec_from_file_location('topo_mod', path)

    mod = importlib.util.module_from_spec(spec)

    spec.loader.exec_module(mod)

    return mod


def wait_rows(rundir, expected, timeout):

    t0 = time.time()

    last = -1

    still = time.time()

    while time.time() - t0 < timeout:

        n = len(read(os.path.join(rundir, 'server_log.csv')))

        if n >= expected:
            return

        if n != last:
            last = n
            still = time.time()

        elif time.time() - still > 6:
            return

        time.sleep(0.5)


def run_one(topo, mode, scenario, n_alerts, gap, rep, outdir):

    rundir = os.path.join(
        outdir,
        f'{mode}_{scenario}_rep{rep}'
    )

    os.makedirs(rundir, exist_ok=True)

    for f in glob.glob(os.path.join(rundir, '*.csv')):
        os.remove(f)

    print(
        f'\n=== {mode} / {scenario} / rep {rep} ({n_alerts} alerts) ===',
        flush=True
    )

    window = None

    net = topo.make_network()

    try:

        h = {
            n: net.get(n)
            for n in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6')
        }

        h['h1'].cmd(
            f'python3 {ROOT}/src/ctl_send.py mode {mode}'
        )

        time.sleep(1)

        h['h1'].popen(
            f'sleep 100000 | python3 {ROOT}/server.py',
            shell=True,
            cwd=rundir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        time.sleep(1)

        for c in CLIENTS:

            h[c].popen(
                f'python3 {ROOT}/client.py 10.0.0.1',
                shell=True,
                cwd=rundir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

        time.sleep(2)

        if scenario == 'loss':

            h['h1'].cmd(
                f'tc qdisc replace dev h1-eth0 root netem loss {LOSS_PCT}%'
            )

        if scenario in ('congestion', 'linkfail'):

            h['h6'].popen(
                'iperf -s -u',
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

            time.sleep(1)

            h['h5'].popen(
                'iperf -u -c 10.0.0.6 -b 20M -t 120',
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

            time.sleep(4)

        h['h1'].cmd(
            f'python3 {ROOT}/bulk.py {n_alerts} {gap}'
        )

        if scenario == 'linkfail':

            time.sleep(FAIL_DOWN_AT)

            window_start = time.time()

            print('link s1-s3 DOWN', flush=True)

            net.configLinkStatus('s1', 's3', 'down')

            time.sleep(FAIL_UP_AT - FAIL_DOWN_AT)

            window = (window_start, time.time())

            print('link s1-s3 UP', flush=True)

            net.configLinkStatus('s1', 's3', 'up')

        wait_rows(
            rundir,
            n_alerts * len(CLIENTS),
            timeout=n_alerts * (gap + 0.5) + 30
        )

    finally:

        for pat in (
            'server.py',
            'client.py',
            'iperf',
            'sleep 100000'
        ):

            subprocess.run(
                ['pkill', '-f', pat],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

        topo.stop_network(net)

    res = analyze(rundir, window)

    print(
        {
            k: res[k]
            for k in (
                'sent',
                'acked',
                'loss_pct',
                'lat_avg',
                'lat_p95',
                'outage_loss_pct'
            )
        },
        flush=True
    )

    return res


def report(rows, outdir):

    cols = [
        'mode',
        'scenario',
        'rep',
        'sent',
        'acked',
        'retransmitted',
        'unreachable',
        'loss_pct',
        'first_try_loss_pct',
        'outage_loss_pct',
        'lat_avg',
        'lat_median',
        'lat_p95',
        'lat_max'
    ]

    with open(
        os.path.join(outdir, 'summary.csv'),
        'w',
        newline=''
    ) as f:

        w = csv.DictWriter(f, fieldnames=cols)

        w.writeheader()

        w.writerows(rows)

    def avg(sel, key):

        v = [
            float(r[key])
            for r in sel
            if r[key] != ''
        ]

        return round(statistics.mean(v), 2) if v else None

    lines = [
        '| scenario | mode | loss % (after retries) | first-try loss % | loss % during outage | avg latency ms | p95 ms |',
        '|---|---|---|---|---|---|---|'
    ]

    table = {}

    for sc in SCENARIOS:

        for m in MODES:

            sel = [
                r for r in rows
                if r['scenario'] == sc and r['mode'] == m
            ]

            if not sel:
                continue

            t = {
                k: avg(sel, k)
                for k in (
                    'loss_pct',
                    'first_try_loss_pct',
                    'outage_loss_pct',
                    'lat_avg',
                    'lat_p95'
                )
            }

            table[(sc, m)] = t

            lines.append(
                f"| {sc} | {m} | {t['loss_pct']} | "
                f"{t['first_try_loss_pct']} | "
                f"{'-' if t['outage_loss_pct'] is None else t['outage_loss_pct']} | "
                f"{t['lat_avg']} | {t['lat_p95']} |"
            )

    with open(
        os.path.join(outdir, 'summary.md'),
        'w'
    ) as f:

        f.write('\n'.join(lines) + '\n')

    print('\n' + '\n'.join(lines))

    try:

        import matplotlib

        matplotlib.use('Agg')

        import matplotlib.pyplot as plt

        scs = sorted({sc for sc, _ in table})

        for key, title, fn in (
            ('loss_pct', 'Alert loss after retries (%)', 'loss.png'),
            ('lat_avg', 'Average alert latency (ms)', 'latency.png')
        ):

            fig, ax = plt.subplots(figsize=(7, 4))

            wdt = 0.25

            for i, m in enumerate(MODES):

                ax.bar(
                    [j + i * wdt for j in range(len(scs))],
                    [
                        (table.get((sc, m), {}).get(key) or 0)
                        for sc in scs
                    ],
                    wdt,
                    label=m
                )

            ax.set_xticks(
                [j + wdt for j in range(len(scs))]
            )

            ax.set_xticklabels(scs)

            ax.set_title(title)

            ax.legend()

            fig.tight_layout()

            fig.savefig(
                os.path.join(outdir, fn),
                dpi=140
            )

            plt.close(fig)

    except Exception as e:

        print('(plots skipped:', e, ')')


def main():

    ap = argparse.ArgumentParser()

    ap.add_argument(
        '--quick',
        action='store_true',
        help='1 rep, congestion only, 20 alerts'
    )

    ap.add_argument(
        '--only',
        choices=list(SCENARIOS),
        help='run just this scenario'
    )

    ap.add_argument(
        '--reps',
        type=int,
        default=None,
        help='repetitions'
    )

    ap.add_argument(
        '--topo',
        default=os.path.join(ROOT, 'topo', 'emergency_topo.py')
    )

    ap.add_argument(
        '--out',
        default=os.path.join(ROOT, 'results')
    )

    a = ap.parse_args()

    if os.geteuid() != 0:
        sys.exit('run with sudo')

    if not os.path.exists(a.topo):

        a.topo = os.path.join(
            ROOT,
            'topo',
            'b_test_topo.py'
        )

    print('topology:', a.topo)

    topo = load_topo(a.topo)

    if not hasattr(topo, 'make_network'):
        sys.exit(
            'topology file must define make_network() and stop_network(net)'
        )

    outdir = os.path.join(
        a.out,
        'quick' if a.quick else a.only if a.only else 'full'
    )

    os.makedirs(outdir, exist_ok=True)

    plan = (
        {'congestion': (20, 0.05)}
        if a.quick
        else {a.only: SCENARIOS[a.only]}
        if a.only
        else SCENARIOS
    )

    reps = a.reps or (
        1 if a.quick or a.only else 3
    )

    rows = []

    for rep in range(1, reps + 1):

        for sc, (n, gap) in plan.items():

            for m in MODES:

                res = run_one(
                    topo,
                    m,
                    sc,
                    n,
                    gap,
                    rep,
                    outdir
                )

                rows.append({
                    'mode': m,
                    'scenario': sc,
                    'rep': rep,
                    **res
                })

                report(rows, outdir)

    print('\nDone. Results in', outdir)


if __name__ == '__main__':
    main()
