"""配置：环境变量 > auditloop.toml > 默认值。

原则：缺省即安全。没配 API key 时自动落到 MockLLM，
而不是启动就崩——这样任何人 clone 下来第一条命令就能跑起来。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigError

# 每百万 token 的美元价格 (input, output)。真实产品应该来自配置中心，
# 这里留成表是为了让「成本」在账本里可见，而不是一句「很便宜」。
PRICES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "claude-sonnet-4": (3.00, 15.00),
    "mock": (0.0, 0.0),
    "replay": (0.0, 0.0),
}
DEFAULT_PRICE = (0.50, 1.50)


def price_for(model: str) -> tuple[float, float]:
    return PRICES_USD_PER_MTOK.get(model, DEFAULT_PRICE)


def cost_usd_for(model: str, input_tokens: int, output_tokens: int) -> float:
    """按**实际返回的模型名**算钱。mock/replay 的价格是 0——不能让演示数据带上真实成本。"""
    price_in, price_out = price_for(model)
    return input_tokens / 1_000_000 * price_in + output_tokens / 1_000_000 * price_out


@dataclass
class ModelConfig:
    base_url: str = ""
    api_key: str = ""
    model: str = "deepseek-chat"
    provider: str = ""
    temperature: float = 0.0
    max_output_tokens: int = 4096
    timeout_s: int = 120
    retries: int = 2

    @property
    def live(self) -> bool:
        return bool(self.base_url and self.api_key)

    def price(self) -> tuple[float, float]:
        return PRICES_USD_PER_MTOK.get(self.model, DEFAULT_PRICE)

    def cost_usd(self, input_tokens: int, output_tokens: int) -> float:
        pi, po = self.price()
        return input_tokens / 1_000_000 * pi + output_tokens / 1_000_000 * po


@dataclass
class Budget:
    """三道闸门：步数、token、钱。任何一个到顶都要优雅收尾。"""

    max_steps: int = 24
    max_input_tokens: int = 400_000
    max_output_tokens: int = 40_000
    max_cost_usd: float = 1.00


@dataclass
class Config:
    workspace: Path = field(default_factory=Path.cwd)
    state_dir: Path = field(default_factory=lambda: Path.cwd() / ".auditloop")
    policy_mode: str = "ask"          # ask | never | auto
    model: ModelConfig = field(default_factory=ModelConfig)
    budget: Budget = field(default_factory=Budget)
    compact_at_tokens: int = 12_000   # 上下文到这个水位就压缩
    keep_recent_messages: int = 8
    tool_output_chars: int = 8_000    # 单条工具输出进上下文前先截断
    verbose: bool = False

    @property
    def runs_dir(self) -> Path:
        return self.state_dir / "runs"

    def ensure_dirs(self) -> None:
        self.workspace = self.workspace.resolve()
        if not self.workspace.is_dir():
            raise ConfigError(f"工作目录不存在：{self.workspace}")
        if self.policy_mode not in {"ask", "never", "auto"}:
            raise ConfigError(f"policy_mode 只能是 ask/never/auto，收到 {self.policy_mode!r}")
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def load(
        cls,
        workspace: str | Path | None = None,
        state_dir: str | Path | None = None,
        config_file: str | Path | None = None,
    ) -> "Config":
        data: dict = {}
        path = Path(config_file) if config_file else Path("auditloop.toml")
        if path.is_file():
            try:
                data = tomllib.loads(path.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError as exc:
                raise ConfigError(f"{path} 不是合法 TOML：{exc}") from exc

        model_data = data.get("model", {})
        budget_data = data.get("budget", {})

        # 只给了平台名（AUDITLOOP_PROVIDER）时，端点和默认模型要从预设里补齐；
        # 否则 `live` 会判定为假，命令会静默退回 MockLLM——这是最难查的一类「配置了却没生效」。
        provider_name = (os.environ.get("AUDITLOOP_PROVIDER") or str(model_data.get("provider", ""))).strip()
        base_url = (os.environ.get("AUDITLOOP_BASE_URL") or str(model_data.get("base_url", ""))).strip()
        model_name = (os.environ.get("AUDITLOOP_MODEL") or str(model_data.get("model", ""))).strip()

        preset = None
        if provider_name:
            from .providers import PRESETS

            preset = PRESETS.get(provider_name.lower())
            if preset is None:
                raise ConfigError(
                    f"未知平台 {provider_name!r}，可用：{', '.join(sorted(PRESETS))}"
                )
            if not base_url:
                base_url = preset.base_url
            if not model_name:
                model_name = preset.default_model
        if not model_name:
            model_name = "deepseek-chat"

        model = ModelConfig(
            base_url=base_url,
            api_key=(os.environ.get("AUDITLOOP_API_KEY") or str(model_data.get("api_key", ""))).strip(),
            model=model_name,
            provider=provider_name,
            temperature=float(model_data.get("temperature", 0.0)),
            max_output_tokens=int(model_data.get("max_output_tokens", 4096)),
            timeout_s=int(model_data.get("timeout_s", 120)),
            retries=int(model_data.get("retries", 2)),
        )
        budget = Budget(
            max_steps=int(os.environ.get("AUDITLOOP_MAX_STEPS", budget_data.get("max_steps", 24))),
            max_input_tokens=int(budget_data.get("max_input_tokens", 400_000)),
            max_output_tokens=int(budget_data.get("max_output_tokens", 40_000)),
            max_cost_usd=float(budget_data.get("max_cost_usd", 1.00)),
        )
        cfg = cls(
            workspace=Path(workspace or data.get("workspace") or os.getcwd()),
            state_dir=Path(state_dir or data.get("state_dir") or Path.cwd() / ".auditloop"),
            policy_mode=os.environ.get("AUDITLOOP_POLICY") or data.get("policy_mode", "ask"),
            model=model,
            budget=budget,
            compact_at_tokens=int(data.get("compact_at_tokens", 12_000)),
            keep_recent_messages=int(data.get("keep_recent_messages", 8)),
            tool_output_chars=int(data.get("tool_output_chars", 8_000)),
            verbose=bool(data.get("verbose", False)),
        )
        cfg.ensure_dirs()
        return cfg
