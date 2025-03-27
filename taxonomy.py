from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class LeafNode:
    path: Path


@dataclass
class SkillLeafNode(LeafNode):
    def to_samples(self):
        ...


class KnowledgeLeafNode(LeafNode):
    def __init__(
        self,
        path,
        documents,
        document_outline,
        domain,
        seed_data
    ):
        super().__init__(path)
        self.documents = documents
        self.document_outline = document_outline
        self.domain = domain
        self.seed_data = self._validate_seed_data(seed_data)
    
    def _validate_seed_data(self, seed_data):
        for icl in seed_data:
            assert isinstance(icl["context"], str)
            for qna in icl["questions_and_answers"]:
                for k in ("question", "answer"):
                    assert qna[k]
                    assert isinstance(qna[k], str)
        return seed_data

    def to_samples(self, document_chunks):
        chunked_dataset = []

        for chunk in document_chunks:
            for icl in self.seed_data:
                record = {
                    "document": chunk,
                    "icl_document": icl["context"],
                    "document_outline": self.document_outline,
                    "domain": self.domain,
                    "leaf_node_type": "knowledge",
                    "leaf_node_path": str(self.path),
                }

                for i, qna in enumerate(icl["questions_and_answers"]):
                    record.update(
                        {
                            f"icl_query_{i+1}": qna["question"],
                            f"icl_response_{i+1}": qna["answer"],
                        }
                    )

                chunked_dataset.append(record)

        return chunked_dataset
        
