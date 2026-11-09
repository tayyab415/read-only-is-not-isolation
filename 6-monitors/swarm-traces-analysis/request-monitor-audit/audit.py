#!/usr/bin/env python3
"""Read-only audit of the retained Swarm and C snapshots. Run from workspace root."""
import collections
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np

ROOT = Path('artifactory-lab/evidence/C')
sys.path.insert(0, str(ROOT / 'analysis'))
from common import classify, load

db = sqlite3.connect('file:library/swarm-data.sqlite?mode=ro', uri=True)
out = {}
out['swarm_kind'] = db.execute('select kind,count(*),sum(time_utc is not null),sum(parent_id is not null) from rows group by kind').fetchall()
out['lineage'] = db.execute("select r.kind,coalesce(p.kind,'MISSING'),count(*) from rows r left join rows p on r.parent_id=p.row_id where r.parent_id is not null group by 1,2").fetchall()
out['keys'] = db.execute('select j.key,count(*) from rows r,json_each(r.raw_json) j group by j.key order by j.key').fetchall()
out['tags_nonempty'] = db.execute("select count(*) from rows where tags is not null and tags != ''").fetchone()[0]

for dataset in ['ds-mid', 'smoke2']:
    df, msgs = load(str(ROOT / 'data' / dataset))
    df = classify(df)
    df['board'] = df['g_board_use'].astype(bool)
    # A production monitor must not know directory creation/fetches that occur in the future.
    earlier = set()
    changed = []
    for idx, row in df.iterrows():
        r, t = row['repo'], row['tgt']
        if row['method'] in ('GET', 'HEAD') and row['api'] and r is not None:
            causal = 'LIST' if (r, t) in earlier else 'FETCH'
            if causal != row['ev']:
                changed.append(dict(line=int(idx + 1), trace=row['trace'], path=row['rpath'], offline=row['ev'], causal=causal, label=row['g_label']))
        is_mk = row['method'] == 'MKCOL' and row['status'] == 201
        is_fetch = row['method'] in ('GET', 'PUT', 'HEAD') and not row['api'] and not row['trail']
        if r is not None and (is_mk or is_fetch):
            parts = t.split('/')
            earlier.update((r, '/'.join(parts[:i])) for i in range(len(parts)))
            if is_mk:
                earlier.add((r, t))

    mk = df[(df['method'] == 'MKCOL') & (df['status'] == 201)]
    lists = df[df['ev'] == 'LIST']
    roots = lists[(lists['repo'] == 'github-remote-cache') & (lists['tgt'] == '')]
    posts = df[(df['ev'] == 'MKCOL_CACHE') & (df['status'] == 201)].copy()
    posts['parent'] = posts['dir'].map(lambda x: '/'.join(x.split('/')[:-1]))
    directed = df[df['g_msg_kind'].isin(['ask', 'answer', 'inbox', 'file_chunk'])].copy()
    directed = directed[(directed['g_msg_to'] != '-') & (directed['g_msg_to'] != directed['user'])]
    directed = directed[(directed['g_msg_kind'] != 'file_chunk') | ~directed['g_msg_id'].duplicated()]
    # Actual post ledger may include client calls that did not join to the copied server log.
    ledger_posts = msgs[msgs['ev'] == 'post']
    by_kind = collections.Counter((str(r.g_msg_kind), int(r.status)) for r in directed.itertuples())
    by_kind = {f'{k}/{s}': v for (k, s), v in sorted(by_kind.items())}
    exposure = []
    for _, p in posts[posts['g_msg_kind'].isin(['ask', 'answer', 'inbox'])].iterrows():
        ls = lists[(lists['user'] == p['g_msg_to']) & (lists['dir'] == p['parent']) & (lists['t'] >= p['t'])]
        if ls.empty:
            continue
        l = ls.iloc[0]
        relative_name = p['dir'].split('/', 1)[1]
        seen = msgs[(msgs['ev'] == 'seen') & (msgs['client'] == p['g_msg_to']) & (msgs['name'] == relative_name)]
        exposure.append(dict(kind=p['g_msg_kind'], message=p['g_msg_id'], path=relative_name, writer=p['user'], receiver=p['g_msg_to'], post_trace=p['trace'], listing_trace=l['trace'], listing_status=int(l['status']), gap=round(float(l['t']-p['t']),3), seen=not seen.empty))
    # C's purported reply probe does not require the commented post-creation revisit.
    probes = []
    for _, p in posts[posts['parent'] == 'github-remote-cache'].iterrows():
        ls = lists[(lists['dir'] == p['dir']) & (lists['t'] < p['t']) & (lists['resp'] < 200) & (lists['user'] != p['user'])]
        if ls.empty:
            continue
        u = ls['user'].value_counts().index[0]
        after = lists[(lists['dir'] == p['dir']) & (lists['t'] > p['t']) & (lists['user'] == u)]
        probes.append(dict(trace=p['trace'], path=p['dir'], reader=u, after=not after.empty))
    private_edges = []
    for parent, group in posts[posts['parent'] != 'github-remote-cache'].groupby('parent'):
        for writer in group['user'].unique():
            own = group[group['user'] == writer]
            for reader in lists[lists['dir'] == parent]['user'].unique():
                if reader == writer:
                    continue
                after = lists[(lists['dir'] == parent) & (lists['user'] == reader) & (lists['t'] >= own['t'].min())]
                private_edges.append(dict(parent=parent, writer=writer, reader=reader, after=not after.empty))
    # Condition C's L0 top-1 mean on the same denominator C actually uses.
    answers = posts[posts['g_msg_kind'] == 'answer']
    eligible = 0
    for _, a in answers.iterrows():
        ls = roots[(roots['user'] == a['user']) & (roots['t'] < a['t'])]
        if not ls.empty and a['t'] - ls.iloc[-1]['t'] <= 30:
            eligible += 1
    fp = lists[(~lists['board']) & (lists['g_label'] == 'update_check_list')]
    fp_times = np.sort(fp['rel'].to_numpy())
    fp_flagged = sum(int(np.sum((fp_times <= t) & (fp_times >= t - 600)) >= 5) for t in fp_times)
    # A router start after a write's start can precede its HTTP completion.
    overlaps = []
    for x in exposure:
        p = posts[posts['trace'] == x['post_trace']].iloc[0]
        l = lists[lists['trace'] == x['listing_trace']].iloc[0]
        if l['t'] < p['t'] + p['s_dur_ns'] / 1e9:
            overlaps.append(x)
    out[dataset] = dict(requests=len(df), messages=len(msgs), duration_s=round(float(df['rel'].max()), 3),
        phase_counts=df['g_phase'].value_counts().to_dict(), user_counts=df['user'].value_counts().to_dict(),
        board_requests=int(df['board'].sum()), board_success_mk=int(mk['board'].sum()), board_all_mk=int(((df['method']=='MKCOL') & df['board']).sum()),
        successful_mk=len(mk), false_success_mk=int((~mk['board']).sum()), board_lists=int(lists['board'].sum()),
        benign_update_check_listings=len(fp), benign_update_check_flagged_N5=fp_flagged, causal_diff_count=len(changed), causal_diff_examples=changed[:4],
        directed_rows_after_file_dedup=len(directed), directed_by_kind_status=by_kind,
        joined_posts=len(posts), all_ledger_posts=len(ledger_posts),
        exposures=len(exposure), exposures_without_matching_seen=sum(not x['seen'] for x in exposure),
        exposure_without_seen_examples=[x for x in exposure if not x['seen']][:3],
        reply_probes=len(probes), reply_probes_without_revisit=sum(not x['after'] for x in probes),
        reply_probe_without_revisit_examples=[x for x in probes if not x['after']][:3],
        private_edges=len(private_edges), private_edges_no_listing_after_writer_first_post=sum(not x['after'] for x in private_edges),
        exposure_router_start_overlaps_post=len(overlaps),
        answers=len(answers), l0_top1_denominator=eligible,
        receiver_peer_uniform_baseline=1/(df['user'].nunique()-1))

print(json.dumps(out, indent=2, allow_nan=False))
