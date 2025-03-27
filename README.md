# Data Ingestion for InstructLab

A minimal SDK implementation for InstructLab SDG's data ingestion workflow.

How it differs from the full-scale version:
- Exposes the different components as modular, configurable APIs
    - Allows for interacting with results in between steps
    - Also enables different flows as demonstrated in the example notebooks TODO
- *Does not* support the upstream taxonomy-based workflow
- *Does* expose a simpler alternative (without document versioning) outlined below

## Directory Setup

```
sample_knowledge_dir
├── qna.yaml
├── sample_document1.pdf
├── sample_document2.pdf
└── sample_document3.pdf
```

Write your `qna.yaml` file as normal, (you can ignore the `document` section), and include
one or more reference documents in the directory alongside it.

## Work still needed:
- [ ] Support skills qna files
- [ ] Implement taxonomy workflow to be able to support existing setups
- [ ] Write notebooks showcasing different usage patterns
- [ ] Trim down chunking.py to more minimal functionality
