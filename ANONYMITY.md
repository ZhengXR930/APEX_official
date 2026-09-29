# Public-release checklist

Run these checks from the repository root before publishing:

```bash
git log --format=fuller
rg -n -i '(/Users/|/home/|author|affiliation|university|company)' \
  src baseline benchmark/adapter benchmark/registry/*.py scripts examples tests \
  --glob '!.git/**'
rg -n '(api[_-]?key|token|secret)\\s*[:=]\\s*[^$< ]+' . \
  --glob '!.env.example' --glob '!.git/**'
git ls-files | rg -i '(results?|outputs?|runs?|logs?|traces?)/|\\.jsonl$'
```

Expected public state:

- no identifying author metadata or machine-specific paths;
- no credentials or private endpoints;
- no generated or frozen TaskContracts;
- benchmark inputs, adapters, trusted registries, baselines, and evaluation
  utilities are present;
- experiment outputs, traces, logs, checkpoints, caches, and run directories
  are absent.
