# Anonymous-release checklist

Run these checks from the repository root before packaging:

```bash
git log --format=fuller
rg -n -i '(/Users/|/home/|author|affiliation|university|company)' . \
  --glob '!ANONYMITY.md' --glob '!.git/**'
rg -n '(api[_-]?key|token|secret)\s*[:=]\s*[^$< ]+' . \
  --glob '!.env.example' --glob '!.git/**'
```

Expected state for the submission repository:

- no imported commit history or identifying author metadata;
- local Git identity set to neutral placeholder values;
- no personal names, usernames, e-mail addresses, affiliations, or absolute
  machine paths;
- no API keys, endpoint credentials, cached outputs, or experiment logs;
- benchmark payloads contain no local machine paths or research credentials;
- only trusted capability manifests retained in `benchmark/registry/data/`.
