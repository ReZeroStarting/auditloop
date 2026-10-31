"""统一异常。

面试价值：错误分类决定了上层能不能做对降级。
把「模型的问题」和「环境的问题」分开，是 agent 能不能自愈的前提——
模型调错了参数应该回填给它自己改，磁盘满了回填多少次都没用。

KeelError
├── ConfigError        配置有问题（启动即失败，不要带病运行）
├── LLMError           上游失败（retryable 决定重试还是换上游）
├── ToolError          工具执行失败（回填给模型，让它自己纠错）
├── ToolArgError       参数不合法（属于模型错误，不是环境错误）
├── PolicyDenied       策略拒绝（不可重试，必须换一条路）
└── BudgetExceeded     预算耗尽（必须优雅收尾，而不是抛栈）
"""

from __future__ import annotations


class AuditloopError(Exception):
    """所有异常的基类。"""


class ConfigError(AuditloopError):
    pass


class LLMError(AuditloopError):
    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class ToolError(AuditloopError):
    pass


class ToolArgError(ToolError):
    pass


class PolicyDenied(AuditloopError):
    def __init__(self, reason: str, *, detail: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


class BudgetExceeded(AuditloopError):
    pass
