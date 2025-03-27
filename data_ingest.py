import yaml
from pathlib import Path
from typing import List, Union
from taxonomy import LeafNode, SkillLeafNode, KnowledgeLeafNode


def _read_qna_file(path: Path):
    with open(path, encoding="utf-8") as f:
        contents = yaml.safe_load(f)
    
    task_description = contents.get("task_description")
    document_outline = contents.get("document_outline")
    domain = contents.get("domain")
    
    seed_data = []

    # TODO handle inline documents + document repo once taxonomy ingestion implemented

    for seed_example in contents.get("seed_examples"):
        assert "questions_and_answers" in seed_example
        seed_data.append(
            {
                "questions_and_answers": seed_example.get("questions_and_answers"),
                "context": seed_example.get("context", ""),
            }
        )
    
    return (
        task_description,
        document_outline,
        domain,
        seed_data,
    )


def ingest_taxonomy(
    repo_path: str | Path,
    base: str = "origin/main"
) -> List[LeafNode]:
    # TODO
    ...


def ingest_skill_qna_file(filepath: Path) -> SkillLeafNode:
    # TODO
    ...


def ingest_knowledge_directory(dirpath: Union[Path, str]) -> KnowledgeLeafNode:
    """Ingest a directory containing a qna.yaml and relevant documents.

    Args:
        dirpath (Path): The path to a directory containing a `qna.yaml`
            and the relevant documents.

    Returns:
        KnowledgeLeafNode: The processed knowledge node.
    """
    dirpath = Path(dirpath).expanduser()
    files = {f for f in dirpath.iterdir() if f.is_file()}

    qna_file = dirpath / "qna.yaml"
    if qna_file not in files:
        raise ValueError("Expected 'qna.yaml' in knowledge directory.")
    
    documents = list(files - {qna_file})

    _, document_outline, domain, seed_data = _read_qna_file(qna_file)

    return KnowledgeLeafNode(
        path=qna_file,  # or dirpath?
        documents=documents,
        document_outline=document_outline,
        domain=domain,
        seed_data=seed_data,
    )
