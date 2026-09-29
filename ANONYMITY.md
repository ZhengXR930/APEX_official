# Public-release checklist

Run these checks from the repository root before publishing:

```bash
git log --format=fuller
rg -n -i '(/Users/|/home/|author|affiliation|university|company)' . \
  --glob '!ANONYMITY.md' --glob '!.git/**'
rg -n '(api[_-]?key|token|secret)\\s*[:=]\\s*[^$< ]+' . \
  --glob '!.env.example' --glob '!.git/**'
git ls-files | rg -i '(results?|outputs?|runs?|logs?|traces?|contracts?)/|\\.jsonl$'
```

Expected public state:

- no identifying author metadata or machine-specific paths;
- no credentials or private endpoints;
- no frozen TaskContracts or Contract caches;
- no benchmark payloads, baseline code, evaluation outputs, traces, logs,
  checkpoints, or result-merging utilities;
- only the APEX implementation, a minimal example, and core tests.
