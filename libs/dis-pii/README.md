# `libs/dis-pii/`

Tokenization and PII handling. HMAC-based deterministic tokens, per-tenant keys, key rotation hooks.

```
libs/dis-pii/
├── pyproject.toml
├── README.md
├── src/
│   └── dis_pii/
│       ├── __init__.py
│       ├── tokenizer.py        # HMAC token generation
│       ├── key_vault.py        # per-tenant key lookup + rotation
│       ├── detectors.py        # field-name and pattern-based PII detection
│       └── policy.py           # what to tokenize per source type
└── tests/
    └── unit/
```

**Why this lib exists.** PII tokenization happens at the receiver (v0.3 G1). The actual tokenizer logic, key handling, and detection rules need to live somewhere all four receivers can import. Putting it in any one service is wrong.

---
