"""代码审查五轮循环 · 第4轮回归：安全与健壮性。"""
from pathlib import Path

import pytest

from adapters import get_adapter
from adapters.base import FormatError
from core.appconfig import save_model_cache


def test_binary_null_byte_rejected(tmp_path):
    """md/html/txt 均拒绝含 null 字节的二进制文件（第4轮：防护统一到 base 层）。"""
    for name, fmt in [("f.md", "md"), ("f.html", "html"), ("f.txt", "txt")]:
        p = tmp_path / name
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
        with pytest.raises(FormatError, match="二进制"):
            get_adapter(fmt).parse(p)


def test_model_cache_capped_at_50(tmp_path):
    """模型缓存上限 50 条，超出按最旧淘汰（第4轮修复）。"""
    cfg = {"model_cache": {}}
    for i in range(60):
        save_model_cache(cfg, f"prov-{i}", ["m"])
    assert len(cfg["model_cache"]) == 50
    # 最旧的 prov-0..prov-9 被淘汰
    assert "prov-0" not in cfg["model_cache"]
    assert "prov-59" in cfg["model_cache"]
    assert "prov-10" in cfg["model_cache"]   # 保留最近 50
