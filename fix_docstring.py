p = r"C:/Users/EDDY/Documents/GitHub/Shiori-Pricing-Lab/tests/test_treasury_futures_workbench_routes.py"
with open(p, "r", encoding="utf-8") as f:
    lines = f.readlines()

new_docstring = [
    '    """Codex review, PR #191 (P2).\n',
    '\n',
    '    `rstrip("%")` removed a whole run of percent signs, so `4.20%%` was priced\n',
    '    as 4.20% -- an unreadable input answered with an apparently valid futures\n',
    "    price, which is the silent-wrong-answer this route's fail-visible contract\n",
    '    exists to prevent. One optional suffix is stripped, not a run.\n',
    '    """\n',
]

lines[368:375] = new_docstring

with open(p, "w", encoding="utf-8") as f:
    f.writelines(lines)

import subprocess
result = subprocess.run(["python3", "-c", f"with open(r'{p}', 'r') as f: compile(f.read(), 'test', 'exec'); print('OK')"], capture_output=True, text=True)
print(result.stdout)
print(result.stderr)