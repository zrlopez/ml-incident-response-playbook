import sys
import re

src = '/Users/zrl/Documents/GitHub/ml-incident-response-playbook/infrastructure/k8s-resilience.yaml'  # noqa: E501
out = '/Users/zrl/Documents/GitHub/ml-incident-response-playbook/infrastructure/k8s-resilience.yaml'  # noqa: E501

with open(src, 'rb') as f:
    raw = f.read()

needle = b'\x0ameta\x0a'
repl = b'\x0ameta\x0a'

before = raw.count(needle)
fixed = raw.replace(needle, repl)
after = fixed.count(needle)
meta_count = len(re.findall(b'meta', fixed))

print(f'before={before} after={after} metadata_count={meta_count} size_diff={len(fixed)-len(raw)}', file=sys.stderr)  # noqa: E501

with open(out, 'wb') as f:
    f.write(fixed)

print('WRITE_OK', file=sys.stderr)
