"""Publish the pipeline's SQL output to Supabase through the locked-down goaliq_write() function.

    GOALIQ_PIPELINE_SECRET=... python3 publish.py out/supabase_state.sql out/supabase_snapshots.sql

Each statement is sent on its own; the function only accepts INSERT/UPDATE on the nhl_props_* tables.
"""
import json, os, re, sys, urllib.request

URL = os.environ.get('SUPABASE_URL', 'https://lfpfdpfktvjpnjwmegyd.supabase.co').rstrip('/')
KEY = os.environ.get('SUPABASE_PUBLISHABLE_KEY', 'sb_publishable_DyoU7kki74m2sYKvBqBHUQ_QsXc5BF9')
SECRET = os.environ['GOALIQ_PIPELINE_SECRET']


def statements(text):
    """Split on ';' at end of line, ignoring anything inside $tag$...$tag$ quotes; drop comment-only lines."""
    out, buf, tag = [], [], None
    for line in text.splitlines():
        if tag is None and not buf and (not line.strip() or line.lstrip().startswith('--')):
            continue
        buf.append(line)
        for m in re.finditer(r'\$([A-Za-z_]*)\$', line):
            t = m.group(1)
            tag = None if tag == t else (t if tag is None else tag)
        if tag is None and line.rstrip().endswith(';'):
            out.append('\n'.join(buf)); buf = []
    if ''.join(buf).strip():
        out.append('\n'.join(buf))
    return out


def send(stmt):
    req = urllib.request.Request(f'{URL}/rest/v1/rpc/goaliq_write', method='POST',
                                 data=json.dumps({'secret': SECRET, 'stmt': stmt}).encode(),
                                 headers={'apikey': KEY, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read() or b'0')


if __name__ == '__main__':
    total = 0
    for f in sys.argv[1:]:
        for i, s in enumerate(statements(open(f, encoding='utf-8').read())):
            try:
                n = send(s)
            except urllib.error.HTTPError as e:
                raise SystemExit(f'{f} statement {i + 1} failed: {e.code} {e.read()[:300]!r}')
            total += 1
            print(f'{os.path.basename(f)} #{i + 1}: {n} rows', flush=True)
    print('published', total, 'statements')
