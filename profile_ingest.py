"""Profile ingest bottlenecks for large show-tech files."""

import argparse
import time
import re
import sys
from pathlib import Path

parser = argparse.ArgumentParser(description="Profile show-tech ingest bottlenecks")
parser.add_argument("file", help="Path to show-tech file (.txt, .tgz, etc.)")
args = parser.parse_args()

path = Path(args.file)
if not path.exists():
    print(f"File not found: {args.file}")
    sys.exit(1)

print(f"File: {path.name}  size: {path.stat().st_size / 1e6:.1f} MB")

t0 = time.perf_counter()
raw = path.read_bytes()
t1 = time.perf_counter()
print(f"read_bytes:  {t1 - t0:.2f}s")

text = raw.decode("utf-8", errors="replace")
del raw
t2 = time.perf_counter()
print(f"decode utf8: {t2 - t1:.2f}s")

lines = text.splitlines()
t3 = time.perf_counter()
print(f"splitlines:  {t3 - t2:.2f}s  -> {len(lines):,} lines")

joined = "\n".join(lines)
t4 = time.perf_counter()
print(f"join back:   {t4 - t3:.2f}s  ({len(joined) / 1e6:.0f} MB)")

NXOS_PAT = re.compile(r"(?m)^`show\s+[^`\n]+`")
m_nxos = list(NXOS_PAT.finditer(joined))
t5 = time.perf_counter()
print(f"NXOS regex:  {t5 - t4:.2f}s  -> {len(m_nxos)} headers")

IOS_PAT = re.compile(r"(?m)^(?:-{3,}\s*show\s+\S[^\n]*\s*-{3,}|show\s+\S[^\n]*)")
m_ios = list(IOS_PAT.finditer(joined))
t6 = time.perf_counter()
print(f"IOS  regex:  {t6 - t5:.2f}s  -> {len(m_ios)} headers")

if m_nxos:
    print("\nFirst 5 NX-OS headers:")
    for m in m_nxos[:5]:
        print(" ", repr(m.group(0)[:80]))
elif m_ios:
    print("\nFirst 5 IOS headers:")
    for m in m_ios[:5]:
        print(" ", repr(m.group(0)[:80]))
else:
    print("\nFirst 5 raw lines:")
    for l in lines[:5]:
        print(" ", repr(l[:120]))

# Simulate chunk_text write volume
avg_chunk_lines = 200
n_chunks = len(lines) // avg_chunk_lines
avg_body_kb = (len(joined) / n_chunks / 1024) if n_chunks else 0
print(f"\nEstimated chunks (at {avg_chunk_lines} lines avg): {n_chunks}")
print(f"Avg chunk body: {avg_body_kb:.0f} KB")
print(f"Total chunk_text write: {len(joined) / 1e6:.0f} MB")
