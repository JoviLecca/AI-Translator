"""项目（设计 §5）：项目 = 一个文件夹；source/ target/ glossary.* .aitrans/。

- .aitrans 内不存绝对路径，项目文件夹整体拷贝即可迁移（设计 v0.6 #38）；
- .aitrans/lock 文件锁防止双实例写坏数据库；
- cfg_hash = hash(风格 + 目标语 + 有效术语集合)（设计 §6.1）。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from core.glossary import Glossary
from storage.db import Database


class ProjectError(Exception):
    pass


class ProjectLockError(ProjectError):
    pass


class Project:
    def __init__(self) -> None:
        self.root: Path | None = None
        self.config: dict = {}
        self.db: Database | None = None
        self.glossary: Glossary | None = None
        self._lock_file = None

    # ---------- 创建 / 打开 ----------
    @classmethod
    def create(cls, root: str | Path, *, name: str, src_lang: str, tgt_lang: str,
               style_preset: str = "formal", custom_style_prompt: str = "",
               provider_id: str = "", model: str = "",
               ruby_policy: str = "drop", ruby_loose: bool = False,
               context_slide: int | None = None) -> "Project":
        root = Path(root)
        if (root / "project.json").exists():
            raise ProjectError("目录中已存在项目，请直接打开")
        for sub in ("source", "target", ".aitrans/logs"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        config = {
            "schema_version": 1,
            "name": name or root.name,
            "src_lang": src_lang,
            "tgt_lang": tgt_lang,
            "style_preset": style_preset,
            "custom_style_prompt": custom_style_prompt,
            "provider_id": provider_id,
            "model": model,
            "ruby_policy": ruby_policy,
            "ruby_loose": bool(ruby_loose),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if context_slide is not None:
            config["context_slide"] = int(context_slide)
        (root / "project.json").write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        return cls.open(root)

    @classmethod
    def open(cls, root: str | Path) -> "Project":
        root = Path(root)
        cfg_path = root / "project.json"
        if not cfg_path.exists():
            raise ProjectError(f"不是有效的项目目录（缺少 project.json）：{root}")
        p = cls()
        p.root = root
        p.config = json.loads(cfg_path.read_text(encoding="utf-8"))
        p._acquire_lock()
        try:
            p.db = Database(root / ".aitrans" / "work.db")
            p.glossary = Glossary(root)
            p.glossary.load()
            # 记一份基线快照：这样用户即便在 Excel 里改过术语表后**直接重开项目**，
            # 「术语变更影响分析」也能比出"改之前"的状态（详见 reload_glossary）
            p.snapshot_terms(only_if_changed=True)
        except Exception:
            p._release_lock()
            raise
        return p

    def close(self) -> None:
        if self.db:
            self.db.close()
            self.db = None
        self._release_lock()

    # ---------- 术语变更历史（供术语变更影响分析对比） ----------
    def snapshot_terms(self, *, only_if_changed: bool = True) -> bool:
        """把当前术语表落一份快照（terms_revision），并同步 terms 缓存表。

        术语变更影响分析靠**相邻快照 diff** 找出「还在用旧译名的已译段落」。
        `only_if_changed=True` 时与最近一份快照内容相同就跳过 —— 内容没变却写快照，
        会把「上一次真实变更」挤出对比窗口（`analyze()` 只看最近两份）。
        """
        g = self.glossary
        if g is None or self.db is None:
            return False
        payload = g.to_payload()
        if only_if_changed:
            last = self.db.last_terms_revision()
            if last is not None:
                try:
                    if json.loads(last["payload"]) == payload:
                        return False
                except Exception:  # noqa: BLE001 快照损坏 → 当作不同，照常写
                    pass
        self.db.save_terms_revision(g.effective_hash(), payload)
        for t in g.entries:
            self.db.upsert_term(t.src, t.candidates, t.note,
                                origin="manual", status="approved")
        return True

    def reload_glossary(self) -> bool:
        """外部（Excel）改过术语表后重新加载，并让变更历史完整。

        返回术语表内容是否发生变化。步骤：
        1. 先把「改之前」的内存状态补一份快照（历史里已有就不重复写）；
        2. 重新读取术语表文件；
        3. 内容确实变了，再把「改之后」的新状态落一份快照。

        为什么要这样：外部改动原先**完全不落快照**，于是最近两份快照都是改之前
        的状态，`analyze()` 的 diff 什么也比不出来。补齐后，相邻快照就是
        「改之前 → 改之后」，术语变更影响分析才能列出受影响的已译段落。
        """
        g = self.glossary
        if g is None:
            return False
        before = g.to_payload()
        self.snapshot_terms(only_if_changed=True)   # 保证「改之前」有记录
        g.load()
        after = g.to_payload()
        if after != before:
            self.snapshot_terms(only_if_changed=False)
        return after != before

    # ---------- 锁 ----------
    def _acquire_lock(self) -> None:
        lock_path = self.root / ".aitrans" / "lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        f = open(lock_path, "a+", encoding="utf-8")
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            raise ProjectLockError("该项目已被其他实例打开（.aitrans/lock）")
        f.seek(0)
        f.truncate()
        f.write(str(time.time()))
        f.flush()
        self._lock_file = f

    def _release_lock(self) -> None:
        if self._lock_file is not None:
            try:
                f = self._lock_file
                if os.name == "nt":
                    import msvcrt
                    f.seek(0)
                    try:
                        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
                f.close()
            finally:
                self._lock_file = None
                try:
                    (self.root / ".aitrans" / "lock").unlink()
                except OSError:
                    pass

    # ---------- 配置 ----------
    @property
    def name(self) -> str:
        return self.config.get("name", "")

    @property
    def src_lang(self) -> str:
        return self.config.get("src_lang", "")

    @property
    def tgt_lang(self) -> str:
        return self.config.get("tgt_lang", "")

    @property
    def style_preset(self) -> str:
        return self.config.get("style_preset", "formal")

    @property
    def custom_style_prompt(self) -> str:
        return self.config.get("custom_style_prompt", "")

    @property
    def provider_id(self) -> str:
        return self.config.get("provider_id", "")

    @property
    def ruby_policy(self) -> str:
        return self.config.get("ruby_policy") or "drop"

    @property
    def ruby_loose(self) -> bool:
        return bool(self.config.get("ruby_loose", False))

    @property
    def context_slide(self):
        """前后滑窗段数；None = 继承全局设置（增补设计 §2.6）。"""
        return self.config.get("context_slide")

    def update_config(self, **kw) -> None:
        allowed = {"name", "src_lang", "tgt_lang", "style_preset", "custom_style_prompt",
                   "provider_id", "model", "ruby_policy", "ruby_loose", "context_slide"}
        assert set(kw) <= allowed, f"非法配置项：{set(kw) - allowed}"
        self.config.update(kw)
        (self.root / "project.json").write_text(
            json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8")

    def source_dir(self) -> Path:
        return self.root / "source"

    def target_dir(self) -> Path:
        return self.root / "target"

    def cfg_hash(self) -> str:
        payload = json.dumps({
            "style": self.style_preset,
            "custom": self.custom_style_prompt,
            "tgt": self.tgt_lang,
            "terms": self.glossary.effective_hash() if self.glossary else "",
            # 振假名策略改变输出形态 → 纳入指纹；默认值(drop/False)不写入，
            # 保证旧项目升级后已有译文缓存不失效（增补设计 §1.7）
            **({"ruby": f"{self.ruby_policy}|{int(self.ruby_loose)}"}
               if (self.ruby_policy != "drop" or self.ruby_loose) else {}),
        }, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
