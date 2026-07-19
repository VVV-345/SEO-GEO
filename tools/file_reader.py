"""读取客户业务资料，统一返回干净文本。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup


TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".tsv", ".yaml", ".yml"}


@dataclass(frozen=True)
class DocumentContent:
    path: str
    kind: str
    text: str
    pages: int | None = None


def _clean_text(text: str) -> str:
    """统一换行、清除 NUL，并压缩多余空白，保留段落结构供 LLM 判断语义。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _read_text_file(path: Path) -> str:
    """优先支持 UTF-8，再兼容常见中文 Windows 编码 GB18030。"""
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def read_document(path: str | Path, *, max_chars: int = 200_000) -> DocumentContent:
    """读取单个支持格式；扫描版 PDF 没有文本层时明确报错，而不返回空资料。"""
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"资料文件不存在：{file_path}")
    suffix = file_path.suffix.lower()
    pages: int | None = None

    if suffix in TEXT_SUFFIXES:
        text = _read_text_file(file_path)
    elif suffix == ".json":
        data = json.loads(file_path.read_text(encoding="utf-8"))
        text = json.dumps(data, ensure_ascii=False, indent=2)
    elif suffix in {".html", ".htm"}:
        soup = BeautifulSoup(_read_text_file(file_path), "lxml")
        text = soup.get_text("\n", strip=True)
    elif suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as error:
            raise RuntimeError("读取 PDF 需要安装 pypdf：pip install -r requirements.txt") from error
        reader = PdfReader(str(file_path))
        pages = len(reader.pages)
        text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
    elif suffix == ".docx":
        try:
            from docx import Document
        except (ImportError, AttributeError) as error:
            raise RuntimeError("读取 DOCX 需要可用的 python-docx：pip install --force-reinstall python-docx") from error
        document = Document(str(file_path))
        text = "\n".join(p.text for p in document.paragraphs)
    else:
        raise ValueError(f"暂不支持 {suffix or '无扩展名'} 文件：{file_path.name}")

    text = _clean_text(text)
    if not text and suffix == ".pdf":
        raise ValueError(f"PDF 未提取到文本，可能是扫描件，需要先 OCR：{file_path.name}")
    return DocumentContent(str(file_path), suffix.lstrip("."), text[:max_chars], pages)


def read_documents(paths: list[str | Path], *, max_chars_each: int = 200_000) -> list[DocumentContent]:
    """按输入顺序读取多个资料文件，任一格式错误时向调用方明确抛出。"""
    return [read_document(path, max_chars=max_chars_each) for path in paths]


def combine_documents(documents: list[DocumentContent], *, max_total_chars: int = 120_000) -> str:
    """在拼接资料时保留文件名，以便模型能追溯每段信息的来源。"""
    sections = [f"## 来源：{Path(doc.path).name}\n{doc.text}" for doc in documents]
    return "\n\n".join(sections)[:max_total_chars]
