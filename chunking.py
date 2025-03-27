# Standard
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional
import json
import logging
import os
import re
import sys

# Third Party
from datasets import Dataset
from docling.datamodel.base_models import InputFormat
from docling.datamodel.document import ConversionResult
from docling.datamodel.pipeline_options import (
    AcceleratorDevice,
    AcceleratorOptions,
    EasyOcrOptions,
    OcrOptions,
    PdfPipelineOptions,
    TesseractOcrOptions,
)
from langchain_text_splitters import Language, RecursiveCharacterTextSplitter

# First Party
from model_formats import is_model_gguf, is_model_safetensors

logger = logging.getLogger(__name__)
_DEFAULT_CHUNK_OVERLAP = 100
SUPPORTED_FILETYPES = [".pdf", ".md"]


def _num_tokens_from_words(num_words) -> int:
    return int(num_words * 1.3)  # 1 word ~ 1.3 token


def _num_chars_from_tokens(num_tokens) -> int:
    return int(num_tokens * 4)  # 1 token ~ 4 English character


def resolve_ocr_options(
    docling_model_path: Optional[Path] = None,
) -> Optional[OcrOptions]:
    # Declare ocr_options explicitly as Optional[OcrOptions]
    ocr_options: Optional[OcrOptions] = None

    # First, attempt to use tesserocr
    try:
        ocr_options = TesseractOcrOptions()
        # pylint: disable=import-outside-toplevel
        # Third Party
        from docling.models.tesseract_ocr_model import TesseractOcrModel

        _ = TesseractOcrModel(
            enabled=True,
            artifacts_path=docling_model_path,
            options=ocr_options,
            accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU),
        )
        return ocr_options
    except ImportError:
        # No tesserocr, so try something else
        logger.warning("Tesseract not found, falling back to EasyOCR.")

    try:
        ocr_options = EasyOcrOptions(
            lang=["en"],
            use_gpu=None,
            confidence_threshold=0.5,
            model_storage_directory=str(docling_model_path),
            recog_network="standard",
            download_enabled=True,
        )
        # triggers torch loading, import lazily
        # pylint: disable=import-outside-toplevel
        # Third Party
        from docling.models.easyocr_model import EasyOcrModel

        _ = EasyOcrModel(
            enabled=True,
            artifacts_path=None,
            options=ocr_options,
            accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU),
        )
        return ocr_options
    except ImportError:
        # no easyocr either, so don't use any OCR
        logger.error(
            "Failed to load Tesseract and EasyOCR - disabling optical character recognition in PDF documents"
        )
        return None


def split_docs_by_filetype(document_paths: List[Path]) -> Dict[str, List[Path]]:
    """Split document paths into a dict of lists based on their file extension."""
    document_dict = defaultdict(list)
    for path in document_paths:
        filetype = path.suffix
        if filetype not in SUPPORTED_FILETYPES:
            raise ValueError(f"Provided unsupported filetype {filetype}")

        document_dict[filetype].append(path)

    return dict(document_dict)


class DocumentChunker:  # pylint: disable=too-many-instance-attributes
    def __init__(
        self,
        document_paths: List[Path],
        tokenizer_model_name: str | Path,
        docling_model_path: Optional[Path] = None,
        server_ctx_size: int = 4096,
        chunk_word_count: int = 1024,
    ):
        if not document_paths:
            raise ValueError("Provided empty list of documents")

        document_dict = split_docs_by_filetype(document_paths)

        if len(document_dict) > 1:
            raise ValueError("Provided multiple document types")

        # We know there is only 1 key, value pair, so we take the first
        self.document_filetype, self.document_paths = next(iter(document_dict.items()))
        self.docling_model_path = docling_model_path
        self.converter = self._init_docling_converter()

        self.server_ctx_size = server_ctx_size
        self.chunk_word_count = chunk_word_count
        self.tokenizer = self.create_tokenizer(tokenizer_model_name)

    def _init_docling_converter(self):
        """Initialize docling converter with filetype-specific configurations"""
        # triggers torch loading, import lazily
        # pylint: disable=import-outside-toplevel
        # Third Party
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline

        if self.docling_model_path is None:
            logger.info("Docling models not found on disk, downloading models...")
            self.docling_model_path = StandardPdfPipeline.download_models_hf()
        else:
            logger.info("Found the docling models")

        pipeline_options = PdfPipelineOptions(
            artifacts_path=self.docling_model_path,
            do_ocr=False,
        )

        # deactivate MPS acceleration on Github CI
        if os.getenv("CI") and sys.platform == "darwin":
            pipeline_options.accelerator_options = AcceleratorOptions(
                device=AcceleratorDevice.CPU
            )
        ocr_options = resolve_ocr_options(docling_model_path=self.docling_model_path)
        if ocr_options is not None:
            pipeline_options.do_ocr = True
            pipeline_options.ocr_options = ocr_options

        return DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )

    def chunk_documents(self) -> List:
        """Split a list of documents into chunks

        Returns:
            List: a list of chunks from the documents
        """
        # Move docling_core import inside method where it's used to avoid importing transformers at top level
        # pylint: disable=import-outside-toplevel
        # Third Party
        from docling_core.transforms.chunker.hybrid_chunker import HybridChunker

        parsed_documents = self.converter.convert_all(self.document_paths)
        all_chunks = []
        for conversion_result in parsed_documents:
            doc = conversion_result.document
            chunker = HybridChunker(tokenizer=self.tokenizer, max_tokens=500)
            try:
                chunk_iter = chunker.chunk(dl_doc=doc)
                chunks = [chunker.serialize(chunk=chunk) for chunk in chunk_iter]
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.error(
                    f"Error chunking document {conversion_result.input.file}: {e}"
                )
                chunks = []

            fused_texts = self.fuse_texts(chunks, 200)
            num_tokens_per_doc = _num_tokens_from_words(self.chunk_word_count)
            chunk_size = _num_chars_from_tokens(num_tokens_per_doc)
            final_chunks = chunk_markdowns(fused_texts, chunk_size)
            all_chunks.extend(final_chunks)

        return all_chunks

    def _path_validator(self, path) -> Path:
        """
        Validate the path and return a Path object.
        Args:
            path (str): Path to be validated.
        Returns:
            Path: Path object.
        """
        if isinstance(path, str):
            path = Path(path)
            if not path.exists():
                raise FileNotFoundError(f"{path} does not exist.")
        return path

    def fuse_texts(
        self, text_list: List, short_length_threshold: int = 130
    ) -> List[str]:
        """
        Fuse short texts with preceding longer texts if their token count is below the threshold.
        Args:
            text_list (list): List of text chunks to process.
            short_length_threshold (int): The token count threshold for determining short texts.
                                      Default is 130, tuned specifically for the Mixtral tokenizer.
                                      Update this value if changing the tokenizer model.
        Returns:
            list: List of fused texts.
        """
        fused_texts: List[str] = []
        previous_long_text = ""

        for text in text_list:
            token_count = self.get_token_count(
                text, self.tokenizer
            )  # Use tokenizer for token count

            if token_count <= short_length_threshold and previous_long_text:
                # Append the short text to the last long text
                fused_texts[-1] += "\n\n" + text
            else:
                # This is a long text, so add it to the list and remember it
                fused_texts.append(text)
                previous_long_text = text

        return fused_texts

    @staticmethod
    def create_tokenizer(model_path: str | Path):
        """
        Create a tokenizer instance from a pre-trained model or a local directory.

        Args:
            model_name (str): The name of the model or the path to the local directory.

        Returns:
            AutoTokenizer: The tokenizer instance.
        """
        # import lazily to not load transformers at top level
        # pylint: disable=import-outside-toplevel
        # Third Party
        from transformers import AutoTokenizer

        if not isinstance(model_path, Path):
            model_path = Path(model_path)
        error_info_message = (
            "Please run `ilab model download {download_args}` and try again"
        )
        try:
            if is_model_safetensors(model_path):
                error_info_message = error_info_message.format(
                    download_args=f"--repository {model_path}"
                )
                tokenizer = AutoTokenizer.from_pretrained(model_path)

            elif is_model_gguf(model_path):
                model_dir, model_filename = model_path.parent, model_path.name
                error_info_message = error_info_message.format(
                    download_args=f"--repository {model_dir} --filename {model_filename}"
                )
                tokenizer = AutoTokenizer.from_pretrained(
                    model_dir, gguf_file=model_filename
                )

            else:
                error_info_message = "Please provide a path to a valid model format. For help on downloading models, run `ilab model download --help`."
                raise ValueError()

            logger.info(f"Successfully loaded tokenizer from: {model_path}")
            return tokenizer

        except (OSError, ValueError) as e:
            logger.error(
                f"Failed to load tokenizer as no valid model was not found at {model_path}. {error_info_message}"
            )
            raise e

    def get_token_count(self, text, tokenizer):
        """
        Get the number of tokens in a text using the provided tokenizer.
        Args:
            text (str): The text to tokenize.
            tokenizer (AutoTokenizer): The tokenizer to use.
        Returns:
            int: Number of tokens.
        """
        return len(tokenizer.tokenize(text))

    def export_documents(self, converted_docs: Iterable[ConversionResult]):
        """Write converted documents to json files

        Check for successful conversions and write those to the docling artifacts directory.
        Returns:
            Path: path to directory with docling json artifacts
        """
        # triggers torch loading, import lazily
        # pylint: disable=import-outside-toplevel
        # Third Party
        from docling.document_converter import ConversionStatus

        docling_artifacts_path = Path() / "docling-artifacts"
        docling_artifacts_path.mkdir(parents=True, exist_ok=True)

        success_count = 0
        failure_count = 0

        for doc in converted_docs:
            if doc.status == ConversionStatus.SUCCESS:
                success_count += 1
                doc_filename = doc.input.file.stem

                # Export Deep Search document JSON format:
                with (docling_artifacts_path / f"{doc_filename}.json").open("w") as fp:
                    fp.write(json.dumps(doc.document.export_to_dict()))

                # Export Markdown format:
                with (docling_artifacts_path / f"{doc_filename}.md").open("w") as fp:
                    fp.write(doc.document.export_to_markdown())
            else:
                logger.info(f"Document {doc.input.file} failed to convert.")
                failure_count += 1

        logger.info(
            f"Processed {success_count + failure_count} docs, of which {failure_count} failed"
        )

        return docling_artifacts_path


def chunk_markdowns(documents: List | Dataset, chunk_size) -> Dataset:
    """
    Iterates over the documents and splits them into chunks based on the word count provided by the user.
    Args:
        documents (list): List of documents retrieved from git (can also consist of a single document).
        server_ctx_size (int): Context window size of server.
        chunk_word_count (int): Maximum number of words to chunk a document.
    Returns:
         List[str]: List of chunked documents.
    """

    # Checks for input type error
    content = []
    # chunk_size = _num_chars_from_tokens(no_tokens_per_doc)
    chunk_overlap = _DEFAULT_CHUNK_OVERLAP

    # Using Markdown as default, document-specific chunking will be implemented in separate pr.
    text_splitter = RecursiveCharacterTextSplitter.from_language(
        language=Language.MARKDOWN,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )

    # Determine file type for heuristics, default with markdown
    for docs in documents:
        # Use regex to remove unnecessary dashes in front of pipe characters in a markdown table.
        docs = re.sub(r"-{2,}\|", "-|", docs)
        # Remove unnecessary spaces in front of pipe characters in a markdown table.
        docs = re.sub(r"\  +\|", " |", docs)
        temp = text_splitter.create_documents([docs])
        content.extend([item.page_content for item in temp])
    return content