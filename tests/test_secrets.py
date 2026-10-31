"""密钥扫描器自身的测试：能抓真 key、放过占位符、尊重行内豁免。"""

from __future__ import annotations

from pathlib import Path

from tools.check_secrets import mask, scan_file, scan_text

FAKE_KEY = "sk-" + "a1b2c3d4" * 4          # 35 字符，形似真 key
PLACEHOLDER = "sk-xxxx"                     # 文档里的占位符，不该命中
ALLOWED = f'KEY = "{FAKE_KEY}"  # auditloop:allow-secret'


def test_real_looking_key_is_caught() -> None:
    hits = scan_text(f'api_key = "{FAKE_KEY}"\n')
    assert hits and hits[0][1] == "openai-style"
    assert FAKE_KEY not in hits[0][2], "报告里只能出现掩码"


def test_placeholder_is_ignored() -> None:
    assert scan_text(f'api_key = "{PLACEHOLDER}"\n') == []


def test_inline_allow_marker_works() -> None:
    assert scan_text(ALLOWED + "\n") == []


def test_private_key_block_is_caught() -> None:
    hits = scan_text("-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n")  # auditloop:allow-secret
    assert hits and hits[0][1] == "private-key-block"


def test_scan_file_handles_binary(tmp_path: Path) -> None:
    blob = tmp_path / "blob.bin"
    blob.write_bytes(b"\x00\x01" + FAKE_KEY.encode())
    assert scan_file(blob) == []


def test_mask_keeps_only_edges() -> None:
    masked = mask(FAKE_KEY)
    assert masked.startswith(FAKE_KEY[:4])
    assert masked.endswith(FAKE_KEY[-4:])
    assert FAKE_KEY[4:-4] not in masked
