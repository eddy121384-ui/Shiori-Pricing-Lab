# Local Runbook

## 1. Create a virtual environment

```bash
python -m venv .venv
```

Activate it on Windows PowerShell:

```bash
.venv\Scripts\Activate.ps1
```

Activate it on macOS/Linux:

```bash
source .venv/bin/activate
```

## 2. Install dependencies

```bash
pip install -r requirements.txt
pip install -e .
```

## 3. Run tests

```bash
pytest
```

## 4. Run the local Streamlit prototype

```bash
streamlit run src/shiori_pricing_lab/app/streamlit_app.py
```

## 5. Development notes

- Use synthetic sample data first.
- Do not commit real market data files.
- Keep Bloomberg integration out of v0.1 unless explicitly requested.
- If a calculation affects pricing or risk, add a test.
